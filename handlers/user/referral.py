"""
handlers/user/referral.py
=========================
"👥 Do'stlarni taklif qilish" bo'limi va 🏆 top reyting handlerlari.

Vazifalari:
 - Bazadan joriy referal narxini oladi (`settings.referral_reward_stars`,
   standart 5⭐️; .env dagi REFERRAL_REWARD_STARS fallback sifatida).
 - Shaxsiy taklif havolasini shakllantiradi:
   `https://t.me/{bot_username}?start=ref_{user_id}`
 - Jami takliflar soni va ishlangan Starslarni hisoblaydi.
 - Chiroyli formatlangan post + inline tugmalar (ulashish, top-10).
"""

from __future__ import annotations

import html
import logging
from typing import Any

from cachetools import TTLCache
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

from config.settings import settings
from database.queries import (
    SETTING_REFERRAL_REWARD,
    ReferralRepository,
    SettingsRepository,
)
from keyboards.inline import CB_TOP_REFERRERS, get_referral_kb
from services.button_registry import ButtonText

logger = logging.getLogger(__name__)

router = Router(name="user_referral")

# Bot username keshi (har xabarda get_me siklamaslik uchun)
_bot_username_cache: TTLCache = TTLCache(maxsize=1, ttl=3600)

# Takliflar reytingidagi medallar
MEDALS = ("🥇", "🥈", "🥉")

# Post matni (HTML)
REFERRAL_TEXT = (
    "👥 <b>DO'STLARNI TAKLIF QILISH DASTURI</b>\n\n"
    "Har bir taklif qilingan va kanallarga a'zo bo'lgan do'stingiz uchun "
    "sizga <b>+{reward_stars} ⭐️ Stars</b> taqdim etiladi!\n\n"
    "🔗 <b>Sizning taklif havolangiz:</b>\n"
    "<code>{referral_link}</code>\n\n"
    "📊 <b>Sizning ko'rsatkichlaringiz:</b>\n"
    "├ Jami takliflar: {total_refs} ta\n"
    "└ Ishlangan Stars: {earned_stars} ⭐️\n\n"
    "Do'stlaringizga yuborish uchun pastdagi tugmani bosing 👇"
)

# Top reyting post matni (HTML)
TOP_REFERRALS_TEXT = (
    "🏆 <b>TOP {limit} TAKLIF QILUVCHILAR</b>\n\n"
    "{rows}\n\n"
    "Siz ham topga kirishingiz mumkin — do'stlaringizni taklif qiling! 💪"
)

EMPTY_TOP_TEXT = (
    "🏆 <b>TAKLIFLAR REYTINGI</b>\n\n"
    "Hozircha reyting bo'sh. Birinchi bo'ling — do'stlaringizni taklif qiling! 🚀"
)


async def _get_bot_username(bot: Any) -> str:
    """Bot username'ini keshlangan holda oladi (1 soat kesh)."""
    cached = _bot_username_cache.get("username")
    if cached:
        return cached
    me = await bot.get_me()
    _bot_username_cache["username"] = me.username
    return me.username


@router.message(StateFilter("*"), ButtonText("menu_refer"))
@router.message(StateFilter("*"), F.text.contains("taklif"))
async def referral_menu(
    message: Message, state: FSMContext | None = None
) -> None:
    """Referal bo'limi postini yuboradi (istalgan FSM holatida)."""
    if state is not None:
        await state.clear()

    user = message.from_user
    if user is None:
        return
    user_id = user.id

    # Joriy referal narxi (bazadan; fallback .env)
    reward_stars = int(
        await SettingsRepository.get_setting(
            SETTING_REFERRAL_REWARD, settings.REFERRAL_REWARD_STARS
        )
    )

    # Statistika: jami takliflar va ishlangan Starslar
    counts = await ReferralRepository.get_user_referrals_count(user_id)
    earned_stars = counts.completed * reward_stars

    bot_username = await _get_bot_username(message.bot)
    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"

    await message.answer(
        REFERRAL_TEXT.format(
            reward_stars=reward_stars,
            referral_link=referral_link,
            total_refs=counts.total,
            earned_stars=earned_stars,
        ),
        parse_mode="HTML",
        reply_markup=get_referral_kb(bot_username, user_id),
        # Link preview o'chiriladi — xabar toza ko'rinsin
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    logger.info(
        "Referral post yuborildi: user=%s total=%s earned=%s",
        user_id, counts.total, earned_stars,
    )


@router.callback_query(StateFilter("*"), F.data == CB_TOP_REFERRERS)
async def top_referrers(
    callback: CallbackQuery, state: FSMContext | None = None
) -> None:
    """🏆 Top 10 taklif qiluvchilar ro'yxatini ko'rsatadi."""
    top = await ReferralRepository.get_top_referrers(limit=10)

    if not top:
        await callback.answer()
        await callback.message.answer(EMPTY_TOP_TEXT, parse_mode="HTML")
        return

    rows: list[str] = []
    for index, entry in enumerate(top, start=1):
        # Medal (top-3) yoki tartib raqami
        badge = MEDALS[index - 1] if index <= 3 else f"{index}."

        # Ism: @username bo'lsa shunday, aks holda userga mention havolasi
        if entry.username:
            who = f"@{html.escape(entry.username)}"
        else:
            safe_name = html.escape(entry.first_name or f"ID {entry.user_id}")
            who = f'<a href="tg://user?id={entry.user_id}">{safe_name}</a>'

        rows.append(f"{badge} {who} — <b>{entry.completed}</b> ta do'st")

    await callback.answer()
    await callback.message.answer(
        TOP_REFERRALS_TEXT.format(limit=len(top), rows="\n".join(rows)),
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
