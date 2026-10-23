"""
handlers/admin/bot_tools.py
===========================
Admin panelning 5 ta yangi vositasi:

 1. [ 💰 Bot Stars hisobi ] (`adm_star_balance`) — Telegram Bot API'ning
    `getMyStarBalance` metodi orqali botning rasmiy Stars balansi.
    Tagida [ ⭐️ Stars to'ldirish ] va [ ◀️ Orqaga ] tugmalari.
 2. [ ⭐️ Botga Stars solish ] (`adm_star_fill`) — adminga XTR invoysi
    (100 / 500 / 1000 Stars). Admin to'lagach Stars bot hisobiga tushadi.
    Payload prefiksi `botfill:` — do'kon/topup oqimlaridan ajratiladi.
 3. [ 🔎 Foydalanuvchini qidirish ] (`adm_user_search`) — ID bo'yicha
    karta: ism, @username, balans, takliflar + balans/bloklash tugmalari.
 4. [ 🎟️ Promokod ] (`adm_promo`) — yangi kod yaratish (nom, stars, limit)
    va mavjud kodlar ro'yxati. Foydalanuvchi kodni yozsa bir marta ishlaydi.
 5. [ 📬 Zakaz kanali ] (`adm_order_channel`) — yopiq kanal ID'si; barcha
    yangi xaridlar shu kanalga chiroyli chek bolib boradi:
    [ ✅ Topshirildi ] / [ ❌ Bekor qilish ] tugmalari bilan.

Xavfsizlik: router darajasidagi admin filtri barcha handlerga ta'sir qiladi;
callback'lar darhol `answer()` qilinadi (spinner yopiladi).
"""

from __future__ import annotations

import html
import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import settings
from database.queries import (
    SETTING_ORDER_LOG_CHAT,
    TX_ADMIN_ADJUST,
    BlockRepository,
    InsufficientBalanceError,
    OrderRepository,
    PromoError,
    PromoRepository,
    ReferralRepository,
    RepositoryError,
    SettingsRepository,
    UserRepository,
)
from keyboards.builders import CB_ADM_BACK
from services.card_payment import format_som
from services.order_log import CB_ZAKAZ_CANCEL_PREFIX, CB_ZAKAZ_DONE_PREFIX
from states.admin_states import (
    OrderChannelStates,
    PromoStates,
    UserSearchStates,
)

logger = logging.getLogger(__name__)

router = Router(name="admin_bot_tools")

# faqat adminlar uchun (router darajasida)
router.message.filter(F.from_user.id.in_(settings.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.ADMIN_IDS))

# -------------------------------------------------------------
# Callback konstantalari
# -------------------------------------------------------------
CB_ADM_STAR_BALANCE = "adm_star_balance"       # botning Stars hisobi
CB_ADM_STAR_FILL = "adm_star_fill"             # Stars to'ldirish menyusi
CB_ADM_STAR_PAY_PREFIX = "adm_star_pay_"       # + miqdor (100/500/1000)
CB_ADM_USER_SEARCH = "adm_user_search"         # foydalanuvchi qidirish
CB_ADM_US_ADD_PREFIX = "adm_usadd_"            # + user_id (balans qo'shish)
CB_ADM_US_SUB_PREFIX = "adm_ussub_"            # + user_id (balans ayirish)
CB_ADM_US_BLOCK_PREFIX = "adm_ublok_"          # + user_id (bloklash)
CB_ADM_US_UNBLOCK_PREFIX = "adm_uunblock_"     # + user_id (ochish)
CB_ADM_PROMO = "adm_promo"                     # promokodlar menyusi
CB_ADM_PROMO_NEW = "adm_promo_new"             # yangi kod yaratish
CB_ADM_ORDER_CHANNEL = "adm_order_channel"     # zakaz kanali sozlamasi

# Stars to'ldirish uchun tayyor miqdorlar (XTR invoice)
STAR_FILL_PRESETS: tuple[int, ...] = (100, 500, 1000)

