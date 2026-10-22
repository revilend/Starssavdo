"""
OP obuna tekshiruvi: servis + middleware + handler testlari.

Ishga tushirish (repo root'dan):
    pip install aiogram>=3.13.0 cachetools
    python tests/test_subscription.py
"""

import asyncio
import os
import sys
from types import SimpleNamespace

os.environ.update({
    "BOT_TOKEN": "123:TEST",
    "ADMIN_IDS": "999001,999002",
    "DATABASE_URL": "postgresql://user:pass@host:5432/postgres",
})
sys.path.insert(0, ".")

from config.settings import settings  # noqa: E402

# Boshqa test moduli `settings` singletonini avvalroq yuklagan bo'lishi mumkin —
# test izchil bo'lishi uchun admin ro'yxatini aniq o'rnatamiz.
settings.ADMIN_IDS = [999001, 999002]

from aiogram.exceptions import (  # noqa: E402
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    Chat,
    InaccessibleMessage,
    User,
)

import handlers.user.subscription as sub_mod  # noqa: E402
import middlewares.subscription as mw_mod  # noqa: E402
import services.channel_checker as cc  # noqa: E402
from database.queries import ReferralReward  # noqa: E402
from middlewares.subscription import (  # noqa: E402
    SUBSCRIPTION_PROMPT_TEXT,
    SubscriptionMiddleware,
)

PASSED = 0


def ok(label: str, cond: bool, extra: str = "") -> None:
    global PASSED
    if not cond:
        raise AssertionError(f"FAILED: {label} {extra}")
    PASSED += 1
    print(f"  ok: {label}")


# =============================================================
# Test yordamchilari
# =============================================================
class FakeBot:
    """get_chat_member ni imitatsiya qiluvchi stub (AI chaqiruvlarini sanaydi)."""

    def __init__(self, statuses: dict | None = None, errors: dict | None = None):
        self.statuses = statuses or {}
        self.errors = errors or {}
        self.calls: list[tuple[int, int]] = []
        self.sent: list[dict] = []
        self.api_calls: list = []

    async def __call__(self, method, *args, **kwargs):
        # TelegramObject ichki API chaqiruvlari (callback.answer va h.k.)
        self.api_calls.append(method)
        return method

    async def get_chat_member(self, chat_id: int, user_id: int):
        self.calls.append((chat_id, user_id))
        exc = self.errors.get(chat_id)
        if exc is not None:
            raise exc
        return SimpleNamespace(status=self.statuses.get(chat_id, "member"))

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})


class FakeEvent:
    """Message ga o'xshash hodisa (answer yozuvlari bilan)."""

    def __init__(self, user: User):
        self.from_user = user
        self.answers: list[dict] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append({"text": text, **kwargs})


class FakeMessage:
    def __init__(self):
        self.deleted = 0
        self.answers: list[dict] = []
        self.markups: list = []

    async def delete(self):
        self.deleted += 1

    async def answer(self, text: str, **kwargs):
        self.answers.append({"text": text, **kwargs})

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        self.markups.append(reply_markup)


class FakeCallback:
    """CallbackQuery ga o'xshash duck-typed hodisa."""

    def __init__(self, user: User, bot: FakeBot, message: FakeMessage | None = None):
        self.from_user = user
        self.bot = bot
        self.message = message or FakeMessage()
        self.answers: list[dict] = []

    async def answer(self, text: str | None = None, show_alert: bool = False, **kwargs):
        self.answers.append({"text": text, "show_alert": show_alert})


def tg_user(uid: int, name: str = "User") -> User:
    return User(id=uid, is_bot=False, first_name=name)


def make_message_event(user: User) -> FakeEvent:
    return FakeEvent(user)


def make_real_callback(data: str, uid: int = 42) -> CallbackQuery:
    return CallbackQuery(
        id="1",
        from_user=tg_user(uid),
        chat_instance="ci",
        data=data,
        message=InaccessibleMessage(
            message_id=5, chat=Chat(id=uid, type="private"), date=0
        ),
    )


def run(coro):
    return asyncio.run(coro)


def fresh_channels_cache():
    cc.clear_subscription_cache()
    cc.invalidate_channels_cache()


