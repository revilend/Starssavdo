"""
Repository qatlamini SQLite (aiosqlite) ustida to'liq sinash.

Ishga tushirish (repo root'dan):
    pip install aiosqlite
    python tests/test_queries.py

Izoh: test SQLite ishlatadi (tez va o'rnatishsiz); SQL konstruksiyalari
dialect-neutral, shuning uchun production (PostgreSQL/asyncpg) da ham ishlaydi.
"""
import asyncio
import os
import sys

os.environ.update({
    "BOT_TOKEN": "123:TEST",
    "ADMIN_IDS": "111,222",
    "DATABASE_URL": "postgresql://user:pass@host:5432/postgres",
    "REFERRAL_REWARD_STARS": "10",
    "MIN_WITHDRAWAL_STARS": "50",
})
sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

import database.connection as conn_mod  # noqa: E402

DB_PATH = os.path.abspath("./_test_queries.sqlite3")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

# Test engine'ini connection moduli global'lariga ulash (get_session shuni ishlatadi)
engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}")
conn_mod._engine = engine
conn_mod._session_factory = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)

from database.models import AntiFraudLog, Base, Product, Referral, Transaction, User, Withdrawal  # noqa: E402
from database.queries import (  # noqa: E402
    ORDER_PAID,
    PAYMENT_BALANCE,
    PAYMENT_XTR,
    WITHDRAWAL_APPROVED,
    WITHDRAWAL_REJECTED,
    ChannelRepository,
    InsufficientBalanceError,
    OrderRepository,
    ProductRepository,
    ReferralRepository,
    RepositoryError,
    SelfReferralError,
    SettingsRepository,
    UserRepository,
    WithdrawalRepository,
    WithdrawalStateError,
    SETTING_REFERRAL_REWARD,
)

PASSED = 0


def ok(label: str, cond: bool, extra: str = "") -> None:
    global PASSED
    if not cond:
        raise AssertionError(f"FAILED: {label} {extra}")
    PASSED += 1
    print(f"  ok: {label}")


async def q(sql: str, params: dict | None = None):
    async with conn_mod._session_factory() as s:
        res = await s.execute(__import__("sqlalchemy").text(sql), params or {})
        return res.fetchall()


