"""
middlewares/throttling.py
=========================
Spam/flood himoyasi — aiogram 3.x BaseMiddleware.

Qoidalari:
 1. Admin (`settings.ADMIN_IDS`) — cheklovsiz o'tadi (panel doim ishlaydi).
 2. Oddiy foydalanuvchi: `COOLDOWN` soniya ichida takroriy hodisa
    RAD ETILADI (handler chaqirilmaydi) — tez bosish va yig'uvchi
    (scraping) botlar uchun minimal himoya.
 3. CallbackQuery cheklanganda unga yengil `answer()` beriladi —
    Telegramdagi "so'rovlash" belgisi (spinner) uzoq turib qolmasligi uchun.
 4. Xotira ichidagi eski yozuvlar muntazam ravishda tozalanadi
    (soat: memory leak yo'q).

Ulashish (main.py da):
    from middlewares.throttling import ThrottlingMiddleware
    dp.update.outer_middleware(ThrottlingMiddleware())
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import TelegramObject

from config.settings import settings

logger = logging.getLogger(__name__)

# Bir xil foydalanuvchi uchun minimal oraliq (soniya)
DEFAULT_COOLDOWN = 0.7

# Xotiradagi yozuvlar soni shu chekdan oshsa eski yozuvlar tozalanadi
_MAX_ENTRIES = 10_000


class ThrottlingMiddleware(BaseMiddleware):
    """Foydalanuvchi boshidagi tezlikni cheklaydigan middleware."""

    def __init__(self, cooldown: float = DEFAULT_COOLDOWN) -> None:
        self._cooldown = cooldown
        self._last_seen: dict[int, float] = {}

    # ------------------------------------------------------------------
    def _prune(self, now: float) -> None:
        """Eskirgan (cooldown o'tgan) yozuvlarni tozalaydi."""
        if len(self._last_seen) < _MAX_ENTRIES:
            return
        cutoff = now - self._cooldown
        for user_id, seen_at in list(self._last_seen.items()):
            if seen_at < cutoff:
                del self._last_seen[user_id]

    # ------------------------------------------------------------------
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")

        # 1) Foydalanuvchi aniqlanmagan hodisalar — cheklovsiz
        if user is None:
            return await handler(event, data)

        # 2) Adminlar — panel doimiy ravishda ishlashi uchun cheklovsiz
        if user.id in settings.ADMIN_IDS:
            return await handler(event, data)

        # 3) Rate-limit tekshiruvi
        now = time.monotonic()
        self._prune(now)

        last = self._last_seen.get(user.id)
        if last is not None and (now - last) < self._cooldown:
            logger.debug("Throttling: user=%s hodisa rad etildi", user.id)
            # Callback bo'lsa — spinner yopiladi (xatolik e'tiborsiz)
            callback = getattr(event, "callback_query", None)
            if callback is not None:
                try:
                    await callback.answer("⏱ Biroz kuting…", show_alert=False)
                except TelegramAPIError:
                    pass
            return None

        self._last_seen[user.id] = now
        return await handler(event, data)