# Bot hisobini to'ldirish invoysi payload'i: "botfill:{amount}"
BOTFILL_PAYLOAD_PREFIX = "botfill"

# Bloklangan foydalanuvchi uchun xabar
BLOCKED_TEXT = "🚫 Siz botdan foydalanish huquqini yo'qotdingiz. Savol uchun adminga murojaat qiling."

# -------------------------------------------------------------
# Matnlar
# -------------------------------------------------------------
STAR_BALANCE_TEXT = "⭐️ Botning rasmiy Stars balansi: {star_count} Stars"

STAR_FILL_TEXT = (
    "⭐️ <b>Botga Stars to'ldirish</b>\n\n"
    "Telegram hisobingizdan bot hisobiga Stars o'tkaziladi.\n"
    "Kerakli miqdorni tanlang 👇"
)

STAR_FILL_OK_TEXT = (
    "✅ <b>To'lov muvaffaqiyatli yakunlandi!</b>\n\n"
    "Bot hisobiga <b>{amount} Stars</b> tushdi.\n"
    "⭐️ Yangi balans: <b>{balance} Stars</b>"
)

ASK_SEARCH_ID_TEXT = (
    "🔎 <b>Foydalanuvchini qidirish</b>\n\n"
    "Foydalanuvchi Telegram ID sini yuboring.\n"
    "(@userinfobot orqali bilib olish mumkin)\n\n"
    "Bekor qilish: /cancel"
)

USER_NOT_FOUND_TEXT = "⚠️ <code>{user_id}</code> IDli foydalanuvchi bazada yo'q."

USER_CARD_TEXT = (
    "🔎 <b>FOYDALANUVCHI</b>\n\n"
    "├ Ism: <b>{name}</b>\n"
    "├ Username: {username}\n"
    "├ ID: <code>{user_id}</code>\n"
    "├ Balans: <b>{balance} ⭐️</b>\n"
    "├ Takliflar: {ref_total} ta ({ref_completed} tasdiqlangan)\n"
    "└ Holat: {status}\n\n"
    "Amal tanlang 👇"
)

ASK_ADJUST_AMOUNT_TEXT = (
    "💰 <b>{name}</b> (ID <code>{user_id}</code>) — {mode} miqdorini yuboring.\n\n"
    "Masalan: <code>100</code>\n"
    "Joriy balans: <b>{balance} ⭐️</b>\n\n"
    "Bekor qilish: /cancel"
)

ADJUST_DONE_TEXT = (
    "✅ <b>Balans yangilandi!</b>\n\n"
    "├ Foydalanuvchi: <code>{user_id}</code>\n"
    "├ O'zgarish: <b>{delta:+d} ⭐️</b>\n"
    "└ Yangi balans: <b>{new_balance} ⭐️</b>"
)

BLOCK_DONE_TEXT = "🚫 <code>{user_id}</code> bloklandi."
UNBLOCK_DONE_TEXT = "✅ <code>{user_id}</code> bloki ochildi."

PROMO_MENU_TEXT = (
    "🎟️ <b>PROMOKODLAR</b>\n\n"
    "Foydalanuvchi botga kodni matn sifatida yozadi — hisobiga Stars "
    "qo'shiladi (har bir odam faqat 1 marta).\n\n"
    "{codes}"
)

PROMO_EMPTY_TEXT = "Hozircha promokodlar yo'q. Yangi yarating 👇"

ASK_PROMO_CODE_TEXT = (
    "1/3 — <b>Promokod nomini</b> yuboring.\n\n"
    "Masalan: <code>YUTUQ</code>\n"
    "Bekor qilish: /cancel"
)

ASK_PROMO_STARS_TEXT = (
    "2/3 — <b>Stars miqdorini</b> yuboring.\n\n"
    "Masalan: <code>10</code>\n"
    "Bekor qilish: /cancel"
)

ASK_PROMO_LIMIT_TEXT = (
    "3/3 — <b>Cheklov sonini</b> yuboring (nechta odam ishlata oladi).\n\n"
    "Masalan: <code>50</code>\n"
    "<code>0</code> — cheklovsiz\n"
    "Bekor qilish: /cancel"
)

