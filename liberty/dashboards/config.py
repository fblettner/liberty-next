"""``config/dashboards.toml`` — operator-curated dashboards.

A *dashboard* is a layout of widgets on a 12-column grid, surfaced through the menu system
like any other screen. Slice 3a (this module) ships two widget kinds:

* **chart** — references a saved chart by ``chart`` id (from ``charts.toml``) *or* inlines
  its own connector/query/spec. The runtime fetches the query result + renders it through
  the same Recharts pipeline the TableView's chart toggle uses.
* **kpi** — a single big number computed from a query's result: ``aggregation(column)`` over
  every returned row. For "active users · 686" style displays.
* **table**, **stat**, **list**, **bars** — the rows of a query as a compact grid; a figure card
  from the first row with a sub line; a decision / ranked list (title, subtitle, figure, count,
  tone dot, tag); a proportion-bar list. The last three are the synthesis-page widgets.

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
from collections.abc import Iterator
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
    description: str | None = Field(default=None, description="A subtitle under the title: what the figure covers (\"Last 12 months, by status\").")
    l: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-language overrides of the widget's text fields, by language code: ``l.fr = { label = \"…\", sub = \"…\", title_column = \"TITLE_FR\" }`` — any string field, including the column a list or bars widget reads a text from.",
    )
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
    compare_previous: bool = Field(
        default=False,
        description="With a dashboard period: also run the query on the period before and show the delta and the previous value.",
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


ValueFormat = Literal["number", "integer", "currency", "percent"]


class StatWidget(WidgetBase):
    """A figure card the way a synthesis page shows one: the **first row** of the query gives
    the value (``value_column``, formatted, with a unit) and the sub line — a template over the
    row's other columns (``"{INVOICES:integer} invoices · {CREDITS:currency}"``). The query
    aggregates in SQL; the card only presents. Unlike ``kpi`` nothing is aggregated client-side."""
    type: Literal["stat"] = "stat"
    connector: str = Field(description="SQL connector for the query.")
    query: str = Field(description="Read query whose first row carries the figure.")
    value_column: str = Field(description="The column shown as the big figure.")
    format: ValueFormat | None = Field(default=None, description="How the figure is formatted: number (default), integer, currency, percent (a ratio 0–1).")
    unit: str | None = Field(default=None, description="A unit after the figure (\"€\", \"users\").")
    sub: str | None = Field(default=None, description="The line under the figure: text with ``{COLUMN}`` or ``{COLUMN:format}`` placeholders filled from the same row.")


class ListWidget(WidgetBase):
    """A list of rows the way a synthesis page lists what needs a decision or the figures per
    company: a title and a subtitle on the left, a value and a count on the right, an optional
    tone dot (ok / warn / error / info) and an optional tag pill. One row per query row, capped."""
    type: Literal["list"] = "list"
    connector: str = Field(description="SQL connector for the query.")
    query: str = Field(description="Read query whose rows are listed, in its order.")
    title_column: str = Field(description="The row's title.")
    subtitle_column: str | None = Field(default=None, description="A muted line under the title (an explanation, a hint).")
    value_column: str | None = Field(default=None, description="The figure on the right.")
    format: ValueFormat | None = Field(default=None, description="The figure's format.")
    unit: str | None = Field(default=None, description="A unit after the figure.")
    count_column: str | None = Field(default=None, description="A count under the figure.")
    count_label: str | None = Field(default=None, description="The count's wording, ``{n}`` for the number (\"{n} invoices\"); blank = the number alone.")
    tone_column: str | None = Field(default=None, description="A column holding ok / warn / error / info: colours the dot and the tag.")
    tag_column: str | None = Field(default=None, description="A short status shown as a pill under the figure.")
    max_rows: int = Field(default=10, ge=1, le=100, description="Rows shown.")


class BarsWidget(WidgetBase):
    """A ranked list with a proportion bar per row (\"per transaction type\"): a label, an optional
    muted sub label, the figure on the right, the bar as the row's share of the total (or of the
    largest row)."""
    type: Literal["bars"] = "bars"
    connector: str = Field(description="SQL connector for the query.")
    query: str = Field(description="Read query whose rows are listed, in its order.")
    label_column: str = Field(description="The row's label.")
    sub_label_column: str | None = Field(default=None, description="A muted word after the label.")
    value_column: str = Field(description="The figure, and the bar's length.")
    format: ValueFormat | None = Field(default=None, description="The figure's format.")
    unit: str | None = Field(default=None, description="A unit after the figure.")
    share: Literal["total", "max"] = Field(default="total", description="The bar's full width: the sum of the rows (a share) or the largest row (a ranking).")
    note: str | None = Field(default=None, description="A muted note under the bars.")
    max_rows: int = Field(default=10, ge=1, le=100, description="Rows shown.")


class TextWidget(WidgetBase):
    """A block of text (Markdown): what the application is for, how to read the page, a note —
    an overview page's introduction. No query behind it; readable by everyone."""
    type: Literal["text"] = "text"
    markdown: str = Field(description="The text, in Markdown (headings, lists, links, emphasis).")


