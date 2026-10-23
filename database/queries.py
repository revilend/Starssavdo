"""
database/queries.py
===================
Repository qatlam — PostgreSQL (Supabase) bilan ishlaydigan BARCHA asinxron SQL metodlar.

Ushbu modulda quyidagi repository klasslari mavjud:
  - UserRepository       : foydalanuvchi, balans, statistika, rassilka
  - ChannelRepository    : majburiy (OP) homiy kanallar
  - ReferralRepository   : referal tizimi (standart 5/10 Stars reward)
  - ProductRepository    : mahsulotlar (stars/premium/nft/gift)
  - OrderRepository      : buyurtmalar va XTR to'lov tasdiqlash
  - WithdrawalRepository : Stars yechib olish (hold -> approve/reject)
  - SettingsRepository   : tizim sozlamalari (kalit-qiymat)

TRANTSAKSIYA YAXLITLIGI (transaction integrity):
  - Har bir metod `get_session()` kontekstidan foydalanadi:
    muvaffaqiyat -> COMMIT, xatolik -> ROLLBACK (avtomatik).
  - Balans o'zgarishlari `SELECT ... FOR UPDATE` (row-lock) bilan bajariladi —
    parallel (bir vaqtli) so'rovlar balansni buzib qo'ya olmaydi.
  - Balans 0 dan pastga tushsa `InsufficientBalanceError` uzatiladi
    (qo'shimcha ravishda DB'da CHECK constraint ham bor).
  - Nusxa yaratishdagi parallel races: `IntegrityError` ushlanib,
    rollback -> qayta o'qish yo'li bilan hal qilinadi.

Ishlatish misoli:
    from database.queries import UserRepository, InsufficientBalanceError

    user = await UserRepository.get_or_create_user(123, "ali", "Ali", None)
    try:
        new_balance = await UserRepository.update_balance(
            123, -30, tx_type="purchase", description="100 Stars xaridi"
        )
    except InsufficientBalanceError:
        ...
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Final, NamedTuple

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings as app_settings
from database.connection import get_session
from database.models import (
    AntiFraudLog,
    Channel,
    DepositRequest,
    Order,
    Product,
    PromoCode,
    PromoRedemption,
    Referral,
    Setting,
    Transaction as TransactionRow,
    User,
    UserBlock,
    Withdrawal,
    utcnow,
)

logger = logging.getLogger(__name__)

# =============================================================
# KONSTANTALAR
# =============================================================

# Mahsulot toifalari (ProductRepository uchun)
PRODUCT_CATEGORIES: Final[tuple[str, ...]] = ("stars", "premium", "nft", "gift")

# Balans tranzaksiya turlari (transactions.tx_type)
TX_DEPOSIT: Final[str] = "deposit"              # to'ldirish (+)
TX_REFERRAL_REWARD: Final[str] = "referral_reward"  # referal reward (+)
TX_BONUS: Final[str] = "bonus"                  # bonus (+)
TX_PURCHASE: Final[str] = "purchase"            # xarid (-)
TX_WITHDRAW_HOLD: Final[str] = "withdraw_hold"  # yechib olish uchun muzlatish (-)
TX_REFUND: Final[str] = "refund"                # rad etilganda qaytarish (+)
TX_ADMIN_ADJUST: Final[str] = "admin_adjust"    # admin tuzatishi (+/-)
TX_ORDER_STARS: Final[str] = "order_stars"      # Stars to'plami xaridi (+)
TX_PROMO: Final[str] = "promo"                 # promokod (+)

# Referal statuslari
REFERRAL_PENDING: Final[str] = "pending"
REFERRAL_COMPLETED: Final[str] = "completed"
REFERRAL_REJECTED: Final[str] = "rejected"

# Buyurtma statuslari
ORDER_PENDING: Final[str] = "pending"
ORDER_PAID: Final[str] = "paid"
ORDER_CANCELLED: Final[str] = "cancelled"
ORDER_EXPIRED: Final[str] = "expired"

# To'lov usullari
PAYMENT_CARD: Final[str] = "card"        # HUMO karta (UZS, unikal summa)
PAYMENT_XTR: Final[str] = "xtr"          # Telegram Stars invoice (eski)
PAYMENT_BALANCE: Final[str] = "balance"  # ichki balansdan (eski)

# Yechib olish statuslari
WITHDRAWAL_PENDING: Final[str] = "pending"
WITHDRAWAL_APPROVED: Final[str] = "approved"
WITHDRAWAL_REJECTED: Final[str] = "rejected"

# Tizim sozlamalari kalitlari
SETTING_REFERRAL_REWARD: Final[str] = "referral_reward_stars"
SETTING_MIN_WITHDRAWAL: Final[str] = "min_withdrawal_stars"
SETTING_CARD_NUMBER: Final[str] = "payment_card_number"
SETTING_CARD_HOLDER: Final[str] = "payment_card_holder"
SETTING_STAR_PRICE: Final[str] = "star_price_uzs"
SETTING_CARD_LOG_CHAT: Final[str] = "card_log_chat_id"
SETTING_ORDER_LOG_CHAT: Final[str] = "order_log_chat_id"

# Balans to'ldirish so'rovlari statuslari
DEPOSIT_PENDING: Final[str] = "pending"
DEPOSIT_APPROVED: Final[str] = "approved"
DEPOSIT_REJECTED: Final[str] = "rejected"
DEPOSIT_EXPIRED: Final[str] = "expired"

# Avtomatik karta to'lovi (jurnal chati orqali)
# `admin_id=0` — so'rov admin emas, tizim tomonidan tasdiqlangan.
AUTO_DEPOSIT_ADMIN_ID: Final[int] = 0

# Kolliziyani oldini oluvchi "kod": asosiy summaga tasodifiy 40..100 so'm
# qo'shiladi (30 000 -> 30 047). Bir xil miqdorni tanlagan ikki foydalanuvchi
# har xil summa to'laydi va to'lov aniq moslanadi.
DEPOSIT_CODE_MIN: Final[int] = 40
DEPOSIT_CODE_MAX: Final[int] = 100
DEPOSIT_CODE_ATTEMPTS: Final[int] = 30

# Referal velocity (nakrutka) tekshiruvi
VELOCITY_WINDOW: Final[timedelta] = timedelta(hours=1)  # so'nggi 1 soat
VELOCITY_DEFAULT_LIMIT: Final[int] = 15


# =============================================================
# XATOLIKLAR (exceptions)
# =============================================================
class RepositoryError(Exception):
    """Repository darajasidagi umumiy xatolik (topilmadi, yaroqsiz holat)."""


class InsufficientBalanceError(RepositoryError):
    """Balans yetarli emas — 0 dan pastga tushishga yo'l qo'yilmaydi."""


class SelfReferralError(RepositoryError):
    """Foydalanuvchi o'zini taklif qilmoqda — bloklandi."""


class WithdrawalStateError(RepositoryError):
    """Yechib olish so'rovi allaqachon ko'rib chiqilgan (pending emas)."""


class DepositStateError(RepositoryError):
    """Balans to'ldirish so'rovi allaqachon ko'rib chiqilgan (pending emas)."""


class PromoError(RepositoryError):
    """Promokod bilan bog'liq xatolik (topilmadi, limit tugagan, allaqachon ishlatilgan)."""


# =============================================================
# NATIJA TIPLARI
# =============================================================
class ReferralCounts(NamedTuple):
    """Referal sonlari: jami va muvaffaqiyatli (completed)."""

    total: int
    completed: int


class VelocityCheck(NamedTuple):
    """Referal velocity natijasi: so'nggi 1 soatdagi soni va flag holati."""

    count: int
    flagged: bool


class TopReferrer(NamedTuple):
    """Top taklif qiluvchi qatori (🏆 reyting uchun)."""

    user_id: int
    username: str | None
    first_name: str | None
    completed: int


class TotalStats(NamedTuple):
    """Admin statistikasi (get_total_stats)."""

    total_members: int          # jami a'zolar
    today_active: int           # bugun kirganlar
    total_distributed_stars: int  # jami tarqatilgan Starslar (rewardlar)
    total_withdrawn_stars: int    # jami yechilgan Starslar (approved)


@dataclass(frozen=True)
class ReferralReward:
    """
    `confirm_referral_reward` natijasi — xabarnoma uchun referrer ma'lumotlari.
    Agar referrer bazadan yo'qolsa yoki reward 0 bo'lsa — metod None qaytaradi.
    """

    referral_id: int
    referrer_id: int
    username: str | None
    first_name: str | None
    reward_amount: int


# =============================================================
# YORDAMCHI FUNKSIYALAR (private)
# =============================================================
def _coerce_setting(value: str | None, default: Any) -> Any:
    """
    settings.value TEXT qiymatini tabiiy tipga o'tkazadi:
      "10" -> 10, "true" -> True, "abc" -> "abc", None -> default
    """
    if value is None:
        return default
    text = value.strip()
    if text == "":
        return default
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(text)
    except ValueError:
        return text


async def _load_setting(session: AsyncSession, key: str, default: Any) -> Any:
    """
    Sozlamani BERILGAN session ichida o'qish (boshqa ulanish ochmaslik uchun).

    Muhim: `confirm_referral_reward` kabi tranzaksiya ichidagi metodlar shu
    yordamchidan foydalanadi — aks holda boshqa connection ochilib,
    lock tartibi buzilishi (deadlock) mumkin edi.
    """
    row = await session.get(Setting, key, with_for_update=True)
    if row is None:
        return default
    return _coerce_setting(row.value, default)


