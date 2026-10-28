"""``/admin/dev`` — the Developer page (C12): what the running code is made of. Superuser only.
``GET /config`` — every app.toml key with where the UI edits it (or why it stays in the file) and the
code that reads it. ``GET /api`` — every route: method, path, group, the access it requires. See :mod:`liberty.devref`."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.devref import api_reference, config_reference, data_model

router = APIRouter(prefix="/admin/dev", tags=["admin"])
Superuser = Annotated[Principal, Depends(require_superuser)]


@router.get("/config", summary="Every setting: where it is edited, who reads it")
async def get_config_reference(_: Superuser) -> dict[str, Any]:
    keys = await asyncio.to_thread(config_reference)   # a scan of the package's sources (once per process)
    gaps = [k["key"] for k in keys if not k["editor"] and not k["file_only"]]
    return {"keys": keys, "gaps": gaps, "editable": sum(1 for k in keys if k["editor"]), "total": len(keys)}


@router.get("/api", summary="Every route: method, path, group, access")
async def get_api_reference(request: Request, _: Superuser) -> dict[str, Any]:
    routes = api_reference(request.app)
    return {"routes": routes, "total": len(routes)}


@router.get("/data", summary="The data model: framework tables + each connector's tables")
async def get_data_model(request: Request, _: Superuser) -> dict[str, Any]:
    tables = await asyncio.to_thread(data_model, request.app.state)
    return {"tables": tables, "total": len(tables)}


@router.get("/reports", summary="The documentation reports and their sections")
async def list_doc_reports(_: Superuser, lang: str = "fr") -> dict[str, Any]:
    from liberty.docreport import catalog
    return {"reports": catalog(lang)}


@router.get("/reports/{kind}", summary="Download the implementation or the technical report (Markdown or PDF)")
async def get_doc_report(
    kind: str, request: Request, principal: Superuser, lang: str = "fr", format: str = "pdf",
    sections: str | None = None, date: str | None = None, api_detail: bool = False, snippets: bool = False,
) -> StreamingResponse:
    from liberty.docreport import CATALOG, build
    from liberty.reports.render import ReportsUnavailableError, render_content
    if kind not in CATALOG:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown report {kind!r}")
    if format not in ("pdf", "markdown"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="format must be pdf or markdown")
    wanted = [s for s in (sections or "").split(",") if s] or None
    content = await build(kind, request.app, lang=lang, sections=wanted, author=principal.username,
                          date_label=date or None, options={"api_detail": api_detail, "snippets": snippets})
    settings = request.app.state.settings
    branding = getattr(getattr(settings, "reports", None), "branding", None)
    try:
        body, content_type, filename = await asyncio.to_thread(
            render_content, content, "pdf" if format == "pdf" else "markdown",
            app_name=getattr(settings.app, "name", ""), report_title=content.title,
            branding=branding.model_dump() if branding is not None else {},
        )
    except ReportsUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return StreamingResponse(iter([body]), media_type=content_type,
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})