class LinkItem(BaseModel):
    """One entry of a links card: a menu item of the dashboard's app (``menu`` = its id — the
    link shows only when the user can see that item, and takes its label when none is given)
    or an external ``url``."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, description="The link's wording (default: the menu item's label).")
    description: str | None = Field(default=None, description="A line under the link.")
    icon: str | None = Field(default=None, description="A lucide icon name the sidebar knows (``users``, ``shield``…); default: the menu item's.")
    menu: str | None = Field(default=None, description="The id of a menu item of the dashboard's app.")
    url: str | None = Field(default=None, description="An external address (opens in a new tab).")
    l: dict[str, dict[str, Any]] = Field(default_factory=dict, description="Per-language overrides of ``label`` / ``description``.")

    @model_validator(mode="after")
    def _one_target(self) -> LinkItem:
        if bool(self.menu) == bool(self.url):
            raise ValueError("a link needs exactly one of `menu` (a menu item id) or `url`")
        if self.url and not self.label:
            raise ValueError(f"link {self.url!r}: a url link needs a `label`")
        return self


class LinksWidget(WidgetBase):
    """A card of links — the entry points of a domain (\"Security Maintenance: users, roles,
    workbench…\"): an overview page's navigation. A ``menu`` link is permission-pruned like the
    sidebar; a ``url`` link opens in a new tab."""
    type: Literal["links"] = "links"
    links: list[LinkItem] = Field(min_length=1, description="The links, in display order.")
    columns: int = Field(default=1, ge=1, le=4, description="Links per row inside the card.")


class JobsWidget(WidgetBase):
    """The nomaflow jobs at a glance, with their controls: per job the next fire (or "paused"), the
    schedule in words, the last run's state, and Run now / Pause / Resume. The controls are the
    superuser's (``/admin/jobs``): the widget is dropped for anyone else."""
    type: Literal["jobs"] = "jobs"
    jobs: list[str] = Field(default_factory=list, description="The job ids, in this order; empty = every job with a schedule (its own or a preset's), soonest first.")
    tags: list[str] = Field(default_factory=list, description="Only the jobs carrying one of these tags (with `jobs` empty).")
    max_rows: int = Field(default=12, ge=1, le=100, description="Rows shown.")


Widget = Annotated[Union[ChartWidget, KpiWidget, TableWidget, StatWidget, ListWidget, BarsWidget, TextWidget, LinksWidget, JobsWidget], Field(discriminator="type")]


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
    l: dict[str, dict[str, Any]] = Field(default_factory=dict, description="Per-language overrides (``l.fr = { label = \"Application\" }``).")
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


_PRESETS = Literal["all", "today", "yesterday", "last7", "last30", "thisMonth", "lastMonth", "thisQuarter", "thisYear", "lastYear", "last12"]


class PeriodBasis(BaseModel):
    """One date the period can compare against ("Document date", "Activity date"): the result
    column (or dictionary item) carrying it. The chosen basis rides as the ``PERIOD_BASIS`` bind
    too, for queries that pick the column themselves."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(description="Stable key, sent as the PERIOD_BASIS bind.")
    label: str = Field(description="What the picker shows.")
    column: str | None = Field(default=None, description="Result column holding this date.")
    dd: str | None = Field(default=None, description="Dictionary item holding this date (matched through the screens' column hints).")


class DashboardPeriod(BaseModel):
    """The dashboard's period: a date range filter in the bar. Every widget query receives the
    range as the ``PERIOD_FROM`` / ``PERIOD_TO`` binds (and ``PREV_FROM`` / ``PREV_TO`` for the
    period before), to use as ``WHERE d BETWEEN :PERIOD_FROM AND :PERIOD_TO``; a query that does
    not mention them is left alone. When ``column`` or ``dd`` names the date column, a widget
    whose screen carries it is also wrapped server-side with that range, so a plain query
    (``SELECT … FROM sales``) is filtered without changing its SQL."""

    model_config = ConfigDict(extra="forbid")

    column: str | None = Field(default=None, description="Result column holding the date every widget filters on.")
    dd: str | None = Field(default=None, description="Dictionary item holding the date (matched through the screens' column hints).")
    default: _PRESETS | None = Field(default=None, description="The preset the dashboard opens on. Blank: the app's ``default_period``.")
    basis: list[PeriodBasis] = Field(default_factory=list, description="Alternative dates to compare against; the first is the default. Empty: ``column`` / ``dd`` is the only axis.")


class Dashboard(BaseModel):
    """One ``[dashboards.<scope>.<id>]`` — title + description + the widget list + optional filters.
    Both ``id`` (the inner key) and ``connector`` (the outer *scope* key — the owning app) are
    injected from the TOML path by :func:`parse_dashboards`, mirroring screens/charts. A widget may
    still read from *any* connector; the scope is just which app the dashboard belongs to. The
    dashboard's public id (menus / URL / permissions) is the qualified ``<scope>.<id>`` —
    :attr:`qualified_id`."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", description="Stable id (matches the inner TOML key).", json_schema_extra={"x_group": "Advanced"})
    label: str = Field(description="Display label — the dashboard's title in lists and menus.")
    description: str | None = Field(default=None, description="Optional longer description.")
    l: dict[str, dict[str, Any]] = Field(default_factory=dict, description="Per-language overrides of ``label`` / ``description`` (``l.fr = { label = \"Synthèse d'audit\" }``).")
    connector: str = Field(
        default="",
        description="Owning app/connector — the ``<scope>`` path key; injected from it. Widgets may read any connector.",
        json_schema_extra={"x_group": "Advanced"},
    )
    filters: list[DashboardFilter] = Field(
        default_factory=list,
        description=(
            "Optional dashboard-level filters — render as a bar above the widget grid; picking "
            "a value refetches every applicable widget with that bind. Empty = no bar shown."
        ),
    )
    widgets: list[Widget] = Field(default_factory=list, description="Widgets, in display order.")
    period: DashboardPeriod | None = Field(default=None, description="A date range filter in the bar, bound to every widget. Blank: no period.")
    # Upgrade-safety flag — see Screen.override for the full story. Default False.
    override: bool = Field(
        default=False,
        description=(
            "Mark this dashboard as a customer customisation that an upgrade-package import "
            "should NOT overwrite. Default False — operators opt in after forking."
        ),
    )

    @property
    def qualified_id(self) -> str:
        """The public id — ``<scope>.<id>`` (e.g. ``nomasx1.overview``). Used by menus
        (``target``), the ``/dashboard/<id>`` route, and ``dashboard:<id>`` permissions."""
        return f"{self.connector}.{self.id}" if self.connector else self.id


