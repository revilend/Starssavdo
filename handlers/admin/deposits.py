"""
handlers/admin/deposits.py
==========================
Karta orqali balans to'ldirish so'rovlarini tasdiqlash / rad etish.

Oqim:
 1. Foydalanuvchi chek yuborganda barcha adminlarga rasm + tugmalar keladi
    (`handlers/user/topup.py`).
 2. [ ✅ Tasdiqlash ] (`dep_approve_{id}`):
      - `DepositRepository.confirm_deposit` — atomik: so'rov qulfi,
        foydalanuvchi balansi +amount, `deposit` audit yozuvi, status approved;
      - foydalanuvchiga "Balansingiz to'ldirildi" xabari yuboriladi;
      - admin kartasi yakuniy holatga o'zgartiriladi (tugmalar olib tashlanadi).
 3. [ ❌ Rad etish ] (`dep_reject_{id}`):
      - `DepositRepository.reject_deposit` — balans o'zgarmaydi;
      - foydalanuvchiga rad etish sababi yuboriladi.

Ikki marta tasdiqlab bo'lmaydi: `DepositStateError` -> "allaqachon
ko'rib chiqilgan" ogohlantirishi (balans ikki marta qo'shilmaydi).
"""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.exc import SQLAlchemyError

from config.settings import settings
from database.queries import (
    DepositRepository,
    DepositStateError,
    RepositoryError,
)
logger = logging.getLogger(__name__)

router = Router(name="admin_deposits")

# faqat adminlar uchun
router.message.filter(F.from_user.id.in_(settings.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.ADMIN_IDS))

DEFAULT_REJECT_REASON = "To'lov cheki tasdiqlanmadi"

# Foydalanuvchiga yuboriladigan xabarlar
USER_APPROVED_TEXT = (
    "✅ <b>Balansingiz to'ldirildi!</b>\n\n"
    "├ Miqdor: <b>+{amount} ⭐️</b>\n"
    "├ Yangi balans: <b>{balance} ⭐️</b>\n"
    "└ So'rov: <code>#{deposit_id}</code>\n\n"
    "Xarid qilishingiz mumkin! 🛍"
)
USER_REJECTED_TEXT = (
    "❌ <b>To'ldirish so'rovingiz rad etildi</b>\n\n"
    "├ Miqdor: <b>{amount} ⭐️</b>\n"
    "├ Sabab: {reason}\n"
    "└ So'rov: <code>#{deposit_id}</code>\n\n"
    "Savollar bo'lsa administratorga murojaat qiling."
)

# Admin kartasining yakuniy ko'rinishi
CARD_APPROVED = (
    "✅ <b>SO'ROV #{deposit_id} TASDIQLANDI</b>\n\n"
    "👤 <code>{user_id}</code>\n"
    "💸 <b>{amount} ⭐️</b> balansga qo'shildi\n"
    "👮 Admin: <code>{admin_id}</code>"
)
CARD_REJECTED = (
    "❌ <b>SO'ROV #{deposit_id} RAD ETILDI</b>\n\n"
    "👤 <code>{user_id}</code>\n"
    "💸 <b>{amount} ⭐️</b> (balans o'zgarmadi)\n"
    "👮 Admin: <code>{admin_id}</code>"
)


def _parse_deposit_id(data: str | None) -> int | None:
    """`dep_approve_12` -> 12 (noto'g'ri formatda None)."""
    raw = (data or "").rsplit("_", 1)[-1]
    return int(raw) if raw.isdigit() else None


async def _notify_user(bot, user_id: int, text: str) -> None:
    """Foydalanuvchiga xabar yuboradi (bloklagan bo'lsa — log)."""
    try:
        await bot.send_message(user_id, text, parse_mode="HTML")
    except TelegramAPIError as exc:
        logger.warning("Foydalanuvchiga xabar yuborilmadi (user=%s): %s", user_id, exc)


