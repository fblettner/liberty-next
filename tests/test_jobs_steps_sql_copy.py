"""Integration tests for :class:`liberty.jobs.SqlCopyExecutor` — exercised
against a real in-memory SQLite ConnectorRegistry.

What these tests catch that the pure-function coercion tests can't:

* The end-to-end orchestration: introspect → DDL → stream → batched INSERT
  → atomic rename.
* Mid-stream failure leaving the previous run's data intact (the §5.1
  data-loss-fix premise).
* The self-copy guard.
* Append mode vs overwrite mode.
* Unknown-mode (upsert) producing the right error message.

SQLite quirks that affect the test design:

* Dynamic typing — SQLite stores anything in any column. Type assertions about
  the target table's column types belong in :mod:`test_jobs_coercion`, not here.
* No schemas (in the Postgres sense). We use ``schema_=None`` throughout.
* Atomic ``ALTER TABLE ... RENAME TO`` works on SQLite 3.25+ and is what
  ships with Python 3.12; both the modern Postgres path and SQLite go through
  the same code.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from liberty.connectors.config import (
    ConnectorsFile,
    PoolConfig,
    SqlConnectorConfig,
)
from liberty.connectors.registry import ConnectorRegistry
from liberty.jobs import (
    CopyMode,
    DecimalMode,
    ManualTrigger,
    RunContext,
    SqlCopyExecutor,
    Step,
    StepFailed,
    StepType,
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def registry(tmp_path):
    """A registry with two SQL connectors over file-backed SQLite (separate DBs
    per connector — closer to the real source/target shape than a single in-mem
    DB pretending to be two)."""
    cfg = ConnectorsFile(
        pools={
            "source": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'source.db'}"),
            "target": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'target.db'}"),
        },
        connectors={
            "src": SqlConnectorConfig(type="sql", pool="source", queries=[]),
            "tgt": SqlConnectorConfig(type="sql", pool="target", queries=[]),
        },
    )
    reg = ConnectorRegistry(cfg)
    # Seed source schema with one mixed-type table
    src_engine = reg.pools.engine("source")
    async with src_engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE F0901 (UPMJ INTEGER, NAME VARCHAR(50), AMOUNT NUMERIC(10, 2))"
        ))
    yield reg
    await reg.aclose()


@pytest_asyncio.fixture
async def registry_strings(tmp_path):
    """Like ``registry`` but the **source** pool sets ``trim_strings=true`` and
    the **target** pool ``coalesce_nulls=true`` — the JDE-shaped configuration
    where sql_copy must strip Oracle CHAR/NCHAR padding on read and pad/zero
    empties on write (PHASE13 §5.1)."""
    cfg = ConnectorsFile(
        pools={
            "source": PoolConfig(
                url=f"sqlite+aiosqlite:///{tmp_path / 'source.db'}",
                trim_strings=True,
            ),
            "target": PoolConfig(
                url=f"sqlite+aiosqlite:///{tmp_path / 'target.db'}",
                coalesce_nulls=True,
            ),
        },
        connectors={
            "src": SqlConnectorConfig(type="sql", pool="source", queries=[]),
            "tgt": SqlConnectorConfig(type="sql", pool="target", queries=[]),
        },
    )
    reg = ConnectorRegistry(cfg)
    src_engine = reg.pools.engine("source")
    async with src_engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE F0901 (UPMJ INTEGER, NAME VARCHAR(50), AMOUNT NUMERIC(10, 2))"
        ))
    yield reg
    await reg.aclose()


async def _seed_source_rows(registry, rows: list[tuple]) -> None:
    src_engine = registry.pools.engine("source")
    async with src_engine.begin() as conn:
        for row in rows:
            await conn.execute(
                text("INSERT INTO F0901 (UPMJ, NAME, AMOUNT) VALUES (:u, :n, :a)"),
                {"u": row[0], "n": row[1], "a": row[2]},
            )


async def _read_target_rows(registry, table: str) -> list[tuple]:
    tgt_engine = registry.pools.engine("target")
    async with tgt_engine.connect() as conn:
        result = await conn.execute(text(f'SELECT * FROM "{table}" ORDER BY 1'))
        return [tuple(r) for r in result.fetchall()]


def _ctx() -> RunContext:
    return RunContext(
        run_id="copy-run-1",
        job_id="copy-job-1",
        trigger=ManualTrigger(triggered_by="tests"),
    )


def _copy_step(*, name="copy-it", mode="overwrite", batch_size=2,
               type_coercion="jde", decimal_mode="truncate",
               source_conn="src", source_schema=None, source_table="F0901",
               target_conn="tgt", target_schema=None, target_table="f0901",
               strip_both_columns: list[str] | None = None) -> Step:
    body: dict = {
        "type": StepType.SQL_COPY.value,
        "name": name,
        "mode": mode,
        "batch_size": batch_size,
        "type_coercion": type_coercion,
        "decimal_mode": decimal_mode,
        "source": {"connector": source_conn, "schema": source_schema, "table": source_table},
        "target": {"connector": target_conn, "schema": target_schema, "table": target_table},
    }
    if strip_both_columns is not None:
        body["strip_both_columns"] = strip_both_columns
    return Step.model_validate(body)


# --------------------------------------------------------------------------- #
# overwrite — happy path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_overwrite_copies_rows_with_jde_coercion(registry) -> None:
    await _seed_source_rows(registry, [
        (123, "  alpha\x00 ", "100.50"),
        (456, "beta", "200.99"),
        (789, "gamma", "300.00"),
    ])
    executor = SqlCopyExecutor(registry)
    result = await executor.execute(_copy_step(batch_size=2), _ctx())

    assert result.rows_affected == 3
    # JDE: column names lowercased; decimals truncated; \x00 stripped from strings.
    # Trailing whitespace is NOT stripped here — the source pool has the default
    # trim_strings=false; the trim is exercised in test_overwrite_honors_pool_*.
    rows = await _read_target_rows(registry, "f0901")
    # Order: ordering by column 1 (upmj) — preserves insert order here
    assert rows[0] == (123, "  alpha ", 100)
    assert rows[1] == (456, "beta", 200)
    assert rows[2] == (789, "gamma", 300)


@pytest.mark.asyncio
async def test_overwrite_replaces_existing_data(registry) -> None:
    """Run the copy twice: first puts data, second replaces it. Verifies the
    atomic rename actually swaps the live table on the second pass."""
    await _seed_source_rows(registry, [(1, "first", "10.0")])
    executor = SqlCopyExecutor(registry)
    await executor.execute(_copy_step(), _ctx())
    assert await _read_target_rows(registry, "f0901") == [(1, "first", 10)]

    # Replace source contents, re-copy
    src_engine = registry.pools.engine("source")
    async with src_engine.begin() as conn:
        await conn.execute(text("DELETE FROM F0901"))
    await _seed_source_rows(registry, [(2, "second", "20.0")])
    await executor.execute(_copy_step(), _ctx())
    assert await _read_target_rows(registry, "f0901") == [(2, "second", 20)]


@pytest.mark.asyncio
async def test_overwrite_creates_target_first_time(registry) -> None:
    """First run: target table didn't exist, the executor must create it."""
    await _seed_source_rows(registry, [(1, "x", "1.0")])
    executor = SqlCopyExecutor(registry)
    await executor.execute(_copy_step(target_table="f0901_new"), _ctx())
    assert await _read_target_rows(registry, "f0901_new") == [(1, "x", 1)]


