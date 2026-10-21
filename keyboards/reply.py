"""
keyboards/reply.py
==================
Asosiy doimiy (reply) menyu — bot bilan suhbatning bosh ekrani.

Xususiyatlar:
 - `resize_keyboard=True`  — tugmalar ekran kengligiga moslanadi.
 - `is_persistent=True`    — menyu har doim (hatto /start dan keyin ham) ko'rinadi.
 - Har bir tugma matni (BTN_*) konstant sifatida export qilinadi —
   handler'larda `message.text == BTN_SHOP` deb tekshirish qulay bo'ladi.

Ishlatish:
    from keyboards.reply import get_main_menu_kb, BTN_SHOP

    await message.answer("Menyu:", reply_markup=get_main_menu_kb(is_admin=False))
"""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

# -------------------------------------------------------------
# Asosiy menyu tugma matnlari (handlerlarda solishtirish uchun)
# -------------------------------------------------------------
BTN_SHOP = "🛍 Do'kon"
BTN_BALANCE = "⭐️ Balansim"
BTN_REFER = "👥 Do'stlarni taklif qilish"
BTN_GIFTS = "🎁 Mayda Giftlar"
BTN_GUIDE = "ℹ️ Qo'llanma"
BTN_ADMIN = "⚙️ Admin Panel"

# Menyu matnlarining to'plami — notanish matn kelganini aniqlash uchun
MAIN_MENU_TEXTS: frozenset[str] = frozenset(
    {BTN_SHOP, BTN_BALANCE, BTN_REFER, BTN_GIFTS, BTN_GUIDE, BTN_ADMIN}
)


def get_main_menu_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """
    Asosiy doimiy menyu (ReplyKeyboardMarkup).

    Tugmalar joylashuvi:
        [ 🛍 Do'kon ]              [ ⭐️ Balansim ]
        [ 👥 Do'stlarni taklif qilish ]  [ 🎁 Mayda Giftlar ]
        [ ℹ️ Qo'llanma ]
        (faqat admin uchun)  [ ⚙️ Admin Panel ]

    Args:
        is_admin: True — foydalanuvchi ADMIN_IDS da bo'lsa, qo'shimcha
                  "⚙️ Admin Panel" qatori qo'shiladi.

    Returns: resize_keyboard=True, is_persistent=True bilan reply keyboard.
    """
    builder = ReplyKeyboardBuilder()

    # 1-qator: do'kon + balans
    builder.row(
        KeyboardButton(text=BTN_SHOP),
        KeyboardButton(text=BTN_BALANCE),
    )
    # 2-qator: referal + mayda giftlar
    builder.row(
        KeyboardButton(text=BTN_REFER),
        KeyboardButton(text=BTN_GIFTS),
    )
    # 3-qator: qo'llanma (to'liq qator)
    builder.row(KeyboardButton(text=BTN_GUIDE))

    # Admin uchun qo'shimcha qator (faqat ADMIN_IDS dagi foydalanuvchida)
    if is_admin:
        builder.row(KeyboardButton(text=BTN_ADMIN))

    return builder.as_markup(
        resize_keyboard=True,   # tugmalar kichraytiriladi (zamonaviy ko'rinish)
        is_persistent=True,     # menyu doimiy — qayta yuborilmaydi ham ko'rinadi
    )
