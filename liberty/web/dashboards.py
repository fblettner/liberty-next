"""``/api/dashboards`` — operator-curated dashboard catalog.

| Route | Permission | Notes |
|---|---|---|
| ``GET /api/dashboards`` | authenticated | every dashboard, with widgets the caller can't read filtered out |
| ``GET /api/dashboards/{id}`` | authenticated | one dashboard, same filtering; 404 when the id doesn't exist |

Each widget is permission-gated by the underlying query's ``sql:<connector>:<query>``;
widgets the caller can't read are dropped from the catalog (the dashboard still surfaces
with the remaining widgets). A dashboard with *no* readable widgets is still listed so an
operator/admin who curated it sees the placeholder.

**Chart-widget resolution.** A chart widget can either reference a saved chart by id (the
``chart`` field, pulled from ``charts.toml``) or inline its own ``connector``/``query``/``spec``.
We resolve the reference *server-side* so the frontend gets a uniform shape — every chart
widget in the wire payload carries an inline ``connector``/``query``/``spec``, no second
fetch needed. A reference to a missing chart drops the widget with a logged warning.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from liberty.auth.dependencies import CurrentPrincipal
from liberty.auth.principal import Principal
from liberty.charts import ChartConfig, ChartsFile
from liberty.dashboards import ChartWidget, Dashboard, DashboardsFile, KpiWidget, TableWidget

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dashboards"])


def get_dashboards(request: Request) -> DashboardsFile:
    return request.app.state.dashboards


def _charts(request: Request) -> ChartsFile:
    return request.app.state.charts


Dashboards = Annotated[DashboardsFile, Depends(get_dashboards)]
Charts = Annotated[ChartsFile, Depends(_charts)]


def _resolve_chart_widget(w: ChartWidget, charts: ChartsFile, scope: str) -> dict[str, Any] | None:
    """Turn a chart widget into a uniform wire shape — always inline connector/query/spec.
    A ``chart`` reference is resolved *within the dashboard's own scope* (charts are now scoped:
    ``[charts.<scope>.<id>]``); a cross-connector chart should use an inline widget instead.
    Returns ``None`` when a referenced chart id can't be resolved (missing from charts.toml);
    the caller drops the widget."""
    if w.chart:
        ref: ChartConfig | None = charts.get_chart(scope, w.chart)
        if ref is None:
            _log.warning(
                "dashboard widget references unknown chart %r in scope %r — dropping the widget", w.chart, scope,
            )
            return None
        return {
            "type": "chart",
            "label": w.label or ref.label,    # inherit the saved chart's label when the widget doesn't override
            "col_span": w.col_span,
            "row_span": w.row_span,
            "chart_id": w.chart,              # echo back so the frontend can link out (future)
            "connector": ref.connector,
            "query": ref.query,
            "spec": ref.spec.model_dump(mode="json", exclude_none=True),
        }
    # Inline mode — validator on ChartWidget already ensured all three are set.
    assert w.connector and w.query and w.spec
    return {
        "type": "chart",
        "label": w.label,
        "col_span": w.col_span,
        "row_span": w.row_span,
        "connector": w.connector,
        "query": w.query,
        "spec": w.spec.model_dump(mode="json", exclude_none=True),
    }


def _resolve_kpi_widget(w: KpiWidget) -> dict[str, Any]:
    """A KPI widget's wire shape — straightforward dump, no inheritance / fan-out logic."""
    return {
        "type": "kpi",
        "label": w.label,
        "col_span": w.col_span,
        "row_span": w.row_span,
        "connector": w.connector,
        "query": w.query,
        "column": w.column,
        "aggregation": w.aggregation,
        **({"format": w.format} if w.format else {}),
    }


def _resolve_table_widget(w: TableWidget) -> dict[str, Any]:
    """A table widget's wire shape — connector/query the frontend fetches, plus the optional
    column subset + row cap. Permission gates on (connector, query) like the others."""
    return {
        "type": "table",
        "label": w.label,
        "col_span": w.col_span,
        "row_span": w.row_span,
        "connector": w.connector,
        "query": w.query,
        "columns": list(w.columns),
        **({"max_rows": w.max_rows} if w.max_rows else {}),
    }


def _widget_query(widget_dict: dict[str, Any]) -> tuple[str, str] | None:
    """Pull (connector, query) out of a resolved widget dict — what the permission gate keys on.
    All current widget kinds back onto exactly one query; returns ``None`` if the dict somehow
    doesn't carry one (a forward-compat hook for future widgets like markdown/heading)."""
    c = widget_dict.get("connector")
    q = widget_dict.get("query")
    if isinstance(c, str) and isinstance(q, str):
        return c, q
    return None


