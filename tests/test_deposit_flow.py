"""
tests/test_deposit_flow.py
==========================
Karta to'lovi oqimining yangi talablarini tekshiradi:

 1. 1 ⭐️ = 300 so'm (default) va unikal kod 40..100 so'm.
 2. So'rov 5 daqiqalik `expires_at` bilan yaratiladi.
 3. Muddati o'tgan so'rov `expired` bo'ladi, xabar yangilanadi va
    kechikkan to'lov balansga qo'shilmaydi.
 4. `@humocardbot` xabaridan summa ajratish (Telethon userbot parseri).
 5. Userbot sozlanmagan bo'lsa ishga tushmaydi (bot ishlashda davom etadi).

Ishga tushirish:
    python -m pytest tests/test_deposit_flow.py -q
"""

import asyncio
import os
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest

os.environ.update(
    {
        "BOT_TOKEN": "123456789:AAHtesttesttesttesttesttesttesttesttest",
        "ADMIN_IDS": "111,222",
        "DATABASE_URL": "postgresql://user:pass@host:5432/postgres",
    }
)
sys.path.insert(0, ".")

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import database.connection as conn_mod  # noqa: E402

DB_PATH = os.path.abspath("./_test_deposit_flow.sqlite3")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}")
conn_mod._engine = engine
conn_mod._session_factory = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)

from config.settings import Settings, settings  # noqa: E402
from database.models import DepositRequest, utcnow  # noqa: E402
from database.queries import (  # noqa: E402
    DEPOSIT_CODE_MAX,
    DEPOSIT_CODE_MIN,
    DEPOSIT_EXPIRED,
    DEPOSIT_PENDING,
    DepositRepository,
    DepositStateError,
    UserRepository,
)
from database.schema import ensure_schema  # noqa: E402
from services.deposit_credit import credit_payment_text  # noqa: E402
from services.deposit_expiry import (  # noqa: E402
    EXPIRED_TEXT,
    process_expired_deposits,
)
from services.userbot import HumocardUserbot, parse_humocard_message  # noqa: E402

USER_ID = 91001

# Jadvallarni yaratish (SQLite test bazasi)
asyncio.run(ensure_schema())


class FakeBot:
    """Telegram Bot o'rnini bosuvchi (xabarlarni yozib oladi)."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.edits: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                **kwargs,
            }
        )


def test_star_price_and_code_range() -> None:
    """Default kurs 300 so'm, unikal kod esa 40..100 so'm."""
    assert Settings.model_fields["SOM_PER_STAR"].default == 300
    assert Settings.model_fields["DEPOSIT_TTL_SECONDS"].default == 300
    assert (DEPOSIT_CODE_MIN, DEPOSIT_CODE_MAX) == (40, 100)


def test_topup_presets_include_10_stars() -> None:
    """Karta usulida 10 ⭐️ tayyor tugmasi ham bor."""
    from keyboards.inline import TOPUP_PRESETS, get_topup_presets_kb

    assert TOPUP_PRESETS == (10, 50, 100, 250, 500, 1000)
    callbacks = [
        button.callback_data
        for row in get_topup_presets_kb().inline_keyboard
        for button in row
    ]
    assert "tamt_10" in callbacks, callbacks


def test_parse_humocard_message() -> None:
    """@humocardbot matnlaridan summa to'g'ri ajratiladi."""
    ok = parse_humocard_message
    assert ok("🎉 To'ldirish ➕ 30 047 UZS") == 30047
    assert ok("To'ldirish ➕ 30,047 UZS") == 30047
    assert ok("to'ldirish ➕ 1\u00a0000\u202f000 so'm") == 1_000_000
    assert ok("To'ldirish ➕ 30 047 UZS. Karta: 8600 1234 5678 9012") == 30047
    # "To'ldirish" so'zisiz xabar to'lov hisoblanmaydi
    assert ok("Balans: 30 047 UZS") is None
    # Noaniq (ikki xil summa) — xavfsiz tomonga, None
    assert ok("To'ldirish ➕ 30 047 UZS va 50 000 UZS") is None
    assert ok("") is None
    assert ok(None) is None


async def _prepare() -> None:
    await UserRepository.get_or_create_user(USER_ID, "ali", "Ali", None)


async def _set_past_expiry(deposit_id: int) -> None:
    async with conn_mod.get_session() as session:
        deposit = await session.get(DepositRequest, deposit_id)
        deposit.expires_at = utcnow() - timedelta(seconds=1)
        await session.flush()


async def _make_deposit(stars: int = 100) -> DepositRequest:
    return await DepositRepository.create_deposit_with_unique_sum(
        user_id=USER_ID, amount=stars, som_per_star=settings.SOM_PER_STAR
    )


def test_unique_sum_and_expiry_window() -> None:
    """Summa `stars*300 + 40..100`, muddat esa ~5 daqiqa."""
    asyncio.run(_prepare())

    async def _run():
        first = await _make_deposit(100)
        second = await _make_deposit(100)
        return first, second

    first, second = asyncio.run(_run())
    base = 100 * settings.SOM_PER_STAR

    assert first.status == DEPOSIT_PENDING
    assert 40 <= first.pay_amount - base <= 100, first.pay_amount
    assert 40 <= second.pay_amount - base <= 100, second.pay_amount
    assert first.pay_amount != second.pay_amount, "kod kolliziyasi"

    assert first.expires_at is not None
    expires_at = first.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=utcnow().tzinfo)
    delta = (expires_at - utcnow()).total_seconds()
    assert 250 <= delta <= 310, delta


def test_late_payment_is_not_credited() -> None:
    """Muddati o'tgan `pending` so'rovga kechikkan to'lov hisoblanmaydi."""
    asyncio.run(_prepare())

    async def _run():
        deposit = await _make_deposit(10)
        await _set_past_expiry(deposit.id)
        return deposit

    deposit = asyncio.run(_run())

    async def _confirm():
        return await DepositRepository.auto_confirm_deposit(deposit.pay_amount)

    assert asyncio.run(_confirm()) is None, "kechikkan to'lov qabul qilindi"


