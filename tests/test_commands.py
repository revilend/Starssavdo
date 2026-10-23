"""
tests/test_commands.py
======================
Bot komandalar menyusi (`services/commands.py`) va doimiy reply klaviatura.

Tekshiradi:
 1. `BOT_COMMANDS` ro'yxati: noyob nomlar, to'liq tavsif (≤256 belgi);
 2. ro'yxatdagi har bir komandaning handleri HAQIQATAN mavjud
    (`handlers/` ichida `Command("...")` bilan) — ya'ni yozish satridagi
    menyu tugmasi bosilganda hech narsa ishlamay qolmaydi;
 3. `setup_bot_commands` `setMyCommands` + `setChatMenuButton` ni chaqiradi
    va Telegram xatolini startupga o'tkazmaydi;
 4. asosiy reply klaviatura `resize_keyboard=True` va `is_persistent=True`
    bilan quriladi (foydalanuvchi tugmalarni yashirgan bo'lsa ham menyu
    tugmasi orqali uni qayta ochish mumkin).

Ishga tushirish (repo root'dan):
    python -m pytest tests/test_commands.py -q
"""

import os
import re
import sys
from pathlib import Path
from typing import Any

os.environ.update(
    {
        "BOT_TOKEN": "123456789:AAHtesttesttesttesttesttesttesttesttest",
        "ADMIN_IDS": "111,222",
        "DATABASE_URL": "postgresql://user:pass@host:5432/postgres",
    }
)
sys.path.insert(0, ".")

from aiogram.exceptions import TelegramBadRequest  # noqa: E402
from aiogram.methods import SetMyCommands  # noqa: E402

from keyboards.reply import get_main_menu_kb  # noqa: E402
from services.commands import BOT_COMMANDS, COMMAND_NAMES, setup_bot_commands  # noqa: E402


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def test_commands_are_unique_and_documented() -> None:
    names = [command.command for command in BOT_COMMANDS]
    assert len(names) == len(set(names)), f"takroriy komanda: {names}"
    assert "start" in names
    assert "help" in names
    assert names == list(COMMAND_NAMES)

    for command in BOT_COMMANDS:
        assert command.description, f"{command.command} uchun tavsif yo'q"
        assert len(command.description) <= 256, "Telegram 256 belgi chegarasi"
        assert re.fullmatch(r"[a-z0-9_]{1,32}", command.command), command.command


def test_every_command_has_a_handler() -> None:
    """`setMyCommands` dagi har bir komanda uchun handler mavjudligini ta'minlaydi."""
    handlers_dir = Path("handlers")
    registered: set[str] = set()
    for path in handlers_dir.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        registered.update(re.findall(r'Command\(\s*"([A-Za-z0-9_]+)"\s*\)', source))

    missing = [name for name in COMMAND_NAMES if name not in registered]
    assert not missing, f"handler'siz komanda(lar): {missing}"


class _FakeBot:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.set_my_commands_calls: list[dict[str, Any]] = []
        self.set_chat_menu_button_calls: list[dict[str, Any]] = []

    async def set_my_commands(self, commands: Any = None, **kwargs: Any) -> bool:
        if self.fail:
            raise TelegramBadRequest(
                method=SetMyCommands(method="setMyCommands"),
                message="Bad Request: test",
            )
        self.set_my_commands_calls.append({"commands": commands, **kwargs})
        return True

    async def set_chat_menu_button(self, **kwargs: Any) -> bool:
        self.set_chat_menu_button_calls.append(kwargs)
        return True


def test_setup_bot_commands_calls_api() -> None:
    bot = _FakeBot()
    _run(setup_bot_commands(bot))

    assert len(bot.set_my_commands_calls) == 1
    commands = bot.set_my_commands_calls[0]["commands"]
    assert [c.command for c in commands] == list(COMMAND_NAMES)

    assert len(bot.set_chat_menu_button_calls) == 1
    assert bot.set_chat_menu_button_calls[0]["menu_button"].type == "commands"


def test_setup_bot_commands_swallows_api_error() -> None:
    """Telegram API xatosi startupni to'xtatmaydi (bot ishlashda davom etadi)."""
    bot = _FakeBot(fail=True)
    _run(setup_bot_commands(bot))  # xato tarqatilmaydi
    assert bot.set_my_commands_calls == []


def test_main_menu_keyboard_is_persistent() -> None:
    for is_admin in (False, True):
        kb = get_main_menu_kb(is_admin=is_admin)
        assert kb.resize_keyboard is True, is_admin
        assert kb.is_persistent is True, (
            "is_persistent=True eng muhim qadam: foydalanuvchi tugmalarni "
            "yashirib qo'yganda ham menyu doim qayta ko'rinadi"
        )
