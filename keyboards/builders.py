"""
keyboards/builders.py
=====================
Admin panel uchun inline klaviaturalar (boshqaruv tugmalari).

Barcha statik tugmalar `services.button_registry` orqali quriladi —
matn/rang/premium emoji admin panelda (`🎛 Tugnalar va Emojilar`)
o'zgartirilishi mumkin.

Callback konventsiyalari:
 - adm_stats        — jonli statistika
 - adm_broadcast    — ommaviy xabar (rassilka)
 - adm_channels     — homiy kanallar boshqaruvi
 - adm_ref_price    — referal narxini sozlash
 - adm_withdrawals  — yechib olish so'rovlari
 - adm_balance_edit — foydalanuvchi balansini o'zgartirish
 - adm_products     — mahsulotlarni boshqarish (tahrirlash/o'chirish)
 - adm_gift_add     — yangi gift kiritish (nom/narx/ta'rif/rasm/gift_id)
 - adm_card         — to'lov kartasi rekvizitlarini o'zgartirish
 - adm_buttons      — tugmalar va premium emojilarni boshqarish
 - adm_exit         — admin paneldan chiqish
 - adm_toggle_{id}  — kanalni yoqish/o'chirish
 - adm_chdel_{id}   — kanalni o'chirish
 - adm_chadd        — yangi kanal qo'shish (forma ochish)
 - adm_back         — admin panelga qaytish
 - approve_payout_{id} / reject_payout_{id} — yechib olish qarori
 - adm_btn_{key}    — bitta tugmani tahrirlash
"""

from __future__ import annotations

from typing import Protocol, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.button_registry import build_inline_button

# -------------------------------------------------------------
# Callback data konstantalari
# -------------------------------------------------------------
CB_ADM_STATS = "adm_stats"
CB_ADM_BROADCAST = "adm_broadcast"
CB_ADM_CHANNELS = "adm_channels"
CB_ADM_REF_PRICE = "adm_ref_price"
CB_ADM_WITHDRAWALS = "adm_withdrawals"
CB_ADM_BALANCE_EDIT = "adm_balance_edit"
CB_ADM_PRODUCTS = "adm_products"
CB_ADM_GIFT_ADD = "adm_gift_add"
CB_ADM_CARD = "adm_card"
CB_ADM_BUTTONS = "adm_buttons"
CB_ADM_EXIT = "adm_exit"
CB_ADM_CH_ADD = "adm_chadd"
CB_ADM_BACK = "adm_back"
CB_ADM_BUTTON_PREFIX = "adm_btn_"
# Mahsulotlar bo'limi (admin CRUD)
CB_ADM_PROD_NEW = "adm_pnew"                 # yangi mahsulot — toifa tanlash
CB_ADM_PROD_LIST_PREFIX = "adm_plist_"       # + category
CB_ADM_PROD_ADD_PREFIX = "adm_padd_"         # + category
CB_ADM_PROD_DETAIL_PREFIX = "adm_pdet_"      # + product_id
CB_ADM_PROD_EDIT_PREFIX = "adm_pedit_"       # + product_id + "_" + field
CB_ADM_PROD_TOGGLE_PREFIX = "adm_ptgl_"      # + product_id
CB_ADM_PROD_DELETE_PREFIX = "adm_pdel_"      # + product_id
CB_ADM_PROD_DELETE_OK_PREFIX = "adm_pdelok_"  # + product_id


class _AdminChannelLike(Protocol):
    id: int
    title: str
    is_active: bool


class _AdminProductLike(Protocol):
    id: int
    category: str
    title: str
    price_xtr: int
    is_active: bool


