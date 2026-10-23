"""
services/order_payments.py
==========================
SO'MDA to'lanadigan mahsulot buyurtmalarini tasdiqlash va yetkazish.

Oqim:
 1. `handlers/user/shop.py` buyurtma yaratadi (`orders.status=pending`,
    `pay_amount = price_uzs + 40..100`, `expires_at = now + 5 daqiqa`).
 2. `@humocardbot` xabari (Telethon yoki bot jurnal chati orqali) kelganda
    `credit_order_text()` summani faol buyurtmaga moslaydi va
    `OrderRepository.pay_order_by_amount()` bilan **atomik** `paid` qiladi.
 3. `deliver_order()` mahsulot turiga qarab yetkazadi:
      stars   — foydalanuvchi `stars_balance` iga qo'shiladi;
      gift    — Telegram `sendGift` orqali darhol yuboriladi;
      premium — @username adminga [✅ Fragment orqali yuborildi] bilan boradi;
      nft     — TON hamyon adminga [✅ NFT yuborildi] bilan boradi.
 4. Foydalanuvchi va adminlar tasdiqnoma oladi.

Idempotentlik: `pay_order_by_amount` faqat `pending` buyurtmani `paid`
qiladi; takroriy xabar balansni ham, sovg'ani ham ikki marta bermaydi.
"""

from __future__ import annotations

import html
import logging
from types import SimpleNamespace
from typing import Any

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.exc import SQLAlchemyError

from config.settings import settings
from database.models import Order
from database.queries import (
    TX_ORDER_STARS,
    OrderRepository,
    ProductRepository,
    RepositoryError,
    UserRepository,
)
from services.card_payment import find_payment_amount, format_som
from services.gift_delivery import deliver_gift
from services.order_log import notify_order_paid

logger = logging.getLogger(__name__)

# Admin tugmasi callback'i: "order_done_{order_id}"
CB_ORDER_DONE_PREFIX = "order_done_"

PAID_TEXT = (
    "✅ <b>To'lov qabul qilindi!</b>\n\n"
    "🧾 Buyurtma: <code>#{order_id}</code>\n"
    "📦 Mahsulot: <b>{title}</b>\n"
    "💰 To'langan summa: <b>{pay_amount} so'm</b>\n\n"
    "{delivery}"
)

STARS_DELIVERY_TEXT = (
    "⭐️ <b>{stars} Stars</b> balansingizga qo'shildi.\n"
    "├ Yangi balans: <b>{balance} ⭐</b>\n"
    "└ Starslarni do'kon yoki sovg'alarga ishlatishingiz mumkin."
)

GIFT_OK_TEXT = (
    "🎁 Sovg'angiz avtomatik yuborildi! Telegram profilngizdagi "
    "sovg'alar bo'limidan ko'rishingiz mumkin."
)

GIFT_PENDING_TEXT = (
    "🎁 Sovg'angiz admin tomonidan tez orada yuboriladi (odatda 5 daqiqa)."
)

PREMIUM_PENDING_TEXT = (
    "💎 Premium obuna <b>@{username}</b> uchun admin tomonidan "
    "<b>Fragment</b> orqali ulanadi (5–15 daqiqa)."
)

NFT_PENDING_TEXT = (
    "🖼 NFT <code>{wallet}</code> hamyon manziliga admin tomonidan "
    "yuboriladi (10–30 daqiqa)."
)

ADMIN_ORDER_TEXT = (
    "💳 <b>YANGI TO'LOV — NFT YUT</b>\n\n"
    "├ Buyurtma: <code>#{order_id}</code>\n"
    "├ Foydalanuvchi: <code>{user_id}</code>\n"
    "├ Mahsulot: <b>{title}</b>\n"
    "├ Narx: <b>{price} so'm</b>\n"
    "├ To'landi: <b>{pay_amount} so'm</b>\n"
    "└ Turi: <code>{category}</code>\n\n"
    "{details}"
)


def _admin_kb(order: Order, label: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=label,
            callback_data=f"{CB_ORDER_DONE_PREFIX}{order.id}",
            style="success",
        )
    )
    return builder.as_markup()


async def _notify(bot, chat_id: int, text: str, markup=None) -> None:
    """Xabar yuboradi; bloklangan foydalanuvchi oqimni to'xtatmaydi."""
    try:
        await bot.send_message(
            chat_id, text, parse_mode="HTML", reply_markup=markup
        )
    except TelegramAPIError as exc:
        logger.warning("Xabar yuborilmadi (chat=%s): %s", chat_id, exc)


