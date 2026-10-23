"""
tests/test_uzs_orders.py
========================
NFT YUT — SO'MDA ishlaydigan do'kon oqimi testlari:

 1. UZS katalog seed: Premium 3/6/12, Stars, Giftlar, NFT va HUMO karta.
 2. Karta buyurtmasi: narx + 40..100 so'm unikal summa, 5 daqiqalik taymer.
 3. To'lov tasdiqlangach mahsulot yetkazish:
      stars   — balansga qo'shiladi;
      premium — @username adminga [Fragment] tugmasi bilan;
      gift    — sendGift avtomatik;
      nft     — TON hamyon adminga.
 4. Muddat tugashi va kechikkan to'lov hisoblanmasligi.
 5. Admin statistikasi (so'mda tushum, sotilgan Stars).

Ishga tushirish:
    python -m pytest tests/test_uzs_orders.py -q
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

DB_PATH = os.path.abspath("./_test_uzs_orders.sqlite3")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}")
conn_mod._engine = engine
conn_mod._session_factory = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)

from config.settings import settings  # noqa: E402
from database.models import Order, utcnow  # noqa: E402
from database.queries import (  # noqa: E402
    ORDER_EXPIRED,
    ORDER_PAID,
    ORDER_PENDING,
    OrderRepository,
    ProductRepository,
    SettingsRepository,
    UserRepository,
)
from database.schema import ensure_schema  # noqa: E402
from database.seed import seed_default_products  # noqa: E402
from services.card_payment import format_som  # noqa: E402
from services.order_expiry import process_expired_orders  # noqa: E402
from services.order_payments import CB_ORDER_DONE_PREFIX, credit_order_text  # noqa: E402

USER_ID = 92001


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, dict]] = []
        self.edits: list[dict] = []
        self.gifts: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=len(self.messages) + 100, chat=SimpleNamespace(id=chat_id))

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text, **kwargs})
        return True

    async def get_available_gifts(self):
        return SimpleNamespace(
            gifts=[
                SimpleNamespace(id="gift15", star_count=15),
                SimpleNamespace(id="gift25", star_count=25),
            ]
        )

    async def send_gift(self, **kwargs):
        self.gifts.append(kwargs)
        return True


def _admin_messages(bot: FakeBot) -> list[tuple[int, str, dict]]:
    return [m for m in bot.messages if m[0] in settings.ADMIN_IDS]


async def _setup() -> None:
    await ensure_schema()
    await seed_default_products()
    await UserRepository.get_or_create_user(USER_ID, "buyer", "Buyer", None)


async def _product(category: str, title: str):
    products = await ProductRepository.get_products_by_category(category)
    return next(product for product in products if product.title == title)


def _pay_text(amount: int | None) -> str:
    return f"🎉 To'ldirish ➕ {format_som(amount or 0)} UZS 💳 HUMOCARD *3573"


def test_uzs_catalog_and_card_settings() -> None:
    """Seed katalogi to'g'ri narxlarda va karta defaultlari mavjud."""
    asyncio.run(_setup())

    async def _catalog():
        stars = await ProductRepository.get_products_by_category("stars")
        premium = await ProductRepository.get_products_by_category("premium")
        gifts = await ProductRepository.get_products_by_category("gift")
        nft = await ProductRepository.get_products_by_category("nft")
        card = await SettingsRepository.get_card_details()
        rate = await SettingsRepository.get_star_price()
        return stars, premium, gifts, nft, card, rate

    stars, premium, gifts, nft, card, rate = asyncio.run(_catalog())

    assert {p.title for p in stars} == {
        "50 Stars", "100 Stars", "250 Stars", "500 Stars", "1000 Stars"
    }
    assert {p.price_uzs for p in stars} == {15_000, 30_000, 75_000, 150_000, 300_000}
    assert {p.title: p.price_uzs for p in premium} == {
        "Premium 3 oy": 135_000,
        "Premium 6 oy": 195_000,
        "Premium 12 oy": 330_000,
    }
    assert gifts and min(p.price_uzs for p in gifts) == 4_500
    assert max(p.price_uzs for p in gifts) == 30_000
    assert nft and {p.price_uzs for p in nft} == {150_000, 300_000, 500_000}

    assert card == ("9860170111243573", "Murodjon Amirov")
    assert rate == 300


def test_stars_order_auto_delivery() -> None:
    """Stars to'plami to'lovi balansga avtomatik qo'shiladi (ikki marta emas)."""
    asyncio.run(_setup())
    bot = FakeBot()

    async def _run():
        product = await _product("stars", "50 Stars")
        order = await OrderRepository.create_card_order(USER_ID, product.id)
        return product, order

    product, order = asyncio.run(_run())

    assert order.status == ORDER_PENDING
    assert order.price_uzs == 15_000
    assert 15_040 <= (order.pay_amount or 0) <= 15_100
    assert order.expires_at is not None

    credited = asyncio.run(credit_order_text(_pay_text(order.pay_amount), bot))
    assert credited is not None and credited.status == ORDER_PAID

    async def _state():
        return (
            await OrderRepository.get_order(order.id),
            await UserRepository.get_user(USER_ID),
        )

    paid_order, user = asyncio.run(_state())
    assert paid_order.status == ORDER_PAID and paid_order.paid_at is not None
    assert user.stars_balance == 50

    # Takroriy xabar — balans oshmaydi
    assert asyncio.run(credit_order_text(_pay_text(order.pay_amount), bot)) is None
    _paid, user = asyncio.run(_state())
    assert user.stars_balance == 50

    user_msgs = [m[1] for m in bot.messages if m[0] == USER_ID]
    assert any("To'lov qabul qilindi" in text for text in user_msgs)
    assert any("50 Stars" in text for text in user_msgs)
    assert len(_admin_messages(bot)) == 2


