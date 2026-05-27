"""Tests for :class:`liberty.jobs.LdapSyncExecutor`.

The LDAP half is stubbed via :func:`monkeypatch` on the module-level
``_search_ldap`` helper — running a real LDAP server in tests would dwarf the
value of the test. The SQL half talks to a real in-memory SQLite
ConnectorRegistry so the per-row writes + the writability check + the
QueryNotFoundError path all execute their actual code (matching the
sql_query test approach).
"""

from __future__ import annotations

from typing import Any

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
    LdapSyncExecutor,
    ManualTrigger,
    RunContext,
    Step,
    StepFailed,
    StepType,
)
from liberty.jobs.steps import ldap_sync as ldap_sync_module


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def registry():
    """Real ConnectorRegistry over in-memory SQLite — same shape as the sql_query
    tests. Two queries to exercise both happy + writable-gate paths."""
    cfg = ConnectorsFile(
        pools={"default": PoolConfig(url="sqlite+aiosqlite:///:memory:")},
        connectors={
            "db": SqlConnectorConfig(
                type="sql",
                pool="default",
                queries=[
                    QueryDef(
                        name="insert_user",
                        sql="INSERT INTO ldap_users (account, mail) VALUES (:account, :mail)",
                        writable=True,
                    ),
                    QueryDef(
                        # Not writable on purpose — exercises the writability gate.
                        name="insert_user_readonly",
                        sql="INSERT INTO ldap_users (account, mail) VALUES (:account, :mail)",
                    ),
                ],
            )
        },
    )
    reg = ConnectorRegistry(cfg)
    engine = reg.pools.engine("default")
    async with engine.begin() as conn:
        # `account` is the PK so a duplicate row trips a constraint error — the
        # row-failure test below relies on that to force a mid-stream failure
        # at a predictable row index.
        await conn.execute(text("CREATE TABLE ldap_users (account TEXT PRIMARY KEY, mail TEXT)"))
    yield reg
    await reg.aclose()


def _ctx() -> RunContext:
    return RunContext(
        run_id="ldap-run-1",
        job_id="ldap-job-1",
        trigger=ManualTrigger(triggered_by="tests"),
    )


def _step(**kwargs) -> Step:
    kwargs.setdefault("type", StepType.LDAP_SYNC.value)
    kwargs.setdefault("name", "ldap-step")
    kwargs.setdefault("server", "ldap://stub.example.com")
    kwargs.setdefault("bind_dn", "CN=svc,DC=example,DC=com")
    kwargs.setdefault("bind_password", "secret")
    kwargs.setdefault("search_base", "DC=example,DC=com")
    kwargs.setdefault("target_connector", "db")
    kwargs.setdefault("target_query", "insert_user")
    return Step.model_validate(kwargs)


