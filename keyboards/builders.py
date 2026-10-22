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
CB_ADM_BUTTONS = "adm_buttons"
CB_ADM_EXIT = "adm_exit"
CB_ADM_CH_ADD = "adm_chadd"
CB_ADM_BACK = "adm_back"
CB_ADM_BUTTON_PREFIX = "adm_btn_"


class _AdminChannelLike(Protocol):
    id: int
    title: str
    is_active: bool


# =============================================================
# 1. ADMIN BOSH MENYUSI
# =============================================================
def get_admin_main_kb() -> InlineKeyboardMarkup:
    """
    Admin panelning bosh klaviaturasi:

        [ 📊 Jonli statistika ]                [ 📢 Ommaviy xabar (Rassilka) ]
        [ 📢 Homiy kanallar ]                  [ ⭐️ Referal narxini sozlash ]
        [ 💳 Yechib olish so'rovlari ]         [ 👤 Balansni o'zgartirish ]
        [ 🎛 Tugnalar va Emojilar ]
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
        build_inline_button("adm_buttons", callback_data=CB_ADM_BUTTONS)
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
# 4. TUGMALAR VA EMOJILAR
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
