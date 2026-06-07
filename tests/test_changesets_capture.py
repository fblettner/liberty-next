"""Change-package capture — the param-split / natural-key logic + the end-to-end write capture."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.changesets import store
from liberty.changesets.capture import capture_write, entity_key, split_params
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
