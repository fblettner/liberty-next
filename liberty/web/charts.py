"""``/api/charts`` — operator-curated chart catalog, permission-pruned.

| Route | Permission | Notes |
|---|---|---|
| ``GET /api/charts`` | authenticated | every accessible chart, filtered by the caller's permission on the underlying query |
| ``GET /api/charts/{id}`` | per-chart ``sql:{connector}:{query}`` | one chart's full definition (spec + label + description) |

A chart inherits its query's gate: the caller may see it iff they hold
``sql:{chart.connector}:{chart.query}``. Same convention as :mod:`liberty.web.screens` —
``GET`` on a chart the caller can't open returns 404 so we don't leak its existence.

The full chart shape (id / label / description / connector / query / spec) is returned
as-is via ``model_dump(mode="json", exclude_none=True)`` — the runtime payload is the same
shape the ``ChartsBuilder`` (future slice) would round-trip.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from liberty.auth.dependencies import CurrentPrincipal
from liberty.auth.principal import Principal
from liberty.charts import ChartConfig, ChartsFile

router = APIRouter(prefix="/api", tags=["charts"])


def get_charts(request: Request) -> ChartsFile:
    return request.app.state.charts


Charts = Annotated[ChartsFile, Depends(get_charts)]


def _can_read(principal: Principal, scope: str, chart: ChartConfig) -> bool:
    return principal.has_permission(f"sql:{scope}:{chart.query}")


def _public_view(scope: str, chart: ChartConfig) -> dict[str, Any]:
    """Wire shape of one chart. The public ``id`` is the *qualified* ``<scope>.<id>`` (charts are
    now scoped to a connector — ``[charts.<scope>.<id>]`` — and the inner id is only unique within
    its scope), and ``connector`` is the scope key."""
    out = chart.model_dump(mode="json", exclude_none=True)
    out["id"] = f"{scope}.{chart.id}"
    out["connector"] = scope
    return out


@router.get("/charts")
async def list_charts(principal: CurrentPrincipal, charts: Charts) -> dict[str, Any]:
    """List every chart the caller may open. Permission gate matches the underlying query's —
    a caller without ``sql:{scope}:{query}`` doesn't see the chart at all."""
    out = [_public_view(s, c) for s, _, c in charts.iter_charts() if _can_read(principal, s, c)]
    return {"charts": out}


@router.get("/charts/{chart_id}")
async def get_chart(chart_id: str, principal: CurrentPrincipal, charts: Charts) -> dict[str, Any]:
    # ``chart_id`` is the qualified ``<scope>.<id>`` (e.g. ``nomasx1.users_per_app``).
    scope, _, cid = chart_id.partition(".")
    chart = charts.get_chart(scope, cid) if cid else None
    if chart is None or not _can_read(principal, scope, chart):
        # 404 (not 403) for the unreadable case — don't leak the chart's existence to a caller
        # who can't run its query. Same convention the connectors / screens routes use.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown chart {chart_id!r}")
    return _public_view(scope, chart)
