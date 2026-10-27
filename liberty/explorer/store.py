"""Saved analyses and dashboards of analyses, on the user-views store.

One row per object in ``ly2_user_view``: ``kind`` is ``analysis`` or ``explorer_dashboard``,
``view_key`` is ``<app>/<model>``, ``name`` the object's id (a slug of its name), ``payload`` the record::

    {id, name, description, audience, owner, shared, bundled, version, updated_at, definition}

A **shared** object is stored under the reserved username ``*`` (everyone with the app's explorer
permission lists it); a private one under its owner. Saving a shared object changes it for
everyone (the UI says so); deleting one is for its owner or a superuser. "Save as" makes a new
id (``-2``, ``-3``…) owned by the caller.

The **bundled catalogue** — ``[explorer.<app>.analyses.<id>]`` / ``.dashboards.<id>`` in
``explorer.toml``, each with a ``version`` — is applied by :func:`sync_bundled` at boot and on
reload: a new id is installed once (shared, ``bundled = version``); an installed copy is
refreshed with the shipped one only while it still carries the ``bundled`` marker, which a user
save drops — edited copies are frozen, deleted copies stay deleted.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from liberty.explorer.config import ExplorerFile

_log = logging.getLogger(__name__)

SHARED_USER = "*"
KIND_ANALYSIS = "analysis"
KIND_DASHBOARD = "explorer_dashboard"
KINDS = (KIND_ANALYSIS, KIND_DASHBOARD)
# The installed-ids marker: one row per app under the shared user, ``{"<kind>:<id>": version}`` —
# what nomaubl keeps in its ``.bundled`` file. An id in the marker whose row is gone was deleted
# by a user: it is not reinstalled.
KIND_MARKER = "explorer_bundled"


def slug(name: str) -> str:
    """``Qty by pôle`` → ``qty-by-pole``; accents stripped, 80 characters at most."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return (s or "analysis")[:80]


class AnalysisStore:
    """The explorer's view of :class:`liberty.userviews.store.UserViewStore`."""

    def __init__(self, views: Any) -> None:
        self._views = views

    async def list(self, app: str, kind: str, username: str) -> list[dict[str, Any]]:
        """Shared objects plus the caller's own, by name."""
        shared = await self._views.list_views(SHARED_USER, kind, app)
        mine = await self._views.list_views(username, kind, app)
        out = [self._public(r, shared=True) for r in shared] + [self._public(r, shared=False) for r in mine]
        return sorted(out, key=lambda x: (str(x.get("audience") or ""), str(x.get("name") or "").lower()))

    async def get(self, app: str, kind: str, username: str, obj_id: str) -> dict[str, Any] | None:
        for r in await self.list(app, kind, username):
            if r["id"] == obj_id:
                return r
        return None

    async def save(
        self, app: str, kind: str, username: str, *, obj_id: str | None, name: str, definition: dict[str, Any],
        description: str | None = None, audience: str | None = None, shared: bool = False, as_new: bool = False,
    ) -> dict[str, Any]:
        """Create or replace. A replace keeps the original owner and audience; the ``bundled``
        marker is dropped (the copy is now the user's). ``as_new`` always creates, with a free
        id derived from the name."""
        existing = await self.get(app, kind, username, obj_id) if (obj_id and not as_new) else None
        if existing is None:
            new_id = await self._free_id(app, kind, username, slug(name))
            record = {
                "id": new_id, "name": name, "description": description, "audience": audience, "owner": username,
                "shared": shared, "bundled": None, "version": 1, "updated_at": _now(), "definition": definition,
            }
        else:
            if existing["shared"] != shared:
                # moving between shared and private: delete the old row
                await self._views.delete_view(SHARED_USER if existing["shared"] else existing["owner"], kind, app, existing["id"])
            record = {
                **existing, "name": name, "description": description if description is not None else existing.get("description"),
                "audience": audience if audience is not None else existing.get("audience"), "shared": shared,
                "bundled": None, "version": int(existing.get("version") or 1) + 1, "updated_at": _now(), "definition": definition,
            }
            if existing["owner"] != username and not existing["shared"]:
                record["owner"] = username
        await self._views.save_view(SHARED_USER if shared else record["owner"], kind, app, record["id"], record)
        return record

    async def delete(self, app: str, kind: str, username: str, obj_id: str, *, superuser: bool = False) -> bool:
        existing = await self.get(app, kind, username, obj_id)
        if existing is None:
            return False
        if existing["owner"] != username and not superuser and not existing["shared"]:
            return False
        if existing["shared"] and existing["owner"] != username and not superuser:
            raise PermissionError(f"{obj_id!r} is shared by {existing['owner']}: only its owner or a superuser can delete it")
        return await self._views.delete_view(SHARED_USER if existing["shared"] else existing["owner"], kind, app, obj_id)

    async def _free_id(self, app: str, kind: str, username: str, base: str) -> str:
        taken = {r["id"] for r in await self.list(app, kind, username)}
        taken |= {r["name"] for r in await self._views.list_views(SHARED_USER, kind, app)}
        if base not in taken:
            return base
        n = 2
        while f"{base}-{n}" in taken:
            n += 1
        return f"{base}-{n}"

    @staticmethod
    def _public(row: dict[str, Any], *, shared: bool) -> dict[str, Any]:
        p = dict(row["payload"])
        p.setdefault("id", row["name"])
        p["shared"] = shared
        p.setdefault("updated_at", row.get("updated_at"))
        return p


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def migrate_keys(views: Any, explorer: ExplorerFile) -> int:
    """Objects saved when a model was the app itself (key ``<app>``) move to ``<app>/<model>``
    for an app that has exactly one model; returns the rows moved. Idempotent."""
    moved = 0
    for app, models in explorer.explorer.items():
        if len(models) != 1:
            continue
        new_key = f"{app}/{next(iter(models))}"
        for kind in (KIND_ANALYSIS, KIND_DASHBOARD, KIND_MARKER):
            moved += await views.rename_key(kind, app, new_key)
    if moved:
        _log.info("explorer: %d saved object(s) moved to the app/model key", moved)
    return moved


