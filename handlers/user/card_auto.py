"""
handlers/user/card_auto.py
==========================
Karta to'lovlarini **avtomatik** aniqlash (jurnal chat orqali).

Qanday ishlaydi:
 1. Foydalanuvchi to'ldirish miqdorini kiritganda `handlers/user/topup.py`
    `stars * SOM_PER_STAR + 1..99 so'm` unikal summa bilan `pending`
    so'rov yaratadi (masalan 100 ⭐️ -> 25 037 so'm).
 2. `@CardXabarBot` / `@HUMOcardbot` yopiq guruh/kanalga to'lov xabarini
    yuboradi; bot o'sha guruh/kanalda (yoki admin panelda sozlangan
    `card_log_chat_id` chatida) turib, xabar matnidan summani ajratadi
    (`services/card_payment.py`).
 3. Summa `pending` so'rovga to'g'ri kelsa — balans **admin aralashuvisiz**
    atomik qo'shiladi (`DepositRepository.auto_confirm_deposit`), so'rov
    `approved` bo'ladi va foydalanuvchiga xabar boradi.
 4. Moslik topilmasa — xabar e'tiborsiz qoldiriladi (jurnal chatida hech
    qanday javob yozilmaydi). Foydalanuvchi chek yuborsa, adminlar uchun
    [✅ Tasdiqlash] / [❌ Rad etish] zaxira yo'li ishlaydi.

Muhim cheklov (Telegram qoidasi): botlar **boshqa botlar yuborgan guruh
xabarlarini olmaydi**. Shuning uchun:
  - jurnal sifatida **kanal** ishlatilsa (bank boti kanalga post qiladi va
    bizning bot kanal admini bo'lsa) postlar yetib keladi;
  - guruh bo'lsa, to'lov xabari botga ko'rinishi uchun uni qo'lda forward
    qilish yoki MTProto (userbot) ishlatish kerak.
Bu cheklovni README'da ham yozib qo'yganmiz; chek + admin yo'li har doim
zaxira sifatida ishlaydi.
"""

from __future__ import annotations

import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Filter
from aiogram.types import Message
from sqlalchemy.exc import SQLAlchemyError

from config.settings import settings
from database.queries import (
    AUTO_DEPOSIT_ADMIN_ID,
    DEPOSIT_PENDING,
    DepositRepository,
    RepositoryError,
    SettingsRepository,
)
from services.card_payment import find_payment_amount, format_som

logger = logging.getLogger(__name__)

router = Router(name="card_auto")


# Sozlangan chat ID keshi (har xabar uchun DB so'rovi qilmaslik uchun)
_chat_cache: dict[str, float | int] = {"value": 0, "at": 0.0}
_CHAT_CACHE_TTL = 60.0  # soniya


async def _configured_log_chat_id() -> int:
    """Bazadagi (yoki .env dagi) xabarnoma chat ID sini kesh bilan qaytaradi."""
    now = time.monotonic()
    if now - float(_chat_cache["at"]) < _CHAT_CACHE_TTL:
        return int(_chat_cache["value"])

    try:
        chat_id = await SettingsRepository.get_card_log_chat_id()
    except Exception:  # noqa: BLE001 — DB/network xatosi botni to'xtatmasin
        logger.warning(
            "Xabarnoma chat ID sini o'qib bo'lmadi — .env qiymati ishlatiladi"
        )
        chat_id = settings.CARD_LOG_CHAT_ID

    _chat_cache["value"] = chat_id
    _chat_cache["at"] = now
    return chat_id


class CardLogChat(Filter):
    """
    Karta to'lovlari xabari kelgan chatni aniqlaydi.

    Mos keladi, agar:
      - chat ID admin panelda (`settings.card_log_chat_id`) yoki `.env` dagi
        `CARD_LOG_CHAT_ID` ga teng bo'lsa; YOKI
      - xabar bot a'zo bo'lgan **har qanday guruh/superguruh/kanalga**
        tushgan bo'lsa (bank boti post qiladigan yopiq guruh shu yerda).

    Shu bilan `@CardXabarBot` / `@HUMOcardbot` xabarlari qaysi guruhga
    tushishidan qat'i nazar o'qiladi — admin qo'lda chat ID kiritishi shart
    emas. Kiritilsa, o'sha chat ID ham qo'shimcha tekshiriladi.
    """

    async def __call__(self, message: Message) -> bool:
        chat_type = getattr(message.chat, "type", "") or ""

        # Guruh/kanal xabari — DB'ga murojaat qilmasdan darhol qabul qilinadi
        # (bank boti xabarlari aynan shu yerga tushadi).
        if chat_type in {"group", "supergroup", "channel"}:
            return True

        # Boshqa (masalan, admin qo'lda sozlagan) chat — ID bo'yicha.
        configured = await _configured_log_chat_id()
        return bool(configured) and message.chat.id == configured


