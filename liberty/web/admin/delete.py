"""``/admin`` — delete with dependencies (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import tomlkit
from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, _snapshot_all_config, _snapshot_config, router
from liberty.web.admin.bundles import reload_connectors
from pydantic import BaseModel, ValidationError

from liberty.charts import load_charts
from liberty.charts.config import ChartsFile
from liberty.config import (
    substitute_env,
)
from liberty.connectors.config import (
    ConnectorsFile,
)
from liberty.dashboards import load_dashboards
from liberty.dashboards.config import DashboardsFile
from liberty.screens import load_screens
from liberty.screens.config import ScreensFile
from liberty.web.delete_with_deps import Seed as DeleteSeed
from liberty.web.delete_with_deps import plan_delete_with_deps

# ── Delete with dependencies ────────────────────────────────────────────────────────────────
# Mirror of clone-with-deps: destructive. Two endpoints — a PREVIEW one (returns the plan
# so the operator sees what'll happen before confirming) + an APPLY one (executes). The
# planner is in liberty/web/delete_with_deps.py; it only marks queries as deletable when
# every reference to them comes back from the entity being deleted.


class DeleteWithDepsBody(BaseModel):
    kind: str                               # 'screen' | 'chart' | 'dashboard'
    name: str
    scope: str | None = None
    options: dict[str, bool] = {}           # {delete_queries: bool}


@router.post("/delete-with-deps/preview", summary="Preview delete-with-deps")
async def delete_with_deps_preview(body: DeleteWithDepsBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Compute (without applying) the deletion plan: which queries are safely orphaned by
    deleting *body.name* and which would be preserved (still referenced by other entities).
    The frontend uses this to show the operator a preview before they confirm."""
    if body.kind not in ("screen", "chart", "dashboard"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"delete-with-deps doesn't support kind {body.kind!r}")
    plan = plan_delete_with_deps(
        request.app.state,
        seed=DeleteSeed(kind=body.kind, name=body.name, scope=body.scope),
        options=body.options,
    )
    return plan.to_dict()


@router.post("/delete-with-deps", summary="Delete entity with dependencies")
async def delete_with_deps_endpoint(body: DeleteWithDepsBody, request: Request, _: Superuser) -> dict[str, object]:
    """Delete *body.name* (a screen / chart / dashboard) and — when
    ``options.delete_queries`` is set — every query exclusively used by it. Queries
    referenced by other entities are PRESERVED + listed in the response.

    Order matters: the seed entity is removed FIRST (so its refs to the soon-to-be-
    deleted queries are gone), then the queries themselves. Hot-reload sees consistent
    state at each step. The final ``/admin/reload`` stitches the live registries."""
    _snapshot_all_config(request)  # cross-file mutation — snapshot all config first
    if body.kind not in ("screen", "chart", "dashboard"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"delete-with-deps doesn't support kind {body.kind!r}")
    seed = DeleteSeed(kind=body.kind, name=body.name, scope=body.scope)
    plan = plan_delete_with_deps(request.app.state, seed=seed, options=body.options)
    summaries: list[str] = []
    # Pass 1 — delete the seed entity. References from the seed to the soon-to-be-
    # deleted queries vanish at this step, so the connector reload (pass 2) doesn't
    # see broken refs even if hot-reload fires mid-flight.
    if body.kind == "screen":
        await _delete_screen(request, body.scope, body.name)
        summaries.append(f"deleted screen {body.scope}.{body.name}")
    elif body.kind == "chart":
        await _delete_chart(request, body.scope, body.name)
        summaries.append(f"deleted chart {body.scope}.{body.name}")
    elif body.kind == "dashboard":
        await _delete_dashboard(request, body.scope, body.name)
        summaries.append(f"deleted dashboard {body.scope or '_'}.{body.name}")
    # Pass 2 — delete each safe query (grouped by connector for one PUT per connector).
    by_conn: dict[str, list[str]] = {}
    for conn, qname in plan.delete_queries:
        by_conn.setdefault(conn, []).append(qname)
    for conn, qnames in by_conn.items():
        await _delete_connector_queries(request, conn, qnames)
        summaries.append(f"deleted {len(qnames)} quer{'y' if len(qnames) == 1 else 'ies'} on {conn}")
    await reload_connectors(request, _)  # type: ignore[arg-type]
    return {
        "deleted": True,
        "summary": "; ".join(summaries),
        "plan": plan.to_dict(),
    }


