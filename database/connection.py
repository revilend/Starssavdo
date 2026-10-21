"""
database/connection.py
======================
Supabase PostgreSQL bilan asinxron ulanish drayveri.

Ushbu modul:
 - SQLAlchemy 2.0 asinxron engine (async_engine) yaratadi.
 - `asyncpg` drayveri orqali real asinxron ulanishlarni boshqaradi.
 - Connection pool sozlamalari:
      pool_size=10      -> doimiy ochiq ulanishlar soni
      max_overflow=20   -> yuk ko'payganda qo'shimcha ulanishlar (jami 30)
      pool_recycle=300  -> har 300 soniyadan so'ng ulanishni yangilaydi
                           (Supabase pooler ba'zan eski ulanishlarni uzadi)
      pool_pre_ping=True-> har bir ulanishdan oldin SELECT 1 bilan tekshiradi
                           ("server has gone away" xatolarining oldini oladi)
 - test_connection()   -> dastur ishga tushganda bazani tekshiradi.
 - close_connection()  -> dastur to'xtaganda poolni xavfsiz yopadi.

Ishlatish (asosiy):
    from database.connection import test_connection, close_connection, get_session

    # FastAPI uchun:
    async with get_session() as session:
        result = await session.execute(text("SELECT 1"))
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config.settings import settings

# -------------------------------------------------------------
# Logger: modul nomi bo'yicha log qaytaramiz
# -------------------------------------------------------------
logger = logging.getLogger(__name__)


# -------------------------------------------------------------
# Global o'zgaruvchilar (module-level singleton)
# - Engine bir marta yaratiladi va butun dastur davomida qayta ishlatiladi.
# - Pool shu engine ichida saqlanadi, shuning uchun alohida pool obyekti
#   boshqarish shart emas.
# -------------------------------------------------------------
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
# parallel ulanishlar uchun lock (birinchi chaqiruvda engine yaratish)
_init_lock = asyncio.Lock()


def _build_database_url(raw_url: str) -> str:
    """
    `.env` dagi DATABASE_URL ni SQLAlchemy async engine uchun normallashtiradi.

    Supabase bir nechta formatda connection string beradi:
      - postgresql://user:pass@host:5432/db      (postgresql+asyncpg kerak)
      - postgresql://user:pass@host:6543/db?sslmode=require (pooler)
      - postgresql+asyncpg://...                  (allaqachon to'g'ri)

    Biz asyncpg drayverdan foydalanamiz, shuning uchun "postgresql://" yoki
    "postgres://" prefikslarini "postgresql+asyncpg://" ga almashtiramiz.
    """
    url = raw_url.strip()

    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgres+asyncpg://"):
        return url.replace("postgres+asyncpg://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)

    # Kutilmagan format - xato qaytaramiz, shu bilan nosozlikni erta aniqlaymiz
    raise ValueError(
        "DATABASE_URL noto'g'ri formatda. "
        "postgresql:// yoki postgresql+asyncpg:// bilan boshlanishi kerak. "
        f"Olingan: {url[:30]}..."
    )


def _create_engine() -> AsyncEngine:
    """
    Supabase uchun asinxron engine yaratadi (pool sozlamalari bilan).

    Pool siyosati izohlanganidek:
      * pool_size=10      - lazim bo'lganda 10 ta doimiy ulanish saqlanadi.
      * max_overflow=20   - bosim ostida yana 20 ta ulanish ochiladi (jami 30).
      * pool_recycle=300  - 5 daqiqadan eski ulanish yangilanadi (dead connections).
      * pool_pre_ping=True - ulanish olishdan oldin bazaga "SELECT 1" yuboriladi.
    """
    async_url = _build_database_url(settings.DATABASE_URL)

    engine = create_async_engine(
        async_url,
        echo=False,                 # True qilsangiz SQL so'rovlari logga chiqadi
        future=True,                # SQLAlchemy 2.0 uslubidagi API
        pool_size=10,               # doimiy ochiq ulanishlar soni
        max_overflow=20,            # qo'shimcha ulanishlar (jami 10 + 20 = 30)
        pool_recycle=300,           # 300 soniyadan so'ng ulanishni yangilash
        pool_pre_ping=True,         # ulanishdan oldin sog'liqni tekshirish
        pool_use_lifo=True,         # oxirgi ishlatilgan ulanishni birinchi qaytaradi
                                    # (eski ulanishlar uzoq yotib qolmasligi uchun)
    )
    return engine


async def get_engine() -> AsyncEngine:
    """
    Global engine obyektini qaytaradi; agar hali yaratilmagan bo'lsa - yaratadi.

    asyncio.Lock bilan himoyalangan: parallel chaqiruvlar bir vaqtning o'zida
    bitta engine yaratmasligi uchun.
    """
    global _engine, _session_factory

    # Tez yo'l: engine allaqachon mavjud bo'lsa - darhol qaytaramiz
    if _engine is not None:
        return _engine

    async with _init_lock:
        # Ikki tekshiruv (double-checked locking): lock kutganda boshqasi yaratgan bo'lishi mumkin
        if _engine is None:
            _engine = _create_engine()
            # Session factory: bir xil engine ustida yengil AsyncSession'lar yaratadi
            _session_factory = async_sessionmaker(
                bind=_engine,
                class_=AsyncSession,
                expire_on_commit=False,  # commit dan keyin attribute'lar o'chmasin
                autoflush=False,         # avtomatik flush - aniq boshqariladi
            )
            logger.info("PostgreSQL async engine yaratildi (Supabase).")

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    AsyncSession yaratuvchi factory'ni qaytaradi.

    Ishlatish:
        factory = get_session_factory()
        async with factory() as session:
            ...
    """
    if _session_factory is None:
        raise RuntimeError(
            "Session factory hali yaratilmagan. Avval `await get_engine()` "
            "yoki `await test_connection()` chaqiring."
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Qisqa muddatli session konteksti (tegishli transaction bilan).

    FastAPI handler yoki bot handler ichida:
        async with get_session() as session:
            await session.execute(text("SELECT 1"))
            await session.commit()

    Xatolik yuz bersa avtomatik rollback bajariladi va session yopiladi.
    """
    if _session_factory is None:
        await get_engine()

    assert _session_factory is not None
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_connection() -> AsyncConnection:
    """
    Pool'dan bitta alohida AsyncConnection olish uchun yordamchi.

    Ishlatish (muhim: connection ni qo'lda yopish kerak):
        conn = await get_connection()
        try:
            result = await conn.execute(text("SELECT 1"))
        finally:
            await conn.close()
    """
    engine = await get_engine()
    return await engine.connect()


async def test_connection() -> bool:
    """
    Dastur ishga tushganda bazani tekshirish.

    Nima qiladi:
      1. Engine'ni yaratadi (agar hali yo'q bo'lsa).
      2. Pool'dan ulanish oladi.
      3. `SELECT 1` so'rovini yuboradi.
      4. Muvaffaqiyatli bo'lsa -> True, aks holda -> False qaytaradi
         va xatoni loglaydi (dastur ishga tushishini bloklamaydi).

    Ishlatish (main.py / bot.py da):
        if not await test_connection():
            raise SystemExit("Bazaga ulanib bo'lmadi!")
    """
    try:
        engine = await get_engine()

        # engine.connect() -> pool'dan ulanish oladi (pre_ping bilan tekshiriladi)
        async with engine.connect() as conn:
            # Oddiy tekshiruv so'rovi
            result = await conn.execute(text("SELECT 1"))
            value = result.scalar_one()

        if value == 1:
            logger.info(
                "PostgreSQL bilan ulanish muvaffaqiyatli (Supabase). "
                "pool_size=10, max_overflow=20, pool_recycle=300, pool_pre_ping=True"
            )
            return True

        logger.error("Bazadan kutilmagan javob qaytdi: %r", value)
        return False

    except Exception as exc:
        # Batafsil xatoni logga yozamiz, lekin traceback ham chiqariladi
        logger.exception("PostgreSQL bilan ulanishda xatolik yuz berdi: %s", exc)
        return False


async def close_connection() -> None:
    """
    Dastur to'xtaganda ulanishlarni xavfsiz yopish.

    Qiladigan ishlari:
      1. AsyncSession'lar (agar ochiq qolgan bo'lsa) - engine.dispose ga topshiriladi.
      2. Pool'dagi barcha ulanishlarni yopadi (engine.dispose()).
      3. Global o'zgaruvchilarni None qilib qo'yadi (keyingi ishga tushish uchun).

    Ishlatish:
        # Webhook rejimida:
        await close_connection()

        # Yoki FastAPI lifespan da:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            yield
            await close_connection()
    """
    global _engine, _session_factory

    if _engine is None:
        logger.info("Engine mavjud emas - yopish shart emas.")
        return

    try:
        # dispose() pool'dagi barcha ochiq ulanishlarni chiqarib yuboradi
        # await qilinadi, chunki bu asinxron engine
        await _engine.dispose()
        logger.info("Barcha PostgreSQL ulanishlari xavfsiz yopildi.")
    except Exception as exc:
        # Yopishda xatolik bo'lsa ham dastur to'xtashiga yo'l qo'ymaymiz,
        # lekin xatoni albatta loglaymiz.
        logger.exception("Ulanishlarni yopishda xatolik: %s", exc)
    finally:
        _engine = None
        _session_factory = None
