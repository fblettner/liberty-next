from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.connectors.base import (
    QueryNotFoundError,
    StatementNotAllowedError,
    UnknownPoolError,
    WriteNotAllowedError,
)
from liberty.connectors.config import ParamDef, PoolConfig, QueryDef, SqlConnectorConfig
from liberty.connectors.db import PoolRegistry
from liberty.connectors.sql import SQLConnector


def test_pool_registry_unknown_and_empty_url() -> None:
    pools = PoolRegistry({"blank": PoolConfig(url="   ")})
    with pytest.raises(UnknownPoolError, match="empty url"):
        pools.engine("blank")
    with pytest.raises(UnknownPoolError, match="Unknown pool"):
        pools.engine("nope")


def _connector(pools: PoolRegistry, *queries: QueryDef, max_rows: int = 1000) -> SQLConnector:
    cfg = SqlConnectorConfig(type="sql", pool="test", max_rows=max_rows, queries=list(queries))
    return SQLConnector("db", cfg, pools)


@pytest_asyncio.fixture
async def pools(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT, status TEXT)"))
        await conn.execute(
            text("INSERT INTO item (id, name, status) VALUES (1,'a','on'),(2,'b','on'),(3,'c','off')")
        )
    registry = PoolRegistry()
    registry.register_engine("test", engine)
    yield registry
    await engine.dispose()


@pytest.mark.asyncio
async def test_select_returns_rows_and_columns(pools: PoolRegistry) -> None:
    conn = _connector(pools, QueryDef(name="all", sql="SELECT id, name FROM item ORDER BY id"))
    result = await conn.execute("all")
    assert result.statement_type == "SELECT"
    assert [c.name for c in result.columns] == ["id", "name"]
    assert result.rows == [
        {"id": 1, "name": "a"},
        {"id": 2, "name": "b"},
        {"id": 3, "name": "c"},
    ]
    assert result.rowcount == -1
    assert result.row_count == 3
    assert result.truncated is False
    assert "columns" in result.to_dict()


@pytest.mark.asyncio
async def test_named_params_and_missing_param_binds_null(pools: PoolRegistry) -> None:
    conn = _connector(
        pools,
        QueryDef(
            name="filtered",
            sql="SELECT id FROM item WHERE (:status IS NULL OR status = :status) ORDER BY id",
            params=[ParamDef(name="status")],
        ),
    )
    assert [r["id"] for r in (await conn.execute("filtered", {"status": "on"})).rows] == [1, 2]
    # No param supplied → :status bound to SQL NULL → filter is a no-op.
    assert [r["id"] for r in (await conn.execute("filtered")).rows] == [1, 2, 3]


@pytest.mark.asyncio
async def test_param_default_applied(pools: PoolRegistry) -> None:
    conn = _connector(
        pools,
        QueryDef(
            name="def",
            sql="SELECT id FROM item WHERE status = :status ORDER BY id",
            params=[ParamDef(name="status", default="off")],
        ),
    )
    assert [r["id"] for r in (await conn.execute("def")).rows] == [3]


@pytest.mark.asyncio
async def test_max_rows_truncation(pools: PoolRegistry) -> None:
    conn = _connector(pools, QueryDef(name="all", sql="SELECT id FROM item ORDER BY id"), max_rows=2)
    result = await conn.execute("all")
    assert len(result.rows) == 2
    assert result.truncated is True


@pytest.mark.asyncio
async def test_write_requires_writable(pools: PoolRegistry) -> None:
    conn = _connector(pools, QueryDef(name="ins", sql="INSERT INTO item (id, name) VALUES (9, 'z')"))
    with pytest.raises(WriteNotAllowedError):
        await conn.execute("ins")


@pytest.mark.asyncio
async def test_write_when_writable(pools: PoolRegistry) -> None:
    conn = _connector(
        pools,
        QueryDef(name="ins", sql="INSERT INTO item (id, name) VALUES (:id, :name)", writable=True),
        QueryDef(name="count", sql="SELECT COUNT(*) AS n FROM item"),
    )
    result = await conn.execute("ins", {"id": 9, "name": "z"})
    assert result.statement_type == "INSERT"
    assert result.rowcount == 1
    assert (await conn.execute("count")).rows == [{"n": 4}]


@pytest.mark.asyncio
async def test_disallowed_statement_rejected_before_connecting(pools: PoolRegistry) -> None:
    conn = _connector(pools, QueryDef(name="bad", sql="DROP TABLE item", writable=True))
    with pytest.raises(StatementNotAllowedError):
        await conn.execute("bad")
    # The table is still there.
    ok = _connector(pools, QueryDef(name="c", sql="SELECT COUNT(*) AS n FROM item"))
    assert (await ok.execute("c")).rows == [{"n": 3}]


@pytest.mark.asyncio
async def test_unknown_query(pools: PoolRegistry) -> None:
    conn = _connector(pools, QueryDef(name="x", sql="SELECT 1"))
    with pytest.raises(QueryNotFoundError):
        await conn.execute("nope")


def test_describe_lists_metadata(pools: PoolRegistry) -> None:
    conn = _connector(
        pools,
        QueryDef(name="all", sql="SELECT id FROM item WHERE status = :status", params=[ParamDef(name="status")]),
    )
    desc = conn.describe()
    assert desc["type"] == "sql"
    assert desc["pool"] == "test"
    q = desc["queries"][0]
    assert q["name"] == "all"
    assert q["statement_type"] == "SELECT"
    assert q["bind_params"] == ["status"]
