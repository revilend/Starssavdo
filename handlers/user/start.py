"""
handlers/user/start.py
======================
`/start` komandasi va referal deep-link (`/start ref_{referrer_id}`) logikasi.

Oqim:
 1. Referal havola analiz qilinadi (`ref_12345`); o'zini o'zi taklif
    qilish taqiqlanadi.
 2. Foydalanuvchi bazada yo'q bo'lsa — yangi sifatida qo'shiladi
    (referrer_id bilan) va `referrals` jadvaliga `status='pending'`
    yozuv yoziladi.
 3. Mavjud foydalanuvchi uchun — profil/`last_active_at` yangilanadi;
    agar u hali referalsiz bo'lsa va deep-link kelgan bo'lsa —
    referal yozuvi baribir yaratiladi (birinchi bosishda havola
    yo'qolgani uchun mukofot yo'qolmasin).
 4. Admin (`settings.ADMIN_IDS`) — tekshiruvsiz, Admin Panel tugmasi
    bilan to'liq menyu oladi.
 5. Oddiy foydalanuvchi — avval homiy kanallar a'zoligi tekshiriladi:
    a'zo emas -> kanallar ro'yxati + "✅ A'zo bo'ldim";
    a'zo -> salomlashuv + asosiy reply menyu.
"""

from __future__ import annotations

import html
import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config.settings import settings
from database.queries import ReferralRepository, UserRepository
from keyboards.inline import get_subscription_kb
from keyboards.reply import get_main_menu_kb
from middlewares.subscription import SUBSCRIPTION_PROMPT_TEXT
from services.channel_checker import (
    check_user_subscription,
    get_active_channel_dicts,
)

logger = logging.getLogger(__name__)

router = Router(name="user_start")

# Deep-link prefiksi: /start ref_123456789
REF_PREFIX = "ref_"

# Salomlashuv matni (HTML)
WELCOME_TEXT = (
    "👋 <b>Assalomu alaykum, {name}!</b>\n\n"
    "⭐️ <b>@nftyutbot</b> ga xush kelibsiz! Bu yerda siz Telegram Stars, "
    "Premium, NFT va mayda sovg'alarni xarid qilishingiz yoki do'stlaringizni "
    "taklif qilib bepul Stars ishlashingiz mumkin!"
)


def parse_referrer_id(raw_args: str | None) -> int | None:
    """
    `/start ref_123456789` argumentidan referrer ID'ni ajratib oladi.

    Noto'g'ri formatlar (son emas, manfiy, juda uzun) -> None.
    """
    if not raw_args:
        return None
    arg = raw_args.strip()
    if not arg.startswith(REF_PREFIX):
        return None
    payload = arg[len(REF_PREFIX):].strip()
    if not payload.isdigit():
        return None
    referrer_id = int(payload)
    return referrer_id if referrer_id > 0 else None


async def _register_referral(referrer_id: int, referred_id: int) -> None:
    """
    Referal yozuvini `pending` holatida yaratadi (idempotent).

    Nakrutka himoyasi: referrer so'nggi 1 soatda limitdan oshgan bo'lsa
    (`check_referral_velocity`) yangi referal yaratilmaydi.
    """
    velocity = await ReferralRepository.check_referral_velocity(referrer_id)
    if velocity.flagged:
        logger.warning(
            "Velocity limit oshdi: referrer=%s (%s ta/soat) — referal yaratilmadi",
            referrer_id, velocity.count,
        )
        return

    referral = await ReferralRepository.create_referral(referrer_id, referred_id)
    if referral is not None:
        logger.info(
            "Yangi referal yozuvi: %s -> %s (pending)", referrer_id, referred_id
        )


@router.message(StateFilter("*"), Command("start"))
async def cmd_start(
    message: Message,
    command: CommandObject,
    state: FSMContext | None = None,
) -> None:
    """/start — yangi va mavjud foydalanuvchilar uchun kirish nuqtasi."""
    # FSM oqimida qolib ketgan bo'lsa ham /start darhol ishlaydi.
    if state is not None:
        await state.clear()

    user = message.from_user
    if user is None:
        return

    user_id = user.id
    referrer_id = parse_referrer_id(command.args)

    # O'zini o'zi taklif qilishni taqiqlash
    if referrer_id is not None and referrer_id == user_id:
        logger.info("user=%s o'zini taklif qildi — e'tibors qoldirildi", user_id)
        referrer_id = None

    # Referar mavjudligini tekshirish (yo'q ID bilan FK buzilmasligi uchun)
    if referrer_id is not None:
        referrer = await UserRepository.get_user(referrer_id)
        if referrer is None:
            logger.info(
                "Deep-linkdagi referrer=%s bazada yo'q — e'tibors qoldirildi",
                referrer_id,
            )
            referrer_id = None

    # 1) Bazada mavjudligi tekshiriladi / yangi foydalanuvchi qo'shiladi
    existing = await UserRepository.get_user(user_id)
    is_new = existing is None

    # get_or_create_user: yangi qo'shadi yoki mavjud bo'lsa profil +
    # last_active_at ni yangilaydi; referrer_id faqat bo'sh bo'lsa to'ldiradi
    db_user = await UserRepository.get_or_create_user(
        user_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        referrer_id=referrer_id,
    )

    # 2) Referal yozuvi: birinchi marta kelgan yoki hali referalsiz bo'lgan
    #    foydalanuvchi uchun `pending` qilib yoziladi (create_referral
    #    takroriy chaqiruvda None qaytaradi — xavfsiz/idempotent)
    if referrer_id is not None:
        await _register_referral(referrer_id, user_id)

    display_name = html.escape(user.first_name or user.username or "do'stim")

    # 3) ADMIN — tekshiruvsiz to'liq menyu (Admin Panel bilan)
    if user_id in settings.ADMIN_IDS:
        logger.info("Admin %s /start bosdi", user_id)
        await message.answer(
            WELCOME_TEXT.format(name=display_name),
            parse_mode="HTML",
            reply_markup=get_main_menu_kb(is_admin=True),
        )
        return

    # 4) ODDIY FOYDALANUVCHI — majburiy kanallar tekshiruvi
    channels = await get_active_channel_dicts()
    if channels:
        is_subscribed, unjoined = await check_user_subscription(
            message.bot, user_id, channels
        )
        if not is_subscribed:
            await message.answer(
                SUBSCRIPTION_PROMPT_TEXT,
                parse_mode="HTML",
                reply_markup=get_subscription_kb(unjoined),
            )
            return

    # 5) A'zo (yoki majburiy kanallar yo'q) — salomlashuv + menyu
    await message.answer(
        WELCOME_TEXT.format(name=display_name),
        parse_mode="HTML",
        reply_markup=get_main_menu_kb(is_admin=False),
    )

    if is_new:
        logger.info("Yangi foydalanuvchi qo'shildi: %s (referrer=%s)",
                    user_id, db_user.referrer_id)
