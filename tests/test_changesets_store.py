"""Change-package store — schema create + the Phase-1 capture/read surface, on SQLite."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.changesets.db import ChangeSetDatabase
from liberty.changesets import store
from liberty.changesets.models import EntryStatus, Operation, PackageKind, PackageStatus
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


@pytest.mark.asyncio
async def test_lifecycle_submit_then_approve_opens_fresh_draft() -> None:
    db = await _db()
    # an empty draft can't be submitted
    async with db.session() as s:
        pkg = await store.get_or_create_active_package(s, "jde", user="u")
    async with db.session() as s:
        with pytest.raises(store.LifecycleError):
            await store.submit_package(s, pkg.id, user="u")
    # add a change, then submit → pending
    await store.capture(db, "jde", connector="jde", query="f_post", operation="INSERT")
    async with db.session() as s:
        active = await store.get_active_package(s, "jde")
        p = await store.submit_package(s, active.id, user="op")
        assert p.status == PackageStatus.PENDING.value and p.submitted_by == "op"
        pkg_id = active.id
    # re-submit fails (not a draft anymore)
    async with db.session() as s:
        with pytest.raises(store.LifecycleError):
            await store.submit_package(s, pkg_id, user="op")
    # approve → approved
    async with db.session() as s:
        p = await store.approve_package(s, pkg_id, user="boss")
        assert p.status == PackageStatus.APPROVED.value and p.approved_by == "boss"
    # the next change opens a NEW draft (the approved one is closed)
    await store.capture(db, "jde", connector="jde", query="f_put", operation="UPDATE")
    async with db.session() as s:
        fresh = await store.get_active_package(s, "jde")
        assert fresh is not None and fresh.id != pkg_id


@pytest.mark.asyncio
async def test_lifecycle_exclude_entry_and_reject() -> None:
    db = await _db()
    eid = await store.capture(db, "jde", connector="jde", query="f_post", operation="INSERT")
    async with db.session() as s:
        pkg_id = (await store.get_active_package(s, "jde")).id
    async with db.session() as s:
        e = await store.set_entry_excluded(s, eid, excluded=True)
        assert e.status == EntryStatus.EXCLUDED.value
    # all entries excluded → nothing to submit
    async with db.session() as s:
        with pytest.raises(store.LifecycleError):
            await store.submit_package(s, pkg_id, user="u")
    async with db.session() as s:
        await store.set_entry_excluded(s, eid, excluded=False)
    async with db.session() as s:
        assert (await store.submit_package(s, pkg_id, user="u")).status == PackageStatus.PENDING.value
    async with db.session() as s:
        assert (await store.reject_package(s, pkg_id, user="boss")).status == PackageStatus.REJECTED.value
    # entries of a non-draft package can't be toggled
    async with db.session() as s:
        with pytest.raises(store.LifecycleError):
            await store.set_entry_excluded(s, eid, excluded=True)


@pytest.mark.asyncio
async def test_mark_exported_requires_approved() -> None:
    db = await _db()
    await store.capture(db, "jde", connector="jde", query="f_post", operation="INSERT")
    async with db.session() as s:
        pid = (await store.get_active_package(s, "jde")).id
    async with db.session() as s:                       # draft can't be exported
        with pytest.raises(store.LifecycleError):
            await store.mark_exported(s, pid)
    async with db.session() as s:
        await store.submit_package(s, pid, user="u")
    async with db.session() as s:
        await store.approve_package(s, pid, user="b")
    async with db.session() as s:
        p = await store.mark_exported(s, pid)
        assert p.status == PackageStatus.EXPORTED.value and p.exported_at is not None
    async with db.session() as s:                       # re-export allowed
        assert (await store.mark_exported(s, pid)).status == PackageStatus.EXPORTED.value


@pytest.mark.asyncio
async def test_applied_bundle_log_record_find_list() -> None:
    """The target-side import log: record an apply, find a prior apply by checksum (the re-apply
    warning), and list newest-first."""
    db = await _db()
    async with db.session() as s:
        await store.record_applied_bundle(
            s, source_package_id="p1", name="Package 1 · jdedwards", application="jdedwards",
            checksum="abc123", op_count=3, status="applied", summary={"applied": 3}, details=[{"status": "applied"}], user="admin",
        )
    # find by checksum → the recorded apply; unknown checksum → None
    async with db.session() as s:
        prev = await store.find_applied_by_checksum(s, "abc123")
        assert prev is not None and prev.name == "Package 1 · jdedwards" and prev.applied_by == "admin"
        assert await store.find_applied_by_checksum(s, "nope") is None
        assert await store.find_applied_by_checksum(s, None) is None
    # a second apply (partial) then list newest-first, filtered by application
    async with db.session() as s:
        await store.record_applied_bundle(
            s, source_package_id="p2", name="Package 2 · jdedwards", application="jdedwards",
            checksum="def456", op_count=5, status="partial", summary={"applied": 4, "conflict": 1}, details=None, user="admin",
        )
    async with db.session() as s:
        rows = await store.list_applied_bundles(s, application="jdedwards")
        assert [r.name for r in rows] == ["Package 2 · jdedwards", "Package 1 · jdedwards"]
        assert rows[0].status == "partial"
        assert await store.list_applied_bundles(s, application="other") == []


# ── custom packages + move/delete entries ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_custom_package_coexists_with_auto_draft() -> None:
    """A custom holding package is a draft too, but kind='custom' — so it doesn't trip the
    one-auto-draft-per-application invariant, and get_active_package never returns it."""
    db = await _db()
    await store.capture(db, "jde", connector="jde", query="f_post", operation="INSERT")
    async with db.session() as s:
        auto = await store.get_active_package(s, "jde")
        assert auto is not None and auto.kind == PackageKind.AUTO.value
        custom = await store.create_custom_package(s, "jde", name="Future work", user="op")
        assert custom.kind == PackageKind.CUSTOM.value and custom.status == PackageStatus.DRAFT.value
        assert custom.id != auto.id
    # the auto-capture target is still the AUTO draft, never the custom one
    async with db.session() as s:
        active = await store.get_active_package(s, "jde")
        assert active.id == auto.id
        pkgs = await store.list_packages(s, application="jde")
        assert len(pkgs) == 2


@pytest.mark.asyncio
async def test_move_entries_into_custom_package() -> None:
    db = await _db()
    await store.capture(db, "jde", connector="jde", query="f_post", operation="INSERT",
                        entity="role", entity_key={"AUUSER": "R1"})
    await store.capture(db, "jde", connector="jde", query="f_post", operation="INSERT",
                        entity="role", entity_key={"AUUSER": "R2"})
    async with db.session() as s:
        auto = await store.get_active_package(s, "jde")
        custom = await store.create_custom_package(s, "jde", name="Parked", user="op")
        full = await store.get_package(s, auto.id)
        move_id = [e.id for e in full.entries if (e.entity_key or {}).get("AUUSER") == "R2"]
        dest, moved = await store.move_entries(s, entry_ids=move_id, dest_package_id=custom.id)
        assert moved == 1 and dest.id == custom.id
    async with db.session() as s:
        auto_full = await store.get_package(s, auto.id)
        custom_full = await store.get_package(s, custom.id)
        assert [(e.entity_key or {}).get("AUUSER") for e in auto_full.entries] == ["R1"]
        assert [(e.entity_key or {}).get("AUUSER") for e in custom_full.entries] == ["R2"]
        assert custom_full.entries[0].seq == 1  # re-sequenced at the destination's tail


@pytest.mark.asyncio
async def test_move_rejects_cross_application_and_frozen_source() -> None:
    db = await _db()
    await store.capture(db, "appA", connector="c", query="f_post", operation="INSERT", entity="x", entity_key={"k": "1"})
    await store.capture(db, "appB", connector="c", query="f_post", operation="INSERT", entity="x", entity_key={"k": "2"})
    async with db.session() as s:
        a = await store.get_active_package(s, "appA")
        b = await store.get_active_package(s, "appB")
        a_full = await store.get_package(s, a.id)
        # cross-application move is refused
        with pytest.raises(store.LifecycleError):
            await store.move_entries(s, entry_ids=[a_full.entries[0].id], dest_package_id=b.id)
    # submitting appA freezes it → can't move its entries out
    await store.capture(db, "appA", connector="c", query="f_put", operation="UPDATE", entity="x", entity_key={"k": "1"})
    async with db.session() as s:
        a = await store.get_active_package(s, "appA")
        await store.submit_package(s, a.id, user="op")
        custom = await store.create_custom_package(s, "appA", name="P", user="op")
        a_full = await store.get_package(s, a.id)
        with pytest.raises(store.LifecycleError):
            await store.move_entries(s, entry_ids=[a_full.entries[0].id], dest_package_id=custom.id)


@pytest.mark.asyncio
async def test_delete_entries_permanently_drops_them() -> None:
    db = await _db()
    await store.capture(db, "jde", connector="jde", query="f_post", operation="INSERT", entity="r", entity_key={"k": "1"})
    await store.capture(db, "jde", connector="jde", query="f_delete", operation="DELETE", entity="r", entity_key={"k": "1"})
    async with db.session() as s:
        auto = await store.get_active_package(s, "jde")
        full = await store.get_package(s, auto.id)
        ids = [e.id for e in full.entries]
        deleted = await store.delete_entries(s, entry_ids=ids)
        assert deleted == 2
    async with db.session() as s:
        full = await store.get_package(s, auto.id)
        assert full.entries == []


@pytest.mark.asyncio
async def test_rename_only_drafts() -> None:
    db = await _db()
    async with db.session() as s:
        custom = await store.create_custom_package(s, "jde", name="Old", user="op")
        renamed = await store.rename_package(s, custom.id, name="New name", description="why")
        assert renamed.name == "New name" and renamed.description == "why"
    # freeze it, then rename is refused
    await store.capture(db, "jde", connector="jde", query="f_post", operation="INSERT")
    async with db.session() as s:
        await store.move_entries(
            s, entry_ids=[(await store.get_package(s, (await store.get_active_package(s, "jde")).id)).entries[0].id],
            dest_package_id=custom.id,
        )
        await store.submit_package(s, custom.id, user="op")
        with pytest.raises(store.LifecycleError):
            await store.rename_package(s, custom.id, name="nope")
