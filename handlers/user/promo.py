"""
handlers/user/promo.py
======================
Foydalanuvchining promokodni qabul qilishi.

Foydalanuvchi botga matn yozadi (masalan: `YUTUQ`); matn promokod
formatiga mos kelsa (`3-32 belgi, harflar/raqamlar/_`) va bazada shunday
kod bo'lsa — bir marta ishlatilib, hisobiga Stars qo'shiladi.

Xavfsizlik:
 - Har bir odam bir kodni faqat 1 marta ishlata oladi
   (`promo_redemptions` UNIQUE himoyasi, atomik tranzaksiya).
 - Limit (`max_uses`) tugagan kodlar rad etiladi.
 - Oddiy matn (promokod emas) — handler umuman tegmaydi (regex filtr).
 - FSM holatida ham ishlaydi (`StateFilter("*")`) va state'ni tozalamaydi
   (to'lov oqimi kabi holatlar uchun xavfsiz).
"""

from __future__ import annotations

import html
import logging
import re

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from database.queries import PromoError, PromoRepository

logger = logging.getLogger(__name__)

router = Router(name="user_promo")

# Promokod formati: 3-32 belgi, faqat harflar/raqamlar/underscore.
# `fullmatch` — butun xabar aynan shu formatda bo'lishi kerak.
PROMO_CODE_RE = re.compile(r"[A-Za-z0-9_]{3,32}")

PROMO_OK_TEXT = (
    "🎉 <b>Promokod qabul qilindi!</b>\n\n"
    "├ Kod: <code>{code}</code>\n"
    "├ Miqdor: <b>+{stars} ⭐️</b>\n"
    "└ Yangi balans: <b>{balance} ⭐️</b>\n\n"
    "Rahmat! 🚀"
)


@router.message(StateFilter("*"), F.text.regexp(PROMO_CODE_RE))
async def promo_redeem(message: Message, state: FSMContext | None = None) -> None:
    """
    Matn promokod formatida bo'lsa — bazadan tekshirib ishlatadi.

    Noto'g'ri formatdagi matnlarga bu handler umuman tegmaydi
    (`regexp fullmatch` talab qiladi); format mos, lekin kod topilmasa
    esa hech narsa javob qaytarilmaydi — boshqa handlerlar ishlayveradi.
    """
    raw = (message.text or "").strip()
    if not PROMO_CODE_RE.fullmatch(raw):
        return

    code = raw.upper()
    user = message.from_user
    if user is None:
        return

    try:
        new_balance = await PromoRepository.redeem_code(code, user.id)
    except PromoError as exc:
        # Kod topilmadi — jim o'tamiz (oddiy matn bo'lishi mumkin);
        # lekin "allaqachon ishlatgan"/"limit tugagan" kabi aniq xatolar
        # foydalanuvchiga ko'rsatiladi.
        text = str(exc)
        if "topilmadi" in text:
            logger.debug("Promokod topilmadi: %r user=%s", code, user.id)
            return
        await message.answer(f"⚠️ {html.escape(text)}")
        return

    # `redeem_code` PromoCode obyektini qaytarmaydi — stars miqdorini
    # matndan ajratib olish o'rniga qayta o'qiymiz (xavfsiz, kod o'zgarmaydi).
    promo = await PromoRepository.get_code(code)
    stars = promo.stars if promo is not None else 0

    await message.answer(
        PROMO_OK_TEXT.format(
            code=html.escape(code),
            stars=stars,
            balance=new_balance,
        )
    )
    logger.info("Promokod ishlatildi: %s user=%s +%s", code, user.id, stars)
