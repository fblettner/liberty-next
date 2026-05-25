"""Tests for :func:`liberty.etl.copy_query_to_table` — the source-to-target
streaming copy used by python steps with hand-written SELECTs.

Uses two SQLite databases (one source, one target) so the multi-engine
streaming pattern is exercised end-to-end without needing real Postgres /
Oracle in the test loop.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from liberty.connectors.config import ConnectorsFile, PoolConfig, SqlConnectorConfig
from liberty.connectors.registry import ConnectorRegistry
from liberty.etl import copy_query_to_table


@pytest_asyncio.fixture
async def registry(tmp_path):
    cfg = ConnectorsFile(
        pools={
            "source": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 's.db'}"),
            "target": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 't.db'}"),
        },
        connectors={
            "src": SqlConnectorConfig(type="sql", pool="source", queries=[]),
            "tgt": SqlConnectorConfig(type="sql", pool="target", queries=[]),
        },
    )
    reg = ConnectorRegistry(cfg)
    # Source mimics a JDE-style uppercase schema; target is the lowercase nomasx1.
    src_eng = reg.pools.engine("source")
    async with src_eng.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE F0092 (
                USR_APPS_ID INTEGER, USR_UKID INTEGER, USR_LOGIN TEXT
            )
        """))
    tgt_eng = reg.pools.engine("target")
    async with tgt_eng.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE security_users (
                usr_apps_id INTEGER, usr_ukid INTEGER, usr_login TEXT
            )
        """))
    yield reg
    await reg.aclose()


async def _seed_source(reg, rows):
    eng = reg.pools.engine("source")
    async with eng.begin() as conn:
        for a, u, l in rows:
            await conn.execute(
                text("INSERT INTO F0092 VALUES (:a, :u, :l)"), {"a": a, "u": u, "l": l},
            )


async def _read_target(reg) -> list[tuple]:
    eng = reg.pools.engine("target")
    async with eng.connect() as conn:
        result = await conn.execute(text("SELECT * FROM security_users ORDER BY 1, 2"))
        return [tuple(r) for r in result.fetchall()]


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_copy_query_lowercases_columns_by_default(registry) -> None:
    """Source columns are uppercase (USR_APPS_ID …); target is lowercase. The
    default mapping lowercases source names — matches the nomasx1/nomajde
    convention without the caller having to spell out a column list."""
    await _seed_source(registry, [(10, 1, "alice"), (10, 2, "bob"), (20, 1, "carol")])
    n = await copy_query_to_table(
        connectors=registry, source_connector="source",
        source_sql="SELECT * FROM F0092 WHERE USR_APPS_ID = :apps_id",
        source_params={"apps_id": 10},
        target_connector="target", target_table="security_users",
    )
    assert n == 2
    assert await _read_target(registry) == [(10, 1, "alice"), (10, 2, "bob")]


@pytest.mark.asyncio
async def test_copy_query_with_explicit_target_columns(registry) -> None:
    """When source ↔ target columns aren't a lowercase mapping, pass the list."""
    await _seed_source(registry, [(10, 1, "x")])
    n = await copy_query_to_table(
        connectors=registry, source_connector="source",
        source_sql="SELECT USR_APPS_ID, USR_UKID, USR_LOGIN FROM F0092",
        target_connector="target", target_table="security_users",
        target_columns=["usr_apps_id", "usr_ukid", "usr_login"],
    )
    assert n == 1
    assert await _read_target(registry) == [(10, 1, "x")]


@pytest.mark.asyncio
async def test_copy_query_batches_split_into_multiple_inserts(registry) -> None:
    """7 rows with batch_size=3 → multiple INSERTs all land in the target."""
    await _seed_source(registry, [(10, i, f"u{i}") for i in range(1, 8)])
    n = await copy_query_to_table(
        connectors=registry, source_connector="source",
        source_sql="SELECT * FROM F0092",
        target_connector="target", target_table="security_users",
        batch_size=3,
    )
    assert n == 7
    rows = await _read_target(registry)
    assert [r[1] for r in rows] == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.asyncio
async def test_copy_query_empty_source_is_zero_rows(registry) -> None:
    n = await copy_query_to_table(
        connectors=registry, source_connector="source",
        source_sql="SELECT * FROM F0092 WHERE 1 = 0",
        target_connector="target", target_table="security_users",
    )
    assert n == 0
    assert await _read_target(registry) == []


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_copy_query_target_columns_length_mismatch_raises(registry) -> None:
    """If the caller hands us a target_columns list whose length doesn't match
    the source SELECT, fail loudly on the first batch — silent column drops
    would corrupt the target."""
    await _seed_source(registry, [(10, 1, "x")])
    with pytest.raises(ValueError) as exc:
        await copy_query_to_table(
            connectors=registry, source_connector="source",
            source_sql="SELECT * FROM F0092",  # 3 columns
            target_connector="target", target_table="security_users",
            target_columns=["usr_apps_id"],    # 1 column — mismatch
        )
    assert "length mismatch" in str(exc.value)
