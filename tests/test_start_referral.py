"""
/start va referal tizimi handlerlari testlari.

Ishga tushirish (repo root'dan):
    pip install aiogram>=3.13.0 cachetools
    python tests/test_start_referral.py
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

from aiogram.filters import CommandObject  # noqa: E402

import handlers.user.referral as ref_mod  # noqa: E402
import handlers.user.start as start_mod  # noqa: E402
from database.queries import VelocityCheck  # noqa: E402

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
    def __init__(self, username: str = "nftyutbot"):
        self.username = username

    async def get_me(self):
        return SimpleNamespace(username=self.username, id=1)


class FakeMessage:
    def __init__(self, user, bot: FakeBot | None = None):
        self.from_user = user
        self.bot = bot or FakeBot()
        self.answers: list[dict] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append({"text": text, **kwargs})


class FakeCallback:
    def __init__(self, user, bot: FakeBot | None = None):
        self.from_user = user
        self.bot = bot or FakeBot()
        self.message = FakeMessage(user, self.bot)
        self.answers: list[dict] = []

    async def answer(self, text: str | None = None, show_alert: bool = False, **kwargs):
        self.answers.append({"text": text, "show_alert": show_alert})


def tg_user(uid: int, first_name: str = "User", username: str | None = None):
    return SimpleNamespace(id=uid, is_bot=False, first_name=first_name,
                           last_name=None, username=username)


def run(coro):
    return asyncio.run(coro)


# =============================================================
# Fake repository'lar
# =============================================================
def make_user_repo(users: dict[int, SimpleNamespace]):
    created: list[dict] = []

    class FakeUserRepo:
        @staticmethod
        async def get_user(uid: int):
            return users.get(uid)

        @staticmethod
        async def get_or_create_user(**kw):
            created.append(kw)
            return SimpleNamespace(user_id=kw["user_id"], referrer_id=kw.get("referrer_id"))

    return FakeUserRepo, created


def make_referral_repo():
    referrals: list[tuple[int, int]] = []
    velocity = VelocityCheck(count=0, flagged=False)

    class FakeReferralRepo:
        @staticmethod
        async def check_referral_velocity(referrer_id: int, limit: int = 15):
            return velocity

        @staticmethod
        async def create_referral(referrer_id: int, referred_id: int):
            referrals.append((referrer_id, referred_id))
            return SimpleNamespace(status="pending")

        @staticmethod
        async def get_user_referrals_count(user_id: int):
            return SimpleNamespace(total=0, completed=0)

    return FakeReferralRepo, referrals


def patch_start(users=None, subscribed=True, channels=None, reward_referral=None):
    """start_mod uchun repository/servis fake'larini o'rnatadi."""
    user_repo, created = make_user_repo(users or {})
    ref_repo, referrals = make_referral_repo()
    checks: list[int] = []

    async def _channels():
        return channels if channels is not None else [
            {"chat_id": -1001, "title": "K1", "invite_link": "https://t.me/+1"},
        ]

    async def _check(bot, uid, ch):
        checks.append(uid)
        return (True, []) if subscribed else (False, ch)

    saved = (start_mod.UserRepository, start_mod.ReferralRepository,
             start_mod.get_active_channel_dicts, start_mod.check_user_subscription,
             start_mod.settings.ADMIN_IDS)
    start_mod.UserRepository = user_repo
    start_mod.ReferralRepository = ref_repo
    start_mod.get_active_channel_dicts = _channels
    start_mod.check_user_subscription = _check
    start_mod.settings.ADMIN_IDS = [999001]

    def restore():
        (start_mod.UserRepository, start_mod.ReferralRepository,
         start_mod.get_active_channel_dicts, start_mod.check_user_subscription,
         start_mod.settings.ADMIN_IDS) = saved

    return SimpleNamespace(created=created, referrals=referrals, checks=checks,
                           user_repo=user_repo, ref_repo=ref_repo, restore=restore)


def kb_texts(markup) -> list[str]:
    return [b.text for row in markup.keyboard for b in row]


def inline_flat(markup):
    return [b for row in markup.inline_keyboard for b in row]


# =============================================================
# 1. parse_referrer_id
# =============================================================
def test_parse_referrer() -> None:
    print("== start: parse_referrer_id ==")
    ok("ref_123 -> 123", start_mod.parse_referrer_id("ref_123") == 123)
    ok("bo'sh -> None", start_mod.parse_referrer_id(None) is None)
    ok("ref_abc -> None", start_mod.parse_referrer_id("ref_abc") is None)
    ok("prefiksiz 123 -> None", start_mod.parse_referrer_id("123") is None)
    ok("ref_0 -> None", start_mod.parse_referrer_id("ref_0") is None)
    ok("ref_-5 -> None", start_mod.parse_referrer_id("ref_-5") is None)
    ok("ref_ 7 -> 7 (bo'shliq)", start_mod.parse_referrer_id(" ref_7 ") == 7)


# =============================================================
# 2. /start handler
# =============================================================
def test_start() -> None:
    print("== handlers/user/start ==")
    args = CommandObject(command="start", args=None)

    # A) Yangi user, havolasiz, a'zo bo'lgan
    p = patch_start(subscribed=True)
    try:
        msg = FakeMessage(tg_user(10, "Ali"))
        run(start_mod.cmd_start(msg, args))
        ok("A: yangi user qo'shildi", len(p.created) == 1
           and p.created[0]["user_id"] == 10 and p.created[0]["referrer_id"] is None)
        ok("A: referal yozilmadi", p.referrals == [])
        ok("A: salomlashuv + menyu", len(msg.answers) == 1
           and "Assalomu alaykum, Ali!" in msg.answers[0]["text"]
           and msg.answers[0]["reply_markup"] is not None)
        ok("A: oddiy menyu (admin tugmasiz)",
           "⚙️ Admin Panel" not in kb_texts(msg.answers[0]["reply_markup"]))
        ok("A: HTML parse_mode", msg.answers[0]["parse_mode"] == "HTML")
    finally:
        p.restore()

    # B) Yangi user + ref_123 (referrer mavjud)
    p = patch_start(users={123: SimpleNamespace(user_id=123)}, subscribed=True)
    try:
        msg = FakeMessage(tg_user(11, "Bob"))
        run(start_mod.cmd_start(msg, CommandObject(command="start", args="ref_123")))
        ok("B: referrer saqlandi", p.created[0]["referrer_id"] == 123)
        ok("B: pending referal yozildi", p.referrals == [(123, 11)])
    finally:
        p.restore()

    # C) O'zini o'zi taklif qilish taqiqlangan
    p = patch_start(users={22: SimpleNamespace(user_id=22)}, subscribed=True)
    try:
        msg = FakeMessage(tg_user(22, "Self"))
        run(start_mod.cmd_start(msg, CommandObject(command="start", args="ref_22")))
        ok("C: o'zini taklif qilish -> referrer None", p.created[0]["referrer_id"] is None)
        ok("C: referal yozilmadi", p.referrals == [])
    finally:
        p.restore()

    # D) Referrer bazada yo'q
    p = patch_start(subscribed=True)  # users bo'sh
    try:
        msg = FakeMessage(tg_user(12, "Dan"))
        run(start_mod.cmd_start(msg, CommandObject(command="start", args="ref_99999")))
        ok("D: mavjud bo'lmagan referrer e'tibors qoldirildi",
           p.created[0]["referrer_id"] is None and p.referrals == [])
    finally:
        p.restore()

    # E) A'zo emas -> obuna prompti, salomlashuv yo'q
    p = patch_start(subscribed=False)
    try:
        msg = FakeMessage(tg_user(13, "Eve"))
        run(start_mod.cmd_start(msg, args))
        ok("E: prompt + kanallar kb", len(msg.answers) == 1
           and "homiy kanallarga a'zo bo'ling" in msg.answers[0]["text"]
           and inline_flat(msg.answers[0]["reply_markup"])[-1].callback_data == "check_subscription")
        ok("E: salomlashuv yo'q", "Assalomu alaykum" not in msg.answers[0]["text"])
    finally:
        p.restore()

    # F) Admin -> tekshiruvsiz, Admin Panel tugmasi bilan
    p = patch_start(subscribed=False)
    try:
        msg = FakeMessage(tg_user(999001, "Boss"))
        run(start_mod.cmd_start(msg, args))
        ok("F: admin kanal tekshiruvi o'tkazildi", p.checks == [])
        ok("F: Admin Panel tugmasi bor",
           "⚙️ Admin Panel" in kb_texts(msg.answers[0]["reply_markup"]))
    finally:
        p.restore()

    # G) Majburiy kanallar umuman yo'q -> erkin kirish
    p = patch_start(channels=[])
    try:
        msg = FakeMessage(tg_user(14, "Free"))
        run(start_mod.cmd_start(msg, args))
        ok("G: kanalsiz -> salomlashuv", "Assalomu alaykum" in msg.answers[0]["text"])
    finally:
        p.restore()

    # H) Mavjud user + havola: referrer to'ldiriladi, takroriy referal yozilmaydi
    p = patch_start(users={33: SimpleNamespace(user_id=33, referrer_id=None),
                           123: SimpleNamespace(user_id=123)},
                    subscribed=True)
    try:
        msg = FakeMessage(tg_user(33, "Mavjud"), )
        run(start_mod.cmd_start(msg, CommandObject(command="start", args="ref_123")))
        ok("H: referrer to'ldirildi", p.created[0]["referrer_id"] == 123)
        ok("H: referal yozuvi yaratildi", p.referrals == [(123, 33)])
    finally:
        p.restore()


# =============================================================
# 3. Referal bo'limi
# =============================================================
def patch_referral(reward=5, total=3, completed=2, top=None):
    class FakeSettings:
        @staticmethod
        async def get_setting(key, default=None):
            return reward

    class FakeRefRepo:
        @staticmethod
        async def get_user_referrals_count(uid):
            return SimpleNamespace(total=total, completed=completed)

        @staticmethod
        async def get_top_referrers(limit=10):
            return (top or [])[:limit]

    saved = (ref_mod.SettingsRepository, ref_mod.ReferralRepository,
             ref_mod._bot_username_cache)
    ref_mod.SettingsRepository = FakeSettings
    ref_mod.ReferralRepository = FakeRefRepo
    ref_mod._bot_username_cache = {}

    def restore():
        (ref_mod.SettingsRepository, ref_mod.ReferralRepository,
         ref_mod._bot_username_cache) = saved

    return restore


def test_referral_menu() -> None:
    print("== handlers/user/referral: menyu ==")
    restore = patch_referral(reward=5, total=3, completed=2)
    try:
        msg = FakeMessage(tg_user(42, "Ali"))
        run(ref_mod.referral_menu(msg))
        post = msg.answers[0]
        text = post["text"]
        ok("matn: BO'LIM sarlavhasi", "DO'STLARNI TAKLIF QILISH DASTURI" in text)
        ok("matn: reward +5", "+5 ⭐️ Stars" in text)
        ok("matn: havola", "https://t.me/nftyutbot?start=ref_42" in text)
        ok("matn: ko'rsatkichlar (3 ta / 10⭐️)", "Jami takliflar: 3 ta" in text
           and "Ishlangan Stars: 10 ⭐️" in text)
        ok("HTML parse_mode + link preview o'chiq",
           post["parse_mode"] == "HTML"
           and post["link_preview_options"].is_disabled is True)
        flat = inline_flat(post["reply_markup"])
        ok("tugmalar: ulashish + top + bosh menyu", len(flat) == 3
           and flat[0].text == "📲 Do'stlarga ulashish"
           and flat[1].callback_data == "top_referrers"
           and flat[2].callback_data == "back_to_menu")
        ok("ulashish matnida havola bor",
           flat[0].switch_inline_query is not None
           and "t.me/nftyutbot?start=ref_42" in flat[0].switch_inline_query)
    finally:
        restore()


def test_top_referrers() -> None:
    print("== handlers/user/referral: top-10 ==")
    top = [
        SimpleNamespace(user_id=1, username="best", first_name="Best", completed=30),
        SimpleNamespace(user_id=2, username=None, second=None, first_name="Ikkinchi", completed=12),
        SimpleNamespace(user_id=3, username=None, first_name="Uchinchi <>", completed=7),
    ]
    # yana 7 ta odam -> jami 10 ta
    top += [
        SimpleNamespace(user_id=10 + i, username=f"u{10+i}", first_name="X",
                        completed=6 - i) for i in range(7)
    ]
    restore = patch_referral(top=top)
    try:
        cb = FakeCallback(tg_user(42, "Ali"))
        run(ref_mod.top_referrers(cb))
        text = cb.message.answers[0]["text"]
        ok("TOP 10 sarlavhasi", "TOP 10 TAKLIF QILUVCHILAR" in text)
        ok("medallar top-3", "🥇 @best — <b>30</b> ta" in text
           and "🥈" in text and "🥉" in text)
        ok("username'siz -> mention havolasi", 'tg://user?id=2' in text)
        ok("escape: <>", "&lt;&gt;" in text and "Uchinchi &lt;&gt;" in text)
        ok("callback tasdiqlandi", cb.answers == [{"text": None, "show_alert": False}])
    finally:
        restore()

    # bo'sh reyting
    restore = patch_referral(top=[])
    try:
        cb = FakeCallback(tg_user(42, "Ali"))
        run(ref_mod.top_referrers(cb))
        ok("bo'sh reyting matni", "reyting bo'sh" in cb.message.answers[0]["text"])
    finally:
        restore()


if __name__ == "__main__":
    test_parse_referrer()
    test_start()
    test_referral_menu()
    test_top_referrers()
    print(f"\nBARCHA TESTLAR O'TDI: {PASSED} ta tekshiruv")
