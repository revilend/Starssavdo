"""
services/button_store.py
========================
Tugma override'larini `settings` jadvalida saqlash va yuklash.

`button_registry` sof (DB'siz) modul; bu qatlam esa:
 - startupda `btn:*` yozuvlarini o'qib keshga qo'llaydi;
 - admin o'zgartirishlarini DB'ga yozadi va keshni darhol yangilaydi;
 - "tiklash" (reset) amalini bajaradi.

Kalitlar:
    btn:<button_key>:text  — tugma matni
    btn:<button_key>:icon  — `icon_custom_emoji_id` (custom emoji)

Ishlatish:
    from services.button_store import load_button_overrides, save_button_text

    await load_button_overrides()                 # main.py startupda
    await save_button_text("menu_shop", "🛍 Bozor")
"""

from __future__ import annotations

import logging

from database.queries import SettingsRepository
from services import button_registry as registry

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 64
MAX_ICON_LENGTH = 64


async def load_button_overrides() -> int:
    """
    Barcha `btn:*` sozlamalarini DB'dan o'qib, keshga qo'llaydi.

    Returns: qo'llangan yozuvlar soni.
    """
    rows = await SettingsRepository.get_settings_by_prefix(
        registry.SETTING_PREFIX
    )
    registry.apply_overrides(rows)
    if rows:
        logger.info("Tugma override'lari yuklandi: %s ta yozuv", len(rows))
    return len(rows)


async def save_button_text(key: str, text: str) -> str:
    """
    Tugma matnini saqlaydi (bo'sh joylar tozalanadi, uzunlik tekshiriladi).

    Raises:
        ValueError: kalit noma'lum yoki matn bo'sh/juda uzun.
    """
    if not registry.is_known(key):
        raise ValueError(f"Noma'lum tugma kaliti: {key!r}")

    cleaned = " ".join((text or "").split())
    if not cleaned:
        raise ValueError("Tugma matni bo'sh bo'lishi mumkin emas")
    if len(cleaned) > MAX_TEXT_LENGTH:
        raise ValueError(
            f"Tugma matni {MAX_TEXT_LENGTH} belgidan oshmasligi kerak"
        )

    await SettingsRepository.update_setting(registry.text_setting_key(key), cleaned)
    registry.set_override_in_cache(key, text=cleaned)
    logger.info("Tugma matni yangilandi: %s -> %s", key, cleaned)
    return cleaned


async def save_button_icon(key: str, icon_id: str) -> str:
    """
    Tugma uchun Telegram custom emoji ID'sini saqlaydi.

    Raises:
        ValueError: kalit noma'lum yoki ID noto'g'ri.
    """
    if not registry.is_known(key):
        raise ValueError(f"Noma'lum tugma kaliti: {key!r}")

    cleaned = (icon_id or "").strip()
    if not cleaned:
        raise ValueError("Custom emoji ID bo'sh bo'lishi mumkin emas")
    if len(cleaned) > MAX_ICON_LENGTH or not cleaned.isdigit():
        raise ValueError(
            "Custom emoji ID faqat raqamlardan iborat bo'lishi kerak"
        )

    await SettingsRepository.update_setting(registry.icon_setting_key(key), cleaned)
    registry.set_override_in_cache(key, icon_id=cleaned)
    logger.info("Tugma emojisi yangilandi: %s -> %s", key, cleaned)
    return cleaned


async def reset_button(key: str) -> None:
    """Tugmaning barcha override'larini o'chiradi (default holatga qaytaradi)."""
    if not registry.is_known(key):
        raise ValueError(f"Noma'lum tugma kaliti: {key!r}")

    await SettingsRepository.delete_setting(registry.text_setting_key(key))
    await SettingsRepository.delete_setting(registry.icon_setting_key(key))
    registry.clear_override_in_cache(key)
    logger.info("Tugma default holatga qaytarildi: %s", key)