def _apply_profile(
    user: User,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> None:
    """Profil maydonlarini faqat berilgan (None bo'lmagan) qiymatlar bilan yangilaydi."""
    if username is not None:
        user.username = username
    if first_name is not None:
        user.first_name = first_name
    if last_name is not None:
        user.last_name = last_name


# =============================================================
# 1. USER REPOSITORY
# =============================================================
class UserRepository:
    """Foydalanuvchilar, balans, statistika va rassilka metodlari."""

    # ------------------------------------------------------------------
    @staticmethod
    async def get_or_create_user(
        user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        referrer_id: int | None = None,
    ) -> User:
        """
        Foydalanuvchini bazaga qo'shadi YOKI mavjud bo'lsa `last_active_at`
        va profil ma'lumotlarini yangilaydi.

        - Yangi foydalanuvchi: referrer_id saqlanadi (o'zini taklif qilish
          himoyasi bilan — referrer_id == user_id bo'lsa e'tibors qoldiriladi).
        - Mavjud foydalanuvchi: last_active_at = hozir; username/first_name/
          last_name faqat berilgan (None emas) qiymatlar bilan yangilanadi;
          referrer_id faqat hali bo'sh bo'lsa to'ldiriladi.
        - Parallel yozishdagi PK conflict `IntegrityError` sifatida ushlanib,
          rollback -> qayta o'qish orqali hal qilinadi (tranzaksiya yaxlitligi
          buzilmaydi).

        Returns: User (barcha ustunlar yuklangan holatda).
        """
        now = utcnow()

        async with get_session() as session:
            # Rekordni qulf bilan o'qish (FOR UPDATE) — parallel kirish uchun
            user = await session.get(User, user_id, with_for_update=True)

            if user is None:
                # O'zini taklif qilishni bloklash
                safe_referrer = None if referrer_id == user_id else referrer_id
                user = User(
                    user_id=user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    referrer_id=safe_referrer,
                    last_active_at=now,
                )
                session.add(user)
                try:
                    await session.flush()  # xatolik shu yerda ushlanadi
                except IntegrityError:
                    # Parallel chaqiruv allaqachon yaratgan — uni olib, yangilaymiz
                    await session.rollback()
                    user = await session.get(User, user_id, with_for_update=True)
                    if user is None:
                        raise RepositoryError(
                            f"Foydalanuvchi {user_id} yaratish konfliktidan "
                            "keyin ham topilmadi"
                        ) from None
                    _apply_profile(user, username, first_name, last_name)
                    user.last_active_at = now
                    if user.referrer_id is None and referrer_id not in (None, user_id):
                        user.referrer_id = referrer_id
                    await session.flush()
                return user

            # Mavjud foydalanuvchi — faoliyatni va profilni yangilash
            _apply_profile(user, username, first_name, last_name)
            user.last_active_at = now
            if user.referrer_id is None and referrer_id not in (None, user_id):
                user.referrer_id = referrer_id
            await session.flush()
            return user

    # ------------------------------------------------------------------
    @staticmethod
    async def get_user(user_id: int) -> User | None:
        """
        Foydalanuvchi ma'lumotlari va joriy `stars_balance`ni qaytaradi.

        Returns: User | None (topilmagan bo'lsa None).
        """
        async with get_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    @staticmethod
    async def update_balance(
        user_id: int,
        amount: int,
        tx_type: str,
        description: str,
        reference_id: str | None = None,
    ) -> int:
        """
        Balansni ATOMAR tarzda o'zgartiradi va `transactions` jadvaliga
        audit yozuv qoldiradi.

        Tartib (bitta tranzaksiya ichida):
          1. SELECT ... FOR UPDATE — foydalanuvchi qatorini qulflaydi
             (parallel so'rovlar navbat kutadi — race yo'q).
          2. Yangi balans hisoblanadi; 0 dan pastga tushsa
             `InsufficientBalanceError` -> avtomatik ROLLBACK.
          3. Balans yangilanadi.
          4. transactions jadvaliga audit yozuvi qo'shiladi (oxirgi
             raqamni tekshirish uchun qo'llaniladigan to'liq jurnal).
          5. COMMIT (get_session context manager).

        Args:
            amount: ishora bilan (+ to'ldirish, - yeкish/xarid)
            tx_type: 'deposit', 'referral_reward', 'purchase', 'refund', ...
            reference_id: bog'liq obyekt IDsi (order_id, withdrawal_id, ...)

        Returns: yangi balans (int)
        """
        if amount == 0:
            raise RepositoryError("amount 0 bo'lishi mumkin emas")

        async with get_session() as session:
            user = await session.get(User, user_id, with_for_update=True)
            if user is None:
                raise RepositoryError(f"Foydalanuvchi {user_id} topilmadi")

            new_balance = user.stars_balance + amount
            if new_balance < 0:
                # Xatolik -> session ROLLBACK (balans o'zgarmaydi, audit ham yozilmaydi)
                raise InsufficientBalanceError(
                    f"Balans yetarli emas: joriy={user.stars_balance}, "
                    f"so'ralgan={amount}, qolgan={new_balance}"
                )

            user.stars_balance = new_balance
            if amount > 0:
                # "Jami to'plangan" ko'rsatkichi ham yangilanadi (NULL bo'lsa 0).
                user.total_earned_stars = (user.total_earned_stars or 0) + amount
            session.add(
                TransactionRow(
                    user_id=user_id,
                    amount=amount,
                    tx_type=tx_type,
                    description=description,
                    reference_id=reference_id,
                )
            )
            await session.flush()
            logger.info(
                "Balans yangilandi: user=%s amount=%+d type=%s -> %s",
                user_id, amount, tx_type, new_balance,
            )
            return new_balance

    # ------------------------------------------------------------------
    @staticmethod
    async def set_channels_verified(user_id: int) -> bool:
        """
        Homiy kanallarga a'zolikni tasdiqlaydi (`channels_verified = True`).

        Returns: True — yangilandi; False — foydalanuvchi topilmadi.
        """
        async with get_session() as session:
            user = await session.get(User, user_id, with_for_update=True)
            if user is None:
                logger.warning("set_channels_verified: user %s topilmadi", user_id)
                return False
            if not user.channels_verified:
                user.channels_verified = True
                await session.flush()
            return True

    # ------------------------------------------------------------------
    @staticmethod
    async def is_channels_verified(user_id: int) -> bool:
        """
        Faqat `channels_verified` ustunini o'qiydi (yengil so'rov).

        Middleware har bir yangilanishda to'liq `User` qatorini yuklamasligi
        uchun ishlatiladi.

        Returns: True — foydalanuvchi kanallarga a'zo deb tasdiqlangan;
                 False — tasdiqlanmagan yoki foydalanuvchi topilmadi.
        """
        async with get_session() as session:
            result = await session.execute(
                select(User.channels_verified).where(User.user_id == user_id)
            )
            return bool(result.scalar_one_or_none())

    # ------------------------------------------------------------------
    @staticmethod
    async def get_total_stats() -> TotalStats:
        """
        Admin uchun umumiy hisobot:

          - total_members        : jami a'zolar (users soni)
          - today_active         : bugun (UTC kun boshidan) kirganlar
          - total_distributed_stars : jami tarqatilgan Starslar
            (musbat tranzaksiyalar, deposit va refundlardan TASHQARI —
             ya'ni referral reward/bonus kabi tarqatilgan mablag'lar)
          - total_withdrawn_stars : jami yechilgan Starslar (status='approved')
        """
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        async with get_session() as session:
            total_members = int(
                await session.scalar(select(func.count()).select_from(User)) or 0
            )

            today_active = int(
                await session.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(User.last_active_at >= today_start)
                )
                or 0
            )

            distributed = int(
                await session.scalar(
                    select(
                        func.coalesce(
                            func.sum(
                                case(
                                    (
                                        and_(
                                            TransactionRow.amount > 0,
                                            TransactionRow.tx_type.notin_(
                                                (TX_DEPOSIT, TX_REFUND)
                                            ),
                                        ),
                                        TransactionRow.amount,
                                    ),
                                    else_=0,
                                )
                            ),
                            0,
                        )
                    )
                )
                or 0
            )

            withdrawn = int(
                await session.scalar(
                    select(func.coalesce(func.sum(Withdrawal.amount), 0)).where(
                        Withdrawal.status == WITHDRAWAL_APPROVED
                    )
                )
                or 0
            )

            return TotalStats(
                total_members=total_members,
                today_active=today_active,
                total_distributed_stars=distributed,
                total_withdrawn_stars=withdrawn,
            )

    # ------------------------------------------------------------------
    @staticmethod
    async def get_all_user_ids() -> list[int]:
        """
        Admin rassilka (broadcast) uchun barcha FAOL foydalanuvchilar IDlari.
        """
        async with get_session() as session:
            result = await session.execute(
                select(User.user_id)
                .where(User.is_active.is_(True))
                .order_by(User.user_id)
            )
            return [int(uid) for uid in result.scalars().all()]

    # ------------------------------------------------------------------
    @staticmethod
    async def get_recent_transactions(
        user_id: int, limit: int = 10
    ) -> list[TransactionRow]:
        """
        Foydalanuvchining oxirgi tranzaksiyalari (eng yangisi birinchi).

        "📜 Tranzaksiyalar tarixi" tugmasi uchun ishlatiladi.
        """
        if limit <= 0:
            return []
        async with get_session() as session:
            result = await session.execute(
                select(TransactionRow)
                .where(TransactionRow.user_id == user_id)
                .order_by(
                    TransactionRow.created_at.desc(), TransactionRow.id.desc()
                )
                .limit(limit)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    @staticmethod
    async def count_new_users_today() -> int:
        """
        Bugun (UTC kun boshidan) qo'shilgan foydalanuvchilar soni.

        Admin statistikasidagi "📅 Bugun qo'shilganlar" uchun.
        """
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        async with get_session() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(User.created_at >= today_start)
                )
                or 0
            )


