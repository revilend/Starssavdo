"""
handlers/admin/users_manage.py
==============================
Referal narxini sozlash va foydalanuvchi balansini qo'lda boshqarish.

Imkoniyatlar:
 - [ ⭐️ Referal narxini sozlash ] (`adm_ref_price`):
     * Sub-menu: ⭐️ Referal narxi yoki 💸 Minimal yechib olish miqdori
     * Ikkalasi ham bazadan (`settings` jadvali) o'qiladi va o'zgartiriladi;
       `.env` qiymati faqat bazada kalit bo'lmasa fallback sifatida ishlatiladi.
 - [ 👤 Balansni o'zgartirish ] (`adm_balance_edit`):
     1. Nixo'langan foydalanuvchi IDsi so'raladi (`waiting_for_user_id`)
     2. Miqdor so'raladi (`waiting_for_amount`):
        - "+100" yoki "100" -> balansga QO'SHISH
        - "-50"             -> balansdan AYIRISH (0 dan pastga tushmaydi)
     Yozuv `transactions` jadvaliga `admin_adjust` sifatida tushadi (audit).

Bekor qilish: istalgan bosqichda /cancel
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import settings
from database.queries import (
    SETTING_MIN_WITHDRAWAL,
    SETTING_REFERRAL_REWARD,
    TX_ADMIN_ADJUST,
    InsufficientBalanceError,
    SettingsRepository,
    UserRepository,
)
from keyboards.builders import CB_ADM_BALANCE_EDIT, CB_ADM_REF_PRICE
from states.admin_states import ChangeSettingsStates, ManualBalanceStates

logger = logging.getLogger(__name__)

router = Router(name="admin_users_manage")

# faqat adminlar uchun
router.message.filter(F.from_user.id.in_(settings.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.ADMIN_IDS))

# Sub-menu callback'lari (shu modul ichida)
CB_UM_SET_REF = "adm_um_set_ref"
CB_UM_SET_MIN = "adm_um_set_min"

# -------------------------------------------------------------
# Matnlar (HTML)
# -------------------------------------------------------------
SETTINGS_MENU_TEXT = (
    "⭐️ <b>TIZIM SOZLAMALARI</b>\n\n"
    "Joriy qiymatlar:\n"
    "├ Bir referal uchun: <b>{ref_reward} ⭐️</b>\n"
    "└ Minimal yechib olish: <b>{min_withdrawal} ⭐️</b>\n\n"
    "Qaysirini o'zgartirmoqchisiz?"
)

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
    """Sozlamalar sub-menusi: referal narxi / minimal yechish."""
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐️ Referal narxi", callback_data=CB_UM_SET_REF)
    builder.button(text="💸 Minimal yechib olish", callback_data=CB_UM_SET_MIN)
    builder.adjust(1)
    return builder.as_markup()


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
async def users_manage_cancel(message: Message, state: FSMContext) -> None:
    """FSM holatini tozalaydi va sozlamalar menyusiga qaytadi."""
    await state.clear()
    await message.answer(CANCELLED_TEXT)
    ref_reward, min_withdrawal = await _current_settings()
    await message.answer(
        SETTINGS_MENU_TEXT.format(
            ref_reward=ref_reward, min_withdrawal=min_withdrawal
        ),
        reply_markup=_settings_menu_kb(),
    )


# =============================================================
# 1. SOZLAMALAR MENYUSI
# =============================================================
@router.callback_query(F.data == CB_ADM_REF_PRICE)
async def settings_menu(callback: CallbackQuery) -> None:
    """[ ⭐️ Referal narxini sozlash ] — joriy qiymatlar bilan sub-menu."""
    ref_reward, min_withdrawal = await _current_settings()
    await callback.answer()
    await callback.message.answer(
        SETTINGS_MENU_TEXT.format(
            ref_reward=ref_reward, min_withdrawal=min_withdrawal
        ),
        reply_markup=_settings_menu_kb(),
    )


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
    ref_reward, min_withdrawal = await _current_settings()
    await message.answer(
        SETTINGS_MENU_TEXT.format(
            ref_reward=ref_reward, min_withdrawal=min_withdrawal
        ),
        reply_markup=_settings_menu_kb(),
    )


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
    ref_reward, min_withdrawal = await _current_settings()
    await message.answer(
        SETTINGS_MENU_TEXT.format(
            ref_reward=ref_reward, min_withdrawal=min_withdrawal
        ),
        reply_markup=_settings_menu_kb(),
    )


# =============================================================
# 2. BALANSNI QO'LDA O'ZGARTIRISH
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
    name = str(data.get("name", user_id))

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
