"""
services/userbot.py
===================
Telethon (MTProto) **userbot** — `@humocardbot` to'lov xabarlarini eshitadi.

Nega kerak:
 - Telegram Bot API qoidasiga ko'ra bot **boshqa bot** yuborgan guruh
   xabarlarini olmaydi. Shu sababli `@humocardbot` yuboradigan
   "🎉 To'ldirish ➕ 30 047 UZS" xabarlarini bot o'zi ko'ra olmaydi.
 - Telethon **shaxsiy profil** (user session) orqali esa bu xabarlar
   to'g'ridan-to'g'ri o'qiladi: foydalanuvchi profilida `@humocardbot`
   bilan chat mavjud bo'lsa, unga kelgan xabar shu handler'ga tushadi.

Ishga tushirish (`.env`):
    TELEGRAM_API_ID=1234567            # https://my.telegram.org/apps
    TELEGRAM_API_HASH=abcdef...        # https://my.telegram.org/apps
    TELEGRAM_SESSION_STRING=1BVtsOK... # StringSession (pastdagi README)
    HUMOCARD_BOT_USERNAME=humocardbot

Agar qiymatlar bo'sh bo'lsa modul umuman ishga tushmaydi — bot guruh/kanal
jurnal xabarlari (`handlers/user/card_auto.py`) bilan avvalgidek ishlaydi,
ya'ni bu integratsiya **ixtiyoriy** va startup'ni to'xtatmaydi.

Xavfsizlik:
 - Session string — shaxsiy profil kaliti; faqat env/secret sifatida
   saqlanadi, kodga yozilmaydi.
 - Xabar matnidagi summa faqat **muddati o'tmagan `pending`** so'rovga
   moslanadi va tasdiqlash atomik (`services/deposit_credit.py`).
"""

from __future__ import annotations

import asyncio
import logging
import re

from config.settings import settings
from services.deposit_credit import credit_payment_text
from services.order_payments import credit_order_text

logger = logging.getLogger(__name__)

# "To'ldirish" so'zi turli apostrof variantlari bilan kelishi mumkin
_TOPDIRISH_RE = re.compile(r"to['\u2019\u02bb\u02bc\u0060]ldirish", re.IGNORECASE)

# Summa + valyuta: "30 047 UZS", "30047 so'm", "1\u00a0000\u202f000 UZS"
_AMOUNT_RE = re.compile(
    r"(\d[\d\s\u00a0\u202f\u2009,.]*)\s*(?:UZS|so['\u2019\u02bb\u02bc]?m)",
    re.IGNORECASE,
)

# Summani tozalash uchun guruh ajratgichlari
_SEPARATORS_RE = re.compile(r"[\s\u00a0\u202f\u2009,.]")


def parse_humocard_message(text: str | None) -> int | None:
    """
    `@humocardbot` xabaridan to'lov summasini (so'm) ajratadi.

    Misollar:
        "🎉 To'ldirish ➕ 30 047 UZS"        -> 30047
        "To'ldirish ➕ 100 000 so'm"        -> 100000
        "Balans: 5 000 UZS"                -> None (To'ldirish yo'q)
        "To'ldirish ➕ 30 047 UZS, 1 000"   -> 30047 (aniq bitta valyutali summa)

    Noaniq holatda (bir nechta turli valyutali summa) `None` qaytariladi —
    noto'g'ri foydalanuvchiga balans qo'shilmasligi uchun.
    """
    if not text:
        return None

    if not _TOPDIRISH_RE.search(text):
        return None

    amounts: list[int] = []
    for match in _AMOUNT_RE.finditer(text):
        digits = _SEPARATORS_RE.sub("", match.group(1))
        if not digits.isdigit():
            continue
        value = int(digits)
        if value > 0 and value not in amounts:
            amounts.append(value)

    if len(amounts) != 1:
        return None
    return amounts[0]


class HumocardUserbot:
    """`@humocardbot` xabarlarini tinglaydigan Telethon klienti."""

    def __init__(self, bot) -> None:
        self._bot = bot
        self._client = None

    @property
    def configured(self) -> bool:
        """Env'da API_ID/HASH/SESSION to'liq kiritilganmi."""
        return settings.USERBOT_ENABLED

    @property
    def running(self) -> bool:
        """Klient ulanganmi."""
        return self._client is not None and self._client.is_connected()

    async def _handle_payment_message(self, event) -> None:
        """Bitta `@humocardbot` xabarini qayta ishlaydi."""
        try:
            text = event.raw_text or ""
            amount = parse_humocard_message(text)
            if amount is None:
                logger.debug("Userbot: xabar summasi aniqlanmadi: %r", text[:120])
                return

            logger.info("Userbot: to'lov xabari topildi — %s so'm", amount)
            # Avval do'kon buyurtmasi, keyin balans to'ldirish tekshiriladi
            order = await credit_order_text(text, self._bot)
            if order is None:
                await credit_payment_text(text, self._bot, comment=text[:1000])
        except Exception:  # noqa: BLE001 — bitta xabar userbotni to'xtatmasin
            logger.exception("Userbot: xabarni qayta ishlashda xatolik")

    async def start(self) -> bool:
        """
        Telethon klientini ulaydi va handler'ni ro'yxatdan o'tkazadi.

        Returns:
            True — userbot ishlayapti; False — sozlanmagan yoki ulanmadi
            (bot baribir ishlayveradi).
        """
        if not self.configured:
            logger.info(
                "Telethon userbot o'chirilgan (TELEGRAM_API_ID/HASH/"
                "SESSION_STRING bo'sh) — jurnal chat rejimi ishlatiladi."
            )
            return False

        try:
            from telethon import TelegramClient, events
            from telethon.sessions import StringSession
        except ImportError:
            logger.error(
                "telethon o'rnatilmagan — `pip install -r requirements.txt`"
            )
            return False

        client = TelegramClient(
            StringSession(settings.TELEGRAM_SESSION_STRING.strip()),
            settings.TELEGRAM_API_ID,
            settings.TELEGRAM_API_HASH.strip(),
            connection_retries=5,
            retry_delay=2,
            auto_reconnect=True,
        )

        try:
            # Ulanish Render startup'ini uzoq kutdirib qo'ymasin
            await asyncio.wait_for(client.connect(), timeout=30)
            if not await client.is_user_authorized():
                logger.error(
                    "Userbot sessiyasi avtorizatsiyadan o'tmagan — "
                    "TELEGRAM_SESSION_STRING'ni yangilang."
                )
                await client.disconnect()
                return False

            client.add_event_handler(
                self._handle_payment_message,
                events.NewMessage(
                    chats=settings.HUMOCARD_BOT_USERNAME, incoming=True
                ),
            )
            self._client = client

            me = await client.get_me()
            logger.info(
                "Telethon userbot ulandi: id=%s username=@%s — "
                "@%s xabarlari tinglanmoqda.",
                getattr(me, "id", "?"),
                getattr(me, "username", "?"),
                settings.HUMOCARD_BOT_USERNAME,
            )
            return True
        except Exception:  # noqa: BLE001 — startup to'xtamasin
            logger.exception(
                "Telethon userbot ulanmadi (API ID/HASH/sessiya yoki tarmoq)"
            )
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            return False

    async def stop(self) -> None:
        """Ulanishni yopadi."""
        if self._client is None:
            return
        try:
            await self._client.disconnect()
            logger.info("Telethon userbot to'xtatildi.")
        except Exception:  # noqa: BLE001 — shutdown toza bo'lsin
            logger.warning("Userbotni yopishda xatolik")
        finally:
            self._client = None