PROMO_CREATED_TEXT = (
    "✅ <b>Promokod yaratildi!</b>\n\n"
    "├ Kod: <code>{code}</code>\n"
    "├ Miqdor: <b>{stars} ⭐️</b>\n"
    "└ Cheklov: <b>{limit}</b>\n\n"
    "Foydalanuvchilar shu kodni botga yozishi mumkin."
)

ORDER_CHANNEL_TEXT = (
    "📬 <b>ZAKAZ KANALI</b>\n\n"
    "Joriy kanal: {current}\n\n"
    "Barcha yangi xaridlar (Premium, Gift, NFT, Stars) shu kanalga chiroyli "
    "chek bolib yuboriladi; tagida [ ✅ Topshirildi ] / [ ❌ Bekor qilish ] "
    "tugmalari bo'ladi.\n\n"
    "Yopiq kanal ID sini yuboring (<code>-100...</code>).\n"
    "Bot kanalda admin bo'lishi shart!\n"
    "O'chirish uchun <code>0</code> yuboring.\n\n"
    "Bekor qilish: /cancel"
)

ORDER_CHANNEL_SAVED_TEXT = "✅ Saqlandi: zakaz kanali <code>{value}</code>"

ZAKAZ_DONE_TEXT = (
    "✅ <b>Topshirildi</b> — buyurtma yakunlandi.\n\n"
    "🧾 Buyurtma: <code>#{order_id}</code>"
)

ZAKAZ_CANCELLED_TEXT = (
    "❌ <b>Bekor qilindi</b> — buyurtma bekor qilindi.\n\n"
    "🧾 Buyurtma: <code>#{order_id}</code>"
)

CANCELLED_TEXT = "❌ Bekor qilindi."


# =============================================================
# Yordamchilar
# =============================================================
def _star_balance_kb() -> InlineKeyboardMarkup:
    """[ ⭐️ Stars to'ldirish ] + [ ◀️ Orqaga ]."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⭐️ Stars to'ldirish",
            callback_data=CB_ADM_STAR_FILL,
            style="success",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Orqaga", callback_data=CB_ADM_BACK, style="primary"
        )
    )
    return builder.as_markup()


def _star_fill_kb() -> InlineKeyboardMarkup:
    """100 / 500 / 1000 Stars to'ldirish tugmalari + orqaga."""
    builder = InlineKeyboardBuilder()
    for amount in STAR_FILL_PRESETS:
        builder.row(
            InlineKeyboardButton(
                text=f"⭐️ {amount} Stars",
                callback_data=f"{CB_ADM_STAR_PAY_PREFIX}{amount}",
                style="success",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Orqaga",
            callback_data=CB_ADM_STAR_BALANCE,
            style="primary",
        )
    )
    return builder.as_markup()


