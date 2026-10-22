"""
services/gift_delivery.py
=========================
Telegram sovg'alarini (gift) avtomatik yuborish — Bot API `sendGift`.

Mahsulot `payload` (JSON) ichida rasmiy Telegram `gift_id` bo'lsa, to'lov
muvaffaqiyatli yakunlangach sovg'a foydalanuvchiga avtomatik jo'natiladi:

    {"gift_id": "5170145012310081615", ...}

yoki metadata ichida:

    {"metadata": {"gift_id": "..."}}

`gift_id` yo'q bo'lsa yoki `sendGift` xato bersa — `False` qaytadi va
chaqiruvchi (shop handler) adminga qo'lda yetkazish uchun xabar yuboradi.

Muhim: `sendGift` sovg'a narxini **botning Stars balansidan** yechadi;
botda yetarli Stars bo'lmasa Telegram xato qaytaradi (admin fallback).
"""

from __future__ import annotations

import html
import logging
from collections.abc import Mapping
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)


def extract_gift_id(product: Any) -> str | None:
    """
    Mahsulotdan `gift_id` ni ajratib oladi.

    Qabul qilinadigan shakllar:
      - product.payload["gift_id"]
      - product.payload["metadata"]["gift_id"]

    Returns: gift_id (str) yoki None.
    """
    payload = getattr(product, "payload", None)
    if not isinstance(payload, Mapping):
        return None

    gift_id = payload.get("gift_id")
    if gift_id in (None, ""):
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping):
            gift_id = metadata.get("gift_id")

    if gift_id in (None, ""):
        return None
    return str(gift_id)


async def deliver_gift(bot: Bot, user_id: int, product: Any) -> bool:
    """
    Mahsulotdagi `gift_id` bo'yicha sovg'ani foydalanuvchiga yuboradi.

    Returns:
        True  — sovg'a yuborildi (yoki allaqachon yuborilgan).
        False — gift_id yo'q yoki Telegram xatosi (admin fallback kerak).
    """
    gift_id = extract_gift_id(product)
    if gift_id is None:
        return False

    title = html.escape(str(getattr(product, "title", "Sovg'a")))
    try:
        await bot.send_gift(
            gift_id=gift_id,
            user_id=user_id,
            text=f"🎁 <b>{title}</b> — xaridingiz uchun rahmat!",
        )
    except (TelegramAPIError, ValueError) as exc:
        logger.warning(
            "Sovg'a avtomatik yuborilmadi (user=%s gift_id=%s): %s",
            user_id,
            gift_id,
            exc,
        )
        return False
    except Exception:  # noqa: BLE001 — yetkazish xatosi to'lovni buzmasin
        logger.exception(
            "Sovg'a yuborishda kutilmagan xato (user=%s gift_id=%s)",
            user_id,
            gift_id,
        )
        return False

    logger.info(
        "Sovg'a avtomatik yuborildi: user=%s gift_id=%s product=%r",
        user_id,
        gift_id,
        getattr(product, "title", None),
    )
    return True
