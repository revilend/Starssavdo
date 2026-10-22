"""
main.py
=======
Telegram Stars, NFT, Premium, Mayda Giftlar va Referal boti (@nftyutbot)
— Render.com da 24/7 ishlaydigan **webhook** kirish nuqtasi (FastAPI).

Arxitektura:
 - `FastAPI` ilova `uvicorn` orqali `0.0.0.0:$PORT` da ishlaydi
   (Render start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`).
 - `lifespan` (startup/shutdown) boshqaradi:
     1) Supabase (PostgreSQL) ulanishini tekshiradi (`test_connection()`);
        muvaffaqiyatsiz bo'lsa ilova ishga tushmaydi (xato erta aniqlanadi).
     2) Webhook rejimida: `set_webhook(url=f"{WEBHOOK_URL}{WEBHOOK_PATH}",
        drop_pending_updates=True, secret_token=WEBHOOK_SECRET,
        allowed_updates=...)`.
     3) Polling rejimida (`BOT_MODE=polling` — lokal ishlash uchun):
        webhook o'chiriladi va `dp.start_polling()` fon vazifasi sifatida
        ishga tushadi.
     4) To'xtaganda: webhook o'chiriladi (yoki polling to'xtatiladi),
        DB pool (`close_connection()`) va bot session yopiladi.
 - Endpointlar:
     | Manzil   | Javob                                              |
     |----------|----------------------------------------------------|
     | GET /    | 200 — {"status": "ok", "bot": "@nftyutbot"}         |
     | GET /health | 200 — {"status": "ok", "bot": "@nftyutbot"}       |
     | POST /webhook | 200 — Telegram update Dispatcher'ga uzatiladi |
   `/` va `/health` — Render health check + UptimeRobot keep-alive uchun.
 - `/webhook` `X-Telegram-Bot-Api-Secret-Token` sarlavhasini tekshiradi
   (agar `WEBHOOK_SECRET` o'rnatilgan bo'lsa). Noto'g'ri token -> 403,
   buzilgan JSON -> 400, handlerdagi kutilmagan xato -> 500 (Telegram
   qayta yuboradi; buyurtma/referal operatsiyalari idempotent).
 - Routerlar tartibi: admin routers -> subscription -> start & referral
   -> shop & payout. Middlewarelar: Throttling -> Subscription.
 - Bitta worker (uvicorn default) ishlatiladi: FSM `MemoryStorage` va
   ichki TTL keshlar jarayon ichida, shuning uchun `--workers > 1` tavsiya
   etilmaydi.

Ishga tushirish:
    python main.py                                  # lokal (uvicorn ichida)
    uvicorn main:app --host 0.0.0.0 --port $PORT    # Render (webhook)
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager, suppress
from typing import Annotated, AsyncIterator

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from config.settings import settings

# -------------------------------------------------------------
# 1. Admin routerlari (birinchi ulanadi)
# -------------------------------------------------------------
from handlers.admin.broadcast import router as admin_broadcast_router
from handlers.admin.channels import router as admin_channels_router
from handlers.admin.panel import router as admin_panel_router
from handlers.admin.users_manage import router as admin_users_manage_router
from handlers.admin.withdrawals import router as admin_withdrawals_router

# -------------------------------------------------------------
# 2. User routerlari (subscription -> start/referral -> shop/payout)
# -------------------------------------------------------------
from handlers.user.payout import router as payout_router
from handlers.user.referral import router as referral_router
from handlers.user.shop import router as shop_router
from handlers.user.start import router as start_router
from handlers.user.subscription import router as subscription_router

# -------------------------------------------------------------
# 3. Middlewarelar
# -------------------------------------------------------------
from middlewares.subscription import SubscriptionMiddleware
from middlewares.throttling import ThrottlingMiddleware

# -------------------------------------------------------------
# 4. Database
# -------------------------------------------------------------
from database.connection import close_connection, test_connection

logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# 5. Health check (Render + UptimeRobot)
# -------------------------------------------------------------
# Bot nomi — health check javobida qaytariladi.
HEALTH_BOT_USERNAME = "@nftyutbot"

# Health check javobining tanasi.
HEALTH_PAYLOAD: dict[str, str] = {"status": "ok", "bot": HEALTH_BOT_USERNAME}

# Render port berilmagan holatda ishlatiladigan standart port.
DEFAULT_PORT = 8080

# Telegram secret_token sarlavhasi (Telegram Bot API tomonidan yuboriladi).
SECRET_TOKEN_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def setup_logging() -> None:
    """Konsol loggingini sozlaydi (INFO; xatolar batafsil)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Ayrim "shovqin" kutubxonalarni pasaytiramiz
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def create_bot() -> Bot:
    """
    Bot yaratadi — barcha xabarlar standart HTML parse_mode bilan
    (DefaultBotProperties) — handlerlarda har safar yozish shart emas.
    """
    return Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    """Dispatcher (FSM: MemoryStorage) yaratadi va routerlarni ulaydi."""
    dp = Dispatcher(storage=MemoryStorage())

    # --- Middlewarelar (outer, tartib: avval spam, keyin obuna) ---
    dp.update.outer_middleware(ThrottlingMiddleware())
    dp.update.outer_middleware(SubscriptionMiddleware())

    # --- Routerlar: admin -> subscription -> start/referral -> shop/payout ---
    dp.include_router(admin_panel_router)
    dp.include_router(admin_channels_router)
    dp.include_router(admin_broadcast_router)
    dp.include_router(admin_users_manage_router)
    dp.include_router(admin_withdrawals_router)

    dp.include_router(subscription_router)

    dp.include_router(start_router)
    dp.include_router(referral_router)

    dp.include_router(shop_router)
    dp.include_router(payout_router)

    return dp


