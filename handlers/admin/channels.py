"""
handlers/admin/channels.py
==========================
Majburiy (OP) homiy kanallarni bot ichidan boshqarish.

Imkoniyatlar:
 - Mavjud kanallarni ko'rish (✅ faol / 🚫 nofaol holati bilan).
 - FSM orqali yangi kanal qo'shish (3 bosqich):
     1. `waiting_for_chat_id`     — kanal chat_id (masalan -100...)
     2. `waiting_for_title`       — kanal nomi
     3. `waiting_for_invite_link` — taklif havolasi (https://t.me/+...)
 - Bir bosishda yoqish/o'chirish (`adm_toggle_{id}`).
 - Bir bosishda ro'yxatdan o'chirish (`adm_chdel_{id}`).

Eslatma: kanal qo'shilgandan keyin `services/channel_checker` 60 soniya
keshidan keyin uni majburiy ro'yxatda ko'radi (alohida refresh shart emas).
"""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config.settings import settings
from database.queries import ChannelRepository, RepositoryError
from keyboards.builders import CB_ADM_CH_ADD, CB_ADM_CHANNELS, get_admin_channel_manage_kb
from states.admin_states import AddChannelStates

logger = logging.getLogger(__name__)

router = Router(name="admin_channels")

# faqat adminlar uchun
router.message.filter(F.from_user.id.in_(settings.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.ADMIN_IDS))

# Kanal taklif havolasi (t.me/..., https://t.me/+, invite/joinchat)
_INVITE_LINK_RE = re.compile(
    r"^(https?://)?t\.me/(joinchat/|\+|[A-Za-z0-9_]+/?$)", re.IGNORECASE
)

# Kanallar ro'yxati matni
CHANNELS_TEXT = (
    "📢 <b>MAJBURIY (OP) HOMIY KANALLAR</b>\n\n"
    "{rows}\n\n"
    "➕ <b>Yangi kanal qo'shish</b> — chat_id, nom va taklif havolasini "
    "ketma-ket yuboring.\n"
    "Tugmalar: ✅/🚫 — yoqish/o'chirish, 🗑 — ro'yxatdan o'chirish."
)
CHANNELS_EMPTY_TEXT = (
    "📢 <b>MAJBURIY (OP) HOMIY KANALLAR</b>\n\n"
    "Hozircha kanal yo'q. Pastdagi ➕ tugma orqali birinchi kanalni "
    "qo'shing 👇"
)


def _list_text(channels: list) -> str:
    """Kanallar ro'yxati matnini shakllantiradi (HTML)."""
    if not channels:
        return CHANNELS_EMPTY_TEXT

    rows: list[str] = []
    for index, channel in enumerate(channels, start=1):
        status = "✅" if channel.is_active else "🚫"
        rows.append(
            f"{status} <b>{index}.</b> {channel.title} — "
            f"<code>{channel.chat_id}</code>"
        )
    return CHANNELS_TEXT.format(rows="\n".join(rows))


# =============================================================
# 1. RO'YXATNI KO'RSATISH
# =============================================================
@router.callback_query(F.data == CB_ADM_CHANNELS)
async def channels_list(callback: CallbackQuery) -> None:
    """[ 📢 Homiy kanallar ] — barcha kanallarni (holati bilan) ko'rsatadi."""
    # Spinner DARHOL yopiladi (DB so'rovidan oldin)
    await callback.answer()

    channels = await ChannelRepository.get_all_channels()
    await callback.message.answer(
        _list_text(channels),
        reply_markup=get_admin_channel_manage_kb(channels),
    )


async def _refresh_list(message: Message, text: str | None = None) -> None:
    """Xabarni yangilangan kanallar ro'yxati bilan tahrirlaydi."""
    channels = await ChannelRepository.get_all_channels()
    try:
        await message.edit_text(
            text or _list_text(channels),
            reply_markup=get_admin_channel_manage_kb(channels),
        )
    except TelegramBadRequest:
        # Eski xabar tahrirlab bo'lmadi — yangi xabar yuboramiz
        await message.answer(
            _list_text(channels),
            reply_markup=get_admin_channel_manage_kb(channels),
        )


# =============================================================
# 2. YANGI KANAL QO'SHISH (FSM — 3 bosqich)
# =============================================================
@router.callback_query(F.data == CB_ADM_CH_ADD)
async def channel_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    """[ ➕ Yangi kanal qo'shish ] — chat_id so'raydi."""
    await callback.answer()
    await state.set_state(AddChannelStates.waiting_for_chat_id)
    await callback.message.answer(
        "➕ <b>Yangi kanal qo'shish</b>\n\n"
        "1/3 — Kanal <b>chat_id</b> raqamini yuboring.\n"
        "Masalan: <code>-1001234567890</code>\n\n"
        "💡 Kanalga xabar yuboring va xabarni <code>Forward</code> "
        "qilib botga ushlab turib <code>Copy</code> qiling — ID shu yerda "
        "ko'rinadi yoki @userinfobot yordamida bilib oling.",
    )


