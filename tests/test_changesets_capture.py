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
    tracked = SimpleNamespace(change_tracked=True, change_entity="security", key_columns=["FSOBNM"])
    untracked = SimpleNamespace(change_tracked=False, change_entity=None, key_columns=[])

    # untracked screen → no capture
    assert await capture_write(db, connector="jdedwards", query="f00950_put", statement_type="UPDATE",
                               params={"FSOBNM": "P01013"}, screen=untracked, user="franck") is None
    # SELECT → no capture even if tracked
    assert await capture_write(db, connector="jdedwards", query="f00950_get", statement_type="SELECT",
                               params={"FSOBNM": "P01013"}, screen=tracked, user="franck") is None
    # tracked write → captured
    eid = await capture_write(
        db, connector="jdedwards", query="f00950_put", statement_type="UPDATE",
        params={"FSOBNM": "P01013", "FSOBNM_ORIGINAL": "P01012", "FSUSER": "DEMO"},
        screen=tracked, user="franck",
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