async def sync_bundled(views: Any, explorer: ExplorerFile) -> dict[str, int]:
    """Apply the bundled catalogue of every model; returns ``{installed, refreshed}``. The
    saved-object keys of the previous convention are migrated first."""
    await migrate_keys(views, explorer)
    installed = refreshed = 0
    for app_name, model_id, model in explorer.items():
        if not model.analyses and not model.dashboards:
            continue
        app = f"{app_name}/{model_id}"
        marker_rows = await views.list_views(SHARED_USER, KIND_MARKER, app)
        marker: dict[str, int] = dict(marker_rows[0]["payload"]) if marker_rows else {}
        changed = False
        for kind, items in ((KIND_ANALYSIS, model.analyses), (KIND_DASHBOARD, model.dashboards)):
            if not items:
                continue
            current = {r["name"]: r for r in await views.list_views(SHARED_USER, kind, app)}
            for obj_id, b in items.items():
                shipped = {
                    "id": obj_id, "name": b.label or obj_id, "description": b.description, "audience": b.audience, "owner": "bundled",
                    "shared": True, "bundled": b.version, "version": b.version, "updated_at": _now(), "definition": dict(b.definition),
                }
                mkey = f"{kind}:{obj_id}"
                row = current.get(obj_id)
                if row is None:
                    if mkey in marker:
                        continue          # installed before and deleted by a user: stays deleted
                    await views.save_view(SHARED_USER, kind, app, obj_id, shipped)
                    marker[mkey] = b.version
                    installed += 1
                    changed = True
                    continue
                stamp = row["payload"].get("bundled")
                if stamp is not None and int(stamp) < b.version:
                    await views.save_view(SHARED_USER, kind, app, obj_id, shipped)
                    refreshed += 1
                if marker.get(mkey) != b.version:
                    marker[mkey] = b.version
                    changed = True
        if changed:
            await views.save_view(SHARED_USER, KIND_MARKER, app, "installed", marker)
    if installed or refreshed:
        _log.info("explorer: bundled catalogue applied — %d installed, %d refreshed", installed, refreshed)
    return {"installed": installed, "refreshed": refreshed}