# =============================================================
# 2. CHANNEL REPOSITORY (majburiy kanallar — OP)
# =============================================================
class ChannelRepository:
    """Majburiy homiy kanallar boshqaruvi (admin panel uchun)."""

    # ------------------------------------------------------------------
    @staticmethod
    async def get_active_channels() -> list[Channel]:
        """Barcha faol (is_active=True) majburiy kanallar ro'yxati."""
        async with get_session() as session:
            result = await session.execute(
                select(Channel)
                .where(Channel.is_active.is_(True))
                .order_by(Channel.id)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    @staticmethod
    async def get_all_channels() -> list[Channel]:
        """
        Barcha kanallar (faol VA nofaol) — admin ro'yxatidagi holat
        (✅/🚫 emoji) to'g'ri ko'rsatishi uchun.
        """
        async with get_session() as session:
            result = await session.execute(
                select(Channel).order_by(Channel.id)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    @staticmethod
    async def get_channel(channel_id: int) -> Channel | None:
        """
        Kanalni ID bo'yicha oladi (yoqish/o'chirish va o'chirish
        tugmalari uchun — `delete_channel` ga chat_id kerak).

        Returns: Channel | None (topilmagan bo'lsa None).
        """
        async with get_session() as session:
            return await session.get(Channel, channel_id)

    # ------------------------------------------------------------------
    @staticmethod
    async def add_channel(
        chat_id: int,
        title: str,
        invite_link: str,
        username: str | None = None,
    ) -> Channel:
        """
        Yangi majburiy kanal qo'shadi.

        Raises:
            RepositoryError: bunday chat_id allaqachon mavjud bo'lsa.
        """
        async with get_session() as session:
            channel = Channel(
                chat_id=chat_id,
                title=title,
                invite_link=invite_link,
                username=username,
                is_active=True,
            )
            session.add(channel)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                raise RepositoryError(
                    f"Kanal chat_id={chat_id} allaqachon bazada mavjud"
                ) from None
            logger.info("Kanal qo'shildi: %s (%s)", title, chat_id)
            return channel

    # ------------------------------------------------------------------
    @staticmethod
    async def delete_channel(chat_id: int) -> bool:
        """
        Kanalni bazadan o'chiradi (chat_id bo'yicha).

        Returns: True — o'chirildi; False — topilmadi.
        """
        async with get_session() as session:
            result = await session.execute(
                delete(Channel).where(Channel.chat_id == chat_id)
            )
            await session.flush()
            removed = bool(result.rowcount)
            if removed:
                logger.info("Kanal o'chirildi: chat_id=%s", chat_id)
            return removed

    # ------------------------------------------------------------------
    @staticmethod
    async def toggle_channel_status(channel_id: int, is_active: bool) -> Channel | None:
        """
        Kanalni yoqish/o'chirish.

        Returns: yangilangan Channel yoki kanal topilmagan bo'lsa None.
        """
        async with get_session() as session:
            channel = await session.get(Channel, channel_id, with_for_update=True)
            if channel is None:
                return None
            channel.is_active = is_active
            await session.flush()
            logger.info(
                "Kanal statusi: id=%s -> is_active=%s", channel_id, is_active
            )
            return channel


# =============================================================
# 3. REFERRAL REPOSITORY (referal tizimi)
# =============================================================
class ReferralRepository:
    """Referal takliflar: yaratish, reward berish, statistika, anti-fraud."""

    # ------------------------------------------------------------------
    @staticmethod
    async def create_referral(referrer_id: int, referred_id: int) -> Referral | None:
        """
        Yangi taklifni `pending` statusda yaratadi.

        Himoyalar:
          - O'zini taklif qilish -> `SelfReferralError`.
          - referred_id allaqachan kuzatuvda bo'lsa -> None qaytaradi
            (takroriy yozuv yaratilmaydi, UNIQUE constraint himoyasi).

        Returns: yangilangan Referral yoki taklif allaqachon mavjud bo'lsa None.
        """
        if referrer_id == referred_id:
            raise SelfReferralError(
                f"Foydalanuvchi o'zini taklif qila olmaydi (user_id={referrer_id})"
            )

        async with get_session() as session:
            # Avval mavjudlikni tekshirish (tez yo'l)
            existing = await session.execute(
                select(Referral).where(Referral.referred_id == referred_id)
            )
            if existing.scalar_one_or_none() is not None:
                return None

            referral = Referral(
                referrer_id=referrer_id,
                referred_id=referred_id,
                status=REFERRAL_PENDING,
                reward_amount=0,
            )
            session.add(referral)
            try:
                await session.flush()
            except IntegrityError:
                # Parallel yozuv paydo bo'ldi — uni olib qaytaramiz
                await session.rollback()
                again = await session.execute(
                    select(Referral).where(Referral.referred_id == referred_id)
                )
                if again.scalar_one_or_none() is None:
                    raise RepositoryError(
                        "Referal yozuvi yaratishda noma'lum konflikt"
                    ) from None
                return None

            logger.info(
                "Yangi referal: %s -> %s (pending)", referrer_id, referred_id
            )
            return referral

    # ------------------------------------------------------------------
    @staticmethod
    async def confirm_referral_reward(referred_id: int) -> ReferralReward | None:
        """
        Yangi a'zo majburiy kanallarga a'zo bo'lgach chaqiriladi.

        Bitta tranzaksiyada bajariladi (atomik):
          1. referred_id bo'yicha `pending` referal qulflanadi (FOR UPDATE).
          2. Agar topilmagi yoki allaqachon completed bo'lsa -> None
             (takroriy reward BERILMAYDI — idempotent).
          3. `settings.referral_reward_stars` o'qiladi (odatda 5/10 Stars,
             fallback — .env dagi REFERRAL_REWARD_STARS).
          4. Referrer qatori qulflanadi va balansga reward qo'shiladi
             (`transactions` audit bilan).
          5. Referal statusi -> 'completed', completed_at o'rnatiladi.

        Returns:
            ReferralReward — xabar yuborish uchun referrer ma'lumotlari.
            None — referal topilmadi/allaqachon tugallangan/referrer yo'q.
        """
        async with get_session() as session:
            result = await session.execute(
                select(Referral)
                .where(Referral.referred_id == referred_id)
                .with_for_update()
            )
            referral = result.scalar_one_or_none()

            if referral is None or referral.status != REFERRAL_PENDING:
                # Mavjud emas yoki allaqachon completed/rejected — qayta berilmaydi
                return None

            reward = int(
                await _load_setting(
                    session, SETTING_REFERRAL_REWARD, app_settings.REFERRAL_REWARD_STARS
                )
            )

            referrer = await session.get(User, referral.referrer_id, with_for_update=True)
            if referrer is None:
                # Referrer bazadan yo'qolgani — referalni yakunlaymiz, reward yo'q
                logger.warning(
                    "Referrer %s topilmadi; referal %s rewardsiz yakunlanadi",
                    referral.referrer_id, referred_id,
                )
                referral.status = REFERRAL_COMPLETED
                referral.reward_amount = 0
                referral.completed_at = utcnow()
                await session.flush()
                return None

            if reward > 0:
                referrer.stars_balance += reward
                session.add(
                    TransactionRow(
                        user_id=referrer.user_id,
                        amount=reward,
                        tx_type=TX_REFERRAL_REWARD,
                        description=f"Referal reward ({referred_id} taklif qilindi)",
                        reference_id=str(referred_id),
                    )
                )

            referral.status = REFERRAL_COMPLETED
            referral.reward_amount = reward
            referral.completed_at = utcnow()
            await session.flush()

            logger.info(
                "Referal tasdiqlandi: referrer=%s referred=%s reward=%s",
                referrer.user_id, referred_id, reward,
            )
            return ReferralReward(
                referral_id=referral.id,
                referrer_id=referrer.user_id,
                username=referrer.username,
                first_name=referrer.first_name,
                reward_amount=reward,
            )

    # ------------------------------------------------------------------
    @staticmethod
    async def get_user_referrals_count(user_id: int) -> ReferralCounts:
        """
        Foydalanuvchining jami va muvaffaqiyatli (completed) takliflari soni.

        Returns: ReferralCounts(total, completed)
        """
        async with get_session() as session:
            row = (
                await session.execute(
                    select(
                        func.count().label("total"),
                        func.sum(
                            case((Referral.status == REFERRAL_COMPLETED, 1), else_=0)
                        ).label("completed"),
                    ).where(Referral.referrer_id == user_id)
                )
            ).one()
            return ReferralCounts(
                total=int(row.total or 0),
                completed=int(row.completed or 0),
            )

    # ------------------------------------------------------------------
    @staticmethod
    async def get_top_referrers(limit: int = 10) -> list[TopReferrer]:
        """
        Eng ko'p do'st taklif qilgan foydalanuvchilar reytingi.

        Faqat `completed` (mukofot berilgan) takliflar sanaladi —
        pending yozuvlar reytingga ta'sir qilmaydi.

        Returns: TopReferrer(user_id, username, first_name, completed)
            ko'rinishidagi ro'yxat — kamayish tartibida, `limit` taga.
        """
        async with get_session() as session:
            result = await session.execute(
                select(
                    User.user_id,
                    User.username,
                    User.first_name,
                    func.count(Referral.id).label("completed"),
                )
                .join(Referral, Referral.referrer_id == User.user_id)
                .where(Referral.status == REFERRAL_COMPLETED)
                .group_by(User.user_id)
                .order_by(func.count(Referral.id).desc(), User.user_id.asc())
                .limit(limit)
            )
            return [
                TopReferrer(
                    user_id=int(row.user_id),
                    username=row.username,
                    first_name=row.first_name,
                    completed=int(row.completed),
                )
                for row in result.all()
            ]

    # ------------------------------------------------------------------
    @staticmethod
    async def check_referral_velocity(
        referrer_id: int, limit: int = VELOCITY_DEFAULT_LIMIT
    ) -> VelocityCheck:
        """
        Nakrutka (velocity) tekshiruvi: referrer bergan SO'NGGI 1 SOATDAGI
        takliflar sonini sanaydi.

        Agar count >= limit bo'lsa:
          - `anti_fraud_logs` jadvaliga yozuv qo'shiladi (JSON details bilan)
          - VelocityCheck(flagged=True) qaytariladi — handler rad etishi kerak.

        Returns: VelocityCheck(count, flagged)
        """
        cutoff = utcnow() - VELOCITY_WINDOW

        async with get_session() as session:
            count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Referral)
                    .where(
                        and_(
                            Referral.referrer_id == referrer_id,
                            Referral.created_at >= cutoff,
                        )
                    )
                )
                or 0
            )
            flagged = count >= limit

            if flagged:
                details = {
                    "referrer_id": referrer_id,
                    "count": count,
                    "limit": limit,
                    "window_seconds": int(VELOCITY_WINDOW.total_seconds()),
                    "cutoff": cutoff.isoformat(),
                }
                session.add(
                    AntiFraudLog(
                        referrer_id=referrer_id,
                        action="referral_velocity",
                        details=details,
                    )
                )
                await session.flush()
                logger.warning(
                    "REFERAL VELOCITY flag: referrer=%s count=%s limit=%s",
                    referrer_id, count, limit,
                )

            return VelocityCheck(count=count, flagged=flagged)


