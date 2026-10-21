"""
keyboards/inline.py
===================
Botning asosiy inline (ichki) klaviaturalari:
obuna tekshiruvi, do'kon, referal bo'limi va balans bo'limi.

Navigatsiya qoidalari:
 - Har bir bo'limda "◀️ Orqaga" yoki "◀️ Bosh menyu" tugmasi MAVJUD.
 - Bosh menyuga qaytish callback'i yagona: `back_to_menu`
   (barcha bo'limlardan handler bitta joyda tutadi).

Callback konventsiyalar (handlerlar uchun):
 - check_subscription     — kanal a'zoligini tekshirish
 - cat_{category}         — do'kon toifasi (stars/premium/nft/gift)
 - shop_categories        — toifalarga qaytish
 - buy_prod_{product_id}  — mahsulot xaridi (usul tanlash)
 - pay_xtr_{product_id}   — rasmiy Telegram Stars (XTR) bilan to'lov
 - pay_balance_{product_id} — bot balansidan xarid
 - top_referrers          — top taklif qiluvchilar reytingi
 - withdraw_start         — yechib olishni boshlash
 - tx_history             — tranzaksiya tarixi
 - back_to_menu           — bosh menyu

Ishlatish:
    from keyboards.inline import get_shop_categories_kb
    await msg.answer("Do'kon:", reply_markup=get_shop_categories_kb())
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# -------------------------------------------------------------
# Callback data konstantalari (string magic yo'q — bitta manba)
# -------------------------------------------------------------
CB_CHECK_SUBSCRIPTION = "check_subscription"
CB_BACK_TO_MENU = "back_to_menu"
CB_SHOP_CATEGORIES = "shop_categories"
CB_TOP_REFERRERS = "top_referrers"
CB_WITHDRAW_START = "withdraw_start"
CB_TX_HISTORY = "tx_history"

# Xarid callback prefikslari (handlerlar `F.data.startswith(...)` bilan tutadi)
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
# Tip protokollari — repository modellariga bog'lanmaydi
# (unittest/dummy obyektlar bilan ham ishlaydi)
# -------------------------------------------------------------
class _ChannelLike(Protocol):
    invite_link: str
    title: str


def _channel_field(channel: Any, key: str) -> Any:
    """
    Kanal maydonini olish — dict YOKI ORM obyekt uchun ham ishlaydi.

    Servis `list[dict]` uzatadi, repository esa ORM obyektlar qaytaradi —
    ikkala shakl ham qo'llab-quvvatlanadi.
    """
    if isinstance(channel, Mapping):
        return channel[key]
    return getattr(channel, key)


class _ProductLike(Protocol):
    id: int
    title: str
    price_xtr: int


# =============================================================
# 1. OBUNA (majburiy homiy kanallar)
# =============================================================
def get_subscription_kb(channels: Sequence[_ChannelLike]) -> InlineKeyboardMarkup:
    """
    Majburiy kanallarga a'zo bo'lish klaviaturasi.

    Tuzilishi:
        [ 📢 1-Kanalga a'zo bo'lish ]   (url — invite_link)
        [ 📢 2-Kanalga a'zo bo'lish ]   (url — invite_link)
        ...
        [ ✅ A'zo bo'ldim ]             (callback="check_subscription")

    Har bir kanal alohida qatorda (to'liq kenglikda) — bosish qulay.
    Pastdagi katta yashil tugma kanallar sonini tekshiradi.
    """
    builder = InlineKeyboardBuilder()

    # Har bir homiy kanal uchun URL tugma (raqamlash: 1-Kanal, 2-Kanal, ...)
    for index, channel in enumerate(channels, start=1):
        builder.button(
            text=f"📢 {index}-Kanalga a'zo bo'lish",
            url=_channel_field(channel, "invite_link"),
        )
    # Kanallar vertikal (har bir qatorda bittadan)
    builder.adjust(1)

    # Yagona tekshiruv tugmasi — to'liq kenglikda, yashil emoji bilan
    builder.row(
        InlineKeyboardButton(
            text="✅ A'zo bo'ldim",
            callback_data=CB_CHECK_SUBSCRIPTION,
        )
    )
    return builder.as_markup()


# =============================================================
# 2. DO'KON — TOIFALAR
# =============================================================
def get_shop_categories_kb() -> InlineKeyboardMarkup:
    """
    Do'kon toifalari klaviaturasi:

        [ ⭐️ Stars xarid qilish ]      (cat_stars)
        [ 💎 Telegram Premium ]        (cat_premium)
        [ 🖼 Telegram NFT ]            (cat_nft)
        [ 🎁 Mayda Giftlar ]           (cat_gift)
        [ ◀️ Orqaga ]                  (back_to_menu)
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐️ Stars xarid qilish", callback_data="cat_stars")
    builder.button(text="💎 Telegram Premium", callback_data="cat_premium")
    builder.button(text="🖼 Telegram NFT", callback_data="cat_nft")
    builder.button(text="🎁 Mayda Giftlar", callback_data="cat_gift")
    builder.adjust(1)  # har bir toifa alohida qatorda
    builder.row(
        InlineKeyboardButton(text=BACK_BTN_TEXT, callback_data=CB_BACK_TO_MENU)
    )
    return builder.as_markup()


# =============================================================
# 3. DO'KON — MAHSULOTLAR RO'YXATI
# =============================================================
def get_products_list_kb(
    products: Sequence[_ProductLike], category: str
) -> InlineKeyboardMarkup:
    """
    Tanlangan toifa bo'yicha mahsulotlar ro'yxati.

    Har bir mahsulot:
        text          = "{emoji} {title} — {narx} XTR"
                        masalan: "⭐️ 100 Stars — 100 XTR"
        callback_data = "buy_prod_{product_id}"

    Pastda qaytish tugmasi:
        [ ◀️ Toifalarga qaytish ]  (shop_categories)

    Mahsulot bo'lmasa — faqat qaytish tugmasi qaytadi
    (handler "bo'sh" xabarni ko'rsatadi).
    """
    builder = InlineKeyboardBuilder()
    emoji = CATEGORY_EMOJI.get(category, "🛍")

    for product in products:
        builder.button(
            text=f"{emoji} {product.title} — {product.price_xtr} XTR",
            callback_data=f"buy_prod_{product.id}",
        )

    if products:
        builder.adjust(1)  # mahsulotlar vertikal ro'yxatda

    builder.row(
        InlineKeyboardButton(
            text="◀️ Toifalarga qaytish",
            callback_data=CB_SHOP_CATEGORIES,
        )
    )
    return builder.as_markup()


# =============================================================
# 3.1. DO'KON — XARID USULINI TANLASH
# =============================================================
def get_payment_methods_kb(product_id: int) -> InlineKeyboardMarkup:
    """
    Xarid usulini tanlash klaviaturasi (mahsulot tafsilotlari ostida):

        [ ⭐️ Rasmiy Telegram Stars (XTR) bilan to'lash ]  (pay_xtr_{id})
        [ 💳 Botdagi balansimdan yechish ]                (pay_balance_{id})
        [ ◀️ Orqaga ]                                      (shop_categories)
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⭐️ Rasmiy Telegram Stars (XTR) bilan to'lash",
        callback_data=f"{CB_PAY_XTR_PREFIX}{product_id}",
    )
    builder.button(
        text="💳 Botdagi balansimdan yechish",
        callback_data=f"{CB_PAY_BALANCE_PREFIX}{product_id}",
    )
    builder.adjust(1)  # usullar vertikal — bosish qulay
    builder.row(
        InlineKeyboardButton(text=BACK_BTN_TEXT, callback_data=CB_SHOP_CATEGORIES)
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

    "Do'stlarga ulashish" — Telegramning inline-query mexanizmi:
    bosilganda foydalanuvchi do'stlaridan birini tanlaydi va bot
    havolasi (deep-link) tayyor matn ko'rinishida kiritiladi —
    bir bosishda jo'natish qoladi.

    Deep-link konventsiyasi: https://t.me/{bot_username}?start=ref_{user_id}
    """
    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    share_text = (
        "⭐️ Do'st, keling birga o'ynaymiz! 💎 NFT, Premium va "
        "Mayda Giftlar shu botda:\n" + referral_link
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="📲 Do'stlarga ulashish", switch_inline_query=share_text)
    builder.button(text="🏆 Top taklif qiluvchilar", callback_data=CB_TOP_REFERRERS)
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text=MENU_BTN_TEXT, callback_data=CB_BACK_TO_MENU)
    )
    return builder.as_markup()


