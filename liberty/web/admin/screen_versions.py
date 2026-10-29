"""``/admin`` — screen + dependency bundle versions (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import Response
from liberty.web.admin._base import Superuser, _snapshot_all_config, router
from liberty.web.admin.bundles import _BUNDLE_PREFIX, _screen_bundle_key, reload_connectors


# ── screen + dependency bundle versions ───────────────────────────────────────────────────
@router.get("/config/screen-versions", summary="List screen bundle versions")
async def list_screen_versions(request: Request, _: Superuser,
                               app: str | None = None, screen: str | None = None) -> dict[str, object]:
    """Screen+dependency bundle history. Each version is the pre-save dependency closure of a screen,
    captured automatically on every screen save. Returns ``{bundles: [{app, screen, key, versions:[…]}]}``
    grouped by screen, newest version first. Filter to one screen with ``app`` + ``screen``."""
    store = getattr(request.app.state, "config_versions", None)
    if store is None:
        return {"bundles": []}
    want = _screen_bundle_key(app, screen) if app and screen else None
    groups: dict[str, dict[str, Any]] = {}
    for v in store.list_versions():
        if not v.rel_path.startswith(_BUNDLE_PREFIX):
            continue
        if want is not None and v.rel_path != want:
            continue
        # rel_path == "screen:<app>:<id>" — id may contain ':' in theory, so split with maxsplit.
        rest = v.rel_path[len(_BUNDLE_PREFIX):]
        bapp, _, bscreen = rest.partition(":")
        g = groups.setdefault(v.rel_path, {"app": bapp, "screen": bscreen, "key": v.rel_path, "versions": []})
        g["versions"].append(v.to_dict())
    return {"bundles": list(groups.values())}


@router.get("/config/screen-versions/{version_id}/download", summary="Download a screen bundle")
async def screen_version_download(version_id: int, request: Request, _: Superuser) -> Response:
    """The bundle ZIP for one version — a deployable package (the same shape /admin/build-package
    produces) of the screen + its dependency closure at that point in time."""
    store = getattr(request.app.state, "config_versions", None)
    v = store.get(version_id) if store is not None else None
    data = store.content(version_id) if store is not None else None
    if v is None or data is None or not v.rel_path.startswith(_BUNDLE_PREFIX):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="bundle version not found")
    safe = v.rel_path[len(_BUNDLE_PREFIX):].replace(":", "_")
    fname = f"{safe}.v{v.version_num}.zip"
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/config/screen-versions/{version_id}/contents", summary="Inspect a screen bundle")
async def screen_version_contents(version_id: int, request: Request, _: Superuser) -> dict[str, Any]:
    """The dependency closure captured in a bundle version — ``{app, screen, version, created_at,
    manifest}`` where ``manifest`` is the same ``{by_kind, counts, deps, missing, warnings}`` shape
    the dependency-inspect UI renders, read back from the ZIP's embedded ``manifest.json``. Lets the
    history view expand a row into its full content tree without rebuilding anything."""
    import io
    import json
    import zipfile
    store = getattr(request.app.state, "config_versions", None)
    v = store.get(version_id) if store is not None else None
    data = store.content(version_id) if store is not None else None
    if v is None or data is None or not v.rel_path.startswith(_BUNDLE_PREFIX):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="bundle version not found")
    rest = v.rel_path[len(_BUNDLE_PREFIX):]
    bapp, _sep, bscreen = rest.partition(":")
    manifest: dict[str, Any] = {"by_kind": {}, "counts": {}, "deps": [], "missing": [], "warnings": []}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except (KeyError, zipfile.BadZipFile, ValueError):
        pass  # bundle captured before manifest.json was embedded — show an empty tree
    return {"app": bapp, "screen": bscreen, "version": v.version_num,
            "created_at": v.created_at, "manifest": manifest}


@router.post("/config/screen-versions/{version_id}/restore", summary="Restore a screen bundle")
async def restore_screen_version(version_id: int, request: Request, principal: Superuser) -> dict[str, object]:
    """Re-apply a screen's stored dependency bundle — reverts the screen *and* its captured
    dependencies (queries / lookups / sequences / DD entries) together. Snapshots ALL config first
    (so the restore is itself undoable), then applies the bundle with the ``overwrite`` strategy:
    entities flagged ``override = true`` on this install are PRESERVED (a customer fork survives the
    revert). Reloads afterwards so the change is live."""
    store = getattr(request.app.state, "config_versions", None)
    if store is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="config versioning is unavailable")
    v = store.get(version_id)
    blob = store.content(version_id)
    if v is None or blob is None or not v.rel_path.startswith(_BUNDLE_PREFIX):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="bundle version not found")
    _snapshot_all_config(request)  # the bundle apply may touch several files — capture the pre-state
    from liberty.web.package_import import apply_package_zip
    try:
        report = apply_package_zip(zip_bytes=blob, strategy="overwrite", settings=request.app.state.settings)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    reload_result = await reload_connectors(request, principal)
    return {"restored": v.rel_path, "version": v.version_num, "report": report.to_dict(), "reload": reload_result}


@router.delete("/config/screen-versions/{version_id}", summary="Delete a screen bundle version")
async def delete_screen_version(version_id: int, request: Request, _: Superuser) -> dict[str, object]:
    """Permanently delete one screen-bundle snapshot. 404s on a non-bundle id (use the file-version
    endpoint for those)."""
    store = getattr(request.app.state, "config_versions", None)
    if store is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="config versioning is unavailable")
    v = store.get(version_id)
    if v is None or not v.rel_path.startswith(_BUNDLE_PREFIX):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="bundle version not found")
    store.delete(version_id)
    return {"deleted": version_id}


@router.delete("/config/screen-versions", summary="Clear a screen's bundle history")
async def clear_screen_versions(request: Request, _: Superuser, app: str, screen: str) -> dict[str, object]:
    """Delete EVERY bundle version for one screen — its whole dependency-snapshot history."""
    store = getattr(request.app.state, "config_versions", None)
    if store is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="config versioning is unavailable")
    return {"deleted": store.delete_key(_screen_bundle_key(app, screen))}
