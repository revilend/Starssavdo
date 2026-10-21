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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Final, NamedTuple

from sqlalchemy import and_, case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings as app_settings
from database.connection import get_session
from database.models import (
    AntiFraudLog,
    Channel,
    Order,
    Product,
    Referral,
    Setting,
    Transaction as TransactionRow,
    User,
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

# Referal statuslari
REFERRAL_PENDING: Final[str] = "pending"
REFERRAL_COMPLETED: Final[str] = "completed"
REFERRAL_REJECTED: Final[str] = "rejected"

# Buyurtma statuslari
ORDER_PENDING: Final[str] = "pending"
ORDER_PAID: Final[str] = "paid"
ORDER_CANCELLED: Final[str] = "cancelled"

# To'lov usullari
PAYMENT_XTR: Final[str] = "xtr"          # Telegram Stars invoice
PAYMENT_BALANCE: Final[str] = "balance"  # ichki balansdan

# Yechib olish statuslari
WITHDRAWAL_PENDING: Final[str] = "pending"
WITHDRAWAL_APPROVED: Final[str] = "approved"
WITHDRAWAL_REJECTED: Final[str] = "rejected"

# Tizim sozlamalari kalitlari
SETTING_REFERRAL_REWARD: Final[str] = "referral_reward_stars"
SETTING_MIN_WITHDRAWAL: Final[str] = "min_withdrawal_stars"

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


# =============================================================
# 6. WITHDRAWAL REPOSITORY (Stars yechib olish)
# =============================================================
class WithdrawalRepository:
    """Yechib olish so'rovlari: hold -> approve / reject(+refund)."""

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
# 7. SETTINGS REPOSITORY
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
