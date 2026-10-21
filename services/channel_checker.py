"""
services/channel_checker.py
===========================
Majburiy homiy kanallarga a'zolikni tekshiruvchi servis (OP tekshiruvi).

Vazifalari:
 - `check_user_subscription()` — foydalanuvchini barcha faol kanallar bo'yicha
   tekshiradi va (hammasiga_a'zo, a'zo_bo'lmaganlar) juftligini qaytaradi.
 - Telegram API cheklovlariga (TooManyRequests/429) tushmaslik uchun har bir
   (user_id, chat_id) natijasi `cachetools.TTLCache` (120 soniya) da saqlanadi.
 - Xato holatlari (foydalanuvchi botni blocklagan, kanal topilmagan va h.k.)
   "a'zo emas" deb talqin qilinadi va keshlanadi — API qayta-qayta siklanmaydi.
 - `TelegramRetryAfter` (flood control) bo'lsa — bitta marta kutib, qayta urinadi.

Ishlatish:
    from services.channel_checker import check_user_subscription, get_active_channel_dicts

    channels = await get_active_channel_dicts()
    is_subscribed, unjoined = await check_user_subscription(bot, user_id, channels)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping, Sequence

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from cachetools import TTLCache

from database.queries import ChannelRepository

logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# "A'zo deb hisoblanadigan" statuslar
# (Telegram ChatMember.status qiymatlari)
# -------------------------------------------------------------
SUBSCRIBED_STATUSES = frozenset(
    {"creator", "administrator", "member", "restricted"}
)

# -------------------------------------------------------------
# Kesh sozlamalari
# - status keshi: har bir (user_id, chat_id) natijasi 120 soniya saqlanadi
# - kanallar keshi: faol kanallar ro'yxati 60 soniya (admin qo'shganda
#   `invalidate_channels_cache()` chaqiriladi)
# -------------------------------------------------------------
STATUS_CACHE_TTL = 120          # soniya
STATUS_CACHE_MAXSIZE = 10_000   # ta (user, chat) juftligi
CHANNELS_CACHE_TTL = 60
CHANNELS_CACHE_KEY = "active"

# TTLCache'lar asyncio (bitta event loop) uchun yetarli; alohida lock shart emas
_status_cache: TTLCache = TTLCache(maxsize=STATUS_CACHE_MAXSIZE, ttl=STATUS_CACHE_TTL)
_channels_cache: TTLCache = TTLCache(maxsize=4, ttl=CHANNELS_CACHE_TTL)


def clear_subscription_cache() -> None:
    """Barcha a'zolik keshini tozalash (admin qo'lda chaqirishi mumkin)."""
    _status_cache.clear()
    logger.info("A'zolik keshi tozalandi")


def invalidate_channels_cache() -> None:
    """
    Faol kanallar keshini bekor qilish.

    ChannelRepository orqali kanal qo'shish/o'chirish/yoqish-o'chirishdan
    keyin chaqirilsin — yangi kanallar darhol tekshiruvga tushsin.
    """
    _channels_cache.pop(CHANNELS_CACHE_KEY, None)


# =============================================================
# Yordamchilar
# =============================================================
def _channel_field(channel: Any, key: str, default: Any = None) -> Any:
    """
    Kanal maydonini olish — dict yoki ORM obyekt (Channel) uchun ham ishlaydi.

    Repository ORM obyektlar qaytaradi, lekin servis `list[dict]` bilan ham
    ishlayverishi uchun ikkala shakl qo'llab-quvvatlanadi.
    """
    if isinstance(channel, Mapping):
        return channel.get(key, default)
    return getattr(channel, key, default)


async def get_active_channel_dicts() -> list[dict[str, Any]]:
    """
    Bazadagi barcha FAOL majburiy kanallarni dict ko'rinishida qaytaradi.

    Natija 60 soniya keshlanadi (har bir /start da DB so'rovi ketma-ket
    bo'lmasligi uchun). Kanallar o'zgargach `invalidate_channels_cache()`
    chaqirilishi kerak.

    Returns:
        [{"id", "chat_id", "title", "invite_link", "username", "is_active"}, ...]
    """
    cached = _channels_cache.get(CHANNELS_CACHE_KEY)
    if cached is not None:
        return cached

    channels = await ChannelRepository.get_active_channels()
    data: list[dict[str, Any]] = [
        {
            "id": c.id,
            "chat_id": c.chat_id,
            "title": c.title,
            "invite_link": c.invite_link,
            "username": c.username,
            "is_active": c.is_active,
        }
        for c in channels
    ]
    _channels_cache[CHANNELS_CACHE_KEY] = data
    return data


async def _fetch_member_status(bot: Bot, chat_id: int, user_id: int) -> bool:
    """
    Bitta kanal uchun a'zolikni tekshiradi (keshsiz, API chaqiruvi bilan).

    Xatolar:
      - TelegramRetryAfter — flood control; bir marta kutib qayta urinadi.
      - TelegramForbiddenError — bot bloklangan yoki kanaldan chiqarilgan.
      - TelegramBadRequest — kanal topilmadi / foydalanuvchi topilmadi.
      - TelegramAPIError — boshqa API xatolari.

    Forbidden/BadRequest "a'zo emas" demakdir (lekin bunday holatni ham
    keshlaymiz — noto'g'ri so'rovlar bilan API siklamaslik uchun).
    """
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except TelegramRetryAfter as flood:
        # 429 TooManyRequests — kutib, bir marta qayta urinamiz
        logger.warning(
            "Flood control: chat=%s user=%s, %ss kutamiz",
            chat_id, user_id, flood.retry_after,
        )
        await asyncio.sleep(flood.retry_after + 1)
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            logger.warning(
                "A'zolik tekshiruvi rad etildi (chat=%s user=%s): %s",
                chat_id, user_id, exc,
            )
            return False
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        # bot blocklangan, ChatNotFound, "user not found" — a'zo emas
        logger.warning(
            "A'zolik tekshiruvi rad etildi (chat=%s user=%s): %s",
            chat_id, user_id, exc,
        )
        return False
    except TelegramAPIError as exc:
        # Kutilmagan API xatoligi — keshlamaymiz (vaqtincha muammo bo'lishi mumkin)
        logger.error(
            "Telegram API xatoligi (chat=%s user=%s): %s", chat_id, user_id, exc
        )
        return False

    return member.status in SUBSCRIBED_STATUSES


async def _is_subscribed(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Kesh orqali a'zolikni tekshiradi (120 soniya ichida API chaqirilmaydi)."""
    cache_key = (user_id, chat_id)
    cached = _status_cache.get(cache_key)
    if cached is not None:
        return bool(cached)

    result = await _fetch_member_status(bot, chat_id, user_id)
    _status_cache[cache_key] = result
    return result


# =============================================================
# ASOSIY SERVIS
# =============================================================
async def check_user_subscription(
    bot: Bot, user_id: int, channels: Sequence[Any]
) -> tuple[bool, list[Any]]:
    """
    Foydalanuvchini barcha majburiy kanallar bo'yicha tekshiradi.

    Args:
        bot: aiogram Bot instansi.
        user_id: tekshiriladigan foydalanuvchi.
        channels: faol kanallar ro'yxati — list[dict] (chat_id kaliti bilan)
            yoki Channel ORM obyektlari.

    Returns:
        (True, []) — barcha kanallarga a'zo;
        (False, unjoined_channels) — a'zo bo'lmagan kanallar ro'yxati
        (input shaklida: dict bo'lsa dict, ORM bo'lsa ORM).

    Har bir (user_id, chat_id) natijasi 120 soniya keshlanadi — bot
    xabarlari ketma-ket kelganda Telegram API limiti (TooManyRequests)
    sabab bo'lmaydi.
    """
    unjoined: list[Any] = []

    for channel in channels:
        chat_id = _channel_field(channel, "chat_id")
        if chat_id is None:
            # Kanal yozuvida chat_id yo'q — buzilgan data, o'tkazib yuboramiz
            logger.error("Kanalda chat_id yo'q: %r", channel)
            continue

        if not await _is_subscribed(bot, int(chat_id), user_id):
            unjoined.append(channel)

    if unjoined:
        titles = [_channel_field(ch, "title", "?") for ch in unjoined]
        logger.debug(
            "user=%s hali a'zo emas: %s", user_id, ", ".join(map(str, titles))
        )
        return False, unjoined

    return True, []
