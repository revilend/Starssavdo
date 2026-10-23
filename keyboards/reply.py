"""
keyboards/reply.py
==================
Asosiy doimiy (reply) menyu — bot bilan suhbatning bosh ekrani.

Xususiyatlar:
 - `resize_keyboard=True`  — tugmalar ekran kengligiga moslanadi.
 - `is_persistent=True`    — menyu har doim ko'rinadi.
 - Har bir tugma matni/rangi/premium emojisi `services.button_registry`
   orqali olinadi — admin panelda o'zgartirilsa, keyingi klaviatura
   yangilangan ko'rinishda chiqadi.
 - `style`: primary (ko'k), success (yashil), danger (qizil) —
   Telegram Bot API 9.4.

Ishlatish:
    from keyboards.reply import get_main_menu_kb

    await message.answer("Menyu:", reply_markup=get_main_menu_kb(is_admin=False))
"""

from __future__ import annotations

from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from services.button_registry import BUTTONS, build_reply_button

# -------------------------------------------------------------
# Asosiy menyu tugma kalitlari (tartib muhim)
# -------------------------------------------------------------
MENU_KEYS: tuple[str, ...] = (
    "menu_shop",
    "menu_balance",
    "menu_refer",
    "menu_gifts",
    "menu_topup",
    "menu_guide",
)

# Handlerlar/testlar uchun default matnlar (admin override bo'lmasa).
BTN_SHOP = BUTTONS["menu_shop"].text
BTN_BALANCE = BUTTONS["menu_balance"].text
BTN_TOPUP = BUTTONS["menu_topup"].text
BTN_REFER = BUTTONS["menu_refer"].text
BTN_GIFTS = BUTTONS["menu_gifts"].text
BTN_GUIDE = BUTTONS["menu_guide"].text
BTN_ADMIN = BUTTONS["menu_admin"].text

# Menyu matnlarining to'plami (default holatda) — testlar/validatsiya uchun
MAIN_MENU_TEXTS: frozenset[str] = frozenset(
    BUTTONS[key].text for key in (*MENU_KEYS, "menu_admin")
)


def get_main_menu_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """
    Asosiy doimiy menyu (ReplyKeyboardMarkup).

    Tugmalar joylashuvi:
        [ 🛒 Do'kon ]                    [ ⭐ Balansim ]
        [ 👥 Do'stlarni taklif qilish ]  [ 🐥 Mayda Giftlar ]
        [ 💳 Balans to'ldirish ]
        [ ℹ️ Qo'llanma ]
        (faqat admin uchun)  [ ⚙️ Admin Panel ]

    Args:
        is_admin: True — qo'shimcha "⚙️ Admin Panel" qatori qo'shiladi.
    """
    builder = ReplyKeyboardBuilder()

    # 1-qator: do'kon + balans
    builder.row(
        build_reply_button("menu_shop"),
        build_reply_button("menu_balance"),
    )
    # 2-qator: referal + mayda giftlar
    builder.row(
        build_reply_button("menu_refer"),
        build_reply_button("menu_gifts"),
    )
    # 3-qator: balans to'ldirish (to'liq qator)
    builder.row(build_reply_button("menu_topup"))
    # 4-qator: qo'llanma
    builder.row(build_reply_button("menu_guide"))

    # Admin uchun qo'shimcha qator
    if is_admin:
        builder.row(build_reply_button("menu_admin"))

    return builder.as_markup(
        resize_keyboard=True,
        is_persistent=True,
    )