@router.message(AddChannelStates.waiting_for_chat_id, F.text)
async def channel_add_chat_id(message: Message, state: FSMContext) -> None:
    """Kanal chat_id tekshiruvi (butun son, -100... bilan boshlanishi)."""
    text = (message.text or "").strip().replace(" ", "")
    try:
        chat_id = int(text)
    except ValueError:
        await message.answer(
            "⚠️ Chat_id faqat butun son bo'lishi kerak.\n"
            "Masalan: <code>-1001234567890</code>",
        )
        return

    if chat_id >= 0:
        await message.answer(
            "⚠️ Kanal (guruh) chat_id manfiy bo'lishi kerak — "
            "<code>-100...</code> bilan boshlanadi.",
        )
        return

    await state.update_data(chat_id=chat_id)
    await state.set_state(AddChannelStates.waiting_for_title)
    await message.answer(
        "2/3 — Kanal <b>nomini</b> yuboring.\n"
        "Masalan: <code>NFT Yutuqlar</code>",
    )


@router.message(AddChannelStates.waiting_for_title, F.text)
async def channel_add_title(message: Message, state: FSMContext) -> None:
    """Kanal nomini saqlaydi (1-255 belgi)."""
    title = (message.text or "").strip()
    if not 1 <= len(title) <= 255:
        await message.answer("⚠️ Nom 1 dan 255 belgigacha bo'lishi kerak.")
        return

    await state.update_data(title=title)
    await state.set_state(AddChannelStates.waiting_for_invite_link)
    await message.answer(
        "3/3 — Kanal <b>taklif havolasini</b> yuboring.\n"
        "Masalan: <code>https://t.me/+AbCdEfGhIjK</code>\n\n"
        "Kanal sozlamalaridan <b>Invite links</b> bo'limidan oling.",
    )


@router.message(AddChannelStates.waiting_for_invite_link, F.text)
async def channel_add_link(message: Message, state: FSMContext) -> None:
    """Taklif havolasini tekshirib, kanalni bazaga qo'shadi."""
    link = (message.text or "").strip()
    if not _INVITE_LINK_RE.match(link):
        await message.answer(
            "⚠️ Noto'g'ri taklif havolasi.\n"
            "<code>https://t.me/+...</code> yoki "
            "<code>https://t.me/joinchat/...</code> ko'rinishida bo'lishi kerak.",
        )
        return

    data = await state.get_data()
    chat_id = int(data["chat_id"])
    title = data["title"]

    try:
        channel = await ChannelRepository.add_channel(
            chat_id=chat_id, title=title, invite_link=link
        )
    except RepositoryError:
        # UNIQUE konflikti — kanal allaqachon bazada
        await message.answer(
            "⚠️ Bu chat_id allaqachon bazada mavjud. Boshqa kanal qo'shing.",
        )
        await state.clear()
        return

    await state.clear()
    logger.info("Kanal qo'shildi (admin): chat_id=%s title=%s", chat_id, title)

    channels = await ChannelRepository.get_all_channels()
    await message.answer(
        f"✅ <b>Kanal qo'shildi:</b> {channel.title}\n\n"
        "Endi majburiy kanallar ro'yxatida ko'rinadi:",
        reply_markup=get_admin_channel_manage_kb(channels),
    )
    await message.answer(_list_text(channels))


# =============================================================
# 3. YOQISH / O'CHIRISH VA RO'YXATDAN O'CHIRISH
# =============================================================
@router.callback_query(F.data.startswith("adm_toggle_"))
async def channel_toggle(callback: CallbackQuery) -> None:
    """[ ✅/🚫 ] — kanalni faol/nofaol holatga o'tkazadi."""
    channel_id = int(callback.data.rsplit("_", 1)[-1])

    # Spinner DARHOL yopiladi (DB so'rovlaridan oldin)
    await callback.answer()

    channel = await ChannelRepository.get_channel(channel_id)
    if channel is None:
        await callback.message.answer("⚠️ Kanal topilmadi.")
        return

    updated = await ChannelRepository.toggle_channel_status(
        channel_id, not channel.is_active
    )
    logger.info(
        "Kanal holati o'zgardi (admin): id=%s -> is_active=%s",
        channel_id, updated.is_active if updated else None,
    )
    await _refresh_list(callback.message)


@router.callback_query(F.data.startswith("adm_chdel_"))
async def channel_delete(callback: CallbackQuery) -> None:
    """[ 🗑 ] — kanalni bazadan butunlay o'chiradi."""
    channel_id = int(callback.data.rsplit("_", 1)[-1])

    # Spinner DARHOL yopiladi (DB so'rovlaridan oldin)
    await callback.answer()

    channel = await ChannelRepository.get_channel(channel_id)
    if channel is None:
        await callback.message.answer("⚠️ Kanal topilmadi.")
        return

    await ChannelRepository.delete_channel(channel.chat_id)
    logger.warning("Kanal o'chirildi (admin): id=%s chat_id=%s", channel_id, channel.chat_id)

    await _refresh_list(callback.message)
