"""
database/models.py
==================
PostgreSQL (Supabase) bazasining to'liq sxemasi — SQLAlchemy 2.0 declarative ORM.

Jadvallar:
 - users             : foydalanuvchilar (balans, referal, kanal tasdiqlash)
 - transactions      : balans harakatlari audit jurnali (tranzaksiya yaxlitligi)
 - channels          : majburiy (OP) homiy kanallar
 - referrals         : referal takliflar (pending -> completed)
 - products          : mahsulotlar (stars, premium, nft, gift)
 - orders            : buyurtmalar (XTR to'lov bilan)
 - withdrawals       : Stars yechib olish so'rovlari (hold -> approved/rejected)
 - settings          : tizim sozlamalari (referral_reward_stars, min_withdrawal_stars)
 - anti_fraud_logs   : nakrutka/fraud holatlari jurnali

Eslatma: BigInteger PK `sqlite` uchun Integer'ga variant qilingan — shu bilan
sintaksis/yigmaq testlari SQLite'da ham, production'da Postgres'da ham ishlaydi.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# -------------------------------------------------------------
# Yordamchi tiplar: production (PostgreSQL) uchun BIGINT,
# lokal test (SQLite) uchun INTEGER (avtoincrement ishlashi uchun).
# -------------------------------------------------------------
BIGINT = BigInteger().with_variant(Integer(), "sqlite")


def utcnow() -> dt.datetime:
    """Hozirgi vaqt (timezone bilan) — barcha sana ustunlari uchun yagona manba."""
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    """Barcha modellar uchun umumiy deklarativ asos."""


# =============================================================
# 1. FOYDALANUVCHILAR
# =============================================================
class User(Base):
    """Telegram foydalanuvchisi va uning Stars balansi."""

    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(
        BIGINT, primary_key=True, comment="Telegram user ID (PK)"
    )
    username: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="@username (bo'lishi mumkin emas)"
    )
    first_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="Telegram first_name"
    )
    last_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="Telegram last_name"
    )
    referrer_id: Mapped[int | None] = mapped_column(
        BIGINT,
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Kim taklif qilgan (birinchi kelgan referal)",
    )
    stars_balance: Mapped[int] = mapped_column(
        BIGINT,
        nullable=False,
        default=0,
        comment="Joriy balans (Telegram Stars)",
    )
    channels_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Majburiy kanallarga a'zolik tasdiqlanganmi",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Rassilka uchun faol foydalanuvchi (ban bo'lsa False)",
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    last_active_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        comment="Oxirgi kirgan vaqti (bugun kirganlar hisobi uchun)",
    )

    __table_args__ = (
        # DB darajasidagi qo'shimcha himoya: balans hech qachon 0 dan pastga tushmasin.
        # Asosiy tekshiruv kodda (update_balance), bu — oxirgi chegara.
        CheckConstraint("stars_balance >= 0", name="ck_users_balance_non_negative"),
        Index("ix_users_last_active_at", "last_active_at"),
    )


# =============================================================
# 2. BALANS TRANTSAKSIYALARI (AUDIT)
# =============================================================
class Transaction(Base):
    """
    Balans harakatlarining audit jurnali.

    Har bir update_balance chaqiruvi shu jadvalga yozuv qoldiradi:
    deposit (+), referral_reward (+), bonus (+), purchase (-),
    withdraw_hold (-), refund (+), admin_adjust (+/-).
    """

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount: Mapped[int] = mapped_column(
        BIGINT, nullable=False, comment="Ishora bilan miqdor (musbat/yorliq)"
    )
    tx_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="Tranzaksiya turi (deposit, refund, ...)"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="Tashqi ID: order_id, withdrawal_id, telegram_payment_charge_id",
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    __table_args__ = (
        Index("ix_transactions_user_created", "user_id", "created_at"),
        Index("ix_transactions_type", "tx_type"),
    )


# =============================================================
# 3. MAJBURIY (OP) KANALLAR
# =============================================================
class Channel(Base):
    """Botga kirishda majburiy a'zo bo'lishi kerak bo'lgan homiy kanallar."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BIGINT, nullable=False, unique=True, comment="Kanal chat_id (-100...)"
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    invite_link: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="@username (mos kelmasa None)"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="Faqat faol kanallar majburiy"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (Index("ix_channels_active", "is_active"),)