@pytest.mark.asyncio
async def test_overwrite_empty_source_creates_empty_target(registry) -> None:
    """Zero rows in source → target still gets created (empty), rows_affected=0."""
    executor = SqlCopyExecutor(registry)
    result = await executor.execute(_copy_step(), _ctx())
    assert result.rows_affected == 0
    assert await _read_target_rows(registry, "f0901") == []


@pytest.mark.asyncio
async def test_overwrite_honors_pool_string_settings(registry_strings) -> None:
    """sql_copy reads the **source** pool's ``trim_strings`` (strip Oracle
    CHAR/NCHAR padding on read) and the **target** pool's ``coalesce_nulls``
    (pad/zero empties on write) — the two pool flags from PHASE13 §5.1."""
    await _seed_source_rows(registry_strings, [
        (1, "padded     ", "10.0"),   # trailing padding → trimmed on read
        (2, None, None),              # NULLs → coalesced on write
    ])
    executor = SqlCopyExecutor(registry_strings)
    result = await executor.execute(_copy_step(batch_size=10), _ctx())

    assert result.rows_affected == 2
    rows = await _read_target_rows(registry_strings, "f0901")
    # row 1: trailing padding stripped by the source pool's trim_strings
    assert rows[0] == (1, "padded", 10)
    # row 2: NULL name → " ", NULL amount → 0 (target pool's coalesce_nulls)
    assert rows[1] == (2, " ", 0)


