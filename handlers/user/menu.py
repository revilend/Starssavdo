"""
handlers/user/menu.py
=====================
Asosiy reply menyuning qolgan bo'limlari va umumiy navigatsiya.

Bu modul quyidagilarni boshqaradi:
  - ⭐️ **Balansim** — joriy balans, jami to'plangan Stars, jami takliflar
    va balans klaviaturasi (to'ldirish / yechish / tarix);
  - ℹ️ **Qo'llanma** — botdan qanday foydalanish bo'yicha qisqa yo'riqnoma;
  - 📜 `tx_history` — oxirgi tranzaksiyalar tarixi;
  - ◀️ `back_to_menu` — inline xabarni yopib, asosiy reply menyuga qaytish
    (do'kon "Orqaga" tugmasi ham shu yerga keladi).

Muhim: barcha reply handlerlari `StateFilter("*")` bilan ishlaydi va boshida
`state.clear()` chaqiradi — foydalanuvchi FSM oqimida (masalan, to'lov
miqdorini kiritishda) qolib ketgan bo'lsa ham menyu tugmalari DARHOL ishlaydi.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config.settings import settings
from database.queries import (
    SETTING_MIN_WITHDRAWAL,
    ReferralRepository,
    SettingsRepository,
    UserRepository,
)
from keyboards.inline import (
    CB_BACK_TO_MENU,
    CB_TX_HISTORY,
    get_balance_kb,
)
from keyboards.reply import get_main_menu_kb
from services.button_registry import ButtonText

logger = logging.getLogger(__name__)

router = Router(name="user_menu")

# Orqaga tugmasi yuboradigan matn
MAIN_MENU_TEXT = "🏠 <b>Bosh menyu</b>\n\nKerakli bo'limni tanlang 👇"

# Balans bo'limi matni
BALANCE_TEXT = (
    "⭐️ <b>BALANSIM</b>\n\n"
    "├ Joriy balans: <b>{balance} ⭐️</b>\n"
    "├ Jami to'plangan: <b>{earned} ⭐️</b>\n"
    "├ Taklif qilingan do'stlar: <b>{referrals} ta</b>\n"
    "└ Minimal yechish: <b>{min_withdrawal} ⭐️</b>\n\n"
    "Balansni to'ldirishingiz yoki Starslarni yechib olishingiz mumkin 👇"
)

# Qo'llanma matni
GUIDE_TEXT = (
    "ℹ️ <b>QO'LLANMA — @nftyutbot</b>\n\n"
    "🛍 <b>Do'kon</b> — Telegram Stars, Premium, NFT va Mayda Giftlarni "
    "rasmiy Stars yoki bot balansi orqali xarid qiling.\n\n"
    "⭐️ <b>Balansim</b> — joriy balans, to'ldirish, Starslarni yechib olish "
    "va tranzaksiyalar tarixi.\n\n"
    "💳 <b>Balans to'ldirish</b> — 2 usul:\n"
    "   ⚡️ Avtomatik to'lov (Telegram Stars) — darhol qo'shiladi;\n"
    "   💳 Uzcard / Humo kartaga o'tkazma — chek yoki avtomatik tasdiqlash.\n\n"
    "👥 <b>Do'stlarni taklif qilish</b> — har bir do'st uchun Stars "
    "mukofot oling.\n\n"
    "🎁 <b>Mayda Giftlar</b> — do'stlaringizga sovg'a yuboring.\n\n"
    "❓ Savollar bo'lsa administratorga murojaat qiling."
)

# Tranzaksiya turi yorliqlari
TX_LABELS: dict[str, str] = {
    "deposit": "💳 To'ldirish",
    "referral_reward": "👥 Referal mukofot",
    "bonus": "🎁 Bonus",
    "purchase": "🛍 Xarid",
    "withdraw_hold": "💸 Yechib olish",
    "refund": "↩️ Qaytarish",
    "admin_adjust": "⚙️ Admin tuzatishi",
}

EMPTY_HISTORY_TEXT = (
    "📜 <b>Tranzaksiyalar tarixi</b>\n\n"
    "Hozircha tranzaksiyalar yo'q. Balansni to'ldirib yoki do'stlaringizni "
    "taklif qilib boshlang! 🚀"
)


async def _main_menu_markup(user_id: int):
    """Foydalanuvchi uchun asosiy menyu klaviaturasi (admin qatorisiz/ bilan)."""
    return get_main_menu_kb(is_admin=user_id in settings.ADMIN_IDS)


# =============================================================
# 1. ⭐️ BALANSIM
# =============================================================
@router.message(StateFilter("*"), ButtonText("menu_balance"))
@router.message(StateFilter("*"), F.text.contains("Balansim"))
async def balance_menu(message: Message, state: FSMContext | None = None) -> None:
    """⭐️ Balansim — balans, jami to'plangan Stars va klaviatura."""
    if state is not None:
        await state.clear()

    user = message.from_user
    if user is None:
        return

    db_user = await UserRepository.get_user(user.id)
    balance = db_user.stars_balance if db_user else 0
    # `total_earned_stars` eski bazada bo'lmasligi mumkin — xavfsiz fallback.
    earned = getattr(db_user, "total_earned_stars", 0) or 0

    counts = await ReferralRepository.get_user_referrals_count(user.id)
    min_withdrawal = int(
        await SettingsRepository.get_setting(
            SETTING_MIN_WITHDRAWAL, settings.MIN_WITHDRAWAL_STARS
        )
    )

    await message.answer(
        BALANCE_TEXT.format(
            balance=balance,
            earned=earned,
            referrals=counts.total,
            min_withdrawal=min_withdrawal,
        ),
        reply_markup=get_balance_kb(balance, min_withdrawal),
    )
    logger.info("Balans bo'limi ochildi: user=%s balance=%s", user.id, balance)


