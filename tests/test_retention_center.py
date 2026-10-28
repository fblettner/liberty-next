"""The change-package purge of the Retention page (:mod:`liberty.retention_center`): only
exported / promoted packages (with their entries) and import logs past the age go; a dry run
counts without deleting; 0 days keeps everything."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import func, select

from liberty.changesets.db import ChangeSetDatabase
from liberty.changesets.models import AppliedBundle, ChangeEntry, ChangePackage
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.retention_center import purge_change_packages

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db(tmp_path):
    pools = PoolRegistry({"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'cs.db'}")})
    d = ChangeSetDatabase(pools, "default")
    await d.create_schema()
    yield d
    await pools.dispose()


async def _seed(db) -> None:
    old, recent = NOW - timedelta(days=400), NOW - timedelta(days=10)
    async with db.session() as s:
        for pid, status, at in (("old-exported", "exported", old), ("old-promoted", "promoted", old),
                                ("old-draft", "draft", old), ("recent-exported", "exported", recent)):
            s.add(ChangePackage(id=pid, application="app", name=pid, status=status, created_at=at,
                                exported_at=at if status == "exported" else None, promoted_at=at if status == "promoted" else None))
        await s.flush()
        s.add(ChangeEntry(package_id="old-exported", seq=1, connector="c", query="q_put", operation="update"))
        s.add(AppliedBundle(name="old bundle", checksum="x" * 16, applied_at=old))
        s.add(AppliedBundle(name="recent bundle", checksum="y" * 16, applied_at=recent))


async def _count(db, model) -> int:
    async with db.session() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar()


async def test_purge_change_packages(db) -> None:
    await _seed(db)
    assert await purge_change_packages(db, 0, now=NOW) == {"packages": 0, "import_logs": 0}
    assert await purge_change_packages(db, 365, dry_run=True, now=NOW) == {"packages": 2, "import_logs": 1}
    assert await _count(db, ChangePackage) == 4 and await _count(db, AppliedBundle) == 2      # dry run: nothing gone
    assert await purge_change_packages(db, 365, now=NOW) == {"packages": 2, "import_logs": 1}
    async with db.session() as s:
        left = sorted(r[0] for r in (await s.execute(select(ChangePackage.id))).all())
    assert left == ["old-draft", "recent-exported"]                     # a draft is never purged
    assert await _count(db, ChangeEntry) == 0 and await _count(db, AppliedBundle) == 1