async def _finalize_admin_card(callback: CallbackQuery, text: str) -> None:
    """Admin kartasini (rasm yoki matn) yakuniy holatga o'zgartiradi."""
    message = callback.message
    try:
        await message.edit_caption(caption=text, reply_markup=None)
        return
    except (TelegramBadRequest, AttributeError):
        pass
    try:
        await message.edit_text(text, reply_markup=None)
    except (TelegramBadRequest, AttributeError):
        pass


# =============================================================
# 1. TASDIQLASH
# =============================================================
@router.callback_query(F.data.startswith("dep_approve_"))
async def deposit_approve(callback: CallbackQuery) -> None:
    """[ ✅ Tasdiqlash ] — balansni atomik to'ldiradi va foydalanuvchiga xabar."""
    deposit_id = _parse_deposit_id(callback.data)
    if deposit_id is None:
        await callback.answer("⚠️ Noto'g'ri so'rov IDsi", show_alert=True)
        return

    admin_id = callback.from_user.id
    try:
        deposit, new_balance = await DepositRepository.confirm_deposit(
            deposit_id, admin_id
        )
    except DepositStateError:
        await callback.answer(
            "⚠️ Bu so'rov allaqachon ko'rib chiqilgan.", show_alert=True
        )
        return
    except RepositoryError:
        await callback.answer("⚠️ So'rov topilmadi.", show_alert=True)
        return
    except SQLAlchemyError:
        logger.exception("Deposit tasdiqlashda DB xatoligi: %s", deposit_id)
        await callback.answer("⚠️ Bazada xatolik yuz berdi.", show_alert=True)
        return

    logger.info(
        "Deposit tasdiqlandi (admin=%s): id=%s user=%s amount=%s",
        admin_id, deposit.id, deposit.user_id, deposit.amount,
    )
    await callback.answer("✅ Tasdiqlandi — balans to'ldirildi")

    await _notify_user(
        callback.bot,
        deposit.user_id,
        USER_APPROVED_TEXT.format(
            amount=deposit.amount,
            balance=new_balance,
            deposit_id=deposit.id,
        ),
    )
    await _finalize_admin_card(
        callback,
        CARD_APPROVED.format(
            deposit_id=deposit.id,
            user_id=deposit.user_id,
            amount=deposit.amount,
            admin_id=admin_id,
        ),
    )


# =============================================================
# 2. RAD ETISH
# =============================================================
@router.callback_query(F.data.startswith("dep_reject_"))
async def deposit_reject(callback: CallbackQuery) -> None:
    """[ ❌ Rad etish ] — so'rov rejected bo'ladi (balans o'zgarmaydi)."""
    deposit_id = _parse_deposit_id(callback.data)
    if deposit_id is None:
        await callback.answer("⚠️ Noto'g'ri so'rov IDsi", show_alert=True)
        return

    admin_id = callback.from_user.id
    try:
        deposit = await DepositRepository.reject_deposit(
            deposit_id, admin_id, DEFAULT_REJECT_REASON
        )
    except DepositStateError:
        await callback.answer(
            "⚠️ Bu so'rov allaqachon ko'rib chiqilgan.", show_alert=True
        )
        return
    except RepositoryError:
        await callback.answer("⚠️ So'rov topilmadi.", show_alert=True)
        return
    except SQLAlchemyError:
        logger.exception("Deposit rad etishda DB xatoligi: %s", deposit_id)
        await callback.answer("⚠️ Bazada xatolik yuz berdi.", show_alert=True)
        return

    logger.info(
        "Deposit rad etildi (admin=%s): id=%s user=%s",
        admin_id, deposit.id, deposit.user_id,
    )
    await callback.answer("❌ Rad etildi")

    await _notify_user(
        callback.bot,
        deposit.user_id,
        USER_REJECTED_TEXT.format(
            amount=deposit.amount,
            reason=html.escape(DEFAULT_REJECT_REASON),
            deposit_id=deposit.id,
        ),
    )
    await _finalize_admin_card(
        callback,
        CARD_REJECTED.format(
            deposit_id=deposit.id,
            user_id=deposit.user_id,
            amount=deposit.amount,
            admin_id=admin_id,
        ),
    )
