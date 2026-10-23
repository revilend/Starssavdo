"""
middlewares/subscription.py
===========================
Majburiy kanallar (OP) tekshiruvchi middleware — aiogram 3.x BaseMiddleware.

Qoidalari:
 1. Admin (`settings.ADMIN_IDS`) — tekshiruvsiz o'tadi.
 2. `check_subscription` callback'i — bloklanmaydi (aynan shu tugma
    qayta tekshiruv uchun ishlatiladi).
 3. TASDIQLANGAN foydalanuvchi (xotiradagi kesh YOKI bazada
    `channels_verified = True`) hech qanday tekshiruvsiz darhol
    o'tkaziladi — har bir tugma bosilishida Telegram API ham, baza ham
    so'ralmaydi (tezlik uchun asosiy optimizatsiya).
 4. Boshqa barcha xabar/callbacklar: foydalanuvchi kanallarga a'zo emas
    bo'lsa hodisa TO'XTATILADI (`return None`) va unga ogohlantirish +
    kanallar ro'yxati + "✅ A'zo bo'ldim" tugmasi yuboriladi.
 5. Faol kanallar ro'yxati servis tomonidan qisqa muddat keshlanadi,
    a'zolik holati esa 120 soniya keshlanadi — Telegram API limiti
    (TooManyRequests) xavfi yo'q.

Ulashish (bot.py da):
    from middlewares.subscription import SubscriptionMiddleware

    dp.update.outer_middleware(SubscriptionMiddleware())
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, TelegramObject
from cachetools import TTLCache

from config.settings import settings
from database.queries import UserRepository
from keyboards.inline import CB_CHECK_SUBSCRIPTION, get_subscription_kb
from services.channel_checker import (
    check_user_subscription,
    get_active_channel_dicts,
)

logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# Tasdiqlangan foydalanuvchilar keshi
# - Bir marta kanallarga a'zo bo'lgan foydalanuvchi IDsi shu yerga yoziladi.
# - Keyingi yangilanishlarda middleware na Telegram API'ga, na bazaga
#   murojaat qiladi — faqat dict tekshiruvi (~mikrosekund).
# - TTL 1 soat: uzoq tanaffusdan keyin holat qayta tekshiriladi
#   (foydalanuvchi kanallardan chiqib ketgan bo'lishi mumkin).
# -------------------------------------------------------------
VERIFIED_CACHE_TTL = 3600          # soniya
VERIFIED_CACHE_MAXSIZE = 50_000    # ta foydalanuvchi
_verified_cache: TTLCache = TTLCache(
    maxsize=VERIFIED_CACHE_MAXSIZE, ttl=VERIFIED_CACHE_TTL
)


def invalidate_verified_cache(user_id: int | None = None) -> None:
    """
    Tasdiqlangan foydalanuvchilar keshini tozalaydi.

    Args:
        user_id: berilsa — faqat shu foydalanuvchi, aks holda butun kesh.
    """
    if user_id is None:
        _verified_cache.clear()
        logger.info("Tasdiqlangan foydalanuvchilar keshi tozalandi")
        return
    _verified_cache.pop(user_id, None)


# Ogohlantirish matni (HTML parse_mode bilan yuboriladi)
SUBSCRIPTION_PROMPT_TEXT = (
    "⚠️ <b>Botdan to'liq foydalanish va Stars ishlash uchun quyidagi "
    "homiy kanallarga a'zo bo'ling:</b>"
)


class SubscriptionMiddleware(BaseMiddleware):
    """
    Har bir hodisadan oldin majburiy kanallar a'zoligini tekshiradi.

    Faqat private xabarlar/callbacklar uchun ishlaydi (channel/post xabarlar
    botga tushmaydi, lekin himoya sifatida tekshiriladi).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")

        # 1) Foydalanuvchi aniqlanmagan hodisalar (masalan, kanal xabarlari)
        if user is None:
            return await handler(event, data)

        # 2) Adminlar — tekshiruvsiz
        if user.id in settings.ADMIN_IDS:
            return await handler(event, data)

        # 3) "✅ A'zo bo'ldim" tugmasi bloklanmaydi — u qayta tekshiruv uchun
        if isinstance(event, CallbackQuery) and event.data == CB_CHECK_SUBSCRIPTION:
            return await handler(event, data)

        # 4) XOTIRA KESHI — bir marta tasdiqlangan foydalanuvchi.
        #    Eng tez yo'l: DB so'rovi ham, Telegram API ham YO'Q.
        if user.id in _verified_cache:
            return await handler(event, data)

        # 5) Faol kanallar ro'yxati (keshli, 60 soniya)
        channels = await get_active_channel_dicts()
        if not channels:
            # Majburiy kanallar o'rnatilmagan — erkin foydalanish
            return await handler(event, data)

        # 6) BAZA BELGISI — `channels_verified = True`.
        #    Bir marta "✅ A'zo bo'ldim" bosilgach, har bir tugma bosilishida
        #    qayta API so'rovi yuborilmaydi.
        if await self._is_verified(user.id):
            return await handler(event, data)

        bot: Bot = data["bot"]
        is_subscribed, unjoined = await check_user_subscription(bot, user.id, channels)

        if is_subscribed:
            # Keyingi yangilanishlar uchun keshlaymiz (DB/API'siz o'tadi)
            _verified_cache[user.id] = True
            return await handler(event, data)

        # 7) A'zo emas — hodisani to'xtatamiz va yo'naltiruvchi yuboramiz
        logger.debug("user=%s obuna tekshiruvidan o'tmadi", user.id)
        await self._send_subscription_prompt(event, bot, unjoined)
        return None

    # ------------------------------------------------------------------
    @staticmethod
    async def _is_verified(user_id: int) -> bool:
        """
        Bazadagi `channels_verified` belgisini tekshiradi (xotira keshi bilan).

        Baza vaqtincha ishlamasa ham bot to'xtab qolmasligi uchun xatolik
        loglanadi va `False` qaytadi (keyin odatdagi Telegram tekshiruvi
        bajariladi).
        """
        try:
            verified = await UserRepository.is_channels_verified(user_id)
        except Exception:  # noqa: BLE001 — DB xatosi bot ishini to'xtatmasin
            logger.exception(
                "channels_verified tekshirib bo'lmadi (user=%s)", user_id
            )
            return False

        if verified:
            _verified_cache[user_id] = True
        return verified

    # ------------------------------------------------------------------
    @staticmethod
    async def _send_subscription_prompt(
        event: TelegramObject, bot: Bot, unjoined: list[Any]
    ) -> None:
        """
        Ogohlantirish xabari + kanallar klaviaturasini yuboradi.

        - CallbackQuery hodisasi: callback tasdiqlanadi (spinner yopiladi)
          va kanal chatiga xabar yuboriladi.
        - Message hodisasi: xuddi shu chatga javob sifatida yuboriladi.

        Xatolar (foydalanuvchi botni blocklagan, xabar eskirgan va h.k.)
        ushlanadi va loglanadi — middleware hech qachon yangilanishni
        butunlay sindirmaydi.
        """
        markup = get_subscription_kb(unjoined)

        try:
            if isinstance(event, CallbackQuery):
                # Spinner'ni yopish (javob bermaslik Telegramda "so'rovlash" belgisini qoldiradi)
                try:
                    await event.answer()
                except TelegramAPIError:
                    pass

                target_chat_id = event.message.chat.id if event.message else None
                if target_chat_id is not None:
                    await bot.send_message(
                        chat_id=target_chat_id,
                        text=SUBSCRIPTION_PROMPT_TEXT,
                        reply_markup=markup,
                    )
                return

            # Message va boshqa "answer" metodi bor hodisalar
            answer = getattr(event, "answer", None)
            if callable(answer):
                await answer(SUBSCRIPTION_PROMPT_TEXT, reply_markup=markup)
                return

            # Kutilmagan hodisa turi — javob bera olmaymiz
            logger.warning(
                "Obuna prompti uchun answer metodi yo'q hodisa: %s",
                type(event).__name__,
            )
        except (TelegramAPIError, AttributeError) as exc:
            logger.warning(
                "Obuna promptini yuborib bo'lmadi (user event): %s", exc
            )
