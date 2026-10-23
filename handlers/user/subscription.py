"""
handlers/user/subscription.py
=============================
"✅ A'zo bo'ldim" tugmasining callback handleri.

Oqimi:
 1. Kanallar qayta tekshiriladi (keshli servis).
 2. Hali a'zo bo'lmasa — alert: "❌ Hali barcha kanallarga a'zo bo'lmadingiz!"
 3. Barchasiga a'zo bo'lsa:
      a) `set_channels_verified(user_id)` — bazada tasdiqlanadi;
      b) `confirm_referral_reward(user_id)` — taklif qilgan shaxs mukofotlanadi
         (miqdor `settings.referral_reward_stars`, odatda 5⭐️) va darhol
         xabarnoma oladi;
      c) eski ogohlantirish xabari o'chiriladi/yangilanadi va asosiy
         reply menyu bilan salomlashuv yuboriladi.
"""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

from config.settings import settings
from database.queries import ReferralRepository, UserRepository
from keyboards.inline import get_subscription_kb
from keyboards.reply import get_main_menu_kb
from services.channel_checker import (
    check_user_subscription,
    get_active_channel_dicts,
)

logger = logging.getLogger(__name__)

# Ushbu modul uchun yagona router (bot.py da ulanadi)
router = Router(name="user_subscription")

# Salomlashuv matni
WELCOME_TEXT = (
    "Assalomu alaykum, {name}! 🎉\n\n"
    "⭐️ Stars ishlashingiz, 💎 Premium, 🖼 NFT va 🎁 mayda giftlar "
    "yutib olishingiz mumkin!\n\n"
    "Quyidagi menyudan kerakli bo'limni tanlang 👇"
)

# Mukofot xabari (HTML — <b> teqlari uchun)
REFERRAL_REWARD_TEXT = (
    "🎉 <b>Ajoyib yangilik!</b> Siz taklif qilgan do'stingiz kanallarga "
    "a'zo bo'ldi va balansingizga <b>+{amount} ⭐️ Stars</b> qo'shildi!"
)


@router.callback_query(F.data == "check_subscription")
async def cb_check_subscription(callback: CallbackQuery) -> None:
    """
    "✅ A'zo bo'ldim" tugmasi — kanallar a'zoligini qayta tekshiradi
    va muvaffaqiyatli holatda foydalanuvchini botga to'liq kiritadi.
    """
    user = callback.from_user
    user_id = user.id
    display_name = html.escape(user.first_name or user.username or "do'stim")

    # Spinner DARHOL yopiladi — Telegram API tekshiruvi kutdirilmaydi
    await callback.answer()

    # ---------------- 1. Qayta tekshiruv ----------------
    channels = await get_active_channel_dicts()
    if channels:
        bot = callback.bot
        is_subscribed, unjoined = await check_user_subscription(bot, user_id, channels)

        if not is_subscribed:
            # Hali ham a'zo emas — ogohlantirish + klaviaturani holatga moslash
            await callback.message.answer(
                "❌ Hali barcha kanallarga a'zo bo'lmadingiz!"
            )
            try:
                # Xabarni qayta yubormasdan, faqat klaviaturani
                # a'zo bo'lmagan kanallarga yangilaymiz
                await callback.message.edit_reply_markup(
                    reply_markup=get_subscription_kb(unjoined)
                )
            except (TelegramAPIError, AttributeError):
                pass
            return

    # ---------------- 2. Bazada tasdiqlash ----------------
    await UserRepository.set_channels_verified(user_id)

    # ---------------- 3. Referal mukofoti ----------------
    try:
        reward = await ReferralRepository.confirm_referral_reward(user_id)
    except Exception:  # noqa: BLE001 — mukofot xatosi obunani bloklamasin
        logger.exception("Referal mukofotini tasdiqlashda xatolik (user=%s)", user_id)
        reward = None

    if reward is not None and reward.reward_amount > 0:
        notification = REFERRAL_REWARD_TEXT.format(amount=reward.reward_amount)
        try:
            await callback.bot.send_message(
                chat_id=reward.referrer_id, text=notification, parse_mode="HTML"
            )
        except TelegramAPIError as exc:
            # Taklif qilgan shaxs botni blocklagan bo'lishi mumkin
            logger.warning(
                "Referrer %s ga mukofot xabari yetmadi: %s",
                reward.referrer_id, exc,
            )

    # ---------------- 4. Xush kelibsiz + menyu ----------------
    try:
        await callback.message.delete()
    except (TelegramAPIError, AttributeError):
        # xabar o'chirib bo'lmasa — tahrirlashga urinamiz
        try:
            await callback.message.edit_text("✅ Obuna tasdiqlandi!")
        except (TelegramAPIError, AttributeError):
            pass

    is_admin = user_id in settings.ADMIN_IDS
    await callback.message.answer(
        WELCOME_TEXT.format(name=display_name),
        reply_markup=get_main_menu_kb(is_admin=is_admin),
    )
