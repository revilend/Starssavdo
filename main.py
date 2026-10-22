"""
main.py
=======
Telegram Stars, NFT, Premium, Mayda Giftlar va Referal boti (@nftyutbot)
— asosiy kirish nuqtasi (Polling rejimida 24/7 ishga tushirish).

Tartib:
 1. Logging sozlanadi (INFO darajasi).
 2. Bot + Dispatcher yaratiladi:
      - `DefaultBotProperties(parse_mode=ParseMode.HTML)` — barcha
        xabarlar standart HTML formatida (handlerlarda qayta yozish shart emas).
      - `MemoryStorage` — FSM holatlari (kanal qo'shish, rassilka,
        sozlamalar, balans, payout).
 3. Database ulanishi tekshiriladi (`test_connection()`) — muvaffaqiyatsiz
    bo'lsa dastur ishga tushmaydi (xatoni erta aniqlanadi).
 4. Middlewarelar (outer, tartib muhim):
      1) ThrottlingMiddleware — spam/flood himoyasi (adminlar bundan mustasno)
      2) SubscriptionMiddleware — majburiy homiy kanallar tekshiruvi
 5. Routerlar tartib bilan ulanadi:
      admin routers -> subscription router -> start & referral routers
      -> shop & payout routers
    (admin routerlar birinchi — ularning callback'lari user handlerlarga
    tushib qolmasligi uchun; subscription esa barcha user hodisalaridan
    oldin o'tadi).
 6. `dp.start_polling(bot)` — 24/7 polling; SIGINT/SIGSTOP (Ctrl+C)
    toza yopilishni ta'minlaydi (pool dispose + bot session).

Ishga tushirish:
    python main.py
"""

from __future__ import annotations

import asyncio
import logging
import sys

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


async def on_startup(bot: Bot) -> None:
    """Ishga tushishdagi tekshiruvlar: DB ulanishi va webhook tozalash."""
    # 1) Database ulanishini tekshirish
    if not await test_connection():
        logger.error("PostgreSQL bazasiga ulanib bo'lmadi!")
        raise SystemExit("Bazaga ulanib bo'lmadi — .env ni tekshiring.")

    # 2) Eski webhook va kutayotgan yangilanishlarni tozalash (polling uchun)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook tozalandi — polling rejimi tayyor.")


async def on_shutdown(bot: Bot) -> None:
    """Toza yopish: DB poolni yopish va bot sessionni yopish."""
    logger.info("Bot yopilmoqda…")
    await close_connection()
    await bot.session.close()
    logger.info("Barcha ulanishlar yopildi. Xayr!")


async def run() -> None:
    """Asosiy sikl: yaratish -> ulash -> polling (24/7) -> toza yopish."""
    setup_logging()
    logger.info("Bot ishga tushmoqda… adminlar: %s", settings.ADMIN_IDS)

    bot = create_bot()
    dp = create_dispatcher()

    try:
        await on_startup(bot)

        # 24/7 polling — ishlatilgan update turlari avtomatik aniqlanadi
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            handle_signals=True,
        )
    finally:
        await on_shutdown(bot)


def main() -> None:
    """Sinxron kirish nuqtasi: `python main.py`."""
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Dastur to'xtatildi.")


if __name__ == "__main__":
    main()