# =============================================================
# 1. ADMIN BOSH MENYUSI
# =============================================================
def get_admin_main_kb() -> InlineKeyboardMarkup:
    """
    Admin panelning bosh klaviaturasi:

        [ 📊 Jonli statistika ]                [ 📢 Ommaviy xabar (Rassilka) ]
        [ 📢 Homiy kanallar ]                  [ ⚙️ Tizim sozlamalari ]
        [ 💳 Yechib olish so'rovlari ]         [ 👤 Balansni o'zgartirish ]
        [ 🎁 Giftlarni kiritish ]              [ 🛍 Mahsulotlarni boshqarish ]
        [ 💳 Karta ma'lumotlari ]              [ 🎛 Tugnalar va Emojilar ]
        [ ❌ Chiqish ]
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        build_inline_button("adm_stats", callback_data=CB_ADM_STATS),
        build_inline_button("adm_broadcast", callback_data=CB_ADM_BROADCAST),
    )
    builder.row(
        build_inline_button("adm_channels", callback_data=CB_ADM_CHANNELS),
        build_inline_button("adm_ref_price", callback_data=CB_ADM_REF_PRICE),
    )
    builder.row(
        build_inline_button("adm_withdrawals", callback_data=CB_ADM_WITHDRAWALS),
        build_inline_button("adm_balance_edit", callback_data=CB_ADM_BALANCE_EDIT),
    )
    builder.row(
        build_inline_button("adm_gift_add", callback_data=CB_ADM_GIFT_ADD),
        build_inline_button("adm_products", callback_data=CB_ADM_PRODUCTS),
    )
    builder.row(
        build_inline_button("adm_card", callback_data=CB_ADM_CARD),
        build_inline_button("adm_buttons", callback_data=CB_ADM_BUTTONS),
    )
    builder.row(build_inline_button("adm_exit", callback_data=CB_ADM_EXIT))
    return builder.as_markup()


# =============================================================
# 2. HOMIY KANALLAR BOSHQARUVI
# =============================================================
def get_admin_channel_manage_kb(
    channels: Sequence[_AdminChannelLike],
) -> InlineKeyboardMarkup:
    """
    Majburiy kanallarni boshqarish klaviaturasi.

    Har bir kanal uchun bitta qator:
        [ ✅ Kanal nomi ]  — yoqish/o'chirish (adm_toggle_{id})
        [ 🗑 ]             — o'chirish (adm_chdel_{id})

    Pastda:
        [ ➕ Yangi kanal qo'shish ]   (adm_chadd)
        [ ◀️ Admin panelga qaytish ]  (adm_back)
    """
    builder = InlineKeyboardBuilder()

    for channel in channels:
        status_emoji = "✅" if channel.is_active else "🚫"
        builder.row(
            InlineKeyboardButton(
                text=f"{status_emoji} {channel.title}",
                callback_data=f"adm_toggle_{channel.id}",
                style="success" if channel.is_active else "danger",
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=f"adm_chdel_{channel.id}",
                style="danger",
            ),
        )

    builder.row(
        build_inline_button("adm_chadd", callback_data=CB_ADM_CH_ADD)
    )
    builder.row(
        build_inline_button("adm_back", callback_data=CB_ADM_BACK)
    )
    return builder.as_markup()


# =============================================================
# 3. YECHIB OLISH SO'ROVI QARORI
# =============================================================
def get_withdrawal_decision_kb(withdrawal_id: int) -> InlineKeyboardMarkup:
    """
    Bitta yechib olish so'rovi uchun qaror tugmalari:

        [ ✅ Tasdiqlash ]  (approve_payout_{id}, yashil)
        [ ❌ Rad etish ]   (reject_payout_{id}, qizil)
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        build_inline_button(
            "approve", callback_data=f"approve_payout_{withdrawal_id}"
        ),
        build_inline_button(
            "reject", callback_data=f"reject_payout_{withdrawal_id}"
        ),
    )
    return builder.as_markup()


# =============================================================
# 4. MAHSULOTLAR (ADMIN CRUD)
# =============================================================
PRODUCT_CATEGORY_LABELS: dict[str, str] = {
    "stars": "⭐️ Stars",
    "premium": "💎 Premium",
    "nft": "🖼 NFT",
    "gift": "🎁 Mayda Giftlar",
}


def get_admin_products_kb(counts: dict[str, int] | None = None) -> InlineKeyboardMarkup:
    """
    🛍 Mahsulotlarni boshqarish — bosh ekran.

    Birinchi qatorda barcha toifalardagi sotiladigan narsalar umumiy
    ro'yxati, undan keyin toifalar (soni bilan).

    Args:
        counts: {'stars': 3, 'premium': 1, ...} — ixtiyoriy.
    """
    counts = counts or {}
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"📋 Barcha mahsulotlar ({sum(counts.values())})",
            callback_data=f"{CB_ADM_PROD_LIST_PREFIX}all",
            style="primary",
        )
    )
    for category, label in PRODUCT_CATEGORY_LABELS.items():
        count = counts.get(category, 0)
        builder.row(
            InlineKeyboardButton(
                text=f"{label} ({count})",
                callback_data=f"{CB_ADM_PROD_LIST_PREFIX}{category}",
                style="primary",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="➕ Yangi mahsulot qo'shish",
            callback_data=CB_ADM_PROD_NEW,
            style="success",
        )
    )
    builder.row(build_inline_button("adm_back", callback_data=CB_ADM_BACK))
    return builder.as_markup()


def get_product_category_kb() -> InlineKeyboardMarkup:
    """Yangi mahsulot uchun toifani tanlash."""
    builder = InlineKeyboardBuilder()
    for category, label in PRODUCT_CATEGORY_LABELS.items():
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=f"{CB_ADM_PROD_ADD_PREFIX}{category}",
                style="success",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Orqaga", callback_data=CB_ADM_PRODUCTS, style="primary"
        )
    )
    return builder.as_markup()


