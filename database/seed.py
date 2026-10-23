"""
database/seed.py
================
NFT YUT do'konining boshlang'ich katalogi (barcha narxlar SO'MDA) va
tizim sozlamalarining default qiymatlari.

Katalog (1 ⭐ = 300 so'm):
  ⭐️ Stars: 50 / 100 / 250 / 500 / 1000
  💎 Premium: 3 oy — 135 000, 6 oy — 195 000, 12 oy — 330 000
  🐥 Mayda Giftlar: 15 / 25 / 50 / 100 ⭐️ (4 500 — 30 000 so'm)
  🖼 NFT: 150 000 / 300 000 / 500 000 so'm

Bir martalik migratsiya (`catalog_version=2`):
 - eski (Stars narxidagi) katalog nofaol qilinadi va yangi UZS katalogi
   yoziladi; buyurtmalar tarixi saqlanadi (mahsulotlar o'chirilmaydi);
 - to'lov kartasi (`payment_card_number`, `payment_card_holder`) va
   `star_price_uzs` bo'sh bo'lsa default qiymat bilan to'ldiriladi.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, update

from database.connection import get_session
from database.models import Product, Setting

logger = logging.getLogger(__name__)

# Katalog versiyasi — o'zgarganda bir marta migratsiya ishlaydi
CATALOG_VERSION = "2"
SETTING_CATALOG_VERSION = "catalog_version"
SETTING_CARD_NUMBER = "payment_card_number"
SETTING_CARD_HOLDER = "payment_card_holder"
SETTING_STAR_PRICE = "star_price_uzs"

DEFAULT_CARD_NUMBER = "9860170111243573"
DEFAULT_CARD_HOLDER = "Murodjon Amirov"
DEFAULT_STAR_PRICE_UZS = 300

# Har bir mahsulot: nom, ta'rif, so'mdagi narx, payload va tartib.
DEFAULT_PRODUCTS: tuple[dict[str, Any], ...] = (
    # --- ⭐️ Stars to'plamlari (1 ⭐ = 300 so'm) ---
    {
        "category": "stars",
        "title": "50 Stars",
        "description": "Telegram Stars 50 dona",
        "price_uzs": 15_000,
        "price_xtr": 50,
        "payload": {"stars": 50},
        "sort_order": 1,
    },
    {
        "category": "stars",
        "title": "100 Stars",
        "description": "Telegram Stars 100 dona — eng ommabop",
        "price_uzs": 30_000,
        "price_xtr": 100,
        "payload": {"stars": 100},
        "sort_order": 2,
    },
    {
        "category": "stars",
        "title": "250 Stars",
        "description": "Telegram Stars 250 dona",
        "price_uzs": 75_000,
        "price_xtr": 250,
        "payload": {"stars": 250},
        "sort_order": 3,
    },
    {
        "category": "stars",
        "title": "500 Stars",
        "description": "Telegram Stars 500 dona",
        "price_uzs": 150_000,
        "price_xtr": 500,
        "payload": {"stars": 500},
        "sort_order": 4,
    },
    {
        "category": "stars",
        "title": "1000 Stars",
        "description": "Telegram Stars 1000 dona — eng tejamkor",
        "price_uzs": 300_000,
        "price_xtr": 1000,
        "payload": {"stars": 1000},
        "sort_order": 5,
    },
    # --- 💎 Telegram Premium ---
    {
        "category": "premium",
        "title": "Premium 3 oy",
        "description": "Telegram Premium 3 oylik obuna (Fragment orqali)",
        "price_uzs": 135_000,
        "price_xtr": 0,
        "payload": {"months": 3},
        "sort_order": 1,
    },
    {
        "category": "premium",
        "title": "Premium 6 oy",
        "description": "Telegram Premium 6 oylik obuna (Fragment orqali)",
        "price_uzs": 195_000,
        "price_xtr": 0,
        "payload": {"months": 6},
        "sort_order": 2,
    },
    {
        "category": "premium",
        "title": "Premium 12 oy",
        "description": "Telegram Premium 12 oylik obuna (Fragment orqali)",
        "price_uzs": 330_000,
        "price_xtr": 0,
        "payload": {"months": 12},
        "sort_order": 3,
    },
    # --- 🖼 Katta NFTlar (TON hamyon orqali yetkaziladi) ---
    {
        "category": "nft",
        "title": "NFT Bronze",
        "description": "Eksklyuziv NFT kolleksiya — Bronze",
        "price_uzs": 150_000,
        "price_xtr": 0,
        "payload": {"nft": "bronze", "wallet": True},
        "sort_order": 1,
    },
    {
        "category": "nft",
        "title": "NFT Silver",
        "description": "Eksklyuziv NFT kolleksiya — Silver",
        "price_uzs": 300_000,
        "price_xtr": 0,
        "payload": {"nft": "silver", "wallet": True},
        "sort_order": 2,
    },
    {
        "category": "nft",
        "title": "NFT Gold",
        "description": "Eksklyuziv NFT kolleksiya — Gold",
        "price_uzs": 500_000,
        "price_xtr": 0,
        "payload": {"nft": "gold", "wallet": True},
        "sort_order": 3,
    },
    # --- 🐥 Mayda Giftlar (Telegram rasmiy sovg'alari) ---
    {
        "category": "gift",
        "title": "Gift 15 ⭐️",
        "description": "Kichik Telegram sovg'asi (15 Stars)",
        "price_uzs": 4_500,
        "price_xtr": 0,
        "payload": {"gift_stars": 15},
        "sort_order": 1,
    },
    {
        "category": "gift",
        "title": "Gift 25 ⭐️",
        "description": "Telegram sovg'asi (25 Stars)",
        "price_uzs": 7_500,
        "price_xtr": 0,
        "payload": {"gift_stars": 25},
        "sort_order": 2,
    },
    {
        "category": "gift",
        "title": "Gift 50 ⭐️",
        "description": "Telegram sovg'asi (50 Stars)",
        "price_uzs": 15_000,
        "price_xtr": 0,
        "payload": {"gift_stars": 50},
        "sort_order": 3,
    },
    {
        "category": "gift",
        "title": "Gift 100 ⭐️",
        "description": "Katta Telegram sovg'asi (100 Stars)",
        "price_uzs": 30_000,
        "price_xtr": 0,
        "payload": {"gift_stars": 100},
        "sort_order": 4,
    },
)


async def _ensure_setting(session, key: str, value: str) -> None:
    """Sozlama bo'lmasa default qiymatni yozadi (mavjudni o'zgartirmaydi)."""
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))


async def seed_default_products() -> int:
    """
    Katalogni UZS versiyasiga keltiradi (idempotent).

    Birinchi ishga tushishda (`catalog_version` yo'q) eski katalog nofaol
    qilinadi va yangi UZS katalogi yoziladi. Keyingi ishga tushishlarda
    hech narsa o'zgarmaydi — admin tahrirlari saqlanadi.

    Returns: yangi qo'shilgan mahsulotlar soni.
    """
    inserted = 0
    try:
        async with get_session() as session:
            await _ensure_setting(session, SETTING_CARD_NUMBER, DEFAULT_CARD_NUMBER)
            await _ensure_setting(session, SETTING_CARD_HOLDER, DEFAULT_CARD_HOLDER)
            await _ensure_setting(session, SETTING_STAR_PRICE, str(DEFAULT_STAR_PRICE_UZS))

            version = await session.get(Setting, SETTING_CATALOG_VERSION)
            if version is not None and version.value == CATALOG_VERSION:
                count = int(
                    await session.scalar(select(func.count()).select_from(Product)) or 0
                )
                if count == 0:
                    session.add_all([Product(**item) for item in DEFAULT_PRODUCTS])
                    await session.flush()
                    return len(DEFAULT_PRODUCTS)
                return 0

            # Eski katalogni nofaol qilamiz (buyurtmalar tarixi saqlanadi)
            await session.execute(update(Product).values(is_active=False))

            # Yangi iqtisod: 1 ⭐ = 300 so'm
            star_price = await session.get(Setting, SETTING_STAR_PRICE)
            if star_price is not None:
                star_price.value = str(DEFAULT_STAR_PRICE_UZS)

            existing = {
                (product.category, product.title): product
                for product in (
                    await session.execute(select(Product))
                ).scalars().all()
            }

            for item in DEFAULT_PRODUCTS:
                product = existing.get((item["category"], item["title"]))
                if product is None:
                    session.add(Product(**item))
                    inserted += 1
                else:
                    product.description = item["description"]
                    product.price_uzs = item["price_uzs"]
                    product.price_xtr = item["price_xtr"]
                    product.payload = item["payload"]
                    product.sort_order = item["sort_order"]
                    product.is_active = True

            if version is None:
                session.add(Setting(key=SETTING_CATALOG_VERSION, value=CATALOG_VERSION))
            else:
                version.value = CATALOG_VERSION

            await session.flush()
            logger.info(
                "UZS katalogi tayyor: %s ta mahsulot (yangi: %s).",
                len(DEFAULT_PRODUCTS), inserted,
            )
            return inserted
    except Exception as exc:  # noqa: BLE001 — seed xatosi botni to'xtatmasin
        logger.exception("Mahsulotlar seed'ida xatolik: %s", exc)
        return 0