def test_premium_order_admin_fragment_delivery() -> None:
    """Premium: @username adminga Fragment tugmasi bilan boradi."""
    asyncio.run(_setup())
    bot = FakeBot()

    async def _run():
        product = await _product("premium", "Premium 3 oy")
        return await OrderRepository.create_card_order(
            USER_ID, product.id, {"target_username": "ali_uz"}
        )

    order = asyncio.run(_run())
    asyncio.run(credit_order_text(_pay_text(order.pay_amount), bot))

    admins = _admin_messages(bot)
    assert len(admins) == 2
    _chat, text, kwargs = admins[0]
    assert "@ali_uz" in text and "Fragment" in text
    markup = kwargs.get("reply_markup")
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]
    assert f"{CB_ORDER_DONE_PREFIX}{order.id}" in callbacks

    updated = asyncio.run(
        OrderRepository.mark_order_fulfilled(order.id)
    )
    assert updated.fulfilled_at is not None


def test_gift_order_auto_send() -> None:
    """Gift: sendGift orqali avtomatik yuboriladi."""
    asyncio.run(_setup())
    bot = FakeBot()

    async def _run():
        product = await _product("gift", "Gift 15 ⭐️")
        return product, await OrderRepository.create_card_order(USER_ID, product.id)

    product, order = asyncio.run(_run())
    asyncio.run(credit_order_text(_pay_text(order.pay_amount), bot))

    assert bot.gifts and bot.gifts[0]["gift_id"] == "gift15"
    user_msgs = [m[1] for m in bot.messages if m[0] == USER_ID]
    assert any("avtomatik yuborildi" in text for text in user_msgs)


def test_nft_order_requires_wallet_and_notifies_admin() -> None:
    """NFT: TON hamyon adminga yuboriladi."""
    asyncio.run(_setup())
    bot = FakeBot()
    wallet = "UQ" + "0" * 46

    async def _run():
        product = await _product("nft", "NFT Bronze")
        return await OrderRepository.create_card_order(
            USER_ID, product.id, {"wallet": wallet}
        )

    order = asyncio.run(_run())
    asyncio.run(credit_order_text(_pay_text(order.pay_amount), bot))

    admins = _admin_messages(bot)
    assert admins and wallet in admins[0][1]
    callbacks = [
        button.callback_data
        for row in admins[0][2]["reply_markup"].inline_keyboard
        for button in row
    ]
    assert f"{CB_ORDER_DONE_PREFIX}{order.id}" in callbacks


def test_order_expiry_and_late_payment() -> None:
    """Muddat tugagach buyurtma expired bo'ladi va kechikkan to'lov o'tmaydi."""
    asyncio.run(_setup())
    bot = FakeBot()

    async def _prepare():
        product = await _product("nft", "NFT Silver")
        order = await OrderRepository.create_card_order(USER_ID, product.id)
        await OrderRepository.set_order_payment_message(order.id, 777)
        async with conn_mod.get_session() as session:
            row = await session.get(Order, order.id)
            row.expires_at = utcnow() - timedelta(seconds=1)
            await session.flush()
        return order

    order = asyncio.run(_prepare())
    count = asyncio.run(process_expired_orders(bot))
    assert count >= 1

    expired = asyncio.run(OrderRepository.get_order(order.id))
    assert expired.status == ORDER_EXPIRED

    edit = next(e for e in bot.edits if e["message_id"] == 777)
    assert "muddati tugadi" in edit["text"].lower()

    assert asyncio.run(credit_order_text(_pay_text(order.pay_amount), bot)) is None


