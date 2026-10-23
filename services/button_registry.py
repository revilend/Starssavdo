"""
services/button_registry.py
===========================
Tugmalarning yagona reyestri: matn, rang (`style`) va custom emoji.

Nega kerak:
 - Telegram Bot API 9.4 `KeyboardButton`/`InlineKeyboardButton` uchun
   `style` (primary/success/danger) va `icon_custom_emoji_id` maydonlarini
   qo'shdi — ularni bitta joyda boshqarish uchun reyestr.
 - Admin panel (`🎛 Tugnalar va Emojilar`) tugma nomini va premium emojisini
   o'zgartira oladi; o'zgartirishlar `settings` jadvalida (`btn:<key>:text`,
   `btn:<key>:icon`) saqlanadi va jarayon xotirasidagi kesh orqali
   klaviaturalarga darhol qo'llanadi.
 - Handler'lar matn bo'yicha emas, **kalit** bo'yicha filtrlaydi
   (`ButtonText("menu_shop")`) — admin nomni o'zgartirsa ham handler ishlaydi.

Modul ataylab faqat aiogram'ga bog'langan (config/DB import qilmaydi):
`keyboards/*` va testlar uni `.env` bo'lmasa ham import qila oladi.
DB bilan ishlash — `services/button_store.py` da.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from aiogram.filters import Filter
from aiogram.types import InlineKeyboardButton, KeyboardButton, Message

from services.emoji_loader import find_custom_emoji_id

# =============================================================
# Style konstantalari (Telegram Bot API 9.4)
# =============================================================
STYLE_PRIMARY = "primary"  # ko'k
STYLE_SUCCESS = "success"  # yashil
STYLE_DANGER = "danger"    # qizil

# Settings jadvalidagi kalit prefiksi
SETTING_PREFIX = "btn:"


def text_setting_key(key: str) -> str:
    """Tugma matni saqlanadigan settings kaliti."""
    return f"{SETTING_PREFIX}{key}:text"


def icon_setting_key(key: str) -> str:
    """Tugma custom emoji ID'si saqlanadigan settings kaliti."""
    return f"{SETTING_PREFIX}{key}:icon"


# =============================================================
# Tugma spetsifikatsiyasi
# =============================================================
@dataclass(frozen=True)
class ButtonSpec:
    """Bitta tugmaning standart (default) ko'rinishi."""

    key: str
    text: str
    group: str
    style: str | None = None
    emoji: str | None = None  # matndagi fallback emoji (premium emoji topilmasa)
    emoji_sets: tuple[str, ...] = ()


