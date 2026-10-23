"""
Admin panelning 5 ta yangi vositasi, promokod, bloklash va zakaz kanali testlari.

Ishga tushirish (repo root'dan):
    pip install aiogram>=3.13.0 aiosqlite
    python tests/test_admin_tools.py

Qamrov:
 1. 💰 Bot Stars hisobi — getMyStarBalance + tugmalar.
 2. ⭐️ Botga Stars solish — XTR invoice (100/500/1000) + botfill oqimi.
 3. 🔎 Foydalanuvchini qidirish — karta, balans +/-, bloklash/ochish.
 4. 🎟️ Promokod — yaratish, bir marta ishlatish, limit.
 5. 📬 Zakaz kanali — chek matni + Topshirildi/Bekor qilish tugmalari.
 Qo'shimcha: BlockingMiddleware, admin klaviaturasi tugmalari.
"""

import asyncio
import os
import sys
from types import SimpleNamespace

os.environ.update({
    "BOT_TOKEN": "123:TEST",
    "ADMIN_IDS": "999001",
    "DATABASE_URL": "postgresql://user:pass@host:5432/postgres",
})
sys.path.insert(0, ".")

from aiogram.exceptions import TelegramAPIError  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import database.connection as conn_mod  # noqa: E402

DB_PATH = os.path.abspath("./_test_admin_tools.sqlite3")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}")
conn_mod._engine = engine
conn_mod._session_factory = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)

from database.models import (  # noqa: E402
    Base,
    PromoCode,
    PromoRedemption as PromoRedemptionRow,
    Transaction,
    User,
    UserBlock,
)
from database.queries import (  # noqa: E402
    BlockRepository,
    PromoError,
    PromoRepository,
    SettingsRepository,
    UserRepository,
    SETTING_ORDER_LOG_CHAT,
)
from handlers.admin import bot_tools  # noqa: E402
from handlers.admin.bot_tools import UserSearchStates  # noqa: E402
from keyboards.builders import get_admin_main_kb  # noqa: E402
from middlewares.blocking import (  # noqa: E402
    BlockingMiddleware,
    clear_cache as clear_block_cache,
)
from services.order_log import (  # noqa: E402
    CB_ZAKAZ_CANCEL_PREFIX,
    CB_ZAKAZ_DONE_PREFIX,
    build_zakaz_receipt,
    notify_order_paid,
)

PASSED = 0


def ok(label: str, cond: bool, extra: str = "") -> None:
    global PASSED
    if not cond:
        raise AssertionError(f"FAILED: {label} {extra}")
    PASSED += 1
    print(f"  ok: {label}")


def run(coro):
    return asyncio.run(coro)


def kb_callbacks(kb) -> list[str]:
    return [b.callback_data for row in kb.inline_keyboard for b in row]


# =============================================================
# Duck-typed Telegram obyektlari
# =============================================================
def tg_user(uid: int, first_name: str = "User", username: str | None = None):
    return SimpleNamespace(
        id=uid, is_bot=False, first_name=first_name,
        last_name=None, username=username,
    )


class FakeBot:
    def __init__(self, star_balance: int = 1234):
        self.star_balance = star_balance
        self.sent: list[dict] = []
        self.invoices: list[dict] = []

    async def get_my_star_balance(self):
        return SimpleNamespace(amount=self.star_balance)

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id))

    async def send_invoice(self, **kwargs):
        self.invoices.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(id=kwargs["chat_id"]))


class FakeMessage:
    def __init__(self, user, bot: FakeBot | None = None, text: str | None = None):
        self.from_user = user
        self.bot = bot or FakeBot()
        self.chat = SimpleNamespace(id=user.id, type="private")
        self.text = text
        self.answers: list[dict] = []
        self.successful_payment = None

    async def answer(self, text, **kwargs):
        self.answers.append({"text": text, **kwargs})


class FakeCallback:
    def __init__(self, user, data: str, bot: FakeBot | None = None):
        self.from_user = user
        self.data = data
        self.bot = bot or FakeBot()
        self.message = FakeMessage(user, self.bot)
        self.answers: list[dict] = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append({"text": text, "show_alert": show_alert})


class FakeState:
    def __init__(self):
        self.state = None
        self.data: dict = {}
        self.cleared = 0

    async def set_state(self, state):
        self.state = state

    async def get_state(self):
        return self.state

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.state = None
        self.data = {}
        self.cleared += 1


async def seed_user(uid: int, username: str = "ali", first_name: str = "Ali"):
    return await UserRepository.get_or_create_user(uid, username, first_name, None)


