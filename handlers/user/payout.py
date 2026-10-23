"""
handlers/user/payout.py
=======================
"💸 Starslarni yechib olish" (payout) oqimi — FSM boshqaruvi.

Oqim (states/payout_states.py hujjatidagidek):
 1. `withdraw_start` callback — balans va minimal miqdor tekshiriladi,
    `PayoutStates.waiting_for_amount` holatiga o'tiladi.
 2. Foydalanuvchi miqdorni yuboradi — butun son, min <= amount <= balance
    tekshiruvidan o'tsa `PayoutStates.waiting_for_destination` holatiga o'tiladi.
 3. Manzil (TON hamyon yoki @username) yuboriladi — validatsiya qilinadi va
    `WithdrawalRepository.create_withdrawal` atomik hold bilan bazaga yoziladi,
    holat `state.clear()` bilan tozalanadi va adminlarga xabar yuboriladi.

Xavfsizlik:
 - Balans tekshiruvi `create_withdrawal` ichida qayta (FOR UPDATE) tekshiriladi
   — race holatida `InsufficientBalanceError` -> ROLLBACK (hold bo'lmaydi).
 - FSM massivida `amount` saqlanadi; "Bekor qilish" tugmasi holatni tozalaydi.
"""

from __future__ import annotations

import html
import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import settings
from database.queries import (
    SETTING_MIN_WITHDRAWAL,
    InsufficientBalanceError,
    RepositoryError,
    SettingsRepository,
    WithdrawalRepository,
)
from keyboards.inline import CB_BACK_TO_MENU, CB_WITHDRAW_START, get_balance_kb, MENU_BTN_TEXT
from states.payout_states import PayoutStates

logger = logging.getLogger(__name__)

router = Router(name="user_payout")

# TON hamyon manzili: 48 ta hex belgi (0x, EQ/UQ prefikslari bilan ham qabul qilinadi)
_TON_RE = re.compile(r"^(0x)?[0-9a-fA-F]{48}$|^(UQ|EQ)[0-9a-fA-F]{48}$")
# Telegram @username: 5-32 belgi, harf/bosh harf/raqam/underscore bilan boshlanadi
_USERNAME_RE = re.compile(r"^@?[a-zA-Z][a-zA-Z0-9_]{4,31}$")
# Bank kartasi: 16 raqam (bo'shliqlar olib tashlangan holda)
_CARD_RE = re.compile(r"^\d{16}$")

# Bekor qilish tugma matni (UI matni lokal — callback CB_PAYOUT_CANCEL)
BTN_CANCEL = "❌ Bekor qilish"
CB_PAYOUT_CANCEL = "payout_cancel"

# Doimiy (inline) klaviatura: faqat "Bekor qilish" + orqaga
def _cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_PAYOUT_CANCEL))
    builder.row(InlineKeyboardButton(text=MENU_BTN_TEXT, callback_data=CB_BACK_TO_MENU))
    return builder.as_markup()


async def _get_min_withdrawal() -> int:
    """Minimal yechib olish miqdorini bazadan (yo'qsa .env) oladi."""
    return int(
        await SettingsRepository.get_setting(
            SETTING_MIN_WITHDRAWAL, settings.MIN_WITHDRAWAL_STARS
        )
    )


async def _notify_admins(bot, withdrawal, user) -> None:
    """Yangi withdrawal so'rovini barcha ADMIN_IDS ga xabar qiladi (xatolik e'tiborsiz)."""
    from config.settings import settings as _settings

    who = (
        f"@{html.escape(user.username)}"
        if user.username
        else f'<a href="tg://user?id={user.user_id}">{html.escape(user.first_name or str(user.user_id))}</a>'
    )
    text = (
        "💸 <b>Yangi yechib olish so'rovi</b>\n\n"
        f"├ Foydalanuvchi: {who} (ID <code>{user.user_id}</code>)\n"
        f"├ Miqdor: <b>{withdrawal.amount} ⭐️</b>\n"
        f"├ Manzil turi: <code>{withdrawal.destination_type}</code>\n"
        f"└ Manzil: <code>{html.escape(withdrawal.destination_address)}</code>\n\n"
        f"So'rov ID: <code>#{withdrawal.id}</code>"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tasdiqlash", callback_data=f"approve_payout_{withdrawal.id}")
    kb.button(text="❌ Rad etish", callback_data=f"reject_payout_{withdrawal.id}")
    kb.adjust(1)

    for admin_id in _settings.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id, text, parse_mode="HTML", reply_markup=kb.as_markup()
            )
        except Exception:  # noqa: BLE001 — admin yuborishda xatolik oqimni to'xtatmasin
            logger.warning("Admin %s ga withdrawal xabari yuborilmadi", admin_id)


