"""
services/payments.py
====================
Rasmiy Telegram Stars (XTR) to'lov servisi.

Telegram Stars to'lovlari Bot API'da maxsus rejimda ishlaydi:
 - `currency="XTR"` — valyuta aynan XTR;
 - `provider_token=""` — Stars to'lovlari uchun provider token
   talab qilinmaydi (shuning uchun bo'sh qoldiriladi!);
 - narxlar `LabeledPrice` ko'rinishida yuboriladi;
 - `successful_payment.telegram_payment_charge_id` — to'lovning
   yagona ID'si (idempotency uchun `orders` jadvalida UNIQUE).

Invoys payload'i buyurtmani identifikatsiya qiladi:
    "order:{order_id}:{user_id}"

`pre_checkout_query` va `successful_payment` handlerlari shu payload'ni
qayta o'qib, qaysi buyurtma to'langanini aniqlaydi.

Ishlatish:
    from services.payments import send_stars_invoice, build_order_payload

    await send_stars_invoice(
        bot=message.bot,
        chat_id=message.chat.id,
        title="100 Stars",
        description="100 Telegram Stars to'plami",
        payload=build_order_payload(order.id, user_id),
        amount_xtr=100,
    )
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import LabeledPrice, Message

logger = logging.getLogger(__name__)

# Invoys start parameter — Telegram chekda/qayta yuborishda qulay nom
STARS_INVOICE_START_PARAMETER: str = "stars-purchase"

# Payload konventsiyasi: "order:{order_id}:{user_id}"
_PAYLOAD_PREFIX: str = "order"
_PAYLOAD_PARTS: int = 3

# Ochiq prefiks — handlerlar pre_checkout/successful_payment'ni ajratishi uchun.
ORDER_PAYLOAD_PREFIX: str = _PAYLOAD_PREFIX

# Balans to'ldirish invoysi: "topup:{deposit_id}:{user_id}"
TOPUP_PAYLOAD_PREFIX: str = "topup"


def build_order_payload(order_id: int, user_id: int) -> str:
    """
    Invoys payload'ini shakllantiradi.

    `pre_checkout_query` va `successful_payment` shu payload orqali
    qaysi buyurtma to'langanini aniqlaydi.

    Returns: "order:{order_id}:{user_id}"
    """
    return f"{_PAYLOAD_PREFIX}:{order_id}:{user_id}"


def parse_order_payload(payload: str) -> tuple[int, int] | None:
    """
    `build_order_payload` qurgan payload'ni qayta o'qiydi.

    Noto'g'ri formatlar (boshqa prefiks, son emas, kam qism)
    -> None (handler xatolikka tayyor bo'lishi kerak).

    Returns: (order_id, user_id) yoki None.
    """
    parts = (payload or "").split(":")
    if len(parts) != _PAYLOAD_PARTS or parts[0] != _PAYLOAD_PREFIX:
        return None
    try:
        order_id = int(parts[1])
        user_id = int(parts[2])
    except ValueError:
        return None
    if order_id <= 0 or user_id <= 0:
        return None
    return order_id, user_id


def build_topup_payload(deposit_id: int, user_id: int) -> str:
    """
    Balans to'ldirish invoysi uchun payload.

    Returns: "topup:{deposit_id}:{user_id}"
    """
    return f"{TOPUP_PAYLOAD_PREFIX}:{deposit_id}:{user_id}"


def parse_topup_payload(payload: str) -> tuple[int, int] | None:
    """
    `build_topup_payload` qurgan payload'ni qayta o'qiydi.

    Returns: (deposit_id, user_id) yoki None (noto'g'ri formatda).
    """
    parts = (payload or "").split(":")
    if len(parts) != _PAYLOAD_PARTS or parts[0] != TOPUP_PAYLOAD_PREFIX:
        return None
    try:
        deposit_id = int(parts[1])
        user_id = int(parts[2])
    except ValueError:
        return None
    if deposit_id <= 0 or user_id <= 0:
        return None
    return deposit_id, user_id


async def send_stars_invoice(
    bot: Bot,
    chat_id: int,
    title: str,
    description: str,
    payload: str,
    amount_xtr: int,
) -> Message:
    """
    Rasmiy Telegram Stars (XTR) invoicesini yuboradi.

    Foydalanuvchi Telegram ichidagi Stars balansi bilan to'laydi —
    provider token talab qilinmaydi (`provider_token=""`).

    Args:
        bot: aiogram Bot instansi.
        chat_id: Xarid qilayotgan foydalanuvchining chat IDsi.
        title: Invoys sarlavhasi (mahsulot nomi).
        description: Invoys tavsifi.
        payload: Buyurtma identifikatsiyasi ("order:{id}:{user_id}").
        amount_xtr: Narx (butun son, XTR da).

    Returns: yuborilgan Invoice xabari (aiogram Message).

    Raises:
        ValueError: amount_xtr <= 0.
        TelegramAPIError: Telegram tomonidan rad etilsa
            (aiogram.exceptions'dan uzatiladi — handler ushlaydi).
    """
    if amount_xtr <= 0:
        raise ValueError("amount_xtr 0 dan katta bo'lishi kerak")

    invoice_message = await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        # Telegram Stars to'lovlarda provider_token bo'sh qoldiriladi!
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=amount_xtr)],
        start_parameter=STARS_INVOICE_START_PARAMETER,
    )
    logger.info(
        "XTR invoice yuborildi: chat=%s amount=%sXTR payload=%s",
        chat_id, amount_xtr, payload,
    )
    return invoice_message