# =============================================================
# 1. SERVIS — check_user_subscription
# =============================================================
def test_service() -> None:
    print("== services/channel_checker ==")
    chans = [
        {"chat_id": -1001, "title": "Kanal1", "invite_link": "https://t.me/+a"},
        {"chat_id": -1002, "title": "Kanal2", "invite_link": "https://t.me/+b"},
        {"chat_id": -1003, "title": "Kanal3", "invite_link": "https://t.me/+c"},
        {"chat_id": -1004, "title": "Kanal4", "invite_link": "https://t.me/+d"},
    ]

    async def scenario():
        fresh_channels_cache()
        # 1) barcha "a'zo" statuslar
        bot = FakeBot(statuses={
            -1001: "creator", -1002: "administrator",
            -1003: "member", -1004: "restricted",
        })
        is_sub, unj = await cc.check_user_subscription(bot, 1, chans)
        ok("creator/admin/member/restricted -> a'zo", is_sub and unj == [])

        # 2) bittasi 'left'
        fresh_channels_cache()
        bot2 = FakeBot(statuses={-1001: "member", -1002: "left",
                                 -1003: "member", -1004: "member"})
        is_sub, unj = await cc.check_user_subscription(bot2, 2, chans)
        ok("(False, [Kanal2])", not is_sub and [c["title"] for c in unj] == ["Kanal2"])

        # 3) kesh: takroriy tekshiruv API ga siklanmaydi
        calls_before = len(bot2.calls)
        await cc.check_user_subscription(bot2, 2, chans)
        ok("kesh: qo'shimcha API chaqiruvi yo'q", len(bot2.calls) == calls_before)

        # 4) TelegramForbiddenError (bot blocklangan) -> a'zo emas + kesh
        fresh_channels_cache()
        bot3 = FakeBot(errors={-1001: TelegramForbiddenError(
            method=None, message="Forbidden: bot was blocked by the user")})
        is_sub, unj = await cc.check_user_subscription(bot3, 3, chans)
        ok("ForbiddenError -> 1 ta kanal a'zo emas", not is_sub and len(unj) == 1)
        n = len(bot3.calls)
        await cc.check_user_subscription(bot3, 3, chans)
        ok("xato ham keshlandi", len(bot3.calls) == n)

        # 5) TelegramBadRequest (ChatNotFound)
        fresh_channels_cache()
        bot4 = FakeBot(errors={-1002: TelegramBadRequest(
            method=None, message="Bad Request: chat not found")})
        is_sub, unj = await cc.check_user_subscription(bot4, 4, chans)
        ok("BadRequest -> a'zo emas", not is_sub and len(unj) == 1)

        # 6) TelegramRetryAfter -> kutib qayta urinadi
        fresh_channels_cache()

        class FlakyBot(FakeBot):
            def __init__(self):
                super().__init__()
                self.flaky = True

            async def get_chat_member(self, chat_id, user_id):
                self.calls.append((chat_id, user_id))
                if self.flaky and chat_id == -1001:
                    self.flaky = False
                    raise TelegramRetryAfter(
                        method=None, message="Too Many Requests", retry_after=0
                    )
                return SimpleNamespace(status="member")

        bot5 = FlakyBot()
        is_sub, _ = await cc.check_user_subscription(bot5, 5, chans)
        ok("RetryAfter -> retry keyin a'zo", is_sub)

        # 7) ORM-obyektlar ham qabul qilinadi
        fresh_channels_cache()
        orm_channels = [
            SimpleNamespace(id=1, chat_id=-2001, title="ORM1",
                            invite_link="https://t.me/+x", username=None, is_active=True),
        ]
        bot6 = FakeBot(statuses={-2001: "member"})
        is_sub, _ = await cc.check_user_subscription(bot6, 6, orm_channels)
        ok("ORM Channel obyektlari bilan ishlaydi", is_sub)

    run(scenario())


# =============================================================
# 2. SERVIS — get_active_channel_dicts (+kesh)
# =============================================================
def test_active_channels() -> None:
    print("== services: get_active_channel_dicts ==")
    calls = {"n": 0}

    async def fake_get_active_channels():
        calls["n"] += 1
        return [
            SimpleNamespace(id=1, chat_id=-1001, title="Kanal1",
                            invite_link="https://t.me/+a", username="k1", is_active=True),
        ]

    original = cc.ChannelRepository.get_active_channels
    cc.ChannelRepository.get_active_channels = staticmethod(fake_get_active_channels)
    try:
        fresh_channels_cache()

        async def scenario():
            data = await cc.get_active_channel_dicts()
            ok("dict ko'rinishiga o'tdi", data[0]["chat_id"] == -1001
               and data[0]["title"] == "Kanal1" and set(data[0]) >= {"chat_id", "invite_link", "title"})
            await cc.get_active_channel_dicts()
            ok("kanallar keshlandi (1 DB so'rov)", calls["n"] == 1)
            cc.invalidate_channels_cache()
            await cc.get_active_channel_dicts()
            ok("invalidate dan keyin qayta so'rov", calls["n"] == 2)

        run(scenario())
    finally:
        cc.ChannelRepository.get_active_channels = original
        fresh_channels_cache()


