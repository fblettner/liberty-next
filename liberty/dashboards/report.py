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
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from liberty.explorer.report import MAX_ROWS, Section, _minus_months

ALL_DATES = ("0001-01-01", "9999-12-31")
DD_PARAM_PREFIX = "_dd_"            # a dictionary bind: the server resolves the item (web/connectors.py split_dd_binds)
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
    elif dd:
        out[f"{DD_PARAM_PREFIX}{dd}_from"] = f
        out[f"{DD_PARAM_PREFIX}{dd}_to"] = t
    return out


def filter_params(filters: list[dict[str, Any]], values: dict[str, str], dd_map: dict[str, str] | None) -> dict[str, str]:
    """The filter bar's picks as binds for one widget (``buildWidgetFilterParams``): a pick binds
    the widget screen's column carrying the filter's ``dictionary_key``, else goes as a
    dictionary bind for the server to resolve; no pick → nothing."""
    out: dict[str, str] = {}
    for f in filters or []:
        v = values.get(f["id"])
        if not v:
            continue
        col = (dd_map or {}).get(f["dictionary_key"])
        if col:
            out[col] = v
            out[f"{col}_op"] = "equals"
        else:
            out[f"{DD_PARAM_PREFIX}{f['dictionary_key']}"] = v
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
    if spec.get("series"):
        # one series per distinct value of the series column, each the aggregate of y[0]
        sk, yk = resolve_column(result, spec["series"]), yks[0]
        groups: dict[str, dict[str, list[float | None]]] = {}
        names: list[str] = []
        for r in rows_in:
            x, sname = _category(r.get(xk)), _category(r.get(sk))
            if sname not in names:
                names.append(sname)
            groups.setdefault(x, {}).setdefault(sname, []).append(_num(r.get(yk)))
        keys = sorted(groups) if spec.get("sort_by_x") else list(groups)
        rows = [[k, *[(_agg(groups[k][n], agg) if n in groups[k] else None) for n in names]] for k in keys]
        return Section(title, "chart", [_label(result, xk), *names], rows[:MAX_ROWS], note=note, truncated=len(rows) > MAX_ROWS, chart=kind, stacked=bool(spec.get("stacked")))
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


_TEMPLATE = re.compile(r"\{([A-Za-z0-9_]+)(?::([a-z]+))?\}")


def format_value(v: Any, fmt: str | None, unit: str | None, lang: str | None) -> str:
    """A figure as the synthesis widgets show it: ``integer`` / ``number`` / ``currency`` /
    ``percent`` (a 0–1 ratio), the unit after; text as is."""
    from liberty.explorer.chart_svg import fmt_number
    n = _num(v)
    if n is None:
        return "" if v is None else str(v)
    if fmt == "percent":
        out = f"{fmt_number(n * 100, lang, 1)}\u00a0%"
    elif fmt == "currency":
        out = f"{fmt_number(n, lang, 0 if n == int(n) else 2)}\u00a0€"
    elif fmt == "integer":
        out = fmt_number(n, lang, 0)
    else:
        out = fmt_number(n, lang)
    return f"{out}\u00a0{unit}" if unit and fmt != "currency" else out


def fill_template(template: str, row: dict[str, Any], result: dict[str, Any], lang: str | None) -> str:
    """``{COLUMN}`` / ``{COLUMN:format}`` placeholders filled from *row* (names resolved to the
    result's spelling); an unknown column stays as written."""
    def sub(m: re.Match[str]) -> str:
        col = resolve_column(result, m.group(1))
        if col not in row:
            return m.group(0)
        return format_value(row[col], m.group(2), None, lang)
    return _TEMPLATE.sub(sub, template)


def stat_card(widget: dict[str, Any], result: dict[str, Any], lang: str | None) -> tuple[str, Any, str | None]:
    """The figure of a stat widget (its first row): ``(label, figure text, sub line)``."""
    rows = result.get("rows") or []
    row = rows[0] if rows else {}
    col = resolve_column(result, widget.get("value_column") or "")
    text = format_value(row.get(col), widget.get("format"), widget.get("unit"), lang) if row else ""
    sub = fill_template(widget["sub"], row, result, lang) if (widget.get("sub") and row) else None
    return widget.get("label") or widget.get("query") or "", text or None, sub