# =============================================================
# 4. PRODUCT REPOSITORY
# =============================================================
class ProductRepository:
    """Mahsulotlar katalogi (stars, premium, nft, gift)."""

    # ------------------------------------------------------------------
    @staticmethod
    async def get_products_by_category(category: str) -> list[Product]:
        """
        Toifa bo'yicha faol mahsulotlar ro'yxati (sort_order bo'yicha).

        Args:
            category: 'stars' | 'premium' | 'nft' | 'gift'

        Raises:
            ValueError: toifa notanish bo'lsa.
        """
        if category not in PRODUCT_CATEGORIES:
            raise ValueError(
                f"Noto'g'ri kategoriya: {category!r}. "
                f"Ruxsat etilgan: {', '.join(PRODUCT_CATEGORIES)}"
            )

        async with get_session() as session:
            result = await session.execute(
                select(Product)
                .where(
                    and_(Product.category == category, Product.is_active.is_(True))
                )
                .order_by(Product.sort_order, Product.id)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    @staticmethod
    async def get_product_by_id(product_id: int) -> Product | None:
        """
        Bitta mahsulotni ID bo'yicha oladi.

        Faollik (`is_active`) BU yerda tekshirilmaydi — xarid qilinadigan
        daqiqada `create_order` / `create_balance_order` qayta tekshiradi
        (o'chirilgan mahsulot uchun buyurtma yaratilmaydi).

        Returns: Product | None (topilmagan bo'lsa None).
        """
        async with get_session() as session:
            return await session.get(Product, product_id)

    # ------------------------------------------------------------------
    @staticmethod
    async def get_all_products(limit: int = 300) -> list[Product]:
        """Admin panel uchun barcha mahsulotlar (toifa va tartib bo'yicha)."""
        async with get_session() as session:
            result = await session.execute(
                select(Product)
                .order_by(Product.category, Product.sort_order, Product.id)
                .limit(limit)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    @staticmethod
    async def create_product(
        category: str,
        title: str,
        price_xtr: int = 0,
        description: str | None = None,
        payload: dict[str, Any] | None = None,
        image_file_id: str | None = None,
        sort_order: int = 0,
        is_active: bool = True,
        price_uzs: int | None = None,
    ) -> Product:
        """
        Yangi mahsulot qo'shadi (admin panel).

        Narx SO'MDA (`price_uzs`) — asosiy. `price_xtr` berilmasa 0;
        `price_uzs` berilmasa `price_xtr * SOM_PER_STAR` dan hisoblanadi
        (eski chaqiruvlar bilan moslik uchun).

        Raises:
            ValueError: toifa notanish, nom bo'sh yoki narx <= 0.
        """
        if category not in PRODUCT_CATEGORIES:
            raise ValueError(f"Noto'g'ri kategoriya: {category!r}")
        title = (title or "").strip()
        if not title:
            raise ValueError("Mahsulot nomi bo'sh bo'lishi mumkin emas")
        if price_uzs is None:
            price_uzs = int(price_xtr) * app_settings.SOM_PER_STAR
        price_uzs = int(price_uzs)
        if price_uzs <= 0:
            raise ValueError("Narx 0 dan katta bo'lishi kerak")
        if int(price_xtr) < 0:
            raise ValueError("price_xtr manfiy bo'lishi mumkin emas")

        async with get_session() as session:
            product = Product(
                category=category,
                title=title,
                description=(description or "").strip() or None,
                price_uzs=price_uzs,
                price_xtr=int(price_xtr),
                payload=payload,
                image_file_id=(image_file_id or None),
                sort_order=sort_order,
                is_active=is_active,
            )
            session.add(product)
            await session.flush()
            logger.info(
                "Mahsulot qo'shildi: id=%s category=%s title=%r price_uzs=%s img=%s",
                product.id, category, title, price_uzs,
                bool(image_file_id),
            )
            return product

    # ------------------------------------------------------------------
    @staticmethod
    async def update_product(product_id: int, **fields: Any) -> Product:
        """
        Mahsulot maydonlarini yangilaydi (`title`, `description`, `price_xtr`,
        `payload`, `image_file_id`, `is_active`, `sort_order`, `category`).

        Raises:
            RepositoryError: mahsulot topilmadi.
            ValueError: noto'g'ri qiymat (narx <= 0, bo'sh nom, ...).
        """
        allowed = {
            "category", "title", "description", "price_xtr", "price_uzs",
            "payload", "image_file_id", "is_active", "sort_order",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Noma'lum maydonlar: {', '.join(sorted(unknown))}")

        if "category" in fields and fields["category"] not in PRODUCT_CATEGORIES:
            raise ValueError(f"Noto'g'ri kategoriya: {fields['category']!r}")
        if "title" in fields:
            fields["title"] = (fields["title"] or "").strip()
            if not fields["title"]:
                raise ValueError("Mahsulot nomi bo'sh bo'lishi mumkin emas")
        if "price_xtr" in fields and int(fields["price_xtr"]) < 0:
            raise ValueError("Narx manfiy bo'lishi mumkin emas")
        if "price_uzs" in fields and int(fields["price_uzs"]) <= 0:
            raise ValueError("Narx 0 dan katta bo'lishi kerak")

        async with get_session() as session:
            product = await session.get(Product, product_id, with_for_update=True)
            if product is None:
                raise RepositoryError(f"Mahsulot {product_id} topilmadi")

            for key, value in fields.items():
                setattr(product, key, value)
            await session.flush()
            logger.info("Mahsulot yangilandi: id=%s fields=%s", product_id, list(fields))
            return product

    # ------------------------------------------------------------------
    @staticmethod
    async def delete_product(product_id: int) -> bool:
        """
        Mahsulotni o'chiradi.

        Agar mahsulot bo'yicha buyurtmalar mavjud bo'lsa (tarixni saqlash
        uchun) o'chirish o'rniga `is_active=False` qilinadi va `False`
        qaytariladi.

        Returns: True — butunlay o'chirildi, False — nofaol qilindi.

        Raises:
            RepositoryError: mahsulot topilmadi.
        """
        async with get_session() as session:
            product = await session.get(Product, product_id, with_for_update=True)
            if product is None:
                raise RepositoryError(f"Mahsulot {product_id} topilmadi")

            orders_count = await session.scalar(
                select(func.count(Order.id)).where(Order.product_id == product_id)
            )
            if orders_count:
                product.is_active = False
                await session.flush()
                logger.info(
                    "Mahsulot o'chirilmadi (buyurtmalar bor) — nofaol qilindi: %s",
                    product_id,
                )
                return False

            await session.delete(product)
            await session.flush()
            logger.info("Mahsulot o'chirildi: id=%s", product_id)
            return True


# =============================================================
# 5. ORDER REPOSITORY
# =============================================================
class OrderRepository:
    """Buyurtmalar: yaratish va Telegram XTR to'lovini tasdiqlash."""

    # ------------------------------------------------------------------
    @staticmethod
    async def create_order(
        user_id: int,
        product_id: int,
        payment_method: str,
        amount_xtr: int,
        order_data: dict[str, Any] | None = None,
    ) -> Order:
        """
        Yangi buyurtma yaratadi (status='pending').

        Mahsulot mavjudligi va faolligi avval tekshiriladi — o'chirilgan
        mahsulot uchun buyurtma yaratilmaydi.

        Raises:
            RepositoryError: foydalanuvchi yoki mahsulot topilmadi/o'chirilgan.
            ValueError: amount_xtr <= 0.
        """
        if amount_xtr <= 0:
            raise ValueError("amount_xtr 0 dan katta bo'lishi kerak")

        async with get_session() as session:
            user = await session.get(User, user_id)
            if user is None:
                raise RepositoryError(f"Foydalanuvchi {user_id} topilmadi")

            product = await session.get(Product, product_id, with_for_update=True)
            if product is None or not product.is_active:
                raise RepositoryError(
                    f"Mahsulot {product_id} topilmadi yoki o'chirilgan"
                )

            order = Order(
                user_id=user_id,
                product_id=product_id,
                payment_method=payment_method,
                amount_xtr=amount_xtr,
                status=ORDER_PENDING,
                order_data=order_data or {},
            )
            session.add(order)
            await session.flush()
            logger.info(
                "Buyurtma yaratildi: id=%s user=%s product=%s amount=%sXTR",
                order.id, user_id, product_id, amount_xtr,
            )
            return order

    # ------------------------------------------------------------------
    @staticmethod
    async def create_card_order(
        user_id: int,
        product_id: int,
        order_data: dict[str, Any] | None = None,
    ) -> Order:
        """
        SO'MDA to'lanadigan (HUMO karta) buyurtma yaratadi.

        Summa = `product.price_uzs + 40..100 so'm` unikal kod. Kod boshqa
        faol buyurtma yoki balans to'ldirish so'rovi bilan takrorlanmaydi.
        Buyurtma `DEPOSIT_TTL_SECONDS` (5 daqiqa) muddatga ega.

        Raises:
            RepositoryError: foydalanuvchi/mahsulot topilmadi, narx 0 yoki
                bo'sh unikal summa topilmadi.
        """
        async with get_session() as session:
            user = await session.get(User, user_id)
            if user is None:
                raise RepositoryError(f"Foydalanuvchi {user_id} topilmadi")

            product = await session.get(Product, product_id, with_for_update=True)
            if product is None or not product.is_active:
                raise RepositoryError(
                    f"Mahsulot {product_id} topilmadi yoki o'chirilgan"
                )
            if product.price_uzs <= 0:
                raise RepositoryError(f"Mahsulot {product_id} narxi belgilanmagan")

            base = int(product.price_uzs)
            pay_amount: int | None = None
            for _ in range(DEPOSIT_CODE_ATTEMPTS):
                code = DEPOSIT_CODE_MIN + secrets.randbelow(
                    DEPOSIT_CODE_MAX - DEPOSIT_CODE_MIN + 1
                )
                candidate = base + code
                taken_order = await session.scalar(
                    select(Order.id).where(
                        Order.status == ORDER_PENDING,
                        Order.pay_amount == candidate,
                    )
                )
                taken_deposit = await session.scalar(
                    select(DepositRequest.id).where(
                        DepositRequest.status == DEPOSIT_PENDING,
                        DepositRequest.pay_amount == candidate,
                    )
                )
                if taken_order is None and taken_deposit is None:
                    pay_amount = candidate
                    break

            if pay_amount is None:
                raise RepositoryError(
                    "Unikal to'lov summasini tanlab bo'lmadi — keyinroq urinib ko'ring"
                )

            payload = product.payload or {}
            stars = int(payload.get("stars") or 0) if isinstance(payload, dict) else 0
            data: dict[str, Any] = {
                "product_title": product.title,
                "category": product.category,
                "payload": payload,
                "price_uzs": base,
            }
            data.update(order_data or {})

            order = Order(
                user_id=user_id,
                product_id=product_id,
                payment_method=PAYMENT_CARD,
                amount_xtr=max(stars, 0),
                price_uzs=base,
                pay_amount=pay_amount,
                status=ORDER_PENDING,
                expires_at=utcnow()
                + timedelta(seconds=app_settings.DEPOSIT_TTL_SECONDS),
                order_data=data,
            )
            session.add(order)
            await session.flush()
            logger.info(
                "Karta buyurtmasi yaratildi: id=%s user=%s product=%s "
                "price=%s pay=%s",
                order.id, user_id, product_id, base, pay_amount,
            )
            return order

    # ------------------------------------------------------------------
    @staticmethod
    async def set_order_payment_message(order_id: int, message_id: int) -> None:
        """To'lov xabari ID'sini saqlaydi (muddat tugaganda tahrirlash uchun)."""
        async with get_session() as session:
            order = await session.get(Order, order_id, with_for_update=True)
            if order is not None:
                order.payment_message_id = message_id
                await session.flush()

    # ------------------------------------------------------------------
    @staticmethod
    async def get_pending_card_orders(limit: int = 500) -> list[Order]:
        """Karta to'lovini kutayotgan buyurtmalar (summa moslashtirish uchun)."""
        async with get_session() as session:
            result = await session.execute(
                select(Order)
                .where(
                    Order.status == ORDER_PENDING,
                    Order.payment_method == PAYMENT_CARD,
                    Order.pay_amount.is_not(None),
                )
                .order_by(Order.created_at.asc())
                .limit(limit)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    @staticmethod
    async def pay_order_by_amount(
        pay_amount: int, comment: str | None = None
    ) -> Order | None:
        """
        Kelgan to'lov summasini faol buyurtmaga atomik moslaydi.

        Faqat muddati o'tmagan `pending` buyurtma `paid` bo'ladi.
        Returns: `Order` yoki `None` (mos buyurtma yo'q / allaqachon to'langan).
        """
        if pay_amount <= 0:
            return None

        async with get_session() as session:
            order = await session.scalar(
                select(Order)
                .where(
                    Order.status == ORDER_PENDING,
                    Order.payment_method == PAYMENT_CARD,
                    Order.pay_amount == pay_amount,
                    or_(
                        Order.expires_at.is_(None),
                        Order.expires_at > utcnow(),
                    ),
                )
                .order_by(Order.created_at.asc())
                .limit(1)
                .with_for_update()
            )
            if order is None:
                return None

            order.status = ORDER_PAID
            order.paid_at = utcnow()
            if comment:
                order.payment_comment = comment[:1000]
            await session.flush()
            logger.info(
                "Buyurtma to'landi (karta): id=%s user=%s pay=%s",
                order.id, order.user_id, pay_amount,
            )
            return order

    # ------------------------------------------------------------------
    @staticmethod
    async def cancel_order(order_id: int, user_id: int | None = None) -> Order | None:
        """
        `pending` buyurtmani bekor qiladi (ega tekshiriladi).

        Returns: yangilangan buyurtma yoki `None` (topilmadi yoki allaqachon
        to'langan/bekor qilingan).
        """
        async with get_session() as session:
            order = await session.get(Order, order_id, with_for_update=True)
            if order is None or order.status != ORDER_PENDING:
                return None
            if user_id is not None and order.user_id != user_id:
                return None
            order.status = ORDER_CANCELLED
            await session.flush()
            logger.info("Buyurtma bekor qilindi: id=%s", order_id)
            return order

    # ------------------------------------------------------------------
    @staticmethod
    async def get_expired_orders(limit: int = 50) -> list[Order]:
        """Muddati o'tgan, lekin hali `pending` karta buyurtmalari."""
        async with get_session() as session:
            result = await session.execute(
                select(Order)
                .where(
                    Order.status == ORDER_PENDING,
                    Order.payment_method == PAYMENT_CARD,
                    Order.expires_at.is_not(None),
                    Order.expires_at <= utcnow(),
                )
                .order_by(Order.expires_at.asc())
                .limit(limit)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    @staticmethod
    async def expire_order(order_id: int) -> Order | None:
        """`pending` buyurtmani `expired` qiladi (to'lanmagan)."""
        async with get_session() as session:
            order = await session.get(Order, order_id, with_for_update=True)
            if order is None or order.status != ORDER_PENDING:
                return None
            if order.expires_at is None:
                return None
            expires_at = order.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > utcnow():
                return None

            order.status = ORDER_EXPIRED
            await session.flush()
            logger.info("Buyurtma muddati tugadi: id=%s", order.id)
            return order

    # ------------------------------------------------------------------
    @staticmethod
    async def get_order(order_id: int) -> Order | None:
        """Buyurtmani ID bo'yicha oladi."""
        async with get_session() as session:
            return await session.get(Order, order_id)

    # ------------------------------------------------------------------
    @staticmethod
    async def get_orders(
        status: str | None = None, limit: int = 20
    ) -> list[Order]:
        """Buyurtmalar ro'yxati (admin panel uchun, oxirgilari birinchi)."""
        async with get_session() as session:
            stmt = select(Order)
            if status is not None:
                stmt = stmt.where(Order.status == status)
            result = await session.execute(
                stmt.order_by(Order.created_at.desc()).limit(limit)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    @staticmethod
    async def mark_order_fulfilled(order_id: int) -> Order:
        """Premium/NFT buyurtma admin tomonidan yetkazilganini belgilaydi."""
        async with get_session() as session:
            order = await session.get(Order, order_id, with_for_update=True)
            if order is None:
                raise RepositoryError(f"Buyurtma {order_id} topilmadi")
            order.fulfilled_at = utcnow()
            await session.flush()
            logger.info("Buyurtma yetkazildi: id=%s", order_id)
            return order

    # ------------------------------------------------------------------
    @staticmethod
    async def count_pending_orders() -> int:
        """Kutilayotgan (pending) kartali buyurtmalar soni."""
        async with get_session() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(Order)
                    .where(Order.status == ORDER_PENDING)
                )
                or 0
            )

    # ------------------------------------------------------------------
    @staticmethod
    async def get_revenue_stats() -> dict[str, int]:
        """To'langan buyurtmalar bo'yicha tushum (so'mda va Starsda)."""
        today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        async with get_session() as session:
            total_uzs = await session.scalar(
                select(func.coalesce(func.sum(Order.pay_amount), 0)).where(
                    Order.status == ORDER_PAID
                )
            )
            today_uzs = await session.scalar(
                select(func.coalesce(func.sum(Order.pay_amount), 0)).where(
                    Order.status == ORDER_PAID,
                    Order.paid_at.is_not(None),
                    Order.paid_at >= today_start,
                )
            )
            total_stars = await session.scalar(
                select(func.coalesce(func.sum(Order.amount_xtr), 0)).where(
                    Order.status == ORDER_PAID,
                    Order.payment_method == PAYMENT_CARD,
                )
            )
            return {
                "total_uzs": int(total_uzs or 0),
                "today_uzs": int(today_uzs or 0),
                "total_stars": int(total_stars or 0),
            }

    # ------------------------------------------------------------------
    @staticmethod
    async def complete_order(order_id: int, telegram_payment_charge_id: str) -> Order:
        """
        Rasmiy Telegram Stars (XTR) to'lovi tasdiqlangach buyurtmani
        `paid` qiladi va to'lov ID'sini saqlaydi.

        Idempotent: buyurtma allaqachon `paid` bo'lsa — qayta ishlamasdan
        O'SHA buyurtmani qaytaradi (Telegram webhook takroriy chaqiruvlari
        uchun xavfsiz). UNIQUE(telegram_payment_charge_id) ham dublikat
        to'lovni bloklaydi.

        Raises:
            RepositoryError: buyurtma topilmagan bo'lsa.
        """
        async with get_session() as session:
            order = await session.get(Order, order_id, with_for_update=True)
            if order is None:
                raise RepositoryError(f"Buyurtma {order_id} topilmadi")

            if order.status == ORDER_PAID:
                # allaqachon to'langan — idempotent qaytish
                return order

            order.status = ORDER_PAID
            order.telegram_payment_charge_id = telegram_payment_charge_id
            order.paid_at = utcnow()
            await session.flush()
            logger.info(
                "Buyurtma to'landi: id=%s charge_id=%s", order_id, telegram_payment_charge_id
            )
            return order

    # ------------------------------------------------------------------
    @staticmethod
    async def create_balance_order(user_id: int, product_id: int) -> Order:
        """
        Bot balansi orqali ATOMIK xarid (bitta tranzaksiya ichida):
          1. Foydalanuvchi qatori FOR UPDATE bilan qulflanadi.
          2. Mahsulot tekshiriladi (topilmagan/o'chirilgan -> RepositoryError).
          3. Balans yetarliligi tekshiriladi — yetmasa
             `InsufficientBalanceError` -> ROLLBACK (na buyurtma, na balans
             o'zgaradi — xavfsiz).
          4. Buyurtma yaratiladi va darhol `paid` qilinadi
             (telegram_payment_charge_id None — ichki to'lov).
          5. Balansdan ayriladi + transactions'ga `purchase` audit yoziladi.

        Parallel himoya: qator qulfi tufayli bir vaqtli ikki xariddan
        biri navbat kutadi va balans yetarliligini yangi qiymat bilan
        qayta tekshiradi (race yo'q).

        Raises:
            RepositoryError: foydalanuvchi yoki mahsulot topilmadi/o'chirilgan.
            InsufficientBalanceError: balans yetarli emas.
        """
        async with get_session() as session:
            user = await session.get(User, user_id, with_for_update=True)
            if user is None:
                raise RepositoryError(f"Foydalanuvchi {user_id} topilmadi")

            product = await session.get(Product, product_id, with_for_update=True)
            if product is None or not product.is_active:
                raise RepositoryError(
                    f"Mahsulot {product_id} topilmadi yoki o'chirilgan"
                )

            if user.stars_balance < product.price_xtr:
                raise InsufficientBalanceError(
                    f"Balans yetarli emas: joriy={user.stars_balance}, "
                    f"narx={product.price_xtr}"
                )

            order = Order(
                user_id=user_id,
                product_id=product_id,
                payment_method=PAYMENT_BALANCE,
                amount_xtr=product.price_xtr,
                status=ORDER_PAID,
                paid_at=utcnow(),
                order_data={
                    "product_title": product.title,
                    "category": product.category,
                },
            )
            session.add(order)
            await session.flush()  # order.id olish uchun

            user.stars_balance -= product.price_xtr
            session.add(
                TransactionRow(
                    user_id=user_id,
                    amount=-product.price_xtr,
                    tx_type=TX_PURCHASE,
                    description=f"Balansdan xarid: {product.title}",
                    reference_id=str(order.id),
                )
            )
            await session.flush()
            logger.info(
                "Balans orqali xarid: order=%s user=%s product=%s -%sXTR",
                order.id, user_id, product_id, product.price_xtr,
            )
            return order


# =============================================================
# 6. WITHDRAWAL REPOSITORY (Stars yechib olish)
# =============================================================
class WithdrawalRepository:
    """Yechib olish so'rovlari: hold -> approve / reject(+refund)."""

    # ------------------------------------------------------------------
    @staticmethod
    async def get_withdrawals(
        status: str | None = None, limit: int = 20
    ) -> list[Withdrawal]:
        """
        Yechib olish so'rovlarini ro'yxati (admin panel uchun).

        Args:
            status: 'pending' | 'approved' | 'rejected' — filtr (None = barchasi)
            limit: maksimal natija soni (oxirgilari birinchi)

        Returns: Withdrawal obyektlari — qo'shilgan vaqt bo'yicha kamayishda.
        """
        async with get_session() as session:
            stmt = select(Withdrawal)
            if status is not None:
                stmt = stmt.where(Withdrawal.status == status)
            result = await session.execute(
                stmt.order_by(Withdrawal.created_at.desc()).limit(limit)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    @staticmethod
    async def count_pending_withdrawals() -> int:
        """
        Kutilayotgan (status='pending') yechib olish so'rovlari soni —
        admin statistikasidagi "⏳ Kutilayotgan..." uchun.
        """
        async with get_session() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(Withdrawal)
                    .where(Withdrawal.status == WITHDRAWAL_PENDING)
                )
                or 0
            )

    # ------------------------------------------------------------------
    @staticmethod
    async def create_withdrawal(
        user_id: int,
        amount: int,
        destination_type: str,
        destination_address: str,
    ) -> Withdrawal:
        """
        Yangi so'rov yaratadi VA foydalanuvchi balansidan Starsni MUZLATADI
        (hold): balans -amount, withdrawal.status='pending'.

        Bitta tranzaksiyada (atomik):
          1. Foydalanuvchi qatori FOR UPDATE bilan qulflanadi.
          2. Balans yetarliligi tekshiriladi — yetmasa
             `InsufficientBalanceError` -> ROLLBACK (na so'rov, na
             balans o'zgaradi).
          3. Balans kamayadi + transactions'ga `withdraw_hold` audit yoziladi.
          4. withdrawals jadvaliga pending so'rov qo'shiladi.

        Raises:
            ValueError: amount <= 0.
            InsufficientBalanceError: balans yetarli emas.
            RepositoryError: foydalanuvchi topilmadi.
        """
        if amount <= 0:
            raise ValueError("Miqdor 0 dan katta bo'lishi kerak")

        async with get_session() as session:
            user = await session.get(User, user_id, with_for_update=True)
            if user is None:
                raise RepositoryError(f"Foydalanuvchi {user_id} topilmadi")

            if user.stars_balance < amount:
                raise InsufficientBalanceError(
                    f"Balans yetarli emas: joriy={user.stars_balance}, "
                    f"so'ralgan={amount}"
                )

            withdrawal = Withdrawal(
                user_id=user_id,
                amount=amount,
                destination_type=destination_type,
                destination_address=destination_address,
                status=WITHDRAWAL_PENDING,
            )
            session.add(withdrawal)
            await session.flush()  # withdrawal.id olish uchun

            user.stars_balance -= amount
            session.add(
                TransactionRow(
                    user_id=user_id,
                    amount=-amount,
                    tx_type=TX_WITHDRAW_HOLD,
                    description=(
                        f"Yechib olish uchun muzlatildi -> "
                        f"{destination_type}:{destination_address[:40]}"
                    ),
                    reference_id=str(withdrawal.id),
                )
            )
            await session.flush()
            logger.info(
                "Withdrawal yaratildi: id=%s user=%s amount=%s (hold)",
                withdrawal.id, user_id, amount,
            )
            return withdrawal

    # ------------------------------------------------------------------
    @staticmethod
    async def approve_withdrawal(withdrawal_id: int, admin_id: int) -> Withdrawal:
        """
        So'rovni tasdiqlash (`approved`).

        Balans o'zgarmaydi — Stars allaqachon `create_withdrawal` da
        muzlatilgan (hold). Faqat status va admin_id yangilanadi.

        Raises:
            RepositoryError: so'rov topilmadi.
            WithdrawalStateError: so'rov allaqachon ko'rib chiqilgan.
        """
        async with get_session() as session:
            withdrawal = await session.get(
                Withdrawal, withdrawal_id, with_for_update=True
            )
            if withdrawal is None:
                raise RepositoryError(f"Yechib olish {withdrawal_id} topilmadi")
            if withdrawal.status != WITHDRAWAL_PENDING:
                raise WithdrawalStateError(
                    f"So'rov {withdrawal_id} allaqachon "
                    f"'{withdrawal.status}' holatida"
                )

            withdrawal.status = WITHDRAWAL_APPROVED
            withdrawal.admin_id = admin_id
            withdrawal.processed_at = utcnow()
            await session.flush()
            logger.info(
                "Withdrawal tasdiqlandi: id=%s admin=%s", withdrawal_id, admin_id
            )
            return withdrawal

    # ------------------------------------------------------------------
    @staticmethod
    async def reject_withdrawal(
        withdrawal_id: int, admin_id: int, reason: str
    ) -> Withdrawal:
        """
        So'rovni rad etish (`rejected`) va muzlatilgan Starsni foydalanuvchiga
        qaytarish (`refund`).

        Bitta tranzaksiyada (atomik):
          1. So'rov FOR UPDATE bilan qulflanadi; pending emas -> 
             `WithdrawalStateError` (ikki marta rad etib bo'lmaydi).
          2. Foydalanuvchi qatori qulflanadi, balansga +amount qaytariladi.
          3. transactions'ga `refund` audit yozuvi qo'shiladi.
          4. So'rov statusi -> 'rejected' (admin_id, reject_reason, processed_at).
        """
        async with get_session() as session:
            withdrawal = await session.get(
                Withdrawal, withdrawal_id, with_for_update=True
            )
            if withdrawal is None:
                raise RepositoryError(f"Yechib olish {withdrawal_id} topilmadi")
            if withdrawal.status != WITHDRAWAL_PENDING:
                raise WithdrawalStateError(
                    f"So'rov {withdrawal_id} allaqachon "
                    f"'{withdrawal.status}' holatida"
                )

            user = await session.get(User, withdrawal.user_id, with_for_update=True)
            if user is None:
                raise RepositoryError(
                    f"Foydalanuvchi {withdrawal.user_id} topilmadi "
                    "(qaytarib bo'lmaydi)"
                )

            # Muzlatilgan Starsni qaytarish (+amount)
            user.stars_balance += withdrawal.amount
            session.add(
                TransactionRow(
                    user_id=user.user_id,
                    amount=withdrawal.amount,
                    tx_type=TX_REFUND,
                    description=f"Rad etildi: {reason}",
                    reference_id=str(withdrawal.id),
                )
            )

            withdrawal.status = WITHDRAWAL_REJECTED
            withdrawal.admin_id = admin_id
            withdrawal.reject_reason = reason
            withdrawal.processed_at = utcnow()
            await session.flush()
            logger.info(
                "Withdrawal rad etildi: id=%s admin=%s reason=%s",
                withdrawal_id, admin_id, reason,
            )
            return withdrawal


# =============================================================
# 7. DEPOSIT REPOSITORY (karta orqali balans to'ldirish)
# =============================================================
class DepositRepository:
    """
    Karta (UZCARD/HUMO) orqali balans to'ldirish so'rovlari.

    Oqim: create_deposit (pending) -> confirm_deposit (balans + audit) yoki
    reject_deposit (balans o'zgarmaydi). Tasdiqlash idempotent: pending
    bo'lmagan so'rov qayta tasdiqlanmaydi (`DepositStateError`).
    """

    # ------------------------------------------------------------------
    @staticmethod
    async def create_deposit(
        user_id: int,
        amount: int,
        receipt_file_id: str | None = None,
        pay_amount: int | None = None,
        expires_at: datetime | None = None,
        payment_message_id: int | None = None,
    ) -> DepositRequest:
        """
        Yangi `pending` to'ldirish so'rovini yaratadi (balans hali tegmaydi).

        Args:
            pay_amount: aynan to'lanadigan summa (so'm). Avtomatik to'lov
                shu summa bo'yicha topiladi; `None` bo'lsa faqat chek + admin.
            expires_at: so'rov amal qilish muddati. `None` — muddatsiz
                (Telegram Stars invoysi uchun).
            payment_message_id: foydalanuvchiga yuborilgan to'lov xabari
                ID'si (muddat tugaganda shu xabar yangilanadi).

        Raises:
            ValueError: amount <= 0.
            RepositoryError: foydalanuvchi topilmadi.
        """
        if amount <= 0:
            raise ValueError("Miqdor 0 dan katta bo'lishi kerak")

        async with get_session() as session:
            user = await session.get(User, user_id, with_for_update=True)
            if user is None:
                raise RepositoryError(f"Foydalanuvchi {user_id} topilmadi")

            deposit = DepositRequest(
                user_id=user_id,
                amount=amount,
                status=DEPOSIT_PENDING,
                receipt_file_id=receipt_file_id,
                pay_amount=pay_amount,
                expires_at=expires_at,
                payment_message_id=payment_message_id,
            )
            session.add(deposit)
            await session.flush()
            logger.info(
                "Deposit so'rovi yaratildi: id=%s user=%s amount=%s pay=%s",
                deposit.id, user_id, amount, pay_amount,
            )
            return deposit

    # ------------------------------------------------------------------
    @staticmethod
    async def create_deposit_with_unique_sum(
        user_id: int,
        amount: int,
        som_per_star: int,
        receipt_file_id: str | None = None,
    ) -> DepositRequest:
        """
        Unikal to'lov summasi bilan `pending` so'rov yaratadi.

        Summa = `amount * som_per_star` + 40..100 so'm "kod". Kod boshqa
        `pending` so'rovlar bilan takrorlanmasligi tekshiriladi (30 urinish).
        So'rov `DEPOSIT_TTL_SECONDS` (standart 5 daqiqa) muddatga ega —
        muddat o'tsa `expired` bo'ladi va kechikkan to'lov hisoblanmaydi.

        Raises:
            ValueError: amount <= 0 yoki som_per_star <= 0.
            RepositoryError: foydalanuvchi topilmadi / bo'sh kod topilmadi.
        """
        if amount <= 0:
            raise ValueError("Miqdor 0 dan katta bo'lishi kerak")
        if som_per_star <= 0:
            raise ValueError("som_per_star 0 dan katta bo'lishi kerak")

        base = amount * som_per_star

        async with get_session() as session:
            user = await session.get(User, user_id, with_for_update=True)
            if user is None:
                raise RepositoryError(f"Foydalanuvchi {user_id} topilmadi")

            pay_amount: int | None = None
            for _ in range(DEPOSIT_CODE_ATTEMPTS):
                code = DEPOSIT_CODE_MIN + secrets.randbelow(
                    DEPOSIT_CODE_MAX - DEPOSIT_CODE_MIN + 1
                )
                candidate = base + code
                taken = await session.scalar(
                    select(DepositRequest.id).where(
                        DepositRequest.status == DEPOSIT_PENDING,
                        DepositRequest.pay_amount == candidate,
                    )
                )
                if taken is None:
                    taken = await session.scalar(
                        select(Order.id).where(
                            Order.status == ORDER_PENDING,
                            Order.pay_amount == candidate,
                        )
                    )
                if taken is None:
                    pay_amount = candidate
                    break

            if pay_amount is None:
                raise RepositoryError(
                    "Unikal to'lov summasini tanlab bo'lmadi — keyinroq urinib ko'ring"
                )

            deposit = DepositRequest(
                user_id=user_id,
                amount=amount,
                status=DEPOSIT_PENDING,
                receipt_file_id=receipt_file_id,
                pay_amount=pay_amount,
                expires_at=utcnow() + timedelta(
                    seconds=app_settings.DEPOSIT_TTL_SECONDS
                ),
            )
            session.add(deposit)
            await session.flush()
            logger.info(
                "Deposit (unikal summa) yaratildi: id=%s user=%s amount=%s pay=%s "
                "expires_at=%s",
                deposit.id, user_id, amount, pay_amount, deposit.expires_at,
            )
            return deposit

    # ------------------------------------------------------------------
    @staticmethod
    async def attach_receipt(
        deposit_id: int, receipt_file_id: str
    ) -> DepositRequest | None:
        """
        Mavjud so'rovga chek `file_id`sini biriktiradi (holat o'zgarmaydi).

        Avtomatik tasdiqlangan so'rovga chek biriktirilmaydi — chaqiruvchi
        `deposit.status` ni tekshirib foydalanuvchiga xabar beradi.
        """
        async with get_session() as session:
            deposit = await session.get(
                DepositRequest, deposit_id, with_for_update=True
            )
            if deposit is None:
                return None
            if deposit.status == DEPOSIT_PENDING:
                deposit.receipt_file_id = receipt_file_id
                await session.flush()
            return deposit

    # ------------------------------------------------------------------
    @staticmethod
    async def auto_confirm_deposit(
        pay_amount: int,
        comment: str | None = None,
    ) -> tuple[DepositRequest, int] | None:
        """
        Jurnal chatidagi to'lovni summa bo'yicha ATOMIK tasdiqlaydi.

        Bitta tranzaksiyada `pending` so'rov qidiriladi (summa bo'yicha),
        balans +amount, `deposit` audit yozuvi, status -> approved,
        `admin_id=AUTO_DEPOSIT_ADMIN_ID`.

        Returns:
            (deposit, yangi_balans) — so'rov topildi va tasdiqlandi;
            None — bunday summa bilan `pending` so'rov yo'q (yoki allaqachon
            tasdiqlangan) — balans tegilmaydi.
        """
        if pay_amount <= 0:
            return None

        async with get_session() as session:
            deposit = await session.scalar(
                select(DepositRequest)
                .where(
                    DepositRequest.status == DEPOSIT_PENDING,
                    DepositRequest.pay_amount == pay_amount,
                    # Muddati o'tgan so'rovga kechikkan to'lov hisoblanmaydi
                    or_(
                        DepositRequest.expires_at.is_(None),
                        DepositRequest.expires_at > utcnow(),
                    ),
                )
                .order_by(DepositRequest.created_at.asc())
                .limit(1)
                .with_for_update()
            )
            if deposit is None:
                return None

            user = await session.get(User, deposit.user_id, with_for_update=True)
            if user is None:
                raise RepositoryError(
                    f"Foydalanuvchi {deposit.user_id} topilmadi (balans qo'shilmadi)"
                )

            # NULL bo'lsa ham xatosiz: (x or 0) + amount
            user.stars_balance = (user.stars_balance or 0) + deposit.amount
            user.total_earned_stars = (
                (user.total_earned_stars or 0) + deposit.amount
            )
            session.add(
                TransactionRow(
                    user_id=user.user_id,
                    amount=deposit.amount,
                    tx_type=TX_DEPOSIT,
                    description=(
                        "Karta orqali avtomatik to'ldirish "
                        f"(deposit #{deposit.id}, {pay_amount} so'm)"
                    ),
                    reference_id=str(deposit.id),
                )
            )

            deposit.status = DEPOSIT_APPROVED
            deposit.admin_id = AUTO_DEPOSIT_ADMIN_ID
            deposit.processed_at = utcnow()
            if comment:
                deposit.payment_comment = comment[:1000]
            await session.flush()

            logger.info(
                "Deposit avtomatik tasdiqlandi: id=%s user=%s amount=%s pay=%s -> %s",
                deposit.id, deposit.user_id, deposit.amount, pay_amount,
                user.stars_balance,
            )
            return deposit, user.stars_balance

    # ------------------------------------------------------------------
    @staticmethod
    async def set_payment_message(deposit_id: int, message_id: int) -> None:
        """
        Foydalanuvchiga yuborilgan to'lov xabari ID'sini saqlaydi.

        Muddat tugaganda aynan shu xabar "muddati o'tdi" matniga
        o'zgartiriladi (yangi xabar yuborilmaydi).
        """
        async with get_session() as session:
            deposit = await session.get(
                DepositRequest, deposit_id, with_for_update=True
            )
            if deposit is not None:
                deposit.payment_message_id = message_id
                await session.flush()

    # ------------------------------------------------------------------
    @staticmethod
    async def get_expired_deposits(limit: int = 50) -> list[DepositRequest]:
        """Muddati o'tgan, lekin hali `pending` bo'lgan so'rovlar."""
        async with get_session() as session:
            result = await session.execute(
                select(DepositRequest)
                .where(
                    DepositRequest.status == DEPOSIT_PENDING,
                    DepositRequest.expires_at.is_not(None),
                    DepositRequest.expires_at <= utcnow(),
                )
                .order_by(DepositRequest.expires_at.asc())
                .limit(limit)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    @staticmethod
    async def expire_deposit(deposit_id: int) -> DepositRequest | None:
        """
        `pending` so'rovni `expired` holatiga o'tkazadi (balans tegilmaydi).

        Returns:
            Yangilangan so'rov yoki `None` — so'rov allaqachon to'langan/rad
            etilgan yoki hali muddati tugamagan.
        """
        async with get_session() as session:
            deposit = await session.get(
                DepositRequest, deposit_id, with_for_update=True
            )
            if deposit is None or deposit.status != DEPOSIT_PENDING:
                return None
            if deposit.expires_at is None:
                return None
            expires_at = deposit.expires_at
            # SQLite naive datetime qaytarishi mumkin — solishtirishdan oldin
            # timezone'ni tenglashtiramiz.
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > utcnow():
                return None

            deposit.status = DEPOSIT_EXPIRED
            deposit.processed_at = utcnow()
            await session.flush()
            logger.info(
                "Deposit muddati tugadi: id=%s user=%s pay=%s",
                deposit.id, deposit.user_id, deposit.pay_amount,
            )
            return deposit

    # ------------------------------------------------------------------
    @staticmethod
    async def get_deposit(deposit_id: int) -> DepositRequest | None:
        """Bitta to'ldirish so'rovini ID bo'yicha oladi."""
        async with get_session() as session:
            return await session.get(DepositRequest, deposit_id)

    # ------------------------------------------------------------------
    @staticmethod
    async def get_deposits(
        status: str | None = None, limit: int = 20
    ) -> list[DepositRequest]:
        """So'rovlar ro'yxati (admin panel uchun, oxirgilari birinchi)."""
        # `expired` so'rovlar ham tarixda ko'rinadi (status bo'yicha filtr).
        async with get_session() as session:
            stmt = select(DepositRequest)
            if status is not None:
                stmt = stmt.where(DepositRequest.status == status)
            result = await session.execute(
                stmt.order_by(DepositRequest.created_at.desc()).limit(limit)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    @staticmethod
    async def count_pending_deposits() -> int:
        """Kutilayotgan (pending) to'ldirish so'rovlari soni."""
        async with get_session() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(DepositRequest)
                    .where(DepositRequest.status == DEPOSIT_PENDING)
                )
                or 0
            )

    # ------------------------------------------------------------------
    @staticmethod
    async def confirm_deposit(
        deposit_id: int, admin_id: int
    ) -> tuple[DepositRequest, int]:
        """
        To'ldirishni tasdiqlaydi va balansga ATOMIK qo'shadi.

        Admin qo'lda tasdiqlashi mumkin: `pending` yoki muddati o'tgan
        (`expired`) so'rov tasdiqlanadi — masalan, foydalanuvchi 5 daqiqadan
        keyin to'lov qilib, chek yuborsa. Avtomatik tasdiqlash esa faqat
        muddati o'tmagan `pending` so'rov bilan ishlaydi.

        Bitta tranzaksiyada:
          1. So'rov FOR UPDATE bilan qulflanadi; approved/rejected bo'lsa ->
             `DepositStateError` (ikki marta hisoblanmaydi).
          2. Foydalanuvchi qatori qulflanadi, balans +amount.
          3. `transactions`ga `deposit` audit yozuvi qo'shiladi.
          4. Status -> approved, admin_id/processed_at.

        Returns: (deposit, yangi_balans)
        """
        async with get_session() as session:
            deposit = await session.get(
                DepositRequest, deposit_id, with_for_update=True
            )
            if deposit is None:
                raise RepositoryError(f"Deposit {deposit_id} topilmadi")
            if deposit.status not in (DEPOSIT_PENDING, DEPOSIT_EXPIRED):
                raise DepositStateError(
                    f"So'rov {deposit_id} allaqachon '{deposit.status}' holatida"
                )

            user = await session.get(User, deposit.user_id, with_for_update=True)
            if user is None:
                raise RepositoryError(
                    f"Foydalanuvchi {deposit.user_id} topilmadi (balans qo'shilmadi)"
                )

            # NULL bo'lsa ham xatosiz: (x or 0) + amount
            user.stars_balance = (user.stars_balance or 0) + deposit.amount
            user.total_earned_stars = (
                (user.total_earned_stars or 0) + deposit.amount
            )
            session.add(
                TransactionRow(
                    user_id=user.user_id,
                    amount=deposit.amount,
                    tx_type=TX_DEPOSIT,
                    description=f"Karta orqali to'ldirish (deposit #{deposit.id})",
                    reference_id=str(deposit.id),
                )
            )

            deposit.status = DEPOSIT_APPROVED
            deposit.admin_id = admin_id
            deposit.processed_at = utcnow()
            await session.flush()

            logger.info(
                "Deposit tasdiqlandi: id=%s user=%s amount=%s admin=%s -> %s",
                deposit.id, deposit.user_id, deposit.amount, admin_id,
                user.stars_balance,
            )
            return deposit, user.stars_balance

    # ------------------------------------------------------------------
    @staticmethod
    async def reject_deposit(
        deposit_id: int, admin_id: int, reason: str
    ) -> DepositRequest:
        """
        To'ldirish so'rovini rad etadi (balans o'zgarmaydi).

        `expired` so'rovni ham rad etish mumkin (masalan, chek kelgan,
        lekin to'lov topilmagan holat).

        Raises:
            RepositoryError: so'rov topilmadi.
            DepositStateError: so'rov allaqachon ko'rib chiqilgan.
        """
        async with get_session() as session:
            deposit = await session.get(
                DepositRequest, deposit_id, with_for_update=True
            )
            if deposit is None:
                raise RepositoryError(f"Deposit {deposit_id} topilmadi")
            if deposit.status not in (DEPOSIT_PENDING, DEPOSIT_EXPIRED):
                raise DepositStateError(
                    f"So'rov {deposit_id} allaqachon '{deposit.status}' holatida"
                )

            deposit.status = DEPOSIT_REJECTED
            deposit.admin_id = admin_id
            deposit.reject_reason = reason
            deposit.processed_at = utcnow()
            await session.flush()

            logger.info(
                "Deposit rad etildi: id=%s user=%s admin=%s reason=%s",
                deposit.id, deposit.user_id, admin_id, reason,
            )
            return deposit


# =============================================================
# 8. SETTINGS REPOSITORY
# =============================================================
class SettingsRepository:
    """Tizim sozlamalari (kalit-qiymat) — admin paneldan o'zgartiriladi."""

    # ------------------------------------------------------------------
    @staticmethod
    async def get_setting(key: str, default: Any = None) -> Any:
        """
        Tizim sozlamasini o'qiydi (masalan: referral_reward_stars,
        min_withdrawal_stars).

        Qiymat TEXT'dan tabiiy tipga coerced qilinadi: "10" -> 10.

        Returns: qiymat yoki bazada kalit bo'lmasa `default`.
        """
        async with get_session() as session:
            return await _load_setting(session, key, default)

    # ------------------------------------------------------------------
    @staticmethod
    async def get_int_setting(key: str, default: int) -> int:
        """Butun son sozlamasini o'qiydi (noto'g'ri/NULL bo'lsa `default`)."""
        value = await SettingsRepository.get_setting(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    @staticmethod
    async def get_card_details() -> tuple[str, str]:
        """Karta raqami va egasini qaytaradi (baza -> .env default)."""
        number = str(
            await SettingsRepository.get_setting(
                SETTING_CARD_NUMBER, app_settings.CARD_NUMBER
            )
            or ""
        ).strip()
        holder = str(
            await SettingsRepository.get_setting(
                SETTING_CARD_HOLDER, app_settings.CARD_HOLDER
            )
            or ""
        ).strip()
        return number, holder

    # ------------------------------------------------------------------
    @staticmethod
    async def get_star_price() -> int:
        """
        1 ⭐️ ning so'mdagi narxi (`settings.star_price_uzs`).

        Bazada yo'q bo'lsa `.env` dagi `SOM_PER_STAR` (standart 300) ishlatiladi.
        """
        price = await SettingsRepository.get_int_setting(
            SETTING_STAR_PRICE, app_settings.SOM_PER_STAR
        )
        return price if price > 0 else app_settings.SOM_PER_STAR

    # ------------------------------------------------------------------
    @staticmethod
    async def get_card_log_chat_id() -> int:
        """
        Karta to'lovlari jurnali chat ID'si (`settings.card_log_chat_id`).

        `0` — sozlanmagan (bot baribir guruh/kanal xabarlarini o'qiydi).
        """
        return await SettingsRepository.get_int_setting(
            SETTING_CARD_LOG_CHAT, app_settings.CARD_LOG_CHAT_ID
        )

    # ------------------------------------------------------------------
    @staticmethod
    async def get_settings_by_prefix(prefix: str) -> dict[str, str]:
        """
        Berilgan prefiks bilan boshlanadigan barcha sozlamalar (dict).

        `btn:` prefiksli tugma override'larini bitta so'rovda yuklash uchun.
        """
        async with get_session() as session:
            result = await session.execute(
                select(Setting).where(Setting.key.startswith(prefix))
            )
            return {row.key: row.value for row in result.scalars().all()}

    # ------------------------------------------------------------------
    @staticmethod
    async def delete_setting(key: str) -> bool:
        """
        Sozlamani o'chiradi (tugma override'ini tiklash uchun).

        Returns: True — o'chirildi; False — bunday kalit yo'q edi.
        """
        async with get_session() as session:
            result = await session.execute(delete(Setting).where(Setting.key == key))
            await session.flush()
            return bool(result.rowcount)

    # ------------------------------------------------------------------
    @staticmethod
    async def update_setting(key: str, value: Any) -> Any:
        """
        Sozlamani yangilaydi YOKI yangi kalit yaratadi (upsert).

        Admin panelda referal narxi yoki minimal yechish miqdorini
        o'zgartirish uchun ishlatiladi.

        Returns: saqlangan qiymat (coerced holatda).
        """
        text_value = str(value)
        async with get_session() as session:
            row = await session.get(Setting, key, with_for_update=True)

            if row is None:
                row = Setting(key=key, value=text_value)
                session.add(row)
                try:
                    await session.flush()
                except IntegrityError:
                    # Parallel ravishda boshqasi yaratgan — qayta o'qib, yangilaymiz
                    await session.rollback()
                    row = await session.get(Setting, key, with_for_update=True)
                    if row is None:
                        raise RepositoryError(
                            f"Sozlama '{key}' yaratish konfliktidan keyin topilmadi"
                        ) from None
                    row.value = text_value
                    await session.flush()
            else:
                row.value = text_value
                await session.flush()

            logger.info("Sozlama yangilandi: %s = %s", key, text_value)
            return _coerce_setting(row.value, value)

    # ------------------------------------------------------------------
    @staticmethod
    async def get_order_log_chat_id() -> int:
        """
        Zakaz kanali chat ID'si (`settings.order_log_chat_id`).

        Barcha yangi xaridlar shu kanalga chek sifatida yuboriladi.
        `0` — sozlanmagan (yuborilmaydi).
        """
        return await SettingsRepository.get_int_setting(
            SETTING_ORDER_LOG_CHAT, 0
        )


# =============================================================
# 9. PROMOKOD REPOSITORY
# =============================================================
class PromoRepository:
    """Promokodlar: yaratish, ro'yxat, bir marta ishlatish (atomik)."""

    # ------------------------------------------------------------------
    @staticmethod
    async def create_code(
        code: str,
        stars: int,
        max_uses: int = 0,
        created_by: int | None = None,
    ) -> PromoCode:
        """
        Yangi promokod yaratadi (kod katta harflarda saqlanadi).

        Raises:
            PromoError: kod allaqachon mavjud.
            ValueError: noto'g'ri qiymat (stars <= 0, max_uses < 0, bo'sh kod).
        """
        normalized = (code or "").strip().upper()
        if not normalized or len(normalized) > 32:
            raise ValueError("Kod 1-32 belgi bo'lishi kerak")
        if int(stars) <= 0:
            raise ValueError("Stars miqdori 0 dan katta bo'lishi kerak")
        if int(max_uses) < 0:
            raise ValueError("Cheklov soni manfiy bo'lishi mumkin emas")

        async with get_session() as session:
            existing = await session.get(PromoCode, normalized)
            if existing is not None:
                raise PromoError(f"'{normalized}' kod allaqachon mavjud")

            promo = PromoCode(
                code=normalized,
                stars=int(stars),
                max_uses=int(max_uses),
                used_count=0,
                is_active=True,
                created_by=created_by,
            )
            session.add(promo)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                raise PromoError(
                    f"'{normalized}' kod allaqachon mavjud"
                ) from None

            logger.info(
                "Promokod yaratildi: %s (+%s⭐, limit=%s, admin=%s)",
                normalized, stars, max_uses or "∞", created_by,
            )
            return promo

    # ------------------------------------------------------------------
    @staticmethod
    async def get_code(code: str) -> PromoCode | None:
        """Kodni (katta harflarda) qaytaradi — topilmasa None."""
        normalized = (code or "").strip().upper()
        async with get_session() as session:
            return await session.get(PromoCode, normalized)

    # ------------------------------------------------------------------
    @staticmethod
    async def list_codes(limit: int = 20) -> list[PromoCode]:
        """Eng yangi promokodlar ro'yxati (admin uchun)."""
        async with get_session() as session:
            result = await session.execute(
                select(PromoCode)
                .order_by(PromoCode.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    @staticmethod
    async def redeem_code(code: str, user_id: int) -> int:
        """
        Promokodni bir marta ishlatish — ATOMIK (bitta tranzaksiya):
          1. Kod `FOR UPDATE` bilan qulflanadi.
          2. Topilmadi/ faol emas -> `PromoError`.
          3. Limit tugagan -> `PromoError`.
          4. Shu foydalanuvchi allaqachan ishlatgan -> `PromoError`.
          5. Balansga Stars qo'shiladi, `used_count` oshadi va
             `promo_redemptions` yozuvi yaratiladi (UNIQUE himoyasi).

        Returns: yangi balans.

        Raises:
            PromoError: kod topilmadi, faol emas, limit tugagan yoki
                foydalanuvchi allaqachan ishlatgan.
        """
        normalized = (code or "").strip().upper()

        async with get_session() as session:
            promo = await session.get(PromoCode, normalized, with_for_update=True)
            if promo is None or not promo.is_active:
                raise PromoError("Bunday promokod topilmadi ❌")

            if promo.max_uses > 0 and promo.used_count >= promo.max_uses:
                raise PromoError("Bu promokod limiti tugagan ⌛️")

            already = await session.execute(
                select(PromoRedemption.id).where(
                    PromoRedemption.code == normalized,
                    PromoRedemption.user_id == user_id,
                )
            )
            if already.scalar_one_or_none() is not None:
                raise PromoError("Siz bu promokoddan allaqachon foydalaningiz 🚫")

            user = await session.get(User, user_id, with_for_update=True)
            if user is None:
                raise PromoError("Profil topilmadi — /start bosing")

            user.stars_balance = (user.stars_balance or 0) + promo.stars
            user.total_earned_stars = (user.total_earned_stars or 0) + promo.stars
            promo.used_count = int(promo.used_count or 0) + 1

            session.add(
                TransactionRow(
                    user_id=user_id,
                    amount=promo.stars,
                    tx_type=TX_PROMO,
                    description=f"Promokod: {normalized}",
                    reference_id=normalized,
                )
            )
            session.add(
                PromoRedemption(
                    code=normalized,
                    user_id=user_id,
                    stars=promo.stars,
                )
            )
            await session.flush()

            logger.info(
                "Promokod ishlatildi: %s user=%s +%s⭐ (used=%s/%s)",
                normalized, user_id, promo.stars,
                promo.used_count, promo.max_uses or "∞",
            )
            return int(user.stars_balance)


# =============================================================
# 10. BLOK REPOSITORY (admin tomonidan bloklash/ochish)
# =============================================================
class BlockRepository:
    """Bloklangan foydalanuvchilar (`user_blocks` jadvali)."""

    # ------------------------------------------------------------------
    @staticmethod
    async def block_user(
        user_id: int, blocked_by: int | None = None, reason: str | None = None
    ) -> bool:
        """
        Foydalanuvchini bloklaydi (`user_blocks` + `users.is_active=False`).

        Returns: True — yangi blok; False — allaqachon bloklangan.
        """
        async with get_session() as session:
            existing = await session.get(UserBlock, user_id)
            if existing is not None:
                return False

            session.add(
                UserBlock(
                    user_id=user_id,
                    blocked_by=blocked_by,
                    reason=reason,
                )
            )
            user = await session.get(User, user_id)
            if user is not None:
                user.is_active = False
            await session.flush()
            logger.info("Foydalanuvchi bloklandi: %s (admin=%s)", user_id, blocked_by)
            return True

    # ------------------------------------------------------------------
    @staticmethod
    async def unblock_user(user_id: int) -> bool:
        """Blokni olib tashlaydi.

        Returns: True — blok olib tashlandi; False — bloklangan emas edi.
        """
        async with get_session() as session:
            result = await session.execute(
                delete(UserBlock).where(UserBlock.user_id == user_id)
            )
            await session.flush()
            removed = bool(result.rowcount)
            if removed:
                user = await session.get(User, user_id)
                if user is not None:
                    user.is_active = True
                logger.info("Foydalanuvchi bloki ochildi: %s", user_id)
            return removed

    # ------------------------------------------------------------------
    @staticmethod
    async def is_blocked(user_id: int) -> bool:
        """Foydalanuvchi bloklanganmi (tez tekshiruv)."""
        async with get_session() as session:
            return await session.get(UserBlock, user_id) is not None
