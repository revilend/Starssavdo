"""
tests/test_render_deploy.py
===========================
Render deploy qismini tekshiradi:

 1. Health check endpointlari (`/` va `/health` -> 200 OK + JSON).
 2. Webhook endpointi (`/webhook`):
      - `X-Telegram-Bot-Api-Secret-Token` tekshiruvi (403);
      - buzilgan JSON / noto'g'ri update (400);
      - to'g'ri update Dispatcher'ga uzatiladi (200).
 3. `lifespan`: baza tekshiruvi, `set_webhook` (URL + secret + allowed_updates),
    `BOT_MODE=polling` rejimi va shutdown (delete_webhook / DB / session).
 4. Sozlamalar validatsiyasi (WEBHOOK_URL, BOT_MODE, WEBHOOK_SECRET).
 5. Port aniqlash (`PORT` environment variable -> Render).
 6. `render.yaml` start command'i (uvicorn) va health check path'i.
 7. Supabase/Render uchun DATABASE_URL konfiguratsiyasi:
      - `sslmode=require` -> asyncpg'ning `ssl="require"` argumentiga;
      - Transaction Pooler (port 6543) -> `statement_cache_size=0`;
      - Supabase hostida TLS avtomatik yoqiladi.

Ishga tushirish (repo root'dan):
    python -m pytest tests/test_render_deploy.py -q
"""

import asyncio
import json
import os
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

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

import main as app_main  # noqa: E402
from config.settings import Settings, settings  # noqa: E402
from database.connection import _build_database_config  # noqa: E402


# ------------------------------------------------------------------
# ASGI yordamchilari (qo'shimcha dependency'siz — FastAPI'ni to'g'ridan
# chaqiradi; lifespan ishga tushmaydi, faqat routing/endpoint sinaladi)
# ------------------------------------------------------------------
async def _asgi_request(
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Ilovaga bitta HTTP so'rov yuboradi va (status, JSON) qaytaradi."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            *(headers or []),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }

    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app_main.app(scope, receive, send)

    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    payload = b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )
    return status, (json.loads(payload) if payload else {})


def _request(method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
    return asyncio.run(_asgi_request(method, path, **kwargs))


def _post_webhook(
    body: bytes, secret: str | None = None
) -> tuple[int, dict[str, Any]]:
    headers = (
        [(b"x-telegram-bot-api-secret-token", secret.encode())] if secret else []
    )
    return _request("POST", settings.WEBHOOK_PATH, body=body, headers=headers)


# ------------------------------------------------------------------
# Runtime mock'lari (baza va Telegram tarmog'isiz)
# ------------------------------------------------------------------
def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch, *, db_ok: bool = True
) -> dict[str, Any]:
    """DB va Telegram chaqiruvlarini kuzatuvchi mock'lar bilan almashtiradi."""
    calls: dict[str, Any] = {
        "set_webhook": [],
        "delete_webhook": 0,
        "session_closed": 0,
        "close_connection": 0,
        "start_polling": [],
    }

    async def fake_test_connection() -> bool:
        return db_ok

    async def fake_close_connection() -> None:
        calls["close_connection"] += 1

    async def fake_set_webhook(**kwargs: Any) -> bool:
        calls["set_webhook"].append(kwargs)
        return True

    async def fake_delete_webhook(**kwargs: Any) -> bool:
        calls["delete_webhook"] += 1
        return True

    async def fake_session_close() -> None:
        calls["session_closed"] += 1

    def fake_start_polling(*args: Any, **kwargs: Any) -> Any:
        # `dp.start_polling(...)` create_task ichida chaqiriladi, shuning uchun
        # argumentlar korutin ishga tushishini kutmasdan yozib olinadi.
        calls["start_polling"].append(kwargs)

        async def _run_forever() -> None:
            await asyncio.Event().wait()  # shutdown'da bekor qilinadi

        return _run_forever()

    async def fake_noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(app_main, "test_connection", fake_test_connection)
    monkeypatch.setattr(app_main, "close_connection", fake_close_connection)
    monkeypatch.setattr(app_main, "ensure_schema", fake_noop)
    monkeypatch.setattr(app_main, "load_button_overrides", fake_noop)
    monkeypatch.setattr(app_main, "load_custom_emojis", fake_noop)
    monkeypatch.setattr(app_main.bot, "set_webhook", fake_set_webhook)
    monkeypatch.setattr(app_main.bot, "delete_webhook", fake_delete_webhook)
    monkeypatch.setattr(app_main.bot.session, "close", fake_session_close)
    monkeypatch.setattr(app_main.dp, "start_polling", fake_start_polling)
    return calls


