"""``config/charts.toml`` — operator-curated chart definitions.

A *chart* is a named, version-controlled view of a connector's query, rendered as a bar /
line / area / pie. Phase 8 slice 1 introduced charts as a per-session UX layer on the
TableView (spec persisted to ``localStorage`` per ``(connector, query)``); slice 2 lifts
the same shape into ``charts.toml`` so an operator can save a useful chart, share it with
the team, and reference it by id from a menu or dashboard.

Charts are scoped to the connector they read from — ``[charts.<scope>.<id>]`` where ``<scope>``
is the connector name and ``<id>`` is a stable per-scope key (e.g. ``users_per_app``). This
mirrors ``screens.<scope>.<id>``: the connector is the table key (not a body field), and the
read query stays an ordinary field. The id only has to be unique *within its scope*.

Example::

    [charts.nomasx1.users_per_app]
    label = "Users per application"
    description = "Active user count grouped by application"
    query = "security_users_get"

      [charts.nomasx1.users_per_app.spec]
      type = "bar"
      x = "APPS_ID"
      y = ["USR_ID"]
      aggregation = "count"

Permission: the chart inherits the underlying query's ``sql:<connector>:<query>`` gate —
the operator who can run the query can see (and read) the chart. (A future slice may add
chart-specific ACLs if a use case appears.)
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ChartType = Literal["bar", "line", "area", "pie"]
Aggregation = Literal["sum", "avg", "count", "min", "max", "none"]
# Which side of the cartesian plot a Y series binds to. The default ("left") is what
# every chart used until this iteration; "right" opts a series into a second Y axis,
# the v1 dual-scale pattern (e.g. revenue $M on the left, unit count on the right).
# The frontend only emits the second YAxis when at least one series asks for it, so
# single-axis charts stay visually identical to their pre-multi-axis selves.
YAxisSide = Literal["left", "right"]


class ChartSpec(BaseModel):
    """How to render a query result. Mirrors the frontend's runtime ``ChartSpec`` shape; the
    same JSON serialises both directions. Keep additions optional with defaults so older
    ``charts.toml`` files keep parsing as the schema grows."""

    model_config = ConfigDict(extra="forbid")

    type: ChartType = Field(default="bar", description="Chart kind. ``bar`` / ``line`` / ``area`` / ``pie``.")
    x: str = Field(
        description="Result column for the X axis (slice label on pie).",
        # ChartsBuilder augments CHART_COLUMNS based on the parent chart's
        # connector + query (live introspection via /api/sql ?_limit=0).
        # ``allowCustom`` on the SearchSelect still lets operators type a
        # column the introspection didn't surface (e.g. a column added to the
        # query AFTER the chart was saved). ``x_case = upper`` normalises any
        # typed-custom value to UPPERCASE on save (the v1 column-name convention
        # the rest of the editors follow). Options from the dropdown are
        # already correctly cased.
        json_schema_extra={"x_enum_ref": "CHART_COLUMNS", "x_case": "upper"},
    )
    y: list[str] = Field(
        default_factory=list,
        description="Result column(s) for the Y axis. Empty → nothing to render. Pie collapses to the first entry.",
        json_schema_extra={"x_enum_ref": "CHART_COLUMNS", "x_case": "upper"},
    )
    aggregation: Aggregation = Field(
        default="sum",
        description=(
            "How to combine rows that share the same X value. ``count`` ignores the Y value (counts rows); "
            "``none`` keeps each row as its own datum (suitable for time series where X is already unique)."
        ),
    )
    stacked: bool | None = Field(default=None, description="Bar / area only — stack the series rather than place side-by-side.")
    show_legend: bool | None = Field(default=None, description="Show the legend (default: only when there's more than one series).")
    show_grid: bool | None = Field(default=None, description="Show the cartesian grid (default on; pie ignores).")
    sort_by_x: bool | None = Field(default=None, description="Sort categories alphabetically by X (default: input order).")
    # Per-series colour overrides — parallel to ``y`` and indexed positionally. An empty
    # string (or a missing trailing entry) means "fall back to the built-in palette" for
    # that series. Operators reorder ``y`` and ``colors`` together; the validator below
    # enforces that ``len(colors) <= len(y)`` so an off-by-one never silently colours the
    # wrong series. Stored as CSS-friendly strings (``"#3b82f6"`` / ``"rgb(...)"``) so the
    # frontend can pass them straight to Recharts without parsing.
    colors: list[str] = Field(
        default_factory=list,
        description="Optional per-series colour overrides (CSS strings, parallel to `y`). Empty → use the palette.",
    )
    # Per-series axis assignment — same parallel-list pattern as ``colors``. Use "right"
    # to opt a series onto the right-hand Y axis (the v1 dual-Y pattern); "left" is the
    # default. Missing trailing entries default to "left". Pie ignores (no cartesian axes).
    y_axis: list[YAxisSide] = Field(
        default_factory=list,
        description=(
            "Per-series Y-axis assignment (`left` / `right`), parallel to `y`. Empty or all "
            "`left` → single-axis chart (current default behaviour). Any `right` entry switches "
            "the chart to dual-axis."
        ),
    )


class ChartConfig(BaseModel):
    """One ``[charts.<scope>.<id>]`` entry — a named, version-controlled chart. Both ``id`` (the
    inner key) and ``connector`` (the outer *scope* key) are injected from the TOML path by
    :func:`parse_charts`, the same way screens/menus derive their scope — so the body only needs
    ``label`` / ``query`` / ``spec``."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        default="",
        description="Stable id (matches the inner TOML key; the validator below forces them to agree).",
        json_schema_extra={"x_group": "Advanced"},
    )
    label: str = Field(description="Display label — the chart's title in lists, dashboards, menus.")
    description: str | None = Field(default=None, description="Optional longer description.")
    connector: str = Field(
        default="",
        description="The SQL connector whose query backs this chart — the ``<scope>`` path key; injected from it.",
        json_schema_extra={"x_group": "Advanced"},
    )
    query: str = Field(
        description="The read query (the chart pulls its data from this query's result).",
        # ChartsBuilder builds CHART_QUERIES from the selected connector's
        # ``[connectors.<connector>.queries]`` array (same shape MenusBuilder
        # uses for MENU_TARGETS). Empty when no connector picked or the
        # connector has no queries; SearchSelect's allowCustom still lets
        # operators type a name not in the catalog.
        json_schema_extra={"x_enum_ref": "CHART_QUERIES"},
    )
    spec: ChartSpec = Field(description="How to render the result.")
    # Upgrade-safety flag — see Screen.override for the full story. Default False.
    override: bool = Field(
        default=False,
        description=(
            "Mark this chart as a customer customisation that an upgrade-package import "
            "should NOT overwrite. Default False — operators opt in after forking."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> ChartConfig:
        if not self.spec.x:
            raise ValueError(f"chart {self.id!r}: spec.x (the X axis column) is required.")
        if not self.spec.y:
            raise ValueError(f"chart {self.id!r}: spec.y (the Y axis column list) must have at least one entry.")
        # Parallel-array sanity check — a longer ``colors`` / ``y_axis`` than ``y`` means
        # the operator deleted a Y series without trimming the corresponding tail entry,
        # which would silently colour the wrong series next time they add one back.
        # Trailing entries shorter than ``y`` are fine (default to palette / "left").
        if len(self.spec.colors) > len(self.spec.y):
            raise ValueError(
                f"chart {self.id!r}: spec.colors ({len(self.spec.colors)}) has more entries than "
                f"spec.y ({len(self.spec.y)}); colours are positional, trim the trailing entries."
            )
        if len(self.spec.y_axis) > len(self.spec.y):
            raise ValueError(
                f"chart {self.id!r}: spec.y_axis ({len(self.spec.y_axis)}) has more entries than "
                f"spec.y ({len(self.spec.y)}); axis assignments are positional, trim the trailing entries."
            )
        return self


class ChartsFile(BaseModel):
    """Top-level ``charts.toml`` shape — ``[charts.<scope>.<id>]``: a dict of *scope* (connector)
    → dict of *id* → chart. Mirrors ``screens.toml`` (``screens.<app>.<id>``)."""

    model_config = ConfigDict(extra="forbid")

    # scope (connector) -> chart id -> chart
    charts: dict[str, dict[str, ChartConfig]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_ids(self) -> ChartsFile:
        for scope, by_id in self.charts.items():
            for cid, chart in by_id.items():
                if chart.id and chart.id != cid:
                    raise ValueError(f"chart {scope}.{cid!r}: ``id`` field is {chart.id!r}, must match its key")
                if chart.connector and chart.connector != scope:
                    raise ValueError(
                        f"chart {scope}.{cid!r}: ``connector`` field is {chart.connector!r}, "
                        f"must match its scope key {scope!r}"
                    )
        return self

    def iter_charts(self) -> Iterator[tuple[str, str, ChartConfig]]:
        """Walk every chart as ``(scope, id, chart)`` — the flat view the runtime/API need."""
        for scope, by_id in self.charts.items():
            for cid, chart in by_id.items():
                yield scope, cid, chart

    def get_chart(self, scope: str, chart_id: str) -> ChartConfig | None:
        """Resolve a chart by ``(scope, id)``; ``None`` when absent."""
        return self.charts.get(scope, {}).get(chart_id)


def parse_charts(data: dict[str, Any]) -> ChartsFile:
    """Validate a raw TOML dict into a :class:`ChartsFile`. Injects each entry's ``id`` (inner
    key) and ``connector`` (outer scope key) when omitted — so hand-edited files only carry
    ``label`` / ``query`` / ``spec`` in the body.
    """
    charts = data.get("charts") or {}
    if isinstance(charts, dict):
        for scope, by_id in charts.items():
            if not isinstance(by_id, dict):
                continue
            for cid, chart in by_id.items():
                if not isinstance(chart, dict):
                    continue
                if not chart.get("id"):
                    chart["id"] = cid
                if not chart.get("connector"):
                    chart["connector"] = scope
    return ChartsFile.model_validate(data)


def load_charts(path: Path | str) -> ChartsFile:
    """Load and validate ``charts.toml``. A missing file yields an empty :class:`ChartsFile`
    (the operator just hasn't saved any charts yet — same as a fresh ``menus.toml``)."""
    path = Path(path)
    if not path.exists():
        return ChartsFile()
    with path.open("rb") as fh:
        return parse_charts(tomllib.load(fh))