# Bot va Dispatcher bitta jarayonda (uvicorn worker) — bitta nusxa.
bot: Bot = create_bot()
dp: Dispatcher = create_dispatcher()


def resolve_port() -> int:
    """
    Tinglanadigan portni aniqlaydi.

    Render `PORT` environment variable'ini qo'yadi (odatda 10000+).
    Topilmasa `settings.PORT` (default 8080) ishlatiladi.
    """
    raw = os.getenv("PORT") or settings.PORT or DEFAULT_PORT
    try:
        port = int(raw)
    except (TypeError, ValueError):
        logger.warning("PORT noto'g'ri qiymat: %r — %s ishlatiladi.", raw, DEFAULT_PORT)
        return DEFAULT_PORT

    if not (1 <= port <= 65535):
        logger.warning("PORT chegaradan tashqarida: %s — %s.", port, DEFAULT_PORT)
        return DEFAULT_PORT
    return port


def webhook_endpoint_url() -> str:
    """
    To'liq webhook manzilini qaytaradi: `{WEBHOOK_URL}{WEBHOOK_PATH}`.

    `WEBHOOK_URL` oxiridagi `/` settings validatori tomonidan olib tashlangan.
    Raises:
        RuntimeError: `BOT_MODE=webhook` bo'lib, `WEBHOOK_URL` bo'sh bo'lsa.
    """
    base = (settings.WEBHOOK_URL or "").rstrip("/")
    if not base:
        raise RuntimeError(
            "WEBHOOK_URL bo'sh! Render'da https://<service>.onrender.com ni "
            "kiriting yoki lokal ishlash uchun BOT_MODE=polling qo'ying."
        )
    return f"{base}{settings.WEBHOOK_PATH}"


# -------------------------------------------------------------
# Startup / shutdown
# -------------------------------------------------------------
async def _start_webhook() -> None:
    """Telegram webhookini o'rnatadi (Render https manzili bilan)."""
    url = webhook_endpoint_url()
    await bot.set_webhook(
        url=url,
        drop_pending_updates=True,
        secret_token=settings.WEBHOOK_SECRET or None,
        allowed_updates=dp.resolve_used_update_types(),
    )
    logger.info(
        "Webhook o'rnatildi: %s (secret_token=%s, allowed_updates=%s)",
        url,
        "ha" if settings.WEBHOOK_SECRET else "yo'q",
        dp.resolve_used_update_types(),
    )
    if not settings.WEBHOOK_SECRET:
        logger.warning(
            "WEBHOOK_SECRET bo'sh — /webhook endpointi imzosiz so'rovlarni ham "
            "qabul qiladi. Production'da secret o'rnatish tavsiya etiladi."
        )


