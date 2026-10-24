"""
database/schema.py
==================
Sxema bootstrap'i: mavjud bo'lmagan jadvallar va ustunlarni qo'shadi.

`Base.metadata.create_all()` faqat **yo'q** jadvallarni yaratadi — mavjud
jadvallarga tegmaydi. Shu sababli `ensure_schema()` uch qadamni bajaradi:

  1. `Base.metadata.create_all` — modeldagi barcha jadvallar (agar yo'q bo'lsa);
  2. `CREATE TABLE IF NOT EXISTS` — balans to'ldirish uchun muhim jadvallar
     (`users`, `transactions`, `deposits`) aniq DDL bilan qayta kafolatlanadi;
  3. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — eski Supabase bazasida
     yetishmayotgan HAR BIR ustun avtomatik qo'shiladi (masalan
     `users.total_earned_stars`, `deposits.pay_amount`, `deposits.admin_id`).

Shu bilan "Bazada xatolik yuz berdi" (`UndefinedColumn`) xatosi yangi
deploy'da ham, eski bazada ham takrorlanmaydi: kerakli jadvallar va ustunlar
bot ishga tushishida avtomatik tayyorlanadi.

Eslatma: bu to'liq migratsiya tizimi emas (mavjud ustun tipini o'zgartirmaydi,
mavjud CHECK constraint'larni faqat deposits statusi uchun qayta tiklaydi).
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from database.connection import get_engine
from database.models import Base

logger = logging.getLogger(__name__)

# =============================================================
# 1. ANIQ DDL — balans to'ldirish uchun zarur jadvallar
# =============================================================
# `create_all` ham shu jadvallarni yaratadi, lekin eski (qo'lda yaratilgan)
# bazalarda sxema to'liq bo'lmasligi mumkin — shu DDL kafolat beradi.
SCHEMA_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username VARCHAR(64),
        first_name VARCHAR(128),
        last_name VARCHAR(128),
        referrer_id BIGINT,
        stars_balance BIGINT NOT NULL DEFAULT 0,
        total_earned_stars BIGINT NOT NULL DEFAULT 0,
        channels_verified BOOLEAN NOT NULL DEFAULT false,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_active_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transactions (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        amount BIGINT NOT NULL,
        tx_type VARCHAR(32) NOT NULL,
        description TEXT,
        reference_id VARCHAR(128),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS deposits (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        amount BIGINT NOT NULL,
        pay_amount BIGINT,
        payment_comment TEXT,
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        receipt_file_id TEXT,
        expires_at TIMESTAMPTZ,
        payment_message_id BIGINT,
        admin_id BIGINT,
        reject_reason TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        processed_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS promo_codes (
        code VARCHAR(32) PRIMARY KEY,
        stars BIGINT NOT NULL,
        max_uses INTEGER NOT NULL DEFAULT 0,
        used_count INTEGER NOT NULL DEFAULT 0,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS promo_redemptions (
        id BIGSERIAL PRIMARY KEY,
        code VARCHAR(32) NOT NULL REFERENCES promo_codes(code) ON DELETE CASCADE,
        user_id BIGINT NOT NULL,
        stars BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_promo_redemptions_code_user
        ON promo_redemptions (code, user_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS user_blocks (
        user_id BIGINT PRIMARY KEY,
        reason TEXT,
        blocked_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)

# Ma'lumotlarni to'g'rilash: eski qatorlarda NULL balans bo'lsa 0 ga tenglash
# (aks holda `stars_balance + amount` TypeError beradi).
# Eski `products.name` NULL bo'lsa `title` dan to'ldiriladi (yangi INSERT'lar
# uchun `name` DEFAULT '' bilan COLUMN_DEFAULTS'da kafolatlangan).
DATA_FIXUPS: tuple[str, ...] = (
    "UPDATE users SET stars_balance = 0 WHERE stars_balance IS NULL",
    "UPDATE users SET total_earned_stars = 0 WHERE total_earned_stars IS NULL",
    "UPDATE users SET channels_verified = false WHERE channels_verified IS NULL",
    "UPDATE users SET is_active = true WHERE is_active IS NULL",
    "UPDATE products SET name = COALESCE(title, '') WHERE name IS NULL",
)

# Ustun default'lari (yangi qatorlar uchun DB darajasida kafolat).
COLUMN_DEFAULTS: tuple[str, ...] = (
    "ALTER TABLE users ALTER COLUMN stars_balance SET DEFAULT 0",
    "ALTER TABLE users ALTER COLUMN total_earned_stars SET DEFAULT 0",
    # Bazadagi eski products jadvalida `name` NOT NULL — agar default bo'lmasa,
    # seed'dagi INSERT (title bilan) NotNullViolationError beradi.
    "ALTER TABLE products ALTER COLUMN name SET DEFAULT ''",
)

# Eski bazada `status` CHECK'i boshqa qiymatlar bilan yaratilgan bo'lsa,
# to'g'ri constraint bilan almashtiriladi (idempotent).
STATUS_CONSTRAINTS: tuple[str, ...] = (
    "ALTER TABLE deposits DROP CONSTRAINT IF EXISTS ck_deposits_status",
    (
        "ALTER TABLE deposits ADD CONSTRAINT ck_deposits_status "
        "CHECK (status IN ('pending', 'approved', 'rejected', 'expired'))"
    ),
    "ALTER TABLE orders DROP CONSTRAINT IF EXISTS ck_orders_status",
    (
        "ALTER TABLE orders ADD CONSTRAINT ck_orders_status "
        "CHECK (status IN ('pending', 'paid', 'cancelled', 'expired'))"
    ),
    "ALTER TABLE orders DROP CONSTRAINT IF EXISTS ck_orders_amount_positive",
    (
        "ALTER TABLE orders ADD CONSTRAINT ck_orders_amount_positive "
        "CHECK (amount_xtr >= 0)"
    ),
)


def _run_safely(sync_conn: Connection, sql: str, *, label: str) -> None:
    """
    Bitta DDL/DML buyruqni bajaradi; xato bo'lsa logga yozadi va davom etadi.

    Startup hech qachon sxema tafsiloti (masalan, mavjud constraint bilan
    konflikt) sababli to'xtab qolmasligi kerak — asosiy ishni `create_all`
    allaqachon bajargan bo'ladi.
    """
    try:
        sync_conn.exec_driver_sql(sql)
    except Exception as exc:  # noqa: BLE001 — bitta qadam qolganlarini to'xtatmasin
        logger.warning("Sxema qadami o'tkazib yuborildi (%s): %s", label, exc)


def _bootstrap_raw_schema(sync_conn: Connection) -> None:
    """PostgreSQL uchun aniq `CREATE TABLE IF NOT EXISTS` va fixuplar."""
    if sync_conn.dialect.name != "postgresql":
        return

    for index, ddl in enumerate(SCHEMA_DDL, start=1):
        _run_safely(sync_conn, ddl, label=f"create#{index}")

    for sql in DATA_FIXUPS:
        _run_safely(sync_conn, sql, label="data-fixup")

    for sql in COLUMN_DEFAULTS:
        _run_safely(sync_conn, sql, label="column-default")

    for sql in STATUS_CONSTRAINTS:
        _run_safely(sync_conn, sql, label="status-constraint")

    logger.info(
        "Xom sxema tekshirildi: users/transactions/deposits jadvallari tayyor."
    )


# NOT NULL string ustunlari uchun migratsiya default'lari.
# Eski bazada yetishmayotgan VARCHAR ustunlar shu qiymat bilan qo'shiladi —
# aks holda "qo'lda migratsiya kerak" deya o'tkazib yuborilardi va
# "column ... does not exist" (ProgrammingError) production'da chiqar edi.
STRING_COLUMN_DEFAULTS: dict[tuple[str, str], str] = {
    ("transactions", "tx_type"): "'system'",
    ("transactions", "description"): "NULL",
    ("orders", "payment_method"): "'balance'",
    ("orders", "telegram_payment_charge_id"): "NULL",
    ("orders", "payment_comment"): "NULL",
    ("products", "category"): "'stars'",
    ("products", "title"): "'Mahsulot'",
    ("products", "description"): "NULL",
    ("products", "image_file_id"): "NULL",
    ("channels", "title"): "'Kanal'",
    ("channels", "invite_link"): "'https://t.me/0'",
    ("channels", "username"): "NULL",
    ("withdrawals", "destination_type"): "'ton_wallet'",
    ("withdrawals", "destination_address"): "'unknown'",
    ("withdrawals", "status"): "'pending'",
    ("referrals", "status"): "'pending'",
    ("users", "username"): "NULL",
    ("users", "first_name"): "'User'",
    ("users", "last_name"): "NULL",
    ("promo_codes", "code"): "'INVALID'",
    ("anti_fraud_logs", "severity"): "'medium'",
    ("anti_fraud_logs", "reason"): "'unknown'",
}


def _fallback_default_sql(column, dialect_name: str) -> str | None:
    """
    NOT NULL ustun uchun xavfsiz DB default topadi (dialektnaundum).

    Eski bazada `transactions.tx_type` kabi NOT NULL ustun yetishmasa, uni
    defaultsiz qo'shib bo'lmaydi (`ALTER TABLE ... ADD COLUMN` xato beradi —
    Postgres ham, SQLite ham). Shu yordamchi tipga mos xavfsiz qiymat
    qaytarmoqda: string/varchar yo'q bo'lsa hamda har qam holda ishlaydi.
    """
    if column.name == "status":
        return "'pending'"

    type_name = type(column.type).__name__.lower()
    if "string" in type_name or "text" in type_name or "varchar" in type_name:
        # NOT NULL string ustunlari — jadval/ustun bo'yicha intelligent default:
        # migrator endi ularni skip qilmasdan qo'shadi.
        key = (column.table.name, column.name)
        default = STRING_COLUMN_DEFAULTS.get(key)
        if default is not None and default != "NULL":
            return default
        # Xaritada yo'q string ustun — bo'sh satr default xavfsiz yordam
        return "''"
    if "int" in type_name:
        return "0"
    if "bool" in type_name:
        return "false"
    if "date" in type_name or "time" in type_name:
        return "now()"
    return None


def _add_missing_columns(sync_conn: Connection) -> None:
    """
    Mavjud jadvallarga yetishmayotgan ustun va indekslarni qo'shadi.

    PostgreSQL'da `ADD COLUMN IF NOT EXISTS` ishlatiladi (parallel deploy'da
    ham xavfsiz); boshqa dialektlarda ustun bor-yo'qligi oldindan tekshiriladi.
    Faqat yangi ustun qo'shish bilan cheklanadi — mavjud ma'lumotga tegmaydi.
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    dialect = sync_conn.dialect
    is_postgres = dialect.name == "postgresql"

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue

        present = {column["name"] for column in inspector.get_columns(table.name)}
        added: set[str] = set()

        for column in table.columns:
            if column.name in present:
                continue

            default_sql = ""
            if column.server_default is not None:
                default_sql = f" DEFAULT {column.server_default.arg}"
            elif not column.nullable:
                fallback = _fallback_default_sql(column, dialect.name)
                if fallback is None:
                    logger.warning(
                        "%s.%s nullable emas va default'siz — avtomatik "
                        "qo'shilmadi (qo'lda migratsiya kerak).",
                        table.name,
                        column.name,
                    )
                    continue
                default_sql = f" DEFAULT {fallback}"

            column_type = column.type.compile(dialect=dialect)

            if is_postgres:
                sql = (
                    f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS '
                    f'"{column.name}" {column_type}{default_sql}'
                )
            else:
                sql = (
                    f'ALTER TABLE "{table.name}" ADD COLUMN '
                    f'"{column.name}" {column_type}{default_sql}'
                )
            _run_safely(
                sync_conn,
                sql,
                label=f"{table.name}.{column.name}",
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
            _run_safely(
                sync_conn,
                f'CREATE INDEX IF NOT EXISTS "{index.name}" '
                f'ON "{table.name}" ({column_list})',
                label=f"index {index.name}",
            )


async def ensure_schema() -> None:
    """
    Barcha model jadvallarini yaratadi va yetishmayotgan ustunlarni qo'shadi.

    `main.py` lifespan'da DB ulanishi tekshirilgandan keyin chaqiriladi.
    Balans to'ldirish (admin tasdiqlash / avtomatik tasdiqlash) uchun zarur
    bo'lgan `deposits`, `transactions` va `users` ustunlari shu yerda
    kafolatlanadi.
    """
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_bootstrap_raw_schema)
        await conn.run_sync(_add_missing_columns)
    logger.info("Baza sxemasi tekshirildi: jadval va ustunlar mavjud.")
