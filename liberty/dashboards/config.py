"""``config/dashboards.toml`` — operator-curated dashboards.

A *dashboard* is a layout of widgets on a 12-column grid, surfaced through the menu system
like any other screen. Slice 3a (this module) ships two widget kinds:

* **chart** — references a saved chart by ``chart`` id (from ``charts.toml``) *or* inlines
  its own connector/query/spec. The runtime fetches the query result + renders it through
  the same Recharts pipeline the TableView's chart toggle uses.
* **kpi** — a single big number computed from a query's result: ``aggregation(column)`` over
  every returned row. For "active users · 686" style displays.

Each widget claims ``col_span`` × ``row_span`` cells of a 12-column grid; widgets land in
declaration order. Permission: each widget gates on its underlying query's
``sql:<connector>:<query>``; an unreadable widget is hidden, the rest of the dashboard
still renders. A dashboard with *no* readable widgets still appears in the catalog (the
operator may want it visible as a placeholder).

Example::

    [dashboards.security_overview]
    label = "Security Overview"
    description = "User counts + assignments at a glance"

      [[dashboards.security_overview.widgets]]
      type = "chart"
      chart = "users_per_app"      # by id (from charts.toml)
      col_span = 6
      row_span = 1

      [[dashboards.security_overview.widgets]]
      type = "kpi"
      label = "Active users"
      connector = "nomasx1"
      query = "security_users_get"
      column = "USR_ID"
      aggregation = "count"
      col_span = 3
      row_span = 1

Later slices: table widget, markdown / heading widget, multi-axis charts, drill-down
clicks (a bar click → /sql/<connector>/<query>?...).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from liberty.charts.config import Aggregation, ChartSpec


# A widget is one cell-block on the dashboard grid. Two kinds today; the discriminator is
# ``type``, the same pattern v2's actions / connector-config use.


class WidgetBase(BaseModel):
    """Fields every widget kind shares — the grid placement + the optional title bar."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, description="Optional title shown above the widget.")
    col_span: int = Field(default=6, ge=1, le=12, description="Width in 12-column grid units (1–12).")
    row_span: int = Field(default=1, ge=1, le=8, description="Height in grid rows (1–8).")


class ChartWidget(WidgetBase):
    """A chart widget. Either references a saved chart by id (``chart``) — taking its connector/
    query/spec from ``charts.toml`` — or inlines its own ``connector``/``query``/``spec``. Exactly
    one of those two modes; the runtime errors if both or neither are given."""

    type: Literal["chart"] = "chart"
    chart: str | None = Field(
        default=None,
        description="ID of a saved chart in ``charts.toml`` to render. Leave blank to inline the spec instead.",
        # `x_enum_ref` makes the DashboardsBuilder render this as a SearchSelect populated from the
        # current charts.toml (the builder fetches GET /admin/config/charts/parsed and materialises
        # `CHART_IDS` on top of the bundled framework enums). The field is free-text on its own —
        # an operator typing an id that doesn't (yet) exist still saves cleanly — the dropdown is
        # a UI convenience, not a validation gate.
        json_schema_extra={"x_enum_ref": "CHART_IDS"},
    )
    connector: str | None = Field(default=None, description="(inline mode) SQL connector for the query.")
    query: str | None = Field(default=None, description="(inline mode) read query whose result we chart.")
    spec: ChartSpec | None = Field(default=None, description="(inline mode) how to render the result.")

    @model_validator(mode="after")
    def _check(self) -> ChartWidget:
        # Either-or — exactly one mode set. Catches typos like passing both a `chart` id and an
        # inline `spec` (the runtime would silently use one and the operator wouldn't know which).
        ref_set = bool(self.chart)
        inline_set = bool(self.connector or self.query or self.spec)
        if ref_set and inline_set:
            raise ValueError("chart widget: pick *either* `chart = \"<id>\"` *or* an inline connector/query/spec, not both.")
        if not ref_set and not inline_set:
            raise ValueError("chart widget: missing `chart = \"<id>\"` or an inline connector/query/spec.")
        if inline_set:
            # Inline mode requires the full triple — partial inlines (just `query` without `spec`) confuse the renderer.
            if not (self.connector and self.query and self.spec):
                raise ValueError("chart widget (inline mode): `connector`, `query`, and `spec` must all be set.")
        return self


class KpiWidget(WidgetBase):
    """A single big-number card. Runs a query, applies an aggregation to one column, displays
    the result. Suitable for "Active users · 686" style displays. The query inherits the
    dashboard's permission gate; an unreadable KPI is hidden like any other widget."""

    type: Literal["kpi"] = "kpi"
    connector: str = Field(description="SQL connector for the query.")
    query: str = Field(description="Read query whose result the KPI summarises.")
    column: str = Field(description="Result column to aggregate. (Pick a numeric column for sum/avg/min/max.)")
    aggregation: Aggregation = Field(
        default="count",
        description="How to combine the column's values. ``count`` ignores the value (counts rows, like SELECT COUNT(*)).",
    )
    format: str | None = Field(
        default=None,
        description="Optional format hint — passed through to the frontend (e.g. ``currency``, ``percent``); unused for now.",
    )


