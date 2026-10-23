"""
services/order_expiry.py
========================
UZS mahsulot buyurtmalarining 5 daqiqalik taymeri.

`handlers/user/shop.py` buyurtmani `expires_at = now + 5 daqiqa` bilan
yaratadi. Shu modul muddati o'tgan `pending` buyurtmalarni `expired`
qiladi va foydalanuvchining to'lov xabarini «⏳ To'lov muddati tugadi»
matniga o'zgartiradi. Kechikkan to'lov hisoblanmaydi
(`OrderRepository.pay_order_by_amount` faqat faol `pending` ni oladi).
"""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError
from sqlalchemy.exc import SQLAlchemyError

from database.queries import OrderRepository
from keyboards.inline import get_order_expired_kb
from services.card_payment import format_som

logger = logging.getLogger(__name__)

ORDER_EXPIRED_TEXT = (
    "⏳ <b>To'lov muddati tugadi</b>\n\n"
    "├ Buyurtma: <code>#{order_id}</code>\n"
    "├ Mahsulot: <b>{title}</b>\n"
    "└ To'lov summasi: <b>{pay_amount} so'm</b>\n\n"
    "5 daqiqa ichida to'lov qilinmadi — buyurtma bekor qilindi. "
    "Kechikkan to'lov hisoblanmaydi.\n\n"
    "Yangi xarid uchun do'konga qaytishingiz mumkin 👇"
)


async def _notify_expired_order(bot, order) -> None:
    """To'lov xabarini \"muddati tugadi\" holatiga o'zgartiradi."""
    data = order.order_data or {}
    text = ORDER_EXPIRED_TEXT.format(
        order_id=order.id,
        title=str(data.get("product_title") or "Mahsulot"),
        pay_amount=format_som(order.pay_amount or order.price_uzs),
    )

    if order.payment_message_id:
        try:
            await bot.edit_message_text(
                chat_id=order.user_id,
                message_id=order.payment_message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=get_order_expired_kb(),
            )
            return
        except TelegramAPIError as exc:
            logger.debug(
                "Buyurtma xabarini yangilab bo'lmadi (order=%s): %s",
                order.id, exc,
            )

    try:
        await bot.send_message(
            order.user_id,
            text,
            parse_mode="HTML",
            reply_markup=get_order_expired_kb(),
        )
    except TelegramAPIError as exc:
        logger.warning(
            "Buyurtma muddat xabari yuborilmadi (user=%s): %s",
            order.user_id, exc,
        )


async def process_expired_orders(bot) -> int:
    """Muddati o'tgan buyurtmalarni yopadi. Returns: yopilganlar soni."""
    try:
        orders = await OrderRepository.get_expired_orders()
    except SQLAlchemyError:
        logger.exception("Muddati o'tgan buyurtmalarni o'qib bo'lmadi")
        return 0

    count = 0
    for order in orders:
        try:
            expired = await OrderRepository.expire_order(order.id)
        except SQLAlchemyError:
            logger.exception("Buyurtmani expired qilib bo'lmadi: %s", order.id)
            continue
        if expired is None:
            continue
        count += 1
        await _notify_expired_order(bot, expired)

    if count:
        logger.info("Muddati o'tgan buyurtmalar yopildi: %s ta", count)
    return count