# =============================================================
# 3. MIDDLEWARE
# =============================================================
def test_middleware() -> None:
    print("== middlewares/subscription ==")
    mw = SubscriptionMiddleware()

    async def dummy_handler(event, data):
        data["called"] = True
        return "HANDLED"

    def channels_stub(n: int = 2):
        async def _stub():
            return [{"chat_id": -1000 - i, "title": f"K{i}",
                     "invite_link": f"https://t.me/+{i}"} for i in range(1, n + 1)]
        return _stub

    def run_case(uid, statuses, data_extra=None, event=None, channels=None):
        async def _run():
            fresh_channels_cache()
            orig = mw_mod.get_active_channel_dicts
            mw_mod.get_active_channel_dicts = channels or channels_stub()
            try:
                bot = FakeBot(statuses=statuses)
                ev = event or make_message_event(tg_user(uid))
                data = {"bot": bot, "event_from_user": ev.from_user}
                data.update(data_extra or {})
                result = await mw.__call__(dummy_handler, ev, data)
                return result, bot, ev, data
            finally:
                mw_mod.get_active_channel_dicts = orig
                fresh_channels_cache()
        return asyncio.run(_run())

    # A) user yo'q -> o'tkaziladi
    async def case_a():
        fresh_channels_cache()
        orig = mw_mod.get_active_channel_dicts
        mw_mod.get_active_channel_dicts = channels_stub()
        try:
            ev = make_message_event(tg_user(1))
            data = {"bot": FakeBot(), "called": False}
            res = await mw.__call__(dummy_handler, ev, data)  # event_from_user yo'q
            ok("user yo'q -> o'tkazildi", res == "HANDLED")
        finally:
            mw_mod.get_active_channel_dicts = orig
    run(case_a())

    # B) admin -> tekshiruvsiz
    res, bot, ev, data = run_case(999001, statuses={})
    ok("admin -> tekshiruvsiz o'tdi", res == "HANDLED" and not bot.calls)

    # C) check_subscription callback -> bloklanmaydi
    cb = make_real_callback("check_subscription", uid=77)
    res, bot, ev, data = run_case(77, statuses={}, event=cb)
    ok("check_subscription bloklanmadi", res == "HANDLED")

    # D) boshqa callback -> obuna bo'lmasa bloklanadi (real CallbackQuery)

    async def case_d():
        fresh_channels_cache()
        orig = mw_mod.get_active_channel_dicts
        mw_mod.get_active_channel_dicts = channels_stub(2)
        try:
            bot = FakeBot(statuses={-1001: "member", -1002: "left"})
            cb2 = make_real_callback("buy_prod_5", uid=78).as_(bot)
            data = {"bot": bot, "event_from_user": cb2.from_user, "called": False}
            res = await mw.__call__(dummy_handler, cb2, data)
            ok("a'zo bo'lmagan callback -> bloklandi",
               res is None and not data.get("called"))
            ok("API chaqirildi", len(bot.calls) == 2)
            ok("callback spinner yopildi", len(bot.api_calls) == 1)
            ok("prompt kanal chatiga yuborildi", len(bot.sent) == 1
               and bot.sent[0]["chat_id"] == 78
               and bot.sent[0]["text"] == SUBSCRIPTION_PROMPT_TEXT)
        finally:
            mw_mod.get_active_channel_dicts = orig
            fresh_channels_cache()
    run(case_d())

    # E) a'zo bo'lmagan Message -> prompt yuboriladi
    res, bot, ev, data = run_case(101, statuses={-1001: "left", -1002: "member"})
    ok("a'zo emas -> bloklandi (None)", res is None and not data.get("called"))
    ok("prompt yuborildi", len(ev.answers) == 1
       and ev.answers[0]["text"] == SUBSCRIPTION_PROMPT_TEXT)
    kb = ev.answers[0]["reply_markup"]
    flat = [b for row in kb.inline_keyboard for b in row]
    ok("faqat a'zo bo'lmagan kanal + tekshiruv", len(flat) == 2
       and flat[0].url == "https://t.me/+1" and flat[1].callback_data == "check_subscription")

    # F) to'liq a'zo -> o'tkaziladi
    res, bot, ev, data = run_case(102, statuses={-1001: "member", -1002: "member"})
    ok("to'liq a'zo -> HANDLED", res == "HANDLED")

    # G) majburiy kanal yo'q -> erkin
    async def _empty_channels():
        return []

    res, bot, ev, data = run_case(103, statuses={}, channels=_empty_channels)
    ok("kanallar bo'sh -> erkin foydalanish", res == "HANDLED")


