"""
handlers/admin/users_manage.py
==============================
Tizim sozlamalari va foydalanuvchi balansini qo'lda boshqarish.

Imkoniyatlar:
 - [ ⚙️ Tizim sozlamalari ] (`adm_ref_price`):
     * Sub-menu: ⭐️ Referal narxi, 💸 Minimal yechib olish,
       💳 To'lov kartasi, ⭐️ 1 Stars narxi va 📢 Xabarnoma guruhi ID
     * Barcha qiymatlar bazadan (`settings` jadvali) o'qiladi va
       o'zgartiriladi; `.env` qiymati faqat bazada kalit bo'lmasa fallback.
 - [ 👤 Balansni o'zgartirish ] (`adm_balance_edit`):
     1. Nixo'langan foydalanuvchi IDsi so'raladi (`waiting_for_user_id`)
     2. Miqdor so'raladi (`waiting_for_amount`):
        - "+100" yoki "100" -> balansga QO'SHISH
        - "-50"             -> balansdan AYIRISH (0 dan pastga tushmaydi)
     Yozuv `transactions` jadvaliga `admin_adjust` sifatida tushadi (audit).

Bekor qilish: istalgan bosqichda /cancel
"""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import settings
from database.queries import (
    SETTING_CARD_HOLDER,
    SETTING_CARD_LOG_CHAT,
    SETTING_CARD_NUMBER,
    SETTING_MIN_WITHDRAWAL,
    SETTING_REFERRAL_REWARD,
    SETTING_STAR_PRICE,
    TX_ADMIN_ADJUST,
    InsufficientBalanceError,
    SettingsRepository,
    UserRepository,
)
from keyboards.builders import CB_ADM_BALANCE_EDIT, CB_ADM_CARD, CB_ADM_REF_PRICE
from states.admin_states import (
    ChangeSettingsStates,
    ManualBalanceStates,
    PaymentCardStates,
)

logger = logging.getLogger(__name__)

router = Router(name="admin_users_manage")

# faqat adminlar uchun
router.message.filter(F.from_user.id.in_(settings.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.ADMIN_IDS))

# Sub-menu callback'lari (shu modul ichida)
CB_UM_SET_REF = "adm_um_set_ref"
CB_UM_SET_MIN = "adm_um_set_min"
CB_UM_SET_CARD = "adm_um_set_card"
CB_UM_SET_PRICE = "adm_um_set_price"
CB_UM_SET_LOG = "adm_um_set_log"

# -------------------------------------------------------------
# Matnlar (HTML)
# -------------------------------------------------------------
SETTINGS_MENU_TEXT = (
    "⭐️ <b>TIZIM SOZLAMALARI</b>\n\n"
    "Joriy qiymatlar:\n"
    "├ Bir referal uchun: <b>{ref_reward} ⭐️</b>\n"
    "├ Minimal yechib olish: <b>{min_withdrawal} ⭐️</b>\n"
    "├ 1 ⭐️ narxi: <b>{star_price} so'm</b>\n"
    "└ Xabarnoma guruhi: <code>{log_chat}</code>\n\n"
    "Qaysirini o'zgartirmoqchisiz?"
)

ASK_STAR_PRICE_TEXT = (
    "⭐️ <b>1 Stars narxini o'zgartirish</b>\n\n"
    "Joriy narx: <b>{current} so'm</b>\n\n"
    "Yangi narxni so'mda yuboring (butun son, kamida 1).\n"
    "Masalan: <code>250</code>\n\n"
    "Bekor qilish: /cancel"
)

ASK_LOG_CHAT_TEXT = (
    "📢 <b>Xabarnoma guruhi / kanali</b>\n\n"
    "Joriy ID: <code>{current}</code>\n\n"
    "Karta to'lovlari xabarlari keladigan guruh/kanal chat ID sini "
    "yuboring (masalan: <code>-1001234567890</code>).\n\n"
    "O'chirish uchun <code>0</code> yuboring.\n\n"
    "Bekor qilish: /cancel"
)

