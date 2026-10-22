"""
main.py
=======
Telegram Stars, NFT, Premium, Mayda Giftlar va Referal boti (@nftyutbot)
— asosiy kirish nuqtasi (Render.com da 24/7 ishga tushirish uchun).

Tartib:
 1. Logging sozlanadi (INFO darajasi).
 2. Bot + Dispatcher yaratiladi:
      - `DefaultBotProperties(parse_mode=ParseMode.HTML)` — barcha
        xabarlar standart HTML formatida (handlerlarda qayta yozish shart emas).
      - `MemoryStorage` — FSM holatlari (kanal qo'shish, rassilka,
        sozlamalar, balans, payout).
 3. Health check serveri ishga tushadi (Render uchun):
      - Render "Free Web Service" dastur doimiy HTTP portni ochiq
        ushlab turishni talab qiladi — aks holda service "not listening"
        deb to'xtatiladi.
      - `aiohttp.web` asosida yengil server: `/` va `/health` manzillari
        `200 OK` + `{"status": "ok", "bot": "@nftyutbot"}` javobini beradi
        (UptimeRobot har 5 daqiqada ping yuboradi).
      - Server `host="0.0.0.0"`, `port=int(os.getenv("PORT", 8080))` da
        ishga tushadi va polling bilan **parallel** ravishda ishlaydi.
 4. Database ulanishi tekshiriladi (`test_connection()`) — muvaffaqiyatsiz
    bo'lsa dastur ishga tushmaydi (xatoni erta aniqlanadi).
 5. Middlewarelar (outer, tartib muhim):
      1) ThrottlingMiddleware — spam/flood himoyasi (adminlar bundan mustasno)
      2) SubscriptionMiddleware — majburiy homiy kanallar tekshiruvi
 6. Routerlar tartib bilan ulanadi:
      admin routers -> subscription router -> start & referral routers
      -> shop & payout routers
    (admin routerlar birinchi — ularning callback'lari user handlerlarga
    tushib qolmasligi uchun; subscription esa barcha user hodisalaridan
    oldin o'tadi).
 7. `dp.start_polling(bot)` — 24/7 polling; SIGINT/SIGTERM (Ctrl+C yoki
    Render deploy/restart) toza yopilishni ta'minlaydi (veb-server,
    Supabase connection pool va bot session xavfsiz yopiladi).

Ishga tushirish:
    python main.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

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
# 5. Health check serveri (Render)
# -------------------------------------------------------------
# Bot nomi — health check javobida qaytariladi.
HEALTH_BOT_USERNAME = "@nftyutbot"

# Health check javobining tanasi (Render + UptimeRobot uchun).
HEALTH_PAYLOAD: dict[str, str] = {"status": "ok", "bot": HEALTH_BOT_USERNAME}

# Render port berilmagan holatda ishlatiladigan standart port.
DEFAULT_PORT = 8080


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


# -------------------------------------------------------------
# Health check serveri
# -------------------------------------------------------------
async def health_handler(request: web.Request) -> web.Response:
    """
    Health check so'rovi.

    Render va UptimeRobot shu manzilga ping yuboradi — bot ishlayotgan
    holatda doimiy `200 OK` va JSON javob qaytariladi.
    """
    return web.json_response(HEALTH_PAYLOAD, status=200)


def create_health_app() -> web.Application:
    """Health check uchun minimal aiohttp ilovasi (`/` va `/health`)."""
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    return app


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


class HealthServer:
    """
    Render uchun yengil HTTP server.

    Vazifasi — Render "Free Web Service" talabini bajarish: dastur
    `0.0.0.0:PORT` manzilida HTTP portini ochiq ushlab turadi va
    health check so'rovlariga `200 OK` javob beradi.

    Server `start()` dan keyin event loop ichida **fon rejimida** ishlaydi
    va polling (`dp.start_polling`) bilan parallel ravishda javob beradi.
    """

    def __init__(self, port: int, host: str = "0.0.0.0") -> None:
        self.host = host
        self.port = port
        self._runner: web.AppRunner | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        """Portni ochadi (Render shundan keyin service'ni "listening" deb hisoblaydi)."""
        app = create_health_app()
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, host=self.host, port=self.port)
        await site.start()
        self._runner = runner
        logger.info(
            "Health check server %s:%s da ishga tushdi (/ va /health -> 200 OK).",
            self.host,
            self.port,
        )

    async def serve(self) -> None:
        """Fon vazifasi — server to'xtatilguncha kutadi."""
        await self._stopped.wait()

    async def stop(self) -> None:
        """Serverni xavfsiz to'xtatadi (shutdown da chaqiriladi)."""
        self._stopped.set()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            logger.info("Health check server to'xtatildi.")


async def on_startup(bot: Bot) -> None:
    """Ishga tushishdagi tekshiruvlar: DB ulanishi va webhook tozalash."""
    # 1) Database ulanishini tekshirish
    if not await test_connection():
        logger.error("PostgreSQL bazasiga ulanib bo'lmadi!")
        raise SystemExit("Bazaga ulanib bo'lmadi — .env ni tekshiring.")

    # 2) Eski webhook va kutayotgan yangilanishlarni tozalash (polling uchun)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook tozalandi — polling rejimi tayyor.")


async def on_shutdown(bot: Bot, health: HealthServer | None = None) -> None:
    """Toza yopish: veb-server, DB pool va bot session."""
    logger.info("Bot yopilmoqda…")

    # 1) Health check serverini to'xtatish (yangi so'rovlar qabul qilinmasin)
    if health is not None:
        await health.stop()

    # 2) Supabase connection poolni yopish
    await close_connection()

    # 3) Bot sessionni yopish
    await bot.session.close()

    logger.info("Barcha ulanishlar yopildi. Xayr!")


async def run() -> None:
    """Asosiy sikl: yaratish -> ulash -> polling (24/7) -> toza yopish."""
    setup_logging()
    logger.info("Bot ishga tushmoqda… adminlar: %s", settings.ADMIN_IDS)

    bot = create_bot()
    dp = create_dispatcher()
    health = HealthServer(port=resolve_port())

    polling_task: asyncio.Task[None] | None = None
    health_task: asyncio.Task[None] | None = None

    try:
        # 1) Render portini ochamiz — health check 200 qaytarishi kerak
        await health.start()
        health_task = asyncio.create_task(health.serve(), name="health-server")

        # 2) DB ulanishi + webhook tozalash
        await on_startup(bot)

        # 3) Polling — health server bilan parallel ravishda ishlaydi
        polling_task = asyncio.create_task(
            dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
                handle_signals=True,
            ),
            name="polling",
        )

        # 4) Ikkalasi ham to'xtaguncha kutamiz (Render restart / SIGTERM)
        done, _pending = await asyncio.wait(
            {polling_task, health_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            exc = task.exception()
            if exc is not None:
                raise exc

    finally:
        # Polling va health tasklarini to'xtatamiz (DB yopilishidan OLDIN)
        for task in (polling_task, health_task):
            if task is not None and not task.done():
                task.cancel()

        await asyncio.gather(
            *(task for task in (polling_task, health_task) if task is not None),
            return_exceptions=True,
        )

        # Veb-server + DB pool + bot sessionni xavfsiz yopamiz
        await on_shutdown(bot, health)


def main() -> None:
    """Sinxron kirish nuqtasi: `python main.py`."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Dastur to'xtatildi (Ctrl+C).")
    # SystemExit (masalan, bazaga ulanib bo'lmasa) ataylab chiqadi —
    # Render loglarida xato ko'rinishi va xato kodi bilan tugashi uchun.


if __name__ == "__main__":
    main()