# =============================================================
# 4. HANDLER — check_subscription
# =============================================================
def test_handler() -> None:
    print("== handlers/user/subscription ==")

    def patch(subscribed: bool, reward=None, channels=None, raise_confirm=False):
        async def _channels():
            return channels if channels is not None else [
                {"chat_id": -1001, "title": "K1", "invite_link": "https://t.me/+1"},
            ]

        async def _check(bot, uid, ch):
            return (True, []) if subscribed else (False, ch)

        verified = []

        async def _set_verified(uid):
            verified.append(uid)

        async def _confirm(uid):
            if raise_confirm:
                raise RuntimeError("db down")
            return reward

        class UR:
            set_channels_verified = staticmethod(_set_verified)

        class RR:
            confirm_referral_reward = staticmethod(_confirm)

        saved = (sub_mod.get_active_channel_dicts, sub_mod.check_user_subscription,
                 sub_mod.UserRepository, sub_mod.ReferralRepository,
                 sub_mod.settings.ADMIN_IDS)
        sub_mod.get_active_channel_dicts = _channels
        sub_mod.check_user_subscription = _check
        sub_mod.UserRepository = UR
        sub_mod.ReferralRepository = RR
        sub_mod.settings.ADMIN_IDS = [999001]

        def restore():
            (sub_mod.get_active_channel_dicts, sub_mod.check_user_subscription,
             sub_mod.UserRepository, sub_mod.ReferralRepository,
             sub_mod.settings.ADMIN_IDS) = saved
        return verified, restore

    # 1) hali a'zo emas
    verified, restore = patch(subscribed=False)
    try:
        bot = FakeBot()
        cb = FakeCallback(tg_user(10, "Ali"), bot)
        run(sub_mod.cb_check_subscription(cb))
        ok("a'zo emas: alert matni", cb.answers and cb.answers[0] == {
            "text": "❌ Hali barcha kanallarga a'zo bo'lmadingiz!", "show_alert": True})
        ok("a'zo emas: verified chaqirilmadi", verified == [])
        ok("a'zo emas: klaviatura yangilandi",
           len(cb.message.markups) == 1 and cb.message.markups[0] is not None)
        ok("a'zo emas: menyu yuborilmadi", cb.message.answers == [])
    finally:
        restore()

    # 2) a'zo, referral yo'q
    verified, restore = patch(subscribed=True, reward=None)
    try:
        bot = FakeBot()
        cb = FakeCallback(tg_user(10, "Ali"), bot)
        run(sub_mod.cb_check_subscription(cb))
        ok("verified qo'yildi", verified == [10])
        ok("xabar o'chirildi", cb.message.deleted == 1)
        ok("mukofot xabari yo'q", bot.sent == [])
        ok("menyu yuborildi", len(cb.message.answers) == 1
           and "Assalomu alaykum, Ali!" in cb.message.answers[0]["text"]
           and cb.message.answers[0]["reply_markup"] is not None)
        ok("oddiy foydalanuvchida admin tugma yo'q",
           all(b.text != "⚙️ Admin Panel"
               for row in cb.message.answers[0]["reply_markup"].keyboard for b in row))
    finally:
        restore()

    # 3) a'zo + referal mukofoti
    reward = ReferralReward(referral_id=1, referrer_id=555, username="ref",
                            first_name="Ref", reward_amount=5)
    verified, restore = patch(subscribed=True, reward=reward)
    try:
        bot = FakeBot()
        cb = FakeCallback(tg_user(10, "Ali"), bot)
        run(sub_mod.cb_check_subscription(cb))
        ok("referrerga xabar ketdi", len(bot.sent) == 1 and bot.sent[0]["chat_id"] == 555)
        ok("matnda +5 Stars", "+5 ⭐️ Stars" in bot.sent[0]["text"]
           and bot.sent[0]["parse_mode"] == "HTML")
    finally:
        restore()

    # 4) confirm_referral_reward xato bersa ham handler tugaydi
    verified, restore = patch(subscribed=True, raise_confirm=True)
    try:
        bot = FakeBot()
        cb = FakeCallback(tg_user(10, "Ali"), bot)
        run(sub_mod.cb_check_subscription(cb))
        ok("confirm xatosi handlerni buzmadi", verified == [10] and len(cb.message.answers) == 1)
    finally:
        restore()

    # 5) admin uchun menyu ustida Admin Panel tugmasi bor
    verified, restore = patch(subscribed=True)
    try:
        bot = FakeBot()
        cb = FakeCallback(tg_user(999001, "Boss"), bot)
        run(sub_mod.cb_check_subscription(cb))
        kb = cb.message.answers[0]["reply_markup"]
        ok("admin menyusida ⚙️ Admin Panel bor",
           any(b.text == "⚙️ Admin Panel" for row in kb.keyboard for b in row))
    finally:
        restore()

    # 6) router ro'yxatdan o'tgan
    ok("router nomi", sub_mod.router.name == "user_subscription")


if __name__ == "__main__":
    test_service()
    test_active_channels()
    test_middleware()
    test_handler()
    print(f"\nBARCHA TESTLAR O'TDI: {PASSED} ta tekshiruv")
