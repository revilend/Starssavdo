"""
handlers/admin/withdrawals.py
=============================
Yechib olish (payout) so'rovlarini ko'rish va qaror qabul qilish.

Oqim:
 1. [ 💳 Yechib olish so'rovlari ] (`adm_withdrawals`) — bazadan
    `status='pending'` so'rovlar ro'yxati (oxirgilari birinchi) +
    har biriga [ ✅ Tasdiqlash ] / [ ❌ Rad etish ] tugmalari.
 2. [ ✅ Tasdiqlash ] (`approve_payout_{id}`):
    - `WithdrawalRepository.approve_withdrawal` -> status='approved'
      (balans o'zgarmaydi — Stars `create_withdrawal` da muzlatilgan).
    - Foydalanuvchiga "To'lovingiz yuborildi" xabari yuboriladi.
 3. [ ❌ Rad etish ] (`reject_payout_{id}`):
    - `WithdrawalRepository.reject_withdrawal` -> status='rejected'
      va muzlatilgan Stars ATOMIK tarzda foydalanuvchi balansiga
      qaytariladi (`refund` audit bilan).
    - Foydalanuvchiga qaytarilgan miqdor haqida xabar yuboriladi.

Ikki marta ko'rib bo'lmaydi: `WithdrawalStateError` ushlanib, adminga
"allaqachon ko'rib chiqilgan" bildirish chiqadi.
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
    WITHDRAWAL_PENDING,
    RepositoryError,
    WithdrawalRepository,
    WithdrawalStateError,
)
from keyboards.builders import CB_ADM_WITHDRAWALS, get_withdrawal_decision_kb

logger = logging.getLogger(__name__)

router = Router(name="admin_withdrawals")

# faqat adminlar uchun
router.message.filter(F.from_user.id.in_(settings.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.ADMIN_IDS))

# Rad etish sababi (oddiy/tez rad etish uchun)
DEFAULT_REJECT_REASON = "Admin tomonidan rad etildi"

# Ro'yxat sarlavhasi
PENDING_LIST_HEADER = (
    "⏳ <b>KUTILAYOTGAN YECHIB OLISH SO'ROVLARI</b>\n\n"
    "Har bir so'rovga qaror qabul qiling 👇"
)
EMPTY_LIST_TEXT = (
    "✅ <b>Kutilayotgan so'rovlar yo'q.</b>\n\n"
    "Hozircha barcha yechib olish so'rovlari ko'rib chiqilgan."
)

# Bitta so'rov kartasi
REQUEST_TEXT = (
    "🆔 So'rov: <b>#{w_id}</b>\n"
    "👤 Foydalanuvchi: <code>{user_id}</code>\n"
    "💸 Miqdor: <b>{amount} ⭐️</b>\n"
    "🏦 Manzil: <code>{destination}</code>\n"
    "📅 Sana: {created_at}"
)

# Yechilgan (rad etilgan) xabarning qisqa ko'rinishi (tahrirlanadi)
APPROVED_VIEW = (
    "✅ <b>So'rov #{w_id} TASDIQLANDI</b>\n\n"
    "👤 <code>{user_id}</code> — 💸 <b>{amount} ⭐️</b>\n"
    "🏦 {destination}\n"
    "Bajaruvchi admin: <code>{admin_id}</code>"
)
REJECTED_VIEW = (
    "❌ <b>So'rov #{w_id} RAD ETILDI</b>\n\n"
    "👤 <code>{user_id}</code> — 💸 <b>{amount} ⭐️</b> balansga qaytarildi\n"
    "🏦 {destination}\n"
    "Bajaruvchi admin: <code>{admin_id}</code>"
)

# Foydalanuvchiga yuboriladigan xabarlar
USER_APPROVED_TEXT = (
    "✅ <b>To'lovingiz yuborildi!</b>\n\n"
    "💸 {amount} ⭐️ yechib olish so'rovingiz tasdiqlandi va "
    "{destination} manziliga yuborildi.\n\n"
    "🆔 So'rov: #{w_id}"
)
USER_REJECTED_TEXT = (
    "❌ <b>So'rov rad etildi</b>\n\n"
    "💸 {amount} ⭐️ muzlatilgan Stars balansingizga qaytarildi.\n"
    "Sabab: {reason}\n\n"
    "🆔 So'rov: #{w_id}"
)


def _request_text(w) -> str:
    """Bitta withdrawal qatori uchun kartani shakllantiradi (HTML)."""
    destination = f"{w.destination_type}:{w.destination_address}"
    created = w.created_at.strftime("%Y-%m-%d %H:%M UTC") if w.created_at else "—"
    return REQUEST_TEXT.format(
        w_id=w.id,
        user_id=w.user_id,
        amount=w.amount,
        destination=html.escape(destination),
        created_at=created,
    )


async def _notify_user(bot, user_id: int, text: str) -> None:
    """Foydalanuvchiga xabar yuboradi (blocklangan bo'lsa — faqat log)."""
    try:
        await bot.send_message(user_id, text, parse_mode="HTML")
    except TelegramAPIError as exc:
        logger.warning(
            "Foydalanuvchiga xabar yuborilmadi (user=%s): %s", user_id, exc
        )


# =============================================================
# 1. KUTILAYOTGAN SO'ROVLAR RO'YXATI
# =============================================================
@router.callback_query(F.data == CB_ADM_WITHDRAWALS)
async def withdrawals_list(callback: CallbackQuery) -> None:
    """[ 💳 Yechib olish so'rovlari ] — pending ro'yxatini ko'rsatadi."""
    # Spinner DARHOL yopiladi (DB so'rovidan oldin)
    await callback.answer()

    pending = await WithdrawalRepository.get_withdrawals(
        status=WITHDRAWAL_PENDING, limit=10
    )

    if not pending:
        await callback.message.answer(EMPTY_LIST_TEXT)
        return

    await callback.message.answer(PENDING_LIST_HEADER)
    for w in pending:
        await callback.message.answer(
            _request_text(w),
            reply_markup=get_withdrawal_decision_kb(w.id),
        )


# =============================================================
# 2. TASDIQLASH
# =============================================================
@router.callback_query(F.data.startswith("approve_payout_"))
async def withdrawal_approve(callback: CallbackQuery) -> None:
    """[ ✅ Tasdiqlash ] — so'rov approved bo'ladi, foydalanuvchiga xabar."""
    withdrawal_id = int(callback.data.rsplit("_", 1)[-1])
    admin_id = callback.from_user.id

    # Spinner DARHOL yopiladi — DB tranzaksiyasi kutdirilmaydi
    await callback.answer()

    try:
        w = await WithdrawalRepository.approve_withdrawal(
            withdrawal_id, admin_id
        )
    except WithdrawalStateError:
        await callback.message.answer("⚠️ Bu so'rov allaqachon ko'rib chiqilgan.")
        return
    except RepositoryError:
        await callback.message.answer("⚠️ So'rov topilmadi.")
        return
    except SQLAlchemyError:
        logger.exception("Withdrawal tasdiqlashda DB xatoligi: %s", withdrawal_id)
        await callback.message.answer("⚠️ Bazada xatolik yuz berdi.")
        return

    logger.info(
        "Withdrawal tasdiqlandi (admin=%s): id=%s amount=%s",
        admin_id, w.id, w.amount,
    )

    # Foydalanuvchiga "To'lovingiz yuborildi" xabari
    destination = html.escape(f"{w.destination_type}:{w.destination_address}")
    await _notify_user(
        callback.bot,
        w.user_id,
        USER_APPROVED_TEXT.format(
            amount=w.amount, destination=destination, w_id=w.id
        ),
    )

    # Admin xabarini yakuniy holatga o'zgartirish (tugmalar olib tashlanadi)
    try:
        await callback.message.edit_text(
            APPROVED_VIEW.format(
                w_id=w.id, user_id=w.user_id, amount=w.amount,
                destination=destination, admin_id=admin_id,
            )
        )
    except TelegramBadRequest:
        pass  # xabar tahrirlab bo'lmadi — e'tiborsiz


# =============================================================
# 3. RAD ETISH (refund bilan)
# =============================================================
@router.callback_query(F.data.startswith("reject_payout_"))
async def withdrawal_reject(callback: CallbackQuery) -> None:
    """
    [ ❌ Rad etish ] — so'rov rejected bo'ladi va muzlatilgan Stars
    foydalanuvchi balansiga ATOMIK tarzda qaytariladi (refund).
    """
    withdrawal_id = int(callback.data.rsplit("_", 1)[-1])
    admin_id = callback.from_user.id

    # Spinner DARHOL yopiladi — DB tranzaksiyasi kutdirilmaydi
    await callback.answer()

    try:
        w = await WithdrawalRepository.reject_withdrawal(
            withdrawal_id, admin_id, DEFAULT_REJECT_REASON
        )
    except WithdrawalStateError:
        await callback.message.answer("⚠️ Bu so'rov allaqachon ko'rib chiqilgan.")
        return
    except RepositoryError:
        await callback.message.answer("⚠️ So'rov topilmadi.")
        return
    except SQLAlchemyError:
        logger.exception("Withdrawal rad etishda DB xatoligi: %s", withdrawal_id)
        await callback.message.answer("⚠️ Bazada xatolik yuz berdi.")
        return

    logger.info(
        "Withdrawal rad etildi (admin=%s): id=%s amount=%s reason=%s",
        admin_id, w.id, w.amount, DEFAULT_REJECT_REASON,
    )

    # Foydalanuvchiga rad etish + refund haqida xabar
    destination = html.escape(f"{w.destination_type}:{w.destination_address}")
    await _notify_user(
        callback.bot,
        w.user_id,
        USER_REJECTED_TEXT.format(
            amount=w.amount, reason=DEFAULT_REJECT_REASON, w_id=w.id
        ),
    )

    # Admin xabarini yakuniy holatga o'zgartirish
    try:
        await callback.message.edit_text(
            REJECTED_VIEW.format(
                w_id=w.id, user_id=w.user_id, amount=w.amount,
                destination=destination, admin_id=admin_id,
            )
        )
    except TelegramBadRequest:
        pass
