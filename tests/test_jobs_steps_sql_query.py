"""Tests for :class:`liberty.jobs.SqlQueryExecutor` — exercised against a real
in-memory SQLite ConnectorRegistry, no mocks.

Three things matter here that the runner-level mock tests can't cover:

* The executor talks to a real :class:`SQLConnector.execute` so a refactor of
  the connector API surfaces as a test failure here, not as a 3am production bug.
* Named-query lookup actually goes through the registry — typos in connector
  or query names raise :class:`StepFailed` with a useful message.
* Write-vs-read row counts both end up in :attr:`StepResult.rows_affected`,
  using ``QueryResult.row_count``'s SELECT/write asymmetry handling.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from liberty.connectors.config import (
    ConnectorsFile,
    PoolConfig,
    QueryDef,
    SqlConnectorConfig,
)
from liberty.connectors.registry import ConnectorRegistry
from liberty.jobs import (
    ManualTrigger,
    RunContext,
    SqlQueryExecutor,
    Step,
    StepFailed,
    StepType,
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def registry():
    """A real ConnectorRegistry over an in-memory SQLite with a small schema —
    one named SELECT, one named INSERT, one writable DELETE."""
    cfg = ConnectorsFile(
        pools={"default": PoolConfig(url="sqlite+aiosqlite:///:memory:")},
        connectors={
            "db": SqlConnectorConfig(
                type="sql",
                pool="default",
                queries=[
                    QueryDef(name="count_widgets", sql="SELECT COUNT(*) AS n FROM widgets"),
                    QueryDef(name="list_widgets", sql="SELECT id, name FROM widgets ORDER BY id"),
                    QueryDef(
                        name="insert_widget",
                        sql="INSERT INTO widgets (id, name) VALUES (:id, :name)",
                        writable=True,
                    ),
                    QueryDef(
                        name="delete_all",
                        sql="DELETE FROM widgets",
                        writable=True,
                    ),
                ],
            )
        },
    )
    reg = ConnectorRegistry(cfg)
    # Bootstrap the schema directly through the pool — no need for a separate setup query
    engine = reg.pools.engine("default")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)"))
    yield reg
    await reg.aclose()


def _ctx() -> RunContext:
    return RunContext(
        run_id="run-1",
        job_id="job-1",
        trigger=ManualTrigger(triggered_by="tests"),
    )


def _step(**kwargs) -> Step:
    """Build a sql_query Step inline; convenient for one-test shapes."""
    kwargs.setdefault("type", StepType.SQL_QUERY)
    kwargs.setdefault("name", "test-step")
    return Step.model_validate(kwargs)


# --------------------------------------------------------------------------- #
# happy paths — SELECT + write
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_select_returns_row_count(registry) -> None:
    """SELECT row_count == len(rows). Empty table → 1 row (the COUNT result)."""
    executor = SqlQueryExecutor(registry)
    result = await executor.execute(
        _step(connector="db", query="count_widgets"), _ctx(),
    )
    assert result.rows_affected == 1  # one row from COUNT(*)


@pytest.mark.asyncio
async def test_insert_returns_rowcount(registry) -> None:
    executor = SqlQueryExecutor(registry)
    result = await executor.execute(
        _step(connector="db", query="insert_widget", params={"id": 1, "name": "alpha"}),
        _ctx(),
    )
    assert result.rows_affected == 1


@pytest.mark.asyncio
async def test_writes_persist_across_steps(registry) -> None:
    """Two consecutive steps see each other's writes — proves we're not in
    a session that rolls back at step boundaries."""
    executor = SqlQueryExecutor(registry)
    await executor.execute(
        _step(connector="db", query="insert_widget", params={"id": 1, "name": "a"}),
        _ctx(),
    )
    await executor.execute(
        _step(connector="db", query="insert_widget", params={"id": 2, "name": "b"}),
        _ctx(),
    )
    listing = await executor.execute(
        _step(connector="db", query="list_widgets"), _ctx(),
    )
    assert listing.rows_affected == 2  # two rows from SELECT after the inserts


@pytest.mark.asyncio
async def test_delete_returns_rowcount(registry) -> None:
    """Writable DELETE returns the driver's rowcount of affected rows."""
    executor = SqlQueryExecutor(registry)
    # Seed two rows first
    for i, name in [(10, "x"), (20, "y")]:
        await executor.execute(
            _step(connector="db", query="insert_widget", params={"id": i, "name": name}),
            _ctx(),
        )
    result = await executor.execute(
        _step(connector="db", query="delete_all"), _ctx(),
    )
    assert result.rows_affected == 2


# --------------------------------------------------------------------------- #
# error paths — surfaced as StepFailed, not raw connector errors
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unknown_connector_raises_step_failed(registry) -> None:
    executor = SqlQueryExecutor(registry)
    with pytest.raises(StepFailed) as exc:
        await executor.execute(
            _step(connector="nope", query="count_widgets"), _ctx(),
        )
    assert "nope" in str(exc.value)


@pytest.mark.asyncio
async def test_unknown_query_raises_step_failed(registry) -> None:
    executor = SqlQueryExecutor(registry)
    with pytest.raises(StepFailed) as exc:
        await executor.execute(
            _step(connector="db", query="does_not_exist"), _ctx(),
        )
    # Should mention the missing query name
    assert "does_not_exist" in str(exc.value)


@pytest.mark.asyncio
async def test_wrong_step_type_raises_step_failed(registry) -> None:
    """Defence-in-depth: if a runner wires SqlQueryExecutor for the wrong step
    type, we raise with a clear message rather than mis-execute."""
    executor = SqlQueryExecutor(registry)
    bogus = Step.model_validate({
        "type": "python",
        "name": "wrong",
        "callable": "mod:fn",
    })
    with pytest.raises(StepFailed) as exc:
        await executor.execute(bogus, _ctx())
    assert "python" in str(exc.value)