# =============================================================
# 4. REFERALLAR
# =============================================================
class Referral(Base):
    """Referal takliflar: pending -> completed (kanallar tasdiqlangach)."""

    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    referrer_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Taklif qilgan foydalanuvchi",
    )
    referred_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # bitta foydalanuvchi faqat bir marta referal bo'la oladi
        comment="Taklif qilingan yangi a'zo",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", comment="pending/completed/rejected"
    )
    reward_amount: Mapped[int] = mapped_column(
        BIGINT, nullable=False, default=0, comment="Berilgan reward (completed bo'lganda)"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'rejected')",
            name="ck_referrals_status",
        ),
        # Tezlik (velocity) so'rovi uchun: kim, qachon
        Index("ix_referrals_referrer_created", "referrer_id", "created_at"),
    )


# =============================================================
# 5. MAHSULOTLAR
# =============================================================
class Product(Base):
    """
    Xarid qilinadigan mahsulotlar.

    Kategoriyalar (category):
      - 'stars'  : Telegram Stars to'plamlari
      - 'premium': Telegram Premium obunasi
      - 'nft'    : NFT sovg'alar
      - 'gift'   : Mayda giftlar
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_xtr: Mapped[int] = mapped_column(
        BIGINT, nullable=False, comment="Narx Telegram Stars (XTR) da"
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="Mahsulot detali: {'stars': 100}, {'months': 1} ..."
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "category IN ('stars', 'premium', 'nft', 'gift')",
            name="ck_products_category",
        ),
        Index("ix_products_category_active", "category", "is_active"),
    )


# =============================================================
# 6. BUYURTMALAR
# =============================================================
class Order(Base):
    """Xarid buyurtmalari (Telegram Stars to'lovi bilan tasdiqlanadi)."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    payment_method: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="xtr (Telegram Stars invoice) yoki balance"
    )
    amount_xtr: Mapped[int] = mapped_column(BIGINT, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", comment="pending/paid/cancelled"
    )
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,  # bir xil to'lov ikki marta hisoblanmasligi uchun (idempotency)
        comment="successful_payment.telegram_payment_charge_id",
    )
    order_data: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, comment="Buyurtma detali (snapshot)"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    paid_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'paid', 'cancelled')", name="ck_orders_status"),
        CheckConstraint("amount_xtr > 0", name="ck_orders_amount_positive"),
        Index("ix_orders_user_status", "user_id", "status"),
    )


# =============================================================
# 7. STARS YECHIB OLISH
# =============================================================
class Withdrawal(Base):
    """
    Stars yechib olish so'rovlari.

    Oqim: create_withdrawal (balansdan muzlatish/hold, status=pending)
          -> approve_withdrawal (status=approved, pul allaqachon ushlangan)
          -> reject_withdrawal (status=rejected, muzlatilgan Stars qaytariladi)
    """

    __tablename__ = "withdrawals"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount: Mapped[int] = mapped_column(BIGINT, nullable=False)
    destination_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="ton_wallet / card / stars_username ..."
    )
    destination_address: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", comment="pending/approved/rejected"
    )
    admin_id: Mapped[int | None] = mapped_column(
        BIGINT, nullable=True, comment="Qaysi admin ko'rib chiqdi"
    )
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    processed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_withdrawals_status",
        ),
        CheckConstraint("amount > 0", name="ck_withdrawals_amount_positive"),
        Index("ix_withdrawals_status", "status"),
    )


# =============================================================
# 8. TIZIM SOZLAMALARI
# =============================================================
class Setting(Base):
    """
    Kalit-qiymat sozlamalari (admin paneldan o'zgartiriladi).

    Masalan:
      referral_reward_stars  -> "10"
      min_withdrawal_stars   -> "50"
    Qiymat TEXT sifatida saqlanadi; o'qishda int/bool'ga coerced qilinadi.
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


# =============================================================
# 9. ANTI-FRAUD JURNALI
# =============================================================
class AntiFraudLog(Base):
    """Nakrutka va boshqa shubhali faoliyat yozuvlari."""

    __tablename__ = "anti_fraud_logs"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    referrer_id: Mapped[int] = mapped_column(
        BIGINT, nullable=False, index=True, comment="Tekshirilgan referrer"
    )
    action: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="Masalan: referral_velocity"
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, comment="Holat detali (count, limit, ...)"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    __table_args__ = (Index("ix_antifraud_referrer_created", "referrer_id", "created_at"),)
