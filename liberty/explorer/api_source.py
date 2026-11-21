"""An Explorer entity read through an API.

An entity is a query or a table of an SQL connector — or, here, an **endpoint with rows** of an API
connector (``rows_field`` set: the endpoints a screen reads, :mod:`liberty.connectors.api_rows`).
The engine cannot push a ``GROUP BY`` to an API, so this source does the other half itself: it
reads the endpoint's rows once — under the caller's data scope, up to :data:`SCAN_CAP` — and
filters, groups, measures, sorts and pages them in Python, answering exactly what
:func:`liberty.explorer.engine.run_aggregate` / ``run_detail`` answer for an SQL entity. The page,
the saved analyses, the exports and the assistant's tools therefore need to know nothing about it.

What such an entity cannot do: it **stands alone** (no join with another entity — a field of
another entity in the request is refused, with the reason) and it has no calculated field (the
calculated-field language compiles to SQL). The cost is the read's: every request calls the
endpoint; ``scan_truncated`` says when it returned as many rows as the cap, or said it had more.

An application's own faster path — a service that aggregates on the server (JD Edwards' data
service does) — is the application's to add on top; this is the mechanism for any API.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from liberty.auth.scope import DataScope
from liberty.connectors.api_rows import api_connector, api_row_events, rows_endpoint
from liberty.connectors.registry import ConnectorRegistry
from liberty.connectors.sql import StreamDone, StreamMeta, StreamRows
from liberty.explorer.config import ExplorerEntity
from liberty.explorer.model import ExplorerField, ModelView
from liberty.screens.config import ScreensFile

SCAN_CAP = 50_000


def api_entity(connectors: ConnectorRegistry, app: str, entity: ExplorerEntity) -> bool:
    """True when *entity* names an endpoint with rows of an API connector."""
    if not entity.query:
        return False
    conn = api_connector(connectors, entity.connector or app)
    return conn is not None and rows_endpoint(conn, entity.query) is not None


def endpoint_values(connectors: ConnectorRegistry, app: str, entity: ExplorerEntity, filters: list[tuple[ExplorerField, str, Any]]) -> dict[str, Any]:
    """The filters the ENDPOINT can apply itself: an ``equals`` on a field that is one of its
    parameters is sent to it (a JDE form lists nothing without its search type; a service narrows
    on the server what would otherwise be read whole). The engine still applies every filter to
    the rows that come back, so sending one never changes the answer — only what is read."""
    conn = api_connector(connectors, entity.connector or app)
    endpoint = rows_endpoint(conn, entity.query or "") if conn is not None else None
    if endpoint is None:
        return {}
    names = {p.name.lower(): p.name for p in endpoint.params if not (endpoint.limit_param and p.name.lower() == endpoint.limit_param.lower())}
    out: dict[str, Any] = {}
    for fld, op, value in filters:
        name = names.get(fld.name.lower())
        if name and op == "equals" and not isinstance(value, list) and value not in (None, ""):
            out[name] = str(value).strip()
    return out


async def read_entity(
    connectors: ConnectorRegistry, screens: ScreensFile | None, app: str, entity: ExplorerEntity, *,
    language: str | None = None, scope: DataScope | None = None, max_rows: int = SCAN_CAP, values: dict[str, Any] | None = None,
) -> tuple[list[Any], list[dict[str, Any]], bool]:
    """The endpoint's ``(columns, rows, truncated)`` — the columns labelled through the screen's
    hints and the dictionary, the rows the caller's data scope lets through. *values*: parameters of
    the endpoint filled from the request's filters (:func:`endpoint_values`), over the entity's own."""
    conn = api_connector(connectors, entity.connector or app)
    if conn is None:
        raise ValueError(f"{entity.connector or app}: not an API connector")
    screen = (screens.screens.get(app) or {}).get(entity.screen) if screens is not None and entity.screen else None
    columns: list[Any] = []
    rows: list[dict[str, Any]] = []
    truncated = False
    async for ev in api_row_events(
        conn, entity.query or "", {**entity.params, **(values or {})}, dictionary=connectors.dictionary, language=language, max_rows=max_rows,
        column_hints=list(screen.columns) if screen is not None and screen.columns else None, dictionary_scope=app, scope=scope,
    ):
        if isinstance(ev, StreamMeta):
            columns = list(ev.columns)
        elif isinstance(ev, StreamRows):
            rows.extend(ev.rows)
        elif isinstance(ev, StreamDone):
            truncated = bool(ev.truncated)
    return columns, rows, truncated


# ── the request's fields ───────────────────────────────────────────────────────────────────────

def _field(view: ModelView, primary: str, key: str) -> ExplorerField:
    from liberty.explorer.engine import ExplorerError

    ent, _, _name = key.partition(".")
    if ent == "calc":
        raise ExplorerError("a calculated field is not available on an entity read through an API")
    fld = view.field(key)
    if fld is None:
        raise ExplorerError(f"unknown field {key!r}")
    if fld.entity != primary:
        raise ExplorerError(f"{key}: an entity read through an API stands alone — it cannot be joined with {fld.entity!r}")
    return fld


def _getter(rows: list[dict[str, Any]]):
    """``(row, field) → value`` whatever the case the endpoint's columns come in."""
    names = {str(k).lower(): k for r in rows[:1] for k in r}

    def get(row: dict[str, Any], fld: ExplorerField) -> Any:
        k = names.get(fld.name.lower())
        if k is None or k not in row:
            k = next((x for x in row if str(x).lower() == fld.name.lower()), None)
        return row.get(k) if k is not None else None
    return get