# =============================================================
# 1. Yechib olishni boshlash (callback)
# =============================================================
@router.callback_query(F.data == CB_WITHDRAW_START)
async def withdraw_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Balans va minimal miqdor tekshiruvidan keyin miqdor so'raydi."""
    user = callback.from_user
    user_id = user.id

    # Minimal miqdor (bazadan)
    min_withdrawal = await _get_min_withdrawal()

    # Joriy balans
    from database.queries import UserRepository

    db_user = await UserRepository.get_user(user_id)
    balance = db_user.stars_balance if db_user else 0

    if balance < min_withdrawal:
        await callback.answer(
            f"Balans yetarli emas — kamida {min_withdrawal}⭐️ kerak",
            show_alert=True,
        )
        return

    await state.set_state(PayoutStates.waiting_for_amount)
    await callback.message.answer(
        "💸 <b>Starslarni yechib olish</b>\n\n"
        f"Joriy balans: <b>{balance} ⭐️</b>\n"
        f"Minimal miqdor: <b>{min_withdrawal} ⭐️</b>\n\n"
        "Yechmoqchi bo'lgan miqdorni butun son ko'rinishida yuboring:",
        parse_mode="HTML",
        reply_markup=_cancel_kb(),
    )
    await callback.answer()


# =============================================================
# 2. Miqdorni qabul qilish
# =============================================================
@router.message(PayoutStates.waiting_for_amount, F.text)
async def on_amount(message: Message, state: FSMContext) -> None:
    """Miqdorni validatsiya qiladi va manzil so'ragan holatga o'tadi."""
    min_withdrawal = await _get_min_withdrawal()

    text = (message.text or "").strip().replace(" ", "")
    if not text.isdigit():
        await message.answer(
            "⚠️ Iltimos, miqdorni FAQAT butun son ko'rinishida yuboring (masalan: 100).",
            reply_markup=_cancel_kb(),
        )
        return

    amount = int(text)

    from database.queries import UserRepository

    db_user = await UserRepository.get_user(message.from_user.id)
    balance = db_user.stars_balance if db_user else 0

    if amount < min_withdrawal:
        await message.answer(
            f"⚠️ Minimal miqdor — <b>{min_withdrawal} ⭐️</b>. "
            f"Kamida {min_withdrawal} yuboring.",
            parse_mode="HTML",
            reply_markup=_cancel_kb(),
        )
        return

    if amount > balance:
        await message.answer(
            f"⚠️ Balans yetarli emas: balansingiz <b>{balance} ⭐️</b>, "
            f"so'ralgan <b>{amount} ⭐️</b>.",
            parse_mode="HTML",
            reply_markup=_cancel_kb(),
        )
        return

    await state.update_data(amount=amount)
    await state.set_state(PayoutStates.waiting_for_destination)
    await message.answer(
        "🏦 <b>Manzil</b>\n\n"
        "Yechib olish manzilini yuboring:\n"
        "├ Bank kartasi (16 raqam) yoki\n"
        "├ TON hamyon manzili (48 belgi) yoki\n"
        "└ Telegram @username\n\n"
        "Masalan: <code>9860170111243573</code>",
        parse_mode="HTML",
        reply_markup=_cancel_kb(),
    )