def _can_read(principal: Principal, widget_dict: dict[str, Any]) -> bool:
    target = _widget_query(widget_dict)
    if target is None:
        return True  # placeholder/static widgets — readable by anyone (no query backing them)
    return principal.has_permission(f"sql:{target[0]}:{target[1]}")


def _resolve_filters(dashboard: Dashboard, principal: Principal) -> list[dict[str, Any]]:
    """Build the wire shape for the dashboard's filter bar. A filter is included iff the caller
    holds the permission for its options query (the dropdown can't render otherwise); the rest
    of the dashboard is unaffected."""
    out: list[dict[str, Any]] = []
    for f in dashboard.filters:
        if not principal.has_permission(f"sql:{f.options.connector}:{f.options.query}"):
            continue
        out.append({
            "id": f.id,
            "label": f.label,
            "dictionary_key": f.dictionary_key,
            **({"default_value": f.default_value} if f.default_value else {}),
            "options": {
                "connector": f.options.connector,
                "query": f.options.query,
                "value_column": f.options.value_column,
                "label_column": f.options.label_column,
            },
        })
    return out


def _resolve_dashboard(dashboard: Dashboard, charts: ChartsFile, principal: Principal) -> dict[str, Any] | None:
    """Build the wire shape for one dashboard — resolves chart references, applies the
    per-widget permission gate, drops what the caller can't see. Returns ``None`` when the
    dashboard is explicitly **denied** (``!dashboard:<id>`` — the first-class RBAC overlay, so
    "all access but disable this dashboard" works); otherwise the dashboard is always returned
    (with however many widgets survive the per-widget gate)."""
    if principal.is_denied(f"dashboard:{dashboard.qualified_id}"):
        return None
    resolved: list[dict[str, Any]] = []
    for w in dashboard.widgets:
        if isinstance(w, ChartWidget):
            wd = _resolve_chart_widget(w, charts, dashboard.connector)
        elif isinstance(w, KpiWidget):
            wd = _resolve_kpi_widget(w)
        elif isinstance(w, TableWidget):
            wd = _resolve_table_widget(w)
        else:  # pragma: no cover - guarded by the discriminated union
            continue
        if wd is None:
            continue                 # unresolvable chart reference — logged in the resolver
        if not _can_read(principal, wd):
            continue
        resolved.append(wd)
    out: dict[str, Any] = {"id": dashboard.qualified_id, "label": dashboard.label, "widgets": resolved}
    if dashboard.description:
        out["description"] = dashboard.description
    filters = _resolve_filters(dashboard, principal)
    if filters:
        out["filters"] = filters
    return out


@router.get(
    "/dashboards",
    summary="List dashboards",
    responses={401: {"description": "Missing / invalid access token."}},
)
async def list_dashboards(principal: CurrentPrincipal, dashboards: Dashboards, charts: Charts) -> dict[str, Any]:
    """Returns the resolved dashboards — widget lists already filtered to only the
    widgets the caller can populate (each widget gates on the underlying connector +
    query's permission). A dashboard with zero readable widgets is dropped entirely."""
    out = [r for _, _, d in dashboards.iter_dashboards() if (r := _resolve_dashboard(d, charts, principal)) is not None]
    return {"dashboards": out}


@router.get(
    "/dashboards/{dashboard_id}",
    summary="Get dashboard",
    responses={
        401: {"description": "Missing / invalid access token."},
        404: {"description": "Unknown dashboard, OR caller has no readable widgets in it."},
    },
)
async def get_dashboard(
    dashboard_id: str, principal: CurrentPrincipal, dashboards: Dashboards, charts: Charts,
) -> dict[str, Any]:
    """``dashboard_id`` is the qualified ``<scope>.<id>`` form (e.g. ``nomasx1.overview``).
    Returns the dashboard with each widget resolved (chart-refs flattened to inline
    chart specs, KPI widgets carrying their aggregation column, table widgets carrying
    their column subset) AND the shared ``filters`` block — picker definitions whose
    chosen value rebinds each applicable widget's query."""
    # ``dashboard_id`` is the qualified ``<scope>.<id>`` (e.g. ``nomasx1.overview``).
    d = dashboards.find(dashboard_id)
    resolved = _resolve_dashboard(d, charts, principal) if d is not None else None
    if resolved is None:
        # Unknown id OR explicitly denied — same 404 either way (don't leak existence to a
        # caller who's been denied the dashboard).
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown dashboard {dashboard_id!r}")
    return resolved
