"""
database/seed.py
================
Boshlang'ich (default) mahsulotlarni bazaga qo'shish.

Nega kerak: yangi deploy'da `products` jadvali bo'sh bo'ladi va do'kon
bo'limlari ("⭐️ Stars xarid qilish", "💎 Telegram Premium", "🖼 Telegram NFT",
"🎁 Mayda Giftlar") bo'sh ko'rinadi. Bot ishga tushganda jadval bo'sh bo'lsa,
har bir toifa uchun tayyor mahsulotlar qo'shiladi — do'kon darhol ishlaydi.

Muhim: agar bazada kamida bitta mahsulot bo'lsa (admin o'zi qo'shgan bo'lsa),
seed ISHLAMAYDI — mavjud katalogga tegmaydi.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select

from database.connection import get_session
from database.models import Product

logger = logging.getLogger(__name__)

# Har bir toifa uchun boshlang'ich mahsulotlar.
# `price_xtr` — Telegram Stars (XTR) dagi narx.
DEFAULT_PRODUCTS: tuple[dict[str, Any], ...] = (
    # --- ⭐️ Stars to'plamlari ---
    {
        "category": "stars",
        "title": "50 Stars",
        "description": "Telegram Stars 50 dona — eng kichik to'plam",
        "price_xtr": 50,
        "payload": {"stars": 50},
        "sort_order": 1,
    },
    {
        "category": "stars",
        "title": "100 Stars",
        "description": "Telegram Stars 100 dona — eng ommabop to'plam",
        "price_xtr": 100,
        "payload": {"stars": 100},
        "sort_order": 2,
    },
    {
        "category": "stars",
        "title": "250 Stars",
        "description": "Telegram Stars 250 dona — tejamkor to'plam",
        "price_xtr": 250,
        "payload": {"stars": 250},
        "sort_order": 3,
    },
    {
        "category": "stars",
        "title": "500 Stars",
        "description": "Telegram Stars 500 dona — eng katta to'plam",
        "price_xtr": 500,
        "payload": {"stars": 500},
        "sort_order": 4,
    },
    # --- 💎 Telegram Premium ---
    {
        "category": "premium",
        "title": "Premium 1 oy",
        "description": "Telegram Premium 1 oylik obuna",
        "price_xtr": 250,
        "payload": {"months": 1},
        "sort_order": 1,
    },
    {
        "category": "premium",
        "title": "Premium 3 oy",
        "description": "Telegram Premium 3 oylik obuna",
        "price_xtr": 700,
        "payload": {"months": 3},
        "sort_order": 2,
    },
    {
        "category": "premium",
        "title": "Premium 12 oy",
        "description": "Telegram Premium 12 oylik obuna",
        "price_xtr": 2500,
        "payload": {"months": 12},
        "sort_order": 3,
    },
    # --- 🖼 Telegram NFT ---
    {
        "category": "nft",
        "title": "Telegram NFT sovg'a",
        "description": "Eksklyuziv Telegram NFT kolleksiyasi",
        "price_xtr": 150,
        "payload": {"nft": True},
        "sort_order": 1,
    },
    {
        "category": "nft",
        "title": "Deluxe NFT kolleksiya",
        "description": "Noyob Deluxe Telegram NFT kolleksiyasi",
        "price_xtr": 500,
        "payload": {"nft": True, "deluxe": True},
        "sort_order": 2,
    },
    # --- 🎁 Mayda Giftlar ---
    {
        "category": "gift",
        "title": "Mayda Gift: Ayiqcha",
        "description": "Sevimli ayiqcha sovg'asi",
        "price_xtr": 50,
        "payload": {"gift": "teddy"},
        "sort_order": 1,
    },
    {
        "category": "gift",
        "title": "Mayda Gift: Yurak",
        "description": "Yurak shaklidagi sovg'a",
        "price_xtr": 100,
        "payload": {"gift": "heart"},
        "sort_order": 2,
    },
    {
        "category": "gift",
        "title": "Mayda Gift: Raketa",
        "description": "Raketa sovg'asi — do'stingizni xursand qiling",
        "price_xtr": 250,
        "payload": {"gift": "rocket"},
        "sort_order": 3,
    },
)


async def seed_default_products() -> int:
    """
    `products` jadvali BO'SH bo'lsa, boshlang'ich mahsulotlarni qo'shadi.

    Returns: qo'shilgan mahsulotlar soni (0 — jadval bo'sh emas yoki xato).
    """
    try:
        async with get_session() as session:
            count = int(
                await session.scalar(select(func.count()).select_from(Product)) or 0
            )
            if count > 0:
                logger.info(
                    "Mahsulotlar seed'i o'tkazib yuborildi: bazada %s ta mahsulot bor.",
                    count,
                )
                return 0

            session.add_all([Product(**item) for item in DEFAULT_PRODUCTS])
            await session.flush()
            logger.info(
                "Boshlang'ich mahsulotlar qo'shildi: %s ta (stars/premium/nft/gift).",
                len(DEFAULT_PRODUCTS),
            )
            return len(DEFAULT_PRODUCTS)
    except Exception as exc:  # noqa: BLE001 — seed xatosi botni to'xtatmasin
        logger.exception("Mahsulotlar seed'ida xatolik: %s", exc)
        return 0