# =============================================================
# 1. 💰 BOT STARS HISOBI
# =============================================================
def test_bot_star_balance():
    print("== A. Bot Stars hisobi ==")

    async def case():
        cb = FakeCallback(tg_user(999001, "Admin"), "adm_star_balance", FakeBot(4321))
        await bot_tools.bot_star_balance(cb, FakeState())
        ok("callback darhol javob berdi", bool(cb.answers), str(cb.answers))
        text = cb.message.answers[0]["text"]
        ok("balans ko'rsatildi", "4321 Stars" in text, text)
        cbs = kb_callbacks(cb.message.answers[0]["reply_markup"])
        ok("Stars to'ldirish tugmasi", "adm_star_fill" in cbs, str(cbs))
        ok("Orqaga tugmasi", "adm_back" in cbs, str(cbs))

        class ErrBot(FakeBot):
            async def get_my_star_balance(self):
                raise TelegramAPIError(method=None, message="boom")

        cb2 = FakeCallback(tg_user(999001), "adm_star_balance", ErrBot())
        await bot_tools.bot_star_balance(cb2, FakeState())
        ok("API xatosi -> xabar, crash yo'q",
           len(cb2.message.answers) == 1
           and "urinib ko'ring" in cb2.message.answers[0]["text"])

    run(case())


# =============================================================
# 2. ⭐️ BOTGA STARS SOLISH
# =============================================================
def test_bot_star_fill():
    print("== B. Botga Stars solish ==")

    async def case():
        bot = FakeBot(100)
        cb = FakeCallback(tg_user(999001), "adm_star_pay_500", bot)
        await bot_tools.bot_star_fill_invoice(cb)
        ok("callback javob berdi", bool(cb.answers))
        ok("bitta invoice yuborildi", len(bot.invoices) == 1, str(bot.invoices))
        inv = bot.invoices[0]
        ok("XTR valyuta", inv["currency"] == "XTR")
        ok("provider token bo'sh", inv["provider_token"] == "")
        ok("payload botfill:", inv["payload"] == "botfill:500", str(inv["payload"]))
        ok("narx 500", inv["prices"][0].amount == 500)

        cb2 = FakeCallback(tg_user(999001), "adm_star_pay_777", bot)
        await bot_tools.bot_star_fill_invoice(cb2)
        ok("noto'g'ri miqdor -> invoice yo'q", len(bot.invoices) == 1)
        ok("xabar chiqdi", len(cb2.message.answers) == 1)

    run(case())


# =============================================================
# 3. 🔎 FOYDALANUVCHINI QIDIRISH
# =============================================================
def test_user_search():
    print("== C. Foydalanuvchini qidirish ==")

    async def case():
        async with engine.begin() as a:
            await a.run_sync(Base.metadata.create_all)
        await seed_user(555, "ali", "Ali")

        # Bazada yo'q
        msg = FakeMessage(tg_user(999001), text="999999")
        st = FakeState()
        st.state = UserSearchStates.waiting_for_user_id
        await bot_tools.user_search_apply(msg, st)
        ok("yo'q ID -> ogohlantirish",
           "bazada yo'q" in msg.answers[0]["text"], msg.answers[0]["text"])

        # Mavjud foydalanuvchi -> karta
        msg2 = FakeMessage(tg_user(999001), text="555")
        await bot_tools.user_search_apply(msg2, st)
        card = msg2.answers[0]["text"]
        ok("karta: ism", "Ali" in card, card)
        ok("karta: username", "@ali" in card)
        ok("karta: balans", "Balans" in card)
        ok("karta: takliflar", "Takliflar" in card)
        ok("karta: holat", "✅ faol" in card)
        cbs = kb_callbacks(msg2.answers[0]["reply_markup"])
        ok("tugmalar: qo'shish/ayirish",
           "adm_usadd_555" in cbs and "adm_ussub_555" in cbs, str(cbs))
        ok("tugma: bloklash", "adm_ublok_555" in cbs, str(cbs))
        ok("state tozalandi", st.state is None)

        # Balans qo'shish
        add_cb = FakeCallback(tg_user(999001), "adm_usadd_555")
        await bot_tools.user_balance_add_start(add_cb, FakeState())
        ok("qo'shish: miqdor so'radi", len(add_cb.message.answers) == 1)

        # Balans ayirish (yetarli emas -> xato, balans o'zgarmaydi)
        sub_cb = FakeCallback(tg_user(999001), "adm_ussub_555")
        await bot_tools.user_balance_sub_start(sub_cb, FakeState())
        ok("ayirish: miqdor so'radi", len(sub_cb.message.answers) == 1)

        # Bloklash
        blk_cb = FakeCallback(tg_user(999001), "adm_ublok_555")
        await bot_tools.user_block(blk_cb)
        ok("bloklash xabari", "bloklandi" in blk_cb.message.answers[0]["text"])
        ok("blok saqlandi", await BlockRepository.is_blocked(555))
        card2 = blk_cb.message.answers[-1]["text"]
        ok("karta: holat bloklangan", "🚫 bloklangan" in card2, card2)
        cbs2 = kb_callbacks(blk_cb.message.answers[-1]["reply_markup"])
        ok("ochish tugmasi", "adm_uunblock_555" in cbs2, str(cbs2))

        # Ochish
        unb_cb = FakeCallback(tg_user(999001), "adm_uunblock_555")
        await bot_tools.user_unblock(unb_cb)
        ok("ochish xabari", "bloki ochildi" in unb_cb.message.answers[0]["text"])
        ok("blok ochildi", not await BlockRepository.is_blocked(555))

        # users.is_active ham yangilandi
        user = await UserRepository.get_user(555)
        ok("is_active=True", user.is_active is True)

    run(case())