def _user_card_kb(user_id: int, blocked: bool) -> InlineKeyboardMarkup:
    """Foydalanuvchi kartasi tugmalari: balans +/-, bloklash/ochish, orqaga."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⭐️ Stars qo'shish",
            callback_data=f"{CB_ADM_US_ADD_PREFIX}{user_id}",
            style="success",
        ),
        InlineKeyboardButton(
            text="⭐️ Stars ayirish",
            callback_data=f"{CB_ADM_US_SUB_PREFIX}{user_id}",
            style="primary",
        ),
    )
    if blocked:
        builder.row(
            InlineKeyboardButton(
                text="✅ Ochish",
                callback_data=f"{CB_ADM_US_UNBLOCK_PREFIX}{user_id}",
                style="success",
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text="🚫 Bloklash",
                callback_data=f"{CB_ADM_US_BLOCK_PREFIX}{user_id}",
                style="danger",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Orqaga", callback_data=CB_ADM_BACK, style="primary"
        )
    )
    return builder.as_markup()


def _parse_cb_id(data: str, prefix: str) -> int | None:
    """Callback'dan user/buyurtma ID'sini ajratib oladi (noto'g'ri -> None)."""
    raw = (data or "")[len(prefix):].strip()
    if not raw.lstrip("-").isdigit():
        return None
    value = int(raw)
    return value if value > 0 else None


async def _user_card_data(user_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    """Foydalanuvchi kartasi (matn, klaviatura); foydalanuvchi topilmasa None."""
    user = await UserRepository.get_user(user_id)
    if user is None:
        return None

    counts = await ReferralRepository.get_user_referrals_count(user_id)
    blocked = await BlockRepository.is_blocked(user_id)

    name = html.escape(
        " ".join(
            part for part in (user.first_name, user.last_name) if part
        )
        or user.username
        or str(user_id)
    )
    username = f"@{html.escape(user.username)}" if user.username else "—"
    text = USER_CARD_TEXT.format(
        name=name,
        username=username,
        user_id=user_id,
        balance=user.stars_balance or 0,
        ref_total=counts.total,
        ref_completed=counts.completed,
        status="🚫 bloklangan" if blocked else "✅ faol",
    )
    return text, _user_card_kb(user_id, blocked)


# =============================================================
# 0. BEKOR QILISH (/cancel — barcha yangi FSM oqimlari uchun)
# =============================================================
@router.message(UserSearchStates.waiting_for_user_id, F.text == "/cancel")
@router.message(UserSearchStates.waiting_for_amount, F.text == "/cancel")
@router.message(PromoStates.waiting_for_code, F.text == "/cancel")
@router.message(PromoStates.waiting_for_stars, F.text == "/cancel")
@router.message(PromoStates.waiting_for_limit, F.text == "/cancel")
@router.message(OrderChannelStates.waiting_for_chat_id, F.text == "/cancel")
async def bot_tools_cancel(message: Message, state: FSMContext) -> None:
    """FSM holatini tozalaydi."""
    await state.clear()
    await message.answer(CANCELLED_TEXT)


# =============================================================
# 1. 💰 BOT STARS HISOBI
# =============================================================
@router.callback_query(F.data == CB_ADM_STAR_BALANCE)
async def bot_star_balance(callback: CallbackQuery, state: FSMContext) -> None:
    """[ 💰 Bot Stars hisobi ] — botning rasmiy Stars balansini ko'rsatadi."""
    await callback.answer()
    await state.clear()

    try:
        star_amount = await callback.bot.get_my_star_balance()
        balance = int(star_amount.amount)
    except TelegramAPIError:
        logger.exception("getMyStarBalance so'rovi muvaffaqiyatsiz")
        await callback.message.answer(
            "⚠️ Bot Stars balansini o'olib bo'lmadi — keyinroq urinib ko'ring."
        )
        return

    await callback.message.answer(
        STAR_BALANCE_TEXT.format(star_count=balance),
        reply_markup=_star_balance_kb(),
    )


# =============================================================
# 2. ⭐️ BOTGA STARS SOLISH (XTR invoice)
# =============================================================
@router.callback_query(F.data == CB_ADM_STAR_FILL)
async def bot_star_fill_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """[ ⭐️ Stars to'ldirish ] — miqdor tanlash menyusi."""
    await callback.answer()
    await state.clear()
    await callback.message.answer(
        STAR_FILL_TEXT, reply_markup=_star_fill_kb()
    )


@router.callback_query(F.data.startswith(CB_ADM_STAR_PAY_PREFIX))
async def bot_star_fill_invoice(callback: CallbackQuery) -> None:
    """Tanlangan miqdor uchun XTR invoysi yuboradi (bot hisobini to'ldirish)."""
    await callback.answer()

    amount = _parse_cb_id(callback.data or "", CB_ADM_STAR_PAY_PREFIX)
    if amount is None or amount not in STAR_FILL_PRESETS:
        await callback.message.answer("⚠️ Noto'g'ri miqdor.")
        return

    try:
        await callback.bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=f"Bot hisobini {amount} Stars bilan to'ldirish",
            description=(
                "To'lov Telegram hisobingizdan bot hisobiga o'tkaziladi. "
                "Bu Starslar do'kon to'lovlarini ta'minlash uchun ishlatiladi."
            ),
            payload=f"{BOTFILL_PAYLOAD_PREFIX}:{amount}",
            provider_token="",  # Stars to'lovlari uchun provider token shart emas
            currency="XTR",
            prices=[LabeledPrice(label=f"{amount} Stars", amount=amount)],
            start_parameter=f"bot-fill-{amount}",
        )
    except TelegramAPIError:
        logger.exception("Bot to'ldirish invoysi yuborilmadi: %s", amount)
        await callback.message.answer(
            "⚠️ Invoysni yuborib bo'lmadi — keyinroq urinib ko'ring."
        )