# =============================================================
# REYESTR — barcha tahrirlanadigan tugmalar
# =============================================================
BUTTONS: dict[str, ButtonSpec] = {
    # --- Asosiy reply menyu ---
    "menu_shop": ButtonSpec(
        "menu_shop", "🛒 Do'kon", "menu", STYLE_PRIMARY, "🛒", ("duck", "duck_alt")
    ),
    "menu_balance": ButtonSpec(
        "menu_balance", "⭐ Balansim", "menu", STYLE_PRIMARY, "⭐", ("duck", "duck_alt")
    ),
    "menu_topup": ButtonSpec(
        "menu_topup", "💳 Balans to'ldirish", "menu", STYLE_SUCCESS, "💳", ("payment",)
    ),
    "menu_refer": ButtonSpec(
        "menu_refer",
        "👥 Do'stlarni taklif qilish",
        "menu",
        STYLE_SUCCESS,
        "👥",
        ("duck", "duck_alt"),
    ),
    "menu_gifts": ButtonSpec(
        "menu_gifts", "🐥 Mayda Giftlar", "menu", STYLE_PRIMARY, "🐥", ("duck", "duck_alt")
    ),
    "menu_guide": ButtonSpec(
        "menu_guide", "ℹ️ Qo'llanma", "menu", STYLE_PRIMARY, "ℹ️", ("duck", "duck_alt")
    ),
    "menu_admin": ButtonSpec(
        "menu_admin", "⚙️ Admin Panel", "menu", STYLE_DANGER, "⚙️", ("duck", "duck_alt")
    ),
    # --- Obuna / do'kon ---
    "check_subscription": ButtonSpec(
        "check_subscription", "✅ A'zo bo'ldim", "shop", STYLE_SUCCESS, "✅", ("status",)
    ),
    "shop_back": ButtonSpec("shop_back", "◀️ Orqaga", "shop", STYLE_PRIMARY),
    "cat_stars": ButtonSpec(
        "cat_stars", "⭐️ Stars xarid qilish", "shop", STYLE_PRIMARY, "⭐️", ("duck", "duck_alt")
    ),
    "cat_premium": ButtonSpec(
        "cat_premium", "💎 Telegram Premium", "shop", STYLE_PRIMARY, "💎", ("duck", "duck_alt")
    ),
    "cat_nft": ButtonSpec(
        "cat_nft", "🖼 Telegram NFT", "shop", STYLE_PRIMARY, "🖼", ("duck", "duck_alt")
    ),
    "cat_gift": ButtonSpec(
        "cat_gift", "🐥 Mayda Giftlar", "shop", STYLE_PRIMARY, "🐥", ("duck", "duck_alt")
    ),
    "products_back": ButtonSpec(
        "products_back", "◀️ Toifalarga qaytish", "shop", STYLE_PRIMARY
    ),
    "pay_xtr": ButtonSpec(
        "pay_xtr",
        "⭐️ Rasmiy Telegram Stars (XTR) bilan to'lash",
        "shop",
        STYLE_PRIMARY,
        "⭐️",
        ("payment",),
    ),
    "pay_balance": ButtonSpec(
        "pay_balance",
        "💳 Botdagi balansimdan sotib olish",
        "shop",
        STYLE_PRIMARY,
        "💳",
        ("payment",),
    ),
    # --- Referal / balans / payout ---
    "share": ButtonSpec("share", "📲 Do'stlarga ulashish", "referral", STYLE_PRIMARY),
    "top_referrers": ButtonSpec(
        "top_referrers", "🏆 Top taklif qiluvchilar", "referral", STYLE_PRIMARY
    ),
    "menu_back": ButtonSpec("menu_back", "◀️ Bosh menyu", "referral", STYLE_PRIMARY),
    "withdraw": ButtonSpec(
        "withdraw", "💸 Starslarni yechib olish", "payment", STYLE_SUCCESS, "💸", ("payment",)
    ),
    "topup": ButtonSpec(
        "topup", "💳 Balans to'ldirish", "payment", STYLE_SUCCESS, "💳", ("payment",)
    ),
    "tx_history": ButtonSpec(
        "tx_history", "📜 Tranzaksiyalar tarixi", "payment", STYLE_PRIMARY
    ),
    # --- Admin panel ---
    "adm_stats": ButtonSpec("adm_stats", "📊 Jonli statistika", "admin", STYLE_PRIMARY),
    "adm_broadcast": ButtonSpec(
        "adm_broadcast", "📢 Ommaviy xabar (Rassilka)", "admin", STYLE_PRIMARY
    ),
    "adm_channels": ButtonSpec(
        "adm_channels", "📢 Homiy kanallar", "admin", STYLE_PRIMARY
    ),
    "adm_ref_price": ButtonSpec(
        "adm_ref_price", "⚙️ Tizim sozlamalari", "admin", STYLE_PRIMARY
    ),
    "adm_withdrawals": ButtonSpec(
        "adm_withdrawals", "💳 Yechib olish so'rovlari", "admin", STYLE_PRIMARY
    ),
    "adm_balance_edit": ButtonSpec(
        "adm_balance_edit", "👤 Balansni o'zgartirish", "admin", STYLE_PRIMARY
    ),
    "adm_products": ButtonSpec(
        "adm_products", "🛍 Mahsulotlarni boshqarish", "admin", STYLE_PRIMARY
    ),
    "adm_gift_add": ButtonSpec(
        "adm_gift_add", "🎁 Giftlarni kiritish", "admin", STYLE_SUCCESS
    ),
    "adm_card": ButtonSpec(
        "adm_card", "💳 Karta ma'lumotlari", "admin", STYLE_SUCCESS
    ),
    "adm_buttons": ButtonSpec(
        "adm_buttons", "🎛 Tugnalar va Emojilar", "admin", STYLE_PRIMARY
    ),
    "adm_exit": ButtonSpec("adm_exit", "❌ Chiqish", "admin", STYLE_DANGER),
    "adm_back": ButtonSpec(
        "adm_back", "◀️ Admin panelga qaytish", "admin", STYLE_PRIMARY
    ),
    "adm_chadd": ButtonSpec(
        "adm_chadd", "➕ Yangi kanal qo'shish", "admin", STYLE_SUCCESS
    ),
    "approve": ButtonSpec("approve", "✅ Tasdiqlash", "admin", STYLE_SUCCESS, "✅", ("status",)),
    "reject": ButtonSpec("reject", "❌ Rad etish", "admin", STYLE_DANGER),
    "close": ButtonSpec("close", "❌ Yopish", "admin", STYLE_DANGER),
}

# Guruh sarlavhalari (admin panel ro'yxati uchun)
GROUP_TITLES: dict[str, str] = {
    "menu": "Asosiy menyu",
    "shop": "Do'kon va obuna",
    "referral": "Referal",
    "payment": "To'lov va balans",
    "admin": "Admin panel",
}


# =============================================================
# Runtime kesh (settings'dagi override'lar)
# =============================================================
_text_overrides: dict[str, str] = {}
_icon_overrides: dict[str, str] = {}


def is_known(key: str) -> bool:
    """Kalit reyestrda bormi."""
    return key in BUTTONS


