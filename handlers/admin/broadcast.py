"""
handlers/admin/broadcast.py
===========================
Ommaviy xabar (rassilka) — matn, rasm va video yuborish.

Oqim:
 1. Admindan [ 📢 Ommaviy xabar (Rassilka) ] bosiladi →
    `BroadcastStates.waiting_for_content` holati ochiladi.
 2. Admin ixtiyoriy xabar yuboradi (matn / rasm / video).
    Xabar `copy_message` uchun saqlanadi va tasdiqlash so'raladi →
    `BroadcastStates.waiting_for_confirm`.
 3. [ ✅ Yuborish ] — barcha FAOL foydalanuvchilarga fonga (task)
    yuboriladi; [ ❌ Bekor qilish ] — bekor qilinadi.

Xavfsizlik:
 - `copy_message` ishlatiladi — asl xabar o'zgarmaydi (media, caption,
   format saqlanadi); original xabar o'chirilsa ham nusxa ishlaydi.
 - Flood control: har 25 ta xabardan keyin 1 soniya pauza.
 - TelegramRetryAfter (429) ushlanib, `retry_after` kutiladi va
   bir marta qayta uriniladi.
 - Bloklangan foydalanuvchilar (TelegramForbiddenError) alohida sanaladi.

Hisobot adminga yakunda yangi xabar sifatida yuboriladi.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import settings
from database.queries import UserRepository
from keyboards.builders import CB_ADM_BROADCAST
from states.admin_states import BroadcastStates

logger = logging.getLogger(__name__)

router = Router(name="admin_broadcast")

# faqat adminlar uchun
router.message.filter(F.from_user.id.in_(settings.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.ADMIN_IDS))

# Callback konstantalari (faqat shu modulda)
CB_BC_SEND = "bc_send"
CB_BC_CANCEL = "bc_cancel"

# Flood control: shuncha xabardan keyin pauza
FLOOD_CHUNK = 25
FLOOD_SLEEP = 1.0

# Rassilka boshlanishi haqida xabar
BROADCAST_PROMPT_TEXT = (
    "📢 <b>OMMAVIY XABAR (RASSILKA)</b>\n\n"
    "Barcha faol foydalanuvchilarga yuboriladigan xabarni yuboring:\n"
    "├ ✍️ Matn\n"
    "├ 🖼 Rasm (caption bilan)\n"
    "└ 🎬 Video (caption bilan)\n\n"
    "Bekor qilish: /cancel"
)

CONFIRM_TEXT = (
    "📨 <b>Rassilka tasdiqlashi</b>\n\n"
    "Tayyor xabar: <b>{content_type}</b>\n"
    "Yuboriladigan foydalanuvchilar: <b>{audience} ta</b> (faol)\n\n"
    "Yuborishni tasdiqlaysizmi?"
)

STARTED_TEXT = (
    "🚀 <b>Rassilka boshlandi!</b>\n\n"
    "Xabar {audience} ta foydalanuvchiga yuborilmoqda…\n"
    "Yakunlanganda hisobot yuboriladi."
)

RESULT_TEXT = (
    "✅ <b>Rassilka yakunlandi!</b>\n\n"
    "📋 Jami maqsad: {total} ta\n"
    "📬 Yetkazilgan: {delivered} ta\n"
    "🚫 Bloklangan: {blocked} ta\n"
    "⚠️ Xatolik: {failed} ta"
)

CANCELLED_TEXT = "❌ Rassilka bekor qilindi."

CONTENT_TYPES = {"text", "photo", "video"}


# =============================================================
# 1. RASSILKANI BOSHLASH
# =============================================================
@router.callback_query(F.data == CB_ADM_BROADCAST)
async def broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    """[ 📢 Ommaviy xabar ] — kontent so'raydi."""
    await callback.answer()
    await state.set_state(BroadcastStates.waiting_for_content)
    await callback.message.answer(BROADCAST_PROMPT_TEXT)


# =============================================================
# 2. BEKOR QILISH (keng "har qanday matn" handleridan OLDIN —
#    aks holda /cancel oddiy kontent sifatida qabul qilinardi)
# =============================================================
@router.callback_query(BroadcastStates.waiting_for_confirm, F.data == CB_BC_CANCEL)
@router.message(BroadcastStates.waiting_for_content, F.text == "/cancel")
async def broadcast_cancel(event: Any, state: FSMContext) -> None:
    """Rassilkani bekor qiladi (tasdiqlash bosqichida yoki /cancel)."""
    if isinstance(event, CallbackQuery):
        # Spinner DARHOL yopiladi.
        await event.answer("Bekor qilindi")
        await state.clear()
        await event.message.answer(CANCELLED_TEXT)
    else:
        await state.clear()
        await event.answer(CANCELLED_TEXT)