@router.pre_checkout_query(
    F.invoice_payload.startswith(f"{BOTFILL_PAYLOAD_PREFIX}:")
)
async def botfill_pre_checkout(query: PreCheckoutQuery) -> None:
    """Bot to'ldirish invoysi — 10 soniya ichida javob beriladi."""
    await query.answer(ok=True)


@router.message(
    F.successful_payment.invoice_payload.startswith(
        f"{BOTFILL_PAYLOAD_PREFIX}:"
    )
)
async def botfill_paid(message: Message) -> None:
    """To'lov muvaffaqiyatli — bot hisobiga Stars tushdi (Telegram tomonidan)."""
    payload = message.successful_payment.invoice_payload
    amount = _parse_cb_id(payload or "", f"{BOTFILL_PAYLOAD_PREFIX}:") or 0

    try:
        star_amount = await message.bot.get_my_star_balance()
        balance = int(star_amount.amount)
    except TelegramAPIError:
        balance = -1  # ko'rsatilmaydi

    text = STAR_FILL_OK_TEXT.format(amount=amount, balance=balance)
    if balance < 0:
        text = f"✅ To'lov qabul qilindi: <b>{amount} Stars</b> bot hisobiga tushdi."

    await message.answer(text)
    logger.info("Bot hisobi to'ldirildi (admin=%s): +%s Stars",
                message.from_user.id, amount)


# =============================================================
# 3. 🔎 FOYDALANUVCHINI QIDIRISH
# =============================================================
@router.callback_query(F.data == CB_ADM_USER_SEARCH)
async def user_search_start(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """[ 🔎 Foydalanuvchini qidirish ] — ID so'raydi."""
    await callback.answer()
    await state.set_state(UserSearchStates.waiting_for_user_id)
    await callback.message.answer(ASK_SEARCH_ID_TEXT)


@router.message(UserSearchStates.waiting_for_user_id, F.text)
async def user_search_apply(message: Message, state: FSMContext) -> None:
    """Kiritilgan ID bo'yicha foydalanuvchi kartasini ko'rsatadi."""
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.lstrip("-").isdigit():
        await message.answer("⚠️ ID faqat butun son bo'lishi kerak (masalan: 5555809414).")
        return

    user_id = int(raw)
    card = await _user_card_data(user_id)
    if card is None:
        await message.answer(USER_NOT_FOUND_TEXT.format(user_id=user_id))
        return  # FSM qoladi — qayta ID so'raladi

    await state.clear()
    text, kb = card
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith(CB_ADM_US_ADD_PREFIX))
async def user_balance_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    """[ ⭐️ Stars qo'shish ] — miqdor so'raydi."""
    user_id = _parse_cb_id(callback.data or "", CB_ADM_US_ADD_PREFIX)
    await callback.answer()
    if user_id is None:
        return

    user = await UserRepository.get_user(user_id)
    if user is None:
        await callback.message.answer(USER_NOT_FOUND_TEXT.format(user_id=user_id))
        return

    await state.set_state(UserSearchStates.waiting_for_amount)
    await state.update_data(target_user_id=user_id, mode="add")
    await callback.message.answer(
        ASK_ADJUST_AMOUNT_TEXT.format(
            name=html.escape(user.first_name or str(user_id)),
            user_id=user_id,
            mode="qo'shish",
            balance=user.stars_balance or 0,
        )
    )