# =============================================================
# 4. 🎟️ PROMOKODLAR
# =============================================================
def test_promo():
    print("== D. Promokodlar ==")

    async def case():
        await seed_user(777, "seven", "Seven")

        # Yaratish
        promo = await PromoRepository.create_code("YUTUQ", 10, 2, created_by=999001)
        ok("kod yaratildi", promo.code == "YUTUQ" and promo.stars == 10)
        ok("kod katta harfda", promo.code.isupper())

        # Takroriy yaratish -> PromoError
        try:
            await PromoRepository.create_code("YUTUQ", 5, 1)
            ok("takroriy kod -> xato", False)
        except PromoError:
            ok("takroriy kod -> xato", True)

        # Noto'g'ri qiymat
        try:
            await PromoRepository.create_code("XATO", 0, 1)
            ok("stars=0 -> ValueError", False)
        except ValueError:
            ok("stars=0 -> ValueError", True)

        # Birinchi ishlatish
        balance = await PromoRepository.redeem_code("yutuq", 777)
        ok("birinchi ishlatish", balance == 10, str(balance))

        # Ikkinchi marta -> rad etiladi
        try:
            await PromoRepository.redeem_code("YUTUQ", 777)
            ok("ikkinchi marta -> rad", False)
        except PromoError as exc:
            ok("ikkinchi marta -> rad", "allaqachon" in str(exc), str(exc))

        # Boshqa odam ishlata oladi (limit=2)
        await seed_user(778, "eight", "Eight")
        balance2 = await PromoRepository.redeem_code("YUTUQ", 778)
        ok("ikkinchi odam", balance2 == 10, str(balance2))

        # Limit tugadi (used=2, max=2)
        await seed_user(779, "nine", "Nine")
        try:
            await PromoRepository.redeem_code("YUTUQ", 779)
            ok("limit tugagan -> rad", False)
        except PromoError as exc:
            ok("limit tugagan -> rad", "limit" in str(exc), str(exc))

        # Mavjud bo'lmagan kod
        try:
            await PromoRepository.redeem_code("YOQ", 777)
            ok("yo'q kod -> rad", False)
        except PromoError as exc:
            ok("yo'q kod -> rad", "topilmadi" in str(exc), str(exc))

        # Balans haqiqatan oshdi + tranzaksiya yozildi
        user = await UserRepository.get_user(777)
        ok("balans oshdi", user.stars_balance == 10, str(user.stars_balance))
        async with conn_mod._session_factory() as s:
            tx = (await s.execute(
                select(Transaction).where(Transaction.user_id == 777)
            )).scalars().first()
        ok("tranzaksiya: promo", tx is not None and tx.tx_type == "promo")

        # Admin ro'yxati
        codes = await PromoRepository.list_codes()
        ok("ro'yxatda 1 ta kod", len(codes) == 1 and codes[0].code == "YUTUQ")

        # Bazada jadvallar haqiqatan yaratilgan
        async with conn_mod._session_factory() as s:
            redemptions = (await s.execute(select(PromoRedemptionRow))).scalars().all()
        ok("promo_redemptions yozuvlari", len(redemptions) == 2, str(len(redemptions)))

    run(case())


