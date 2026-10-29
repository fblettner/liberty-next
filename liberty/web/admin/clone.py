"""``/admin`` — clone with dependencies (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, _snapshot_all_config, _snapshot_config, router
from liberty.web.admin.bundles import reload_connectors
from liberty.web.admin.proposals import _apply_connector_queries, _apply_screen
from pydantic import BaseModel, ValidationError

from liberty.charts import load_charts
from liberty.charts.config import ChartsFile, parse_charts
from liberty.dashboards import load_dashboards
from liberty.dashboards.config import DashboardsFile, parse_dashboards
from liberty.web.clone_with_deps import Seed as CloneSeed
from liberty.web.clone_with_deps import clone_with_deps

# ── Clone with dependencies ─────────────────────────────────────────────────────────────────
# Clone a screen / chart / dashboard PLUS optionally duplicate the referenced queries
# under suffixed names, with the clone's own refs rewritten to point at the new query
# names. See liberty/web/clone_with_deps.py for the walker — this endpoint is the thin
# write wrapper that applies the result through the existing parsed-config write paths.



class CloneWithDepsBody(BaseModel):
    kind: str                                  # 'screen' | 'chart' | 'dashboard'
    name: str                                  # source entity id
    scope: str | None = None                   # app for screen / chart / dashboard scope
    new_name: str                              # the cloned entity's id
    suffix: str | None = None                  # appended to each cloned query name; defaults to the diff between new_name and name
    options: dict[str, bool] = {}              # {clone_queries: bool}


@router.post("/clone-with-deps", summary="Clone entity with dependencies")
async def clone_with_deps_endpoint(body: CloneWithDepsBody, request: Request, _: Superuser) -> dict[str, object]:
    """Clone *body.name* under *body.new_name* + (per options) duplicate every referenced
    query under ``<original>_<suffix>``. Rewrites the cloned entity's refs so it's a
    self-contained fork.

    Atomic per-file: dependency queries are written via _apply_connector_queries (grouped
    by connector) and the seed via the matching _apply_<kind>. Hits /admin/reload at the
    end so the new entries are live."""
    _snapshot_all_config(request)  # cross-file mutation — snapshot all config first
    if body.kind not in ("screen", "chart", "dashboard"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"clone-with-deps doesn't support kind {body.kind!r}")
    seed = CloneSeed(kind=body.kind, name=body.name, scope=body.scope)
    result = clone_with_deps(
        request.app.state,
        seed=seed, new_name=body.new_name, suffix=body.suffix, options=body.options,
    )
    # Apply order matters: write QUERIES first, then the seed (screen / chart / dashboard)
    # last. Hot-reload watches the files individually; if the screen lands first while its
    # newly-renamed read_query / update_query / etc. doesn't exist yet on the connector,
    # the screen-only reload sees broken refs (the operator then has to restart to recover).
    # Writing queries first means: when the seed's reload fires, the connector already has
    # the cloned queries. The final explicit /admin/reload below stitches everything.
    queries_by_conn: dict[str, list[dict[str, Any]]] = {}
    seed_items: list[Any] = []
    summaries: list[str] = []
    for item in result.items:
        if item.kind == "query":
            cfg = dict(item.config)
            cfg["name"] = item.new_name
            queries_by_conn.setdefault(item.scope or "", []).append(cfg)
        else:
            seed_items.append(item)
    # Pass 1 — queries.
    for conn, queries in queries_by_conn.items():
        if conn:
            summaries.append(_apply_connector_queries(request, {"connector": conn, "queries": queries}))
    # Pass 2 — the seed entity (screen / chart / dashboard) referencing the now-written queries.
    for item in seed_items:
        if item.kind == "screen":
            payload = {"app": item.scope, "screen_id": item.new_name, "screen": {**item.config, "id": item.new_name}}
            summaries.append(_apply_screen(request, payload))
        elif item.kind == "chart":
            # Charts live at [charts.<scope>.<id>] — there's no dedicated apply helper
            # for one-off chart inserts, so we inline the write here (a dedicated
            # _apply_chart helper would be cleaner; follow-up).
            await _apply_chart_clone(request, item.scope, item.new_name, dict(item.config))
            summaries.append(f"cloned chart {item.scope}.{item.new_name}")
        elif item.kind == "dashboard":
            await _apply_dashboard_clone(request, item.scope, item.new_name, dict(item.config))
            summaries.append(f"cloned dashboard {item.scope or '_'}.{item.new_name}")
    await reload_connectors(request, _)  # type: ignore[arg-type]
    return {
        "cloned": True,
        "items": result.to_dict()["items"],
        "warnings": result.warnings,
        "summary": "; ".join(summaries),
    }


async def _apply_chart_clone(request: Request, scope: str | None, new_id: str, config: dict[str, Any]) -> None:
    """Insert ONE chart at ``[charts.<scope>.<new_id>]`` — same shape an apply-proposal
    for charts would use; minimal helper since clone-with-deps is the only caller today."""
    if not scope:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="chart clone needs a scope (= connector)")
    path = Path(request.app.state.settings.charts.config_path)
    cfg = load_charts(path)
    doc: dict[str, Any] = {s: {cid: c.model_dump(exclude_defaults=True, exclude_none=True) for cid, c in by_id.items()}
                            for s, by_id in cfg.charts.items()}
    doc.setdefault(scope, {})[new_id] = {**config, "id": new_id}
    try:
        ChartsFile.model_validate({"charts": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"chart clone invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"charts": doc})
    parse_charts(tomllib.loads(new_text))
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")


async def _apply_dashboard_clone(request: Request, scope: str | None, new_id: str, config: dict[str, Any]) -> None:
    """Insert ONE dashboard at ``[dashboards.<scope>.<new_id>]`` — minimal helper, same
    pattern as the chart clone."""
    path = Path(request.app.state.settings.dashboards.config_path)
    cfg = load_dashboards(path)
    doc: dict[str, Any] = {s: {did: d.model_dump(exclude_defaults=True, exclude_none=True) for did, d in by_id.items()}
                            for s, by_id in cfg.dashboards.items()}
    bucket = scope or "_"
    doc.setdefault(bucket, {})[new_id] = {**config, "id": new_id}
    try:
        DashboardsFile.model_validate({"dashboards": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"dashboard clone invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"dashboards": doc})
    parse_dashboards(tomllib.loads(new_text))
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")