# =============================================================
# 2. ℹ️ QO'LLANMA
# =============================================================
@router.message(StateFilter("*"), ButtonText("menu_guide"))
@router.message(StateFilter("*"), F.text.contains("Qo'llanma"))
async def guide_menu(message: Message, state: FSMContext | None = None) -> None:
    """ℹ️ Qo'llanma — botdan foydalanish bo'yicha qisqa yo'riqnoma."""
    if state is not None:
        await state.clear()

    await message.answer(
        GUIDE_TEXT,
        reply_markup=get_main_menu_kb(
            is_admin=bool(
                message.from_user and message.from_user.id in settings.ADMIN_IDS
            )
        ),
    )


# =============================================================
# 3. 📜 TRANZAKSIYALAR TARIXI
# =============================================================
@router.callback_query(F.data == CB_TX_HISTORY)
async def tx_history(callback: CallbackQuery, state: FSMContext | None = None) -> None:
    """📜 Oxirgi 10 tranzaksiyani ko'rsatadi."""
    if state is not None:
        await state.clear()

    transactions = await UserRepository.get_recent_transactions(
        callback.from_user.id, limit=10
    )
    await callback.answer()

    if not transactions:
        await callback.message.answer(EMPTY_HISTORY_TEXT, parse_mode="HTML")
        return

    lines: list[str] = ["📜 <b>OXIRGI TRANZAKSIYALAR</b>\n"]
    for tx in transactions:
        label = TX_LABELS.get(tx.tx_type, f"• {tx.tx_type}")
        sign = "+" if tx.amount > 0 else "−"
        lines.append(f"{label}: <b>{sign}{abs(tx.amount)} ⭐️</b>")

    await callback.message.answer("\n".join(lines), parse_mode="HTML")


# =============================================================
# 4. KOMANDALAR: /menu, /balance, /help
# =============================================================
# `services/commands.py` da `setMyCommands` bilan ro'yxatga olingan
# buyruqlar — Telegram shu ro'yxat tufayli yozish satrida menyu
# tugmasini (4 to'rtburchak) avtomatik chiqarib beradi.

@router.message(StateFilter("*"), Command("menu"))
async def cmd_menu(message: Message, state: FSMContext | None = None) -> None:
    """`/menu` — asosiy menyuni qayta ochadi (klaviaturani qaytaradi)."""
    if state is not None:
        await state.clear()

    is_admin = bool(
        message.from_user and message.from_user.id in settings.ADMIN_IDS
    )
    await message.answer(
        MAIN_MENU_TEXT,
        parse_mode="HTML",
        reply_markup=get_main_menu_kb(is_admin=is_admin),
    )


@router.message(StateFilter("*"), Command("balance"))
async def cmd_balance(message: Message, state: FSMContext | None = None) -> None:
    """`/balance` — ⭐️ Balansim bo'limi bilan bir xil ishlaydi."""
    await balance_menu(message, state)


@router.message(StateFilter("*"), Command("help"))
async def cmd_help(message: Message, state: FSMContext | None = None) -> None:
    """`/help` — ℹ️ Qo'llanma bo'limi bilan bir xil ishlaydi."""
    await guide_menu(message, state)


# =============================================================
# 4. ◀️ BOSH MENYU (inline "Orqaga")
# =============================================================
@router.callback_query(F.data == CB_BACK_TO_MENU)
async def back_to_menu(callback: CallbackQuery, state: FSMContext | None = None) -> None:
    """
    Inline "◀️ Orqaga" / "◀️ Bosh menyu" — xabarni yopadi va asosiy
    reply menyuni qaytaradi.
    """
    if state is not None:
        await state.clear()
    await callback.answer()

    message = callback.message
    # Eski inline xabarni o'chirishga urinamiz (bo'lmasa — tugmalarni olib tashlaymiz)
    try:
        await message.delete()
    except (TelegramAPIError, AttributeError):
        try:
            await message.edit_reply_markup(reply_markup=None)
        except (TelegramAPIError, AttributeError):
            pass

    await message.answer(
        MAIN_MENU_TEXT,
        parse_mode="HTML",
        reply_markup=await _main_menu_markup(callback.from_user.id),
    )