def get_admin_product_list_kb(
    products: Sequence[_AdminProductLike], category: str
) -> InlineKeyboardMarkup:
    """
    Toifa mahsulotlari ro'yxati.

        [ ✅ Stars 100 — 100⭐️ ]  (adm_pdet_{id})
        [ ➕ Shu toifaga qo'shish ]  (adm_padd_{category})
        [ ◀️ Toifalarga qaytish ]    (adm_products)
    """
    builder = InlineKeyboardBuilder()
    for product in products:
        status = "✅" if product.is_active else "🚫"
        builder.row(
            InlineKeyboardButton(
                text=f"{status} {product.title} — {product.price_xtr}⭐️",
                callback_data=f"{CB_ADM_PROD_DETAIL_PREFIX}{product.id}",
                style="primary" if product.is_active else "danger",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="➕ Yangi mahsulot qo'shish",
            callback_data=CB_ADM_PROD_NEW,
            style="success",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Toifalarga qaytish",
            callback_data=CB_ADM_PRODUCTS,
            style="primary",
        )
    )
    return builder.as_markup()


def get_product_detail_kb(product: _AdminProductLike) -> InlineKeyboardMarkup:
    """Bitta mahsulot kartasi: tahrirlash, faollik, o'chirish, orqaga."""
    product_id = product.id
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✏️ Nomi",
            callback_data=f"{CB_ADM_PROD_EDIT_PREFIX}{product_id}_title",
            style="primary",
        ),
        InlineKeyboardButton(
            text="💰 Narxi",
            callback_data=f"{CB_ADM_PROD_EDIT_PREFIX}{product_id}_price",
            style="primary",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="📝 Tavsif",
            callback_data=f"{CB_ADM_PROD_EDIT_PREFIX}{product_id}_description",
            style="primary",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🖼 Rasm",
            callback_data=f"{CB_ADM_PROD_EDIT_PREFIX}{product_id}_image",
            style="success",
        )
    )
    if product.category == "gift":
        builder.row(
            InlineKeyboardButton(
                text="🎁 Gift ID",
                callback_data=f"{CB_ADM_PROD_EDIT_PREFIX}{product_id}_gift",
                style="success",
            )
        )
    toggle_text = "🚫 Nofaol qilish" if product.is_active else "✅ Faollashtirish"
    builder.row(
        InlineKeyboardButton(
            text=toggle_text,
            callback_data=f"{CB_ADM_PROD_TOGGLE_PREFIX}{product_id}",
            style="danger" if product.is_active else "success",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🗑 O'chirish",
            callback_data=f"{CB_ADM_PROD_DELETE_PREFIX}{product_id}",
            style="danger",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Ro'yxatga qaytish",
            callback_data=f"{CB_ADM_PROD_LIST_PREFIX}{product.category}",
            style="primary",
        )
    )
    return builder.as_markup()


def get_product_delete_confirm_kb(product: _AdminProductLike) -> InlineKeyboardMarkup:
    """O'chirishni tasdiqlash (yoki bekor qilish)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🗑 Ha, o'chirish",
            callback_data=f"{CB_ADM_PROD_DELETE_OK_PREFIX}{product.id}",
            style="danger",
        ),
        InlineKeyboardButton(
            text="◀️ Bekor qilish",
            callback_data=f"{CB_ADM_PROD_DETAIL_PREFIX}{product.id}",
            style="primary",
        ),
    )
    return builder.as_markup()


# =============================================================
# 5. TUGMALAR VA EMOJILAR
# =============================================================
def get_buttons_manage_kb(buttons: Sequence[tuple[str, str]]) -> InlineKeyboardMarkup:
    """
    Tahrirlanadigan tugmalar ro'yxati.

    Args:
        buttons: [(key, ko'rinadigan_matn), ...] — guruh bo'yicha tartiblangan.
    """
    builder = InlineKeyboardBuilder()
    for key, label in buttons:
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=f"{CB_ADM_BUTTON_PREFIX}{key}",
                style="primary",
            )
        )
    builder.row(build_inline_button("adm_back", callback_data=CB_ADM_BACK))
    return builder.as_markup()


def get_button_edit_kb(key: str) -> InlineKeyboardMarkup:
    """Bitta tugma uchun: nom, emoji, tiklash, orqaga."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✏️ Nomini o'zgartirish",
            callback_data=f"adm_btnedit_text_{key}",
            style="primary",
        ),
        InlineKeyboardButton(
            text="😀 Premium emoji",
            callback_data=f"adm_btnedit_icon_{key}",
            style="success",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="♻️ Default holatga qaytarish",
            callback_data=f"adm_btnreset_{key}",
            style="danger",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Ro'yxatga qaytish",
            callback_data=CB_ADM_BUTTONS,
            style="primary",
        )
    )
    return builder.as_markup()