def get_spec(key: str) -> ButtonSpec:
    """Tugma spetsifikatsiyasi (noma'lum kalitda KeyError)."""
    return BUTTONS[key]


def get_text(key: str) -> str:
    """Tugmaning joriy matni (admin override > default)."""
    if key in _text_overrides:
        return _text_overrides[key]
    return BUTTONS[key].text


def get_style(key: str) -> str | None:
    """Tugma rangi (`primary`/`success`/`danger`)."""
    return BUTTONS[key].style


def get_icon(key: str) -> str | None:
    """
    Tugma uchun `icon_custom_emoji_id`.

    Tartib: admin o'rnatgan ID -> yuklangan sticker to'plamlaridagi emoji.
    Admin ID'ni tozalagan bo'lsa (bo'sh satr) — None qaytadi.
    """
    if key in _icon_overrides:
        return _icon_overrides[key] or None

    spec = BUTTONS[key]
    return find_custom_emoji_id(spec.emoji, spec.emoji_sets)


def editable_keys(group: str | None = None) -> list[str]:
    """Tahrirlanadigan tugma kalitlari (guruh bo'yicha filtr bilan)."""
    return [
        key
        for key, spec in BUTTONS.items()
        if group is None or spec.group == group
    ]


def groups() -> list[str]:
    """Reyestrdagi guruhlar tartibi."""
    seen: list[str] = []
    for spec in BUTTONS.values():
        if spec.group not in seen:
            seen.append(spec.group)
    return seen


def apply_overrides(rows: Mapping[str, str]) -> None:
    """
    Settings'dan o'qilgan `btn:<key>:text|icon` yozuvlarini keshga qo'llaydi.

    Noto'g'ri kalitlar (reyestrda yo'q) e'tiborsiz qoldiriladi.
    """
    _text_overrides.clear()
    _icon_overrides.clear()

    for setting_key, value in rows.items():
        if not setting_key.startswith(SETTING_PREFIX):
            continue

        rest = setting_key[len(SETTING_PREFIX):]
        key, _, field = rest.rpartition(":")
        if key not in BUTTONS:
            continue

        if field == "text" and value:
            _text_overrides[key] = value
        elif field == "icon":
            _icon_overrides[key] = value


def set_override_in_cache(key: str, *, text: str | None = None, icon_id: str | None = None) -> None:
    """Bitta tugma override'ini keshda yangilaydi (DB yozuvidan keyin)."""
    if text is not None:
        _text_overrides[key] = text
    if icon_id is not None:
        _icon_overrides[key] = icon_id


def clear_override_in_cache(key: str) -> None:
    """Bitta tugma override'ini keshdan olib tashlaydi (reset)."""
    _text_overrides.pop(key, None)
    _icon_overrides.pop(key, None)


def clear_overrides() -> None:
    """Barcha override'larni tozalaydi (testlar uchun)."""
    _text_overrides.clear()
    _icon_overrides.clear()


def has_overrides(key: str) -> bool:
    """Tugmada admin o'zgartirishi bormi."""
    return key in _text_overrides or key in _icon_overrides


# =============================================================
# Klaviatura yordamchilari
# =============================================================
def build_inline_button(key: str, **kwargs: object) -> InlineKeyboardButton:
    """
    Inline tugmani reyestr ma'lumotlari bilan quradi.

    `kwargs` — `callback_data`, `url`, `switch_inline_query` va h.k.
    `text` berilmasa — reyestrdagi joriy matn ishlatiladi.
    """
    text = kwargs.pop("text", None) or get_text(key)
    return InlineKeyboardButton(
        text=str(text),
        style=get_style(key),
        icon_custom_emoji_id=get_icon(key),
        **kwargs,  # type: ignore[arg-type]
    )


def build_reply_button(key: str) -> KeyboardButton:
    """Reply (doimiy menyu) tugmasini reyestr ma'lumotlari bilan quradi."""
    return KeyboardButton(
        text=get_text(key),
        style=get_style(key),
        icon_custom_emoji_id=get_icon(key),
    )


# =============================================================
# Handler filtri (dinamik matn bo'yicha)
# =============================================================
class ButtonText(Filter):
    """
    Reply tugma bosilganini KALIT bo'yicha aniqlaydi.

    Admin tugma nomini o'zgartirsa ham ishlaydi (statik `F.text == "..."` dan
    farqli). Ishlatish:
        @router.message(ButtonText("menu_shop"))
    """

    def __init__(self, key: str) -> None:
        if key not in BUTTONS:
            raise ValueError(f"Noma'lum tugma kaliti: {key!r}")
        self.key = key

    async def __call__(self, message: Message) -> bool:
        return message.text == get_text(self.key)


def button_texts(keys: Iterable[str]) -> set[str]:
    """Berilgan kalitlarning joriy matnlari to'plami."""
    return {get_text(key) for key in keys}
