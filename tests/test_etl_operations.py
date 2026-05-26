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
        # PK on the history table mirrors the real nomasx1 schema
        # (security_users$ has PK (usr_apps_id, usr_id, usr_ukid)) — required
        # for the if_not_exists snapshot test to exercise the conflict path.
        await conn.execute(text("""
            CREATE TABLE "security_users$" (
                usr_apps_id INTEGER, usr_ukid INTEGER, usr_login TEXT,
                PRIMARY KEY (usr_apps_id, usr_ukid)
            )
        """))
        # collect_audit — new shape (replaces v1's per-module SECURITY_AUDIT /
        # DB_AUDIT / … and the earlier nomasx1b consolidated security_audit).
        # cla_audit_id is a surrogate PK; cla_module + cla_target identify what
        # was refreshed; cla_run_id links back to the nomaflow job_runs row.
        await conn.execute(text("""
            CREATE TABLE collect_audit (
                cla_audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                cla_apps_id  INTEGER NOT NULL,
                cla_module   TEXT NOT NULL,
                cla_target   TEXT NOT NULL,
                cla_action   TEXT NOT NULL,
                cla_refresh  TIMESTAMP NOT NULL,
                cla_run_id   TEXT
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


@pytest.mark.asyncio
async def test_snapshot_rows_default_mode_raises_on_duplicate_pk(registry) -> None:
    """Default snapshot path is strict — a second call snapshotting the same
    rows hits the history$ PK and raises IntegrityError. This is the failure
    mode that the v1 SECURITY refresher hit after a partially-failed run left
    history$ populated; ``if_not_exists`` is the operator's escape hatch
    (covered by the next test)."""
    await _seed(registry, [(10, 1, "alice"), (10, 2, "bob")])
    await snapshot_rows(
        connectors=registry, target_connector="target",
        source_table="security_users", history_table='"security_users$"',
    )
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        await snapshot_rows(
            connectors=registry, target_connector="target",
            source_table="security_users", history_table='"security_users$"',
        )


@pytest.mark.asyncio
async def test_snapshot_rows_if_not_exists_silently_skips_duplicates(registry) -> None:
    """``if_not_exists=True`` makes the snapshot idempotent — a second call
    re-attempting the same rows succeeds (the duplicates are SKIPPED, not
    re-inserted). Returns rowcount-of-actually-inserted (0 here, since every
    row was already in history). The fix for the failed-then-retried SECURITY
    refresher loop."""
    await _seed(registry, [(10, 1, "alice"), (10, 2, "bob")])
    # First call: inserts both rows.
    n1 = await snapshot_rows(
        connectors=registry, target_connector="target",
        source_table="security_users", history_table='"security_users$"',
        if_not_exists=True,
    )
    assert n1 == 2
    # Second call with the same source rows: 0 inserted (both already in
    # history), but no exception — the operator can re-run a failed refresh
    # without first cleaning history$ by hand.
    n2 = await snapshot_rows(
        connectors=registry, target_connector="target",
        source_table="security_users", history_table='"security_users$"',
        if_not_exists=True,
    )
    assert n2 == 0
    # History still has exactly the 2 original rows (no duplicates).
    assert len(await _read(registry, '"security_users$"')) == 2


@pytest.mark.asyncio
async def test_snapshot_rows_if_not_exists_inserts_only_new_rows(registry) -> None:
    """When a partial refresh: history holds (apps_id, ukid)=(10,1); a fresh
    snapshot with NEW rows + the same old one inserts only the new ones."""
    # Seed live with the "old" row + reload history with it (simulates a prior
    # snapshot that succeeded), then add NEW rows to live and re-snapshot.
    await _seed(registry, [(10, 1, "old-alice")])
    await snapshot_rows(
        connectors=registry, target_connector="target",
        source_table="security_users", history_table='"security_users$"',
        if_not_exists=True,
    )
    # Now live grows — add new rows that should land in history on this snapshot.
    await _seed(registry, [(10, 2, "bob"), (10, 3, "carol")])
    n = await snapshot_rows(
        connectors=registry, target_connector="target",
        source_table="security_users", history_table='"security_users$"',
        where="usr_apps_id = :apps_id", params={"apps_id": 10},
        if_not_exists=True,
    )
    # 2 inserted (the new rows); 1 skipped (old-alice was already there).
    assert n == 2
    assert len(await _read(registry, '"security_users$"')) == 3


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
        target_schema=None, audit_table="collect_audit",
        target_table="SECURITY_USERS", apps_id=10, module="SECURITY",
    )
    rows = await _read(registry, "collect_audit")
    assert len(rows) == 1
    # (cla_audit_id, cla_apps_id, cla_module, cla_target, cla_action, cla_refresh, cla_run_id)
    _, apps_id, module, target, action, refresh, run_id = rows[0]
    assert apps_id == 10
    assert module == "SECURITY"
    assert target == "SECURITY_USERS"
    assert action == "ETL"
    assert refresh is not None  # CURRENT_TIMESTAMP; format varies by driver
    assert run_id is None       # no active nomaflow run in this test


@pytest.mark.asyncio
async def test_insert_audit_record_custom_action(registry) -> None:
    await insert_audit_record(
        connectors=registry, target_connector="target",
        target_schema=None, audit_table="collect_audit",
        target_table="SECURITY_USERS", apps_id=10, module="SECURITY",
        action="REFRESH",
    )
    rows = await _read(registry, "collect_audit")
    assert rows[0][4] == "REFRESH"


@pytest.mark.asyncio
async def test_insert_audit_record_auto_resolves_run_id(registry) -> None:
    """When the caller doesn't pass ``run_id``, the helper pulls the active
    nomaflow run id from :func:`liberty.jobs.runlog.current_run_id` (the
    ContextVar set by the runner around each python step). Mock the
    ContextVar to verify the wiring."""
    from liberty.jobs.runlog import set_run_context, reset_run_context
    token = set_run_context("test-run-123")
    try:
        await insert_audit_record(
            connectors=registry, target_connector="target",
            target_schema=None, audit_table="collect_audit",
            target_table="SECURITY_USERS", apps_id=10, module="SECURITY",
        )
    finally:
        reset_run_context(token)
    rows = await _read(registry, "collect_audit")
    assert rows[0][6] == "test-run-123"  # cla_run_id


@pytest.mark.asyncio
async def test_insert_audit_record_explicit_run_id_wins(registry) -> None:
    """Caller-supplied ``run_id`` overrides whatever the ContextVar holds."""
    from liberty.jobs.runlog import set_run_context, reset_run_context
    token = set_run_context("ctx-id")
    try:
        await insert_audit_record(
            connectors=registry, target_connector="target",
            target_schema=None, audit_table="collect_audit",
            target_table="SECURITY_USERS", apps_id=10, module="SECURITY",
            run_id="explicit-id",
        )
    finally:
        reset_run_context(token)
    rows = await _read(registry, "collect_audit")
    assert rows[0][6] == "explicit-id"


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