@router.callback_query(F.data.startswith(CB_ADM_US_SUB_PREFIX))
async def user_balance_sub_start(callback: CallbackQuery, state: FSMContext) -> None:
    """[ ⭐️ Stars ayirish ] — miqdor so'raydi."""
    user_id = _parse_cb_id(callback.data or "", CB_ADM_US_SUB_PREFIX)
    await callback.answer()
    if user_id is None:
        return

    user = await UserRepository.get_user(user_id)
    if user is None:
        await callback.message.answer(USER_NOT_FOUND_TEXT.format(user_id=user_id))
        return

    await state.set_state(UserSearchStates.waiting_for_amount)
    await state.update_data(target_user_id=user_id, mode="sub")
    await callback.message.answer(
        ASK_ADJUST_AMOUNT_TEXT.format(
            name=html.escape(user.first_name or str(user_id)),
            user_id=user_id,
            mode="ayirish",
            balance=user.stars_balance or 0,
        )
    )


@router.message(UserSearchStates.waiting_for_amount, F.text)
async def user_balance_apply(message: Message, state: FSMContext) -> None:
    """Miqdorni qabul qilib, balansni atomik yangilaydi va kartani qayta ko'rsatadi."""
    data = await state.get_data()
    user_id = int(data.get("target_user_id") or 0)
    mode = str(data.get("mode") or "add")

    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ Miqdor musbat butun son bo'lishi kerak (masalan: 100).")
        return

    amount = int(raw) if mode == "add" else -int(raw)

    try:
        new_balance = await UserRepository.update_balance(
            user_id,
            amount,
            tx_type=TX_ADMIN_ADJUST,
            description=f"Admin qo'shildi/ayrildi (admin={message.from_user.id})",
        )
    except InsufficientBalanceError:
        await message.answer(
            "⚠️ Balansni bu qadar ayirib bo'lmaydi — 0 dan pastga tushadi."
        )
        return

    await state.clear()
    await message.answer(
        ADJUST_DONE_TEXT.format(user_id=user_id, delta=amount, new_balance=new_balance)
    )

    # Yangilangan karta — qulaylik uchun darhol ko'rsatiladi
    card = await _user_card_data(user_id)
    if card is not None:
        text, kb = card
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith(CB_ADM_US_BLOCK_PREFIX))
async def user_block(callback: CallbackQuery) -> None:
    """[ 🚫 Bloklash ] — foydalanuvchini bloklaydi."""
    user_id = _parse_cb_id(callback.data or "", CB_ADM_US_BLOCK_PREFIX)
    await callback.answer()
    if user_id is None:
        return

    created = await BlockRepository.block_user(
        user_id, blocked_by=callback.from_user.id
    )
    await callback.message.answer(
        BLOCK_DONE_TEXT.format(user_id=user_id) if created
        else f"⚠️ <code>{user_id}</code> allaqachon bloklangan."
    )

    card = await _user_card_data(user_id)
    if card is not None:
        text, kb = card
        await callback.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith(CB_ADM_US_UNBLOCK_PREFIX))
async def user_unblock(callback: CallbackQuery) -> None:
    """[ ✅ Ochish ] — blokni olib tashlaydi."""
    user_id = _parse_cb_id(callback.data or "", CB_ADM_US_UNBLOCK_PREFIX)
    await callback.answer()
    if user_id is None:
        return

    removed = await BlockRepository.unblock_user(user_id)
    await callback.message.answer(
        UNBLOCK_DONE_TEXT.format(user_id=user_id) if removed
        else f"⚠️ <code>{user_id}</code> bloklangan emas edi."
    )

    card = await _user_card_data(user_id)
    if card is not None:
        text, kb = card
        await callback.message.answer(text, reply_markup=kb)


