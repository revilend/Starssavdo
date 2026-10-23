"""
tests/test_throttling.py
========================
ThrottlingMiddleware regressiya testlari.

Asosiy talab: inline menyu tugmalari (CallbackQuery) GLOBAL cooldown'ga
tushmasligi kerak — foydalanuvchi turli tugmalarni ketma-ket bossa,
hammasi ishlashi shart. Faqat AYNAN bir xil tugmaning dublikati
(0.5 soniya ichida) rad etiladi.

Ishga tushirish (repo root'dan):
    pip install aiogram>=3.13.0
    python tests/test_throttling.py
"""

import asyncio
import os
import sys
import time
from types import SimpleNamespace

os.environ.update({
    "BOT_TOKEN": "123:TEST",
    "ADMIN_IDS": "999001",
    "DATABASE_URL": "postgresql://user:pass@host:5432/postgres",
})
sys.path.insert(0, ".")

from aiogram.types import CallbackQuery, User  # noqa: E402

from middlewares.throttling import (  # noqa: E402
    CALLBACK_COOLDOWN,
    DEFAULT_COOLDOWN,
    ThrottlingMiddleware,
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
    """`callback.answer()` chaqiruvlarini yozib oluvchi stub bot."""

    def __init__(self):
        self.answers: list[str | None] = []

    async def __call__(self, method, *args, **kwargs):
        # aiogram metodlari `bot(method)` orqali yuboriladi
        if type(method).__name__ == "AnswerCallbackQuery":
            self.answers.append(method.text)
        return True

    async def answer_callback_query(self, callback_query_id=None, text=None, **kwargs):
        self.answers.append(text)
        return True


def make_callback(data: str, uid: int = 555) -> CallbackQuery:
    """Haqiqiy aiogram CallbackQuery (bot ulangan)."""
    callback = CallbackQuery(
        id="1",
        from_user=User(id=uid, is_bot=False, first_name="User"),
        chat_instance="ci",
        data=data,
    )
    return callback.as_(FakeBot())


class FakeMessage:
    """Message — throttling uchun faqat matn muhim."""

    def __init__(self, text: str, uid: int = 555):
        self.text = text
        self.from_user = SimpleNamespace(id=uid, is_bot=False, first_name="User")


def run(coro):
    return asyncio.run(coro)


# =============================================================
# Testlar
# =============================================================
def test_callback_not_globally_throttled() -> None:
    print("== Callback: turli tugmalar ketma-ket ==")
    mw = ThrottlingMiddleware()
    handled: list[str] = []

    async def handler(event, data):
        handled.append(event.data)
        return "HANDLED"

    async def scenario():
        # 1) 4 ta turli toifa tugmasi DARHOL ketma-ket bosiladi
        for data in ("cat_stars", "cat_premium", "cat_nft", "cat_gift"):
            cb = make_callback(data)
            res = await mw(handler, cb, {"event_from_user": cb.from_user})
            ok(f"{data} -> o'tdi", res == "HANDLED" and cb.bot.answers == [])
        ok("turli tugmalar hammasi ishladi", handled == [
            "cat_stars", "cat_premium", "cat_nft", "cat_gift"], str(handled))

        # 2) Navigatsiya ham ishlaydi (orqaga / bosh menyu)
        for data in ("shop_categories", "back_to_menu"):
            cb = make_callback(data)
            res = await mw(handler, cb, {"event_from_user": cb.from_user})
            ok(f"{data} -> o'tdi", res == "HANDLED")

    run(scenario())


def test_update_wrapped_callback() -> None:
    print("== Update ichidagi callback (production yo'li) ==")
    mw = ThrottlingMiddleware()

    async def handler(event, data):
        return "HANDLED"

    async def scenario():
        cb = make_callback("cat_stars")
        # dp.update.outer_middleware kabi: event = Update
        update = SimpleNamespace(callback_query=cb, event_from_user=cb.from_user)
        res = await mw(handler, update, {"event_from_user": cb.from_user})
        ok("Update orqali callback o'tdi", res == "HANDLED")

        # Dublikat — aynan o'sha tugma
        cb2 = make_callback("cat_stars")
        update2 = SimpleNamespace(callback_query=cb2, event_from_user=cb2.from_user)
        res2 = await mw(handler, update2, {"event_from_user": cb2.from_user})
        ok("Update orqali dublikat rad etildi", res2 is None)
        ok("dublikatga toast berildi", cb2.bot.answers == ["⏱ Biroz kuting…"])

    run(scenario())


def test_identical_callback_deduplicated() -> None:
    print("== Callback: aynan bir xil tugma dublikati ==")
    mw = ThrottlingMiddleware()
    handled: list[str] = []

    async def handler(event, data):
        handled.append(event.data)
        return "HANDLED"

    async def scenario():
        first = make_callback("buy_prod_1")
        res1 = await mw(handler, first, {"event_from_user": first.from_user})
        ok("1-bosish o'tdi", res1 == "HANDLED")

        # Dublikat (0.5 s ichida) — rad etiladi, spinner yopiladi
        dup = make_callback("buy_prod_1")
        res2 = await mw(handler, dup, {"event_from_user": dup.from_user})
        ok("dublikat rad etildi", res2 is None and handled == ["buy_prod_1"])
        ok("dublikatga toast berildi", dup.bot.answers == ["⏱ Biroz kuting…"])

        # Boshqa tugma esa o'sha zahoti ishlaydi
        other = make_callback("buy_prod_2")
        res3 = await mw(handler, other, {"event_from_user": other.from_user})
        ok("boshqa tugma ishladi", res3 == "HANDLED" and other.bot.answers == [])

    run(scenario())


def test_callback_allowed_after_cooldown() -> None:
    print("== Callback: cooldown o'tgach qayta ishlaydi ==")
    mw = ThrottlingMiddleware(callback_cooldown=0.1)

    async def handler(event, data):
        return "HANDLED"

    async def scenario():
        cb1 = make_callback("pay_balance_5")
        ok("1-bosish",
           await mw(handler, cb1, {"event_from_user": cb1.from_user}) == "HANDLED")

        started = time.monotonic()
        while time.monotonic() - started < 0.12:
            await asyncio.sleep(0.02)

        cb2 = make_callback("pay_balance_5")
        ok("cooldown o'tdi -> yana ishlaydi",
           await mw(handler, cb2, {"event_from_user": cb2.from_user}) == "HANDLED")

    run(scenario())


def test_message_cooldown() -> None:
    print("== Message: flood himoyasi saqlanadi ==")
    mw = ThrottlingMiddleware(cooldown=DEFAULT_COOLDOWN)
    handled: list[str] = []

    async def handler(event, data):
        handled.append(event.text)
        return "HANDLED"

    async def scenario():
        m1 = FakeMessage("salom")
        ok("1-xabar o'tdi",
           await mw(handler, m1, {"event_from_user": m1.from_user}) == "HANDLED")

        m2 = FakeMessage("yana salom")
        ok("tezkor 2-xabar rad etildi",
           await mw(handler, m2, {"event_from_user": m2.from_user}) is None)
        ok("faqat 1-xabar handler'ga yetdi", handled == ["salom"])

        # Admin — cheklovsiz
        m3 = FakeMessage("admin xabari", uid=999001)
        ok("admin cheklovsiz",
           await mw(handler, m3, {"event_from_user": m3.from_user}) == "HANDLED")

        # Foydalanuvchi aniqlanmagan hodisa — cheklovsiz
        m4 = FakeMessage("kanal posti")
        ok("user yo'q -> cheklovsiz",
           await mw(handler, m4, {}) == "HANDLED")

    run(scenario())


def test_constants() -> None:
    print("== Konstantalar ==")
    ok("message cooldown 0.7 s", DEFAULT_COOLDOWN == 0.7)
    ok("callback dublikat cooldown 0.5 s", CALLBACK_COOLDOWN == 0.5)
    ok("callback cooldown message'dan kichik", CALLBACK_COOLDOWN < DEFAULT_COOLDOWN)


if __name__ == "__main__":
    test_callback_not_globally_throttled()
    test_update_wrapped_callback()
    test_identical_callback_deduplicated()
    test_callback_allowed_after_cooldown()
    test_message_cooldown()
    test_constants()
    print(f"\nBARCHA TESTLAR O'TDI: {PASSED} ta tekshiruv")
