"""
middlewares/blocking.py
=======================
Bloklangan foydalanuvchilarni to'xtatuvchi middleware.

`user_blocks` jadvalida bor foydalanuvchilarning barcha xabar/callback/
pre_checkout hodisalari to'xtatiladi (to'lovlar ham — bloklangan odam
to'lov qila olmaydi). Adminlar bundan mustasno.

Tezlik: har hodisa uchun DB so'rovi qilmaslik uchun per-user kesh
(60 soniya) ishlatiladi — blok keyingi 1 daqiqa ichida kuchga kiradi.

Ishlatish (main.py):
    from middlewares.blocking import BlockingMiddleware
    dp.update.outer_middleware(BlockingMiddleware())
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from config.settings import settings
from database.queries import BlockRepository

logger = logging.getLogger(__name__)

# Kesh: user_id -> (is_blocked, timestamp)
_block_cache: dict[int, tuple[bool, float]] = {}
_CACHE_TTL = 60.0  # soniya

# Bloklangan foydalanuvchiga yuboriladigan xabar
BLOCKED_TEXT = (
    "🚫 Siz botdan foydalanish huquqini yo'qotdingiz.\n"
    "Savol bo'lsa administratorga murojaat qiling."
)


def clear_cache() -> None:
    """Keshni tozalaydi (testlar uchun)."""
    _block_cache.clear()


async def is_blocked_cached(user_id: int) -> bool:
    """Foydalanuvchi bloklanganmi — kesh bilan (60s TTL)."""
    now = time.monotonic()
    cached = _block_cache.get(user_id)
    if cached is not None and now - cached[1] < _CACHE_TTL:
        return cached[0]

    blocked = await BlockRepository.is_blocked(user_id)
    _block_cache[user_id] = (blocked, now)
    return blocked


def invalidate_user(user_id: int) -> None:
    """Bitta foydalanuvchi keshini yangilash (bloklaganda/ochganda chaqiriladi)."""
    _block_cache.pop(user_id, None)


def _unwrap(event: TelegramObject) -> TelegramObject:
    """Update bo'lsa ichidagi haqiqiy hodisani qaytaradi."""
    if not isinstance(event, Update):
        return event
    for field in (
        "message",
        "callback_query",
        "pre_checkout_query",
        "edited_message",
    ):
        inner = getattr(event, field, None)
        if inner is not None:
            return inner
    return event


class BlockingMiddleware(BaseMiddleware):
    """Bloklangan foydalanuvchilarning hodisalarini to'xtatadi."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        inner = _unwrap(event)
        user = data.get("event_from_user") or getattr(inner, "from_user", None)

        # Foydalanuvchi aniqlanmagan hodisalar (kanal postlari, ...) — o'tadi
        if user is None:
            return await handler(event, data)

        # Adminlar hech qachon bloklanmaydi
        if user.id in settings.ADMIN_IDS:
            return await handler(event, data)

        if not await is_blocked_cached(user.id):
            return await handler(event, data)

        # Bloklangan — hodisa to'xtatiladi
        logger.debug("Bloklangan foydalanuvchi hodisasi rad etildi: %s", user.id)
        try:
            # Message.answer() — javob xabari; CallbackQuery.answer() — alert.
            await inner.answer(BLOCKED_TEXT)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — javob majburiy emas (eski xabar, blok)
            pass
        return None
