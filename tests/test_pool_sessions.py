"""What a pool sets on each session it opens: PostgreSQL's query compilation (JIT) is turned off
unless the pool asks to keep the database's own setting."""
from __future__ import annotations

from typing import Any

import pytest

from liberty.connectors import db
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry


@pytest.fixture
def opened(monkeypatch) -> list[dict[str, Any]]:
    """The arguments each engine is created with — no database is reached."""
    seen: list[dict[str, Any]] = []

    def fake(url: Any, **kwargs: Any) -> object:
        seen.append({"url": str(url), **kwargs})
        return object()

    monkeypatch.setattr(db, "create_async_engine", fake)
    return seen


def test_a_postgresql_pool_turns_the_compilation_off(opened) -> None:
    PoolRegistry({"p": PoolConfig(url="postgresql+asyncpg://u:pw@host/db")}).engine("p")
    assert opened[0]["connect_args"] == {"server_settings": {"jit": "off"}}


def test_a_pool_may_keep_the_databases_own_setting(opened) -> None:
    PoolRegistry({"p": PoolConfig(url="postgresql+asyncpg://u:pw@host/db", jit=True)}).engine("p")
    assert "connect_args" not in opened[0]


def test_another_database_is_given_nothing(opened) -> None:
    PoolRegistry({"s": PoolConfig(url="sqlite+aiosqlite:///:memory:"), "o": PoolConfig(url="oracle+oracledb://u:pw@host/?service_name=x")}).engine("o")
    assert "connect_args" not in opened[0]


def test_the_setting_is_off_unless_said() -> None:
    assert PoolConfig(url="postgresql+asyncpg://u:pw@host/db").jit is False
