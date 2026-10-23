"""
middlewares/subscription.py
===========================
Majburiy kanallar (OP) tekshiruvchi middleware — aiogram 3.x BaseMiddleware.

Qoidalari:
 0. To'lov hodisalari (`pre_checkout_query`, `message.successful_payment`)
    TEKSHIRUVSIZ o'tkaziladi — pul hech qachon bloklanmaydi.
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
 4. Faol kanallar ro'yxati servis tomonidan qisqa muddat keshlanadi,
    a'zolik holati esa 120 soniya keshlanadi — Telegram API limiti
    (TooManyRequests) xavfi yo'q.

Muhim: middleware `dp.update.outer_middleware(...)` orqali ulanadi, ya'ni
`event` — `Update`. Shuning uchun ichidagi haqiqiy hodisa
(`message` / `callback_query`) `_unwrap_event()` bilan ajratib olinadi;
aks holda `isinstance(event, CallbackQuery)` hech qachon bajarilmaydi va
a'zo bo'lmagan foydalanuvchi hech qanday javob olmay qoladi.

Ulashish (main.py da):
    from middlewares.subscription import SubscriptionMiddleware

    dp.update.outer_middleware(SubscriptionMiddleware())
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    Message,
    PreCheckoutQuery,
    TelegramObject,
    Update,
)
from cachetools import TTLCache

from config.settings import settings
from keyboards.inline import CB_CHECK_SUBSCRIPTION, get_subscription_kb
from database.queries import UserRepository
from services.channel_checker import (
    check_user_subscription,
    get_active_channel_dicts,
)

logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# Tasdiqlangan foydalanuvchilar keshi
# - Bir marta kanallarga a'zo bo'lgan foydalanuvchi IDsi shu keshga yoziladi.
# - Keyingi yangilanishlarda middleware na Telegram API'ga, na bazaga
#   murojaat qiladi — faqat kesh tekshiruvi (~mikrosekund).
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

# `Update` ichidan qidiriladigan hodisa maydonlari (tartib muhim)
_UPDATE_FIELDS: tuple[str, ...] = (
    "message",
    "callback_query",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_message",
    "edited_business_message",
    "pre_checkout_query",
)


def _unwrap_event(event: TelegramObject) -> TelegramObject | None:
    """
    `Update` bo'lsa ichidagi haqiqiy hodisani qaytaradi.

    Boshqa turdagi hodisalar (Message, CallbackQuery, ...) o'zgarishsiz
    qaytadi. `Update` ichida tanilgan hodisa bo'lmasa — `None`.
    """
    if not isinstance(event, Update):
        return event

    for field in _UPDATE_FIELDS:
        inner = getattr(event, field, None)
        if inner is not None:
            return inner

    return None


def _is_payment_event(event: TelegramObject) -> bool:
    """
    To'lov bilan bog'liq hodisami?

    - `pre_checkout_query` — Telegram to'lovni tasdiqlashni so'raydi
      (10 soniya ichida javob berilmasa to'lov bekor bo'ladi);
    - `message.successful_payment` — to'lov muvaffaqiyatli yakunlandi.

    Bu hodisalar obuna tekshiruvi bilan hech qachon to'silmasligi kerak.
    """
    if isinstance(event, PreCheckoutQuery):
        return True

    if isinstance(event, Update):
        if event.pre_checkout_query is not None:
            return True
        message = event.message or event.edited_message
        return bool(message is not None and message.successful_payment is not None)

    if isinstance(event, Message):
        return event.successful_payment is not None

    return False


def _event_user(event: TelegramObject) -> Any:
    """Hodisadan foydalanuvchini ajratib oladi (fallback)."""
    return getattr(event, "from_user", None)


def _event_chat_id(event: TelegramObject) -> int | None:
    """Hodisadan chat ID'sini ajratib oladi (Message/CallbackQuery)."""
    chat = getattr(event, "chat", None) or getattr(
        getattr(event, "message", None), "chat", None
    )
    return getattr(chat, "id", None)


def _event_chat_type(event: TelegramObject) -> str:
    """Hodisadagi chat turi: private / group / supergroup / channel."""
    chat = getattr(event, "chat", None) or getattr(
        getattr(event, "message", None), "chat", None
    )
    return str(getattr(chat, "type", "") or "")