class FakeState:
    def __init__(self) -> None:
        self.state = None
        self.data: dict = {}

    async def set_state(self, state) -> None:
        self.state = state

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self.data)

    async def clear(self) -> None:
        self.state = None
        self.data = {}


class FakeUserMessage:
    def __init__(self, text: str | None = None, user_id: int = USER_ID) -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=user_id, username="buyer", first_name="Buyer")
        self.chat = SimpleNamespace(id=user_id)
        self.answers: list[dict] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append({"text": text, **kwargs})
        return SimpleNamespace(message_id=900 + len(self.answers))


class FakeCallback:
    def __init__(self, data: str, message: FakeUserMessage, user_id: int = USER_ID) -> None:
        self.data = data
        self.message = message
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[dict] = []

    async def answer(self, text: str | None = None, show_alert: bool = False, **kwargs):
        self.answers.append({"text": text, "show_alert": show_alert})


def test_shop_checkout_handlers() -> None:
    """Do'kon handlerlari: stars darhol, premium @username so'raydi."""
    import handlers.user.shop as shop
    from database.queries import ORDER_PENDING as PENDING

    asyncio.run(_setup())

    async def _ids():
        stars = await _product("stars", "100 Stars")
        premium = await _product("premium", "Premium 12 oy")
        return stars, premium

    stars, premium = asyncio.run(_ids())

    # Stars: to'g'ridan-to'g'ri karta to'lovi xabari
    state = FakeState()
    message = FakeUserMessage()
    cb = FakeCallback(f"ord_prod_{stars.id}", message)
    asyncio.run(shop.order_product(cb, state))

    assert message.answers, "to'lov xabari yuborilmadi"
    text = message.answers[-1]["text"]
    assert "30 0" in text and "9860" in text and "5 daqiqa" in text

    async def _pending_orders():
        return await OrderRepository.get_pending_card_orders()

    pending = asyncio.run(_pending_orders())
    order = next(o for o in pending if o.product_id == stars.id)
    assert order.status == PENDING
    assert order.pay_amount and 30_040 <= order.pay_amount <= 30_100
    assert order.payment_message_id is not None

    # Premium: @username so'raladi, keyin buyurtma yaratiladi
    state2 = FakeState()
    msg2 = FakeUserMessage()
    cb2 = FakeCallback(f"ord_prod_{premium.id}", msg2)
    asyncio.run(shop.order_product(cb2, state2))
    assert state2.state is not None
    assert "username" in msg2.answers[-1]["text"].lower()

    username_msg = FakeUserMessage("@ali_uz")
    asyncio.run(shop.order_username(username_msg, state2))

    async def _premium_order():
        orders = await OrderRepository.get_pending_card_orders()
        return next(o for o in orders if o.product_id == premium.id)

    premium_order = asyncio.run(_premium_order())
    assert premium_order.order_data.get("target_username") == "ali_uz"
    assert premium_order.price_uzs == 330_000


def test_revenue_stats() -> None:
    """Admin statistikasi so'mdagi tushumni ko'rsatadi."""
    asyncio.run(_setup())

    async def _stats():
        return await OrderRepository.get_revenue_stats()

    stats = asyncio.run(_stats())
    assert stats["total_uzs"] >= 15_000
    assert stats["total_stars"] >= 50


def test_expired_orders_require_unexpired_payment_window() -> None:
    """`pay_order_by_amount` muddati o'tgan pending buyurtmani qabul qilmaydi."""
    async def _run():
        product = await _product("premium", "Premium 6 oy")
        order = await OrderRepository.create_card_order(USER_ID, product.id)
        async with conn_mod.get_session() as session:
            row = await session.get(Order, order.id)
            row.expires_at = utcnow() - timedelta(seconds=5)
            await session.flush()
        return order

    order = asyncio.run(_run())
    assert asyncio.run(
        OrderRepository.pay_order_by_amount(order.pay_amount or 0)
    ) is None
