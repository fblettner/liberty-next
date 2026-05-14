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
from liberty.dashboards import ChartWidget, Dashboard, DashboardsFile, KpiWidget

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dashboards"])


def get_dashboards(request: Request) -> DashboardsFile:
    return request.app.state.dashboards


def _charts(request: Request) -> ChartsFile:
    return request.app.state.charts


Dashboards = Annotated[DashboardsFile, Depends(get_dashboards)]
Charts = Annotated[ChartsFile, Depends(_charts)]


def _resolve_chart_widget(w: ChartWidget, charts: ChartsFile) -> dict[str, Any] | None:
    """Turn a chart widget into a uniform wire shape — always inline connector/query/spec.
    Returns ``None`` when a referenced chart id can't be resolved (missing from charts.toml);
    the caller drops the widget."""
    if w.chart:
        ref: ChartConfig | None = charts.charts.get(w.chart)
        if ref is None:
            _log.warning("dashboard widget references unknown chart %r — dropping the widget", w.chart)
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


def _resolve_dashboard(dashboard: Dashboard, charts: ChartsFile, principal: Principal) -> dict[str, Any]:
    """Build the wire shape for one dashboard — resolves chart references, applies the
    per-widget permission gate, drops what the caller can't see. The dashboard itself is
    always returned (with however many widgets survive)."""
    resolved: list[dict[str, Any]] = []
    for w in dashboard.widgets:
        if isinstance(w, ChartWidget):
            wd = _resolve_chart_widget(w, charts)
        elif isinstance(w, KpiWidget):
            wd = _resolve_kpi_widget(w)
        else:  # pragma: no cover - guarded by the discriminated union
            continue
        if wd is None:
            continue                 # unresolvable chart reference — logged in the resolver
        if not _can_read(principal, wd):
            continue
        resolved.append(wd)
    out: dict[str, Any] = {"id": dashboard.id, "label": dashboard.label, "widgets": resolved}
    if dashboard.description:
        out["description"] = dashboard.description
    return out


@router.get("/dashboards")
async def list_dashboards(principal: CurrentPrincipal, dashboards: Dashboards, charts: Charts) -> dict[str, Any]:
    out = [_resolve_dashboard(d, charts, principal) for d in dashboards.dashboards.values()]
    return {"dashboards": out}


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard(
    dashboard_id: str, principal: CurrentPrincipal, dashboards: Dashboards, charts: Charts,
) -> dict[str, Any]:
    d = dashboards.dashboards.get(dashboard_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown dashboard {dashboard_id!r}")
    return _resolve_dashboard(d, charts, principal)