async def deliver_order(
    bot,
    order: Order,
    product: Any | None = None,
) -> None:
    """
    To'langan buyurtmani mahsulot turiga qarab yetkazadi.

    Bu funksiya idempotent emas — faqat `pay_order_by_amount` dan keyin
    bir marta chaqiriladi.
    """
    data: dict[str, Any] = order.order_data or {}
    category = str(data.get("category") or getattr(product, "category", "") or "")
    title = html.escape(
        str(data.get("product_title") or getattr(product, "title", "Mahsulot"))
    )
    payload = data.get("payload")
    if not isinstance(payload, dict):
        payload = getattr(product, "payload", None) or {}

    pay_amount = format_som(order.pay_amount or order.price_uzs)
    details = ""
    delivery = "Buyurtmangiz qabul qilindi."
    admin_markup = None

    if category == "stars":
        stars = int(payload.get("stars") or order.amount_xtr or 0)
        try:
            balance = await UserRepository.update_balance(
                order.user_id,
                stars,
                tx_type=TX_ORDER_STARS,
                description=f"Stars to'plami xaridi: {title}",
                reference_id=str(order.id),
            )
        except (RepositoryError, SQLAlchemyError):
            logger.exception(
                "Stars yetkazilmadi: order=%s user=%s", order.id, order.user_id
            )
            delivery = (
                "⚠️ To'lov qabul qilindi, lekin Starsni qo'shishda xatolik "
                "bo'ldi. Admin tez orada tekshiradi."
            )
            details = "⚠️ Stars balansga qo'shilmadi — qo'lda tekshiring."
        else:
            delivery = STARS_DELIVERY_TEXT.format(stars=stars, balance=balance)
            details = f"✅ {stars} Stars balansga qo'shildi (yangi: {balance})."

    elif category == "gift":
        product_like = product or SimpleNamespace(payload=payload, title=title)
        delivered = await deliver_gift(bot, order.user_id, product_like)
        delivery = GIFT_OK_TEXT if delivered else GIFT_PENDING_TEXT
        details = (
            "✅ Sovg'a avtomatik yuborildi."
            if delivered
            else "🎁 Sovg'ani qo'lda yuborish kerak (sendGift xatosi)."
        )

    elif category == "premium":
        username = str(data.get("target_username") or "").lstrip("@")
        months = int(payload.get("months") or 0)
        delivery = PREMIUM_PENDING_TEXT.format(username=html.escape(username))
        details = (
            f"👤 Username: <b>@{html.escape(username)}</b>\n"
            f"📅 Muddat: <b>{months} oy</b>\n\n"
            "Fragment.com orqali obunani ulang va tasdiqlang."
        )
        admin_markup = _admin_kb(order, "✅ Fragment orqali yuborildi")

    elif category == "nft":
        wallet = str(data.get("wallet") or "")
        delivery = NFT_PENDING_TEXT.format(wallet=html.escape(wallet))
        details = (
            f"👛 TON hamyon: <code>{html.escape(wallet)}</code>\n\n"
            "NFT ni shu hamyonga transfer qiling va tasdiqlang."
        )
        admin_markup = _admin_kb(order, "✅ NFT yuborildi")

    else:
        logger.warning("Noma'lum mahsulot turi: order=%s category=%r", order.id, category)
        details = "⚠️ Noma'lum tur — qo'lda tekshiring."

    # 1) Foydalanuvchiga tasdiqnoma
    await _notify(
        bot,
        order.user_id,
        PAID_TEXT.format(
            order_id=order.id,
            title=title,
            pay_amount=pay_amount,
            delivery=delivery,
        ),
    )

    # 2) Adminlarga buyurtma tafsiloti
    admin_text = ADMIN_ORDER_TEXT.format(
        order_id=order.id,
        user_id=order.user_id,
        title=title,
        price=format_som(order.price_uzs),
        pay_amount=pay_amount,
        category=html.escape(category or "?"),
        details=details,
    )
    for admin_id in settings.ADMIN_IDS:
        await _notify(bot, admin_id, admin_text, admin_markup)

    # 3) Zakaz kanali (sozlangan bo'lsa) — chiroyli chek + topshirish tugmalari
    try:
        await notify_order_paid(bot, order, product=product)
    except Exception:  # noqa: BLE001 — chek yuborilishi yetkazishni buzmasin
        logger.exception("Zakaz kanali chekida kutilmagan xatolik: order=%s", order.id)

    logger.info(
        "Buyurtma yetkazildi: order=%s user=%s category=%s pay=%s",
        order.id, order.user_id, category, order.pay_amount,
    )


async def credit_order_text(text: str, bot) -> Order | None:
    """
    Jurnal xabaridagi summani faol buyurtmaga moslab, to'lovni tasdiqlaydi.

    Returns:
        To'langan `Order` yoki `None` (mos buyurtma yo'q).
    """
    try:
        orders = await OrderRepository.get_pending_card_orders()
    except SQLAlchemyError:
        logger.exception("Buyurtmalarni o'qib bo'lmadi")
        return None

    candidates = [order.pay_amount for order in orders if order.pay_amount]
    pay_amount = find_payment_amount(text, candidates)
    if pay_amount is None:
        return None

    try:
        order = await OrderRepository.pay_order_by_amount(pay_amount, comment=text)
    except SQLAlchemyError:
        logger.exception("Buyurtma to'lovini tasdiqlashda xatolik: %s", pay_amount)
        return None

    if order is None:
        logger.info(
            "To'lov %s so'm: faol buyurtma yo'q (kechikkan yoki bekor qilingan)",
            format_som(pay_amount),
        )
        return None

    try:
        product = await ProductRepository.get_product_by_id(order.product_id)
    except SQLAlchemyError:
        product = None

    await deliver_order(bot, order, product)
    return order
