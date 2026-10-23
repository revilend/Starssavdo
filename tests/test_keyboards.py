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
    get_admin_product_list_kb,
    get_admin_products_kb,
    get_balance_kb,
    get_button_edit_kb,
    get_buttons_manage_kb,
    get_main_menu_kb,
    get_product_category_kb,
    get_product_delete_confirm_kb,
    get_product_detail_kb,
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
    ok("foydalanuvchi: 4 qator", len(r) == 4, str(len(r)))
    ok("1-qator: Do'kon + Balansim", r[0][0].text == BTN_SHOP and r[0][1].text == "⭐️ Balansim")
    ok("2-qator: Taklif + Giftlar",
       r[1][0].text == "👥 Do'stlarni taklif qilish" and r[1][1].text == "🎁 Mayda Giftlar")
    ok("3-qator: Balans to'ldirish", [b.text for b in r[2]] == ["💳 Balans to'ldirish"])
    ok("4-qator: Qo'llanma", [b.text for b in r[3]] == ["ℹ️ Qo'llanma"])
    ok("oddiy foydalanuvchida Admin yo'q", all(b.text != BTN_ADMIN for row in r for b in row))
    ok("7 ta menyu matni konstantasi", len(MAIN_MENU_TEXTS) == 7, str(len(MAIN_MENU_TEXTS)))
    ok("ranglar: Do'kon primary, Taklif success",
       r[0][0].style == "primary" and r[1][0].style == "success")
    ok("Balans to'ldirish success", r[2][0].style == "success")

    kba = get_main_menu_kb(is_admin=True)
    ra = rows(kba)
    ok("admin: 5-qator Admin Panel",
       len(ra) == 5 and ra[4][0].text == BTN_ADMIN and ra[4][0].style == "danger")
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
    ok("tekshiruv tugmasi success rangda", r[3][0].style == "success")
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
    ok("4 tugma (to'ldirish + yechish + tarix + menyu)", len(flat) == 4)
    ok("balans to'ldirish", flat[0].callback_data == "topup_start"
       and flat[0].style == "success")
    ok("yechib olish (balans yetarli)", flat[1].callback_data == "withdraw_start"
       and "120⭐️" in flat[1].text, flat[1].text)
    ok("yechish success rangda", flat[1].style == "success")
    ok("tarix", flat[2].callback_data == "tx_history")
    ok("bosh menyu", flat[3].callback_data == "back_to_menu")
    kb2 = get_balance_kb(10, 50)
    f2 = [b for row in inline_rows(kb2) for b in row]
    ok("balans yetarli emas -> min ko'rsatiladi", "min 50⭐️" in f2[1].text, f2[1].text)

    print("== builders: admin ==")
    kb = get_admin_main_kb()
    flat = [b for row in inline_rows(kb) for b in row]
    ok("11 tugma (10 bo'lim + chiqish)", len(flat) == 11, str(len(flat)))
    ok("callbacklar to'liq",
       [b.callback_data for b in flat[:10]] == ["adm_stats", "adm_broadcast", "adm_channels",
                                               "adm_ref_price", "adm_withdrawals", "adm_balance_edit",
                                               "adm_gift_add", "adm_products", "adm_card",
                                               "adm_buttons"])
    ok("yangi bo'lim: Giftlarni kiritish", flat[6].text == "🎁 Giftlarni kiritish"
       and flat[6].style == "success")
    ok("yangi bo'lim: Mahsulotlarni boshqarish", flat[7].text == "🛍 Mahsulotlarni boshqarish")
    ok("yangi bo'lim: Karta ma'lumotlari", flat[8].text == "💳 Karta ma'lumotlari"
       and flat[8].style == "success")
    ok("yangi bo'lim: Tugnalar va Emojilar", flat[9].text == "🎛 Tugnalar va Emojilar")
    ok("chiqish oxirida to'liq qator", flat[10].text == "❌ Chiqish"
       and flat[10].callback_data == "adm_exit" and flat[10].style == "danger"
       and len(inline_rows(kb)[5]) == 1)

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
    ok("qaror ranglari: success/danger",
       flat[0].style == "success" and flat[1].style == "danger")

    print("== builders: mahsulotlar (admin CRUD) ==")
    kb = get_admin_products_kb({"stars": 3, "gift": 1})
    r = inline_rows(kb)
    flat = [b for row in r for b in row]
    ok("umumiy ro'yxat + 4 toifa + qo'shish + orqaga", len(flat) == 7, str(len(flat)))
    ok("barcha mahsulotlar tugmasi", flat[0].text == "📋 Barcha mahsulotlar (4)"
       and flat[0].callback_data == "adm_plist_all", flat[0].text)
    ok("toifa soni ko'rsatiladi", flat[1].text == "⭐️ Stars (3)"
       and flat[4].text == "🎁 Mayda Giftlar (1)", flat[4].text)
    ok("toifa callbacki", flat[1].callback_data == "adm_plist_stars"
       and flat[3].callback_data == "adm_plist_nft")
    ok("yangi mahsulot tugmasi", flat[5].callback_data == "adm_pnew"
       and flat[5].style == "success")
    ok("orqaga", flat[6].callback_data == "adm_back")
    ok("sonlarsiz ham ishlaydi", "(0)" in get_admin_products_kb().inline_keyboard[0][0].text)

    cat_kb = [b for row in inline_rows(get_product_category_kb()) for b in row]
    ok("toifa tanlash: 4 + orqaga", len(cat_kb) == 5
       and cat_kb[0].callback_data == "adm_padd_stars"
       and cat_kb[3].callback_data == "adm_padd_gift"
       and cat_kb[4].callback_data == "adm_products")

    class APr:
        def __init__(self, i, category, title, price, active=True):
            self.id, self.category, self.title = i, category, title
            self.price_xtr, self.is_active = price, active

    product = APr(7, "gift", "Mayda gift", 50)
    lst = [b for row in inline_rows(get_admin_product_list_kb([product, APr(8, "gift", "O'chiq", 10, False)], "gift")) for b in row]
    ok("ro'yxat: holat + narx", lst[0].text == "✅ Mayda gift — 50⭐️"
       and lst[1].text == "🚫 O'chiq — 10⭐️")
    ok("ro'yxat callbacki", lst[0].callback_data == "adm_pdet_7")
    ok("ro'yxat: qo'shish + orqaga", lst[2].callback_data == "adm_pnew"
       and lst[3].callback_data == "adm_products")
    all_lst = [b for row in inline_rows(
        get_admin_product_list_kb([product], "all")) for b in row]
    ok("umumiy ro'yxat: qo'shish tugmasi ham adm_pnew",
       all_lst[1].callback_data == "adm_pnew")

    detail = [b for row in inline_rows(get_product_detail_kb(product)) for b in row]
    ok("karta: nom/narx/tavsif", [b.callback_data for b in detail[:3]] ==
       ["adm_pedit_7_title", "adm_pedit_7_price", "adm_pedit_7_description"])
    ok("kartada rasm tahrirlash", detail[3].callback_data == "adm_pedit_7_image")
    ok("gift kartasida Gift ID", detail[4].callback_data == "adm_pedit_7_gift")
    ok("karta: nofaol qilish + o'chirish", detail[5].callback_data == "adm_ptgl_7"
       and detail[6].callback_data == "adm_pdel_7")
    ok("karta: ro'yxatga qaytish", detail[7].callback_data == "adm_plist_gift")
    stars_detail = [b for row in inline_rows(
        get_product_detail_kb(APr(9, "stars", "100 Stars", 100))) for b in row]
    ok("stars kartasida Gift ID yo'q",
       all(b.callback_data != "adm_pedit_9_gift" for b in stars_detail))
    ok("stars kartasida rasm bor",
       stars_detail[3].callback_data == "adm_pedit_9_image")
    inactive_detail = [b for row in inline_rows(
        get_product_detail_kb(APr(10, "nft", "NFT", 900, False))) for b in row]
    ok("nofaol kartada 'Faollashtirish'", inactive_detail[4].text == "✅ Faollashtirish"
       and inactive_detail[4].style == "success")

    confirm = [b for row in inline_rows(get_product_delete_confirm_kb(product)) for b in row]
    ok("o'chirishni tasdiqlash", confirm[0].callback_data == "adm_pdelok_7"
       and confirm[0].style == "danger" and confirm[1].callback_data == "adm_pdet_7")

    print("== builders: tugmalar va emojilar ==")
    kb = get_buttons_manage_kb([("menu_shop", "Do'kon"), ("menu_refer", "Taklif")])
    flat = [b for row in inline_rows(kb) for b in row]
    ok("2 tugma + orqaga", len(flat) == 3
       and flat[0].callback_data == "adm_btn_menu_shop"
       and flat[2].callback_data == "adm_back")
    edit = [b for row in inline_rows(get_button_edit_kb("menu_shop")) for b in row]
    ok("tahrirlash: nom + emoji + reset + orqaga", len(edit) == 4
       and edit[0].callback_data == "adm_btnedit_text_menu_shop"
       and edit[1].callback_data == "adm_btnedit_icon_menu_shop"
       and edit[2].callback_data == "adm_btnreset_menu_shop")

    print(f"\nBARCHA TESTLAR O'TDI: {PASSED} ta tekshiruv")


if __name__ == "__main__":
    main()
