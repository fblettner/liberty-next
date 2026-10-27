"""``/api/dashboards`` — operator-curated dashboard catalog.

| Route | Permission | Notes |
|---|---|---|
| ``GET /api/dashboards`` | authenticated | every dashboard, with widgets the caller can't read filtered out |
| ``GET /api/dashboards/{id}`` | authenticated | one dashboard, same filtering; 404 when the id doesn't exist |
| ``POST /api/dashboards/{id}/export`` | authenticated | the dashboard as the page shows it (filters, period) → xlsx / pdf |

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
from typing import Annotated, Any, Literal

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from liberty.auth.dependencies import CurrentPrincipal
from liberty.auth.principal import Principal
from liberty.charts import ChartConfig, ChartsFile
from liberty.connectors.registry import ConnectorRegistry
from liberty.dashboards import BarsWidget, ChartWidget, Dashboard, DashboardsFile, KpiWidget, ListWidget, StatWidget, TableWidget
from liberty.dashboards.report import PeriodPick, dashboard_sections, resolve_column
from liberty.screens import ScreensFile
from liberty.web.deps import get_connectors, get_screens, request_language

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dashboards"])


def get_dashboards(request: Request) -> DashboardsFile:
    return request.app.state.dashboards


def _charts(request: Request) -> ChartsFile:
    return request.app.state.charts


Dashboards = Annotated[DashboardsFile, Depends(get_dashboards)]
Charts = Annotated[ChartsFile, Depends(_charts)]
Screens = Annotated[ScreensFile, Depends(get_screens)]
Connectors = Annotated[ConnectorRegistry, Depends(get_connectors)]


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
            **({"description": w.description} if w.description else {}),
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
        **({"description": w.description} if w.description else {}),
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
        **({"description": w.description} if w.description else {}),
        "col_span": w.col_span,
        "row_span": w.row_span,
        "connector": w.connector,
        "query": w.query,
        "column": w.column,
        "aggregation": w.aggregation,
        **({"format": w.format} if w.format else {}),
        **({"compare_previous": True} if w.compare_previous else {}),
    }


def _resolve_table_widget(w: TableWidget) -> dict[str, Any]:
    """A table widget's wire shape — connector/query the frontend fetches, plus the optional
    column subset + row cap. Permission gates on (connector, query) like the others."""
    return {
        "type": "table",
        "label": w.label,
        **({"description": w.description} if w.description else {}),
        "col_span": w.col_span,
        "row_span": w.row_span,
        "connector": w.connector,
        "query": w.query,
        "columns": list(w.columns),
        **({"max_rows": w.max_rows} if w.max_rows else {}),
    }


def _resolve_plain_widget(w: StatWidget | ListWidget | BarsWidget) -> dict[str, Any]:
    """The synthesis widgets (stat / list / bars) travel as they are configured: every field is
    presentation, the frontend formats the first row / the rows itself."""
    return w.model_dump(mode="json", exclude_none=True, exclude={"l"})


def _localize(d: dict[str, Any], overrides: dict[str, dict[str, Any]], language: str | None) -> dict[str, Any]:
    """*d* with the ``l`` block of *language* (``fr``, or ``fr-FR`` → ``fr``) merged over it — the
    string fields the operator translated; the block itself never travels."""
    if not overrides or not language:
        return d
    lang = language.lower()
    block = overrides.get(lang) or overrides.get(lang.split("-")[0]) or {}
    return {**d, **{k: v for k, v in block.items() if isinstance(v, str) and v}}


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


def _resolve_filters(dashboard: Dashboard, principal: Principal, language: str | None = None) -> list[dict[str, Any]]:
    """Build the wire shape for the dashboard's filter bar. A filter is included iff the caller
    holds the permission for its options query (the dropdown can't render otherwise); the rest
    of the dashboard is unaffected."""
    out: list[dict[str, Any]] = []
    for f in dashboard.filters:
        if not principal.has_permission(f"sql:{f.options.connector}:{f.options.query}"):
            continue
        out.append({
            "id": f.id,
            "label": _localize({"label": f.label}, f.l, language)["label"],
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


def _resolve_dashboard(dashboard: Dashboard, charts: ChartsFile, principal: Principal, language: str | None = None) -> dict[str, Any] | None:
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
        elif isinstance(w, (StatWidget, ListWidget, BarsWidget)):
            wd = _resolve_plain_widget(w)
        else:  # pragma: no cover - guarded by the discriminated union
            continue
        if wd is None:
            continue                 # unresolvable chart reference — logged in the resolver
        if not _can_read(principal, wd):
            continue
        resolved.append(_localize(wd, w.l, language))
    head = _localize({"label": dashboard.label, "description": dashboard.description or ""}, dashboard.l, language)
    out: dict[str, Any] = {"id": dashboard.qualified_id, "label": head["label"], "widgets": resolved}
    if head["description"]:
        out["description"] = head["description"]
    filters = _resolve_filters(dashboard, principal, language)
    if filters:
        out["filters"] = filters
    if dashboard.period is not None:
        out["period"] = dashboard.period.model_dump(mode="json", exclude_none=True)
    return out


@router.get(
    "/dashboards",
    summary="List dashboards",
    responses={401: {"description": "Missing / invalid access token."}},
)
async def list_dashboards(request: Request, principal: CurrentPrincipal, dashboards: Dashboards, charts: Charts) -> dict[str, Any]:
    """Returns the resolved dashboards — widget lists already filtered to only the
    widgets the caller can populate (each widget gates on the underlying connector +
    query's permission). A dashboard with zero readable widgets is dropped entirely."""
    language = request_language(request)
    out = [r for _, _, d in dashboards.iter_dashboards() if (r := _resolve_dashboard(d, charts, principal, language)) is not None]
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
    dashboard_id: str, request: Request, principal: CurrentPrincipal, dashboards: Dashboards, charts: Charts,
) -> dict[str, Any]:
    """``dashboard_id`` is the qualified ``<scope>.<id>`` form (e.g. ``nomasx1.overview``).
    Returns the dashboard with each widget resolved (chart-refs flattened to inline
    chart specs, KPI widgets carrying their aggregation column, table widgets carrying
    their column subset) AND the shared ``filters`` block — picker definitions whose
    chosen value rebinds each applicable widget's query."""
    # ``dashboard_id`` is the qualified ``<scope>.<id>`` (e.g. ``nomasx1.overview``).
    d = dashboards.find(dashboard_id)
    resolved = _resolve_dashboard(d, charts, principal, request_language(request)) if d is not None else None
    if resolved is None:
        # Unknown id OR explicitly denied — same 404 either way (don't leak existence to a
        # caller who's been denied the dashboard).
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown dashboard {dashboard_id!r}")
    return resolved


class ExportPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: date | None = Field(default=None, alias="from", description="The range's first day; both blank = all dates.")
    to: date | None = None
    basis: str = Field(default="", description="The chosen basis value (one of the period's ``basis[].value``).")


class DashboardExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["xlsx", "pdf"] = "xlsx"
    filters: dict[str, str] = Field(default_factory=dict, description="The filter bar's picks, by filter id (blank = all).")
    period: ExportPeriod | None = Field(default=None, description="The period control's state, when the dashboard has one.")


@router.post("/dashboards/{dashboard_id}/export", summary="Export dashboard")
async def export_dashboard(
    dashboard_id: str, body: DashboardExportBody, request: Request, principal: CurrentPrincipal, dashboards: Dashboards, charts: Charts,
    connectors: Connectors, screens: Screens,
) -> StreamingResponse:
    """The dashboard as the page shows it — every readable widget run with the filter picks and
    the period the caller sends, under the caller's row-level scope — as an Excel workbook
    (one sheet per widget, the KPIs together) or a PDF synthesis (cover with the period and
    the filters, the charts drawn, the KPIs as cards). A PDF needs the ``reports`` extra (503)."""
    from liberty.explorer.report import sections_to_pdf, sections_to_xlsx
    from liberty.web.connectors import _build_dd_map_for, _run_sql, _request_pool

    language = request_language(request)
    d = dashboards.find(dashboard_id)
    resolved = _resolve_dashboard(d, charts, principal, language) if d is not None else None
    if resolved is None or d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown dashboard {dashboard_id!r}")
    pool = _request_pool(request)
    pick = PeriodPick(body.period.from_, body.period.to, body.period.basis) if (body.period and resolved.get("period")) else (PeriodPick() if resolved.get("period") else None)

    async def run(connector: str, query: str, params: dict[str, Any]) -> dict[str, Any]:
        return await _run_sql(
            connectors, connector, query, params, language=language, user=principal.username, screens=screens,
            scope=principal.data_scope, verbose_errors=principal.is_superuser, pool=pool,
        )

    sections = await dashboard_sections(resolved, run=run, dd_map=lambda c, q: _build_dd_map_for(screens, c, q), values=body.filters, pick=pick, lang=language)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in d.label).strip("_") or "dashboard"
    if body.format == "pdf":
        from liberty.reports.render import ReportsUnavailableError
        settings = request.app.state.settings
        branding = getattr(getattr(settings, "reports", None), "branding", None)
        period = (pick.date_from, pick.date_to) if pick and pick.date_from and pick.date_to else None
        rows = await _filter_rows(resolved, body.filters, run)
        try:
            data = sections_to_pdf(
                sections, resolved["label"], app_name=getattr(settings.app, "name", ""), branding=branding.model_dump() if branding is not None else None,
                lang=language, author=principal.username, period=period, cover_rows=rows,
            )
        except ReportsUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return StreamingResponse(iter([data]), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{safe}.pdf"'})
    data = sections_to_xlsx(sections)
    return StreamingResponse(iter([data]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{safe}.xlsx"'})


async def _filter_rows(resolved: dict[str, Any], values: dict[str, str], run: Any) -> list[tuple[str, str]]:
    """The cover's filter rows — ``(label, the picked value's label)``: the options query gives
    the label (best effort; the raw value when it cannot)."""
    out: list[tuple[str, str]] = []
    for f in resolved.get("filters") or []:
        v = values.get(f["id"])
        if not v:
            continue
        shown = v
        try:
            o = f["options"]
            res = await run(o["connector"], o["query"], {})
            vk, lk = resolve_column(res, o["value_column"]), resolve_column(res, o["label_column"])
            hit = next((r for r in res.get("rows") or [] if r.get(vk) is not None and str(r[vk]) == v), None)
            if hit is not None and hit.get(lk) is not None:
                shown = str(hit[lk])
        except Exception:  # noqa: BLE001 — the cover row is a nicety
            pass
        out.append((f["label"], shown))
    return out