router.message.filter(CardLogChat())
router.channel_post.filter(CardLogChat())

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


def _message_text(message: Message) -> str:
    """Xabar matni yoki rasm izohi (caption)."""
    return message.text or getattr(message, "caption", None) or ""


async def _notify(bot, chat_id: int, text: str) -> None:
    """Xabar yuboradi; bloklangan/xato holatlar logga yoziladi."""
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except TelegramAPIError as exc:
        logger.warning("Xabar yuborilmadi (chat=%s): %s", chat_id, exc)


async def _handle_card_log(message: Message) -> None:
    """
    Jurnal chatidagi to'lov xabarini qayta ishlaydi.

    Summa `pending` so'rovga mos kelsa — balans atomik qo'shiladi.
    Aks holda xabar e'tiborsiz qoldiriladi (chatda javob yozilmaydi).
    """
    text = _message_text(message)
    if not text:
        return

    try:
        pending = await DepositRepository.get_deposits(
            status=DEPOSIT_PENDING, limit=500
        )
    except SQLAlchemyError:
        logger.exception("Jurnal xabari: pending so'rovlarni o'qib bo'lmadi")
        return

    candidates = [deposit.pay_amount for deposit in pending if deposit.pay_amount]

    pay_amount = find_payment_amount(text, candidates)
    if pay_amount is None:
        logger.debug(
            "Jurnal xabari: mos summa topilmadi (chat=%s, len=%s)",
            message.chat.id,
            len(text),
        )
        return

    try:
        result = await DepositRepository.auto_confirm_deposit(pay_amount, comment=text)
    except (RepositoryError, SQLAlchemyError):
        logger.exception(
            "Avtomatik tasdiqlashda xatolik: pay_amount=%s", pay_amount
        )
        return

    if result is None:
        # So'rov allaqachon ko'rib chiqilgan yoki bekor qilingan
        logger.info(
            "Jurnal xabari: %s so'm uchun pending so'rov yo'q (o'tkazildi)",
            format_som(pay_amount),
        )
        return

    deposit, new_balance = result
    logger.info(
        "Karta to'lovi avtomatik tasdiqlandi: deposit=%s user=%s amount=%s pay=%s",
        deposit.id, deposit.user_id, deposit.amount, pay_amount,
    )

    await _notify(
        message.bot,
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
            message.bot,
            admin_id,
            ADMIN_AUTO_CREDIT_TEXT.format(
                user_id=deposit.user_id,
                amount=deposit.amount,
                pay_amount=format_som(pay_amount),
                deposit_id=deposit.id,
            ),
        )


# =============================================================
# Ro'yxatdan o'tkazish: guruh xabarlari ham, kanal postlari ham
# =============================================================
@router.message(F.text | F.caption)
async def on_card_log_message(message: Message) -> None:
    """Guruh/superguruhdagi to'lov xabari."""
    await _handle_card_log(message)


@router.channel_post(F.text | F.caption)
async def on_card_log_channel_post(message: Message) -> None:
    """Kanaldagi to'lov posti (bank boti kanalga post qilganda)."""
    await _handle_card_log(message)


@router.message()
async def on_card_log_other(message: Message) -> None:
    """
    Jurnal chatidagi matnsiz xabarlar (rasm/stiker) — e'tiborsiz qoldiriladi.

    Bu handler `CardLogChat` filtri tufayli faqat jurnal chatida ishlaydi va
    boshqa routerlarga xabar tushishini to'sadi.
    """
    logger.debug("Jurnal chatida matnsiz xabar: %s", type(message).__name__)


@router.channel_post()
async def on_card_log_other_channel_post(message: Message) -> None:
    """Kanaldagi matnsiz post — e'tiborsiz qoldiriladi."""
    logger.debug("Jurnal kanalida matnsiz post: %s", type(message).__name__)
