"""A config dashboard (``dashboards.toml``) exported as a document — the server-side reading of
what the page shows: every readable widget's query run with the same binds the page sends (the
filter bar's picks resolved through the widget's screen ``dd`` map, the period's binds and the
date-column range), then shaped into the Explorer's :class:`~liberty.explorer.report.Section`
list so the same renderers produce the Excel workbook and the PDF synthesis (charts drawn by
:mod:`liberty.explorer.chart_svg`, KPI cards, the cover with the period and the filters).

The shaping ports the page's rules (``services/chartData.ts`` ``buildChartData``,
``KpiWidget.computeKpi``, ``widgetFilters.ts``): consecutive KPI widgets become one card row, a
chart widget its x / series table, a table widget its rows (the operator's column subset, the
row cap). Pure functions; :func:`dashboard_sections` takes the query runner as a callable so
the web layer threads its own (permissions, scope, pool, language).
"""

from __future__ import annotations

import calendar
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from liberty.explorer.report import MAX_ROWS, Section, _minus_months

ALL_DATES = ("0001-01-01", "9999-12-31")
EMPTY_LABEL = "(empty)"
_WORDS = {"en": {"kpis": "Key figures"}, "fr": {"kpis": "Chiffres clés"}}

RunQuery = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]
DdMap = Callable[[str, str], dict[str, str] | None]


@dataclass(slots=True)
class PeriodPick:
    """What the page's period control holds: the range (both empty = all dates) and the basis."""
    date_from: date | None = None
    date_to: date | None = None
    basis: str = ""


# ── periods ────────────────────────────────────────────────────────────────────


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def previous_period(start: date, end: date) -> tuple[date, date]:
    """The period before *start*..*end* — ``services/period.ts`` ``previousPeriod``: a year
    from January 1st → the year before; whole months → the same number of months before; any
    other range → the same number of days before."""
    to_end = end == _month_end(end)
    if start.month == 1 and start.day == 1 and end.year == start.year:
        pt = date(end.year - 1, end.month, min(end.day, calendar.monthrange(end.year - 1, end.month)[1]))
        return date(start.year - 1, 1, 1), _month_end(pt) if to_end else pt
    if start.day == 1:
        nxt = end + timedelta(days=1)
        months = (nxt.year - start.year) * 12 + (nxt.month - start.month)
        if not to_end or months == 0:
            months += 1
        pf, pt = _minus_months(start, months), _minus_months(end, months)
        return pf, _month_end(pt) if to_end else pt
    pt = start - timedelta(days=1)
    return pt - (end - start), pt


def period_params(period: dict[str, Any] | None, pick: PeriodPick | None, dd_map: dict[str, str] | None) -> dict[str, str]:
    """The period's binds for one widget (``buildWidgetPeriodParams``): the four range binds,
    the basis, and the ``<COL>_from`` / ``<COL>_to`` pair when the dashboard names the date
    column (directly or through the widget screen's ``dd`` map)."""
    if not period or pick is None:
        return {}
    all_dates = pick.date_from is None or pick.date_to is None
    f, t = ALL_DATES if all_dates else (pick.date_from.isoformat(), pick.date_to.isoformat())  # type: ignore[union-attr]
    pf, pt = ALL_DATES if all_dates else tuple(d.isoformat() for d in previous_period(pick.date_from, pick.date_to))  # type: ignore[arg-type]
    out = {"PERIOD_FROM": f, "PERIOD_TO": t, "PREV_FROM": pf, "PREV_TO": pt}
    bases = period.get("basis") or []
    basis = next((b for b in bases if b.get("value") == pick.basis), bases[0] if bases else None)
    if basis:
        out["PERIOD_BASIS"] = str(basis["value"])
    if all_dates:
        return out
    own = bool(basis and (basis.get("column") or basis.get("dd")))
    column = basis.get("column") if own else period.get("column")  # type: ignore[union-attr]
    dd = basis.get("dd") if own else period.get("dd")  # type: ignore[union-attr]
    col = column or ((dd_map or {}).get(dd) if dd else None)
    if col:
        out[f"{col}_from"] = f
        out[f"{col}_to"] = t
    return out


def filter_params(filters: list[dict[str, Any]], values: dict[str, str], dd_map: dict[str, str] | None) -> dict[str, str]:
    """The filter bar's picks as binds for one widget (``buildWidgetFilterParams``): a pick binds
    the widget screen's column carrying the filter's ``dictionary_key``; no screen, no column,
    no pick → nothing."""
    if not filters or not dd_map:
        return {}
    out: dict[str, str] = {}
    for f in filters:
        v = values.get(f["id"])
        col = dd_map.get(f["dictionary_key"]) if v else None
        if col:
            out[col] = v
            out[f"{col}_op"] = "equals"
    return out


# ── shaping ────────────────────────────────────────────────────────────────────


def _num(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip()) if v.strip() else None
        except ValueError:
            return None
    return None


def resolve_column(result: dict[str, Any], name: str) -> str:
    """The result's own spelling of *name* (Postgres lower-cases, Oracle upper-cases)."""
    names = [c["name"] for c in result.get("columns") or []]
    if name in names:
        return name
    low = name.lower()
    return next((n for n in names if n.lower() == low), name)


def _label(result: dict[str, Any], name: str) -> str:
    c = next((c for c in result.get("columns") or [] if c["name"] == name), None)
    return (c.get("label") if c else None) or name


