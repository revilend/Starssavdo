"""
render_polling.py
=================
Render Web Service uchun polling rejimi + dummy veb-server.

Render deploy'i ochiq portni eshitadigan veb-serverni talab qiladi —
aks holda "In progress" bo'lib qotib qoladi. Shu yerda aiohttp serveri
`PORT` (yoki 10000) portida `0.0.0.0` da tinglaydi, `dp.start_polling()`
esa shu async loopda parallel ishlaydi.

Ishlatish (Render Start Command):
    python render_polling.py
Health Check Path:
    /health

Eslatmalar:
 - Webhook o'chirilgan bo'lishi kerak, aks holda start_polling conflict
   beradi — boshida `delete_webhook()` chaqiriladi.
 - Render Free planida xizmat ~15 daqiqa faoliyatsizlikdan keyin uxlaydi
   (bot bilan birga to'xtaydi). Doimiy ishlash uchun Standard plan.
 - `host="0.0.0.0"` majburiy; `PORT` ni qo'lda yozmang — Render o'zi beradi.
"""

import asyncio
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message

BOT_TOKEN = os.environ["BOT_TOKEN"]
# Render PORT ni o'zi beradi (10000+); lokal ishlash uchun 10000
PORT = int(os.environ.get("PORT", "10000"))

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer("Salom! Bot ishlayapti.")


# -------------------------------------------------------------
# Dummy veb-server (Render health check uchun)
# -------------------------------------------------------------
async def handle_root(request: web.Request) -> web.Response:
    """GET / — oddiy matnli javob."""
    return web.Response(text="Bot ishlayapti")


async def handle_health(request: web.Request) -> web.Response:
    """GET /health — Render Health Check uchun JSON."""
    return web.json_response({"status": "ok"})


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("aiogram").setLevel(logging.INFO)

    # 1) Webhook bo'lsa o'chiramiz — polling uchun majburiy
    await bot.delete_webhook(drop_pending_updates=True)

    # 2) Dummy veb-server (fonda ishlaydi)
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logging.info("Web server 0.0.0.0:%s da tinglayapti", PORT)

    try:
        # 3) Polling shu loopni band qilib turadi — server parallel ishlaydi
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