SAVED_PRICE_TEXT = "✅ Saqlandi: <b>1 ⭐️ = {value} so'm</b>"
SAVED_LOG_TEXT = "✅ Saqlandi: xabarnoma guruhi <code>{value}</code>"

ASK_REF_REWARD_TEXT = (
    "⭐️ <b>Referal narxini o'zgartirish</b>\n\n"
    "Joriy qiymat: <b>{current} ⭐️</b>\n\n"
    "Bitta taklif uchun beriladigan yangi Stars miqdorini yuboring\n"
    "(0 dan katta butun son; masalan: 5)\n\n"
    "Bekor qilish: /cancel"
)

ASK_MIN_WITHDRAWAL_TEXT = (
    "💸 <b>Minimal yechib olishni o'zgartirish</b>\n\n"
    "Joriy qiymat: <b>{current} ⭐️</b>\n\n"
    "Yangi minimal miqdorni yuboring\n"
    "(kamida 1 bo'lishi kerak; masalan: 50)\n\n"
    "Bekor qilish: /cancel"
)

SAVED_TEXT = "✅ Saqlandi: <b>{key}</b> = <b>{value} ⭐️</b>"

ASK_CARD_NUMBER_TEXT = (
    "💳 <b>To'lov kartasi (UZCARD / HUMO)</b>\n\n"
    "Joriy karta: <code>{current}</code>\n\n"
    "Yangi karta raqamini yuboring (16–19 raqam, bo'shliqlarsiz).\n"
    "Masalan: <code>8600123456789012</code>\n\n"
    "Bekor qilish: /cancel"
)

ASK_CARD_HOLDER_TEXT = (
    "👤 <b>Karta egasi</b>\n\n"
    "Karta egasining F.I.SH. ni yuboring.\n"
    "Masalan: <code>ALI VALIYEV</code>\n\n"
    "Bekor qilish: /cancel"
)

CARD_SAVED_TEXT = (
    "✅ <b>Karta rekvizitlari saqlandi</b>\n\n"
    "├ Karta: <code>{number}</code>\n"
    "└ Egasi: <b>{holder}</b>\n\n"
    "Endi foydalanuvchilar shu kartaga to'lov qilib balans to'ldirishi mumkin."
)

ASK_USER_ID_TEXT = (
    "👤 <b>Foydalanuvchi balansini o'zgartirish</b>\n\n"
    "1/2 — Maqsad foydalanuvchi <b>ID sini</b> yuboring.\n"
    "(Telegram ID — @userinfobot orqali bilib oling)\n\n"
    "Bekor qilish: /cancel"
)

ASK_AMOUNT_TEXT = (
    "2/2 — <b>{name}</b> (ID <code>{user_id}</code>) hisobiga "
    "qo'shiladigan yoki ayriladigan miqdorni yuboring:\n\n"
    "├ <code>+100</code> yoki <code>100</code> — QO'SHISH\n"
    "└ <code>-50</code> — AYIRISH\n\n"
    "Joriy balans: <b>{balance} ⭐️</b>\n\n"
    "Bekor qilish: /cancel"
)

BALANCE_CHANGED_TEXT = (
    "✅ <b>Balans yangilandi!</b>\n\n"
    "├ Foydalanuvchi: <code>{user_id}</code>\n"
    "├ O'zgarish: <b>{delta:+d} ⭐️</b>\n"
    "└ Yangi balans: <b>{new_balance} ⭐️</b>"
)

CANCELLED_TEXT = "❌ Bekor qilindi."


