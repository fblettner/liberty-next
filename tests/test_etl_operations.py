"""Tests for :mod:`liberty.etl.operations` — the single-statement ETL helpers.

Uses an in-memory SQLite registry as the target connector so the tests are
fast + self-contained; the helpers themselves are dialect-agnostic, only the
TRUNCATE→DELETE fallback path is sqlite-specific.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from liberty.connectors.config import ConnectorsFile, PoolConfig, SqlConnectorConfig
from liberty.connectors.registry import ConnectorRegistry
from liberty.etl import (
    delete_rows,
    insert_audit_record,
    run_query,
    snapshot_rows,
    truncate_table,
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def registry(tmp_path):
    cfg = ConnectorsFile(
        pools={"target": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 't.db'}")},
        connectors={"tgt": SqlConnectorConfig(type="sql", pool="target", queries=[])},
    )
    reg = ConnectorRegistry(cfg)
    eng = reg.pools.engine("target")
    async with eng.begin() as conn:
        # Source table + matching history table — the typical snapshot pair.
        await conn.execute(text("""
            CREATE TABLE security_users (
                usr_apps_id INTEGER, usr_ukid INTEGER, usr_login TEXT
            )
        """))
        await conn.execute(text("""
            CREATE TABLE "security_users$" (
                usr_apps_id INTEGER, usr_ukid INTEGER, usr_login TEXT
            )
        """))
        await conn.execute(text("""
            CREATE TABLE security_audit (
                aud_apps_id INTEGER, aud_table TEXT, aud_action TEXT, aud_date DATE
            )
        """))
    yield reg
    await reg.aclose()


async def _seed(reg, rows):
    eng = reg.pools.engine("target")
    async with eng.begin() as conn:
        for apps_id, ukid, login in rows:
            await conn.execute(
                text("INSERT INTO security_users VALUES (:a, :u, :l)"),
                {"a": apps_id, "u": ukid, "l": login},
            )


async def _read(reg, table: str) -> list[tuple]:
    eng = reg.pools.engine("target")
    async with eng.connect() as conn:
        result = await conn.execute(text(f"SELECT * FROM {table} ORDER BY 1, 2"))
        return [tuple(r) for r in result.fetchall()]


# --------------------------------------------------------------------------- #
# snapshot_rows
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_snapshot_rows_with_filter(registry) -> None:
    """Snapshot the apps_id=10 slice into the history table; rows for other
    apps_ids stay in the live table only."""
    await _seed(registry, [(10, 1, "alice"), (10, 2, "bob"), (20, 1, "carol")])
    n = await snapshot_rows(
        connectors=registry, target_connector="target",
        source_table="security_users", history_table='"security_users$"',
        where="usr_apps_id = :apps_id", params={"apps_id": 10},
    )
    assert n == 2
    assert await _read(registry, '"security_users$"') == [(10, 1, "alice"), (10, 2, "bob")]
    # source untouched
    assert len(await _read(registry, "security_users")) == 3


@pytest.mark.asyncio
async def test_snapshot_rows_no_filter_copies_everything(registry) -> None:
    await _seed(registry, [(10, 1, "x"), (20, 1, "y")])
    n = await snapshot_rows(
        connectors=registry, target_connector="target",
        source_table="security_users", history_table='"security_users$"',
    )
    assert n == 2
    assert len(await _read(registry, '"security_users$"')) == 2


# --------------------------------------------------------------------------- #
# delete_rows
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_delete_rows_with_filter(registry) -> None:
    await _seed(registry, [(10, 1, "a"), (10, 2, "b"), (20, 1, "c")])
    n = await delete_rows(
        connectors=registry, target_connector="target", table="security_users",
        where="usr_apps_id = :apps_id", params={"apps_id": 10},
    )
    assert n == 2
    assert await _read(registry, "security_users") == [(20, 1, "c")]


@pytest.mark.asyncio
async def test_delete_rows_no_filter_clears_table(registry) -> None:
    await _seed(registry, [(10, 1, "a"), (20, 1, "b")])
    n = await delete_rows(
        connectors=registry, target_connector="target", table="security_users",
    )
    assert n == 2
    assert await _read(registry, "security_users") == []


# --------------------------------------------------------------------------- #
# truncate_table — SQLite fallback to DELETE
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_truncate_table_falls_back_to_delete_on_sqlite(registry) -> None:
    """SQLite has no TRUNCATE; the helper detects the dialect and runs DELETE
    so tests (which use SQLite) and prod (Postgres) behave the same."""
    await _seed(registry, [(10, 1, "a"), (20, 1, "b")])
    await truncate_table(
        connectors=registry, target_connector="target", table="security_users",
    )
    assert await _read(registry, "security_users") == []


# --------------------------------------------------------------------------- #
# insert_audit_record
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_insert_audit_record(registry) -> None:
    await insert_audit_record(
        connectors=registry, target_connector="target",
        audit_table="security_audit", target_schema=None,
        target_table="SECURITY_USERS", apps_id=10,
    )
    rows = await _read(registry, "security_audit")
    assert len(rows) == 1
    # (apps_id, target_table, action, date)
    assert rows[0][0] == 10
    assert rows[0][1] == "SECURITY_USERS"
    assert rows[0][2] == "ETL"
    # date column — non-null is enough; format varies by driver.
    assert rows[0][3] is not None


@pytest.mark.asyncio
async def test_insert_audit_record_custom_action(registry) -> None:
    await insert_audit_record(
        connectors=registry, target_connector="target",
        audit_table="security_audit", target_schema=None,
        target_table="SECURITY_USERS", apps_id=10, action="REFRESH",
    )
    rows = await _read(registry, "security_audit")
    assert rows[0][2] == "REFRESH"


# --------------------------------------------------------------------------- #
# run_query — escape hatch
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_query_returns_rowcount(registry) -> None:
    await _seed(registry, [(10, 1, "a"), (10, 2, "b")])
    n = await run_query(
        connectors=registry, connector="target",
        sql="UPDATE security_users SET usr_login = :v WHERE usr_apps_id = :a",
        params={"v": "renamed", "a": 10},
    )
    assert n == 2
    rows = await _read(registry, "security_users")
    assert all(r[2] == "renamed" for r in rows)
