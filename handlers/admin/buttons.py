"""
handlers/admin/buttons.py
=========================
Admin panel: `🎛 Tugnalar va Emojilar` bo'limi.

Imkoniyatlar:
 - Barcha tahrirlanadigan tugmalar ro'yxati (guruhlar bo'yicha);
 - Tugma nomini o'zgartirish (`btn:<key>:text`);
 - Premium emoji o'rnatish — admin yuborgan xabardagi `custom_emoji`
   entity'sidan `custom_emoji_id` olinadi (`btn:<key>:icon`);
 - Default holatga qaytarish (override'larni o'chirish);
 - Har bir o'zgarishdan keyin jonli preview (tugma yangi ko'rinishda).

O'zgarishlar `services.button_store` orqali `settings` jadvaliga yoziladi
va jarayon keshiga darhol qo'llanadi — keyingi klaviaturalar yangilanadi.
Handler'lar matn bo'yicha emas, `ButtonText(key)` filtri bilan ishlaydi,
shuning uchun nom o'zgarsa ham bot ishlashda davom etadi.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import settings
from keyboards.builders import (
    CB_ADM_BUTTON_PREFIX,
    CB_ADM_BUTTONS,
    get_button_edit_kb,
    get_buttons_manage_kb,
)
from services import button_registry as registry
from services.button_store import (
    reset_button,
    save_button_icon,
    save_button_text,
)
from states.admin_states import ButtonEditStates

logger = logging.getLogger(__name__)

router = Router(name="admin_buttons")

# faqat adminlar uchun
router.message.filter(F.from_user.id.in_(settings.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.ADMIN_IDS))

MAX_TEXT_LENGTH = 64
MAX_ICON_LENGTH = 64

BUTTONS_MENU_TEXT = (
    "🎛 <b>TUGNALAR VA EMOJILAR</b>\n\n"
    "Quyidagi tugmalardan birini tanlab, nomini yoki premium emojisini "
    "o'zgartirishingiz mumkin. O'zgarish darhol barcha klaviaturalarga "
    "qo'llanadi.\n\n"
    "<i>Premium emoji faqat bot egasida Telegram Premium bo'lsa "
    "ko'rinadi.</i>"
)

BUTTON_DETAIL_TEXT = (
    "🎛 <b>TUGMA SOZLAMALARI</b>\n\n"
    "├ Kalit: <code>{key}</code>\n"
    "├ Guruh: <b>{group}</b>\n"
    "├ Matn: <b>{text}</b>\n"
    "├ Rang: <code>{style}</code>\n"
    "└ Emoji ID: <code>{icon}</code>\n\n"
    "Nimani o'zgartirmoqchisiz?"
)

ASK_TEXT = (
    "✏️ <b>Yangi nom</b>\n\n"
    "Tugma uchun yangi matn yuboring (1–{max_len} belgi).\n"
    "Masalan: <code>🛍 Bozor</code>\n\n"
    "Bekor qilish: /cancel"
)

ASK_EMOJI = (
    "😀 <b>Premium emoji</b>\n\n"
    "Premium emojini yuboring (Telegram Premium emoji klaviaturasidan) "
    "yoki uning <code>custom_emoji_id</code> raqamini kiriting.\n\n"
    "Bekor qilish: /cancel"
)

TEXT_SAVED = "✅ Tugma nomi saqlandi: <b>{text}</b>"
ICON_SAVED = "✅ Premium emoji saqlandi: <code>{icon}</code>"
RESET_DONE = "♻️ Tugma default holatga qaytarildi."
CANCELLED = "❌ Bekor qilindi."


# =============================================================
# Yordamchilar
# =============================================================
def _extract_custom_emoji_id(message: Message) -> str | None:
    """
    Xabardan `custom_emoji_id` ni ajratib oladi.

    Avval `custom_emoji` entity qidiriladi; agar admin to'g'ridan-to'g'ri
    raqamli ID yuborgan bo'lsa — o'sha qaytariladi.
    """
    for entity in message.entities or []:
        if entity.type == "custom_emoji" and entity.custom_emoji_id:
            return entity.custom_emoji_id

    raw = (message.text or "").strip()
    if raw.isdigit() and 1 <= len(raw) <= MAX_ICON_LENGTH:
        return raw
    return None


def _preview_kb(key: str) -> InlineKeyboardMarkup:
    """Tugmaning yangi ko'rinishini ko'rsatuvchi bir tugmali preview."""
    builder = InlineKeyboardBuilder()
    builder.row(
        registry.build_inline_button(key, callback_data="button_preview")
    )
    return builder.as_markup()


def _detail_text(key: str) -> str:
    spec = registry.get_spec(key)
    return BUTTON_DETAIL_TEXT.format(
        key=key,
        group=registry.GROUP_TITLES.get(spec.group, spec.group),
        text=registry.get_text(key),
        style=registry.get_style(key) or "-",
        icon=registry.get_icon(key) or "yo'q",
    )


async def _show_list(target: Message) -> None:
    """Tugmalar ro'yxatini ko'rsatadi."""
    labels: list[tuple[str, str]] = []
    sections: list[str] = []

    for group in registry.groups():
        title = registry.GROUP_TITLES.get(group, group)
        keys = registry.editable_keys(group)
        if not keys:
            continue
        sections.append(f"<b>{title}</b>")
        for key in keys:
            text = registry.get_text(key)
            mark = "✏️ " if registry.has_overrides(key) else ""
            sections.append(f"  • {mark}{text}  <code>({key})</code>")
            labels.append((key, text))

    await target.answer(
        BUTTONS_MENU_TEXT + "\n\n" + "\n".join(sections),
        reply_markup=get_buttons_manage_kb(labels),
    )


# =============================================================
# 1. RO'YXAT
# =============================================================
@router.callback_query(F.data == CB_ADM_BUTTONS)
async def buttons_list(callback: CallbackQuery) -> None:
    """[ 🎛 Tugnalar va Emojilar ] — ro'yxatni ko'rsatadi."""
    await callback.answer()
    await _show_list(callback.message)


# =============================================================
# 2. BITTA TUGMA
# =============================================================
@router.callback_query(F.data.startswith(CB_ADM_BUTTON_PREFIX))
async def button_detail(callback: CallbackQuery) -> None:
    """Tanlangan tugma tafsilotlari."""
    key = (callback.data or "")[len(CB_ADM_BUTTON_PREFIX):]
    if not registry.is_known(key):
        await callback.answer("⚠️ Tugma topilmadi", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer(
        _detail_text(key),
        reply_markup=get_button_edit_kb(key),
    )


@router.callback_query(F.data.startswith("adm_btnedit_text_"))
async def button_edit_text_start(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Nomni o'zgartirishni boshlaydi."""
    key = (callback.data or "")[len("adm_btnedit_text_"):]
    if not registry.is_known(key):
        await callback.answer("⚠️ Tugma topilmadi", show_alert=True)
        return

    await state.set_state(ButtonEditStates.waiting_for_text)
    await state.update_data(button_key=key)
    await callback.answer()
    await callback.message.answer(ASK_TEXT.format(max_len=MAX_TEXT_LENGTH))


