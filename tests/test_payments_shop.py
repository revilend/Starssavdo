"""
Do'kon, XTR to'lov (services/payments.py) va bot balansi xaridi testlari.

Ishga tushirish (repo root'dan):
    pip install aiogram>=3.13.0 cachetools
    python tests/test_payments_shop.py

Repository metodi (create_balance_order, get_product_by_id, get_order)
SQLite ustida tests/test_queries.py da sinovdan o'tkaziladi.
"""

import asyncio
import os
import sys
from types import SimpleNamespace

os.environ.update({
    "BOT_TOKEN": "123:TEST",
    "ADMIN_IDS": "999001",
    "DATABASE_URL": "postgresql://user:pass@host:5432/postgres",
    "REFERRAL_REWARD_STARS": "10",
})
sys.path.insert(0, ".")

from aiogram.exceptions import TelegramAPIError  # noqa: E402

import handlers.user.shop as shop_mod  # noqa: E402
import services.payments as pay_mod  # noqa: E402
from keyboards.inline import (  # noqa: E402
    CB_PAY_BALANCE_PREFIX,
    CB_PAY_XTR_PREFIX,
    get_payment_methods_kb,
)

PASSED = 0


def ok(label: str, cond: bool, extra: str = "") -> None:
    global PASSED
    if not cond:
        raise AssertionError(f"FAILED: {label} {extra}")
    PASSED += 1
    print(f"  ok: {label}")


# =============================================================
# Duck-typed Telegram obyektlari
# =============================================================
class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id))


def tg_user(uid: int, first_name: str = "User", username: str | None = None):
    return SimpleNamespace(id=uid, is_bot=False, first_name=first_name,
                           last_name=None, username=username)


class FakeMessage:
    def __init__(self, user, bot: FakeBot | None = None, chat_id: int | None = None):
        self.from_user = user
        self.bot = bot or FakeBot()
        self.chat = SimpleNamespace(id=chat_id if chat_id is not None else user.id)
        self.answers: list[dict] = []
        self.successful_payment = None

    async def answer(self, text: str, **kwargs):
        self.answers.append({"text": text, **kwargs})

    async def answer_photo(self, photo: str, caption: str | None = None, **kwargs):
        self.answers.append({"photo": photo, "caption": caption, **kwargs})


class FakeCallback:
    def __init__(self, user, data: str, bot: FakeBot | None = None):
        self.from_user = user
        self.data = data
        self.bot = bot or FakeBot()
        self.message = FakeMessage(user, self.bot)
        self.answers: list[dict] = []

    async def answer(self, text: str | None = None, show_alert: bool = False, **kwargs):
        self.answers.append({"text": text, "show_alert": show_alert})


class FakePCQ:
    def __init__(self, user, payload: str):
        self.from_user = user
        self.invoice_payload = payload
        self.answers: list[dict] = []

    async def answer(self, ok: bool, error_message: str | None = None, **kwargs):
        self.answers.append({"ok": ok, "error_message": error_message})


def make_product(pid=1, category="stars", title="100 Stars",
                 description="Eng mashhur to'plam", price=100, active=True,
                 image_file_id=None, payload=None):
    return SimpleNamespace(id=pid, category=category, title=title,
                           description=description, price_xtr=price,
                           is_active=active, image_file_id=image_file_id,
                           payload=payload)


def make_order(oid=7, uid=555, pid=1, status="pending", method="xtr",
               order_data=None):
    return SimpleNamespace(id=oid, user_id=uid, product_id=pid, status=status,
                           payment_method=method,
                           telegram_payment_charge_id=None,
                           order_data=order_data or {},
                           paid_at=None)


def run(coro):
    return asyncio.run(coro)


