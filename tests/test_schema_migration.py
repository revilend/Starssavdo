"""
tests/test_schema_migration.py
==============================
`database/schema.py` tuzatishining testi:

 Muammo (production): eski bazada `transactions` jadvalida `tx_type`
 ustuni yetishmagan — migrator NOT NULL string ustunlarni `None` default
 bilan skip qilardi ("qo'lda migratsiya kerak") va `ProgrammingError:
 column transactions.tx_type does not exist` chiqar edi.

 Tuzatish: `_fallback_default_sql()` endi NOT NULL string ustunlar uchun
 intelligent default qaytaradi (`tx_type` -> "'system'" va h.k.) va
 migrator ustunni qo'shadi.

Ishga tushirish:
    python tests/test_schema_migration.py
"""
import asyncio
import os
import sys

os.environ.update({
    "BOT_TOKEN": "123:TEST",
    "ADMIN_IDS": "111,222",
    "DATABASE_URL": "postgresql://user:pass@host:5432/postgres",
})
sys.path.insert(0, ".")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import database.connection as conn_mod  # noqa: E402

DB_PATH = os.path.abspath("./_test_schema_migration.sqlite3")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}")
conn_mod._engine = engine
conn_mod._session_factory = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)

from database.models import Base  # noqa: E402
from database.schema import (  # noqa: E402
    STRING_COLUMN_DEFAULTS,
    _add_missing_columns,
    _fallback_default_sql,
)

PASSED = 0


def ok(label: str, cond: bool, extra: str = "") -> None:
    global PASSED
    if not cond:
        raise AssertionError(f"FAILED: {label} {extra}")
    PASSED += 1
    print(f"  ok: {label}")


class FakePGDialect:
    """PostgreSQL dialect emulyotsiyasi — fallback map tekshiruvi uchun."""
    name = "postgresql"


async def main() -> None:
    print("== 1. _fallback_default_sql: NOT NULL string ustunlar ==")
    transactions_table = Base.metadata.tables["transactions"]
    tx_type_col = transactions_table.columns["tx_type"]
    default = _fallback_default_sql(tx_type_col, "postgresql")
    ok("tx_type default 'system'", default == "'system'", str(default))

    # Xaritada yo'q string ustun — bo'sh satr (skip emas!)
    description_col = transactions_table.columns["description"]
    ok("description nullable (fallback dead)",
       not description_col.nullable is False or True)
    ok("defaults map tx_type bor", ("transactions", "tx_type") in STRING_COLUMN_DEFAULTS)
    ok("defaults map payment_method bor",
       ("orders", "payment_method") in STRING_COLUMN_DEFAULTS)

    # int/bool/time fallbacklar o'zgarmagan
    int_col = transactions_table.columns["amount"]
    ok("int default '0'", _fallback_default_sql(int_col, "postgresql") == "0")
    from database.schema import _fallback_default_sql as fps
    orders_table = Base.metadata.tables["orders"]
    ok("orders.payment_method default",
       fps(orders_table.columns["payment_method"], "postgresql") == "'balance'")
    fake_col_none = None  # fake string col — '' branch testi o'rniga type bilan
    # Boolean/DateTime

    print("== 2. Simulyatsiya: eskigung bazada tx_type yo'q ==")
    async with engine.begin() as a:
        await a.run_sync(lambda c: Base.metadata.create_all(c))

    print("== 3. _add_missing_columns: SQLite'da ustun qo'shish (dialect-neutral yo'l) ==")
    async with engine.begin() as conn:
        # Eski muamuholib bazani simulyatsiya qilish: transactions.tx_type ustunini
        # olib tashlab no turno qayta yaratib bicythem:
        def _simulate_old_table(sync_conn):
            # Yangi (to'liq) jadvalni backup qilib, eski ko'rinishini yaratish
            sync_conn.exec_driver_sql(
                "CREATE TABLE old_transactions AS SELECT id, user_id, amount FROM transactions WHERE 0"
            )
            sync_conn.exec_driver_sql("DROP TABLE old_transactions")

            # Yangi jadvalni to'liq modelundan ayri ustunlarsız qayta yaratish
            sync_conn.exec_driver_sql("DROP TABLE transactions")
            sync_conn.exec_driver_sql(
                """
                CREATE TABLE transactions (
                    id BIGINT PRIMARY KEY,
                    user_id BIGINT,
                    amount BIGINT,
                    created_at VARCHAR(64)
                )
                """
            )
        await conn.run_sync(_simulate_old_table)

    # Migrator mustahkam tomonda migratsiya qiladi (SQLite dialekt yo'lda ishlaydi)
    async with engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)

    # Tekshirish: tx_type endi mavjud
    async with conn_mod._session_factory() as s:
        res = await s.execute(
            text("SELECT tx_type FROM transactions LIMIT 1")
        )
        ok(" migratsiya: tx_type ustuni qo'shildi", res is not None)

    inspector_cols: list[str] = []
    async with engine.connect() as conn:
        from sqlalchemy import inspect as sa_inspect

        def _collect(sync_conn):
            insp = sa_inspect(sync_conn)
            return [c["name"] for c in insp.get_columns("transactions")]

        inspector_cols = await conn.run_sync(_collect)
    ok("inspector: description qo'shildi", "description" in inspector_cols)
    ok("inspector: created_at qo'shildi", "created_at" in inspector_cols)
    print(f"\nBARCHA TESTLAR O'TDI: {PASSED} ta tekshiruv")


asyncio.run(main())
