"""
Klaviaturalar struktura/sintaksis testi (aiogram 3.x builder'lari).

Ishga tushirish (repo root'dan):
    pip install aiogram>=3.13.0
    python tests/test_keyboards.py
"""

import sys

sys.path.insert(0, ".")

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup  # noqa: E402

from keyboards import (  # noqa: E402
    get_admin_channel_manage_kb,
    get_admin_main_kb,
    get_balance_kb,
    get_main_menu_kb,
    get_products_list_kb,
    get_referral_kb,
    get_shop_categories_kb,
    get_subscription_kb,
    get_withdrawal_decision_kb,
)
from keyboards.reply import BTN_ADMIN, BTN_SHOP, MAIN_MENU_TEXTS  # noqa: E402

PASSED = 0


def ok(label: str, cond: bool, extra: str = "") -> None:
    global PASSED
    if not cond:
        raise AssertionError(f"FAILED: {label} {extra}")
    PASSED += 1
    print(f"  ok: {label}")


def rows(kb) -> list[list]:
    """Keyboard'ni qatorlar ko'rinishida qaytaradi."""
    return [list(r) for r in kb.keyboard]


def inline_rows(kb: InlineKeyboardMarkup) -> list[list[InlineKeyboardButton]]:
    out: list[list[InlineKeyboardButton]] = []
    for row in kb.inline_keyboard:
        out.append(list(row))
    return out