@pytest.mark.asyncio
async def test_overwrite_strip_both_columns_handles_left_padding(registry_strings) -> None:
    """JDE right-justifies a handful of codes (DRKY, DRMCU…). The step lists NAME
    as the stand-in; a leading-padded value comes through fully stripped, while
    a row with only trailing padding still ends up rstripped (NAME is in the
    step's strip_both_columns → full strip both ends).

    Per-step (not per-pool) on purpose: JDE column names embed a 2-letter table
    prefix (F0005's right-justified code is ``DRKY``, F0101's is ``ABKY``) so
    the right list is per-table."""
    await _seed_source_rows(registry_strings, [
        (1, "       1.4480", "10.0"),    # JDE-style left-padded code → both-end strip
        (2, "ordinary    ", "20.0"),     # also full-strip — same column rule applies
    ])
    executor = SqlCopyExecutor(registry_strings)
    await executor.execute(
        _copy_step(batch_size=10, strip_both_columns=["NAME"]), _ctx(),
    )
    rows = await _read_target_rows(registry_strings, "f0901")
    assert rows[0] == (1, "1.4480", 10)
    assert rows[1] == (2, "ordinary", 20)


# --------------------------------------------------------------------------- #
# overwrite — atomic-rename safety
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_failed_overwrite_recovers_to_previous_data(registry, monkeypatch) -> None:
    """The stash + rename pattern: if the work→live rename fails after the
    live→stash succeeded, recovery puts the previous run's data back. The §5.1
    data-loss fix premise: production never goes "stale → empty" across a
    failed copy.

    Sabotage strategy: sabotage only the second call to _rename_sql (the
    work→live rename). The first (live→stash) succeeds; the third (stash→live
    recovery) also succeeds. Net effect: live ends up unchanged."""
    await _seed_source_rows(registry, [(99, "previous", "99.0")])
    executor = SqlCopyExecutor(registry)
    # First successful copy puts the "previous" data in place
    await executor.execute(_copy_step(), _ctx())
    assert await _read_target_rows(registry, "f0901") == [(99, "previous", 99)]

    # New source data
    src_engine = registry.pools.engine("source")
    async with src_engine.begin() as conn:
        await conn.execute(text("DELETE FROM F0901"))
    await _seed_source_rows(registry, [(1, "new", "1.0")])

    # Sabotage: patch _rename_sql to raise on call #2 (the work→live swap),
    # passing through on calls #1 (live→stash) and #3 (recovery stash→live).
    from liberty.jobs.steps import sql_copy as sql_copy_module
    real_rename = sql_copy_module._rename_sql
    call_count = {"n": 0}

    def selective_explode(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("work→live rename sabotaged")
        return real_rename(*args, **kwargs)

    monkeypatch.setattr(sql_copy_module, "_rename_sql", selective_explode)
    with pytest.raises(Exception):
        await executor.execute(_copy_step(), _ctx())

    # Recovery rename restored live from stash. Previous data is back.
    assert await _read_target_rows(registry, "f0901") == [(99, "previous", 99)]
    # Three calls to _rename_sql: live→stash, work→live (failed), stash→live (recovery)
    assert call_count["n"] == 3

    # Restore + verify a successful re-run cleans up
    monkeypatch.setattr(sql_copy_module, "_rename_sql", real_rename)
    await executor.execute(_copy_step(), _ctx())
    assert await _read_target_rows(registry, "f0901") == [(1, "new", 1)]


# --------------------------------------------------------------------------- #
# append mode
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_append_mode_adds_to_existing_target(registry) -> None:
    # First overwrite to create + seed the target
    await _seed_source_rows(registry, [(1, "a", "1.0"), (2, "b", "2.0")])
    executor = SqlCopyExecutor(registry)
    await executor.execute(_copy_step(), _ctx())

    # Clear source, add new rows, then APPEND
    src_engine = registry.pools.engine("source")
    async with src_engine.begin() as conn:
        await conn.execute(text("DELETE FROM F0901"))
    await _seed_source_rows(registry, [(3, "c", "3.0")])

    result = await executor.execute(_copy_step(mode="append", batch_size=10), _ctx())
    assert result.rows_affected == 1
    rows = await _read_target_rows(registry, "f0901")
    assert len(rows) == 3
    assert {r[0] for r in rows} == {1, 2, 3}


# --------------------------------------------------------------------------- #
# decimal_mode = preserve
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_decimal_mode_preserve_keeps_decimal_places(registry) -> None:
    """decimal_mode=preserve → Decimal(p, s>0) is NOT truncated. The target
    column type is numeric(p, s) (verified at the DDL level in test_jobs_coercion;
    here we verify the row values survive)."""
    await _seed_source_rows(registry, [(1, "x", "123.45")])
    executor = SqlCopyExecutor(registry)
    await executor.execute(_copy_step(decimal_mode="preserve"), _ctx())
    rows = await _read_target_rows(registry, "f0901")
    # SQLite doesn't enforce NUMERIC scale, but the value flowing through
    # should preserve the cents (not be 123 from truncation).
    assert rows[0][2] in ("123.45", 123.45)


# --------------------------------------------------------------------------- #
# batching
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_batch_size_split_into_multiple_inserts(registry) -> None:
    """7 rows with batch_size=3 → 3 + 3 + 1, all land in the target."""
    rows_in = [(i, f"name{i}", f"{i}.0") for i in range(1, 8)]
    await _seed_source_rows(registry, rows_in)
    executor = SqlCopyExecutor(registry)
    result = await executor.execute(_copy_step(batch_size=3), _ctx())
    assert result.rows_affected == 7
    out = await _read_target_rows(registry, "f0901")
    assert [r[0] for r in out] == [1, 2, 3, 4, 5, 6, 7]


# --------------------------------------------------------------------------- #
# error paths
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_self_copy_refused(registry) -> None:
    """Source and target resolving to the same physical table is a configuration
    bug — the overwrite path would DROP the source. Refuse with a clear error."""
    executor = SqlCopyExecutor(registry)
    # Both endpoints point at "src" with the same table
    step = _copy_step(
        source_conn="src", source_table="F0901",
        target_conn="src", target_table="F0901",
    )
    with pytest.raises(StepFailed) as exc:
        await executor.execute(step, _ctx())
    assert "same" in str(exc.value).lower() or "self" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_unknown_source_connector_is_step_failed(registry) -> None:
    executor = SqlCopyExecutor(registry)
    with pytest.raises(StepFailed) as exc:
        await executor.execute(_copy_step(source_conn="missing"), _ctx())
    assert "missing" in str(exc.value)


@pytest.mark.asyncio
async def test_source_table_missing_is_step_failed(registry) -> None:
    """Introspecting a non-existent table returns no columns; the executor
    surfaces this as StepFailed with a useful message instead of cascading
    into an obscure DDL error later."""
    executor = SqlCopyExecutor(registry)
    with pytest.raises(StepFailed) as exc:
        await executor.execute(_copy_step(source_table="does_not_exist"), _ctx())
    assert "does_not_exist" in str(exc.value)


@pytest.mark.asyncio
async def test_upsert_mode_raises_with_helpful_message(registry) -> None:
    executor = SqlCopyExecutor(registry)
    await _seed_source_rows(registry, [(1, "x", "1.0")])
    with pytest.raises(StepFailed) as exc:
        await executor.execute(_copy_step(mode="upsert"), _ctx())
    msg = str(exc.value).lower()
    assert "upsert" in msg
    assert "merge" in msg or "on conflict" in msg or "sql_query" in msg
