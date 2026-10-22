"""
database/schema.py
==================
Sxema bootstrap'i: mavjud bo'lmagan jadvallar va ustunlarni qo'shadi.

`Base.metadata.create_all()` faqat **yo'q** jadvallarni yaratadi — mavjud
jadvallarga tegmaydi. Shuning uchun qo'shimcha `_add_missing_columns()`
qadami bor: u mavjud jadvallarda yetishmayotgan (nullable yoki default'li)
ustunlarni `ALTER TABLE ... ADD COLUMN` bilan qo'shadi va shu ustunlarga
tegishli indekslarni yaratadi.

Shu bilan yangi xususiyatlar (`deposits.pay_amount`,
`deposits.payment_comment`) eski Supabase bazasida ham ishlaydi, birinchi
deploy'da esa barcha jadvallar avtomatik paydo bo'ladi.

Eslatma: bu to'liq migratsiya tizimi emas (masalan, mavjud ustun tipini
o'zgartirmaydi yoki NOT NULL cheklovini keyin qo'shmaydi).
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from database.connection import get_engine
from database.models import Base

logger = logging.getLogger(__name__)


def _add_missing_columns(sync_conn: Connection) -> None:
    """
    Mavjud jadvallarga yetishmayotgan ustun va indekslarni qo'shadi.

    Faqat yangi ustun qo'shish bilan cheklanadi (`ALTER TABLE ADD COLUMN`);
    mavjud ma'lumotlarga tegmaydi. NOT NULL va default'siz ustunlar xavfli
    bo'lgani uchun o'tkazib yuboriladi (qo'lda migratsiya kerak).
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    dialect = sync_conn.dialect

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue

        present = {column["name"] for column in inspector.get_columns(table.name)}
        added: set[str] = set()

        for column in table.columns:
            if column.name in present:
                continue
            if not column.nullable and column.server_default is None:
                logger.warning(
                    "%s.%s nullable emas va default'siz — avtomatik "
                    "qo'shilmadi (qo'lda migratsiya kerak).",
                    table.name,
                    column.name,
                )
                continue

            column_type = column.type.compile(dialect=dialect)
            sync_conn.exec_driver_sql(
                f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'
            )
            added.add(column.name)
            logger.info(
                "Sxema migratsiyasi: %s.%s ustuni qo'shildi.",
                table.name,
                column.name,
            )

        if not added:
            continue

        existing_indexes = {index["name"] for index in inspector.get_indexes(table.name)}
        for index in table.indexes:
            index_columns = {column.name for column in index.columns}
            if index.name in existing_indexes or not index_columns & added:
                continue
            column_list = ", ".join(f'"{name}"' for name in index_columns)
            sync_conn.exec_driver_sql(
                f'CREATE INDEX IF NOT EXISTS "{index.name}" '
                f'ON "{table.name}" ({column_list})'
            )
            logger.info(
                "Sxema migratsiyasi: %s indeksi yaratildi.", index.name
            )


async def ensure_schema() -> None:
    """
    Barcha model jadvallarini yaratadi va yetishmayotgan ustunlarni qo'shadi.

    `main.py` lifespan'da DB ulanishi tekshirilgandan keyin chaqiriladi.
    """
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
    logger.info("Baza sxemasi tekshirildi: jadval va ustunlar mavjud.")
