"""
keyboards/builders.py
=====================
Admin panel uchun inline klaviaturalar (boshqaruv tugmalari).

Callback konventsiyalari:
 - adm_stats        — jonli statistika
 - adm_broadcast    — ommaviy xabar (rassilka)
 - adm_channels     — homiy kanallar boshqaruvi
 - adm_ref_price    — referal narxini sozlash
 - adm_withdrawals  — yechib olish so'rovlari
 - adm_balance_edit — foydalanuvchi balansini o'zgartirish
 - adm_exit         — admin paneldan chiqish
 - adm_toggle_{id}  — kanalni yoqish/o'chirish
 - adm_chdel_{id}   — kanalni o'chirish
 - adm_chadd        — yangi kanal qo'shish (forma ochish)
 - adm_back         — admin panelga qaytish
 - approve_payout_{id} / reject_payout_{id} — yechib olish qarori

Ishlatish:
    from keyboards.builders import get_admin_main_kb
    await msg.answer("Admin panel:", reply_markup=get_admin_main_kb())
"""

from __future__ import annotations

from typing import Protocol, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# -------------------------------------------------------------
# Callback data konstantalari
# -------------------------------------------------------------
CB_ADM_STATS = "adm_stats"
CB_ADM_BROADCAST = "adm_broadcast"
CB_ADM_CHANNELS = "adm_channels"
CB_ADM_REF_PRICE = "adm_ref_price"
CB_ADM_WITHDRAWALS = "adm_withdrawals"
CB_ADM_BALANCE_EDIT = "adm_balance_edit"
CB_ADM_EXIT = "adm_exit"
CB_ADM_CH_ADD = "adm_chadd"
CB_ADM_BACK = "adm_back"


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

        [ 📊 Jonli statistika ]            [ 📢 Ommaviy xabar (Rassilka) ]
        [ 📢 Homiy kanallar ]              [ ⭐️ Referal narxini sozlash ]
        [ 💳 Yechib olish so'rovlari ]     [ 👤 Balansni o'zgartirish ]
        [ ❌ Chiqish ]                     (to'liq qator)
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Jonli statistika", callback_data=CB_ADM_STATS),
        InlineKeyboardButton(text="📢 Ommaviy xabar (Rassilka)", callback_data=CB_ADM_BROADCAST),
    )
    builder.row(
        InlineKeyboardButton(text="📢 Homiy kanallar", callback_data=CB_ADM_CHANNELS),
        InlineKeyboardButton(text="⭐️ Referal narxini sozlash", callback_data=CB_ADM_REF_PRICE),
    )
    builder.row(
        InlineKeyboardButton(text="💳 Yechib olish so'rovlari", callback_data=CB_ADM_WITHDRAWALS),
        InlineKeyboardButton(text="👤 Balansni o'zgartirish", callback_data=CB_ADM_BALANCE_EDIT),
    )
    # Chiqish — alohida to'liq qator (tasan-tus tushmasligi uchun)
    builder.row(InlineKeyboardButton(text="❌ Chiqish", callback_data=CB_ADM_EXIT))
    return builder.as_markup()


# =============================================================
# 2. HOMIY KANALLAR BOSHQARUVI
# =============================================================
def get_admin_channel_manage_kb(
    channels: Sequence[_AdminChannelLike],
) -> InlineKeyboardMarkup:
    """
    Majburiy kanallarni boshqarish klaviaturasi.

    Har bir kanal uchun bitta qator, ikkita tugma:
        [ ✅ Kanal nomi ]  — yoqish/o'chirish (adm_toggle_{id})
        [ 🗑 ]             — o'chirish (adm_chdel_{id})

    Emoji holatni bildiradi: ✅ faol, 🚫 nofaol.

    Pastda:
        [ ➕ Yangi kanal qo'shish ]   (adm_chadd)
        [ ◀️ Admin panelga qaytish ]  (adm_back)

    Kanal bo'lmasa — faqat qo'shish va qaytish tugmalari.
    """
    builder = InlineKeyboardBuilder()

    for channel in channels:
        status_emoji = "✅" if channel.is_active else "🚫"
        builder.row(
            InlineKeyboardButton(
                text=f"{status_emoji} {channel.title}",
                callback_data=f"adm_toggle_{channel.id}",
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=f"adm_chdel_{channel.id}",
            ),
        )

    builder.row(
        InlineKeyboardButton(text="➕ Yangi kanal qo'shish", callback_data=CB_ADM_CH_ADD)
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Admin panelga qaytish", callback_data=CB_ADM_BACK)
    )
    return builder.as_markup()


# =============================================================
# 3. YECHIB OLISH SO'ROVI QARORI
# =============================================================
def get_withdrawal_decision_kb(withdrawal_id: int) -> InlineKeyboardMarkup:
    """
    Bitta yechib olish so'rovi uchun qaror tugmalari:

        [ ✅ Tasdiqlash ]  (approve_payout_{id})
        [ ❌ Rad etish ]   (reject_payout_{id})
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Tasdiqlash",
            callback_data=f"approve_payout_{withdrawal_id}",
        ),
        InlineKeyboardButton(
            text="❌ Rad etish",
            callback_data=f"reject_payout_{withdrawal_id}",
        ),
    )
    return builder.as_markup()
