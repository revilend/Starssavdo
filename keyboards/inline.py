"""
keyboards/inline.py
===================
Botning asosiy inline (ichki) klaviaturalari:
obuna tekshiruvi, do'kon, referal, balans va karta orqali to'ldirish.

Barcha tugmalar `services.button_registry` orqali quriladi:
 - `style` (primary/success/danger) — Bot API 9.4 rangli tugmalar;
 - `icon_custom_emoji_id` — premium emoji (sticker to'plamlaridan yoki
   admin panelda o'rnatilgan ID);
 - matnlar admin tomonidan o'zgartirilishi mumkin.

Callback konventsiyalari:
 - check_subscription       — kanal a'zoligini tekshirish
 - cat_{category}           — do'kon toifasi (stars/premium/nft/gift)
 - shop_categories          — toifalarga qaytish
 - buy_prod_{id}            — mahsulot xaridi
 - pay_xtr_{id} / pay_balance_{id} — to'lov usuli
 - top_referrers            — top taklif qiluvchilar
 - withdraw_start           — yechib olish
 - topup_start              — balans to'ldirish (karta)
 - tx_history               — tranzaksiya tarixi
 - back_to_menu             — bosh menyu
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.button_registry import build_inline_button

# -------------------------------------------------------------
# Callback data konstantalari
# -------------------------------------------------------------
CB_CHECK_SUBSCRIPTION = "check_subscription"
CB_BACK_TO_MENU = "back_to_menu"
CB_SHOP_CATEGORIES = "shop_categories"
CB_TOP_REFERRERS = "top_referrers"
CB_WITHDRAW_START = "withdraw_start"
CB_TOPUP_START = "topup_start"
CB_TOPUP_CANCEL = "topup_cancel"
CB_TOPUP_AUTO = "topup_auto"
CB_TOPUP_CARD = "topup_card"
CB_TX_HISTORY = "tx_history"

# Xarid callback prefikslari
CB_BUY_PRODUCT_PREFIX = "buy_prod_"
CB_PAY_XTR_PREFIX = "pay_xtr_"
CB_PAY_BALANCE_PREFIX = "pay_balance_"

BACK_BTN_TEXT = "◀️ Orqaga"
MENU_BTN_TEXT = "◀️ Bosh menyu"

# Toifa emojilari (mahsulot ro'yxatida qo'llaniladi)
CATEGORY_EMOJI: dict[str, str] = {
    "stars": "⭐️",
    "premium": "💎",
    "nft": "🖼",
    "gift": "🎁",
}


# -------------------------------------------------------------
# Yordamchilar — dict YOKI ORM obyekt bilan ishlaydi
# -------------------------------------------------------------
def _channel_field(channel: Any, key: str) -> Any:
    """Kanal maydonini olish — dict YOKI ORM obyekt uchun ham ishlaydi."""
    if isinstance(channel, Mapping):
        return channel[key]
    return getattr(channel, key)


# =============================================================
# 1. OBUNA (majburiy homiy kanallar)
# =============================================================
def get_subscription_kb(channels: Sequence[Any]) -> InlineKeyboardMarkup:
    """
    Majburiy kanallarga a'zo bo'lish klaviaturasi.

        [ 📢 1-Kanalga a'zo bo'lish ]   (url — invite_link)
        ...
        [ ✅ A'zo bo'ldim ]             (check_subscription, yashil)
    """
    builder = InlineKeyboardBuilder()

    for index, channel in enumerate(channels, start=1):
        builder.button(
            text=f"📢 {index}-Kanalga a'zo bo'lish",
            url=_channel_field(channel, "invite_link"),
            style="primary",
        )
    builder.adjust(1)

    builder.row(
        build_inline_button(
            "check_subscription", callback_data=CB_CHECK_SUBSCRIPTION
        )
    )
    return builder.as_markup()


# =============================================================
# 2. DO'KON — TOIFALAR
# =============================================================
def get_shop_categories_kb() -> InlineKeyboardMarkup:
    """
    Do'kon toifalari klaviaturasi:

        [ ⭐️ Stars xarid qilish ]   (cat_stars)
        [ 💎 Telegram Premium ]     (cat_premium)
        [ 🖼 Telegram NFT ]         (cat_nft)
        [ 🎁 Mayda Giftlar ]        (cat_gift)
        [ ◀️ Orqaga ]               (back_to_menu)
    """
    builder = InlineKeyboardBuilder()
    for key in ("cat_stars", "cat_premium", "cat_nft", "cat_gift"):
        category = key[len("cat_"):]
        builder.row(build_inline_button(key, callback_data=f"cat_{category}"))
    builder.row(build_inline_button("shop_back", callback_data=CB_BACK_TO_MENU))
    return builder.as_markup()


# =============================================================
# 3. DO'KON — MAHSULOTLAR RO'YXATI
# =============================================================
def get_products_list_kb(
    products: Sequence[Any], category: str
) -> InlineKeyboardMarkup:
    """
    Tanlangan toifa bo'yicha mahsulotlar ro'yxati.

    Har bir mahsulot:
        text          = "{emoji} {title} — {narx} XTR"
        callback_data = "buy_prod_{product_id}"

    Pastda [ ◀️ Toifalarga qaytish ].
    Mahsulot bo'lmasa — faqat qaytish tugmasi.
    """
    builder = InlineKeyboardBuilder()
    emoji = CATEGORY_EMOJI.get(category, "🛍")

    for product in products:
        builder.button(
            text=f"{emoji} {product.title} — {product.price_xtr} XTR",
            callback_data=f"buy_prod_{product.id}",
            style="primary",
        )

    if products:
        builder.adjust(1)

    builder.row(
        build_inline_button("products_back", callback_data=CB_SHOP_CATEGORIES)
    )
    return builder.as_markup()


# =============================================================
# 3.1. DO'KON — XARID USULINI TANLASH
# =============================================================
def get_payment_methods_kb(product_id: int) -> InlineKeyboardMarkup:
    """
    Xarid usulini tanlash:

        [ ⭐️ Rasmiy Telegram Stars (XTR) bilan to'lash ]  (pay_xtr_{id})
        [ 💳 Botdagi balansimdan yechish ]                (pay_balance_{id})
        [ ◀️ Orqaga ]                                      (shop_categories)
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        build_inline_button(
            "pay_xtr", callback_data=f"{CB_PAY_XTR_PREFIX}{product_id}"
        )
    )
    builder.row(
        build_inline_button(
            "pay_balance", callback_data=f"{CB_PAY_BALANCE_PREFIX}{product_id}"
        )
    )
    builder.row(
        build_inline_button("shop_back", callback_data=CB_SHOP_CATEGORIES)
    )
    return builder.as_markup()