# =============================================================
# Repository fake'larini o'rnatish / tiklash
# =============================================================
def patch_shop(products=None, orders=None, users=None,
               create_order_exc=None, create_balance_exc=None,
               complete_exc=None, invoice_exc=None):
    """shop_mod repository/servis fake'larini o'rnatadi."""
    products = products if products is not None else {}
    orders = orders if orders is not None else {}
    users = users if users is not None else {}
    state = SimpleNamespace(
        create_order_calls=[], balance_order_calls=[], invoice_calls=[],
        complete_calls=[],
    )

    class FakeProductRepo:
        @staticmethod
        async def get_products_by_category(category: str):
            return [p for p in products.values()
                    if p.category == category and p.is_active]

        @staticmethod
        async def get_product_by_id(pid: int):
            return products.get(pid)

    class FakeOrderRepo:
        @staticmethod
        async def create_order(**kw):
            state.create_order_calls.append(kw)
            if create_order_exc is not None:
                raise create_order_exc
            oid = 100 + len(state.create_order_calls)
            return make_order(oid=oid, uid=kw["user_id"], pid=kw["product_id"],
                              method=kw["payment_method"])

        @staticmethod
        async def create_balance_order(uid: int, pid: int):
            state.balance_order_calls.append((uid, pid))
            if create_balance_exc is not None:
                raise create_balance_exc
            return make_order(oid=200, uid=uid, pid=pid, status="paid",
                              method="balance")

        @staticmethod
        async def get_order(oid: int):
            return orders.get(oid)

        @staticmethod
        async def complete_order(oid: int, charge_id: str):
            state.complete_calls.append((oid, charge_id))
            if complete_exc is not None:
                raise complete_exc
            o = orders.get(oid) or make_order(oid=oid, status="paid")
            o.status = "paid"
            o.telegram_payment_charge_id = charge_id
            return o

    class FakeUserRepo:
        @staticmethod
        async def get_user(uid: int):
            return users.get(uid)

    async def fake_send_invoice(bot, chat_id, title, description, payload,
                                amount_xtr):
        state.invoice_calls.append({
            "chat_id": chat_id, "title": title, "description": description,
            "payload": payload, "amount_xtr": amount_xtr, "bot": bot,
        })
        if invoice_exc is not None:
            raise invoice_exc
        return SimpleNamespace()

    saved = (shop_mod.ProductRepository, shop_mod.OrderRepository,
             shop_mod.UserRepository, shop_mod.send_stars_invoice,
             shop_mod.settings.ADMIN_IDS)

    def restore():
        (shop_mod.ProductRepository, shop_mod.OrderRepository,
         shop_mod.UserRepository, shop_mod.send_stars_invoice,
         shop_mod.settings.ADMIN_IDS) = saved

    shop_mod.ProductRepository = FakeProductRepo
    shop_mod.OrderRepository = FakeOrderRepo
    shop_mod.UserRepository = FakeUserRepo
    shop_mod.send_stars_invoice = fake_send_invoice
    shop_mod.settings.ADMIN_IDS = [999001]
    return SimpleNamespace(state=state, restore=restore)