def _settings_menu_kb() -> InlineKeyboardMarkup:
    """Sozlamalar sub-menusi: referal narxi / minimal yechish / karta."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⭐️ Referal narxi",
        callback_data=CB_UM_SET_REF,
        style="primary",
    )
    builder.button(
        text="💸 Minimal yechib olish",
        callback_data=CB_UM_SET_MIN,
        style="primary",
    )
    builder.button(
        text="💳 To'lov kartasi (UZCARD/HUMO)",
        callback_data=CB_UM_SET_CARD,
        style="success",
    )
    builder.button(
        text="⭐️ 1 Stars narxi (so'm)",
        callback_data=CB_UM_SET_PRICE,
        style="primary",
    )
    builder.button(
        text="📢 Xabarnoma guruhi ID",
        callback_data=CB_UM_SET_LOG,
        style="primary",
    )
    builder.adjust(1)
    return builder.as_markup()


async def _settings_menu_data() -> tuple[str, InlineKeyboardMarkup]:
    """Tizim sozlamalari menyusining (matn, klaviatura) juftligi."""
    ref_reward, min_withdrawal = await _current_settings()
    star_price = await SettingsRepository.get_star_price()
    log_chat = await SettingsRepository.get_card_log_chat_id()
    text = SETTINGS_MENU_TEXT.format(
        ref_reward=ref_reward,
        min_withdrawal=min_withdrawal,
        star_price=star_price,
        log_chat=log_chat or "sozlanmagan",
    )
    return text, _settings_menu_kb()


async def _current_card() -> tuple[str, str]:
    """(karta raqami, karta egasi) — bazadan (bo'sh bo'lishi mumkin)."""
    number = await SettingsRepository.get_setting(SETTING_CARD_NUMBER, "")
    holder = await SettingsRepository.get_setting(SETTING_CARD_HOLDER, "")
    return str(number or ""), str(holder or "")


async def _current_settings() -> tuple[int, int]:
    """(referal narxi, minimal yechish) — bazadan, fallback .env."""
    ref_reward = int(
        await SettingsRepository.get_setting(
            SETTING_REFERRAL_REWARD, settings.REFERRAL_REWARD_STARS
        )
    )
    min_withdrawal = int(
        await SettingsRepository.get_setting(
            SETTING_MIN_WITHDRAWAL, settings.MIN_WITHDRAWAL_STARS
        )
    )
    return ref_reward, min_withdrawal


# =============================================================
# 0. BEKOR QILISH ("/cancel" — keng state-handlerlardan OLDIN)
# =============================================================
@router.message(
    ChangeSettingsStates.waiting_for_referral_reward, F.text == "/cancel"
)
@router.message(
    ChangeSettingsStates.waiting_for_min_withdrawal, F.text == "/cancel"
)
@router.message(ManualBalanceStates.waiting_for_user_id, F.text == "/cancel")
@router.message(ManualBalanceStates.waiting_for_amount, F.text == "/cancel")
@router.message(PaymentCardStates.waiting_for_number, F.text == "/cancel")
@router.message(PaymentCardStates.waiting_for_holder, F.text == "/cancel")
@router.message(PaymentCardStates.waiting_for_star_price, F.text == "/cancel")
@router.message(PaymentCardStates.waiting_for_log_chat, F.text == "/cancel")
async def users_manage_cancel(message: Message, state: FSMContext) -> None:
    """FSM holatini tozalaydi va sozlamalar menyusiga qaytadi."""
    await state.clear()
    await message.answer(CANCELLED_TEXT)
    text, kb = await _settings_menu_data()
    await message.answer(text, reply_markup=kb)


# =============================================================
# 1. SOZLAMALAR MENYUSI
# =============================================================
@router.callback_query(F.data == CB_ADM_REF_PRICE)
async def settings_menu(callback: CallbackQuery) -> None:
    """[ ⚙️ Tizim sozlamalari ] — joriy qiymatlar bilan sub-menu."""
    text, kb = await _settings_menu_data()
    await callback.answer()
    await callback.message.answer(text, reply_markup=kb)


@router.callback_query(F.data == CB_UM_SET_REF)
async def set_ref_reward_start(callback: CallbackQuery, state: FSMContext) -> None:
    """⭐️ Referal narxini o'zgartirish — yangi qiymat so'raydi."""
    ref_reward, _ = await _current_settings()
    await state.set_state(ChangeSettingsStates.waiting_for_referral_reward)
    await callback.answer()
    await callback.message.answer(ASK_REF_REWARD_TEXT.format(current=ref_reward))


@router.callback_query(F.data == CB_UM_SET_MIN)
async def set_min_withdrawal_start(callback: CallbackQuery, state: FSMContext) -> None:
    """💸 Minimal yechib olishni o'zgartirish — yangi qiymat so'raydi."""
    _, min_withdrawal = await _current_settings()
    await state.set_state(ChangeSettingsStates.waiting_for_min_withdrawal)
    await callback.answer()
    await callback.message.answer(
        ASK_MIN_WITHDRAWAL_TEXT.format(current=min_withdrawal)
    )


@router.message(ChangeSettingsStates.waiting_for_referral_reward, F.text)
async def set_ref_reward_apply(message: Message, state: FSMContext) -> None:
    """Yangi referal narxini bazaga yozadi."""
    text = (message.text or "").strip().replace(" ", "")
    if not text.isdigit():
        await message.answer("⚠️ Faqat butun son yuboring (masalan: 5).")
        return

    value = int(text)  # 0 ham ruxsat (mukofotni o'chirish)
    await SettingsRepository.update_setting(SETTING_REFERRAL_REWARD, value)
    await state.clear()
    logger.info("Referal narxi o'zgardi (admin): %s -> %s", message.from_user.id, value)

    await message.answer(
        SAVED_TEXT.format(key="Bitta referal uchun", value=value)
    )
    text, kb = await _settings_menu_data()
    await message.answer(text, reply_markup=kb)


@router.message(ChangeSettingsStates.waiting_for_min_withdrawal, F.text)
async def set_min_withdrawal_apply(message: Message, state: FSMContext) -> None:
    """Yangi minimal yechib olish miqdorini bazaga yozadi."""
    text = (message.text or "").strip().replace(" ", "")
    if not text.isdigit() or int(text) < 1:
        await message.answer("⚠️ Minimal miqdor kamida 1 bo'lishi kerak (masalan: 50).")
        return

    value = int(text)
    await SettingsRepository.update_setting(SETTING_MIN_WITHDRAWAL, value)
    await state.clear()
    logger.info("Minimal yechish o'zgardi (admin): %s -> %s", message.from_user.id, value)

    await message.answer(
        SAVED_TEXT.format(key="Minimal yechib olish", value=value)
    )
    text, kb = await _settings_menu_data()
    await message.answer(text, reply_markup=kb)


# =============================================================
# 2. TO'LOV KARTASI REKVIZITLARI (UZCARD / HUMO)
# =============================================================
@router.callback_query(F.data == CB_UM_SET_CARD)
async def set_card_start(callback: CallbackQuery, state: FSMContext) -> None:
    """💳 Karta raqamini o'zgartirish — joriy qiymat bilan so'raydi."""
    number, _ = await _current_card()
    await state.set_state(PaymentCardStates.waiting_for_number)
    await callback.answer()
    await callback.message.answer(
        ASK_CARD_NUMBER_TEXT.format(current=number or "kiritilmagan")
    )


@router.callback_query(F.data == CB_ADM_CARD)
async def card_from_panel(callback: CallbackQuery, state: FSMContext) -> None:
    """
    [ 💳 Karta ma'lumotlari ] — panelning alohida tugmasi.

    To'g'ridan-to'g'ri karta rekvizitlari oqimini ochadi (raqam → egasi).
    Saqlangan raqam keyinchalik foydalanuvchining '💳 Balans to'ldirish'
    ekranida aynan shunday ko'rsatiladi.
    """
    await callback.answer()
    await state.clear()
    await set_card_start(callback, state)


@router.message(PaymentCardStates.waiting_for_number, F.text)
async def set_card_number(message: Message, state: FSMContext) -> None:
    """Karta raqamini tekshiradi va egasini so'raydi."""
    digits = "".join(ch for ch in (message.text or "") if ch.isdigit())
    if not 16 <= len(digits) <= 19:
        await message.answer(
            "⚠️ Karta raqami 16–19 raqamdan iborat bo'lishi kerak.\n"
            "Masalan: <code>8600123456789012</code>",
        )
        return

    await state.update_data(card_number=digits)
    await state.set_state(PaymentCardStates.waiting_for_holder)
    await message.answer(ASK_CARD_HOLDER_TEXT)


@router.message(PaymentCardStates.waiting_for_holder, F.text)
async def set_card_holder(message: Message, state: FSMContext) -> None:
    """Karta egasini saqlaydi va ikkala rekvizitni bazaga yozadi."""
    holder = " ".join((message.text or "").split())
    if not 3 <= len(holder) <= 64:
        await message.answer(
            "⚠️ Karta egasi 3–64 belgidan iborat bo'lishi kerak "
            "(masalan: <code>ALI VALIYEV</code>)."
        )
        return

    data = await state.get_data()
    number = str(data.get("card_number", ""))
    if not number:
        await state.clear()
        await message.answer("⚠️ Xatolik: karta raqami yo'qolgan. Qaytadan boshlang.")
        return

    await SettingsRepository.update_setting(SETTING_CARD_NUMBER, number)
    await SettingsRepository.update_setting(SETTING_CARD_HOLDER, holder)
    await state.clear()

    formatted = " ".join(
        number[index:index + 4] for index in range(0, len(number), 4)
    )
    logger.info(
        "Karta rekvizitlari o'zgardi (admin=%s): %s",
        message.from_user.id,
        formatted,
    )
    await message.answer(
        CARD_SAVED_TEXT.format(
            number=formatted, holder=html.escape(holder)
        )
    )


# =============================================================
# 2.1. 1 STARS NARXI (so'm)
# =============================================================
@router.callback_query(F.data == CB_UM_SET_PRICE)
async def set_star_price_start(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """⭐️ 1 Stars narxini o'zgartirish — yangi narx so'raydi."""
    price = await SettingsRepository.get_star_price()
    await state.set_state(PaymentCardStates.waiting_for_star_price)
    await callback.answer()
    await callback.message.answer(ASK_STAR_PRICE_TEXT.format(current=price))


@router.message(PaymentCardStates.waiting_for_star_price, F.text)
async def set_star_price_apply(message: Message, state: FSMContext) -> None:
    """Yangi 1 Stars narxini bazaga yozadi."""
    raw = (message.text or "").strip().replace(" ", "").replace(",", "")
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("⚠️ Narx kamida 1 so'm bo'lishi kerak (masalan: 250).")
        return

    value = int(raw)
    await SettingsRepository.update_setting(SETTING_STAR_PRICE, value)
    await state.clear()
    logger.info("1 Stars narxi o'zgardi (admin): %s -> %s so'm", message.from_user.id, value)

    await message.answer(SAVED_PRICE_TEXT.format(value=value))
    text, kb = await _settings_menu_data()
    await message.answer(text, reply_markup=kb)


# =============================================================
# 2.2. XABARNOMA GURUHI / KANALI (chat ID)
# =============================================================
@router.callback_query(F.data == CB_UM_SET_LOG)
async def set_log_chat_start(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """📢 Karta to'lovlari xabarnoma chatini o'zgartirish."""
    current = await SettingsRepository.get_card_log_chat_id()
    await state.set_state(PaymentCardStates.waiting_for_log_chat)
    await callback.answer()
    await callback.message.answer(
        ASK_LOG_CHAT_TEXT.format(current=current or "sozlanmagan")
    )


@router.message(PaymentCardStates.waiting_for_log_chat, F.text)
async def set_log_chat_apply(message: Message, state: FSMContext) -> None:
    """Xabarnoma chat ID sini bazaga yozadi (0 — o'chirish)."""
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.lstrip("-").isdigit():
        await message.answer(
            "⚠️ Chat ID butun son bo'lishi kerak (masalan: -1001234567890)."
        )
        return

    value = int(raw)
    await SettingsRepository.update_setting(SETTING_CARD_LOG_CHAT, value)
    await state.clear()
    logger.info(
        "Karta xabarnoma chat ID o'zgardi (admin): %s -> %s",
        message.from_user.id, value,
    )

    await message.answer(
        SAVED_LOG_TEXT.format(value=value or "sozlanmagan")
    )
    text, kb = await _settings_menu_data()
    await message.answer(text, reply_markup=kb)


# =============================================================
# 3. BALANSNI QO'LDA O'ZGARTIRISH
# =============================================================
@router.callback_query(F.data == CB_ADM_BALANCE_EDIT)
async def balance_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    """[ 👤 Balansni o'zgartirish ] — foydalanuvchi ID sini so'raydi."""
    await state.set_state(ManualBalanceStates.waiting_for_user_id)
    await callback.answer()
    await callback.message.answer(ASK_USER_ID_TEXT)


@router.message(ManualBalanceStates.waiting_for_user_id, F.text)
async def balance_edit_user_id(message: Message, state: FSMContext) -> None:
    """Foydalanuvchi ID sini tekshirib, miqdor so'raydi."""
    text = (message.text or "").strip().replace(" ", "")
    if not text.lstrip("-").isdigit():
        await message.answer("⚠️ ID faqat butun son bo'lishi kerak (masalan: 5555809414).")
        return

    user_id = int(text)
    user = await UserRepository.get_user(user_id)
    if user is None:
        await message.answer(
            f"⚠️ <code>{user_id}</code> IDli foydalanuvchi bazada yo'q.\n"
            "To'g'ri ID yuboring.",
        )
        return

    name = user.first_name or user.username or str(user_id)
    await state.update_data(user_id=user_id, name=name)
    await state.set_state(ManualBalanceStates.waiting_for_amount)
    await message.answer(
        ASK_AMOUNT_TEXT.format(
            name=name, user_id=user_id, balance=user.stars_balance
        )
    )


@router.message(ManualBalanceStates.waiting_for_amount, F.text)
async def balance_edit_apply(message: Message, state: FSMContext) -> None:
    """Miqdorni (+/-) qabul qilib, balansni atomik tarzda yangilaydi."""
    data = await state.get_data()
    user_id = int(data["user_id"])

    raw = (message.text or "").strip().replace(" ", "")
    if raw.startswith("+"):
        digits = raw[1:]
        amount = int(digits) if digits.isdigit() else None
    elif raw.startswith("-"):
        digits = raw[1:]
        amount = -int(digits) if digits.isdigit() else None
    elif raw.isdigit():
        amount = int(raw)  # belgisiz — qo'shish deb hisoblanadi
    else:
        amount = None

    if amount is None or amount == 0:
        await message.answer(
            "⚠️ Noto'g'ri miqdor. Namuna: <code>+100</code> (qo'shish) "
            "yoki <code>-50</code> (ayirish).",
        )
        return

    try:
        new_balance = await UserRepository.update_balance(
            user_id,
            amount,
            tx_type=TX_ADMIN_ADJUST,
            description=f"Admin tomonidan qo'lda o'zgartirildi ({message.from_user.id})",
        )
    except InsufficientBalanceError:
        await message.answer(
            "⚠️ Balansni bu qadar ayirib bo'lmaydi — 0 dan pastga tushadi.\n"
            "Kichikroq miqdor yuboring.",
        )
        return

    await state.clear()
    logger.info(
        "Balans qo'lda o'zgardi (admin=%s): user=%s delta=%+d -> %s",
        message.from_user.id, user_id, amount, new_balance,
    )

    await message.answer(
        BALANCE_CHANGED_TEXT.format(
            user_id=user_id, delta=amount, new_balance=new_balance
        )
    )
    # Qulaylik uchun yana ID so'raymiz (kuzatuvchi operatsiyalar uchun)
    await message.answer(
        ASK_USER_ID_TEXT,
    )
    await state.set_state(ManualBalanceStates.waiting_for_user_id)
