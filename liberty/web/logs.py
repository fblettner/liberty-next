"""``/admin/logs`` — the Server log page's backend (C8): search the log file (level floor,
module, text, since), export the matches as text, download the files, read and change the
levels per module. Superuser only. See :mod:`liberty.logfile`."""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, StreamingResponse

from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.logfile import LEVELS, apply_levels, as_text, log_files, log_path, read_entries

router = APIRouter(prefix="/admin/logs", tags=["admin"])
Superuser = Annotated[Principal, Depends(require_superuser)]
_log = logging.getLogger(__name__)


def _files(request: Request):
    path = log_path(request.app.state.settings)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="the server log file is off ([app] log_file = \"\")")
    return path, log_files(path)


def _since(hours: float | None) -> str | None:
    if not hours:
        return None
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="milliseconds")


@router.get("", summary="Search the server log")
async def search_log(
    request: Request, _: Superuser, level: str | None = None, module: str | None = None, q: str | None = None,
    hours: float | None = 24, limit: int = 500,
) -> dict[str, Any]:
    """The entries matching the filters, newest first (``level`` a floor, ``module`` a logger
    prefix, ``q`` text in the message / logger / exception / request id, ``hours`` back), the
    modules seen among them, and the files on disk with their size."""
    import asyncio
    path, files = _files(request)
    limit = max(1, min(limit, 5000))
    entries, scanned = await asyncio.to_thread(read_entries, files, level=level, module=module, q=q, since=_since(hours), limit=limit)
    modules = sorted({".".join(str(e.get("logger", "")).split(".")[:2]) for e in entries if e.get("logger")})
    return {
        "entries": entries, "scanned": scanned, "truncated": len(entries) >= limit, "modules": modules,
        "files": [{"name": f.name, "size": f.stat().st_size} for f in files], "path": str(path),
    }


@router.get("/export", summary="Export the matching log entries")
async def export_log(
    request: Request, _: Superuser, level: str | None = None, module: str | None = None, q: str | None = None,
    hours: float | None = 24,
) -> PlainTextResponse:
    """The same search as text lines, oldest first — to attach to a support ticket."""
    import asyncio
    _path, files = _files(request)
    entries, _ = await asyncio.to_thread(read_entries, files, level=level, module=module, q=q, since=_since(hours), limit=100_000)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return PlainTextResponse("\n".join(as_text(e) for e in reversed(entries)) + "\n",
                             headers={"Content-Disposition": f'attachment; filename="liberty-log-{stamp}.txt"'})


@router.get("/download", summary="Download the log files")
async def download_log(request: Request, _: Superuser) -> StreamingResponse:
    """Every log file (the current one and the rotated ones) in a zip."""
    _path, files = _files(request)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, arcname=f.name)
    buf.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="liberty-logs-{stamp}.zip"'})


def _level_view(request: Request) -> dict[str, Any]:
    configured = dict(getattr(request.app.state.settings.app, "log_levels", {}) or {})
    # the loggers the server has created (the page's picker), the framework's and the apps' first
    names = sorted({n.split(".")[0] + ("." + n.split(".")[1] if n.startswith("liberty.") and n.count(".") >= 1 else "")
                    for n in logging.root.manager.loggerDict if not n.startswith(("asyncio", "concurrent", "urllib3", "charset_normalizer", "multipart", "PIL", "fontTools"))})
    current = {n: logging.getLevelName(logging.getLogger(n).level) for n in sorted(set(configured) | {"liberty"})}
    return {"levels": current, "configured": configured, "loggers": names, "choices": ["NOTSET", *LEVELS]}


@router.get("/levels", summary="Get the log levels per module")
async def get_levels(request: Request, _: Superuser) -> dict[str, Any]:
    return _level_view(request)


@router.post("/levels", summary="Change the log levels per module")
async def set_levels(request: Request, principal: Superuser, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """``{levels: {logger: level | "NOTSET"}, persist: bool}`` — applied at once; with ``persist``
    also written to ``[app] log_levels`` in app.toml (tomlkit, comments kept) so a restart keeps
    them. ``NOTSET`` returns the logger to inheriting its parent's level."""
    levels = body.get("levels") if isinstance(body, dict) else None
    if not isinstance(levels, dict) or not all(isinstance(k, str) and k and isinstance(v, str) for k, v in levels.items()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="`levels` must be an object of `{logger: level}`")
    try:
        applied = apply_levels(levels)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    settings = request.app.state.settings
    merged = {**(settings.app.log_levels or {}), **{k: v for k, v in applied.items() if v != "NOTSET"}}
    for k, v in applied.items():
        if v == "NOTSET":
            merged.pop(k, None)
    settings.app.log_levels = merged
    if body.get("persist"):
        import tomlkit

        from liberty.config import resolve_app_config_path
        path = resolve_app_config_path()
        doc = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()
        app_tbl = doc.setdefault("app", tomlkit.table())
        if merged:
            tbl = tomlkit.inline_table()
            tbl.update(merged)
            app_tbl["log_levels"] = tbl
        elif "log_levels" in app_tbl:
            del app_tbl["log_levels"]
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    _log.info("log levels changed by %s: %s%s", principal.username, applied, " (saved to app.toml)" if body.get("persist") else "")
    return _level_view(request)