def _run_lifespan() -> None:
    async def _run() -> None:
        async with app_main.app.router.lifespan_context(app_main.app):
            pass

    asyncio.run(_run())


# ------------------------------------------------------------------
# 1. Health check
# ------------------------------------------------------------------
def test_health_payload() -> None:
    """Health javobi Render/UptimeRobot uchun to'g'ri tuzilgan."""
    assert app_main.HEALTH_PAYLOAD == {"status": "ok", "bot": "@nftyutbot"}
    assert app_main.HEALTH_BOT_USERNAME == "@nftyutbot"


def test_health_endpoints_return_200() -> None:
    """`/` va `/health` manzillari 200 OK + JSON qaytaradi."""
    for path in ("/", "/health"):
        status, payload = _request("GET", path)
        assert status == 200, f"{path} -> {status}"
        assert payload == {"status": "ok", "bot": "@nftyutbot"}, f"{path} -> {payload}"


# ------------------------------------------------------------------
# 2. Webhook endpointi
# ------------------------------------------------------------------
def test_webhook_secret_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Secret o'rnatilgan bo'lsa, sarlavhasiz/noto'g'ri so'rov 403 oladi."""
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "test-secret_123")
    body = b'{"update_id": 1}'

    status, _ = _post_webhook(body)
    assert status == 403, "secret sarlavhasi yo'q bo'lsa 403 kutilgan"

    status, _ = _post_webhook(body, secret="wrong-secret")
    assert status == 403, "noto'g'ri secret 403 kutilgan"


def test_webhook_feeds_update_to_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """To'g'ri secret bilan kelgan update Dispatcher'ga uzatiladi (200)."""
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "test-secret_123")

    # `poll` update: foydalanuvchi (event_from_user) yo'q, handler ham yo'q —
    # middlewarelar va baza tegilmaydi, lekin Dispatcher uni qabul qiladi.
    poll_update = json.dumps(
        {
            "update_id": 42,
            "poll": {
                "id": "1",
                "question": "test",
                "options": [
                    {"text": "a", "voter_count": 0, "persistent_id": "opt-1"}
                ],
                "total_voter_count": 0,
                "is_closed": False,
                "is_anonymous": True,
                "type": "regular",
                "allows_multiple_answers": False,
                "allows_revoting": False,
                "members_only": False,
            },
        }
    ).encode()
    status, payload = _post_webhook(poll_update, secret="test-secret_123")
    assert status == 200, f"status={status}"
    assert payload == {"ok": True}


def test_webhook_rejects_broken_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Buzilgan JSON 400 qaytaradi (Telegram qayta urinmaydi)."""
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "test-secret_123")
    status, _ = _post_webhook(b"{not-json", secret="test-secret_123")
    assert status == 400


def test_webhook_rejects_invalid_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram Update sxemasiga mos kelmagan tana 400 qaytaradi."""
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "test-secret_123")
    status, _ = _post_webhook(
        b'{"update_id": "not-a-number"}', secret="test-secret_123"
    )
    assert status == 400


