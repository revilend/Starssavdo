"""
tests/test_render_deploy.py
==========================
Render deploy qismini tekshiradi:

 1. Health check serveri (`/` va `/health` -> 200 OK + JSON).
 2. Port aniqlash (`PORT` environment variable -> Render).
 3. Supabase/Render uchun DATABASE_URL konfiguratsiyasi:
      - `sslmode=require` -> asyncpg'ning `ssl="require"` argumentiga;
      - Transaction Pooler (port 6543 / pooler host) -> `statement_cache_size=0`
        VA `prepared_statement_cache_size=0` (ikki kesh ham o'chadi);
      - Pooler'da pool hajmi 2 + 13 = 15 (direct'da 10 + 20 = 30);
      - Supabase hostida TLS avtomatik yoqiladi.

Ishga tushirish (repo root'dan):
    python -m pytest tests/ -q
"""

import asyncio
import os
import socket
import sys

os.environ.update(
    {
        "BOT_TOKEN": "123456789:AAHtesttesttesttesttesttesttesttesttest",
        "ADMIN_IDS": "111,222",
        "DATABASE_URL": "postgresql://user:pass@host:5432/postgres",
    }
)
sys.path.insert(0, ".")

import aiohttp  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

import main as app_main  # noqa: E402
from config.settings import settings  # noqa: E402
from database.connection import (  # noqa: E402
    _build_database_config,
    _is_pooler,
    _pool_settings,
)