# =============================================================
# 4. REFERAL BO'LIMI
# =============================================================
def get_referral_kb(bot_username: str, user_id: int) -> InlineKeyboardMarkup:
    """
    Referal bo'limi klaviaturasi:

        [ 📲 Do'stlarga ulashish ]     (switch_inline_query)
        [ 🏆 Top taklif qiluvchilar ]  (top_referrers)
        [ ◀️ Bosh menyu ]              (back_to_menu)

    Deep-link: https://t.me/{bot_username}?start=ref_{user_id}
    """
    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    share_text = (
        "⭐️ Do'st, keling birga o'ynaymiz! 💎 NFT, Premium va "
        "Mayda Giftlar shu botda:\n" + referral_link
    )

    builder = InlineKeyboardBuilder()
    builder.row(
        build_inline_button("share", switch_inline_query=share_text)
    )
    builder.row(
        build_inline_button("top_referrers", callback_data=CB_TOP_REFERRERS)
    )
    builder.row(
        build_inline_button("menu_back", callback_data=CB_BACK_TO_MENU)
    )
    return builder.as_markup()


# =============================================================
# 5. BALANS BO'LIMI
# =============================================================
def get_balance_kb(stars_balance: int, min_withdrawal: int) -> InlineKeyboardMarkup:
    """
    Balans bo'limi klaviaturasi:

        [ 💳 Balans to'ldirish ]        (topup_start, yashil)
        [ 💸 Starslarni yechib olish ]  (withdraw_start, yashil)
        [ 📜 Tranzaksiyalar tarixi ]    (tx_history)
        [ ◀️ Bosh menyu ]               (back_to_menu)

    Yechib olish tugmasi matni balansga qarab dinamik:
    yetarli bo'lsa "… (N⭐️)", aks holda "… (min M⭐️)".
    """
    withdraw_text = (
        f"💸 Starslarni yechib olish ({stars_balance}⭐️)"
        if stars_balance >= min_withdrawal
        else f"💸 Yechib olish (min {min_withdrawal}⭐️)"
    )

    builder = InlineKeyboardBuilder()
    builder.row(build_inline_button("topup", callback_data=CB_TOPUP_START))
    builder.row(
        build_inline_button(
            "withdraw",
            callback_data=CB_WITHDRAW_START,
            text=withdraw_text,
        )
    )
    builder.row(build_inline_button("tx_history", callback_data=CB_TX_HISTORY))
    builder.row(build_inline_button("menu_back", callback_data=CB_BACK_TO_MENU))
    return builder.as_markup()


# =============================================================
# 6. KARTA ORQALI TO'LDIRISH — BEKOR QILISH
# =============================================================
def get_topup_cancel_kb() -> InlineKeyboardMarkup:
    """To'ldirish oqimida [ ❌ Yopish ] + [ ◀️ Bosh menyu ]."""
    builder = InlineKeyboardBuilder()
    builder.row(build_inline_button("close", callback_data=CB_TOPUP_CANCEL))
    builder.row(build_inline_button("menu_back", callback_data=CB_BACK_TO_MENU))
    return builder.as_markup()


# =============================================================
# 6.1. TO'LOV USULINI TANLASH (avtomatik XTR yoki karta)
# =============================================================
def get_topup_methods_kb() -> InlineKeyboardMarkup:
    """
    Balans to'ldirish usulini tanlash:

        [ ⚡️ Avtomatik to'lov (Telegram Stars) ]  (topup_auto, yashil)
        [ 💳 Uzcard / Humo kartaga o'tkazish ]    (topup_card)
        [ ❌ Yopish ]                             (topup_cancel)
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⚡️ Avtomatik to'lov (Telegram Stars)",
            callback_data=CB_TOPUP_AUTO,
            style="success",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="💳 Uzcard / Humo kartaga o'tkazish",
            callback_data=CB_TOPUP_CARD,
            style="primary",
        )
    )
    builder.row(build_inline_button("close", callback_data=CB_TOPUP_CANCEL))
    return builder.as_markup()


# =============================================================
# 7. ADMIN — TO'LDIRISHNI TASDIQLASH
# =============================================================
def get_deposit_decision_kb(deposit_id: int) -> InlineKeyboardMarkup:
    """
    Admin uchun karta chekini tasdiqlash/rad etish:

        [ ✅ Tasdiqlash ]  (dep_approve_{id}, yashil)
        [ ❌ Rad etish ]   (dep_reject_{id}, qizil)
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        build_inline_button(
            "approve", callback_data=f"dep_approve_{deposit_id}"
        ),
        build_inline_button(
            "reject", callback_data=f"dep_reject_{deposit_id}"
        ),
    )
    return builder.as_markup()