# =============================================================
# 5. BALANS BO'LIMI
# =============================================================
def get_balance_kb(stars_balance: int, min_withdrawal: int) -> InlineKeyboardMarkup:
    """
    Balans bo'limi klaviaturasi:

        [ 💸 Starslarni yechib olish ]  (withdraw_start)
        [ 📜 Tranzaksiyalar tarixi ]    (tx_history)
        [ ◀️ Bosh menyu ]               (back_to_menu)

    Args:
        stars_balance: joriy balans.
        min_withdrawal: minimal yechib olish miqdori — tugma matnida
            ko'rsatiladi, masalan: "💸 50⭐️ dan yechib olish".
            Agar balans yetarli bo'lmasa tugma matni baribir ko'rinadi,
            handler tekshiradi (emoji bilan cheklangan holatni ham
            ko'rsatish qulay).
    """
    # Balans yetarli bo'lsa — miqdorni matnda ko'rsatamiz
    if stars_balance >= min_withdrawal:
        withdraw_text = f"💸 Starslarni yechib olish ({stars_balance}⭐️)"
    else:
        withdraw_text = f"💸 Yechib olish (min {min_withdrawal}⭐️)"

    builder = InlineKeyboardBuilder()
    builder.button(text=withdraw_text, callback_data=CB_WITHDRAW_START)
    builder.button(text="📜 Tranzaksiyalar tarixi", callback_data=CB_TX_HISTORY)
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text=MENU_BTN_TEXT, callback_data=CB_BACK_TO_MENU)
    )
    return builder.as_markup()