class SubscriptionMiddleware(BaseMiddleware):
    """
    Har bir hodisadan oldin majburiy kanallar a'zoligini tekshiradi.

    To'lov hodisalari tekshiruvsiz o'tadi. Qolgan hodisalar uchun
    `Update` ichidagi xabar/callback ajratib olinadi.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        inner = _unwrap_event(event)

        # 0) To'lov hodisalari — hech qanday tekshiruvsiz (pul bloklanmaydi!)
        if _is_payment_event(inner) or _is_payment_event(event):
            return await handler(event, data)

        # Hodisa turi noma'lum (masalan, faqat `my_chat_member`) — o'tkazamiz
        if inner is None:
            return await handler(event, data)

        # 0.1) Guruh / superguruh / kanal xabarlari — obuna tekshiruvisiz.
        #      Bu yerda karta to'lovlari jurnali (@CardXabarBot xabarlari)
        #      bo'lishi mumkin; obuna esa faqat shaxsiy chatda ma'noga ega.
        if _event_chat_type(inner) in {"group", "supergroup", "channel"}:
            return await handler(event, data)

        # 0.2) Karta to'lovlari jurnali (`CARD_LOG_CHAT_ID`) — obuna tekshiruvisiz,
        # chunki u yerda foydalanuvchi emas, to'lov boti/bank xabarlari keladi.
        if (
            settings.CARD_LOG_CHAT_ID
            and _event_chat_id(inner) == settings.CARD_LOG_CHAT_ID
        ):
            return await handler(event, data)

        user = data.get("event_from_user") or _event_user(inner)

        # 1) Foydalanuvchi aniqlanmagan hodisalar (masalan, kanal xabarlari)
        if user is None:
            return await handler(event, data)

        # 2) Adminlar — tekshiruvsiz
        if user.id in settings.ADMIN_IDS:
            return await handler(event, data)

        # 3) "✅ A'zo bo'ldim" tugmasi bloklanmaydi — u qayta tekshiruv uchun
        if isinstance(inner, CallbackQuery) and inner.data == CB_CHECK_SUBSCRIPTION:
            return await handler(event, data)

        # 4) XOTIRA KESHI — bir marta tasdiqlangan foydalanuvchi.
        #    Eng tez yo'l: Telegram API so'rovi YO'Q.
        if user.id in _verified_cache:
            return await handler(event, data)

        # 5) Faol kanallar ro'yxati (keshli, 60 soniya)
        channels = await get_active_channel_dicts()
        if not channels:
            # Majburiy kanallar o'rnatilmagan — erkin foydalanish
            return await handler(event, data)

        # 6) BAZA BELGISI — `channels_verified = True` (keshli).
        #    Bir marta "✅ A'zo bo'ldim" bosilgach, har bir tugma bosilishida
        #    qayta Telegram API so'rovi yuborilmaydi.
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
        await self._send_subscription_prompt(inner, bot, unjoined)
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
          va xabar yuborilgan chatga yo'naltiruvchi yuboriladi.
        - Message hodisasi: xuddi shu chatga javob sifatida yuboriladi.

        Xatolar (foydalanuvchi botni blocklagan, xabar eskirgan va h.k.)
        ushlanadi va loglanadi — middleware hech qachon yangilanishni
        butunlay sindirmaydi.
        """
        markup = get_subscription_kb(unjoined)

        try:
            if isinstance(event, CallbackQuery):
                # Spinner'ni yopish (javob bermaslik Telegramda "so'rovlash" belgisini qoldiradi).
                # `RuntimeError` — bot'ga ulanmagan (sintetik) callback uchun.
                try:
                    await event.answer()
                except (TelegramAPIError, RuntimeError):
                    pass

                message = event.message
                target_chat_id = (
                    message.chat.id
                    if isinstance(message, (Message, InaccessibleMessage))
                    else None
                )
                if target_chat_id is not None:
                    try:
                        await bot.send_message(
                            chat_id=target_chat_id,
                            text=SUBSCRIPTION_PROMPT_TEXT,
                            reply_markup=markup,
                        )
                    except TelegramAPIError as exc:
                        logger.warning(
                            "Obuna promptini chatga yuborib bo'lmadi "
                            "(chat_id=%s): %s",
                            target_chat_id,
                            exc,
                        )
                return

            if isinstance(event, Message):
                await bot.send_message(
                    chat_id=event.chat.id,
                    text=SUBSCRIPTION_PROMPT_TEXT,
                    reply_markup=markup,
                )
                return

            # Boshqa "answer" metodi bor hodisalar (masalan, business xabarlar)
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
