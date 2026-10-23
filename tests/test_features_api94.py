"""
tests/test_features_api94.py
============================
Yangi imkoniyatlar testi:

 1. Premium emoji yuklovchi (`services/emoji_loader.py`) — to'plamlardan
    `custom_emoji_id` mapping, yetishmayotgan to'plamga chidamlilik.
 2. Tugma reyestri (`services/button_registry.py`) — default matn/rang,
    admin override'lari, dinamik `ButtonText` filtri, ikonka ustuvorligi.
 3. `services/button_store.py` — settings jadvaliga yozish/o'qish/tiklash.
 4. Karta orqali to'ldirish — deposit repository (atomik tasdiqlash,
    idempotentlik, rad etish), user FSM oqimi va admin qarorlari.
 5. `services/gift_delivery.py` + do'kon integratsiyasi (`sendGift`).
 6. Admin tugma muharriri — `custom_emoji` entity'sidan ID olish.

Ishga tushirish:
    python -m pytest tests/test_features_api94.py -q
    yoki: python tests/test_features_api94.py
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.update(
    {
        "BOT_TOKEN": "123456789:AAHtesttesttesttesttesttesttesttesttest",
        "ADMIN_IDS": "111,222",
        "DATABASE_URL": "postgresql://user:pass@host:5432/postgres",
    }
)
sys.path.insert(0, ".")

from sqlalchemy import inspect, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import database.connection as conn_mod  # noqa: E402

DB_PATH = os.path.abspath("./_test_features.sqlite3")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}")
conn_mod._engine = engine
conn_mod._session_factory = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)

from aiogram.exceptions import TelegramAPIError  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402

import handlers.admin.buttons as admin_buttons  # noqa: E402
import handlers.admin.deposits as admin_deposits  # noqa: E402
import handlers.user.card_auto as card_auto  # noqa: E402
import handlers.user.shop as shop  # noqa: E402
import handlers.user.topup as topup  # noqa: E402
from database.models import Base, Transaction  # noqa: E402
from config.settings import settings  # noqa: E402
from database.queries import (  # noqa: E402
    AUTO_DEPOSIT_ADMIN_ID,
    DEPOSIT_APPROVED,
    DEPOSIT_PENDING,
    DEPOSIT_REJECTED,
    SETTING_CARD_HOLDER,
    SETTING_CARD_NUMBER,
    DepositRepository,
    DepositStateError,
    SettingsRepository,
    UserRepository,
)
from database.schema import ensure_schema  # noqa: E402
from services import button_registry as registry  # noqa: E402
from services.button_store import (  # noqa: E402
    load_button_overrides,
    reset_button,
    save_button_icon,
    save_button_text,
)
from services.emoji_loader import (  # noqa: E402
    clear_emoji_cache,
    find_custom_emoji_id,
    get_custom_emoji_id,
    load_custom_emojis,
)
from services.card_payment import (  # noqa: E402
    extract_amounts,
    find_payment_amount,
    format_som,
)
from services.gift_delivery import deliver_gift, extract_gift_id  # noqa: E402

ADMIN_USER_ID = 111
USER_ID = 71001

PASSED = 0


def ok(label: str, cond: bool, extra: str = "") -> None:
    global PASSED
    if not cond:
        raise AssertionError(f"FAILED: {label} {extra}")
    PASSED += 1
    print(f"  ok: {label}")


# =============================================================
# Fake Telegram obyektlari
# =============================================================
class FakeBot:
    def __init__(self, gift_error: Exception | None = None):
        self.messages: list[tuple[int, str, dict]] = []
        self.photos: list[tuple[int, str, dict]] = []
        self.gifts: list[dict] = []
        self._gift_error = gift_error

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id))

    async def send_photo(self, chat_id, photo, **kwargs):
        self.photos.append((chat_id, photo, kwargs))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id))

    async def send_gift(self, **kwargs):
        if self._gift_error is not None:
            raise self._gift_error
        self.gifts.append(kwargs)
        return True


class FakeMessage:
    def __init__(
        self,
        user_id: int = USER_ID,
        bot: FakeBot | None = None,
        text: str | None = None,
        photo: list | None = None,
        entities: list | None = None,
        document: object | None = None,
    ):
        self.from_user = SimpleNamespace(
            id=user_id, is_bot=False, first_name="Ali", username=None
        )
        self.chat = SimpleNamespace(id=user_id)
        self.bot = bot or FakeBot()
        self.text = text
        self.photo = photo
        self.entities = entities
        self.document = document
        self.answers: list[tuple[str, dict]] = []
        self.edits: list[dict] = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_caption(self, **kwargs):
        self.edits.append(kwargs)

    async def edit_text(self, text, **kwargs):
        self.edits.append({"text": text, **kwargs})


class FakeCallback:
    def __init__(self, user_id: int, data: str, bot: FakeBot):
        self.from_user = SimpleNamespace(id=user_id, is_bot=False, first_name="Adm")
        self.data = data
        self.bot = bot
        self.message = FakeMessage(user_id=user_id, bot=bot)
        self.answers: list[dict] = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append({"text": text, "show_alert": show_alert})


class FakeState:
    def __init__(self):
        self.state = None
        self.data: dict = {}

    async def set_state(self, state):
        self.state = state

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.state = None
        self.data = {}


class FakeStickerBot:
    def __init__(self, sets: dict[str, list[tuple[str, str]]]):
        self.sets = sets
        self.calls: list[str] = []

    async def get_sticker_set(self, name):
        self.calls.append(name)
        if name not in self.sets:
            raise RuntimeError(f"sticker set {name!r} topilmadi")
        return SimpleNamespace(
            stickers=[
                SimpleNamespace(emoji=emoji, custom_emoji_id=emoji_id)
                for emoji, emoji_id in self.sets[name]
            ]
        )


# =============================================================
# 0. Baza tayyorlash
# =============================================================
async def _setup_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


asyncio.run(_setup_db())


# =============================================================
# 1. Premium emoji yuklovchi
# =============================================================
def test_emoji_loader() -> None:
    print("== 1. emoji_loader ==")
    clear_emoji_cache()

    fake_bot = FakeStickerBot(
        {
            "SystemicDuck": [("🛍", "duck-shop"), ("👥", "duck-refer")],
            "statuspack_uz": [("✅", "status-check")],
            # business_emojis ataylab yo'q — xato bo'lmasligi kerak
        }
    )

    counts = asyncio.run(load_custom_emojis(fake_bot))
    ok("SystemicDuck yuklandi", counts.get("duck") == 2, str(counts))
    ok("statuspack_uz yuklandi", counts.get("status") == 1)
    ok("yetishmayotgan to'plam 0", counts.get("payment") == 0)
    ok("to'plamlar so'raldi", "CastilosDuck" in fake_bot.calls and "business_emojis" in fake_bot.calls)

    ok("get_custom_emoji_id", get_custom_emoji_id("status", "✅") == "status-check")
    ok("find_custom_emoji_id bir nechta to'plamda",
       find_custom_emoji_id("👥", ("duck", "duck_alt")) == "duck-refer")
    ok("topilmagan emoji -> None", get_custom_emoji_id("status", "🎁") is None)

    # Ikkinchi chaqiruv faqat yuklanmagan to'plamni qayta so'raydi
    calls_before = len(fake_bot.calls)
    asyncio.run(load_custom_emojis(fake_bot))
    ok("yuklangan to'plamlar qayta so'ralmadi",
       fake_bot.calls[calls_before:] == ["CastilosDuck", "business_emojis"],
       str(fake_bot.calls[calls_before:]))

    clear_emoji_cache()


# =============================================================
# 2. Tugma reyestri
# =============================================================
def test_button_registry() -> None:
    print("== 2. button_registry ==")
    registry.clear_overrides()

    ok("default matn", registry.get_text("menu_shop") == "🛒 Do'kon")
    ok("rangi primary", registry.get_style("menu_shop") == "primary")
    ok("check success", registry.get_style("check_subscription") == "success")
    ok("admin exit danger", registry.get_style("adm_exit") == "danger")
    ok("noma'lum kalit yo'q", not registry.is_known("yoq_bunday"))

    registry.apply_overrides(
        {
            "btn:menu_shop:text": "🛍 Bozor",
            "btn:menu_shop:icon": "999888",
            "btn:noma_lum:text": "x",
        }
    )
    ok("override matn", registry.get_text("menu_shop") == "🛍 Bozor")
    ok("override ikonka", registry.get_icon("menu_shop") == "999888")
    ok("noto'g'ri kalit e'tiborsiz", not registry.is_known("noma_lum"))

    import asyncio as _asyncio

    async def _filter_case():
        match = await registry.ButtonText("menu_shop")(SimpleNamespace(text="🛍 Bozor"))
        old = await registry.ButtonText("menu_shop")(SimpleNamespace(text="🛍 Do'kon"))
        return match, old

    match, old = _asyncio.run(_filter_case())
    ok("ButtonText yangi nomni taniydi", match is True)
    ok("ButtonText eski nomni tanimaydi", old is False)

    kb = registry.build_reply_button("menu_shop")
    ok("reply tugmada rang + ikonka",
       kb.style == "primary" and kb.icon_custom_emoji_id == "999888")

    registry.clear_overrides()
    ok("override tozalandi", registry.get_text("menu_shop") == "🛒 Do'kon")


# =============================================================
# 3. Tugma override'larini DB'da saqlash
# =============================================================
def test_button_store() -> None:
    print("== 3. button_store ==")
    registry.clear_overrides()

    async def _case() -> None:
        await save_button_text("menu_shop", "  🛍   Bozor  ")
        ok("matn saqlandi (bo'shliqlar tozalandi)",
           registry.get_text("menu_shop") == "🛍 Bozor")

        rows = await SettingsRepository.get_settings_by_prefix("btn:menu_shop")
        ok("DB'da text kaliti bor",
           rows.get("btn:menu_shop:text") == "🛍 Bozor", str(rows))

        await save_button_icon("menu_shop", "1234567890")
        ok("ikonka saqlandi", registry.get_icon("menu_shop") == "1234567890")

        try:
            await save_button_icon("menu_shop", "not-a-number")
            ok("noto'g'ri ikonka rad etilishi kerak edi", False)
        except ValueError:
            ok("noto'g'ri ikonka rad etildi", True)

        registry.clear_overrides()
        ok("kesh tozalandi (DB hali saqlanadi)",
           registry.get_text("menu_shop") == "🛒 Do'kon")

        await load_button_overrides()
        ok("DB'dan qayta yuklandi", registry.get_text("menu_shop") == "🛍 Bozor")

        await reset_button("menu_shop")
        ok("reset default holatga qaytardi", registry.get_text("menu_shop") == "🛒 Do'kon")
        rows = await SettingsRepository.get_settings_by_prefix("btn:menu_shop")
        ok("reset DB yozuvlarini o'chirdi", rows == {}, str(rows))

    asyncio.run(_case())
    registry.clear_overrides()


# =============================================================
# 4. Deposit repository
# =============================================================
def test_deposit_repository() -> None:
    print("== 4. deposit repository ==")

    async def _case() -> None:
        await UserRepository.get_or_create_user(USER_ID, "ali", "Ali", None)

        deposit = await DepositRepository.create_deposit(
            USER_ID, 150, receipt_file_id="file-1"
        )
        ok("pending yaratildi", deposit.status == DEPOSIT_PENDING and deposit.id > 0)
        ok("chek file_id saqlandi", deposit.receipt_file_id == "file-1")

        pending = await DepositRepository.count_pending_deposits()
        ok("pending sanasi", pending >= 1)

        confirmed, new_balance = await DepositRepository.confirm_deposit(
            deposit.id, ADMIN_USER_ID
        )
        ok("tasdiqlandi", confirmed.status == DEPOSIT_APPROVED)
        ok("balans +150", new_balance == 150, str(new_balance))
        ok("admin_id yozildi", confirmed.admin_id == ADMIN_USER_ID)

        async with conn_mod._session_factory() as session:
            rows = (
                await session.execute(
                    select(Transaction).where(Transaction.user_id == USER_ID)
                )
            ).scalars().all()
        ok("deposit audit yozuvi",
           len(rows) == 1
           and rows[0].tx_type == "deposit"
           and rows[0].amount == 150
           and rows[0].reference_id == str(deposit.id))

        try:
            await DepositRepository.confirm_deposit(deposit.id, ADMIN_USER_ID)
            ok("takroriy tasdiqlash rad etilishi kerak edi", False)
        except DepositStateError:
            ok("takroriy tasdiqlash rad etildi (idempotent)", True)

        user = await UserRepository.get_user(USER_ID)
        ok("balans ikki marta qo'shilmadi", user is not None and user.stars_balance == 150)

        rejected = await DepositRepository.create_deposit(USER_ID, 80, "file-2")
        result = await DepositRepository.reject_deposit(
            rejected.id, ADMIN_USER_ID, "chek xira"
        )
        ok("rad etildi", result.status == DEPOSIT_REJECTED)
        ok("sabab saqlandi", result.reject_reason == "chek xira")
        user = await UserRepository.get_user(USER_ID)
        ok("rad etishda balans o'zgarmadi", user is not None and user.stars_balance == 150)

        try:
            await DepositRepository.confirm_deposit(rejected.id, ADMIN_USER_ID)
            ok("rad etilganni tasdiqlab bo'lmasligi kerak", False)
        except DepositStateError:
            ok("rad etilgan so'rov tasdiqlanmaydi", True)

    asyncio.run(_case())


def test_deposits_table_created_by_schema() -> None:
    print("== 4.1. ensure_schema ==")

    async def _case() -> bool:
        await ensure_schema()
        async with engine.connect() as conn:
            names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        return "deposits" in names

    ok("deposits jadvali mavjud", asyncio.run(_case()))


# =============================================================
# 5. Karta to'ldirish oqimi
# =============================================================
def test_topup_flow() -> None:
    print("== 5. topup flow ==")
    user_id = USER_ID + 1

    async def _prepare() -> None:
        await UserRepository.get_or_create_user(user_id, "vali", "Vali", None)
        await SettingsRepository.update_setting(SETTING_CARD_NUMBER, "8600123456789012")
        await SettingsRepository.update_setting(SETTING_CARD_HOLDER, "ALI VALIYEV")

    asyncio.run(_prepare())

    # 5.1 boshlash
    msg = FakeMessage(user_id=user_id)
    state = FakeState()
    asyncio.run(topup.topup_start_message(msg, state))
    ok("miqdor so'raldi", state.state is not None)
    ok("karta raqami ko'rsatildi",
       any("8600 1234 5678 9012" in text for text, _ in msg.answers))
    ok("bekor qilish tugmasi bor",
       msg.answers[-1][1].get("reply_markup") is not None)

    # 5.2 noto'g'ri miqdor
    bad = FakeMessage(user_id=user_id, text="abc")
    asyncio.run(topup.topup_amount(bad, state))
    ok("noto'g'ri miqdor rad etildi", "butun son" in bad.answers[0][0])

    # 5.3 minimaldan kam
    low = FakeMessage(user_id=user_id, text="5")
    asyncio.run(topup.topup_amount(low, state))
    ok("minimal miqdor tekshirildi", "Minimal" in low.answers[0][0])

    # 5.4 to'g'ri miqdor -> chek so'raladi
    good = FakeMessage(user_id=user_id, text="250")
    asyncio.run(topup.topup_amount(good, state))
    ok("chek so'raldi", "chek" in good.answers[0][0].lower() or "To'lov cheki" in good.answers[0][0])
    ok("miqdor state'da", state.data.get("amount") == 250)

    # 5.5 chek (rasm) -> deposit + adminlarga rasm
    photo_msg = FakeMessage(
        user_id=user_id,
        photo=[SimpleNamespace(file_id="receipt-file-1")],
    )
    asyncio.run(topup.topup_receipt_photo(photo_msg, state))
    ok("so'rov qabul qilindi", any("Chek qabul qilindi" in t for t, _ in photo_msg.answers))
    ok("state tozalandi", state.state is None)
    ok("adminlarga rasm yuborildi", len(photo_msg.bot.photos) == 2,
       str(len(photo_msg.bot.photos)))
    first = photo_msg.bot.photos[0]
    ok("rasm file_id uzatildi", first[1] == "receipt-file-1")
    markup = first[2].get("reply_markup")
    approve = markup.inline_keyboard[0][0]
    reject = markup.inline_keyboard[0][1]
    ok("tasdiqlash/rad etish tugmalari",
       approve.callback_data.startswith("dep_approve_")
       and reject.callback_data.startswith("dep_reject_"))
    ok("tasdiqlash success, rad etish danger",
       approve.style == "success" and reject.style == "danger")

    deposit_id = int(approve.callback_data.rsplit("_", 1)[-1])

    # 5.6 admin tasdiqlaydi
    bot = FakeBot()
    callback = FakeCallback(ADMIN_USER_ID, f"dep_approve_{deposit_id}", bot)
    asyncio.run(admin_deposits.deposit_approve(callback))

    ok("admin javobi", callback.answers[0]["text"].startswith("✅"))
    ok("foydalanuvchiga xabar yuborildi", len(bot.messages) == 1)
    ok("xabar balans haqida", "+250" in bot.messages[0][1])
    ok("admin kartasi tahrirlandi", bool(callback.message.edits))
    ok("tugmalar olib tashlandi",
       callback.message.edits[0].get("reply_markup") is None)

    async def _balance() -> int:
        user = await UserRepository.get_user(user_id)
        return user.stars_balance if user else -1

    ok("balansga 250 qo'shildi", asyncio.run(_balance()) == 250)

    # 5.7 takroriy tasdiqlash
    again = FakeCallback(ADMIN_USER_ID, f"dep_approve_{deposit_id}", FakeBot())
    asyncio.run(admin_deposits.deposit_approve(again))
    ok("takroriy tasdiq ogohlantirildi",
       again.answers[0]["show_alert"] is True)
    ok("balans o'zgarmadi", asyncio.run(_balance()) == 250)

    # 5.8 rad etish
    async def _new_deposit() -> int:
        dep = await DepositRepository.create_deposit(user_id, 70, "receipt-2")
        return dep.id

    new_id = asyncio.run(_new_deposit())
    reject_bot = FakeBot()
    reject_cb = FakeCallback(ADMIN_USER_ID, f"dep_reject_{new_id}", reject_bot)
    asyncio.run(admin_deposits.deposit_reject(reject_cb))
    ok("rad etish javobi", reject_cb.answers[0]["text"].startswith("❌"))
    ok("foydalanuvchiga rad xabari",
       len(reject_bot.messages) == 1 and "rad etildi" in reject_bot.messages[0][1])
    ok("balans o'zgarmadi (rad)", asyncio.run(_balance()) == 250)

    # 5.9 karta kiritilmagan holat
    async def _clear_card() -> None:
        await SettingsRepository.delete_setting(SETTING_CARD_NUMBER)

    asyncio.run(_clear_card())
    no_card_msg = FakeMessage(user_id=user_id)
    no_card_state = FakeState()
    asyncio.run(topup.topup_start_message(no_card_msg, no_card_state))
    ok(
        "kartasiz: avtomatik to'lov usuli taklif qilinadi",
        "Avtomatik to'lov" in no_card_msg.answers[0][0]
        and no_card_state.state is not None,
    )

    # Karta sozlamasi bo'sh bo'lsa ham default HUMO kartasi ishlatiladi
    card_cb = FakeCallback(user_id, "topup_card", FakeBot())
    asyncio.run(topup.topup_card_callback(card_cb, no_card_state))
    ok(
        "default karta bilan miqdor so'raladi",
        "Qancha Stars olmoqchisiz" in card_cb.message.answers[0][0],
        card_cb.message.answers[0][0],
    )


# =============================================================
# 6. Gift delivery (sendGift)
# =============================================================
def test_gift_delivery() -> None:
    print("== 6. gift delivery ==")
    bot = FakeBot()

    product = SimpleNamespace(
        title="Teddy Bear", payload={"gift_id": "5170145012310081615"}
    )
    ok("gift_id payload'dan", extract_gift_id(product) == "5170145012310081615")

    nested = SimpleNamespace(title="X", payload={"metadata": {"gift_id": "42"}})
    ok("gift_id metadata'dan", extract_gift_id(nested) == "42")
    ok("gift_id yo'q -> None", extract_gift_id(SimpleNamespace(payload={})) is None)

    delivered = asyncio.run(deliver_gift(bot, USER_ID, product))
    ok("sovg'a yuborildi", delivered is True)
    ok("send_gift parametrlari",
       bot.gifts[0]["gift_id"] == "5170145012310081615"
       and bot.gifts[0]["user_id"] == USER_ID)
    ok("sovg'a izohi bor", "Teddy Bear" in bot.gifts[0]["text"])

    plain_bot = FakeBot()
    not_delivered = asyncio.run(
        deliver_gift(plain_bot, USER_ID, SimpleNamespace(payload={}))
    )
    ok("gift_id bo'lmasa False", not_delivered is False)
    ok("gift_id bo'lmasa API chaqirilmaydi", plain_bot.gifts == [])

    broken_bot = FakeBot(
        gift_error=TelegramAPIError(
            SendMessage(chat_id=USER_ID, text="x"), "bad request"
        )
    )
    failed = asyncio.run(deliver_gift(broken_bot, USER_ID, product))
    ok("Telegram xatosida False (admin fallback)", failed is False)


def test_shop_auto_gift_on_payment() -> None:
    print("== 6.1 shop + sendGift ==")
    order = SimpleNamespace(
        id=901, user_id=USER_ID, product_id=1, order_data={}, status="paid"
    )
    product = SimpleNamespace(
        id=1,
        category="gift",
        title="Teddy Bear",
        price_xtr=50,
        is_active=True,
        payload={"gift_id": "gift-1"},
    )
    msg = FakeMessage(user_id=USER_ID)
    msg.successful_payment = SimpleNamespace(
        currency="XTR",
        total_amount=50,
        invoice_payload=f"order:901:{USER_ID}",
        telegram_payment_charge_id="charge-1",
        provider_payment_charge_id="",
    )

    async def _case() -> None:
        with patch.object(
            shop.OrderRepository, "complete_order", AsyncMock(return_value=order)
        ), patch.object(
            shop.ProductRepository, "get_product_by_id", AsyncMock(return_value=product)
        ):
            await shop.on_successful_payment(msg)

    asyncio.run(_case())
    ok("to'lovdan keyin sendGift chaqirildi", len(msg.bot.gifts) == 1, str(msg.bot.gifts))
    ok("sovg'a egasi to'g'ri", msg.bot.gifts[0]["user_id"] == USER_ID)
    ok("foydalanuvchiga avtomatik yetkazildi deyildi",
       any("avtomatik yuborildi" in text for text, _ in msg.answers))


# =============================================================
# 7. Admin tugma muharriri
# =============================================================
def test_admin_button_editor_extracts_emoji() -> None:
    print("== 7. admin button editor ==")
    entity = SimpleNamespace(type="custom_emoji", custom_emoji_id="5368324170671202286")
    msg = FakeMessage(entities=[entity], text="🎁")
    ok("entity'dan ID olindi",
       admin_buttons._extract_custom_emoji_id(msg) == "5368324170671202286")

    numeric = FakeMessage(text="5368324170671202286")
    ok("raqamli ID qabul qilindi",
       admin_buttons._extract_custom_emoji_id(numeric) == "5368324170671202286")

    plain = FakeMessage(text="🎁 sovg'a")
    ok("premium emoji yo'q -> None",
       admin_buttons._extract_custom_emoji_id(plain) is None)

    async def _case() -> None:
        await save_button_icon("menu_gifts", "5368324170671202286")
        ok("muharrir ID'si klaviaturaga qo'llandi",
           registry.get_icon("menu_gifts") == "5368324170671202286")
        await reset_button("menu_gifts")

    asyncio.run(_case())
    ok("resetdan keyin default", registry.get_icon("menu_gifts") is None)


# =============================================================
# 8. Karta sozlamalari (admin)
# =============================================================
def test_admin_card_settings_flow() -> None:
    print("== 8. admin karta sozlamalari ==")
    import handlers.admin.users_manage as um

    async def _clear() -> None:
        await SettingsRepository.delete_setting(SETTING_CARD_NUMBER)
        await SettingsRepository.delete_setting(SETTING_CARD_HOLDER)

    asyncio.run(_clear())

    state = FakeState()
    start_cb = FakeCallback(ADMIN_USER_ID, "adm_um_set_card", FakeBot())
    asyncio.run(um.set_card_start(start_cb, state))
    ok("karta raqami so'raldi", state.state is not None)

    bad = FakeMessage(user_id=ADMIN_USER_ID, text="123")
    asyncio.run(um.set_card_number(bad, state))
    ok("qisqa raqam rad etildi", "16–19" in bad.answers[0][0])

    number_msg = FakeMessage(user_id=ADMIN_USER_ID, text="8600 1234 5678 9012")
    asyncio.run(um.set_card_number(number_msg, state))
    ok("raqam saqlandi (state)", state.data.get("card_number") == "8600123456789012")

    holder_msg = FakeMessage(user_id=ADMIN_USER_ID, text="ALI VALIYEV")
    asyncio.run(um.set_card_holder(holder_msg, state))
    ok("rekvizitlar saqlandi", "saqlandi" in holder_msg.answers[0][0].lower())
    ok("state tozalandi", state.state is None)

    async def _stored() -> tuple[str, str]:
        number = await SettingsRepository.get_setting(SETTING_CARD_NUMBER, "")
        holder = await SettingsRepository.get_setting(SETTING_CARD_HOLDER, "")
        return str(number), str(holder)

    number, holder = asyncio.run(_stored())
    ok("DB'da raqam", number == "8600123456789012", number)
    ok("DB'da egasi", holder == "ALI VALIYEV", holder)


def test_card_auto_payment() -> None:
    print("== 9. karta avtomatik to'lovi ==")

    # 9.1 summa ajratish formatlari
    ok("bo'shliqli summa",
       find_payment_amount("Hisobingizga 10 037 so'm tushdi", {10037}) == 10037)
    ok("uzluksiz summa",
       find_payment_amount("To'lov: 10037 so'm", {10037}) == 10037)
    ok("vergulli summa",
       find_payment_amount("10,037 UZS keldi", {10037}) == 10037)
    ok("NBSP bilan summa",
       find_payment_amount("Balans: 10\u00a0037 so'm", {10037}) == 10037)
    ok("karta raqami summani buzmaydi",
       find_payment_amount("8600 1234 5678 9012 -> 10 037 so'm", {10037}) == 10037)
    ok("mos kelmagan summa -> None",
       find_payment_amount("5 000 so'm", {10037}) is None)
    ok("noaniq holat (2 nomzod) -> None",
       find_payment_amount("5 000 va 6 000", {5000, 6000}) is None)
    ok("format_som", format_som(10037) == "10 037", format_som(10037))
    ok("extract_amounts tartibi",
       extract_amounts("10 037 so'm, keyin 25 000") == [10037, 25000])

    # 9.2 unikal summa bilan so'rov yaratish (avtomatik rejim yoqilgan)
    old_chat = settings.CARD_LOG_CHAT_ID
    settings.CARD_LOG_CHAT_ID = -100777

    auto_user = USER_ID + 2
    base = 100 * settings.SOM_PER_STAR

    async def _prepare() -> None:
        await UserRepository.get_or_create_user(auto_user, "karim", "Karim", None)
        await SettingsRepository.update_setting(
            SETTING_CARD_NUMBER, "8600123456789012"
        )
        await SettingsRepository.update_setting(SETTING_CARD_HOLDER, "ALI VALIYEV")

    asyncio.run(_prepare())

    state = FakeState()
    amount_msg = FakeMessage(user_id=auto_user, text="100")
    asyncio.run(topup.topup_amount(amount_msg, state))

    text = amount_msg.answers[0][0]
    ok("summa so'mda ko'rsatildi", "so'm" in text and "unikal kod" in text, text)
    ok("5 daqiqalik ogohlantirish", "daqiqa" in text, text)
    ok("state'da deposit_id", bool(state.data.get("deposit_id")))
    pay_amount = int(state.data.get("pay_amount") or 0)
    ok("unikal kod 40..100", 40 <= pay_amount - base <= 100, str(pay_amount))

    deposit_id = int(state.data["deposit_id"])

    async def _deposit(_id: int):
        return await DepositRepository.get_deposit(_id)

    deposit = asyncio.run(_deposit(deposit_id))
    ok("so'rov pending va summa bilan",
       deposit is not None
       and deposit.status == DEPOSIT_PENDING
       and deposit.pay_amount == pay_amount)

    # 9.3 bir xil miqdorda kod takrorlanmaydi
    second_state = FakeState()
    second_msg = FakeMessage(user_id=auto_user, text="100")
    asyncio.run(topup.topup_amount(second_msg, second_state))
    ok("ikkinchi so'rovda boshqa summa",
       second_state.data.get("pay_amount") != pay_amount,
       f"{second_state.data.get('pay_amount')} == {pay_amount}")

    # 9.4 jurnal chati orqali avtomatik tasdiqlash
    log_bot = FakeBot()
    log_msg = FakeMessage(
        user_id=1,
        bot=log_bot,
        text=f"Kartangizga {format_som(pay_amount)} so'm o'tkazildi",
    )
    log_msg.chat.id = -100777

    asyncio.run(card_auto.on_card_log_message(log_msg))

    async def _balance() -> int:
        user = await UserRepository.get_user(auto_user)
        return user.stars_balance if user else -1

    ok("balans avtomatik +100", asyncio.run(_balance()) == 100, str(asyncio.run(_balance())))
    deposit = asyncio.run(_deposit(deposit_id))
    ok("so'rov approved (tizim)",
       deposit.status == DEPOSIT_APPROVED
       and deposit.admin_id == AUTO_DEPOSIT_ADMIN_ID)
    ok("jurnal matni saqlandi", bool(deposit.payment_comment))
    ok("foydalanuvchiga xabar",
       any(chat == auto_user and "+100" in text for chat, text, _ in log_bot.messages))
    admin_msgs = [chat for chat, _, _ in log_bot.messages if chat in settings.ADMIN_IDS]
    ok("adminlarga hisobot", len(admin_msgs) == 2, str(admin_msgs))

    # 9.5 takroriy xabar -> balans o'zgarmaydi
    repeat_bot = FakeBot()
    repeat_msg = FakeMessage(
        user_id=1,
        bot=repeat_bot,
        text=f"Kartangizga {format_som(pay_amount)} so'm o'tkazildi",
    )
    repeat_msg.chat.id = -100777
    asyncio.run(card_auto.on_card_log_message(repeat_msg))
    ok("takroriy to'lov hisoblanmadi", asyncio.run(_balance()) == 100)
    ok("takroriy xabarda hech kimga yozilmadi", repeat_bot.messages == [])

    # 9.5b kanal posti orqali ham avtomatik tasdiqlanadi
    ok("kanal posti handleri ro'yxatda",
       bool(card_auto.router.channel_post.handlers))
    second_pay = int(second_state.data["pay_amount"])
    channel_bot = FakeBot()
    channel_post = FakeMessage(
        user_id=1,
        bot=channel_bot,
        text=f"Karta to'lovi: {format_som(second_pay)} so'm",
    )
    channel_post.chat.id = -100777
    asyncio.run(card_auto.on_card_log_channel_post(channel_post))
    ok("kanal posti: ikkinchi so'rov +100", asyncio.run(_balance()) == 200)

    # 9.6 mos kelmagan xabar e'tiborsiz
    other_bot = FakeBot()
    other_msg = FakeMessage(user_id=1, bot=other_bot, text="Balans: 999 so'm")
    other_msg.chat.id = -100777
    asyncio.run(card_auto.on_card_log_message(other_msg))
    ok("mos summasiz xabar o'tkazildi", other_bot.messages == [])

    # 9.7 middleware jurnal chatini obunadan ozod qiladi
    import middlewares.subscription as sub_mw

    async def _mw_case() -> str:
        original = sub_mw.get_active_channel_dicts

        async def _channels():
            return [{"chat_id": -1, "title": "K", "invite_link": "x"}]

        sub_mw.get_active_channel_dicts = _channels
        try:
            mw = sub_mw.SubscriptionMiddleware()

            async def _handler(event, data):
                return "HANDLED"

            data = {"bot": FakeBot(), "event_from_user": log_msg.from_user}
            return await mw(_handler, log_msg, data)
        finally:
            sub_mw.get_active_channel_dicts = original

    ok("jurnal chati middleware'dan o'tdi", asyncio.run(_mw_case()) == "HANDLED")

    # 9.8 avtomatik tasdiqlangandan keyin chek -> xabar
    auto_state = FakeState()
    auto_state.data = {"amount": 100, "deposit_id": deposit_id}
    late_photo = FakeMessage(
        user_id=auto_user, photo=[SimpleNamespace(file_id="late-receipt")]
    )
    asyncio.run(topup.topup_receipt_photo(late_photo, auto_state))
    ok("kech kelgan chek: allaqachon tasdiqlangan",
       any("allaqachon avtomatik tasdiqlangan" in text for text, _ in late_photo.answers))
    ok("kech kelgan chek adminlarga yuborilmadi", late_photo.bot.photos == [])

    # 9.9 zaxira yo'l: chek + admin
    fallback_state = FakeState()
    fallback_amount = FakeMessage(user_id=auto_user, text="50")
    asyncio.run(topup.topup_amount(fallback_amount, fallback_state))
    fallback_photo = FakeMessage(
        user_id=auto_user, photo=[SimpleNamespace(file_id="receipt-fallback")]
    )
    asyncio.run(topup.topup_receipt_photo(fallback_photo, fallback_state))
    ok("chek qabul qilindi",
       any("Chek qabul qilindi" in text for text, _ in fallback_photo.answers))
    ok("adminlarga 2 ta rasm", len(fallback_photo.bot.photos) == 2)
    markup = fallback_photo.bot.photos[0][2].get("reply_markup")
    ok("zaxira: tasdiqlash/rad etish",
       markup.inline_keyboard[0][0].callback_data.startswith("dep_approve_"))
    ok("zaxira: state tozalandi", fallback_state.state is None)

    fb_id = int(markup.inline_keyboard[0][0].callback_data.rsplit("_", 1)[-1])
    fb_deposit = asyncio.run(_deposit(fb_id))
    ok("zaxira so'rov pending + chek biriktirildi",
       fb_deposit is not None
       and fb_deposit.status == DEPOSIT_PENDING
       and fb_deposit.receipt_file_id == "receipt-fallback")

    settings.CARD_LOG_CHAT_ID = old_chat


def main() -> None:
    test_emoji_loader()
    test_button_registry()
    test_button_store()
    test_deposit_repository()
    test_deposits_table_created_by_schema()
    test_topup_flow()
    test_gift_delivery()
    test_shop_auto_gift_on_payment()
    test_admin_button_editor_extracts_emoji()
    test_admin_card_settings_flow()
    test_card_auto_payment()
    print(f"\nBARCHA TESTLAR O'TDI: {PASSED} ta tekshiruv")


if __name__ == "__main__":
    main()