# =============================================================
# 3. Manzilni qabul qilish + bazaga yozish (atomik hold)
# =============================================================
@router.message(PayoutStates.waiting_for_destination, F.text)
async def on_destination(message: Message, state: FSMContext) -> None:
    """Manzilni validatsiya qiladi va withdrawal so'rovini yaratadi (atomik)."""
    data = await state.get_data()
    amount = int(data.get("amount", 0))

    if amount <= 0:
        # Zustur holat (state buzilgan) — boshidan boshlaymiz
        await state.clear()
        await message.answer("⚠️ Xatolik yuz berdi. Qaytadan boshlang.", reply_markup=_cancel_kb())
        return

    raw = (message.text or "").strip()
    destination = raw
    if raw.startswith("@"):
        destination_type = "stars_username"
        if not _USERNAME_RE.match(raw):
            await message.answer(
                "⚠️ Noto'g'ri @username. Masalan: @my_username (5-32 belgi).",
                reply_markup=_cancel_kb(),
            )
            return
    elif _TON_RE.match(raw):
        destination_type = "ton_wallet"
    elif _CARD_RE.match(raw.replace(" ", "")):
        destination_type = "card"
        destination = raw.replace(" ", "")
    elif _USERNAME_RE.match(raw):
        destination_type = "stars_username"
        destination = "@" + raw
    else:
        await message.answer(
            "⚠️ Noto'g'ri manzil. Karta (16 raqam), TON hamyon (48 belgi) "
            "yoki @username yuboring.",
            reply_markup=_cancel_kb(),
        )
        return

    # Atomik hold — balans muzlatiladi, withdrawal yaratiladi
    try:
        withdrawal = await WithdrawalRepository.create_withdrawal(
            user_id=message.from_user.id,
            amount=amount,
            destination_type=destination_type,
            destination_address=destination,
        )
    except InsufficientBalanceError:
        await message.answer(
            "⚠️ Balans yetarli emas — so'rov yaratilmadi. Balansni tekshiring.",
            reply_markup=_cancel_kb(),
        )
        await state.clear()
        return
    except (RepositoryError, ValueError) as exc:
        logger.warning("Withdrawal yaratishda xatolik: %s", exc)
        await message.answer(
            "⚠️ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.",
            reply_markup=_cancel_kb(),
        )
        await state.clear()
        return

    await state.clear()

    from database.queries import UserRepository

    db_user = await UserRepository.get_user(message.from_user.id)
    new_balance = db_user.stars_balance if db_user else 0

    await message.answer(
        "✅ <b>So'rov qabul qilindi!</b>\n\n"
        f"├ Miqdor: <b>{amount} ⭐️</b>\n"
        f"├ Manzil: <code>{html.escape(destination)}</code>\n"
        f"├ Yangi balans: <b>{new_balance} ⭐️</b>\n"
        "└ Holat: 🕓 <i>kutilmoqda (admin tasdiqlashini kuting)</i>\n\n"
        f"So'rov raqami: <code>#{withdrawal.id}</code>",
        parse_mode="HTML",
        reply_markup=get_balance_kb(new_balance, await _get_min_withdrawal()),
    )

    # Adminlarga xabar
    try:
        user_row = await UserRepository.get_user(message.from_user.id)
        if user_row:
            await _notify_admins(message.bot, withdrawal, user_row)
    except Exception:  # noqa: BLE001
        logger.warning("Admin xabari yuborishda xatolik (user=%s)", message.from_user.id)

    logger.info(
        "Payout yaratildi: user=%s amount=%s type=%s dest=%s id=%s",
        message.from_user.id, amount, destination_type, destination, withdrawal.id,
    )


# =============================================================
# 4. Bekor qilish (callback yoki matn)
# =============================================================
@router.callback_query(F.data == CB_PAYOUT_CANCEL)
async def payout_cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """Bekor qilish tugmasi — holatni tozalaydi."""
    await state.clear()
    await callback.answer("Bekor qilindi")
    await callback.message.answer("❌ Yechib olish bekor qilindi.")