async def main() -> None:
    async with engine.begin() as a:
        await a.run_sync(Base.metadata.create_all)
    print("== 1. UserRepository ==")
    u1 = await UserRepository.get_or_create_user(1001, "ref_user", "Ref", "User")
    ok("yaratildi", u1.stars_balance == 0 and not u1.channels_verified)
    u1b = await UserRepository.get_or_create_user(1001, "ref_user", "RefX", None)
    ok("takroriy chaqiruv PK'ni saqlaydi", u1b.user_id == 1001 and u1b.first_name == "RefX")
    # o'zini taklif qilish himoyasi
    u2 = await UserRepository.get_or_create_user(1002, "new_user", "New", None, referrer_id=1002)
    ok("o'zini taklif qilish bloklandi", u2.referrer_id is None)
    # to'g'ri referal
    u2 = await UserRepository.get_or_create_user(1002, "new_user", "New", None, referrer_id=1001)
    ok("referrer saqlandi", u2.referrer_id == 1001)
    got = await UserRepository.get_user(1001)
    ok("get_user + balans", got is not None and got.stars_balance == 0)

    bal = await UserRepository.update_balance(1001, 100, "deposit", "test topup")
    ok("deposit +100", bal == 100)
    try:
        await UserRepository.update_balance(1001, -150, "purchase", "xarid")
        raise SystemExit("InsufficientBalanceError kutilgan edi")
    except InsufficientBalanceError:
        ok("balans 0 dan tushmasligi himoyalandi", True)
    got = await UserRepository.get_user(1001)
    ok("balans o'zgarmadi (rollback)", got.stars_balance == 100)
    rows = await q("SELECT count(*) FROM transactions WHERE user_id=1001")
    ok("audit: 1 yozuv (failed tranzaksiya yozilmadi)", rows[0][0] == 1)

    ok("set_channels_verified", await UserRepository.set_channels_verified(1001))
    ok("set_channels_verified (yo'q user)", not await UserRepository.set_channels_verified(9999))
    ok("channels_verified=True", (await UserRepository.get_user(1001)).channels_verified)
    ok("is_channels_verified=True (middleware uchun)",
       await UserRepository.is_channels_verified(1001))
    ok("is_channels_verified: yo'q user -> False",
       not await UserRepository.is_channels_verified(9999))

    print("== 2. ChannelRepository ==")
    c1 = await ChannelRepository.add_channel(-1001, "Kanal1", "https://t.me/+aaa", "kanal1")
    c2 = await ChannelRepository.add_channel(-1002, "Kanal2", "https://t.me/+bbb")
    ok("2 kanal qo'shildi", c1.id and c2.id)
    try:
        await ChannelRepository.add_channel(-1001, "Dublikat", "https://t.me/+aaa")
        raise SystemExit("dublikat kanal xatoligi kutilgan")
    except RepositoryError:
        ok("dublikat chat_id bloklandi", True)
    act = await ChannelRepository.get_active_channels()
    ok("faol kanallar = 2", len(act) == 2)
    t = await ChannelRepository.toggle_channel_status(c2.id, False)
    ok("toggle -> nofaol", t is not False and not t.is_active)
    ok("faol kanallar = 1", len(await ChannelRepository.get_active_channels()) == 1)
    ok("toggle yo'q kanal -> None", await ChannelRepository.toggle_channel_status(9999, True) is None)
    ok("o'chirish", await ChannelRepository.delete_channel(-1001))
    ok("qayta o'chirish -> False", not await ChannelRepository.delete_channel(-1001))

    print("== 3. ReferralRepository ==")
    try:
        await ReferralRepository.create_referral(1001, 1001)
        raise SystemExit("SelfReferralError kutilgan")
    except SelfReferralError:
        ok("o'zini taklif qilish bloklandi", True)
    ref = await ReferralRepository.create_referral(1001, 1002)
    ok("pending referal yaratildi", ref is not None and ref.status == "pending")
    ok("takroriy create -> None", await ReferralRepository.create_referral(1001, 1002) is None)

    # velocity: 1005 uchun 15 ta referal
    await UserRepository.get_or_create_user(1005, "vel_user", "Vel", None)
    for i in range(1010, 1025):
        await UserRepository.get_or_create_user(i, f"v{i}", "V", None, referrer_id=1005)
        await ReferralRepository.create_referral(1005, i)
    v = await ReferralRepository.check_referral_velocity(1005, limit=15)
    ok("velocity count=15 flagged=True", v.count == 15 and v.flagged, str(v))
    v0 = await ReferralRepository.check_referral_velocity(1001)
    ok("oddiy referrer flagged=False", v0.count == 1 and not v0.flagged, str(v0))
    logs = await q("SELECT count(*) FROM anti_fraud_logs WHERE referrer_id=1005")
    ok("anti_fraud_logs yozildi", logs[0][0] == 1)
    cnt = await ReferralRepository.get_user_referrals_count(1005)
    ok("15 jami / 0 completed", cnt.total == 15 and cnt.completed == 0, str(cnt))

    print("== 4. SettingsRepository ==")
    ok("default qaytadi", await SettingsRepository.get_setting("yoq", "dflt") == "dflt")
    ok("7 ga yangilandi", await SettingsRepository.update_setting(SETTING_REFERRAL_REWARD, 7) == 7)
    ok("o'qildi -> 7", await SettingsRepository.get_setting(SETTING_REFERRAL_REWARD, 10) == 7)
    ok("qayta yangilandi -> 9", await SettingsRepository.update_setting(SETTING_REFERRAL_REWARD, 9) == 9)
    ok("yakuniy qiymat 9", await SettingsRepository.get_setting(SETTING_REFERRAL_REWARD) == 9)
    rows = await q("SELECT count(*) FROM settings WHERE key=:k", {"k": SETTING_REFERRAL_REWARD})
    ok("bitta qator (upsert)", rows[0][0] == 1)
    await SettingsRepository.update_setting(SETTING_REFERRAL_REWARD, 7)

    print("== 5. confirm_referral_reward ==")
    reward = await ReferralRepository.confirm_referral_reward(1002)
    ok("reward qaytdi", reward is not None and reward.referrer_id == 1001 and reward.reward_amount == 7, str(reward))
    ok("referrer balans +7", (await UserRepository.get_user(1001)).stars_balance == 107)
    cnt = await ReferralRepository.get_user_referrals_count(1001)
    ok("1 completed", cnt.total == 1 and cnt.completed == 1, str(cnt))
    ok("idempotent: 2-chi chaqiruv None", await ReferralRepository.confirm_referral_reward(1002) is None)
    ok("mavjud bo'lmagan -> None", await ReferralRepository.confirm_referral_reward(777777) is None)
    rows = await q("SELECT count(*) FROM transactions WHERE user_id=1001 AND tx_type='referral_reward'")
    ok("reward audit yozuvi", rows[0][0] == 1)

    print("== 6. ProductRepository + OrderRepository ==")
    async with conn_mod._session_factory() as s:
        async with s.begin():
            ps = Product(category="stars", title="100 Stars", price_xtr=100, payload={"stars": 100})
            pp = Product(category="premium", title="1 oy Premium", price_xtr=500)
            pn = Product(category="nft", title="Golden NFT", price_xtr=1000)
            pg = Product(category="gift", title="Mayda gift", price_xtr=50)
            px = Product(category="stars", title="O'chirilgan", price_xtr=999, is_active=False)
            s.add_all([ps, pp, pn, pg, px])
            await s.flush()
            star_id, gift_id = ps.id, pg.id
    ok("stars=1 (faol)", len(await ProductRepository.get_products_by_category("stars")) == 1)
    ok("premium=1", len(await ProductRepository.get_products_by_category("premium")) == 1)
    ok("nft=1", len(await ProductRepository.get_products_by_category("nft")) == 1)
    ok("gift=1", len(await ProductRepository.get_products_by_category("gift")) == 1)
    try:
        await ProductRepository.get_products_by_category("foo")
        raise SystemExit("ValueError kutilgan")
    except ValueError:
        ok("noto'g'ri kategoriya -> ValueError", True)

    await UserRepository.get_or_create_user(1003, "buyer", "Buyer", None)
    try:
        await OrderRepository.create_order(1003, 9999, PAYMENT_XTR, 100)
        raise SystemExit("RepositoryError kutilgan")
    except RepositoryError:
        ok("yo'q mahsulot -> xato", True)
    try:
        await OrderRepository.create_order(1003, star_id, PAYMENT_XTR, 0)
        raise SystemExit("ValueError kutilgan")
    except ValueError:
        ok("amount<=0 -> ValueError", True)
    order = await OrderRepository.create_order(1003, star_id, PAYMENT_XTR, 100, {"note": "x"})
    ok("pending yaratildi", order.status == "pending" and order.order_data == {"note": "x"})
    paid = await OrderRepository.complete_order(order.id, "chg_abc123")
    ok("paid ga o'tdi", paid.status == ORDER_PAID and paid.telegram_payment_charge_id == "chg_abc123" and paid.paid_at)
    again = await OrderRepository.complete_order(order.id, "chg_other")
    ok("idempotent (charge o'zgarmadi)", again.status == ORDER_PAID and again.telegram_payment_charge_id == "chg_abc123")
    try:
        await OrderRepository.complete_order(88888, "chg_x")
        raise SystemExit("RepositoryError kutilgan")
    except RepositoryError:
        ok("yo'q buyurtma -> xato", True)

    print("== 7. WithdrawalRepository ==")
    await UserRepository.get_or_create_user(1004, "withdr", "W", None)
    await UserRepository.update_balance(1004, 100, "deposit", "topup")
    w1 = await WithdrawalRepository.create_withdrawal(1004, 60, "ton_wallet", "EQABC")
    ok("hold: balans 40", (await UserRepository.get_user(1004)).stars_balance == 40)
    ok("pending status", w1.status == "pending")
    rows = await q("SELECT count(*) FROM transactions WHERE user_id=1004 AND tx_type='withdraw_hold'")
    ok("hold audit yozuvi", rows[0][0] == 1)
    try:
        await WithdrawalRepository.create_withdrawal(1004, 100, "ton_wallet", "EQABC")
        raise SystemExit("InsufficientBalanceError kutilgan")
    except InsufficientBalanceError:
        ok("yetarli emas -> xato, balans saqlandi", (await UserRepository.get_user(1004)).stars_balance == 40)
    rows = await q("SELECT count(*) FROM withdrawals WHERE user_id=1004")
    ok("failed so'rov yaratilmadi", rows[0][0] == 1)

    rej = await WithdrawalRepository.reject_withdrawal(w1.id, 777, "malek emas")
    ok("rejected + referrer", rej.status == WITHDRAWAL_REJECTED and rej.admin_id == 777)
    ok("balans qaytarildi -> 100", (await UserRepository.get_user(1004)).stars_balance == 100)
    rows = await q("SELECT count(*) FROM transactions WHERE user_id=1004 AND tx_type='refund'")
    ok("refund audit yozuvi", rows[0][0] == 1)

    w2 = await WithdrawalRepository.create_withdrawal(1004, 60, "card", "8600")
    app = await WithdrawalRepository.approve_withdrawal(w2.id, 777)
    ok("approved", app.status == WITHDRAWAL_APPROVED and app.processed_at is not None)
    ok("approve'da balans o'zgarmadi", (await UserRepository.get_user(1004)).stars_balance == 40)
    for fn in (
        lambda: WithdrawalRepository.approve_withdrawal(w2.id, 777),
        lambda: WithdrawalRepository.reject_withdrawal(w1.id, 777, "x"),
    ):
        try:
            await fn()
            raise SystemExit("WithdrawalStateError kutilgan")
        except WithdrawalStateError:
            pass
    ok("qayta ko'rib chiqish -> WithdrawalStateError", True)
    try:
        await WithdrawalRepository.create_withdrawal(1004, -5, "card", "8600")
        raise SystemExit("ValueError kutilgan")
    except ValueError:
        ok("amount<=0 -> ValueError", True)

    print("== 8. Statistika ==")
    stats = await UserRepository.get_total_stats()
    ok("jami a'zolar = 20", stats.total_members == 20, str(stats))
    ok("bugun kirganlar = 20", stats.today_active == 20, str(stats))
    ok("tarqatilgan = 7 (faqat reward)", stats.total_distributed_stars == 7, str(stats))
    ok("yechilgan = 60 (approved)", stats.total_withdrawn_stars == 60, str(stats))
    ids = await UserRepository.get_all_user_ids()
    ok("20 ta aktiv ID", len(ids) == 20 and all(isinstance(i, int) for i in ids))
    async with conn_mod._session_factory() as s:
        async with s.begin():
            u3 = await s.get(User, 1003)
            u3.is_active = False
    ids = await UserRepository.get_all_user_ids()
    ok("no'faol chetlab o'tildi", len(ids) == 19 and 1003 not in ids)

    print("== 9. Top referrers ==")
    # 2001-2003 uchun completed referallar yaratamiz (2003: 3 ta, 2002: 2 ta, 2001: 1 ta)
    for uid in (2001, 2002, 2003):
        await UserRepository.get_or_create_user(uid, f"top{uid}", "Top", None)
    async with conn_mod._session_factory() as s:
        async with s.begin():
            for referrer, n in ((2003, 3), (2002, 2), (2001, 1)):
                for i in range(n):
                    referred = 3000 + referrer * 10 + i
                    await UserRepository.get_or_create_user(referred, None, "R", None)
                    s.add(Referral(referrer_id=referrer, referred_id=referred,
                                   status="completed", reward_amount=5))
    top = await ReferralRepository.get_top_referrers(limit=10)
    # 1001 (1 completed) ham ro'yxatda — tartib: completed desc, user_id asc
    ok("top tartibi kamayish", [t.user_id for t in top][:3] == [2003, 2002, 1001], str(top))
    ok("top completed sonlari", top[0].completed == 3 and top[1].completed == 2)
    top2 = await ReferralRepository.get_top_referrers(limit=2)
    ok("limit ishlaydi", len(top2) == 2 and top2[0].user_id == 2003)
    ok("top'da username/first_name", top[0].username == "top2003" and top[0].first_name == "Top")

    print("== 10. get_product_by_id / get_order / create_balance_order ==")
    async with conn_mod._session_factory() as s:
        async with s.begin():
            pbal = Product(category="stars", title="50 Stars balans", price_xtr=50)
            poff = Product(category="gift", title="O'chiq gift", price_xtr=10, is_active=False)
            s.add_all([pbal, poff])
            await s.flush()
            bal_id, off_id = pbal.id, poff.id
    got = await ProductRepository.get_product_by_id(bal_id)
    ok("get_product_by_id (faol)", got is not None and got.title == "50 Stars balans")
    ok("get_product_by_id (o'chirilgan) ham qaytadi",
       (await ProductRepository.get_product_by_id(off_id)) is not None)
    ok("get_product_by_id (yo'q) -> None", await ProductRepository.get_product_by_id(99999) is None)

    await UserRepository.get_or_create_user(1006, "bal_buyer", "BalBuyer", None)
    await UserRepository.update_balance(1006, 80, "deposit", "topup")
    bo = await OrderRepository.create_balance_order(1006, bal_id)
    ok("balance order darhol paid", bo.status == ORDER_PAID and bo.paid_at is not None)
    ok("charge_id None (ichki to'lov)", bo.telegram_payment_charge_id is None)
    ok("payment_method=balance", bo.payment_method == PAYMENT_BALANCE)
    ok("balans 80-50=30", (await UserRepository.get_user(1006)).stars_balance == 30)
    rows = await q("SELECT count(*) FROM transactions WHERE user_id=1006 AND tx_type='purchase' AND amount=-50")
    ok("purchase audit yozuvi", rows[0][0] == 1)
    o = await OrderRepository.get_order(bo.id)
    ok("get_order", o is not None and o.status == ORDER_PAID)
    ok("get_order (yo'q) -> None", await OrderRepository.get_order(99999) is None)
    try:
        await OrderRepository.create_balance_order(1006, gift_id)  # 50 XTR, balans 30
        raise SystemExit("InsufficientBalanceError kutilgan")
    except InsufficientBalanceError:
        ok("balans yetarli emas -> xato", (await UserRepository.get_user(1006)).stars_balance == 30)
    rows = await q("SELECT count(*) FROM orders WHERE user_id=1006")
    ok("muvaffaqiyatsiz xarid buyurtma yaratmaydi", rows[0][0] == 1)
    try:
        await OrderRepository.create_balance_order(1006, off_id)
        raise SystemExit("RepositoryError kutilgan")
    except RepositoryError:
        ok("o'chirilgan mahsulot -> xato", True)
    try:
        await OrderRepository.create_balance_order(88888, bal_id)
        raise SystemExit("RepositoryError kutilgan")
    except RepositoryError:
        ok("mavjud bo'lmagan foydalanuvchi -> xato", True)

    print(f"\nBARCHA TESTLAR O'TDI: {PASSED} ta tekshiruv")


asyncio.run(main())