@router.callback_query(F.data.startswith("adm_btnedit_icon_"))
async def button_edit_icon_start(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Premium emoji o'rnatishni boshlaydi."""
    key = (callback.data or "")[len("adm_btnedit_icon_"):]
    if not registry.is_known(key):
        await callback.answer("⚠️ Tugma topilmadi", show_alert=True)
        return

    await state.set_state(ButtonEditStates.waiting_for_emoji)
    await state.update_data(button_key=key)
    await callback.answer()
    await callback.message.answer(ASK_EMOJI)


@router.callback_query(F.data.startswith("adm_btnreset_"))
async def button_reset(callback: CallbackQuery) -> None:
    """Tugmani default holatga qaytaradi."""
    key = (callback.data or "")[len("adm_btnreset_"):]
    if not registry.is_known(key):
        await callback.answer("⚠️ Tugma topilmadi", show_alert=True)
        return

    await reset_button(key)
    await callback.answer(RESET_DONE)
    await callback.message.answer(
        _detail_text(key),
        reply_markup=get_button_edit_kb(key),
    )


@router.callback_query(F.data == "button_preview")
async def button_preview(callback: CallbackQuery) -> None:
    """Preview tugmasi bosilganda spinner'ni yopadi (amaliy harakat yo'q)."""
    await callback.answer("Ko'rinish shunday 👆")


# =============================================================
# 3. BEKOR QILISH (/cancel)
# =============================================================
@router.message(ButtonEditStates.waiting_for_text, F.text == "/cancel")
@router.message(ButtonEditStates.waiting_for_emoji, F.text == "/cancel")
async def buttons_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(CANCELLED)


# =============================================================
# 4. YANGI NOM
# =============================================================
@router.message(ButtonEditStates.waiting_for_text, F.text)
async def button_text_apply(message: Message, state: FSMContext) -> None:
    """Yangi matnni saqlaydi va preview ko'rsatadi."""
    data = await state.get_data()
    key = str(data.get("button_key", ""))

    try:
        text = await save_button_text(key, message.text or "")
    except (ValueError, KeyError) as exc:
        await message.answer(f"⚠️ {exc}")
        return

    await state.clear()
    await message.answer(
        TEXT_SAVED.format(text=text),
        reply_markup=_preview_kb(key),
    )
    logger.info("Admin tugma nomini o'zgartirdi: key=%s -> %s", key, text)


# =============================================================
# 5. PREMIUM EMOJI
# =============================================================
@router.message(ButtonEditStates.waiting_for_emoji)
async def button_icon_apply(message: Message, state: FSMContext) -> None:
    """`custom_emoji` entity'dan ID olib saqlaydi va preview ko'rsatadi."""
    data = await state.get_data()
    key = str(data.get("button_key", ""))

    icon_id = _extract_custom_emoji_id(message)
    if not icon_id:
        await message.answer(
            "⚠️ Premium emoji topilmadi. Iltimos, Premium emojini yuboring "
            "yoki uning raqamli <code>custom_emoji_id</code> sini kiriting."
        )
        return

    try:
        saved = await save_button_icon(key, icon_id)
    except (ValueError, KeyError) as exc:
        await message.answer(f"⚠️ {exc}")
        return

    await state.clear()
    await message.answer(
        ICON_SAVED.format(icon=saved),
        reply_markup=_preview_kb(key),
    )
    logger.info("Admin tugma emojisini o'zgartirdi: key=%s icon=%s", key, saved)
