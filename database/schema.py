"""
database/schema.py
==================
Sxema bootstrap'i: mavjud bo'lmagan jadvallarni yaratadi.

`Base.metadata.create_all()` faqat **yo'q** jadvallarni yaratadi — mavjud
jadvallarga tegmaydi (ALTER qilmaydi). Shu bilan yangi xususiyatlar
(`deposits` jadvali) eski Supabase bazasida ham ishlaydi, birinchi
deploy'da esa barcha jadvallar avtomatik paydo bo'ladi.

Eslatma: bu to'liq migratsiya tizimi emas. Ustun o'zgarishlari (ALTER)
uchun alohida migratsiya kerak; bu funksiya faqat "yetishmayotgan jadval"
muammosini hal qiladi.
"""

from __future__ import annotations

import logging

from database.connection import get_engine
from database.models import Base

logger = logging.getLogger(__name__)


async def ensure_schema() -> None:
    """
    Barcha model jadvallarini yaratadi (mavjudlariga tegmasdan).

    `main.py` lifespan'da DB ulanishi tekshirilgandan keyin chaqiriladi.
    """
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Baza sxemasi tekshirildi: barcha model jadvallari mavjud.")
