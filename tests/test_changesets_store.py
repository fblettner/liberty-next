"""Change-package store — schema create + the Phase-1 capture/read surface, on SQLite."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.changesets.db import ChangeSetDatabase
from liberty.changesets import store
from liberty.changesets.models import EntryStatus, Operation, PackageStatus
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry


async def _db() -> ChangeSetDatabase:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    pools = PoolRegistry({"ctrl": PoolConfig(url="sqlite://")})
    pools.register_engine("ctrl", engine)
    db = ChangeSetDatabase(pools, "ctrl")
    await db.create_schema()
    return db


@pytest.mark.asyncio
async def test_get_or_create_active_is_idempotent_per_application() -> None:
    db = await _db()
    async with db.session() as s:
        p1 = await store.get_or_create_active_package(s, "jde_dev", user="franck")
        assert p1.status == PackageStatus.DRAFT.value
        assert p1.name == "Package 1 · jde_dev"
    async with db.session() as s:
        p2 = await store.get_or_create_active_package(s, "jde_dev")
        assert p2.id == p1.id           # same active draft, not a new one
        # a different application gets its own draft
        other = await store.get_or_create_active_package(s, "jde_prod")
        assert other.id != p1.id and other.name == "Package 1 · jde_prod"


@pytest.mark.asyncio
async def test_capture_records_entry_with_seq_and_payload() -> None:
    db = await _db()
    e1 = await store.capture(
        db, "jde_dev", connector="jdedwards", query="f00950_post", operation=Operation.INSERT,
        entity="security", entity_key={"FSOBNM": "P01013"}, new_values={"FSOBNM": "P01013", "FSUSER": "DEMO"},
        user="franck",
    )
    e2 = await store.capture(
        db, "jde_dev", connector="jdedwards", query="f00950_put", operation=Operation.UPDATE,
        entity="security", entity_key={"FSOBNM": "P01013"},
        new_values={"FSOBNM": "P01013", "FSUSER": "DEMO2"}, old_values={"FSOBNM": "P01013", "FSUSER": "DEMO"},
    )
    assert e1 != e2
    async with db.session() as s:
        pkgs = await store.list_packages(s, application="jde_dev")
        assert len(pkgs) == 1
        pkg = await store.get_package(s, pkgs[0].id)
        assert pkg is not None and len(pkg.entries) == 2
        assert [e.seq for e in pkg.entries] == [1, 2]              # capture order preserved
        assert pkg.entries[0].operation == "INSERT"
        assert pkg.entries[1].old_values == {"FSOBNM": "P01013", "FSUSER": "DEMO"}  # pre-image stored
        assert all(e.status == EntryStatus.CAPTURED.value for e in pkg.entries)


@pytest.mark.asyncio
async def test_entries_belong_to_one_active_draft() -> None:
    db = await _db()
    await store.capture(db, "jde_dev", connector="jdedwards", query="f0092_post", operation="INSERT")
    await store.capture(db, "jde_dev", connector="jdedwards", query="f0092_put", operation="UPDATE")
    async with db.session() as s:
        pkgs = await store.list_packages(s, application="jde_dev")
        assert len(pkgs) == 1                                       # both attached to the same draft
        pkg = await store.get_package(s, pkgs[0].id)
        assert len(pkg.entries) == 2
