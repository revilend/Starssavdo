"""
services/deposit_expiry.py
==========================
Karta to'lovi so'rovlarining 5 daqiqalik muddatini boshqaradi.

Oqim:
 1. `handlers/user/topup.py` karta so'rovini `expires_at = now + 5 daqiqa`
    bilan yaratadi.
 2. Shu modul fon vazifasi (startupda `main.py` da ishga tushadi) har
    `CHECK_INTERVAL` soniyada muddati o'tgan `pending` so'rovlarni topadi,
    ularni atomik `expired` holatiga o'tkazadi va foydalanuvchining to'lov
    xabarini [🔄 Qaytadan to'ldirish] tugmasi bilan yangilaydi.
 3. `expired` so'rovga kechikkan to'lov **hisoblanmaydi** —
    `DepositRepository.auto_confirm_deposit` faqat muddati o'tmagan
    `pending` so'rovni tasdiqlaydi.

Muhim: bu vazifa bitta jarayonda (uvicorn worker) ishlaydi; ko'p worker
ishlatilsa `expire_deposit` atomik bo'lgani uchun balans baribir ikki marta
o'zgarmaydi (faqat xabar yangilash ikki marta urinilishi mumkin).
"""

from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramAPIError
from sqlalchemy.exc import SQLAlchemyError

from database.queries import DepositRepository
from keyboards.inline import get_topup_expired_kb
from services.card_payment import format_som
from services.order_expiry import process_expired_orders

logger = logging.getLogger(__name__)

# Muddati o'tgan so'rovlarni tekshirish oralig'i (soniya)
CHECK_INTERVAL = 15

EXPIRED_TEXT = (
    "⏳ <b>To'lov muddati tugadi</b>\n\n"
    "├ Miqdor: <b>{amount} ⭐️</b>\n"
    "├ To'lov summasi: <b>{pay_amount} so'm</b>\n"
    "└ So'rov: <code>#{deposit_id}</code>\n\n"
    "5 daqiqa ichida to'lov qilinmadi — so'rov bekor qilindi. "
    "Kechikkan to'lov balansga qo'shilmaydi.\n\n"
    "Qaytadan urinib ko'rish uchun tugmani bosing 👇"
)


async def _notify_expired(bot, deposit) -> None:
    """Foydalanuvchining to'lov xabarini \"muddati tugadi\" matniga o'zgartiradi."""
    text = EXPIRED_TEXT.format(
        amount=deposit.amount,
        pay_amount=format_som(deposit.pay_amount or 0),
        deposit_id=deposit.id,
    )
    markup = get_topup_expired_kb()

    if deposit.payment_message_id:
        try:
            await bot.edit_message_text(
                chat_id=deposit.user_id,
                message_id=deposit.payment_message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup,
            )
            return
        except TelegramAPIError as exc:
            logger.debug(
                "Muddati o'tgan xabarni yangilab bo'lmadi (deposit=%s): %s",
                deposit.id, exc,
            )

    # Xabar ID'si yo'q bo'lsa (eski so'rov) — yangi xabar yuboriladi
    try:
        await bot.send_message(
            deposit.user_id, text, parse_mode="HTML", reply_markup=markup
        )
    except TelegramAPIError as exc:
        logger.warning(
            "Muddat xabarini yuborib bo'lmadi (user=%s): %s",
            deposit.user_id, exc,
        )


async def process_expired_deposits(bot) -> int:
    """
    Bir marta muddati o'tgan so'rovlarni yopadi.

    Returns:
        `expired` holatiga o'tkazilgan so'rovlar soni.
    """
    try:
        deposits = await DepositRepository.get_expired_deposits()
    except SQLAlchemyError:
        logger.exception("Muddati o'tgan so'rovlarni o'qib bo'lmadi")
        return 0

    expired_count = 0
    for deposit in deposits:
        try:
            expired = await DepositRepository.expire_deposit(deposit.id)
        except SQLAlchemyError:
            logger.exception("So'rovni expired qilib bo'lmadi: id=%s", deposit.id)
            continue
        if expired is None:
            # Boshqa jarayon allaqachon yopgan yoki to'lov kelib ulgurgan
            continue
        expired_count += 1
        await _notify_expired(bot, expired)

    if expired_count:
        logger.info("Muddati o'tgan so'rovlar yopildi: %s ta", expired_count)
    return expired_count


async def expiry_worker(bot, interval: int = CHECK_INTERVAL) -> None:
    """
    Fon vazifasi: `interval` soniyada bir marta muddatlarni tekshiradi.

    Xatolik (DB/tarmoq) vazifani to'xtatmaydi — logga yozilib, keyingi
    urinish davom etadi. Bekor qilinganda (shutdown) `CancelledError`
    yuqoriga uzatiladi.
    """
    logger.info("Deposit expiry worker boshlandi (har %s soniya).", interval)
    while True:
        try:
            await process_expired_deposits(bot)
            await process_expired_orders(bot)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — fon vazifasi hech qachon o'lmasin
            logger.exception("Expiry worker: kutilmagan xatolik")

        await asyncio.sleep(interval)