def test_expired_deposit_updates_message() -> None:
    """Expiry worker so'rovni yopadi va foydalanuvchi xabarini yangilaydi."""
    asyncio.run(_prepare())

    async def _run_prepare():
        deposit = await _make_deposit(50)
        await _set_past_expiry(deposit.id)
        await DepositRepository.set_payment_message(deposit.id, 777)
        return deposit

    deposit = asyncio.run(_run_prepare())
    bot = FakeBot()

    count = asyncio.run(process_expired_deposits(bot))
    assert count >= 1

    async def _reload():
        return await DepositRepository.get_deposit(deposit.id)

    expired = asyncio.run(_reload())
    assert expired is not None and expired.status == DEPOSIT_EXPIRED

    edit = next(e for e in bot.edits if e["message_id"] == 777)
    assert edit["chat_id"] == USER_ID
    assert "muddati tugadi" in edit["text"].lower()
    assert EXPIRED_TEXT.startswith("⏳")

    # Endi kechikkan to'lov ham, matn orqali ham hisoblanmaydi
    late_bot = FakeBot()
    credited = asyncio.run(
        credit_payment_text(
            f"To'ldirish ➕ {deposit.pay_amount} UZS", late_bot
        )
    )
    assert credited is None
    assert late_bot.messages == []


def test_admin_can_confirm_expired_with_receipt() -> None:
    """Kechikkan chekni admin qo'lda tasdiqlashi mumkin (avto emas)."""
    asyncio.run(_prepare())

    async def _run_prepare():
        deposit = await _make_deposit(25)
        await _set_past_expiry(deposit.id)
        return deposit

    deposit = asyncio.run(_run_prepare())
    asyncio.run(process_expired_deposits(FakeBot()))

    async def _confirm():
        return await DepositRepository.confirm_deposit(deposit.id, admin_id=111)

    confirmed, balance = asyncio.run(_confirm())
    assert confirmed.status == "approved"
    assert balance >= 25

    async def _again():
        return await DepositRepository.confirm_deposit(deposit.id, admin_id=111)

    with pytest.raises(DepositStateError):
        asyncio.run(_again())


def test_userbot_handler_credits_pending_deposit() -> None:
    """'@humocardbot' xabari kelganda balans avtomatik qo'shiladi."""
    asyncio.run(_prepare())
    deposit = asyncio.run(_make_deposit(15))
    pay_text = f"{deposit.pay_amount:,}".replace(",", " ")

    bot = FakeBot()
    userbot = HumocardUserbot(bot)
    event = SimpleNamespace(
        raw_text=f"🎉 To'ldirish ➕ {pay_text} UZS"
    )
    asyncio.run(userbot._handle_payment_message(event))

    async def _reload():
        return (
            await DepositRepository.get_deposit(deposit.id),
            await UserRepository.get_user(USER_ID),
        )

    updated, user = asyncio.run(_reload())
    assert updated.status == "approved"
    assert user.stars_balance >= 15
    assert any("+15" in m["text"] for m in bot.messages)

    # Takroriy xabar balansni oshirmaydi
    balance_after = user.stars_balance
    asyncio.run(userbot._handle_payment_message(event))
    _updated, user = asyncio.run(_reload())
    assert user.stars_balance == balance_after


def test_userbot_skips_when_not_configured(monkeypatch) -> None:
    """Env'da session bo'lmasa userbot ishga tushmaydi (startup buzilmaydi)."""
    monkeypatch.setattr(settings, "TELEGRAM_API_ID", 0)
    monkeypatch.setattr(settings, "TELEGRAM_API_HASH", "")
    monkeypatch.setattr(settings, "TELEGRAM_SESSION_STRING", "")

    userbot = HumocardUserbot(FakeBot())
    assert userbot.configured is False
    assert asyncio.run(userbot.start()) is False
    assert userbot.running is False
    asyncio.run(userbot.stop())  # xatosiz

    # Sozlangan bo'lsa ham `running` faqat ulangandan keyin True
    monkeypatch.setattr(settings, "TELEGRAM_API_ID", 12345)
    monkeypatch.setattr(settings, "TELEGRAM_API_HASH", "hash")
    monkeypatch.setattr(settings, "TELEGRAM_SESSION_STRING", "session")
    assert userbot.configured is True