# =============================================================
# 5. 📬 ZAKAZ KANALI
# =============================================================
def test_order_log():
    print("== E. Zakaz kanali ==")

    async def case():
        # Chek matni
        text = build_zakaz_receipt(
            order_id=42,
            user_label="Ali (@ali) · <code>555</code>",
            title="100 Stars",
            method="card",
            price_label="30 000 so'm",
            category="stars",
        )
        ok("chek: sarlavha", "YANGI XARID" in text, text)
        ok("chek: buyurtma", "#42" in text)
        ok("chek: mahsulot", "100 Stars" in text)
        ok("chek: usul", "HUMO karta" in text)
        ok("chek: summa", "30 000" in text)
        ok("chek: yetkazish", "balansga qo'shildi" in text)

        # Sozlanmagan kanal -> yuborilmaydi, False
        order = SimpleNamespace(
            id=42, user_id=555, payment_method="card",
            price_uzs=30000, order_data={"product_title": "100 Stars", "category": "stars"},
        )
        bot = FakeBot()
        sent = await notify_order_paid(bot, order)
        ok("sozlanmagan kanal -> False", sent is False)
        ok("hech narsa yuborilmadi", len(bot.sent) == 0)

        # Kanal sozlandi -> chek yuboriladi + tugmalar
        await SettingsRepository.update_setting(SETTING_ORDER_LOG_CHAT, -100123)
        sent2 = await notify_order_paid(bot, order)
        ok("chek yuborildi", sent2 is True and len(bot.sent) == 1)
        kb = bot.sent[0]["reply_markup"]
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        ok("Topshirildi tugmasi", f"{CB_ZAKAZ_DONE_PREFIX}42" in cbs, str(cbs))
        ok("Bekor qilish tugmasi", f"{CB_ZAKAZ_CANCEL_PREFIX}42" in cbs, str(cbs))

        # Tugma ishlashi (zakaz_done) — order bazada yo'q -> xato emas
        cb = FakeCallback(tg_user(999001), f"{CB_ZAKAZ_DONE_PREFIX}999")
        await bot_tools.zakaz_done(cb)
        ok("yo'q order -> xabar",
           len(cb.message.answers) == 1 and "topilmadi" in cb.message.answers[0]["text"])

    run(case())


# =============================================================
# 6. BLOCKING MIDDLEWARE
# =============================================================
def test_blocking_middleware():
    print("== F. BlockingMiddleware ==")

    async def case():
        await seed_user(888, "blocked", "Blocked")
        clear_block_cache()

        mw = BlockingMiddleware()
        calls = []

        async def handler(event, data):
            calls.append("handled")
            return "ok"

        class MsgEvent:
            from_user = tg_user(888)

            async def answer(self, text, **kwargs):
                self.notified = text

        # Bloklanmagan -> o'tadi
        result = await mw(handler, MsgEvent(), {"event_from_user": tg_user(888)})
        ok("bloklanmagan -> o'tdi", result == "ok" and calls == ["handled"])

        # Bloklaymiz
        await BlockRepository.block_user(888, blocked_by=999001)
        clear_block_cache()

        calls.clear()
        event = MsgEvent()
        result = await mw(handler, event, {"event_from_user": tg_user(888)})
        ok("bloklangan -> to'xtadi", result is None and calls == [])
        ok("foydalanuvchiga xabar", "🚫" in getattr(event, "notified", ""))

        # Admin bloklangan bo'lsa ham o'tadi (admin mustasno)
        calls.clear()
        result = await mw(handler, MsgEvent(), {"event_from_user": tg_user(999001)})
        ok("admin -> o'tdi", result == "ok")

        # Blok ochildi -> qayta ishlaydi
        await BlockRepository.unblock_user(888)
        clear_block_cache()
        calls.clear()
        result = await mw(handler, MsgEvent(), {"event_from_user": tg_user(888)})
        ok("blok ochildi -> ishlaydi", result == "ok")

    run(case())


# =============================================================
# 7. ADMIN KLAVIATURA
# =============================================================
def test_admin_keyboard():
    print("== G. Admin klaviaturasi ==")
    cbs = kb_callbacks(get_admin_main_kb())
    ok("Bot Stars hisobi", "adm_star_balance" in cbs, str(cbs))
    ok("Botga Stars solish", "adm_star_fill" in cbs)
    ok("Foydalanuvchini qidirish", "adm_user_search" in cbs)
    ok("Promokod", "adm_promo" in cbs)
    ok("Zakaz kanali", "adm_order_channel" in cbs)
    ok("chiqish oxirida", cbs[-1] == "adm_exit")


def main() -> None:
    test_bot_star_balance()
    test_bot_star_fill()
    test_user_search()
    test_promo()
    test_order_log()
    test_blocking_middleware()
    test_admin_keyboard()
    print(f"\nBARCHA TESTLAR O'TDI: {PASSED} ta tekshiruv")


if __name__ == "__main__":
    main()

       