def _log_polling_failure(task: asyncio.Task[None]) -> None:
    """Polling vazifasi kutilmaganda to'xtasa — sababini logga yozadi."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Polling to'xtab qoldi: %s", exc, exc_info=exc)


async def _start_polling() -> asyncio.Task[None]:
    """
    Polling rejimini (lokal ishlash uchun) fon vazifasi sifatida boshlaydi.

    Signal boshqaruvi uvicorn'ga qoldiriladi (`handle_signals=False`) —
    SIGTERM kelganda uvicorn lifespan shutdown'ni ishga tushiradi va
    vazifa bekor qilinadi.
    """
    await bot.delete_webhook(drop_pending_updates=True)
    task = asyncio.create_task(
        dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            handle_signals=False,
            close_bot_session=False,
        ),
        name="telegram-polling",
    )
    task.add_done_callback(_log_polling_failure)
    logger.info("Polling rejimi boshlandi (BOT_MODE=polling).")
    return task


async def _shutdown(polling_task: asyncio.Task[None] | None) -> None:
    """Toza yopish: polling/webhook, DB pool va bot session."""
    logger.info("Bot yopilmoqda…")

    if polling_task is not None:
        polling_task.cancel()
        with suppress(asyncio.CancelledError):
            await polling_task
    else:
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            logger.info("Webhook o'chirildi.")
        except Exception:  # noqa: BLE001 — yopishda xato dasturni sindirmasin
            logger.warning("Webhookni o'chirib bo'lmadi (tarmoq xatosi?).")

    # Supabase connection poolni yopish
    await close_connection()

    # Bot sessionni yopish
    await bot.session.close()

    logger.info("Barcha ulanishlar yopildi. Xayr!")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """
    Ilova hayot sikli: DB tekshiruvi -> webhook/polling -> toza yopish.

    Startup'da xato bo'lsa (masalan, baza yo'q) ilova ishga tushmaydi —
    Render deploy muvaffaqiyatsiz deb belgilanadi va muammo logda ko'rinadi.
    """
    setup_logging()
    logger.info(
        "Bot ishga tushmoqda… rejim=%s adminlar=%s",
        settings.BOT_MODE,
        settings.ADMIN_IDS,
    )

    polling_task: asyncio.Task[None] | None = None
    try:
        # 1) Database ulanishini tekshirish (majburiy)
        if not await test_connection():
            raise RuntimeError(
                "PostgreSQL bazasiga ulanib bo'lmadi — DATABASE_URL va DB_SSL "
                "qiymatlarini tekshiring."
            )

        # 2) Tanlangan rejimni ishga tushirish
        if settings.BOT_MODE == "polling":
            polling_task = await _start_polling()
        else:
            await _start_webhook()

        yield
    finally:
        await _shutdown(polling_task)


# -------------------------------------------------------------
# FastAPI ilovasi
# -------------------------------------------------------------
app = FastAPI(
    title="nftyutbot",
    description="Telegram Stars / NFT / Premium boti — webhook server",
    lifespan=lifespan,
    # Hujjatlar ommaviy emas (production xavfsizligi uchun o'chirilgan)
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/", include_in_schema=False)
@app.get("/health", include_in_schema=False)
async def health_handler() -> dict[str, str]:
    """
    Health check: Render va UptimeRobot shu manzilga ping yuboradi.

    Bot jarayoni ishlayotgan bo'lsa doimiy `200 OK` + JSON qaytariladi.
    """
    return HEALTH_PAYLOAD


@app.post(settings.WEBHOOK_PATH, include_in_schema=False)
async def telegram_webhook(
    request: Request,
    secret_token: Annotated[
        str | None, Header(alias=SECRET_TOKEN_HEADER)
    ] = None,
) -> JSONResponse:
    """
    Telegram update'larini qabul qiladi va Dispatcher'ga uzatadi.

    Xavfsizlik:
      - `WEBHOOK_SECRET` o'rnatilgan bo'lsa, `X-Telegram-Bot-Api-Secret-Token`
        sarlavhasi doimiy vaqtda (hmac.compare_digest) solishtiriladi.
        Mos kelmasa — 403.
      - Buzilgan JSON yoki noto'g'ri Update — 400.
      - Handlerdagi kutilmagan xato — 500 (Telegram qayta yuboradi;
        buyurtmalar/referal mukofotlari idempotent).
    """
    expected = settings.WEBHOOK_SECRET
    if expected and not hmac.compare_digest(secret_token or "", expected):
        client = request.client.host if request.client else "?"
        logger.warning("Webhook: noto'g'ri secret token (client=%s)", client)
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — buzilgan JSON
        logger.warning("Webhook: JSON o'qib bo'lmadi")
        raise HTTPException(status_code=400, detail="invalid json") from None

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid update")

    try:
        update = Update.model_validate(payload, context={"bot": bot})
    except ValidationError as exc:
        logger.warning("Webhook: noto'g'ri update — %s", exc)
        raise HTTPException(status_code=400, detail="invalid update") from None

    try:
        await dp.feed_webhook_update(bot, update)
    except Exception:  # noqa: BLE001 — 500 -> Telegram qayta yuboradi
        logger.exception("Update #%s ni qayta ishlashda xatolik", update.update_id)
        return JSONResponse({"ok": False}, status_code=500)

    return JSONResponse({"ok": True}, status_code=200)


# -------------------------------------------------------------
# `python main.py` — uvicorn'ni shu jarayonda ishga tushirish
# -------------------------------------------------------------
def main() -> None:
    """
    Sinxron kirish nuqtasi.

    Render uchun tavsiya etilgan start command — to'g'ridan-to'g'ri
    `uvicorn main:app --host 0.0.0.0 --port $PORT`; bu funksiya esa
    lokal `python main.py` uchun qulaylik.
    """
    setup_logging()
    port = resolve_port()
    try:
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=port,
            log_level="info",
            log_config=None,  # logging.basicConfig sozlamalarini saqlaymiz
            proxy_headers=True,
        )
    except KeyboardInterrupt:
        logger.info("Dastur to'xtatildi (Ctrl+C).")


if __name__ == "__main__":
    main()
