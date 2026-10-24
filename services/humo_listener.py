"""
services/humo_listener.py
=========================
Telethon (MTProto) orqali `@humocardbot` to'lov xabarlarini tinglaydigan
avtomatik tasdiqlash moduli.

QANDAY ISHLAYDI:
 1. `.env` dagi `TG_API_ID` / `TG_API_HASH` (yoki eski `TELEGRAM_API_*`)
    qiymatlari bilan `TelegramClient(settings.TG_SESSION_NAME, ...)` ochiladi.
    Sessiya fayli `*.session` — .gitignore'da (GitHubga chiqmaydi).
 2. `events.NewMessage(chats='@humocardbot')` — faqat shu botdan kelgan
    yangi xabarlar tinglanadi.
 3. Xabar matnida:
      - "➕ To'ldirish" yoki "Пополнение" so'zi (paypal uchun keng moslik);
      - karta raqamining OXIRGI 4 raqami (`config.HUMO_CARD_NUMBER[-4:]`);
      - summa (`([\d\s.,]+)\s*UZS` regex — butun songa aylantiriladi);
    borligi tekshiriladi.
 4. Bazada `status='pending'`, `pay_amount == amount` va OXIRGI 20 DAQIQADA
    yaratilgan to'lov so'rovi topiladi.
 5. Topilsa — ATOMIK tranzaksiya (`FOR UPDATE`, `DepositRepository.
    confirm_deposit`) bilan so'rov `approved` qilinadi, Stars balans
    qo'shiladi va Aiogram bot mijozga
    "✅ To'lovingiz avtomatik tasdiqlandi!" xabar yuboradi.

XAVFSIZLIK:
 - Barcha maxfiy qiymatlar faqat `.env`dan o'qiladi (hardcode YO'Q).
 - Tasdiqlash `FOR UPDATE` bilan atomik: bir to'lov ikki marta hisobga
   olinmaydi (`DepositStateError`).
 - Bu modul xato bilan ham botning ishlashini to'xtatmaydi — har bir
   xabar `try/except` bilan o'ralgan.

Ulashish (main.py):
    from services.humo_listener import HumoListener
    listener = HumoListener(bot)
    if listener.configured:
        await listener.start()   # polling bilan parallel (asyncio.gather)
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import Any

from config.settings import settings
from database.queries import (
    AUTO_DEPOSIT_ADMIN_ID,
    DEPOSIT_PENDING,
    DepositRepository,
)
from database.models import utcnow

logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# Regex va konstantalar
# -------------------------------------------------------------
# "To'ldirish" so'zi turli apostrof/emoji variantlari bilan kelishi mumkin
_TRIGGERS: tuple[str, ...] = ("to'ldirish", "toldirish", "пополнение")

# Summa + UZS: "30 047 UZS", "30,047 UZS", "30047 UZS"
_AMOUNT_RE = re.compile(r"([\d\s.,]+)\s*UZS", re.IGNORECASE)

# Summani tozalash: bo'shliq, vergul, nuqta ajratgichlari
_SEPARATORS_RE = re.compile(r"[\s,.]")

# So'rov qidirish oynasi: oxirgi 20 daqiqa
LOOKBACK_WINDOW = timedelta(minutes=20)

# Tinglanadigan bot username'i (sozlanmagan bo'lsa standart)
HUMOCARD_BOT = settings.HUMOCARD_BOT_USERNAME or "humocardbot"


def parse_humo_statement(text: str | None) -> int | None:
    """
    `@humocardbot` xabaridan to'lov summasini (so'm) ajratadi.

    Qoidalar:
      - Matn "To'ldirish" (yoki "Пополнение") so'zini o'z ichiga olishi KEREK;
      - Ikkala son (`([\d\s.,]+)\s*UZS`) faqat BITTA bo'lishi kerak —
        noaniq holatda `None` qaytaradi (noto'g'ri foydalanuvchiga
        balans qo'shilmasligi uchun).

    Misollar:
        "➕ To'ldirish 30 047 UZS"  -> 30047
        "Пополнение 15 042 UZS"     -> 15042
        "Balans: 5 000 UZS"         -> None (to'ldirish yo'q)
        "To'ldirish 100 va 2 UZS"   -> None (noaniq)
    """
    if not text:
        return None

    lower = text.lower()
    if not any(trigger in lower for trigger in _TRIGGERS):
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


class HumoListener:
    """
    Telethon asosidagi `@humocardbot` tinglovchisi.

    Ishlatish (main.py):
        listener = HumoListener(bot)          # bot — aiogram Bot instance
        if listener.configured:
            ok = await listener.start()       # asyncio.gather bilan parallel
            if not ok:
                logger.warning("HumoListener ishga tushmadi")
    """

    def __init__(self, bot: Any) -> None:
        self._bot = bot
        self._client: Any | None = None

    # ------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        ".env'da TG_API_ID / TG_API_HASH o'rnatilganmi (SESSION shart emas)."
        return settings.TG_API_ID_ACTIVE > 0 and bool(settings.TG_API_HASH_ACTIVE)

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        "Klient ulanganmi."
        return self._client is not None and self._client.is_connected()

    # ------------------------------------------------------------------
    async def _match_card_tail(self, text: str) -> bool:
        """
        Xabarda sozlangan kartaning oxirgi 4 raqami bormi?

        Karta raqami env'dan o'qiladi (`HUMO_CARD_NUMBER` yoki
        `CARD_NUMBER`); hech qachon `.py` faylga hardcode qilmang!
        """
        card = settings.CARD_NUMBER_ACTIVE
        if not card:
            # Karta sozlanmagan — hech qachon avtomatik tasdiqlamaymiz.
            return False
        tail = card[-4:]
        return bool(tail) and tail in (text or "")

    # ------------------------------------------------------------------
    async def _handle_payment_message(self, event: Any) -> None:
        """
        Bitta `@humocardbot` xabarini qayta ishlaydi (try/except bilan —
        bitta muammoli xabar listener'ni to'xtatmaydi).
        """
        try:
            text = event.raw_text or ""

            # 1) "To'ldirish"/"Пополнение" belgisi + karta oxirgi 4 raqami
            if not await self._match_card_tail(text):
                logger.debug("HumoListener: karta oxirgi 4 raqami yo'q")
                return

            # 2) Summani regex bilan ajratamiz
            amount = parse_humo_statement(text)
            if amount is None:
                logger.debug("HumoListener: summa aniqlanmadi: %r", text[:120])
                return

            logged = await self.credit_pending_by_amount(amount, comment=text[:1000])
            if not logged:
                logger.info(
                    "HumoListener: %s so'm uchun mos `pending` so'rov topilmadi",
                    amount,
                )
        except Exception:  # noqa: BLE001 — bitta xabar listenerni to'xtatmasin
            logger.exception("HumoListener: xabarni qayta ishlashda xatolik")

    # ------------------------------------------------------------------
    async def credit_pending_by_amount(
        self, amount: int, comment: str | None = None
    ) -> bool:
        """
        Summa bo'yicha oxirgi 20 daqiqadagi `pending` so'rovni ATOMIK tasdiqlaydi.

        Qonunlar:
          1. `status='pending'` va `pay_amount == amount`;
          2. `created_at >= utcnow() - 20 daqiqa`;
          3. `confirm_deposit()` ichida `FOR UPDATE` bilan qulflana-di
             (barcha benefits bir tranzaksiyada qo'shadi);
          4. Tasdiqlangach foydalanuvchiga "✅ To'lovingiz avtomatik
             tasdiqlandi!" xabari jo'natiladi.

        Returns:
            True — so'rov tasdiqlandi va xabar yuborildi;
            False — mos so'rov topilmadi (yoki allaqachon boshqarganda).
        """
        window_start = utcnow() - LOOKBACK_WINDOW

        try:
            deposits = await DepositRepository.get_deposits(
                status=DEPOSIT_PENDING, limit=100
            )
        except Exception:  # noqa: BLE001 — DB xatosi listenerni to'xtatmasin
            logger.exception("HumoListener: pending so'rovlar o'qilmadi")
            return False

        now = utcnow()
        for deposit in deposits:
            if deposit.pay_amount != amount:
                continue
            # OXIRGI 20 DAQIQA oynasi
            if deposit.created_at and deposit.created_at < window_start:
                continue
            # Muddati ham o'tgmagan (xuddi shu ongda bo'lak bot bilan race yo'q —
            # confirm_deposit atomik)
            if deposit.expires_at and deposit.expires_at < now:
                continue

            try:
                deposit, new_balance = await DepositRepository.confirm_deposit(
                    deposit.id, AUTO_DEPOSIT_ADMIN_ID
                )
            except Exception:  # noqa: BLE001 — DB/race xatosi loglanadi
                logger.exception(
                    "HumoListener: deposit=%s tasdiqlashda xatolik", deposit.id
                )
                continue

            # ✅ Mijozga xabar
            try:
                await self._bot.send_message(
                    deposit.user_id,
                    "✅ <b>To'lovingiz avtomatik tasdiqlandi!</b>\n\n"
                    f"├ Miqdor: <b>+{deposit.amount} ⭐️</b>\n"
                    f"├ To'lov: <b>{amount:,} so'm</b>\n"
                    f"├ Yangi balans: <b>{new_balance} ⭐️</b>\n"
                    f"└ So'rov: <code>#{deposit.id}</code>",
                    parse_mode="HTML",
                )
            except Exception:  # noqa: BLE001 — Telegram xatolar ham to'xtatmaydi
                logger.exception(
                    "HumoListener: user=%sga javob yetib bormadi", deposit.user_id
                )

            logger.info(
                "HumoListener: avtomatik tasdiqda — deposit=%s user=%s "
                "amount=%s so'm stars=+%s balance=%s",
                deposit.id, deposit.user_id, amount, deposit.amount, new_balance,
            )
            return True

        return False

    # ------------------------------------------------------------------
    async def start(self) -> bool:
        """
        Telethon klientini ishga tushiradi va handler'ni ro'yxatdan o'tkazadi.

        Returns:
            True — listener ishlayapti;
            False — sozlanmagan yoki ulanmadi (bot baribir ishga kiradi).
        """
        if not self.configured:
            logger.info(
                "HumoListener o'chirilgan (TG_API_ID/TG_API_HASH bo'sh) — "
                "bot guruh/kanal jurnali bilan ishlaydi."
            )
            return False

        try:
            from telethon import TelegramClient, events
        except ImportError:
            logger.error(
                "telethon o'rnatilmagan — `pip install -r requirements.txt`"
            )
            return False

        client = TelegramClient(
            settings.TG_SESSION_NAME,           # *.session fayl nomi (git'da emas)
            settings.TG_API_ID_ACTIVE,          # fallback: TG_API_ID -> TELEGRAM_API_ID
            settings.TG_API_HASH_ACTIVE,        # fallback: TG_API_HASH -> TELEGRAM_API_HASH
            connection_retries=5,
            retry_delay=2,
            auto_reconnect=True,
        )

        try:
            # Ulanish Render startup'ini uzoq kutdirib qo'ymasin
            await asyncio.wait_for(client.connect(), timeout=30)
            if not await client.is_user_authorized():
                logger.error(
                    "HumoListener sessiyasi avtorizatsiyadan o'tmagan — "
                    "bir martalik skript bilan %s sessiyasini yarating.",
                    settings.TG_SESSION_NAME,
                )
                await client.disconnect()
                return False

            client.add_event_handler(
                self._handle_payment_message,
                events.NewMessage(chats=HUMOCARD_BOT, incoming=True),
            )
            self._client = client

            me = await client.get_me()
            logger.info(
                "HumoListener ulandi: id=%s username=@%s — @%s xabarlari "
                "tinglanmoqda.",
                getattr(me, "id", "?"),
                getattr(me, "username", "?"),
                HUMOCARD_BOT,
            )
            return True
        except Exception:  # noqa: BLE001 — startupni to'xtatmaydi
            logger.exception(
                "HumoListener ulanmadi (API ID/HASH/sessiya yoki tarmoq)"
            )
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            return False

    # ------------------------------------------------------------------
    async def stop(self) -> None:
        "Ulanishni xavfsiz yopadi."
        if self._client is None:
            return
        try:
            await self._client.disconnect()
            logger.info("HumoListener to'xtatildi.")
        except Exception:  # noqa: BLE001 — to'xtashda xato botni bloklay olmaydi
            logger.exception("HumoListener to'xtashda xatolik")
        finally:
            self._client = None