# =============================================================
# 4. 🎟️ PROMOKODLAR (admin)
# =============================================================
def _promo_list_text(codes: list) -> str:
    """Mavjud promokodlar ro'yxati (matn qismi)."""
    if not codes:
        return PROMO_EMPTY_TEXT

    lines: list[str] = []
    for promo in codes:
        status = "✅" if promo.is_active else "🚫"
        limit = promo.max_uses or "∞"
        lines.append(
            f"{status} <code>{html.escape(promo.code)}</code> — "
            f"{promo.stars} ⭐️ ({promo.used_count}/{limit})"
        )
    return "\n".join(lines)


def _promo_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="➕ Yangi promokod",
            callback_data=CB_ADM_PROMO_NEW,
            style="success",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Orqaga", callback_data=CB_ADM_BACK, style="primary"
        )
    )
    return builder.as_markup()


@router.callback_query(F.data == CB_ADM_PROMO)
async def promo_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """[ 🎟️ Promokod ] — mavjud kodlar ro'yxati."""
    await callback.answer()
    await state.clear()

    codes = await PromoRepository.list_codes()
    await callback.message.answer(
        PROMO_MENU_TEXT.format(codes=_promo_list_text(codes)),
        reply_markup=_promo_menu_kb(),
    )


@router.callback_query(F.data == CB_ADM_PROMO_NEW)
async def promo_new_start(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """[ ➕ Yangi promokod ] — kod nomini so'raydi."""
    await callback.answer()
    await state.set_state(PromoStates.waiting_for_code)
    await callback.message.answer(ASK_PROMO_CODE_TEXT)


@router.message(PromoStates.waiting_for_code, F.text)
async def promo_new_code(message: Message, state: FSMContext) -> None:
    """Kod nomini tekshirib, Stars miqdorini so'raydi."""
    code = (message.text or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_]{3,32}", code):
        await message.answer(
            "⚠️ Kod 3-32 belgi bo'lishi kerak (faqat harflar, raqamlar va _).\n"
            "Masalan: <code>YUTUQ</code>"
        )
        return

    existing = await PromoRepository.get_code(code)
    if existing is not None:
        await message.answer(
            f"⚠️ <code>{html.escape(code)}</code> allaqachon mavjud. Boshqa nom kiriting."
        )
        return

    await state.update_data(promo_code=code)
    await state.set_state(PromoStates.waiting_for_stars)
    await message.answer(ASK_PROMO_STARS_TEXT)


@router.message(PromoStates.waiting_for_stars, F.text)
async def promo_new_stars(message: Message, state: FSMContext) -> None:
    """Stars miqdorini tekshirib, cheklovni so'raydi."""
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ Stars miqdori musbat butun son bo'lishi kerak (masalan: 10).")
        return

    await state.update_data(promo_stars=int(raw))
    await state.set_state(PromoStates.waiting_for_limit)
    await message.answer(ASK_PROMO_LIMIT_TEXT)


@router.message(PromoStates.waiting_for_limit, F.text)
async def promo_new_limit(message: Message, state: FSMContext) -> None:
    """Cheklovni qabul qilib, promokodni yaratadi."""
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit():
        await message.answer("⚠️ Cheklov butun son bo'lishi kerak (0 — cheklovsiz).")
        return

    data = await state.get_data()
    code = str(data.get("promo_code") or "")
    stars = int(data.get("promo_stars") or 0)

    try:
        promo = await PromoRepository.create_code(
            code=code,
            stars=stars,
            max_uses=int(raw),
            created_by=message.from_user.id,
        )
    except (ValueError, PromoError) as exc:
        await state.clear()
        await message.answer(f"⚠️ {html.escape(str(exc))}")
        return

    await state.clear()
    await message.answer(
        PROMO_CREATED_TEXT.format(
            code=html.escape(promo.code),
            stars=promo.stars,
            limit=promo.max_uses or "cheklovsiz",
        )
    )


# =============================================================
# 5. 📬 ZAKAZ KANALI
# =============================================================
@router.callback_query(F.data == CB_ADM_ORDER_CHANNEL)
async def order_channel_start(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """[ 📬 Zakaz kanali ] — joriy qiymat bilan ID so'raydi."""
    await callback.answer()
    current = await SettingsRepository.get_order_log_chat_id()

    await state.set_state(OrderChannelStates.waiting_for_chat_id)
    await callback.message.answer(
        ORDER_CHANNEL_TEXT.format(current=f"<code>{current}</code>" if current else "sozlanmagan")
    )


@router.message(OrderChannelStates.waiting_for_chat_id, F.text)
async def order_channel_apply(message: Message, state: FSMContext) -> None:
    """Zakaz kanali chat ID'sini bazaga yozadi (0 — o'chirish)."""
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.lstrip("-").isdigit():
        await message.answer(
            "⚠️ Chat ID butun son bo'lishi kerak (masalan: -1001234567890)."
        )
        return

    value = int(raw)
    await SettingsRepository.update_setting(SETTING_ORDER_LOG_CHAT, value)
    await state.clear()
    logger.info("Zakaz kanali o'zgardi (admin=%s): %s", message.from_user.id, value)
    await message.answer(
        ORDER_CHANNEL_SAVED_TEXT.format(value=value if value else "sozlanmagan")
    )


# =============================================================
# 5.1. ZAKAZ KANALI TUGMALARI (Topshirildi / Bekor qilish)
# =============================================================
@router.callback_query(F.data.startswith(CB_ZAKAZ_DONE_PREFIX))
async def zakaz_done(callback: CallbackQuery) -> None:
    """
    [ ✅ Topshirildi ] — zakaz kanalidagi chekda buyurtma yetkazilgan
    deb belgilanadi; foydalanuvchiga xabar boradi.
    """
    order_id = _parse_cb_id(callback.data or "", CB_ZAKAZ_DONE_PREFIX)
    await callback.answer()
    if order_id is None:
        return

    try:
        order = await OrderRepository.mark_order_fulfilled(order_id)
    except RepositoryError:
        await callback.message.answer("⚠️ Buyurtma topilmadi.")
        return

    await callback.message.edit_text(
        ZAKAZ_DONE_TEXT.format(order_id=order.id)
    )

    try:
        await callback.bot.send_message(
            order.user_id,
            "✅ <b>Buyurtmangiz yetkazildi!</b>\n\n"
            f"🧾 Buyurtma: <code>#{order.id}</code>\n"
            "Xaridingiz uchun rahmat! 🛒",
        )
    except TelegramAPIError:
        logger.warning("Yetkazish tasdiqnomasi yuborilmadi: user=%s", order.user_id)

    logger.info("Zakaz yetkazildi (admin=%s): order=%s", callback.from_user.id, order.id)


@router.callback_query(F.data.startswith(CB_ZAKAZ_CANCEL_PREFIX))
async def zakaz_cancel(callback: CallbackQuery) -> None:
    """
    [ ❌ Bekor qilish ] — buyurtma bekor qilinadi (`cancelled`);
    foydalanuvchiga xabar boradi.
    """
    order_id = _parse_cb_id(callback.data or "", CB_ZAKAZ_CANCEL_PREFIX)
    await callback.answer()
    if order_id is None:
        return

    try:
        order = await OrderRepository.cancel_order(order_id)
    except RepositoryError:
        await callback.message.answer("⚠️ Buyurtma topilmadi.")
        return

    if order is None:
        await callback.message.answer(
            "⚠️ Buyurtma allaqachon yopilgan (to'langan yoki bekor qilingan)."
        )
        return

    await callback.message.edit_text(
        ZAKAZ_CANCELLED_TEXT.format(order_id=order.id)
    )

    try:
        await callback.bot.send_message(
            order.user_id,
            "❌ <b>Buyurtmangiz bekor qilindi.</b>\n\n"
            f"🧾 Buyurtma: <code>#{order.id}</code>\n"
            "Sabab: admin tomonidan rad etildi. Savol uchun murojaat qiling.",
        )
    except TelegramAPIError:
        logger.warning("Bekor qilish xabari yuborilmadi: user=%s", order.user_id)

    logger.info("Zakaz bekor qilindi (admin=%s): order=%s", callback.from_user.id, order.id)
