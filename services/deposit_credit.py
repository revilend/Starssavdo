"""
services/deposit_credit.py
==========================
Karta to'lovini balansga qo'shishning **yagona** servisi.

Ikki manba bir xil mantiqdan foydalanadi:
 1. `handlers/user/card_auto.py` — bot o'zi a'zo bo'lgan guruh/kanaldagi
    jurnal xabarlari (Bot API);
 2. `services/userbot.py` — Telethon (shaxsiy profil) orqali
    `@humocardbot` dan kelgan xabarlar (MTProto).

Mantiq:
 - `pending` so'rovlar orasidan xabar matnidagi summa topiladi
   (`services/card_payment.find_payment_amount`);
 - `DepositRepository.auto_confirm_deposit` summani **atomik** tasdiqlaydi
   (muddati o'tgan so'rov hisoblanmaydi);
 - foydalanuvchiga va adminlarga xabar yuboriladi.

Takroriy xabar (Telegram qayta yuborishi yoki ikki manba bir vaqtda
ko'rishi) balansni ikki marta oshirmaydi: `auto_confirm_deposit` faqat
`pending` so'rovni topadi, qolgan holatda `None` qaytaradi.
"""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from sqlalchemy.exc import SQLAlchemyError

from config.settings import settings
from database.models import DepositRequest
from database.queries import (
    AUTO_DEPOSIT_ADMIN_ID,
    DEPOSIT_PENDING,
    DepositRepository,
    RepositoryError,
)
from services.card_payment import find_payment_amount, format_som

logger = logging.getLogger(__name__)

# Foydalanuvchiga yuboriladigan xabar (avtomatik tasdiqlash)
AUTO_CREDIT_USER_TEXT = (
    "🎉 <b>To'lovingiz avtomatik qabul qilindi!</b>\n\n"
    "├ Miqdor: <b>+{amount} ⭐️</b>\n"
    "├ To'lov: <b>{pay_amount} so'm</b>\n"
    "├ Yangi balans: <b>{balance} ⭐️</b>\n"
    "└ So'rov: <code>#{deposit_id}</code>\n\n"
    "Xarid qilishingiz mumkin! 🛍"
)

# Adminlarga qisqa hisobot
ADMIN_AUTO_CREDIT_TEXT = (
    "🤖 <b>AVTOMATIK KARTA TO'LOVI</b>\n\n"
    "├ Foydalanuvchi: <code>{user_id}</code>\n"
    "├ Miqdor: <b>+{amount} ⭐️</b>\n"
    "├ Summa: <b>{pay_amount} so'm</b>\n"
    "└ So'rov: <code>#{deposit_id}</code>\n\n"
    "Balans admin aralashuvisiz qo'shildi."
)


async def _notify(bot, chat_id: int, text: str) -> None:
    """Xabar yuboradi; bloklangan/xato holatlar logga yoziladi."""
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except TelegramAPIError as exc:
        logger.warning("Xabar yuborilmadi (chat=%s): %s", chat_id, exc)


async def credit_payment(
    pay_amount: int,
    bot,
    comment: str | None = None,
) -> tuple[DepositRequest, int] | None:
    """
    Summa bo'yicha `pending` to'lovni tasdiqlaydi va xabarlarni yuboradi.

    Args:
        pay_amount: jurnal xabarida topilgan aniq summa (so'm).
        bot: aiogram `Bot` — foydalanuvchi/adminlarga xabar yuborish uchun.
        comment: jurnal xabari matni (audit uchun saqlanadi).

    Returns:
        (deposit, yangi_balans) yoki `None` — mos `pending` so'rov yo'q.
    """
    if pay_amount <= 0:
        return None

    result = await DepositRepository.auto_confirm_deposit(pay_amount, comment=comment)
    if result is None:
        logger.info(
            "To'lov: %s so'm uchun faol (pending) so'rov topilmadi",
            format_som(pay_amount),
        )
        return None

    deposit, new_balance = result
    logger.info(
        "To'lov avtomatik tasdiqlandi: deposit=%s user=%s amount=%s pay=%s",
        deposit.id, deposit.user_id, deposit.amount, pay_amount,
    )

    await _notify(
        bot,
        deposit.user_id,
        AUTO_CREDIT_USER_TEXT.format(
            amount=deposit.amount,
            pay_amount=format_som(pay_amount),
            balance=new_balance,
            deposit_id=deposit.id,
        ),
    )

    for admin_id in settings.ADMIN_IDS:
        if admin_id == AUTO_DEPOSIT_ADMIN_ID:
            continue
        await _notify(
            bot,
            admin_id,
            ADMIN_AUTO_CREDIT_TEXT.format(
                user_id=deposit.user_id,
                amount=deposit.amount,
                pay_amount=format_som(pay_amount),
                deposit_id=deposit.id,
            ),
        )

    return deposit, new_balance


async def credit_payment_text(
    text: str,
    bot,
    comment: str | None = None,
) -> tuple[DepositRequest, int] | None:
    """
    Matndagi summani `pending` so'rovlar bilan solishtirib tasdiqlaydi.

    Returns:
        (deposit, yangi_balans) yoki `None` (summa topilmadi / mos emas /
        so'rov allaqachon yopilgan).
    """
    try:
        pending = await DepositRepository.get_deposits(
            status=DEPOSIT_PENDING, limit=500
        )
    except SQLAlchemyError:
        logger.exception("To'lov: pending so'rovlarni o'qib bo'lmadi")
        return None

    candidates = [deposit.pay_amount for deposit in pending if deposit.pay_amount]
    pay_amount = find_payment_amount(text, candidates)
    if pay_amount is None:
        logger.debug("To'lov: mos summa topilmadi (len=%s)", len(text or ""))
        return None

    try:
        return await credit_payment(
            pay_amount, bot, comment=comment if comment is not None else text
        )
    except (RepositoryError, SQLAlchemyError):
        logger.exception("Avtomatik tasdiqlashda xatolik: pay_amount=%s", pay_amount)
        return None


def message_text(message: Message) -> str:
    """Xabar matni yoki rasm izohi (caption)."""
    return message.text or getattr(message, "caption", None) or ""