def _stub_search(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> list[Step]:
    """Replace ``_search_ldap`` with a stub that records the Step it was called
    with + returns the canned rows."""
    seen: list[Step] = []

    def fake(step: Step) -> list[dict[str, Any]]:
        seen.append(step)
        return rows

    monkeypatch.setattr(ldap_sync_module, "_search_ldap", fake)
    return seen


# --------------------------------------------------------------------------- #
# happy path — search → per-row write → row count returned
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_writes_each_ldap_row_through_target_query(monkeypatch, registry):
    seen_steps = _stub_search(monkeypatch, [
        {"account": "alice", "mail": "alice@example.com"},
        {"account": "bob", "mail": "bob@example.com"},
        {"account": "carol", "mail": None},
    ])
    step = _step(mapping={"sAMAccountName": "account", "mail": "mail"})

    result = await LdapSyncExecutor(registry).execute(step, _ctx())

    assert result.rows_affected == 3
    assert result.extras == {"ldap_entries": 3, "rows_written": 3}
    # The executor passed the Step through unmodified — search params, mapping, etc.
    assert seen_steps[0] is step

    # Confirm the rows actually landed in the DB.
    engine = registry.pools.engine("default")
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT account, mail FROM ldap_users ORDER BY account"))
        rows = [tuple(row) for row in result]
    assert rows == [
        ("alice", "alice@example.com"),
        ("bob", "bob@example.com"),
        ("carol", None),
    ]


@pytest.mark.asyncio
async def test_empty_ldap_result_writes_nothing(monkeypatch, registry):
    """A search returning zero entries still completes cleanly with rows=0."""
    _stub_search(monkeypatch, [])
    result = await LdapSyncExecutor(registry).execute(_step(), _ctx())
    assert result.rows_affected == 0
    assert result.extras == {"ldap_entries": 0, "rows_written": 0}


# --------------------------------------------------------------------------- #
# failure paths — wiring + write-side errors
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unknown_target_connector_raises_step_failed(monkeypatch, registry):
    _stub_search(monkeypatch, [{"account": "a", "mail": "a@x"}])
    step = _step(target_connector="nope")
    with pytest.raises(StepFailed) as exc:
        await LdapSyncExecutor(registry).execute(step, _ctx())
    assert "nope" in str(exc.value)


@pytest.mark.asyncio
async def test_unknown_target_query_raises_step_failed(monkeypatch, registry):
    _stub_search(monkeypatch, [{"account": "a", "mail": "a@x"}])
    step = _step(target_query="missing_query")
    with pytest.raises(StepFailed) as exc:
        await LdapSyncExecutor(registry).execute(step, _ctx())
    assert "missing_query" in str(exc.value)


@pytest.mark.asyncio
async def test_non_writable_target_query_raises_step_failed(monkeypatch, registry):
    """The target query has to be writable — operators get a clear error if not."""
    _stub_search(monkeypatch, [{"account": "a", "mail": "a@x"}])
    step = _step(target_query="insert_user_readonly")
    with pytest.raises(StepFailed) as exc:
        await LdapSyncExecutor(registry).execute(step, _ctx())
    msg = str(exc.value)
    assert "insert_user_readonly" in msg
    assert "writable" in msg


@pytest.mark.asyncio
async def test_row_write_failure_reports_index(monkeypatch, registry):
    """A failed INSERT mid-stream reports which row failed (so the operator
    knows how far the import got + which entry has the bad shape). Forced via
    a duplicate primary key on row 2 (alice → alice trips the UNIQUE on account)."""
    _stub_search(monkeypatch, [
        {"account": "alice", "mail": "alice@example.com"},
        {"account": "alice", "mail": "alice-dup@example.com"},  # PK collision
        {"account": "carol", "mail": "carol@example.com"},
    ])
    step = _step()
    with pytest.raises(StepFailed) as exc:
        await LdapSyncExecutor(registry).execute(step, _ctx())
    # The message names "row 2/3" so the operator can locate the bad entry.
    assert "row 2/3" in str(exc.value)


# --------------------------------------------------------------------------- #
# attribute reader — sparse / multi-valued / empty handling
# --------------------------------------------------------------------------- #


class _StubAttr:
    """Minimal ldap3-shaped attribute holder so :func:`_read_attr` can be tested
    without the ldap3 entry machinery."""

    def __init__(self, value: Any) -> None:
        self.value = value


class _StubEntry:
    """Dict-keyed entry where ``entry[name]`` returns a ``.value``-bearing stub —
    matches ldap3's ``Entry.__getitem__`` signature for the executor's hot path."""

    def __init__(self, **attrs: Any) -> None:
        self._attrs = {k: _StubAttr(v) for k, v in attrs.items()}

    def __getitem__(self, name: str) -> _StubAttr:
        try:
            return self._attrs[name]
        except KeyError:
            # ldap3 raises LDAPKeyError on unknown attribute names; the reader
            # catches that as "missing attribute → None". Plain KeyError covers
            # the same code path.
            raise


def test_read_attr_returns_scalar_value():
    entry = _StubEntry(mail="a@example.com")
    assert ldap_sync_module._read_attr(entry, "mail") == "a@example.com"


def test_read_attr_returns_none_for_missing_attribute():
    """A user without a ``manager`` / ``mobile`` returns None (not an error,
    not empty string) — important so SQL NOT NULL columns work as a contract."""
    entry = _StubEntry(mail="a@example.com")
    assert ldap_sync_module._read_attr(entry, "manager") is None


def test_read_attr_returns_first_value_for_multi_valued():
    """LDAP multi-valued attributes (memberOf, mail aliases) collapse to the
    first entry — single-value is the dominant case + this matches v1 behaviour."""
    entry = _StubEntry(mail=["primary@example.com", "alias@example.com"])
    assert ldap_sync_module._read_attr(entry, "mail") == "primary@example.com"


def test_read_attr_returns_none_for_empty_multi_valued():
    entry = _StubEntry(mail=[])
    assert ldap_sync_module._read_attr(entry, "mail") is None


def test_read_attr_returns_none_for_empty_string():
    entry = _StubEntry(mail="")
    assert ldap_sync_module._read_attr(entry, "mail") is None