def main() -> None:
    print("== reply.py ==")
    kb = get_main_menu_kb()
    ok("ReplyKeyboardMarkup", isinstance(kb, ReplyKeyboardMarkup))
    ok("resize_keyboard=True", kb.resize_keyboard is True)
    ok("is_persistent=True", kb.is_persistent is True)
    r = rows(kb)
    ok("foydalanuvchi: 3 qator", len(r) == 3, str(len(r)))
    ok("1-qator: Do'kon + Balansim", r[0][0].text == BTN_SHOP and r[0][1].text == "⭐️ Balansim")
    ok("2-qator: Taklif + Giftlar",
       r[1][0].text == "👥 Do'stlarni taklif qilish" and r[1][1].text == "🎁 Mayda Giftlar")
    ok("3-qator: Qo'llanma", [b.text for b in r[2]] == ["ℹ️ Qo'llanma"])
    ok("oddiy foydalanuvchida Admin yo'q", all(b.text != BTN_ADMIN for row in r for b in row))
    ok("6 ta menyu matni konstantasi", len(MAIN_MENU_TEXTS) == 6)

    kba = get_main_menu_kb(is_admin=True)
    ra = rows(kba)
    ok("admin: 4-qator Admin Panel", len(ra) == 4 and ra[3][0].text == BTN_ADMIN)
    ok("admin ham persistent/resize", kba.is_persistent and kba.resize_keyboard)

    print("== inline: obuna ==")
    class Ch:
        def __init__(self, i):
            self.invite_link = f"https://t.me/+inv{i}"
            self.title = f"Kanal{i}"
    kb = get_subscription_kb([Ch(1), Ch(2), Ch(3)])
    r = inline_rows(kb)
    ok("4 qator (3 kanal + tekshiruv)", len(r) == 4, str(len(r)))
    ok("har qatorda 1 tugma", all(len(row) == 1 for row in r))
    ok("raqamlangan url tugmalar",
       r[0][0].text == "📢 1-Kanalga a'zo bo'lish" and r[0][0].url == "https://t.me/+inv1"
       and r[2][0].text == "📢 3-Kanalga a'zo bo'lish")
    ok("yashil tekshiruv tugmasi",
       r[3][0].text == "✅ A'zo bo'ldim" and r[3][0].callback_data == "check_subscription")
    ok("bo'sh kanallar ro'yxati ham ishlaydi",
       len(inline_rows(get_subscription_kb([]))) == 1)

    print("== inline: do'kon toifalari ==")
    kb = get_shop_categories_kb()
    flat = [b for row in inline_rows(kb) for b in row]
    texts = [b.text for b in flat]
    ok("5 ta tugma", len(flat) == 5, str(texts))
    ok("toifa matnlari", texts[:4] == ["⭐️ Stars xarid qilish", "💎 Telegram Premium",
                                       "🖼 Telegram NFT", "🎁 Mayda Giftlar"])
    ok("toifa callbacklari", [b.callback_data for b in flat[:4]] ==
       ["cat_stars", "cat_premium", "cat_nft", "cat_gift"])
    ok("orqaga tugmasi", texts[4] == "◀️ Orqaga" and flat[4].callback_data == "back_to_menu")

    print("== inline: mahsulotlar ==")
    class Prod:
        def __init__(self, i, title, price):
            self.id, self.title, self.price_xtr = i, title, price
    prods = [Prod(11, "100 Stars", 100), Prod(12, "Premium 3 oy", 350), Prod(13, "Teddy Bear Gift", 50)]
    kb = get_products_list_kb(prods, "stars")
    r = inline_rows(kb)
    flat = [b for row in r for b in row]
    ok("3 mahsulot + orqaga", len(flat) == 4)
    ok("matn formati 'emoji narx XTR'", flat[0].text == "⭐️ 100 Stars — 100 XTR"
       and flat[2].text == "⭐️ Teddy Bear Gift — 50 XTR", flat[0].text)
    ok("callback buy_prod_{id}",
       [b.callback_data for b in flat[:3]] == ["buy_prod_11", "buy_prod_12", "buy_prod_13"])
    ok("toifalarga qaytish", flat[3].text == "◀️ Toifalarga qaytish"
       and flat[3].callback_data == "shop_categories")
    empty = [b for row in inline_rows(get_products_list_kb([], "nft")) for b in row]
    ok("bo'sh ro'yxat: faqat orqaga", len(empty) == 1 and empty[0].callback_data == "shop_categories")

    print("== inline: referal ==")
    kb = get_referral_kb("testbot", 42)
    flat = [b for row in inline_rows(kb) for b in row]
    ok("3 tugma", len(flat) == 3, str(len(flat)))
    ok("ulashish switch_inline_query", flat[0].text == "📲 Do'stlarga ulashish"
       and flat[0].switch_inline_query is not None
       and "t.me/testbot?start=ref_42" in flat[0].switch_inline_query)
    ok("top taklif qiluvchilar", flat[1].callback_data == "top_referrers")
    ok("bosh menyu", flat[2].text == "◀️ Bosh menyu" and flat[2].callback_data == "back_to_menu")

    print("== inline: balans ==")
    kb = get_balance_kb(120, 50)
    flat = [b for row in inline_rows(kb) for b in row]
    ok("3 tugma", len(flat) == 3)
    ok("yechib olish (balans yetarli)", flat[0].callback_data == "withdraw_start"
       and "120⭐️" in flat[0].text, flat[0].text)
    ok("tarix", flat[1].callback_data == "tx_history")
    ok("bosh menyu", flat[2].callback_data == "back_to_menu")
    kb2 = get_balance_kb(10, 50)
    f2 = [b for row in inline_rows(kb2) for b in row]
    ok("balans yetarli emas -> min ko'rsatiladi", "min 50⭐️" in f2[0].text, f2[0].text)

    print("== builders: admin ==")
    kb = get_admin_main_kb()
    flat = [b for row in inline_rows(kb) for b in row]
    ok("7 tugma (6 bo'lim + chiqish)", len(flat) == 7, str(len(flat)))
    ok("callbacklar to'liq",
       [b.callback_data for b in flat[:6]] == ["adm_stats", "adm_broadcast", "adm_channels",
                                               "adm_ref_price", "adm_withdrawals", "adm_balance_edit"])
    ok("chiqish oxirida to'liq qator", flat[6].text == "❌ Chiqish"
       and flat[6].callback_data == "adm_exit" and len(inline_rows(kb)[3]) == 1)

    class ACh:
        def __init__(self, i, title, active):
            self.id, self.title, self.is_active = i, title, active
    kb = get_admin_channel_manage_kb([ACh(1, "KanalA", True), ACh(2, "KanalB", False)])
    r = inline_rows(kb)
    ok("2 kanal qatori + 2 navbat qator", len(r) == 4, str(len(r)))
    ok("faol kanal ✅ / nofaol 🚫", r[0][0].text == "✅ KanalA" and r[1][0].text == "🚫 KanalB")
    ok("toggle callbacklari", r[0][0].callback_data == "adm_toggle_1"
       and r[1][0].callback_data == "adm_toggle_2")
    ok("o'chirish tugmasi", r[0][1].text == "🗑" and r[0][1].callback_data == "adm_chdel_1")
    ok("qo'shish + qaytish", r[2][0].callback_data == "adm_chadd"
       and r[3][0].callback_data == "adm_back")
    ok("kanalsiz: 2 qator", len(inline_rows(get_admin_channel_manage_kb([]))) == 2)

    kb = get_withdrawal_decision_kb(77)
    flat = [b for row in inline_rows(kb) for b in row]
    ok("2 qaror tugmasi bitta qatorda", len(flat) == 2 and len(inline_rows(kb)) == 1)
    ok("approve_payout_77", flat[0].text == "✅ Tasdiqlash" and flat[0].callback_data == "approve_payout_77")
    ok("reject_payout_77", flat[1].text == "❌ Rad etish" and flat[1].callback_data == "reject_payout_77")

    print(f"\nBARCHA TESTLAR O'TDI: {PASSED} ta tekshiruv")


if __name__ == "__main__":
    main()