# =============================================================
# Kontentni qabul qilish (cancel handleridan keyin turishi shart!)
# =============================================================
@router.message(BroadcastStates.waiting_for_content)
async def broadcast_receive(message: Message, state: FSMContext) -> None:
    """
    Admindan kontentni qabul qiladi (matn/rasm/video) va tasdiqlashni so'raydi.

    Albom (media_group_id) alohida yuborilmaydi — iltimos, bitta xabar
    sifatida yuborish so'raladi (aks holda faqat bitta element ketardi).
    """
    if message.content_type not in CONTENT_TYPES:
        await message.answer(
            "⚠️ Faqat <b>matn</b>, <b>rasm</b> yoki <b>video</b> "
            "yuborishingiz mumkin.",
        )
        return

    if message.media_group_id is not None:
        await message.answer(
            "⚠️ Albom (bir nechta rasm/video) qo'llab-quvvatlanmaydi.\n"
            "Iltimos, xabarni <b>bitta</b> rasm yoki video sifatida yuboring.",
        )
        return

    audience = len(await UserRepository.get_all_user_ids())

    await state.update_data(
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        content_type=message.content_type,
        audience=audience,
    )
    await state.set_state(BroadcastStates.waiting_for_confirm)

    await message.answer(
        CONFIRM_TEXT.format(
            content_type=message.content_type, audience=audience
        ),
        reply_markup=_confirm_kb(),
    )


def _confirm_kb():
    """Tasdiqlash klaviaturasi: [ ✅ Yuborish ] [ ❌ Bekor qilish ]."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Yuborish", callback_data=CB_BC_SEND),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data=CB_BC_CANCEL),
    )
    return builder.as_markup()


# =============================================================
# 3. YUBORISH (tasdiqlangach) — fonda bajariladi
# =============================================================
@router.callback_query(BroadcastStates.waiting_for_confirm, F.data == CB_BC_SEND)
async def broadcast_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    """Tasdiqlangach rassilkani fon taskda boshlaydi."""
    # Spinner DARHOL yopiladi
    await callback.answer("🚀 Yuborilmoqda…")

    data = await state.get_data()
    await state.clear()

    from_chat_id = int(data["from_chat_id"])
    message_id = int(data["message_id"])
    audience = int(data.get("audience", 0))

    await callback.message.answer(STARTED_TEXT.format(audience=audience))

    # Admin chatiga hisobot alohida yuboriladi — rassilka uzilmaydi
    asyncio.create_task(
        _run_broadcast(
            bot=callback.bot,
            admin_chat_id=callback.message.chat.id,
            from_chat_id=from_chat_id,
            message_id=message_id,
        )
    )


async def _run_broadcast(
    bot: Any,
    admin_chat_id: int,
    from_chat_id: int,
    message_id: int,
) -> None:
    """
    Barcha faol foydalanuvchilarga `copy_message` orqali xabar yuboradi.

    Flood control: har `FLOOD_CHUNK` (25) xabardan keyin `FLOOD_SLEEP` (1s).
    TelegramRetryAfter uchun `retry_after` kutiladi va bir marta qayta uriniladi.
    """
    user_ids = await UserRepository.get_all_user_ids()
    total = len(user_ids)

    delivered = 0
    blocked = 0
    failed = 0

    for index, user_id in enumerate(user_ids, start=1):
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=from_chat_id,
                message_id=message_id,
            )
            delivered += 1
        except TelegramRetryAfter as exc:
            # 429 Too Many Requests — Telegram aytgan muddatgacha kutamiz
            logger.info("Rassilka flood: %ss kutilyapdi (user=%s)", exc.retry_after, user_id)
            await asyncio.sleep(float(exc.retry_after))
            try:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                )
                delivered += 1
            except TelegramForbiddenError:
                blocked += 1
            except TelegramAPIError:
                failed += 1
        except TelegramForbiddenError:
            # Foydalanuvchi botni blocklagan yoki akkaunt deaktiv
            blocked += 1
        except TelegramAPIError as exc:
            failed += 1
            logger.debug("Rassilka xatoligi (user=%s): %s", user_id, exc)

        # Flood control: har 25 ta xabardan keyin 1 soniya kutish
        if index % FLOOD_CHUNK == 0:
            await asyncio.sleep(FLOOD_SLEEP)

    logger.info(
        "Rassilka tugadi: jami=%s yetkazildi=%s blok=%s xatolik=%s",
        total, delivered, blocked, failed,
    )

    try:
        await bot.send_message(
            admin_chat_id,
            RESULT_TEXT.format(
                total=total, delivered=delivered, blocked=blocked, failed=failed
            ),
        )
    except TelegramAPIError as exc:
        logger.warning("Rassilka hisobotini adminga yuborib bo'lmadi: %s", exc)