async def _delete_screen(request: Request, app: str | None, screen_id: str) -> None:
    """Remove ``[screens.<app>.<screen_id>]`` from screens.toml. Validates the resulting
    file before writing — bails with 422 if the delete would leave dangling refs the
    Pydantic validator catches."""
    if not app:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="screen delete needs an app scope")
    path = Path(request.app.state.settings.screens.config_path)
    registry = load_screens(path)
    doc = {a: {sid: s.model_dump(exclude_defaults=False, exclude_none=True) for sid, s in screens.items()}
           for a, screens in registry.screens.items()}
    app_doc = doc.get(app) or {}
    if screen_id not in app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"screen {app}.{screen_id} not found")
    del app_doc[screen_id]
    if not app_doc:
        del doc[app]
    else:
        doc[app] = app_doc
    try:
        ScreensFile.model_validate({"screens": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"delete leaves screens invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"screens": doc})
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")


async def _delete_chart(request: Request, scope: str | None, chart_id: str) -> None:
    """Remove ``[charts.<scope>.<chart_id>]`` from charts.toml."""
    if not scope:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="chart delete needs a scope")
    path = Path(request.app.state.settings.charts.config_path)
    cfg = load_charts(path)
    doc = {s: {cid: c.model_dump(exclude_defaults=True, exclude_none=True) for cid, c in by_id.items()}
           for s, by_id in cfg.charts.items()}
    scope_doc = doc.get(scope) or {}
    if chart_id not in scope_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"chart {scope}.{chart_id} not found")
    del scope_doc[chart_id]
    if not scope_doc:
        del doc[scope]
    else:
        doc[scope] = scope_doc
    try:
        ChartsFile.model_validate({"charts": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"delete leaves charts invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"charts": doc})
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")


async def _delete_dashboard(request: Request, scope: str | None, dashboard_id: str) -> None:
    """Remove ``[dashboards.<scope>.<dashboard_id>]`` from dashboards.toml."""
    path = Path(request.app.state.settings.dashboards.config_path)
    cfg = load_dashboards(path)
    doc = {s: {did: d.model_dump(exclude_defaults=True, exclude_none=True) for did, d in by_id.items()}
           for s, by_id in cfg.dashboards.items()}
    bucket = scope or "_"
    scope_doc = doc.get(bucket) or {}
    if dashboard_id not in scope_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"dashboard {bucket}.{dashboard_id} not found")
    del scope_doc[dashboard_id]
    if not scope_doc:
        del doc[bucket]
    else:
        doc[bucket] = scope_doc
    try:
        DashboardsFile.model_validate({"dashboards": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"delete leaves dashboards invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"dashboards": doc})
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")


async def _delete_connector_queries(request: Request, connector: str, query_names: list[str]) -> None:
    """Remove *query_names* from ``[connectors.<connector>]`` via tomlkit so other connectors /
    comments in connectors.toml round-trip untouched.

    *query_names* are flat synthesised names, so a name is matched against all four sections:
    a custom ``queries`` / ``sequences`` / ``lookups`` entry is dropped by name, and a table
    slot (``<base>_<crud>``) is removed from its :class:`TableDef` — a table left with no
    slots is dropped entirely (an operator deleting every CRUD op of a table means to remove
    the table)."""
    path = Path(request.app.state.settings.connectors.config_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    connectors_tbl = doc.get("connectors")
    if connectors_tbl is None or connector not in connectors_tbl:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"connector {connector!r} not found")
    conn_tbl = connectors_tbl[connector]
    to_drop = set(query_names)
    # Flat sections — drop standalone entries by name.
    for section in ("queries", "sequences", "lookups"):
        lst = conn_tbl.get(section)
        if isinstance(lst, list):
            conn_tbl[section] = [q for q in lst if not (isinstance(q, dict) and q.get("name") in to_drop)]
    # Tables — drop the matching ``<base>_<crud>`` slot off each table; remove a table that's
    # left with no slots at all.
    tables = conn_tbl.get("tables")
    if isinstance(tables, list):
        kept_tables = []
        for t in tables:
            if not isinstance(t, dict):
                kept_tables.append(t)
                continue
            base = t.get("name")
            for crud in ("get", "put", "post", "delete"):
                if f"{base}_{crud}" in to_drop:
                    t.pop(crud, None)
            if any(crud in t for crud in ("get", "put", "post", "delete")):
                kept_tables.append(t)
        conn_tbl["tables"] = kept_tables
    # Re-validate the connector after the drops — refuses to write a connector that's now
    # invalid (e.g. a sequence config that pointed at one of the deleted queries).
    try:
        # Re-load the file with the dropped queries and validate the connector.
        normalised = tomllib.loads(tomlkit.dumps(doc))
        cfg = ConnectorsFile.model_validate(substitute_env(normalised))
        if connector not in cfg.connectors:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail=f"connector {connector!r} disappeared from validation")
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"delete leaves connector {connector!r} invalid: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
