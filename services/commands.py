"""
services/commands.py
====================
Bot komandalarini o'rnatish — `setMyCommands` + `setChatMenuButton`.

Telegram'da quyidagilar mos keladi:

 - `set_my_commands` — chat yozish satridagi menyu tugmasida (yoki `/`
   bosilganda) ko'rinadigan komandalar ro'yxati. Bu ro'yxat faol bo'lsa
   Telegram o'sha tugmani (4 ta to'rtburchak / 🎛) **avtomatik** chiqaradi;
 - `set_chat_menu_button(menu_button=MenuButtonCommands)` — menyu tugmasi
   aniq `commands` bo'lishini kafolatadi (default'da ham shunday, lekin
   foydalanuvchi o'zgartirgan bo'lsa qayta tiklanadi).

Pastki klaviatura (`keyboards/reply.py`) esa `resize_keyboard=True` va
`is_persistent=True` bilan quriladi: foydalanuvchi tugmalarni yashirib
qo'yganda ham, menyu tugmasi (yoki komanda menyu) orqali uni istalgan
payt qayta ochish mumkin.

Xatoliklar startupni to'xtatmaydi — komandalar o'rnatilmasa bot baribir
ishlayda davom etadi (faqat logga yoziladi).
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonCommands

logger = logging.getLogger(__name__)

# Asosiy menyu buyruqlari (Telegram 1–100 ta, mobil ekran bir nechtasini ko'rsatadi)
BOT_COMMANDS: tuple[BotCommand, ...] = (
    BotCommand(command="start", description="Bosh menyu — botni qayta boshlash"),
    BotCommand(command="menu", description="Asosiy menyuni qayta ochish"),
    BotCommand(command="help", description="Qo'llanma — botdan foydalanish"),
    BotCommand(command="shop", description="Do'kon — Stars, Premium, NFT, giftlar"),
    BotCommand(command="balance", description="Balansim — balans va tarix"),
    BotCommand(command="topup", description="Balans to'ldirish — 2 usul"),
)

# Komanda nomlari (tekshiruvlar/testlar uchun qulay ko'rinish)
COMMAND_NAMES: tuple[str, ...] = tuple(command.command for command in BOT_COMMANDS)


async def setup_bot_commands(bot: Bot) -> None:
    """
    Komandalar ro'yxatini o'rnatadi va menyu tugmasini yoqadi.

    Ikkala so'rov ham xavfsiz: Telegram API xatosi startupni buzmaydi.
    """
    try:
        await bot.set_my_commands(
            list(BOT_COMMANDS), scope=BotCommandScopeDefault()
        )
        await bot.set_chat_menu_button(
            menu_button=MenuButtonCommands(type="commands")
        )
    except TelegramAPIError as exc:
        logger.warning("Komandalar/menyu tugmasi o'rnatilmadi: %s", exc)
        return
    except Exception:  # noqa: BLE001 — komanda o'rnatilishi bot ishini to'xtatmasin
        logger.exception("Komandalar o'rnatishda kutilmagan xato")
        return

    logger.info(
        "Bot komandalar o'rnatildi: %s (menyu tugmasi: commands)",
        ", ".join(COMMAND_NAMES),
    )
