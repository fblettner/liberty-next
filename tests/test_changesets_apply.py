"""Apply engine — replay a bundle's ops with full pre-image drift detection, against SQLite."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.changesets.apply import apply_bundle
from liberty.connectors.config import QueryDef, SqlConnectorConfig, PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.connectors.sql import SQLConnector


class _Reg:
    def __init__(self, conns: dict[str, SQLConnector]) -> None:
        self._c = conns

    def sql(self, name: str) -> SQLConnector:
        return self._c[name]


async def _registry() -> _Reg:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        await c.execute(text("CREATE TABLE item (code TEXT PRIMARY KEY, name TEXT)"))
        await c.execute(text("INSERT INTO item VALUES ('X1', 'a')"))
    pools = PoolRegistry({"p": PoolConfig(url="sqlite://")})
    pools.register_engine("p", engine)
    conn = SQLConnector("c", SqlConnectorConfig(type="sql", pool="p", queries=[
        QueryDef(name="item_get", sql="SELECT code, name FROM item"),
        QueryDef(name="item_put", sql="UPDATE item SET name = :name WHERE code = :code_ORIGINAL", writable=True),
        QueryDef(name="item_post", sql="INSERT INTO item (code, name) VALUES (:code, :name)", writable=True),
        QueryDef(name="item_delete", sql="DELETE FROM item WHERE code = :code_ORIGINAL", writable=True),
    ]), pools)
    return _Reg({"c": conn})


def _op(operation, query, *, key, old=None, new=None):
    return {"connector": "c", "query": query, "read_query": "item_get", "operation": operation,
            "entity": "item", "entity_key": key, "old_values": old, "new_values": new}


async def _name(reg: _Reg) -> list[str]:
    r = await reg.sql("c").execute("item_get")
    return [row["name"] for row in r.rows]


@pytest.mark.asyncio
async def test_update_clean_dry_run_then_apply() -> None:
    reg = await _registry()
    bundle = {"ops": [_op("UPDATE", "item_put", key={"code": "X1"},
                          old={"code": "X1", "name": "a"}, new={"code": "X1", "name": "b"})]}
    dry = await apply_bundle(reg, bundle, dry_run=True, forced=set())
    assert dry["results"][0]["status"] == "would_apply"
    assert await _name(reg) == ["a"]   # dry run wrote nothing
    live = await apply_bundle(reg, bundle, dry_run=False, forced=set())
    assert live["results"][0]["status"] == "applied"
    assert await _name(reg) == ["b"]


@pytest.mark.asyncio
async def test_update_drift_conflict_unless_forced() -> None:
    reg = await _registry()
    # capture expected name="a", but the target now has "drifted"
    await reg.sql("c").execute("item_put", {"name": "drifted", "code_ORIGINAL": "X1"})
    bundle = {"ops": [_op("UPDATE", "item_put", key={"code": "X1"},
                          old={"code": "X1", "name": "a"}, new={"code": "X1", "name": "b"})]}
    rep = await apply_bundle(reg, bundle, dry_run=False, forced=set())
    assert rep["results"][0]["status"] == "conflict" and "differs" in rep["results"][0]["detail"]
    assert await _name(reg) == ["drifted"]   # blocked
    # force it through
    forced = await apply_bundle(reg, bundle, dry_run=False, forced={"0"})
    assert forced["results"][0]["status"] == "applied"
    assert await _name(reg) == ["b"]


@pytest.mark.asyncio
async def test_insert_existing_key_conflicts_and_missing_update_conflicts() -> None:
    reg = await _registry()
    # INSERT a key that already exists → conflict (not executed)
    ins = {"ops": [_op("INSERT", "item_post", key={"code": "X1"}, new={"code": "X1", "name": "z"})]}
    assert (await apply_bundle(reg, ins, dry_run=False, forced=set()))["results"][0]["status"] == "conflict"
    # UPDATE a row that doesn't exist → conflict
    upd = {"ops": [_op("UPDATE", "item_put", key={"code": "NOPE"},
                       old={"code": "NOPE", "name": "a"}, new={"code": "NOPE", "name": "b"})]}
    r = await apply_bundle(reg, upd, dry_run=False, forced=set())
    assert r["results"][0]["status"] == "conflict" and "no longer exists" in r["results"][0]["detail"]


@pytest.mark.asyncio
async def test_insert_new_and_delete_apply() -> None:
    reg = await _registry()
    ins = {"ops": [_op("INSERT", "item_post", key={"code": "X2"}, new={"code": "X2", "name": "n"})]}
    assert (await apply_bundle(reg, ins, dry_run=False, forced=set()))["results"][0]["status"] == "applied"
    assert sorted(await _name(reg)) == ["a", "n"]
    dele = {"ops": [_op("DELETE", "item_delete", key={"code": "X1"}, old={"code": "X1", "name": "a"})]}
    assert (await apply_bundle(reg, dele, dry_run=False, forced=set()))["results"][0]["status"] == "applied"
    assert await _name(reg) == ["n"]


@pytest.mark.asyncio
async def test_apply_replays_call_api_invocation() -> None:
    """A CALL_API op re-issues the captured call via the target's API connector — unverified (no
    drift check) but applied. dry_run reports it without firing."""
    reg = await _registry()
    calls: list[tuple] = []

    class _Result:
        success = True

    class _Api:
        async def call(self, endpoint, params):
            calls.append((endpoint, dict(params)))
            return _Result()

    reg.api = lambda name: _Api()   # type: ignore[attr-defined]
    bundle = {"ops": [{
        "connector": "ext", "query": "sync_user", "operation": "CALL_API",
        "entity": "user", "entity_key": None, "new_values": {"USR_ID": "DEMO3"},
    }]}
    # dry run → reported unverified, nothing fired
    rep = await apply_bundle(reg, bundle, dry_run=True, forced=set())
    assert rep["results"][0]["status"] == "unverified" and calls == []
    # real apply → the call fires verbatim
    rep = await apply_bundle(reg, bundle, dry_run=False, forced=set())
    assert calls == [("sync_user", {"USR_ID": "DEMO3"})]
    assert rep["results"][0]["status"] == "applied"


@pytest.mark.asyncio
async def test_apply_call_api_failure_is_reported_not_raised() -> None:
    """An upstream failure on replay surfaces as a per-op error, not a crash of the whole apply."""
    reg = await _registry()

    class _Result:
        success = False
        error = "boom"
        status_code = 500

    class _Api:
        async def call(self, endpoint, params):
            return _Result()

    reg.api = lambda name: _Api()   # type: ignore[attr-defined]
    bundle = {"ops": [{"connector": "ext", "query": "sync_user", "operation": "CALL_API",
                       "entity": "user", "entity_key": None, "new_values": {}}]}
    rep = await apply_bundle(reg, bundle, dry_run=False, forced=set())
    assert rep["results"][0]["status"] == "error" and "boom" in rep["results"][0]["detail"]
