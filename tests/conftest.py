"""
tests/conftest.py
=================
Har bir test moduli o'zining SQLite bazasida ishlashi uchun umumiy fixture.

Test modullari import paytida `database.connection` global engine'ini
almashtiradi. Bir nechta modul yig'ilganda oxirgi modulning engine'i
qolib ketadi — bu fixture har bir test oldidan test modulining o'z
engine/session factory'sini qayta o'rnatadi.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.fixture(autouse=True)
def _use_module_database(request):
    module = request.module
    engine = getattr(module, "engine", None)
    conn_mod = getattr(module, "conn_mod", None)
    if engine is not None and conn_mod is not None:
        conn_mod._engine = engine
        conn_mod._session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    yield
