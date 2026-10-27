"""Headless runs and exports — an analysis or a dashboard rendered to **sections**, then to an
Excel workbook (one sheet per section) or a PDF (through the reports pipeline, markdown first).

This is the one server-side reading of the frontend's ``AnalysisDefinition`` (the object the
page saves): :func:`definition_to_aggregate` / :func:`definition_to_detail` build the engine
requests from it, with the period presets resolved here (the same rules as
``services/period.ts``) and an optional **forced window** (the digest's "last month"). The
export route, the dashboards and the C5 digest all come through :func:`build_sections`, so a
figure is the same everywhere.

A chart section exports as its data table (x, one column per series); a KPI section as one
row of totals; a pivot section as the hierarchy flattened, group rows bold with their subtotals.
"""

from __future__ import annotations

import calendar
import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from liberty.auth.scope import DataScope
from liberty.connectors.registry import ConnectorRegistry
from liberty.explorer.engine import AggregateRequest, DetailRequest, ExplorerError, run_aggregate, run_detail
from liberty.explorer.model import ModelView
from liberty.screens.config import ScreensFile

MAX_ROWS = 3000
ADDITIVE = ("sum", "count")

# ── periods ────────────────────────────────────────────────────────────────────


def _minus_months(d: date, n: int) -> date:
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def period_range(preset: str, today: date | None = None) -> tuple[date, date] | None:
    """The dates a preset covers as of *today* — ``services/period.ts`` ``rangeForPreset``.
    ``None`` for ``all`` / unknown (no period)."""
    t = today or date.today()
    if preset == "today":
        return t, t
    if preset == "yesterday":
        return t - timedelta(days=1), t
    if preset == "last7":
        return t - timedelta(days=6), t
    if preset == "last30":
        return t - timedelta(days=29), t
    if preset == "thisMonth":
        return date(t.year, t.month, 1), t
    if preset == "lastMonth":
        first = date(t.year, t.month, 1)
        prev_last = first - timedelta(days=1)
        return date(prev_last.year, prev_last.month, 1), prev_last
    if preset == "thisQuarter":
        return date(t.year, (t.month - 1) // 3 * 3 + 1, 1), t
    if preset == "thisYear":
        return date(t.year, 1, 1), t
    if preset == "lastYear":
        return date(t.year - 1, 1, 1), date(t.year - 1, 12, 31)
    if preset == "last12":
        return _minus_months(t, 12) + timedelta(days=1), t
    return None


# ── the definition → requests ──────────────────────────────────────────────────


def compile_filters(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """``filterRows`` → the flat bag (``services/explorer.ts`` ``compileFilters``)."""
    bag: dict[str, Any] = {}
    for r in rows or []:
        f, op, v, to = r.get("field"), r.get("op"), r.get("value"), r.get("valueTo")
        if not f:
            continue
        if op in ("empty", "notEmpty"):
            bag[f] = None
            bag[f"{f}__op"] = op
        elif op == "between":
            if v:
                bag[f"{f}__from"] = v
            if to:
                bag[f"{f}__to"] = to
        elif v in (None, ""):
            continue
        elif op == "in":
            bag[f] = [s.strip() for s in str(v).split(",") if s.strip()]
            bag[f"{f}__op"] = "in"
        else:
            bag[f] = v
            bag[f"{f}__op"] = op or "contains"
    return bag


def _period_of(definition: dict[str, Any], window: tuple[date, date] | None, today: date | None) -> dict[str, Any] | None:
    p = definition.get("period") or {}
    fld = p.get("field")
    if not fld:
        return None
    if window is not None:
        return {"field": fld, "from": window[0].isoformat(), "to": window[1].isoformat()}
    preset = p.get("preset") or "all"
    if preset == "custom":
        rng = p.get("range") or {}
        out: dict[str, Any] = {"field": fld}
        if rng.get("dateFrom"):
            out["from"] = rng["dateFrom"]
        if rng.get("dateTo"):
            out["to"] = rng["dateTo"]
        return out if len(out) > 1 else None
    r = period_range(preset, today)
    return {"field": fld, "from": r[0].isoformat(), "to": r[1].isoformat()} if r else None


def definition_to_aggregate(definition: dict[str, Any], *, window: tuple[date, date] | None = None, today: date | None = None) -> AggregateRequest:
    """The aggregate request of a saved definition (``buildAggregateRequest`` in Python)."""
    d = definition
    return AggregateRequest.model_validate({
        "entity": d.get("entity"),
        "calculated": d.get("calculated") or [],
        "rows": [({"field": r["field"], "bucket": r["bucket"]} if r.get("bucket") else {"field": r["field"]}) for r in d.get("rows") or []],
        "measures": [({"field": m["field"], "agg": m.get("agg") or "sum"} if m.get("field") else {"agg": "count"}) for m in d.get("measures") or []],
        "filters": compile_filters(d.get("filterRows") or []),
        "period": _period_of(d, window, today),
        "sort": d.get("sort") or [],
        "limit": int(d.get("limit") or 1000),
    })


def definition_to_detail(definition: dict[str, Any], *, window: tuple[date, date] | None = None, today: date | None = None, page_size: int = MAX_ROWS) -> DetailRequest:
    d = definition
    return DetailRequest.model_validate({
        "entity": d.get("entity"),
        "calculated": d.get("calculated") or [],
        "columns": [r["field"] for r in d.get("rows") or []],
        "filters": compile_filters(d.get("filterRows") or []),
        "period": _period_of(d, window, today),
        "sort": [s for s in (d.get("sort") or []) if "(" not in str(s.get("key", ""))],
        "page": 1, "page_size": min(page_size, MAX_ROWS),
    })


# ── sections ───────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Section:
    title: str
    kind: str                       # table | pivot | chart | kpi | detail
    headers: list[str]
    rows: list[list[Any]] = field(default_factory=list)
    bold: list[bool] = field(default_factory=list)      # per row: a group / total row
    note: str | None = None
    truncated: bool = False


def _label(c: dict[str, Any]) -> str:
    if c.get("kind") == "measure":
        agg = c.get("aggregate") or "sum"
        return f"{agg} {c.get('label') or ''}".strip() if c.get("field") else "count"
    b = c.get("bucket")
    return f"{c.get('label')} ({b})" if b else str(c.get("label") or c.get("key"))


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pivot_rows(rows: list[dict[str, Any]], row_keys: list[str], pivot_key: str | None, measure_key: str, additive: bool) -> tuple[list[str], list[list[Any]], list[bool]]:
    """The pivot flattened: one column per row level, the pivot columns, a total column when
    there are pivot columns; group rows carry the subtotal (additive measures) and are bold."""
    cols: list[str] = []
    seen: set[str] = set()
    tree: dict[str, Any] = {"children": {}, "cells": {}, "total": None, "order": []}

    def add(a: float | None, b: float | None) -> float | None:
        return b if a is None else a if b is None else a + b

    for r in rows:
        c = "" if pivot_key is None or r.get(pivot_key) is None else str(r[pivot_key])
        if c not in seen:
            seen.add(c)
            cols.append(c)
        v = _num(r.get(measure_key))
        node = tree
        if additive:
            node["cells"][c] = add(node["cells"].get(c), v)
            node["total"] = add(node["total"], v)
        for i, k in enumerate(row_keys):
            val = r.get(k)
            key = "" if val is None else str(val)
            child = node["children"].get(key)
            if child is None:
                child = {"value": val, "children": {}, "cells": {}, "total": None, "order": []}
                node["children"][key] = child
                node["order"].append(key)
            node = child
            if additive or i == len(row_keys) - 1:
                node["cells"][c] = add(node["cells"].get(c), v)
                node["total"] = add(node["total"], v)
    cols.sort(key=lambda x: (x == "", x))
    multi = len(cols) > 1 or pivot_key is not None
    out: list[list[Any]] = []
    bold: list[bool] = []

    def walk(node: dict[str, Any], level: int) -> None:
        for key in node["order"]:
            child = node["children"][key]
            group = bool(child["children"])
            cells = ["" for _ in row_keys]
            cells[level] = child["value"] if child["value"] is not None else ""
            vals = [child["cells"].get(c) for c in cols] if multi else []
            total = child["total"] if (additive or not group) else None
            out.append([*cells, *vals, total] if multi else [*cells, total])
            bold.append(group)
            walk(child, level + 1)

    walk(tree, 0)
    if additive:
        out.append(["Total", *["" for _ in row_keys[1:]], *([tree["cells"].get(c) for c in cols] if multi else []), tree["total"]])
        bold.append(True)
    headers = [*row_keys, *(cols if multi else []), "Total" if multi else measure_key]
    return headers, out, bold


def _chart_rows(result: dict[str, Any], measure_key: str | None, series_key: str | None) -> tuple[list[str], list[list[Any]]]:
    """x, one column per series (the second dimension, or the measures)."""
    cols = result["columns"]
    dims = [c for c in cols if c["kind"] == "dimension"]
    measures = [c for c in cols if c["kind"] == "measure"]
    if not measures:
        return [], []
    m = next((c for c in measures if c["key"] == measure_key), measures[0])
    series_col = next((d for d in dims if d["key"] == series_key), None) if series_key else (dims[1] if len(dims) >= 2 else None)
    x_col = next((d for d in dims if series_col is None or d["key"] != series_col["key"]), None)
    use_series = series_col is not None and (m.get("aggregate") in ADDITIVE)
    table: dict[str, dict[str, float]] = {}
    series: list[str] = []
    for r in result["rows"]:
        x = "" if x_col is None else ("" if r.get(x_col["key"]) is None else str(r[x_col["key"]]))
        row = table.setdefault(x, {})
        pairs = [(("" if r.get(series_col["key"]) is None else str(r[series_col["key"]])), _num(r.get(m["key"])))] if use_series else [(mm["key"], _num(r.get(mm["key"]))) for mm in measures]
        for s, v in pairs:
            if s not in series:
                series.append(s)
            if v is not None:
                row[s] = row.get(s, 0.0) + v
    xs = list(table)
    if x_col is not None and (x_col.get("bucket") or x_col.get("type") in ("date", "datetime")):
        xs.sort()
    headers = [x_col["label"] if x_col else "x", *series]
    return headers, [[x, *[table[x].get(s) for s in series]] for x in xs]


async def build_sections(
    connectors: ConnectorRegistry, screens: ScreensFile | None, view: ModelView, items: list[tuple[str, dict[str, Any]]], *,
    window: tuple[date, date] | None = None, scope: DataScope | None = None, today: date | None = None,
) -> list[Section]:
    """One section per (title, definition), in the definition's own view."""
    out: list[Section] = []
    for title, d in items:
        kind = d.get("view") or "table"
        try:
            if kind == "detail":
                res = await run_detail(connectors, screens, view, definition_to_detail(d, window=window, today=today), scope=scope)
                cols = res["columns"]
                out.append(Section(title, "detail", [c["label"] for c in cols], [[r.get(c["key"]) for c in cols] for r in res["rows"]], truncated=res["total"] > len(res["rows"])))
                continue
            res = await run_aggregate(connectors, screens, view, definition_to_aggregate(d, window=window, today=today), scope=scope)
        except ExplorerError as exc:
            out.append(Section(title, "error", ["error"], [[str(exc)]]))
            continue
        cols = res["columns"]
        note = _note(d, res, window)
        if kind == "pivot":
            dims = [c for c in cols if c["kind"] == "dimension"]
            measures = [c for c in cols if c["kind"] == "measure"]
            m = next((c for c in measures if c["key"] == ((d.get("pivot") or {}).get("measure"))), measures[0] if measures else None)
            if m is None:
                out.append(Section(title, "pivot", [], note=note))
                continue
            pivot_col = next((x for x in dims if x["key"] == d.get("pivotColumn")), dims[-1] if len(dims) >= 2 else None)
            row_dims = [x for x in dims if pivot_col is None or x["key"] != pivot_col["key"]]
            headers, rows, bold = pivot_rows(res["rows"], [x["key"] for x in row_dims], pivot_col["key"] if pivot_col else None, m["key"], m.get("aggregate") in ADDITIVE)
            headers = [*[x["label"] for x in row_dims], *headers[len(row_dims):-1], "Total" if len(headers) - len(row_dims) > 1 else _label(m)]
            out.append(Section(title, "pivot", headers, rows[:MAX_ROWS], bold[:MAX_ROWS], note, truncated=len(rows) > MAX_ROWS))
        elif kind in ("bar", "line", "pie"):
            headers, rows = _chart_rows(res, (d.get("chart") or {}).get("measure"), d.get("pivotColumn"))
            out.append(Section(title, "chart", headers, rows[:MAX_ROWS], note=note, truncated=len(rows) > MAX_ROWS))
        elif kind == "kpi":
            measures = [c for c in cols if c["kind"] == "measure"]
            totals = []
            for c in measures:
                vals = [v for v in (_num(r.get(c["key"])) for r in res["rows"]) if v is not None]
                agg = c.get("aggregate")
                totals.append(None if not vals else min(vals) if agg == "min" else max(vals) if agg == "max" else sum(vals) / len(vals) if agg == "avg" else sum(vals))
            out.append(Section(title, "kpi", [_label(c) for c in measures], [totals], note=note))
        else:
            out.append(Section(title, "table", [_label(c) for c in cols], [[r.get(c["key"]) for c in cols] for r in res["rows"][:MAX_ROWS]], note=note, truncated=res["truncated"] or len(res["rows"]) > MAX_ROWS))
    return out


def _note(d: dict[str, Any], res: dict[str, Any], window: tuple[date, date] | None) -> str | None:
    parts: list[str] = []
    p = _period_of(d, window, None)
    if p and (p.get("from") or p.get("to")):
        parts.append(f"period {p.get('from', '…')} → {p.get('to', '…')} on {p['field']}")
    bag = compile_filters(d.get("filterRows") or [])
    flt = [f"{k} {bag.get(k + '__op', '=')} {v}" for k, v in bag.items() if not k.endswith("__op") and not k.endswith("__from") and not k.endswith("__to")]
    flt += [f"{k[:-6]} ≥ {v}" for k, v in bag.items() if k.endswith("__from")] + [f"{k[:-4]} ≤ {v}" for k, v in bag.items() if k.endswith("__to")]
    if flt:
        parts.append("filters: " + ", ".join(flt))
    if res.get("groups") is not None:
        parts.append(f"{res['groups']} group(s) from {res.get('scanned', 0)} row(s)")
    return " · ".join(parts) or None


# ── renderers ──────────────────────────────────────────────────────────────────


def _xl_value(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, str, date)):
        if isinstance(v, str):
            return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", v)
        return v
    return str(v)


def sections_to_xlsx(sections: list[Section]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    wb.remove(wb.active)
    taken: set[str] = set()
    for i, s in enumerate(sections):
        name = re.sub(r"[\[\]\*\?/\\:]", " ", s.title).strip()[:28] or f"Sheet{i + 1}"
        base, n = name, 2
        while name.lower() in taken:
            name, n = f"{base[:25]} {n}", n + 1
        taken.add(name.lower())
        ws = wb.create_sheet(name)
        if s.note:
            ws.append([s.note])
            ws.cell(row=1, column=1).font = Font(italic=True)
        ws.append([_xl_value(h) for h in s.headers])
        head_row = ws.max_row
        for c in range(1, len(s.headers) + 1):
            ws.cell(row=head_row, column=c).font = Font(bold=True)
        for k, row in enumerate(s.rows):
            ws.append([_xl_value(v) for v in row])
            if k < len(s.bold) and s.bold[k]:
                for c in range(1, len(row) + 1):
                    ws.cell(row=ws.max_row, column=c).font = Font(bold=True)
        if s.truncated:
            ws.append([f"… truncated at {MAX_ROWS} rows"])
    if not sections:
        wb.create_sheet("Empty")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _md_cell(v: Any, bold: bool) -> str:
    if v is None:
        return ""
    s = f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
    s = s.replace("|", "\\|").replace("\n", " ")
    return f"**{s}**" if bold and s else s


def sections_to_markdown(sections: list[Section], title: str) -> str:
    lines = [f"# {title}", ""]
    for s in sections:
        if len(sections) > 1:
            lines += [f"## {s.title}", ""]
        if s.note:
            lines += [f"*{s.note}*", ""]
        if not s.headers:
            lines += ["_no data_", ""]
            continue
        lines.append("| " + " | ".join(_md_cell(h, False) for h in s.headers) + " |")
        lines.append("|" + "|".join(" --- " for _ in s.headers) + "|")
        for k, row in enumerate(s.rows):
            b = k < len(s.bold) and s.bold[k]
            lines.append("| " + " | ".join(_md_cell(v, b) for v in row) + " |")
        if s.truncated:
            lines.append(f"| … truncated at {MAX_ROWS} rows |" + " |" * (len(s.headers) - 1))
        lines.append("")
    return "\n".join(lines)


def sections_to_pdf(sections: list[Section], title: str, *, app_name: str = "", branding: dict[str, Any] | None = None) -> bytes:
    """Through the reports pipeline (needs the ``reports`` extra — raises
    :class:`liberty.reports.render.ReportsUnavailableError` otherwise)."""
    from liberty.reports.render import render_content
    from liberty.reports.schema import ReportContent

    content = ReportContent(markdown=sections_to_markdown(sections, title), title=title, filename_base=re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-").lower() or "explorer")
    body, _ct, _fn = render_content(content, "pdf", app_name=app_name, report_title=title, branding=branding)
    return body
