"""
middlewares/throttling.py
=========================
Spam/flood himoyasi — aiogram 3.x BaseMiddleware.

Qoidalari:
 1. Admin (`settings.ADMIN_IDS`) — cheklovsiz o'tadi (panel doim ishlaydi).
 2. Xabarlar (Message): `COOLDOWN` soniya ichida takroriy matn RAD ETILADI
    — matn flooding'i va yig'uvchi (scraping) botlarga qarshi himoya.
 3. CallbackQuery'lar GLOBAL cooldown'ga tushMAYDI: turli tugmalarni
    ketma-ket bosish har doim ishlaydi (aks holda menyu tugmalari
    "javob bermayotgandek" ko'rinadi). Faqat AYNAN bir xil tugma
    `CALLBACK_COOLDOWN` ichida qayta bosilsa — dublikat rad etiladi
    (tasodifiy ikki marta bosishdan himoya).
 4. Rad etilgan callback'ga yengil `answer()` beriladi — Telegramdagi
    "so'rovlash" belgisi (spinner) uzoq turib qolmasligi uchun.
 5. Xotira ichidagi eski yozuvlar muntazam ravishda tozalanadi
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
from aiogram.types import CallbackQuery, TelegramObject

from config.settings import settings

logger = logging.getLogger(__name__)

# Xabarlar uchun minimal oraliq (soniya)
DEFAULT_COOLDOWN = 0.7

# Bir xil callback (aynan o'sha tugma) uchun minimal oraliq (soniya).
# Turli tugmalar orasida cheklov YO'Q — menyu navigatsiyasi bloklanmaydi.
CALLBACK_COOLDOWN = 0.5

# Xotiradagi yozuvlar soni shu chekdan oshsa eski yozuvlar tozalanadi
_MAX_ENTRIES = 10_000


def _extract_callback(event: TelegramObject) -> CallbackQuery | None:
    """
    Hodisadan CallbackQuery'ni ajratib oladi.

    `dp.update.outer_middleware` da `event` — Update (callback_query ichida),
    lekin middleware to'g'ridan-to'g'ri callback bilan ham chaqirilishi mumkin.
    """
    if isinstance(event, CallbackQuery):
        return event
    return getattr(event, "callback_query", None)


class ThrottlingMiddleware(BaseMiddleware):
    """Foydalanuvchi boshidagi tezlikni cheklaydigan middleware."""

    def __init__(
        self,
        cooldown: float = DEFAULT_COOLDOWN,
        callback_cooldown: float = CALLBACK_COOLDOWN,
    ) -> None:
        self._cooldown = cooldown
        self._callback_cooldown = callback_cooldown
        self._last_seen: dict[int, float] = {}
        # (user_id, callback_data) -> vaqt
        self._last_callback: dict[tuple[int, str], float] = {}

    # ------------------------------------------------------------------
    def _prune(self, now: float) -> None:
        """Eskirgan (cooldown o'tgan) yozuvlarni tozalaydi."""
        if len(self._last_seen) < _MAX_ENTRIES:
            return
        cutoff = now - self._cooldown
        for user_id, seen_at in list(self._last_seen.items()):
            if seen_at < cutoff:
                del self._last_seen[user_id]

        callback_cutoff = now - self._callback_cooldown
        for key, seen_at in list(self._last_callback.items()):
            if seen_at < callback_cutoff:
                del self._last_callback[key]

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

        now = time.monotonic()
        self._prune(now)

        # 3) CALLBACK — faqat AYNAN bir xil tugmaning dublikati cheklanadi.
        #    Turli tugmalar ketma-ket bosilsa — hammasi ishlaydi.
        callback = _extract_callback(event)
        if callback is not None:
            key = (user.id, callback.data or "")
            last = self._last_callback.get(key)
            if last is not None and (now - last) < self._callback_cooldown:
                logger.debug(
                    "Throttling: user=%s callback dublikati rad etildi (%s)",
                    user.id, callback.data,
                )
                try:
                    await callback.answer("⏱ Biroz kuting…", show_alert=False)
                except TelegramAPIError:
                    pass
                return None
            self._last_callback[key] = now
            return await handler(event, data)

        # 4) MESSAGE — oddiy cooldown (flood himoyasi)
        last = self._last_seen.get(user.id)
        if last is not None and (now - last) < self._cooldown:
            logger.debug("Throttling: user=%s hodisa rad etildi", user.id)
            return None

        self._last_seen[user.id] = now
        return await handler(event, data)
