"""
handlers/admin/panel.py
=======================
Asosiy admin menyusi va jonli statistika.

Vazifalari:
 - Faqat `settings.ADMIN_IDS` dagi foydalanuvchilar uchun ruxsat
   (router darajasidagi filter — barcha handlerga ta'sir qiladi).
 - Asosiy menyudan `⚙️ Admin Panel` tugmasi yoki `/admin` buyrug'i
   orqali admin panel ochiladi.
 - [ 📊 Jonli statistika ] — bazadan real vaqtda hisoblab chiqaradi:
   jami foydalanuvchilar, bugun qo'shilganlar, tarqatilgan/yechildgan
   Starslar, faol homiy kanallar va kutilayotgan yechib olishlar.
 - [ ◀️ Admin panelga qaytish ] / [ ❌ Chiqish ] — navigatsiya.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config.settings import settings
from database.queries import (
    ChannelRepository,
    UserRepository,
    WithdrawalRepository,
)
from keyboards.builders import (
    CB_ADM_BACK,
    CB_ADM_EXIT,
    CB_ADM_STATS,
    get_admin_main_kb,
)
from keyboards.reply import get_main_menu_kb
from services.button_registry import ButtonText

logger = logging.getLogger(__name__)

router = Router(name="admin_panel")

# faqat adminlar uchun (router darajasida — barcha handlerga ta'sir qiladi)
router.message.filter(F.from_user.id.in_(settings.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.ADMIN_IDS))

# Panel sarlavhasi
PANEL_TEXT = (
    "⚙️ <b>ADMIN PANEL</b>\n\n"
    "Quyidagi bo'limlardan birini tanlang 👇"
)

# Jonli statistika matni (HTML — DefaultBotProperties HTML)
STATS_TEXT = (
    "📊 <b>BOTNING JONLI STATISTIKASI</b>\n\n"
    "👥 Jami foydalanuvchilar: {total_users} ta\n"
    "📅 Bugun qo'shilganlar: {today_users} ta\n"
    "⭐️ Jami tarqatilgan Stars: {total_earned_stars} ⭐️\n"
    "💸 Jami yechib olingan Stars: {total_withdrawn_stars} ⭐️\n"
    "📢 Faol homiy kanallar: {active_channels_count} ta\n"
    "⏳ Kutilayotgan yechib olish so'rovlari: {pending_withdrawals_count} ta"
)

EXIT_TEXT = "👋 Admin paneldan chiqildi. Asosiy menyuga qaytdingiz."


# =============================================================
# 1. PANELNI OCHISH (reply tugma yoki /admin)
# =============================================================
@router.message(StateFilter("*"), ButtonText("menu_admin"))
@router.message(StateFilter("*"), F.text.contains("Admin"))
async def admin_panel_open(
    message: Message, state: FSMContext | None = None
) -> None:
    """Asosiy menyudan '⚙️ Admin Panel' bosilganda ochiladi."""
    if state is not None:
        await state.clear()
    await message.answer(PANEL_TEXT, reply_markup=get_admin_main_kb())
    logger.info("Admin panel ochildi: user=%s", message.from_user.id)


@router.message(StateFilter("*"), Command("admin"))
async def admin_panel_command(
    message: Message, state: FSMContext | None = None
) -> None:
    """`/admin` buyrug'i orqali ochiladi."""
    if state is not None:
        await state.clear()
    await message.answer(PANEL_TEXT, reply_markup=get_admin_main_kb())


# =============================================================
# 2. JONLI STATISTIKA
# =============================================================
@router.callback_query(F.data == CB_ADM_STATS)
async def admin_stats(callback: CallbackQuery) -> None:
    """
    [ 📊 Jonli statistika ] — barcha ko'rsatkichlarni bazadan
    real vaqtda hisoblaydi va yangi xabar sifatida ko'rsatadi.
    """
    stats = await UserRepository.get_total_stats()
    today_users = await UserRepository.count_new_users_today()
    active_channels = await ChannelRepository.get_active_channels()
    pending_withdrawals = await WithdrawalRepository.count_pending_withdrawals()

    text = STATS_TEXT.format(
        total_users=stats.total_members,
        today_users=today_users,
        total_earned_stars=stats.total_distributed_stars,
        total_withdrawn_stars=stats.total_withdrawn_stars,
        active_channels_count=len(active_channels),
        pending_withdrawals_count=pending_withdrawals,
    )

    await callback.answer()
    await callback.message.answer(text)


# =============================================================
# 3. NAVIGATSIYA (qaytish / chiqish)
# =============================================================
@router.callback_query(F.data == CB_ADM_BACK)
async def admin_back(callback: CallbackQuery) -> None:
    """[ ◀️ Admin panelga qaytish ] — panel menyusiga qaytadi."""
    await callback.answer()
    try:
        # Xabar tahrirlanadigan bo'lsa — shu joyda almashtiramiz
        await callback.message.edit_text(
            PANEL_TEXT, reply_markup=get_admin_main_kb()
        )
    except TelegramBadRequest:
        # Eski xabar tahrirlab bo'lmaydi (masalan, media) — yangi xabar
        await callback.message.answer(
            PANEL_TEXT, reply_markup=get_admin_main_kb()
        )


@router.callback_query(F.data == CB_ADM_EXIT)
async def admin_exit(callback: CallbackQuery) -> None:
    """[ ❌ Chiqish ] — paneldan chiqib, asosiy menyuga qaytadi."""
    await callback.answer("Chiqildi")
    try:
        await callback.message.edit_text(EXIT_TEXT)
    except TelegramBadRequest:
        await callback.message.answer(EXIT_TEXT)

    # Asosiy doimiy menyu (admin uchun qo'shimcha qator bilan)
    await callback.message.answer(
        "🏠 Bosh menyu.", reply_markup=get_main_menu_kb(is_admin=True)
    )