# ── filters, as the SQL predicates read ────────────────────────────────────────────────────────

def _number(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    try:
        return float(str(v).replace(" ", "").replace(",", ""))
    except ValueError:
        return None


def _text(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _as_number(v: Any) -> Any:
    """A numeric field's value as a number (an API often writes its figures as text) — whole when it is;
    what is not a number is left as it came."""
    n = _number(v)
    if n is None:
        return v
    return int(n) if n == int(n) and not isinstance(v, float) else n


def matches(fld: ExplorerField, op: str, value: Any, cell: Any) -> bool:
    """Whether *cell* passes ``<field> <op> <value>`` — the meaning of the engine's SQL predicate:
    text compared trimmed and without regard to case, numbers as numbers, a date by its day."""
    from liberty.explorer.engine import _as_day

    if op == "empty":
        return cell is None or (fld.type == "string" and _text(cell) == "")
    if op == "notEmpty":
        return not matches(fld, "empty", None, cell)
    if cell is None:
        return False
    values = value if isinstance(value, list) else [value]
    if fld.type == "string":
        got = _text(cell).lower()
        want = [_text(v).lower() for v in values]
        if op == "in":
            return got in want
        w = want[0] if want else ""
        return {"equals": got == w, "notEquals": got != w, "startsWith": got.startswith(w), "endsWith": got.endswith(w),
                "contains": w in got}.get(op, False)
    if fld.type in ("date", "datetime"):
        got_d = _as_day(cell, fld)
        want_d = [_as_day(str(v), fld) if not isinstance(v, date) else v for v in values]
        # a JDE Julian value given as a filter is already a day number: compared as a day too
        want_d = [w if isinstance(w, date) else None for w in want_d]
        if got_d is None or not want_d or want_d[0] is None:
            return False
        a, b = got_d, want_d[0]
        if op == "in":
            return a in [w for w in want_d if w is not None]
    else:
        na = _number(cell)
        nb = [_number(v) for v in values]
        if na is None or not nb or nb[0] is None:
            return False
        if op == "in":
            return na in [x for x in nb if x is not None]
        a, b = na, nb[0]   # type: ignore[assignment]
    return {"equals": a == b, "notEquals": a != b, "lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}.get(op, False)   # type: ignore[operator]


def _parse_filters(view: ModelView, primary: str, bag: dict[str, Any]) -> list[tuple[ExplorerField, str, Any]]:
    """The engine's flat bag (``key``, ``key__op``, ``key__from``, ``key__to``) as typed filters."""
    from liberty.explorer.engine import _ORDER_OPS, _TEXT_OPS, ExplorerError

    out: list[tuple[ExplorerField, str, Any]] = []
    ops = {k[:-4]: str(v) for k, v in bag.items() if k.endswith("__op")}
    for k, v in bag.items():
        if k.endswith("__op"):
            continue
        base, suffix = (k[:-6], "from") if k.endswith("__from") else (k[:-4], "to") if k.endswith("__to") else (k, None)
        fld = _field(view, primary, base)
        if suffix is not None:
            if v not in (None, ""):
                out.append((fld, "ge" if suffix == "from" else "le", v))
            continue
        op = ops.get(k) or ("in" if isinstance(v, list) else ("contains" if fld.type == "string" else "equals"))
        if op not in ("empty", "notEmpty") and (v is None or v == "" or v == []):
            continue
        if op not in (_TEXT_OPS if fld.type == "string" else _ORDER_OPS):
            raise ExplorerError(f"operator {op!r} is not valid on {fld.key} ({fld.type})")
        out.append((fld, op, v))
    return out


def _period_filters(view: ModelView, primary: str, period: Any) -> list[tuple[ExplorerField, str, Any]]:
    from liberty.explorer.engine import ExplorerError

    if period is None:
        return []
    fld = _field(view, primary, period.field)
    if fld.type not in ("date", "datetime", "string"):
        raise ExplorerError(f"{fld.key} is not a date: no period")
    out: list[tuple[ExplorerField, str, Any]] = []
    if period.from_ is not None:
        out.append((fld, "ge", period.from_.isoformat()))
    if period.to is not None:
        out.append((fld, "lt", (period.to + timedelta(days=1)).isoformat()) if fld.type == "datetime" else (fld, "le", period.to.isoformat()))
    return out


def _kept(rows: list[dict[str, Any]], filters: list[tuple[ExplorerField, str, Any]], get: Any) -> list[dict[str, Any]]:
    if not filters:
        return rows
    return [r for r in rows if all(matches(fld, op, value, get(r, fld)) for fld, op, value in filters)]


# ── aggregate ──────────────────────────────────────────────────────────────────────────────────

def aggregate_rows(view: ModelView, req: Any, rows: list[dict[str, Any]], *, scan_truncated: bool = False) -> dict[str, Any]:
    """The aggregate answer of *req* over the entity's *rows* — the shape of the SQL engine's."""
    from liberty.explorer.engine import ExplorerError, MeasureSpec, _sort_rows, bucket_label

    primary = req.entity or view.model.hub
    if req.calculated:
        raise ExplorerError("a calculated field is not available on an entity read through an API")
    dims = [(d, _field(view, primary, d.field)) for d in req.rows]
    for d, fld in dims:
        if d.bucket and fld.type not in ("date", "datetime"):
            raise ExplorerError(f"{fld.key} is not a date: no bucket")
    measures: list[tuple[Any, ExplorerField | None]] = []
    for m in req.measures:
        fld = _field(view, primary, m.field) if m.field else None
        if fld is not None and m.agg not in ("count", "count_distinct") and fld.type != "number":
            raise ExplorerError(f"{fld.key} is not numeric: {m.agg} needs a number (use count)")
        measures.append((m, fld))
    if not measures:
        measures.append((MeasureSpec(), None))
    get = _getter(rows)
    kept = _kept(rows, _parse_filters(view, primary, req.filters) + _period_filters(view, primary, req.period), get)

    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in kept:
        key = tuple(
            bucket_label(get(r, fld), d.bucket, fld) if fld.type in ("date", "datetime")
            else (_text(get(r, fld)) or None) if fld.type == "string" else _as_number(get(r, fld)) if fld.type == "number" else get(r, fld)
            for d, fld in dims)
        acc = groups.get(key)
        if acc is None:
            acc = groups[key] = {"n": 0}
        acc["n"] += 1
        for i, (m, fld) in enumerate(measures):
            if fld is None:
                continue
            v = get(r, fld)
            if m.agg == "count_distinct":
                if v is not None:
                    acc.setdefault(f"d{i}", set()).add(_text(v) if fld.type == "string" else v)
                continue
            if m.agg == "count":
                acc[f"c{i}"] = acc.get(f"c{i}", 0) + (0 if v is None else 1)
                continue
            n = _number(v)
            if n is None:
                continue
            acc[f"c{i}"] = acc.get(f"c{i}", 0) + 1
            cur = acc.get(f"v{i}")
            acc[f"v{i}"] = n if cur is None else min(cur, n) if m.agg == "min" else max(cur, n) if m.agg == "max" else cur + n

    out_rows: list[dict[str, Any]] = []
    for key, acc in groups.items():
        row: dict[str, Any] = {d.key: key[i] for i, (d, _f) in enumerate(dims)}
        for i, (m, fld) in enumerate(measures):
            if fld is None:
                row[m.key] = acc["n"]
            elif m.agg == "count_distinct":
                row[m.key] = len(acc.get(f"d{i}", ()))
            elif m.agg == "count":
                row[m.key] = acc.get(f"c{i}", 0)
            elif m.agg == "avg":
                row[m.key] = (acc[f"v{i}"] / acc[f"c{i}"]) if acc.get(f"c{i}") else None
            else:
                v = acc.get(f"v{i}")
                row[m.key] = int(v) if isinstance(v, float) and v.is_integer() else v      # 300.0 → 300: a whole figure reads as one
        out_rows.append(row)
    if not dims and not out_rows:                       # no row at all: one line of zeros, as a GROUP BY-less SELECT answers
        out_rows.append({m.key: (0 if (fld is None or m.agg in ("count", "count_distinct")) else None) for m, fld in measures})

    sort = [(s.key, s.dir) for s in req.sort] or [(d.key, "asc") for d, _f in dims] or [(measures[0][0].key, "desc")]
    known = {d.key for d, _f in dims} | {m.key for m, _f in measures}
    for k, _d in sort:
        if k not in known:
            raise ExplorerError(f"cannot sort on {k!r}: not in the result")
    _sort_rows(out_rows, sort)
    total = len(out_rows)

    columns: list[dict[str, Any]] = []
    for d, fld in dims:
        c: dict[str, Any] = {"key": d.key, "kind": "dimension", "field": fld.key, "entity": fld.entity, "label": fld.label, "type": fld.type}
        if d.bucket:
            c["bucket"] = d.bucket
        else:
            for name in ("rule", "format"):
                if getattr(fld, name):
                    c[name] = getattr(fld, name)
            if fld.decimals is not None:
                c["decimals"] = fld.decimals
        columns.append(c)
    for m, fld in measures:
        c = {"key": m.key, "kind": "measure", "aggregate": m.agg, "type": "number", "field": fld.key if fld else None,
             "entity": fld.entity if fld else primary, "label": fld.label if fld else ""}
        if fld and m.agg in ("sum", "avg", "min", "max"):
            if fld.format:
                c["format"] = fld.format
            if fld.decimals is not None:
                c["decimals"] = fld.decimals
        columns.append(c)
    return {"mode": "aggregate", "entity": primary, "columns": columns, "rows": out_rows[: req.limit], "groups": total,
            "scanned": len(rows), "truncated": total > req.limit, "scan_truncated": scan_truncated}


# ── detail ─────────────────────────────────────────────────────────────────────────────────────

def detail_rows(view: ModelView, req: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The detail answer of *req* over the entity's *rows*: the columns asked, filtered (a drill's
    cells included), sorted, one page."""
    from liberty.explorer.engine import ExplorerError, _display, _sort_rows, drill_filters

    primary = req.entity or view.model.hub
    if req.calculated:
        raise ExplorerError("a calculated field is not available on an entity read through an API")
    keys = list(req.columns) or [f.key for ev in view.entities if ev.entity.id == primary for f in ev.fields if not f.hidden]
    if not keys:
        raise ExplorerError("no column to show")
    flds = [_field(view, primary, k) for k in keys]
    get = _getter(rows)
    bag = {**drill_filters(req.drill), **req.filters}
    kept = _kept(rows, _parse_filters(view, primary, bag) + _period_filters(view, primary, req.period), get)
    shown = [{f.key: _as_number(get(r, f)) if f.type == "number" else _display(get(r, f), f) for f in flds} for r in kept]
    by_key = {f.key for f in flds}
    sort = [(s.key, s.dir) for s in req.sort]
    if not sort:
        pk = [f"{primary}.{k.upper()}" for k in view.model.entities[primary].key]
        sort = [(k, "asc") for k in pk if k in by_key] or [(flds[0].key, "asc")]
    for k, _d in sort:
        if k not in by_key:
            raise ExplorerError(f"cannot sort on {k!r}: not among the columns")
    _sort_rows(shown, sort)
    start = (req.page - 1) * req.page_size
    columns = []
    for f in flds:
        c: dict[str, Any] = {"key": f.key, "kind": "column", "field": f.key, "entity": f.entity, "label": f.label, "type": f.type}
        if f.rule:
            c["rule"] = f.rule
        if f.decimals is not None:
            c["decimals"] = f.decimals
        if f.format:
            c["format"] = f.format
        columns.append(c)
    return {"mode": "detail", "entity": primary, "columns": columns, "rows": shown[start:start + req.page_size], "total": len(shown),
            "page": req.page, "page_size": req.page_size}


# ── the entry points the engine hands over to ──────────────────────────────────────────────────

def _primary(view: ModelView, req: Any) -> ExplorerEntity:
    from liberty.explorer.engine import ExplorerError

    name = req.entity or view.model.hub
    if name not in view.model.entities:
        raise ExplorerError(f"unknown entity {name!r}")
    return view.model.entities[name]


def is_api_request(connectors: ConnectorRegistry, view: ModelView, req: Any) -> bool:
    """True when the request's primary entity is read through an API."""
    name = req.entity or view.model.hub
    entity = view.model.entities.get(name)
    return entity is not None and api_entity(connectors, view.app, entity)


async def run_aggregate(connectors: ConnectorRegistry, screens: ScreensFile | None, view: ModelView, req: Any, *,
                        scope: DataScope | None = None, debug: bool = False) -> dict[str, Any]:
    entity = _primary(view, req)
    sent = endpoint_values(connectors, view.app, entity, _parse_filters(view, entity.id, req.filters))
    _cols, rows, truncated = await read_entity(connectors, screens, view.app, entity, scope=scope, values=sent)
    out = aggregate_rows(view, req, rows, scan_truncated=truncated or len(rows) >= SCAN_CAP)
    if debug:
        out["sql"] = f"-- read through the API: {entity.connector or view.app}.{entity.query} ({len(rows)} rows), grouped by the engine"
    return out


async def run_detail(connectors: ConnectorRegistry, screens: ScreensFile | None, view: ModelView, req: Any, *,
                     scope: DataScope | None = None, debug: bool = False) -> dict[str, Any]:
    from liberty.explorer.engine import drill_filters

    entity = _primary(view, req)
    sent = endpoint_values(connectors, view.app, entity, _parse_filters(view, entity.id, {**drill_filters(req.drill), **req.filters}))
    _cols, rows, _truncated = await read_entity(connectors, screens, view.app, entity, scope=scope, values=sent)
    out = detail_rows(view, req, rows)
    if debug:
        out["sql"] = f"-- read through the API: {entity.connector or view.app}.{entity.query} ({len(rows)} rows), paged by the engine"
    return out