# ------------------------------------------------------------------
# 3. Lifespan (startup / shutdown)
# ------------------------------------------------------------------
def test_lifespan_registers_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup'da webhook to'g'ri URL, secret va allowed_updates bilan o'rnatiladi."""
    monkeypatch.setattr(settings, "BOT_MODE", "webhook")
    monkeypatch.setattr(settings, "WEBHOOK_URL", "https://nftyutbot.onrender.com")
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "test-secret_123")
    calls = _patch_runtime(monkeypatch)

    _run_lifespan()

    assert len(calls["set_webhook"]) == 1, "webhook bir marta o'rnatilishi kerak"
    kwargs = calls["set_webhook"][0]
    assert kwargs["url"] == "https://nftyutbot.onrender.com/webhook"
    assert kwargs["drop_pending_updates"] is True
    assert kwargs["secret_token"] == "test-secret_123"
    assert "message" in kwargs["allowed_updates"]
    assert "callback_query" in kwargs["allowed_updates"]

    # Shutdown: webhook o'chiriladi, DB pool va bot session yopiladi
    assert calls["delete_webhook"] == 1
    assert calls["close_connection"] == 1
    assert calls["session_closed"] == 1


def test_lifespan_requires_webhook_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """`WEBHOOK_URL` bo'sh bo'lsa startup aniq xato bilan to'xtaydi."""
    monkeypatch.setattr(settings, "BOT_MODE", "webhook")
    monkeypatch.setattr(settings, "WEBHOOK_URL", "")
    _patch_runtime(monkeypatch)

    with pytest.raises(RuntimeError, match="WEBHOOK_URL"):
        _run_lifespan()


def test_lifespan_fails_when_database_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Baza bo'lmasa ilova ishga tushmaydi (Render deploy fail bo'ladi)."""
    monkeypatch.setattr(settings, "BOT_MODE", "webhook")
    monkeypatch.setattr(settings, "WEBHOOK_URL", "https://nftyutbot.onrender.com")
    calls = _patch_runtime(monkeypatch, db_ok=False)

    with pytest.raises(RuntimeError, match="PostgreSQL"):
        _run_lifespan()

    # Startup muvaffaqiyatsiz bo'lsa ham resurslar yopiladi
    assert calls["close_connection"] == 1
    assert calls["session_closed"] == 1


def test_lifespan_polling_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """`BOT_MODE=polling` da webhook o'rnatilmaydi, polling fon vazifasi boshlanadi."""
    monkeypatch.setattr(settings, "BOT_MODE", "polling")
    calls = _patch_runtime(monkeypatch)

    _run_lifespan()

    assert calls["set_webhook"] == []
    assert len(calls["start_polling"]) == 1
    kwargs = calls["start_polling"][0]
    assert kwargs["handle_signals"] is False
    assert kwargs["close_bot_session"] is False
    # Polling rejimida ham resurslar yopiladi
    assert calls["close_connection"] == 1
    assert calls["session_closed"] == 1


def test_webhook_url_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEBHOOK_URL oxiridagi `/` qo'shilib ketmaydi."""
    monkeypatch.setattr(settings, "WEBHOOK_URL", "https://nftyutbot.onrender.com/")
    assert (
        app_main.webhook_endpoint_url()
        == "https://nftyutbot.onrender.com/webhook"
    )


# ------------------------------------------------------------------
# 4. Konfiguratsiya validatsiyasi (webhook sozlamalari)
# ------------------------------------------------------------------
def _make_settings(**overrides: Any) -> Any:
    """Test uchun `Settings` nusxasi (majburiy maydonlar bilan)."""
    base: dict[str, Any] = {
        "BOT_TOKEN": "123:TEST",
        "ADMIN_IDS": "111,222",
        "DATABASE_URL": "postgresql://user:pass@host:5432/postgres",
    }
    base.update(overrides)
    return Settings(**base)


def test_settings_normalize_webhook_url() -> None:
    """WEBHOOK_URL oxiridagi `/` yuklashda olib tashlanadi."""
    assert (
        _make_settings(WEBHOOK_URL="https://nftyutbot.onrender.com/").WEBHOOK_URL
        == "https://nftyutbot.onrender.com"
    )


