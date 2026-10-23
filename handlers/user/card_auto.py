"""
handlers/user/card_auto.py
==========================
Karta to'lovlarini **avtomatik** aniqlash (bot jurnal chat orqali).

Qanday ishlaydi:
 1. Foydalanuvchi to'ldirish miqdorini kiritganda `handlers/user/topup.py`
    `stars * SOM_PER_STAR + 40..100 so'm` unikal summa bilan `pending`
    so'rov yaratadi (masalan 100 ⭐️ -> 30 047 so'm) va so'rov 5 daqiqa
    amal qiladi.
 2. `@CardXabarBot` / `@HUMOcardbot` yopiq guruh/kanalga to'lov xabarini
    yuboradi; bot o'sha guruh/kanalda (yoki admin panelda sozlangan
    `card_log_chat_id` chatida) turib, xabar matnidan summani ajratadi
    (`services/deposit_credit.py`).
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
  - guruh bo'lsa, `@humocardbot` xabarlarini **Telethon userbot**
    (`services/userbot.py`, `TELEGRAM_API_ID/HASH/SESSION_STRING`) o'qiydi.
Chek + admin yo'li har doim zaxira sifatida ishlaydi.
"""

from __future__ import annotations

import logging
import time

from aiogram import F, Router
from aiogram.filters import Filter
from aiogram.types import Message

from config.settings import settings
from database.queries import SettingsRepository
from services.deposit_credit import (
    ADMIN_AUTO_CREDIT_TEXT,
    AUTO_CREDIT_USER_TEXT,
    credit_payment_text,
    message_text,
)
from services.order_payments import credit_order_text

logger = logging.getLogger(__name__)

router = Router(name="card_auto")

# Orqaga moslik uchun qayta eksport (eski importlar buzilmasin)
__all__ = [
    "ADMIN_AUTO_CREDIT_TEXT",
    "AUTO_CREDIT_USER_TEXT",
    "CardLogChat",
    "on_card_log_channel_post",
    "on_card_log_message",
    "router",
]


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
    """

    async def __call__(self, message: Message) -> bool:
        chat_type = getattr(message.chat, "type", "") or ""

        # Guruh/kanal xabari — DB'ga murojaat qilmasdan darhol qabul qilinadi.
        if chat_type in {"group", "supergroup", "channel"}:
            return True

        # Boshqa (masalan, admin qo'lda sozlagan) chat — ID bo'yicha.
        configured = await _configured_log_chat_id()
        return bool(configured) and message.chat.id == configured


router.message.filter(CardLogChat())
router.channel_post.filter(CardLogChat())


async def _handle_card_log(message: Message) -> None:
    """Jurnal chatidagi to'lov xabarini qayta ishlaydi."""
    text = message_text(message)
    if not text:
        return

    try:
        # 1) Mahsulot buyurtmasi (do'kon) — UZS summa bo'yicha
        order = await credit_order_text(text, message.bot)
        # 2) Balans to'ldirish (Stars) — deposit summasi bo'yicha
        if order is None:
            await credit_payment_text(text, message.bot)
    except Exception:  # noqa: BLE001 — noto'g'ri xabar botni to'xtatmasin
        logger.exception("Jurnal xabari: kutilmagan xatolik")


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
    """Jurnal chatidagi matnsiz xabarlar — e'tiborsiz qoldiriladi."""
    logger.debug("Jurnal chatida matnsiz xabar: %s", type(message).__name__)


@router.channel_post()
async def on_card_log_other_channel_post(message: Message) -> None:
    """Jurnal kanalidagi matnsiz post — e'tiborsiz qoldiriladi."""
    logger.debug("Jurnal kanalida matnsiz post: %s", type(message).__name__)
