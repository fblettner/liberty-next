"""``/admin`` — config version history (filesystem snapshots) (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request, status
from fastapi.responses import Response
from liberty.web.admin._base import Superuser, router
from liberty.web.admin.bundles import _BUNDLE_PREFIX, reload_connectors


# ── config version history (filesystem snapshots; see liberty.versioning) ─────────────────
@router.get("/config/raw", summary="Read a config file (raw)")
async def read_config_raw(request: Request, _: Superuser, file: str) -> Response:
    """The LIVE bytes of a config file (e.g. ``screens.toml``) — read-only, for diffing a stored
    version against the current file. (The legacy raw *editor* was removed; this read-only peek is
    safe and never writes.) Resolved under the config dir; path-traversal is rejected."""
    base = request.app.state.settings.connectors.config_path.parent.resolve()
    target = (base / file).resolve()
    if base not in target.parents or not target.is_file():   # must live under the config dir
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="file not found")
    return Response(content=target.read_bytes(), media_type="text/plain; charset=utf-8")


@router.get("/config/versions", summary="List config versions")
async def list_config_versions(request: Request, _: Superuser, file: str | None = None) -> dict[str, object]:
    """Snapshot history — every version of *file* (e.g. ``screens.toml``), newest first; or every
    version across all config files when *file* is omitted."""
    store = getattr(request.app.state, "config_versions", None)
    if store is None:
        return {"versions": []}
    base = request.app.state.settings.connectors.config_path.parent
    versions = store.list_versions((base / file) if file else None)
    # Screen+dependency bundles live in the same store under a ``screen:`` synthetic key — keep them
    # out of the file-history list (they have their own /config/screen-versions surface).
    return {"versions": [v.to_dict() for v in versions if not v.rel_path.startswith(_BUNDLE_PREFIX)]}


@router.get("/config/versions/{version_id}/content", summary="Get a version's content")
async def config_version_content(version_id: int, request: Request, _: Superuser) -> Response:
    """A version's raw TOML text — the frontend diffs two of these client-side (Monaco)."""
    store = getattr(request.app.state, "config_versions", None)
    data = store.content(version_id) if store is not None else None
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version not found")
    return Response(content=data, media_type="text/plain; charset=utf-8")


@router.get("/config/versions/{version_id}/download", summary="Download a version")
async def config_version_download(version_id: int, request: Request, _: Superuser) -> Response:
    store = getattr(request.app.state, "config_versions", None)
    v = store.get(version_id) if store is not None else None
    data = store.content(version_id) if store is not None else None
    if v is None or data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version not found")
    stem, suffix = Path(v.rel_path).stem, Path(v.rel_path).suffix
    fname = f"{stem}.v{v.version_num}{suffix}"
    return Response(content=data, media_type="application/toml",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/config/versions/{version_id}/restore", summary="Restore a version")
async def restore_config_version(version_id: int, request: Request, principal: Superuser) -> dict[str, object]:
    """Write a version's content back to its live file (snapshotting the current one first), then
    reload so it takes effect immediately. Returns the restored file + the reload summary."""
    store = getattr(request.app.state, "config_versions", None)
    if store is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="config versioning is unavailable")
    live = store.restore(version_id, who=principal.username)
    if live is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version not found")
    reload_result = await reload_connectors(request, principal)
    return {"restored": live.name, "reload": reload_result}


@router.delete("/config/versions/{version_id}", summary="Delete a config version")
async def delete_config_version(version_id: int, request: Request, _: Superuser) -> dict[str, object]:
    """Permanently delete one file-version snapshot (file + index row). Screen bundles are deleted via
    their own endpoint — this 404s on a bundle id so the two surfaces stay separate. Does not touch
    the live config."""
    store = getattr(request.app.state, "config_versions", None)
    if store is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="config versioning is unavailable")
    v = store.get(version_id)
    if v is None or v.rel_path.startswith(_BUNDLE_PREFIX):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="version not found")
    store.delete(version_id)
    return {"deleted": version_id}


@router.delete("/config/versions", summary="Clear a file's version history")
async def clear_config_versions(request: Request, _: Superuser, file: str) -> dict[str, object]:
    """Delete EVERY stored version of one config file. The live file is untouched — only its history
    is cleared."""
    store = getattr(request.app.state, "config_versions", None)
    if store is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="config versioning is unavailable")
    base = request.app.state.settings.connectors.config_path.parent
    rel = store._rel(base / file)
    if rel.startswith(_BUNDLE_PREFIX):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="file not found")
    return {"deleted": store.delete_key(rel)}