def test_settings_validate_bot_mode() -> None:
    """BOT_MODE faqat webhook/polling bo'lishi mumkin."""
    from pydantic import ValidationError

    assert _make_settings(BOT_MODE="Polling").BOT_MODE == "polling"
    with pytest.raises(ValidationError):
        _make_settings(BOT_MODE="worker")


def test_settings_validate_webhook_secret() -> None:
    """Telegram secret_token formati tekshiriladi (A-Za-z0-9_-)."""
    from pydantic import ValidationError

    assert _make_settings(WEBHOOK_SECRET="").WEBHOOK_SECRET == ""
    assert _make_settings(WEBHOOK_SECRET="abc-123_XYZ").WEBHOOK_SECRET == "abc-123_XYZ"
    with pytest.raises(ValidationError):
        _make_settings(WEBHOOK_SECRET="bad secret!")


def test_main_entrypoint_runs_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`python main.py` uvicorn'ni 0.0.0.0:$PORT da ishga tushirishi kerak."""
    captured: dict[str, Any] = {}

    def fake_uvicorn_run(app: Any, **kwargs: Any) -> None:
        captured.update(kwargs)
        captured["app"] = app

    monkeypatch.setattr(app_main.uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setenv("PORT", "8098")

    app_main.main()

    assert captured["app"] is app_main.app
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8098


# ------------------------------------------------------------------
# 5. Port aniqlash
# ------------------------------------------------------------------
def test_resolve_port_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render `PORT` environment variable'i ustunlik qiladi."""
    monkeypatch.setenv("PORT", "34567")
    assert app_main.resolve_port() == 34567


def test_resolve_port_falls_back_to_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """PORT topilmasa sozlamalardagi qiymat ishlatiladi."""
    monkeypatch.delenv("PORT", raising=False)
    assert app_main.resolve_port() == settings.PORT


def test_resolve_port_handles_broken_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Noto'g'ri PORT qiymati dasturni sindirmaydi."""
    monkeypatch.setenv("PORT", "not-a-port")
    assert app_main.resolve_port() == app_main.DEFAULT_PORT


# ------------------------------------------------------------------
# 6. render.yaml (Blueprint)
# ------------------------------------------------------------------
def test_render_blueprint_uses_uvicorn_webhook() -> None:
    """render.yaml FastAPI ilovasini uvicorn bilan ishga tushirishi kerak."""
    text = Path("render.yaml").read_text(encoding="utf-8")

    assert "uvicorn main:app --host 0.0.0.0 --port $PORT" in text
    assert "healthCheckPath: /health" in text
    assert "pip install -r requirements.txt" in text
    for key in ("BOT_TOKEN", "ADMIN_IDS", "DATABASE_URL", "WEBHOOK_URL"):
        assert f"key: {key}" in text, f"{key} render.yaml da yo'q"
    # Render PORT'ni o'zi beradi — blueprint'da qat'iy yozilmasligi kerak
    assert "key: PORT" not in text


# ------------------------------------------------------------------
# 7. Supabase / Render DATABASE_URL konfiguratsiyasi
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
    """Transaction Pooler (6543) uchun statement cache o'chiriladi."""
    url, args = _build_database_config(
        "postgresql://postgres.ref:pass@aws-0-eu-central-1.pooler.supabase.com"
        ":6543/postgres"
    )
    assert args["statement_cache_size"] == 0
    assert args["ssl"] == "require"


def test_database_config_supabase_direct_requires_ssl() -> None:
    """Supabase'ning to'g'ridan-to'g'ri ulanishida TLS majburiy."""
    url, args = _build_database_config(
        "postgresql://postgres.ref:pass@db.abcdefgh.supabase.co:5432/postgres"
    )
    assert args["ssl"] == "require"
    assert "statement_cache_size" not in args


def test_database_config_db_ssl_override(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_connect_args_reach_asyncpg_safely(monkeypatch: pytest.MonkeyPatch) -> None:
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

    async def _run() -> None:
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