def kb_callbacks(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def kb_texts(markup) -> list[str]:
    return [btn.text for row in markup.inline_keyboard for btn in row]


# =============================================================
# Testlar
# =============================================================
def test_payments_service():
    print("== A. services/payments ==")
    # payload round-trip
    p = pay_mod.build_order_payload(42, 555)
    ok("payload formati", p == "order:42:555", p)
    ok("parse round-trip", pay_mod.parse_order_payload(p) == (42, 555))
    for bad in ("", "order:x:y", "order:1", "foo:1:2", "order:0:2",
                "order:-1:2", "order:1:0", None):
        ok(f"parse noto'g'ri -> None: {bad!r}",
           pay_mod.parse_order_payload(bad) is None)

    # send_stars_invoice argumentlari
    class InvoiceBot:
        async def send_invoice(self, **kw):
            self.kw = kw
            return SimpleNamespace()

    async def check_invoice():
        bot = InvoiceBot()
        await pay_mod.send_stars_invoice(
            bot=bot, chat_id=555, title="100 Stars",
            description="test to'plam", payload="order:7:555", amount_xtr=100,
        )
        kw = bot.kw
        ok("currency=XTR", kw["currency"] == "XTR")
        ok("provider_token bo'sh (Stars talab qilmaydi)",
           kw["provider_token"] == "")
        ok("prices: bitta LabeledPrice",
           len(kw["prices"]) == 1 and kw["prices"][0].amount == 100
           and kw["prices"][0].label == "100 Stars")
        ok("start_parameter", kw["start_parameter"] == "stars-purchase")
        ok("payload o'tkazildi", kw["payload"] == "order:7:555")
        ok("chat_id/title/description",
           kw["chat_id"] == 555 and kw["title"] == "100 Stars"
           and kw["description"] == "test to'plam")
        try:
            await pay_mod.send_stars_invoice(
                bot=bot, chat_id=1, title="t", description="d",
                payload="p", amount_xtr=0,
            )
            raise SystemExit("ValueError kutilgan")
        except ValueError:
            ok("amount_xtr<=0 -> ValueError", True)

    run(check_invoice())


def test_payment_methods_kb():
    print("== B. get_payment_methods_kb ==")
    kb = get_payment_methods_kb(15)
    cbs = kb_callbacks(kb)
    ok("faqat balans tugmasi", f"{CB_PAY_BALANCE_PREFIX}15" in cbs, str(cbs))
    ok("to'ldirish tugmasi", "topup_start" in cbs, str(cbs))
    ok("orqaga tugmasi", "shop_categories" in cbs)
    texts = kb_texts(kb)
    ok("matnlar", "💳 Botdagi balansimdan sotib olish" in texts
       and "💳 Balans to'ldirish" in texts, str(texts))


def test_shop_menu():
    print("== C. 🛍 Do'kon (menu) ==")
    env = patch_shop()

    async def case():
        msg = FakeMessage(tg_user(555, "Ali"))
        await shop_mod.shop_menu(msg)
        env.restore()
        ok("bitta xabar yuborildi", len(msg.answers) == 1)
        a = msg.answers[0]
        ok("matn: do'kon sarlavhasi",
           "NFTYUT Rasmiy Do'koniga xush kelibsiz" in a["text"])
        ok("matn: 4 bo'lim", all(s in a["text"] for s in (
            "Rasmiy Telegram Stars to'plamlari",
            "Tezkor Telegram Premium obunasi",
            "Telegram eksklyuziv raqamli kolleksiyalari",
            "ajoyib sovg'alar")))
        ok("matn: to'lov haqida",
           "rasmiy Telegram Stars yoki botdagi balansingiz" in a["text"])
        cbs = kb_callbacks(a["reply_markup"])
        ok("toifalar klaviaturasi",
           cbs[:4] == ["cat_stars", "cat_premium", "cat_nft", "cat_gift"]
           and cbs[4] == "back_to_menu", str(cbs))

    run(case())


def test_shop_category():
    print("== D. cat_ toifasi ==")
    prods = {1: make_product(1), 2: make_product(2, "premium", "3 oy Premium",
                                                 "Uch oylik obuna", 1500)}
    env = patch_shop(products=prods)

    async def case():
        cb = FakeCallback(tg_user(555), "cat_stars")
        await shop_mod.shop_category(cb)
        a = cb.message.answers
        ok("xabar yuborildi", len(a) == 1)
        ok("nom+narx+tavsif ko'rsatildi",
           "100 Stars" in a[0]["text"] and "100 XTR" in a[0]["text"]
           and "Eng mashhur to&#x27;plam" in a[0]["text"], a[0]["text"])
        cbs = kb_callbacks(a[0]["reply_markup"])
        ok("buy_prod tugmasi", "buy_prod_1" in cbs, str(cbs))

        # rasmli mahsulot — foto sifatida (caption bilan) yuboriladi
        prods[3] = make_product(3, "gift", "Rasmli gift", "Rasmli sovg'a", 50,
                                image_file_id="AgACAgQxAAImgTest")
        cb_img = FakeCallback(tg_user(555), "cat_gift")
        await shop_mod.shop_category(cb_img)
        a_img = cb_img.message.answers[-1]
        ok("rasmli toifa foto sifatida yuboriladi",
           a_img.get("photo") == "AgACAgQxAAImgTest"
           and "Rasmli gift" in a_img.get("caption", ""), str(a_img))
        ok("foto captionida ham tugmalar bor",
           "buy_prod_3" in kb_callbacks(a_img["reply_markup"]))

        # bo'sh toifa
        cb2 = FakeCallback(tg_user(555), "cat_nft")
        await shop_mod.shop_category(cb2)
        ok("bo'sh toifa matni",
           "Hozircha bu bo'limda mahsulot yo'q" in cb2.message.answers[0]["text"])

        # notanish toifa
        cb3 = FakeCallback(tg_user(555), "cat_hacker")
        await shop_mod.shop_category(cb3)
        ok("notanish toifa -> alert",
           cb3.answers[0]["show_alert"] is True
           and cb3.answers[0]["text"] == shop_mod.ALERT_CATEGORY_NOT_FOUND)

    run(case())
    env.restore()


def test_buy_product():
    print("== E. buy_prod_ — balansdan xarid ==")
    prods = {1: make_product(1)}
    users = {555: SimpleNamespace(user_id=555, stars_balance=150)}
    env = patch_shop(products=prods, users=users)

    async def case():
        cb = FakeCallback(tg_user(555), "buy_prod_1")
        await shop_mod.buy_product(cb)
        ok("atomik xarid chaqirildi",
           env.state.balance_order_calls == [(555, 1)],
           str(env.state.balance_order_calls))
        a = cb.message.answers
        ok("chek matni", len(a) == 1
           and "100 Stars" in a[0]["text"] and "100 XTR" in a[0]["text"]
           and "Qolgan balans: 50 ⭐️" in a[0]["text"])

        # mavjud bo'lmagan
        cb2 = FakeCallback(tg_user(555), "buy_prod_999")
        await shop_mod.buy_product(cb2)
        ok("yo'q mahsulot -> alert",
           cb2.answers[0]["show_alert"] is True
           and cb2.answers[0]["text"] == shop_mod.ALERT_PRODUCT_NOT_FOUND)

        # o'chirilgan
        env.restore()
        env2 = patch_shop(products={5: make_product(5, active=False)})
        cb3 = FakeCallback(tg_user(555), "buy_prod_5")
        await shop_mod.buy_product(cb3)
        env2.restore()
        patch_done = cb3.answers[0]["show_alert"] is True
        ok("o'chirilgan mahsulot -> alert", patch_done)

        # noto'g'ri format
        cb4 = FakeCallback(tg_user(555), "buy_prod_abc")
        await shop_mod.buy_product(cb4)
        ok("noto'g'ri ID -> alert",
           cb4.answers[0]["text"] == shop_mod.ALERT_PRODUCT_NOT_FOUND)

    run(case())
    env.restore()


def test_pay_xtr():
    print("== F. pay_xtr_ (invoys) ==")
    prods = {1: make_product(1)}
    env = patch_shop(products=prods)

    async def case():
        cb = FakeCallback(tg_user(555), "pay_xtr_1")
        await shop_mod.pay_with_xtr(cb)
        calls = env.state.create_order_calls
        ok("pending buyurtma yaratildi",
           len(calls) == 1 and calls[0]["payment_method"] == "xtr"
           and calls[0]["amount_xtr"] == 100 and calls[0]["user_id"] == 555
           and calls[0]["product_id"] == 1, str(calls))
        inv = env.state.invoice_calls
        ok("invoys yuborildi", len(inv) == 1 and inv[0]["amount_xtr"] == 100
           and inv[0]["chat_id"] == 555, str(inv))
        ok("payload buyurtma bilan",
           inv[0]["payload"] == "order:101:555", inv[0]["payload"])
        ok("javob", "To'lov sahifasi" in cb.answers[0]["text"]
           and not cb.answers[0]["show_alert"])

    run(case())
    env.restore()

    # invoys yuborishda Telegram xatosi
    env2 = patch_shop(products=prods,
                      invoice_exc=TelegramAPIError(method=None, message="flood"))

    async def fail_case():
        cb = FakeCallback(tg_user(555), "pay_xtr_1")
        await shop_mod.pay_with_xtr(cb)
        ok("invoys xatosi -> alert",
           cb.answers[0]["show_alert"] is True
           and cb.answers[0]["text"] == shop_mod.ALERT_INVOICE_FAILED)

    run(fail_case())
    env2.restore()

    # create_order xatosi
    env3 = patch_shop(products=prods, create_order_exc=shop_mod.RepositoryError("x"))

    def fail_case2():
        cb = FakeCallback(tg_user(555), "pay_xtr_1")
        run(shop_mod.pay_with_xtr(cb))
        ok("buyurtma xatosi -> alert",
           cb.answers[0]["show_alert"] is True
           and cb.answers[0]["text"] == shop_mod.ALERT_PURCHASE_FAILED)
        ok("invoys yuborilmadi", len(env3.state.invoice_calls) == 0)

    fail_case2()
    env3.restore()


def test_pre_checkout():
    print("== G. pre_checkout_query ==")
    orders = {7: make_order(7, uid=555, status="pending")}
    env = patch_shop(orders=orders)

    async def case():
        # ok holat
        q1 = FakePCQ(tg_user(555), "order:7:555")
        await shop_mod.on_pre_checkout(q1)
        ok("valid pending -> ok=True", q1.answers[0]["ok"] is True)

        # boshqa foydalanuvchi
        q2 = FakePCQ(tg_user(666), "order:7:555")
        await shop_mod.on_pre_checkout(q2)
        ok("egasi emas -> ok=False",
           q2.answers[0]["ok"] is False and q2.answers[0]["error_message"])

        # noto'g'ri payload
        q3 = FakePCQ(tg_user(555), "garbage")
        await shop_mod.on_pre_checkout(q3)
        ok("noto'g'ri payload -> ok=False", q3.answers[0]["ok"] is False)

        # buyurtma topilmadi
        q4 = FakePCQ(tg_user(555), "order:42:555")
        await shop_mod.on_pre_checkout(q4)
        ok("yo'q buyurtma -> ok=False", q4.answers[0]["ok"] is False)

        # allaqachon to'langan
        env.restore()
        env2 = patch_shop(orders={9: make_order(9, uid=555, status="paid")})
        q5 = FakePCQ(tg_user(555), "order:9:555")
        await shop_mod.on_pre_checkout(q5)
        env2.restore()
        ok("paid buyurtma -> ok=False", q5.answers[0]["ok"] is False)

    run(case())
    env.restore()


def test_successful_payment():
    print("== H. successful_payment (chek + admin) ==")
    orders = {7: make_order(7, uid=555, pid=1, status="pending")}
    prods = {1: make_product(1)}
    bot = FakeBot()
    env = patch_shop(orders=orders, products=prods)

    async def case():
        msg = FakeMessage(tg_user(555, "Ali", "ali_123"), bot=bot)
        msg.successful_payment = SimpleNamespace(
            total_amount=100,
            telegram_payment_charge_id="chg_abc",
            currency="XTR",
            invoice_payload="order:7:555",
        )
        await shop_mod.on_successful_payment(msg)
        ok("complete_order charge bilan",
           env.state.complete_calls == [(7, "chg_abc")])
        a = msg.answers[0]["text"]
        ok("chek matni: sarlavha", "To'lov muvaffaqiyatli qabul qilindi" in a)
        ok("chek matni: buyurtma ID", "Buyurtma ID: #7" in a, a)
        ok("chek matni: mahsulot", "100 Stars" in a)
        ok("chek matni: summa", "100 XTR" in a)
        ok("chek matni: yetkaziladi",
           "tez orada yetkaziladi yoki hisobingizga biriktiriladi" in a)
        ok("HTML parse_mode", msg.answers[0].get("parse_mode") == "HTML")
        ok("adminga xabarnoma (999001)",
           any(cid == 999001 for cid, _ in bot.sent), str(bot.sent))
        admin_text = bot.sent[0][1]
        ok("admin matni: foydalanuvchi", "@ali_123 (ID: 555)" in admin_text,
           admin_text)
        ok("admin matni: mahsulot", "Mahsulot: 100 Stars" in admin_text)
        ok("admin matni: summa", "Summa: 100 Stars" in admin_text)
        ok("admin matni: sarlavha", "Yangi xarid amalga oshirildi" in admin_text)

        # noto'g'ri payload -> fallback xabar, complete chaqirilmaydi
        env.state.complete_calls.clear()
        msg2 = FakeMessage(tg_user(555), bot=bot)
        msg2.successful_payment = SimpleNamespace(
            total_amount=100, telegram_payment_charge_id="chg_x",
            currency="XTR", invoice_payload="garbage")
        await shop_mod.on_successful_payment(msg2)
        ok("noto'g'ri payload: fallback",
           "administrator tomonidan qo'lda tekshiriladi" in msg2.answers[0]["text"]
           and env.state.complete_calls == [])

        # complete_order xatosi -> fallback
        env.restore()
        env2 = patch_shop(orders=orders, products=prods,
                          complete_exc=shop_mod.RepositoryError("y"))
        msg3 = FakeMessage(tg_user(555), bot=bot)
        msg3.successful_payment = SimpleNamespace(
            total_amount=100, telegram_payment_charge_id="chg_y",
            currency="XTR", invoice_payload="order:7:555")
        await shop_mod.on_successful_payment(msg3)
        env2.restore()
        ok("complete xatosi: fallback xabar",
           "administrator tomonidan qo'lda tekshiriladi" in msg3.answers[0]["text"])

        # username'siz foydalanuvchi -> HTML escape admin xabarda
        bot.sent.clear()
        env3 = patch_shop(orders=orders, products=prods)
        msg4 = FakeMessage(tg_user(556, "<O'zger>", None), bot=bot)
        msg4.successful_payment = SimpleNamespace(
            total_amount=100, telegram_payment_charge_id="chg_z",
            currency="XTR", invoice_payload="order:7:556")
        await shop_mod.on_successful_payment(msg4)
        env3.restore()
        ok("ism HTML escape qilindi",
           "&lt;O&#x27;zger&gt;" in bot.sent[0][1], bot.sent[0][1])

    run(case())
    env.restore()


def test_pay_balance():
    print("== I. pay_balance_ (bot balansi) ==")
    prods = {1: make_product(1)}
    users = {555: SimpleNamespace(user_id=555, stars_balance=150)}
    env = patch_shop(products=prods, users=users)

    async def case():
        cb = FakeCallback(tg_user(555), "pay_balance_1")
        await shop_mod.pay_with_balance(cb)
        ok("atomik xarid chaqirildi",
           env.state.balance_order_calls == [(555, 1)], str(env.state.balance_order_calls))
        a = cb.message.answers[0]["text"]
        ok("chek: buyurtma", "Buyurtma ID: #200" in a, a)
        ok("chek: mahsulot", "100 Stars" in a)
        ok("chek: bot balansi", "bot balansidan" in a and "100 XTR" in a)
        ok("chek: qolgan balans", "Qolgan balans: 50 ⭐️" in a, a)
        ok("javob: muvaffaqiyat",
           cb.answers[0]["text"] == "✅ Xarid muvaffaqiyatli!"
           and not cb.answers[0]["show_alert"])

    run(case())
    env.restore()

    # balans yetarli emas
    users2 = {555: SimpleNamespace(user_id=555, stars_balance=30)}
    env2 = patch_shop(products=prods, users=users2)

    async def poor_case():
        cb = FakeCallback(tg_user(555), "pay_balance_1")
        await shop_mod.pay_with_balance(cb)
        ok("yetarli emas -> aniq alert matni",
           cb.answers[0]["show_alert"] is True
           and cb.answers[0]["text"] == shop_mod.ALERT_NOT_ENOUGH_BALANCE,
           str(cb.answers))
        ok("xarid chaqirilmadi", env2.state.balance_order_calls == [])
        ok("to'ldirish tugmasi yuborildi",
           len(cb.message.answers) == 1
           and "Balansingizda Stars yetarli emas" in cb.message.answers[0]["text"]
           and "topup_start" in kb_callbacks(
               cb.message.answers[0]["reply_markup"]
           ))

    run(poor_case())
    env2.restore()

    # race: InsufficientBalanceError
    env3 = patch_shop(products=prods, users=users,
                      create_balance_exc=shop_mod.InsufficientBalanceError("x"))

    async def race_case():
        cb = FakeCallback(tg_user(555), "pay_balance_1")
        await shop_mod.pay_with_balance(cb)
        ok("race InsufficientBalanceError -> alert",
           cb.answers[0]["show_alert"] is True
           and cb.answers[0]["text"] == shop_mod.ALERT_NOT_ENOUGH_BALANCE)

    run(race_case())
    env3.restore()

    # RepositoryError
    env4 = patch_shop(products=prods, users=users,
                      create_balance_exc=shop_mod.RepositoryError("x"))

    async def err_case():
        cb = FakeCallback(tg_user(555), "pay_balance_1")
        await shop_mod.pay_with_balance(cb)
        ok("RepositoryError -> alert",
           cb.answers[0]["show_alert"] is True
           and cb.answers[0]["text"] == shop_mod.ALERT_PURCHASE_FAILED)

    run(err_case())
    env4.restore()

    # user bazada yo'q
    env5 = patch_shop(products=prods, users={})

    async def nouser_case():
        cb = FakeCallback(tg_user(555), "pay_balance_1")
        await shop_mod.pay_with_balance(cb)
        ok("user topilmadi -> /start alert",
           cb.answers[0]["show_alert"] is True
           and "/start" in cb.answers[0]["text"])

    run(nouser_case())
    env5.restore()

    # o'chirilgan mahsulot
    env6 = patch_shop(products={3: make_product(3, active=False)}, users=users)

    async def off_case():
        cb = FakeCallback(tg_user(555), "pay_balance_3")
        await shop_mod.pay_with_balance(cb)
        ok("o'chirilgan mahsulot -> alert",
           cb.answers[0]["text"] == shop_mod.ALERT_PRODUCT_NOT_FOUND)

    run(off_case())
    env6.restore()


def main() -> None:
    test_payments_service()
    test_payment_methods_kb()
    test_shop_menu()
    test_shop_category()
    test_buy_product()
    test_pay_xtr()
    test_pre_checkout()
    test_successful_payment()
    test_pay_balance()
    print(f"\nBARCHA TESTLAR O'TDI: {PASSED} ta tekshiruv")


if __name__ == "__main__":
    main()
