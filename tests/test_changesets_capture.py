"""Change-package capture — the param-split / natural-key logic + the end-to-end write capture."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.changesets import store
from liberty.changesets.capture import capture_invocation, capture_write, entity_key, split_params
from liberty.changesets.db import ChangeSetDatabase
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry


def test_split_params_separates_new_from_pre_image() -> None:
    params = {
        "FSOBNM": "P01013", "FSUSER": "DEMO",          # new state
        "FSOBNM_ORIGINAL": "P01012", "FSUSER_ORIGINAL": "DEMO",  # pre-image
        "FSOBNM_op": "equals",                          # filter op — ignored (lowercase tail)
        "_aud_user": "x",                               # internal — ignored (not all-upper)
    }
    new, old = split_params(params)
    assert new == {"FSOBNM": "P01013", "FSUSER": "DEMO"}
    assert old == {"FSOBNM": "P01012", "FSUSER": "DEMO"}   # suffix stripped


def test_split_params_json_normalises_resolved_binds() -> None:
    """Capture now records the FULL resolved bind set, which can carry non-JSON-native values
    (a SYSDATE ``datetime``, a numeric ``Decimal``). split_params must coerce them so the JSON
    new_values/old_values columns accept them. JDE audit dates are jdedate/jdetime ints already."""
    from datetime import datetime
    from decimal import Decimal
    new, old = split_params({
        "ULJOBN": "LIBERTY",                       # DD default, server-filled on an empty bind
        "CREATED_AT": datetime(2026, 6, 7, 14, 30, 52),
        "AMOUNT": Decimal("10.50"),
        "QTY": Decimal("3"),
        "BLOB": b"\x00\x01",                        # binary → dropped to None
        "ULUSER_ORIGINAL": "DEMO3",
    })
    assert new["ULJOBN"] == "LIBERTY"              # the captured default
    assert new["CREATED_AT"] == "2026-06-07T14:30:52"
    assert new["AMOUNT"] == 10.5 and new["QTY"] == 3
    assert new["BLOB"] is None
    assert old == {"ULUSER": "DEMO3"}


def test_entity_key_prefers_pre_image_for_stable_identity() -> None:
    # update: pre-image key is the stable identity (the OLD object name)
    assert entity_key({"FSOBNM": "P01013"}, {"FSOBNM": "P01012"}, ["FSOBNM"]) == {"FSOBNM": "P01012"}
    # insert: no pre-image → new
    assert entity_key({"FSOBNM": "P01013"}, {}, ["FSOBNM"]) == {"FSOBNM": "P01013"}
    # key-less delete (key bound as the plain param) → new
    assert entity_key({"FSSETY": "3", "FSOBNM": "P01012"}, {}, ["FSSETY", "FSOBNM"]) == {"FSSETY": "3", "FSOBNM": "P01012"}


async def _db() -> ChangeSetDatabase:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    pools = PoolRegistry({"ctrl": PoolConfig(url="sqlite://")})
    pools.register_engine("ctrl", engine)
    db = ChangeSetDatabase(pools, "ctrl")
    await db.create_schema()
    return db


@pytest.mark.asyncio
async def test_capture_write_records_tracked_write_only() -> None:
    db = await _db()

    # not tracked → no capture
    assert await capture_write(db, connector="jdedwards", query="f00950_put", statement_type="UPDATE",
                               params={"FSOBNM": "P01013"}, user="franck",
                               key_columns=["FSOBNM"], read_query="f00950_get", entity="security",
                               change_tracked=False) is None
    # SELECT → no capture even if tracked
    assert await capture_write(db, connector="jdedwards", query="f00950_get", statement_type="SELECT",
                               params={"FSOBNM": "P01013"}, user="franck",
                               key_columns=["FSOBNM"], read_query="f00950_get", entity="security") is None
    # tracked write → captured
    eid = await capture_write(
        db, connector="jdedwards", query="f00950_put", statement_type="UPDATE",
        params={"FSOBNM": "P01013", "FSOBNM_ORIGINAL": "P01012", "FSUSER": "DEMO"},
        user="franck", key_columns=["FSOBNM"], read_query="f00950_get", entity="security",
    )
    assert eid is not None
    async with db.session() as s:
        pkgs = await store.list_packages(s, application="jdedwards")
        assert len(pkgs) == 1 and pkgs[0].name == "Package 1 · jdedwards"
        pkg = await store.get_package(s, pkgs[0].id)
        e = pkg.entries[0]
        assert e.operation == "UPDATE" and e.entity == "security"
        assert e.entity_key == {"FSOBNM": "P01012"}        # pre-image identity
        assert e.new_values == {"FSOBNM": "P01013", "FSUSER": "DEMO"}
        assert e.old_values == {"FSOBNM": "P01012"}
        assert e.read_query == "f00950_get"


@pytest.mark.asyncio
async def test_capture_invocation_records_a_replayable_call() -> None:
    """An opted-in API/plugin screen action is captured as an invocation entry: the resolved call
    params in new_values, no key/pre-image/read-query (effects are opaque, replayed verbatim). The
    package scope is the originating screen's connector, which may differ from the call's target."""
    db = await _db()
    eid = await capture_invocation(
        db, application="jdedwards", connector="ldap", operation="CALL_API",
        target="sync_user", params={"USR_ID": "DEMO3", "MAIL": "d@x"}, entity="user", user="franck",
    )
    assert eid is not None
    async with db.session() as s:
        pkg = await store.get_package(s, (await store.list_packages(s, application="jdedwards"))[0].id)
        e = pkg.entries[0]
        assert e.operation == "CALL_API" and e.connector == "ldap" and e.query == "sync_user"
        assert e.new_values == {"USR_ID": "DEMO3", "MAIL": "d@x"}
        assert e.entity == "user" and e.entity_key is None and e.old_values is None and e.read_query is None
        assert e.replay is True   # default — opted in


@pytest.mark.asyncio
async def test_capture_only_invocation_is_recorded_but_not_replayed() -> None:
    """A change-tracked screen captures EVERY call_api/plugin (so the package shows it), but a call
    with ``change_replay`` off is stored with ``replay=False`` and apply SKIPS it (doesn't re-fire)."""
    from liberty.changesets.apply import apply_bundle
    from liberty.changesets.compaction import compact
    db = await _db()
    await capture_invocation(
        db, application="jdedwards", connector="ldap", operation="CALL_API",
        target="notify_admin", params={"MAIL": "a@x"}, entity="user", user="franck", replay=False,
    )
    async with db.session() as s:
        pkg = await store.get_package(s, (await store.list_packages(s, application="jdedwards"))[0].id)
        assert pkg.entries[0].replay is False
        ops = compact(pkg.entries)
    assert ops[0]["replay"] is False
    rep = await apply_bundle(_NoConns(), {"ops": ops}, dry_run=False, forced=set())
    assert rep["results"][0]["status"] == "skipped"
    assert rep["summary"] == {"skipped": 1}


class _NoConns:
    """Connector registry that fails if any API/SQL is touched — proves a skipped op never executes."""
    def api(self, name):  # noqa: ANN001
        raise AssertionError("apply must NOT re-fire a capture-only invocation")
    def sql(self, name):  # noqa: ANN001
        raise AssertionError("apply must NOT touch SQL for a capture-only invocation")


@pytest.mark.asyncio
async def test_capture_accumulates_screen_post_apply_ids_on_package() -> None:
    """A screen's ``post_apply`` ids are unioned onto its package as changes are captured — so export
    carries only the steps the contributing screens require (a UDC change won't pull in a remerge)."""
    from liberty.changesets import store
    db = await _db()
    # security screen change → carries the remerge step
    await capture_write(db, connector="jdedwards", query="f00950_put", statement_type="UPDATE",
                        params={"FSOBNM": "P01013", "FSOBNM_ORIGINAL": "P01012"}, user="admin",
                        key_columns=["FSOBNM"], read_query="f00950_get", entity="security",
                        post_apply_ids=["remerge"])
    # a second security change with the same step → no duplicate
    await capture_write(db, connector="jdedwards", query="f00950_put", statement_type="UPDATE",
                        params={"FSOBNM": "P9", "FSOBNM_ORIGINAL": "P8"}, user="admin",
                        key_columns=["FSOBNM"], read_query="f00950_get", entity="security",
                        post_apply_ids=["remerge"])
    async with db.session() as s:
        pkg = (await store.list_packages(s, application="jdedwards"))[0]
        assert pkg.post_apply_ids == ["remerge"]   # union, deduped


@pytest.mark.asyncio
async def test_delete_package_removes_it_and_its_entries() -> None:
    """delete_package drops the package + cascades its entries; returns False when already gone."""
    db = await _db()
    await capture_write(db, connector="jdedwards", query="f0092_post", statement_type="INSERT",
                        params={"ULUSER": "DEMO3"}, user="admin",
                        key_columns=["ULUSER"], read_query="f0092_get", entity="user")
    async with db.session() as s:
        pid = (await store.list_packages(s, application="jdedwards"))[0].id
    async with db.session() as s:
        assert await store.delete_package(s, pid) is True
    async with db.session() as s:
        assert await store.get_package(s, pid) is None
        assert await store.list_packages(s, application="jdedwards") == []
    async with db.session() as s:
        assert await store.delete_package(s, pid) is False   # already gone


@pytest.mark.asyncio
async def test_capture_main_and_group_writes_stay_distinct_ops() -> None:
    """A user Save writes the main F0092 row AND the 1:1 F00921 prefs row — both keyed on ULUSER.
    Capture records two entries; compaction must NOT collapse them into one just because they share
    the parent key. The group entry carries no read_query (the related table is only JOINed)."""
    from liberty.changesets.compaction import compact

    db = await _db()
    # Main user insert.
    await capture_write(db, connector="jdedwards", query="f0092_post", statement_type="INSERT",
                        params={"ULUSER": "DEMO3", "ABALPH": "Demo User 3"}, user="admin",
                        key_columns=["ULUSER"], read_query="f0092_get", entity="user")
    # 1:1 group prefs insert — same ULUSER key, no standalone read query.
    await capture_write(db, connector="jdedwards", query="f00921_post", statement_type="INSERT",
                        params={"ULUSER": "DEMO3", "ULFRMT": "DMY"}, user="admin",
                        key_columns=["ULUSER"], read_query=None, entity="user")
    async with db.session() as s:
        pkgs = await store.list_packages(s, application="jdedwards")
        pkg = await store.get_package(s, pkgs[0].id)
        assert len(pkg.entries) == 2
        ops = compact(list(pkg.entries))
        # Two distinct INSERTs, main BEFORE group (FK order preserved by capture sequence).
        assert [o["query"] for o in ops] == ["f0092_post", "f00921_post"]
        assert all(o["operation"] == "INSERT" for o in ops)
        assert ops[1]["read_query"] is None   # group write → unverified drift, still applied