class TableWidget(WidgetBase):
    """A table widget — runs a read query and shows its rows in a compact grid. Suitable for
    "recent failures" / "top N" lists sitting next to the charts. Gates on the query's
    permission like every other widget; the dashboard filter bar binds it the same way charts
    and KPIs are bound (a column whose ``dd`` matches a filter's ``dictionary_key``)."""

    type: Literal["table"] = "table"
    connector: str = Field(description="SQL connector for the query.")
    query: str = Field(description="Read query whose rows are listed.")
    columns: list[str] = Field(
        default_factory=list,
        description="Optional subset / order of result columns to show (by name). Empty → every non-hidden column.",
    )
    max_rows: int | None = Field(
        default=None,
        ge=1,
        description="Cap the rows shown in the widget (client-side trim). Blank → whatever the query / screen returns.",
    )


Widget = Annotated[Union[ChartWidget, KpiWidget, TableWidget], Field(discriminator="type")]


class DashboardFilterOptions(BaseModel):
    """How to populate a dashboard filter's value picker — a lookup query whose result drives the
    dropdown options. The frontend renders the dropdown as a SearchSelect over ``{value, label}``
    pairs taken from each row's ``value_column`` and ``label_column``. The caller must hold
    ``sql:<connector>:<query>`` to populate the dropdown (the filter is hidden if they don't —
    same convention the rest of the routes use)."""

    model_config = ConfigDict(extra="forbid")

    connector: str = Field(description="SQL connector hosting the options query.")
    query: str = Field(description="Read query whose rows populate the dropdown options.")
    value_column: str = Field(description="Result column whose value becomes the filter's bound value.")
    label_column: str = Field(description="Result column whose value is shown in the dropdown.")


class DashboardFilter(BaseModel):
    """A dashboard-level filter — picks a value, every widget whose query has a column with the
    matching ``dictionary_key`` is refetched with that column bound to the picked value. Widgets
    whose query has no matching column ignore the filter (no SQL change). For nomasx1, a single
    ``APPS_ID`` filter cross-maps to ``USR_APPS_ID`` / ``RLU_APPS_ID`` / ``CFD_APPS_ID`` etc. via
    the column hints' ``dd = "APPS_ID"`` annotation."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable id (used as the state key on the frontend).")
    label: str = Field(description="Display label shown above the dropdown.")
    dictionary_key: str = Field(
        description=(
            "Cross-table glue — every widget's query column whose ``dd`` hint matches this is the "
            "one that gets bound. For nomasx1's ``APPS_ID`` filter, that's USR_APPS_ID / "
            "RLU_APPS_ID / CFD_APPS_ID / ..."
        ),
    )
    default_value: str | None = Field(
        default=None,
        description="Pre-selected value when the dashboard opens. Blank = no filter (show all).",
    )
    options: DashboardFilterOptions = Field(description="Where the dropdown's choices come from.")


class Dashboard(BaseModel):
    """One ``[dashboards.<id>]`` — title + description + the widget list + optional filters."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", description="Stable id (matches the TOML key).", json_schema_extra={"x_group": "Advanced"})
    label: str = Field(description="Display label — the dashboard's title in lists and menus.")
    description: str | None = Field(default=None, description="Optional longer description.")
    filters: list[DashboardFilter] = Field(
        default_factory=list,
        description=(
            "Optional dashboard-level filters — render as a bar above the widget grid; picking "
            "a value refetches every applicable widget with that bind. Empty = no bar shown."
        ),
    )
    widgets: list[Widget] = Field(default_factory=list, description="Widgets, in display order.")


class DashboardsFile(BaseModel):
    """Top-level ``dashboards.toml`` shape — one flat dict keyed by id (matches charts/menus/screens)."""

    model_config = ConfigDict(extra="forbid")

    dashboards: dict[str, Dashboard] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_ids(self) -> DashboardsFile:
        for did, d in self.dashboards.items():
            if d.id and d.id != did:
                raise ValueError(f"dashboard {did!r}: ``id`` field is {d.id!r}, must match its key")
        return self


def parse_dashboards(data: dict[str, Any]) -> DashboardsFile:
    """Validate a raw TOML dict into a :class:`DashboardsFile`. Each entry's ``id`` is injected
    from its key when omitted (same convention as charts/screens/menus)."""
    ds = data.get("dashboards") or {}
    if isinstance(ds, dict):
        for did, d in ds.items():
            if isinstance(d, dict) and not d.get("id"):
                d["id"] = did
    return DashboardsFile.model_validate(data)


def load_dashboards(path: Path | str) -> DashboardsFile:
    """Load and validate ``dashboards.toml``. A missing file yields an empty :class:`DashboardsFile`."""
    path = Path(path)
    if not path.exists():
        return DashboardsFile()
    with path.open("rb") as fh:
        return parse_dashboards(tomllib.load(fh))
