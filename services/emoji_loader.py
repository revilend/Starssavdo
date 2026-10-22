"""
services/emoji_loader.py
========================
Telegram Bot API 9.4 custom emoji (Premium emoji) yuklovchisi.

Bot ishga tushganda (main.py `lifespan`) `bot.get_sticker_set(name)` orqali
stiker/custom emoji to'plamlaridan `custom_emoji_id` larni tortib oladi.
Shundan keyin klaviaturalar tugmalariga `icon_custom_emoji_id` sifatida
shu ID'lar biriktiriladi (custom emoji faqat bot egasida Telegram Premium
bo'lsa ko'rinadi — bu Telegram cheklovi).

To'plamlar:
  - SystemicDuck / CastilosDuck : asosiy menyu tugmalari (harakatlanuvchi o'rdaklar)
  - statuspack_uz               : tasdiqlash / "A'zo bo'ldim" galochkalari
  - business_emojis             : to'lov va yechib olish tugmalari

Muhim: to'plam topilmasa yoki bot unga kira olmasa — bot ISHLASHDA DAVOM
ETADI, faqat log'ga ogohlantirish yoziladi va tugmalar oddiy emoji bilan
qoladi. Shu bilan `.env`/Telegram sozlamalari to'liq bo'lmagan muhitda ham
klaviatura buzilmaydi.

Ishlatish:
    from services.emoji_loader import load_custom_emojis, get_custom_emoji_id

    await load_custom_emojis(bot)                  # startupda bir marta
    detail_id = get_custom_emoji_id("status", "✅")
"""

from __future__ import annotations

import logging
from typing import Iterable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)

# =============================================================
# To'plamlar (kalit -> Telegram sticker set nomi)
# =============================================================
EMOJI_SOURCE_SETS: dict[str, str] = {
    "duck": "SystemicDuck",
    "duck_alt": "CastilosDuck",
    "status": "statuspack_uz",
    "payment": "business_emojis",
}

# Yuklangan to'plamlar: set_key -> {emoji_char: custom_emoji_id}
_registry: dict[str, dict[str, str]] = {}

# Qaysi to'plamlar muvaffaqiyatli yuklangani (qayta so'rov yubormaslik uchun)
_loaded_sets: set[str] = set()


async def load_custom_emojis(bot: Bot, *, force: bool = False) -> dict[str, int]:
    """
    Barcha to'plamlardan custom emoji ID'larini yuklaydi.

    Args:
        bot: aiogram Bot instansi.
        force: True — allaqachon yuklangan to'plamlarni ham qayta so'raydi
            (masalan, admin to'plamni yangilagandan keyin).

    Returns:
        {set_key: topilgan_emoji_soni} — log/diagnostika uchun.
    """
    counts: dict[str, int] = {}

    for set_key, set_name in EMOJI_SOURCE_SETS.items():
        if set_key in _loaded_sets and not force:
            counts[set_key] = len(_registry.get(set_key, {}))
            continue

        try:
            sticker_set = await bot.get_sticker_set(set_name)
        except TelegramAPIError as exc:
            # To'plam yo'q / bot ko'ra olmaydi — bu kritik emas
            logger.warning(
                "Custom emoji to'plami '%s' (%s) yuklanmadi: %s",
                set_name,
                set_key,
                exc,
            )
            _registry.setdefault(set_key, {})
            counts[set_key] = 0
            continue
        except Exception:  # noqa: BLE001 — tarmoq xatosi botni to'xtatmasin
            logger.exception(
                "Custom emoji to'plamini yuklashda kutilmagan xato: %s", set_name
            )
            _registry.setdefault(set_key, {})
            counts[set_key] = 0
            continue

        mapping: dict[str, str] = {}
        for sticker in sticker_set.stickers:
            emoji = getattr(sticker, "emoji", None)
            emoji_id = getattr(sticker, "custom_emoji_id", None)
            if emoji and emoji_id:
                # Bir xil emoji bir nechta bo'lsa — birinchisi olinadi
                mapping.setdefault(emoji, emoji_id)

        _registry[set_key] = mapping
        _loaded_sets.add(set_key)
        counts[set_key] = len(mapping)
        logger.info(
            "Custom emoji to'plami yuklandi: %s (%s) — %s ta emoji",
            set_name,
            set_key,
            len(mapping),
        )

    total = sum(len(mapping) for mapping in _registry.values())
    logger.info("Custom emoji registry tayyor: %s ta ID (%s)", total, counts)
    return counts


def get_custom_emoji_id(set_key: str, emoji: str) -> str | None:
    """
    Bitta to'plamdan emoji belgisiga mos `custom_emoji_id` qaytaradi.

    Topilmasa (to'plam yuklanmagan yoki bunday emoji yo'q) — None.
    """
    return _registry.get(set_key, {}).get(emoji)


def find_custom_emoji_id(
    emoji: str | None, set_keys: Iterable[str] = ()
) -> str | None:
    """
    Bir nechta to'plam bo'ylab emoji ID'sini qidiradi (birinchi topilgani).

    `set_keys` bo'sh bo'lsa — barcha yuklangan to'plamlar tekshiriladi.
    """
    if not emoji:
        return None

    keys = list(set_keys) or list(_registry)
    for set_key in keys:
        emoji_id = _registry.get(set_key, {}).get(emoji)
        if emoji_id:
            return emoji_id
    return None


def loaded_emoji_count() -> int:
    """Registry'dagi jami custom emoji ID'lari soni (diagnostika uchun)."""
    return sum(len(mapping) for mapping in _registry.values())


def loaded_sets() -> list[str]:
    """Muvaffaqiyatli yuklangan to'plam kalitlari."""
    return sorted(_loaded_sets)


def clear_emoji_cache() -> None:
    """Registry'ni tozalaydi (testlar yoki qayta yuklash uchun)."""
    _registry.clear()
    _loaded_sets.clear()