def _free_port() -> int:
    """Bo'sh TCP portni topadi (test uchun)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ------------------------------------------------------------------
# 1. Health check serveri
# ------------------------------------------------------------------
def test_health_payload() -> None:
    """Health javobi Render/UptimeRobot uchun to'g'ri tuzilgan."""
    assert app_main.HEALTH_PAYLOAD == {"status": "ok", "bot": "@nftyutbot"}
    assert app_main.HEALTH_BOT_USERNAME == "@nftyutbot"


def test_health_endpoints_return_200() -> None:
    """`/` va `/health` manzillari 200 OK + JSON qaytaradi."""

    async def _run():
        client = TestClient(TestServer(app_main.create_health_app()))
        await client.start_server()
        try:
            out = []
            for path in ("/", "/health"):
                resp = await client.get(path)
                out.append((path, resp.status, await resp.json()))
            return out
        finally:
            await client.close()

    for path, status, payload in asyncio.run(_run()):
        assert status == 200, f"{path} -> {status}"
        assert payload == {"status": "ok", "bot": "@nftyutbot"}, f"{path} -> {payload}"


def test_health_server_binds_real_port() -> None:
    """HealthServer real portni ochadi va undan javob beradi (Render shuni talab qiladi)."""

    async def _run():
        port = _free_port()
        server = app_main.HealthServer(port=port)
        await server.start()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/health") as resp:
                    return resp.status, await resp.json()
        finally:
            await server.stop()

    status, payload = asyncio.run(_run())
    assert status == 200
    assert payload == {"status": "ok", "bot": "@nftyutbot"}


def test_health_server_default_host_is_all_interfaces() -> None:
    """Render portni 0.0.0.0 da kutadi (tashqi so'rovlar kelishi uchun)."""
    server = app_main.HealthServer(port=8080)
    assert server.host == "0.0.0.0"


# ------------------------------------------------------------------
# 2. Port aniqlash
# ------------------------------------------------------------------
def test_resolve_port_uses_env(monkeypatch) -> None:
    """Render `PORT` environment variable'i ustunlik qiladi."""
    monkeypatch.setenv("PORT", "34567")
    assert app_main.resolve_port() == 34567


def test_resolve_port_falls_back_to_settings(monkeypatch) -> None:
    """PORT topilmasa sozlamalardagi qiymat ishlatiladi."""
    monkeypatch.delenv("PORT", raising=False)
    assert app_main.resolve_port() == settings.PORT


def test_resolve_port_handles_broken_value(monkeypatch) -> None:
    """Noto'g'ri PORT qiymati dasturni sindirmaydi."""
    monkeypatch.setenv("PORT", "not-a-port")
    assert app_main.resolve_port() == app_main.DEFAULT_PORT


# ------------------------------------------------------------------
# 3. Supabase / Render DATABASE_URL konfiguratsiyasi
# ------------------------------------------------------------------
def test_database_config_normalizes_scheme() -> None:
    """postgresql:// -> postgresql+asyncpg:// (asyncpg drayveri uchun)."""
    url, args = _build_database_config(
        "postgresql://user:pass@db.example.com:5432/postgres"
    )
    assert url.startswith("postgresql+asyncpg://")
    assert args == {}


def test_database_config_converts_sslmode() -> None:
    """`sslmode=require` asyncpg'ga to'g'ri kelmaydi -> `ssl="require"` ga o'tadi."""
    url, args = _build_database_config(
        "postgresql://user:pass@db.example.com:5432/postgres?sslmode=require"
    )
    assert "sslmode" not in url
    assert args["ssl"] == "require"


def test_database_config_pooler_disables_statement_cache() -> None:
    """Transaction Pooler (6543) uchun IKKI statement cache ham o'chiriladi."""
    url, args = _build_database_config(
        "postgresql://postgres.ref:pass@aws-0-eu-central-1.pooler.supabase.com"
        ":6543/postgres"
    )
    assert args["statement_cache_size"] == 0
    assert args["prepared_statement_cache_size"] == 0
    assert args["ssl"] == "require"


def test_database_config_pooler_host_detected_without_port() -> None:
    """Port ko'rsatilmasa ham pooler host bo'yicha aniqlanadi."""
    url, args = _build_database_config(
        "postgresql://postgres.ref:pass@aws-0-eu-central-1.pooler.supabase.com"
        "/postgres"
    )
    assert args["statement_cache_size"] == 0
    assert args["prepared_statement_cache_size"] == 0


def test_is_pooler() -> None:
    """Pooler aniqlash qoidalari."""
    assert _is_pooler("aws-0-eu-central-1.pooler.supabase.com", 6543)
    assert _is_pooler("aws-0-eu-central-1.pooler.supabase.com", 5432)
    assert _is_pooler("db.example.com", 6543)
    assert not _is_pooler("db.abcdefgh.supabase.co", 5432)
    assert not _is_pooler(None, 5432)


def test_pool_settings() -> None:
    """Pooler'da pool cheklanadi (2 + 13 = 15), direct'da 10 + 20 = 30."""
    assert _pool_settings(
        "postgresql://postgres.ref:pass@aws-0-eu-central-1.pooler.supabase.com"
        ":6543/postgres"
    ) == {"pool_size": 2, "max_overflow": 13}
    assert _pool_settings(
        "postgresql://postgres.ref:pass@db.abcdefgh.supabase.co:5432/postgres"
    ) == {"pool_size": 10, "max_overflow": 20}


def test_database_config_supabase_direct_requires_ssl() -> None:
    """Supabase'ning to'g'ridan-to'g'ri ulanishida TLS majburiy."""
    url, args = _build_database_config(
        "postgresql://postgres.ref:pass@db.abcdefgh.supabase.co:5432/postgres"
    )
    assert args["ssl"] == "require"
    assert "statement_cache_size" not in args


def test_database_config_db_ssl_override(monkeypatch) -> None:
    """DB_SSL=disable SSL ni o'chiradi (lokal Postgres uchun)."""
    monkeypatch.setattr(settings, "DB_SSL", "disable")
    url, args = _build_database_config(
        "postgresql://postgres.ref:pass@db.abcdefgh.supabase.co:5432/postgres"
    )
    assert "ssl" not in args


def test_database_config_rejects_bad_url() -> None:
    """Noto'g'ri scheme erta aniqlanadi."""
    try:
        _build_database_config("mysql://user:pass@host:3306/db")
    except ValueError:
        return
    raise AssertionError("ValueError kutilgan edi")


def test_connect_args_reach_asyncpg_safely(monkeypatch) -> None:
    """
    Eng muhim regressiya testi.

    `?sslmode=require` SQLAlchemy asyncpg dialekti orqali
    `asyncpg.connect(sslmode=...)` ga o'tadi va asyncpg bu kwarg'ni
    qabul qilmaydi (TypeError). Konfiguratsiyadan keyin engine
    ulanishga qachon real asyncpg chaqiriladi va u chaqiruvga
    `ssl`/`statement_cache_size` keladi, `sslmode` KELMAYDI.
    """
    import asyncpg

    captured: dict = {}

    async def fake_connect(*args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("test: ulanish sun'batga to'xtatildi")

    monkeypatch.setattr(asyncpg, "connect", fake_connect)

    url, args = _build_database_config(
        "postgresql://postgres.ref:pass@aws-0-eu-central-1.pooler.supabase.com"
        ":6543/postgres?sslmode=require"
    )

    async def _run():
        engine = create_async_engine(url, connect_args=args)
        try:
            await engine.connect()
        except Exception:
            pass  # fake_connect ataylab to'xtatdi — biz parametrlarni tekshiramiz
        finally:
            await engine.dispose()

    asyncio.run(_run())

    assert "sslmode" not in captured, "asyncpg 'sslmode' kwarg'ini qabul qilmaydi!"
    assert captured.get("ssl") == "require"
    assert captured.get("statement_cache_size") == 0
    # `prepared_statement_cache_size` SQLAlchemy DBAPI qatlamida "yutiladi"
    # (asyncpg.connect ga uzatilsa TypeError bo'lardi)
    assert "prepared_statement_cache_size" not in captured