class DashboardsFile(BaseModel):
    """Top-level ``dashboards.toml`` shape — ``[dashboards.<scope>.<id>]``: a dict of *scope* (the
    owning connector/app) → dict of *id* → dashboard. Mirrors ``screens.toml`` / ``charts.toml``."""

    model_config = ConfigDict(extra="forbid")

    # scope (connector) -> dashboard id -> dashboard
    dashboards: dict[str, dict[str, Dashboard]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_ids(self) -> DashboardsFile:
        for scope, by_id in self.dashboards.items():
            for did, d in by_id.items():
                if d.id and d.id != did:
                    raise ValueError(f"dashboard {scope}.{did!r}: ``id`` field is {d.id!r}, must match its key")
                if d.connector and d.connector != scope:
                    raise ValueError(
                        f"dashboard {scope}.{did!r}: ``connector`` field is {d.connector!r}, "
                        f"must match its scope key {scope!r}"
                    )
        return self

    def iter_dashboards(self) -> Iterator[tuple[str, str, Dashboard]]:
        """Walk every dashboard as ``(scope, id, dashboard)``."""
        for scope, by_id in self.dashboards.items():
            for did, d in by_id.items():
                yield scope, did, d

    def get(self, scope: str, dashboard_id: str) -> Dashboard | None:
        """Resolve a dashboard by ``(scope, id)``; ``None`` when absent."""
        return self.dashboards.get(scope, {}).get(dashboard_id)

    def find(self, qualified_id: str) -> Dashboard | None:
        """Resolve a dashboard by its qualified ``<scope>.<id>`` public id; ``None`` when absent."""
        scope, _, did = qualified_id.partition(".")
        return self.get(scope, did) if did else None


def parse_dashboards(data: dict[str, Any]) -> DashboardsFile:
    """Validate a raw TOML dict into a :class:`DashboardsFile`. Each entry's ``id`` (inner key) and
    ``connector`` (outer scope key) are injected from the path when omitted (same convention as
    charts/screens)."""
    ds = data.get("dashboards") or {}
    if isinstance(ds, dict):
        for scope, by_id in ds.items():
            if not isinstance(by_id, dict):
                continue
            for did, d in by_id.items():
                if not isinstance(d, dict):
                    continue
                if not d.get("id"):
                    d["id"] = did
                if not d.get("connector"):
                    d["connector"] = scope
    return DashboardsFile.model_validate(data)


def load_dashboards(path: Path | str) -> DashboardsFile:
    """Load and validate ``dashboards.toml``. A missing file yields an empty :class:`DashboardsFile`."""
    path = Path(path)
    if not path.exists():
        return DashboardsFile()
    with path.open("rb") as fh:
        return parse_dashboards(tomllib.load(fh))