def _agg(values: list[float | None], agg: str) -> float | None:
    if agg == "count":
        return float(len(values))
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    if agg == "sum":
        return sum(nums)
    if agg == "avg":
        return sum(nums) / len(nums)
    if agg == "min":
        return min(nums)
    if agg == "max":
        return max(nums)
    return nums[0]


def kpi_value(result: dict[str, Any], column: str, aggregation: str) -> float | None:
    """``KpiWidget.computeKpi``: the aggregate of one column over every row; ``count`` counts rows."""
    if aggregation == "count":
        return float(len(result.get("rows") or []))
    col = resolve_column(result, column)
    return _agg([_num(r.get(col)) for r in result.get("rows") or []], aggregation)


def kpi_display(value: float | None, fmt: str | None, lang: str | None) -> Any:
    """A KPI as the section stores it: a number, or the formatted text for ``percent`` /
    ``currency`` (what the card shows)."""
    from liberty.explorer.chart_svg import fmt_number
    if value is None:
        return None
    if fmt == "percent":
        return f"{fmt_number(value * 100, lang, 1)} %"
    if fmt == "currency":
        return f"{fmt_number(value, lang, 2)} €"
    return value


def _category(v: Any) -> str:
    if v is None:
        return EMPTY_LABEL
    return v if isinstance(v, str) else str(v)


def chart_section(title: str, widget: dict[str, Any], result: dict[str, Any], note: str | None = None) -> Section:
    """``buildChartData`` as a section: one row per x category (first seen, or sorted with
    ``sort_by_x``), one column per y series aggregated per the spec; ``area`` draws as a line."""
    spec = widget.get("spec") or {}
    x = spec.get("x") or ""
    ys = list(spec.get("y") or [])
    kind = {"area": "line"}.get(spec.get("type"), spec.get("type") or "bar")
    if not x or not ys:
        return Section(title, "chart", [], note=note, chart=kind)
    xk = resolve_column(result, x)
    yks = [resolve_column(result, y) for y in ys]
    headers = [_label(result, xk), *[_label(result, yk) for yk in yks]]
    rows_in = result.get("rows") or []
    agg = spec.get("aggregation") or "sum"
    if agg == "none":
        rows = [[_category(r.get(xk)), *[_num(r.get(yk)) for yk in yks]] for r in rows_in]
    else:
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in rows_in:
            groups.setdefault(_category(r.get(xk)), []).append(r)
        keys = sorted(groups) if spec.get("sort_by_x") else list(groups)
        rows = [[k, *[_agg([_num(r.get(yk)) for r in groups[k]], agg) for yk in yks]] for k in keys]
    return Section(title, "chart", headers, rows[:MAX_ROWS], note=note, truncated=len(rows) > MAX_ROWS, chart=kind, stacked=bool(spec.get("stacked")))


def table_section(title: str, widget: dict[str, Any], result: dict[str, Any], note: str | None = None) -> Section:
    """The table widget's rows: the operator's column subset (case-insensitive) or every
    non-hidden column, capped at ``max_rows``."""
    cols = result.get("columns") or []
    wanted = widget.get("columns") or []
    if wanted:
        by_low = {c["name"].lower(): c for c in cols}
        chosen = [c for n in wanted if (c := by_low.get(n.lower()))]
    else:
        chosen = [c for c in cols if not c.get("hidden")]
    rows = result.get("rows") or []
    cap = widget.get("max_rows")
    if cap:
        rows = rows[:cap]
    return Section(title, "table", [(c.get("label") or c["name"]) for c in chosen], [[r.get(c["name"]) for c in chosen] for r in rows[:MAX_ROWS]], note=note, truncated=len(rows) > MAX_ROWS)


def kpi_section(title: str, cards: list[tuple[str, Any]]) -> Section:
    return Section(title, "kpi", [h for h, _v in cards], [[v for _h, v in cards]])


async def dashboard_sections(
    dashboard: dict[str, Any], *, run: RunQuery, dd_map: DdMap, values: dict[str, str] | None = None, pick: PeriodPick | None = None,
    lang: str | None = None,
) -> list[Section]:
    """The sections of a resolved dashboard (the ``GET /api/dashboards/{id}`` shape, so only the
    widgets the caller may read). *run* executes ``(connector, query, params)`` and returns the
    wire result; *dd_map* gives a widget's screen ``dd`` map (``None`` without a screen). A
    widget whose query fails becomes an error section; the others still render."""
    words = _WORDS["fr" if (lang or "").lower().startswith("fr") else "en"]
    filters = dashboard.get("filters") or []
    period = dashboard.get("period")
    out: list[Section] = []
    cards: list[tuple[str, Any]] = []

    def flush() -> None:
        if cards:
            out.append(kpi_section(words["kpis"], list(cards)))
            cards.clear()

    for w in dashboard.get("widgets") or []:
        c, q = w.get("connector"), w.get("query")
        if not c or not q:
            continue
        ddm = dd_map(c, q)
        params = {**filter_params(filters, values or {}, ddm), **period_params(period, pick, ddm)}
        title = w.get("label") or q
        try:
            res = await run(c, q, params)
        except Exception as exc:  # noqa: BLE001 — one widget's failure must not sink the document
            flush()
            out.append(Section(title, "error", ["error"], [[str(exc)]]))
            continue
        if w.get("type") == "kpi":
            cards.append((title, kpi_display(kpi_value(res, w.get("column") or "", w.get("aggregation") or "count"), w.get("format"), lang)))
            continue
        flush()
        if w.get("type") == "chart":
            out.append(chart_section(title, w, res))
        else:
            out.append(table_section(title, w, res))
    flush()
    return out