def list_section(title: str, widget: dict[str, Any], result: dict[str, Any], lang: str | None) -> Section:
    """A list widget as a table: title, subtitle, figure, count, tag — the columns it shows."""
    rows_in = (result.get("rows") or [])[: widget.get("max_rows") or 10]
    cols = [("title_column", _label(result, resolve_column(result, widget["title_column"])))]
    for key, default in (("subtitle_column", ""), ("value_column", ""), ("count_column", ""), ("tag_column", "")):
        if widget.get(key):
            cols.append((key, _label(result, resolve_column(result, widget[key])) or default))
    out: list[list[Any]] = []
    for r in rows_in:
        line: list[Any] = []
        for key, _h in cols:
            c = resolve_column(result, widget[key])
            v = r.get(c)
            if key == "value_column":
                v = format_value(v, widget.get("format"), widget.get("unit"), lang)
            elif key == "count_column" and widget.get("count_label") and v is not None:
                v = widget["count_label"].replace("{n}", format_value(v, "integer", None, lang))
            line.append(v)
        out.append(line)
    return Section(title, "table", [h for _k, h in cols], out)


def bars_section(title: str, widget: dict[str, Any], result: dict[str, Any], lang: str | None) -> Section:
    """A bars widget as a table: label, sub label, figure, share."""
    rows_in = (result.get("rows") or [])[: widget.get("max_rows") or 10]
    lc, vc = resolve_column(result, widget["label_column"]), resolve_column(result, widget["value_column"])
    sc = resolve_column(result, widget["sub_label_column"]) if widget.get("sub_label_column") else None
    vals = [_num(r.get(vc)) or 0.0 for r in rows_in]
    base = (sum(vals) if widget.get("share", "total") == "total" else max(vals, default=0.0)) or 0.0
    headers = [_label(result, lc), *([_label(result, sc)] if sc else []), _label(result, vc), "%"]
    out = []
    for r, v in zip(rows_in, vals, strict=True):
        out.append([r.get(lc), *([r.get(sc)] if sc else []), format_value(r.get(vc), widget.get("format"), widget.get("unit"), lang), format_value(v / base if base else 0.0, "percent", None, lang)])
    return Section(title, "table", headers, out, note=widget.get("note"))


def kpi_section(title: str, cards: list[tuple[str, Any, str | None]]) -> Section:
    """One card row: ``(label, value, sub line)`` per card."""
    return Section(title, "kpi", [h for h, _v, _s in cards], [[v for _h, v, _s in cards]], subs=[sb for _h, _v, sb in cards])


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
    cards: list[tuple[str, Any, str | None]] = []

    def flush() -> None:
        if cards:
            out.append(kpi_section(words["kpis"], list(cards)))
            cards.clear()

    for w in dashboard.get("widgets") or []:
        if w.get("type") == "text":
            flush()
            out.append(Section(w.get("label") or "", "text", [], note=str(w.get("markdown") or "")))
            continue
        if w.get("type") == "links":
            flush()
            fr = (lang or "").lower().startswith("fr")
            out.append(Section(w.get("label") or ("Liens" if fr else "Links"), "table", ["Lien" if fr else "Link", "Description"],
                               [[link.get("label") or link.get("menu") or link.get("url") or "", link.get("description") or ""] for link in w.get("links") or []]))
            continue
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
            cards.append((title, kpi_display(kpi_value(res, w.get("column") or "", w.get("aggregation") or "count"), w.get("format"), lang), None))
            continue
        if w.get("type") == "stat":
            cards.append(stat_card(w, res, lang))
            continue
        flush()
        if w.get("type") == "chart":
            out.append(chart_section(title, w, res))
        elif w.get("type") == "list":
            out.append(list_section(title, w, res, lang))
        elif w.get("type") == "bars":
            out.append(bars_section(title, w, res, lang))
        else:
            out.append(table_section(title, w, res))
    flush()
    return out
