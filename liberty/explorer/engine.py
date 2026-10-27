"""The aggregate engine — one dialect-neutral ``SELECT dims, aggs … GROUP BY dims`` per request.

The request names fields of the model (``entity.COLUMN``), as rows (dimensions, a date one
with a bucket), measures (an aggregate of a numeric field, or ``count(*)``), filters (a flat
bag), a period on a date field, a sort and a Top N. The plan:

* **Row sources** are the primary entity (the hub, unless the request names another) plus
  the entities of the dimensions. At most one of them may be ``many`` — rows never multiply
  twice.
* Every other entity a dimension, measure or filter needs is joined through its relations:
  a ``one`` entity is LEFT JOINed; a ``many`` entity that is not a row source is reached by a
  **correlated sub-query** for a measure (``SUM((SELECT SUM(x.COL) FROM (…) x WHERE x.K =
  p.K))``) and by ``EXISTS`` for a filter — it needs a direct relation to a joined entity.
* Each entity is its connector query, row-level scope already applied
  (:meth:`SQLConnector.scoped_select`); every entity of one request must run on one pool.
* SQL does the grouping at day level and sums; Python does the date buckets (week / month /
  quarter / year), the merge of the groups that collapse, ``avg`` from sum + count, the sort
  and the Top N — so the statement is the same on Oracle, Postgres and SQLite. Column
  references are UNQUOTED so each dialect's identifier folding resolves them; the result
  keys are upper-cased.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from liberty.auth.scope import DataScope
from liberty.connectors.registry import ConnectorRegistry
from liberty.connectors.sql import _coerce_value, _filter_sql_type, _from_jde_julian, _to_jde_julian
from liberty.explorer.config import Aggregate, ExplorerModel, ExplorerRelation
from liberty.explorer.model import ExplorerField, ModelView
from liberty.screens.config import ScreensFile

Bucket = Literal["day", "week", "month", "quarter", "year"]
SCAN_CAP = 50_000
MAX_LIMIT = 10_000
_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
_TEXT_OPS = {"contains", "equals", "notEquals", "startsWith", "endsWith", "in", "empty", "notEmpty"}
_ORDER_OPS = {"equals", "notEquals", "lt", "le", "gt", "ge", "in", "empty", "notEmpty"}


class ExplorerError(ValueError):
    """A request the model cannot serve (unknown field, two many-side row sources…) — a 422."""


class DimSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    bucket: Bucket | None = None

    @property
    def key(self) -> str:
        return f"{self.field}@{self.bucket}" if self.bucket else self.field


class MeasureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str | None = Field(default=None, description="A numeric field; omitted = count(*).")
    agg: Aggregate = "sum"

    @property
    def key(self) -> str:
        return f"{self.agg}({self.field})" if self.field else "count(*)"


class SortSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    dir: Literal["asc", "desc"] = "asc"


class PeriodSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field: str
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None


class AggregateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str | None = Field(default=None, description="The primary entity; the hub when omitted.")
    rows: list[DimSpec] = Field(default_factory=list)
    measures: list[MeasureSpec] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict, description="`key`, `key__op`, `key__from`, `key__to`; a list value = IN.")
    period: PeriodSpec | None = None
    sort: list[SortSpec] = Field(default_factory=list)
    limit: int = Field(default=1000, ge=1, le=MAX_LIMIT)
    debug: bool = False


# ── planning ───────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class _Join:
    entity: str
    parent: str
    pairs: list[tuple[str, str]]     # (entity column, parent column)


@dataclass(slots=True)
class Plan:
    primary: str
    joined: list[str] = field(default_factory=list)        # in FROM order, primary first
    joins: list[_Join] = field(default_factory=list)
    many_sources: list[str] = field(default_factory=list)

    def alias(self, entity: str) -> str:
        return "e_" + entity.replace("-", "_")


def _hop_pairs(rel: ExplorerRelation, entity: str) -> list[tuple[str, str]]:
    """The relation's pairs as ``(entity column, other column)``."""
    if rel.from_ == entity:
        return [(a, b) for a, b in rel.on]
    return [(b, a) for a, b in rel.on]


def _path(model: ExplorerModel, start: set[str], target: str) -> list[tuple[str, str, ExplorerRelation]]:
    """Breadth-first: the hops ``(from_entity, to_entity, relation)`` from any entity of *start*
    to *target*; empty when already there."""
    if target in start:
        return []
    prev: dict[str, tuple[str, ExplorerRelation]] = {}
    seen = set(start)
    frontier = list(start)
    while frontier:
        cur = frontier.pop(0)
        for r in model.relations:
            nxt = r.to if r.from_ == cur else r.from_ if r.to == cur else None
            if nxt is None or nxt in seen:
                continue
            seen.add(nxt)
            prev[nxt] = (cur, r)
            if nxt == target:
                hops: list[tuple[str, str, ExplorerRelation]] = []
                node = target
                while node not in start:
                    parent, rel = prev[node]
                    hops.append((parent, node, rel))
                    node = parent
                hops.reverse()
                return hops
            frontier.append(nxt)
    raise ExplorerError(f"entity {target!r} is not related to {sorted(start)}")


def _join(plan: Plan, model: ExplorerModel, entity: str) -> None:
    for parent, child, rel in _path(model, set(plan.joined), entity):
        plan.joins.append(_Join(entity=child, parent=parent, pairs=_hop_pairs(rel, child)))
        plan.joined.append(child)


def _ident(name: str) -> str:
    if not _IDENT.match(name or ""):
        raise ExplorerError(f"invalid column name {name!r}")
    return name


# ── filters ────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class _Filter:
    fld: ExplorerField
    op: str
    value: Any


def parse_filters(view: ModelView, bag: dict[str, Any]) -> list[_Filter]:
    """The flat bag → typed filters. ``key`` is the value, ``key__op`` the operator (default:
    ``contains`` on text, ``equals`` otherwise, ``in`` for a list), ``key__from`` / ``key__to``
    a range on a date or number."""
    out: list[_Filter] = []
    ops = {k[:-4]: str(v) for k, v in bag.items() if k.endswith("__op")}
    for k, v in bag.items():
        if k.endswith("__op"):
            continue
        base, suffix = (k[:-6], "from") if k.endswith("__from") else (k[:-4], "to") if k.endswith("__to") else (k, None)
        fld = view.field(base)
        if fld is None:
            raise ExplorerError(f"unknown field {base!r}")
        if suffix is not None:
            if v in (None, ""):
                continue
            out.append(_Filter(fld, "ge" if suffix == "from" else "le", v))
            continue
        op = ops.get(k)
        if op is None:
            op = "in" if isinstance(v, list) else ("contains" if fld.type == "string" else "equals")
        if op not in ("empty", "notEmpty") and (v is None or v == "" or v == []):
            continue
        allowed = _TEXT_OPS if fld.type == "string" else _ORDER_OPS
        if op not in allowed:
            raise ExplorerError(f"operator {op!r} is not valid on {fld.key} ({fld.type})")
        out.append(_Filter(fld, op, v))
    return out


def _typed(fld: ExplorerField, v: Any, trim: bool) -> Any:
    """A filter value as the Python type the driver expects for the column."""
    if fld.type == "number":
        return _coerce_value(v, "number") if isinstance(v, str) else v
    fmt = (fld.format or "").lower()
    if fld.type == "date":
        if fmt == "jdedate":
            return _to_jde_julian(v)
        return date.fromisoformat(str(v)[:10]) if isinstance(v, str) else v
    if fld.type == "datetime":
        return datetime.fromisoformat(str(v)) if isinstance(v, str) else v
    return v.strip() if (trim and isinstance(v, str)) else v


def _predicate(col: str, fld: ExplorerField, op: str, binds: list[str], dialect: str, trim: bool) -> str:
    """One predicate on *col* (an aliased reference) for *op* with the given bind names."""
    is_text = fld.type == "string"
    ref = f"TRIM({col})" if (is_text and trim) else col
    if op == "empty":
        return f"({col} IS NULL OR TRIM({col}) = '')" if is_text else f"{col} IS NULL"
    if op == "notEmpty":
        return f"({col} IS NOT NULL AND TRIM({col}) <> '')" if is_text else f"{col} IS NOT NULL"
    if op == "in":
        return f"{ref} IN ({', '.join(f':{b}' for b in binds)})"
    b = binds[0]
    pv = f"CAST(:{b} AS {_filter_sql_type(None, dialect)})" if is_text else f":{b}"
    if op == "equals":
        return f"{ref} = {pv}"
    if op == "notEquals":
        return f"{ref} <> {pv}"
    if op == "startsWith":
        return f"LOWER({ref}) LIKE LOWER({pv} || '%')"
    if op == "endsWith":
        return f"LOWER({ref}) LIKE LOWER('%' || {pv})"
    if op == "contains":
        return f"LOWER({ref}) LIKE LOWER('%' || {pv} || '%')"
    sym = {"lt": "<", "le": "<=", "gt": ">", "ge": ">="}[op]
    return f"{ref} {sym} {pv}"


# ── the aggregate run ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class _Measure:
    spec: MeasureSpec
    fld: ExplorerField | None
    alias: str              # m<i>


@dataclass(slots=True)
class _Dim:
    spec: DimSpec
    fld: ExplorerField
    alias: str              # d<i>


def _bucket_label(v: Any, bucket: Bucket | None, fld: ExplorerField) -> Any:
    """A day-level value (date / datetime / ISO text / JDE Julian) → the bucket's label."""
    if v is None:
        return None
    if (fld.format or "").lower() == "jdedate":
        v = _from_jde_julian(v)
    d: date | None = None
    if isinstance(v, datetime):
        d = v.date()
    elif isinstance(v, date):
        d = v
    elif isinstance(v, str):
        try:
            d = date.fromisoformat(v[:10])
        except ValueError:
            return v
    if d is None:
        return v
    if bucket is None:
        return d.isoformat() if fld.type == "date" or not isinstance(v, datetime) else v.isoformat(timespec="seconds")
    if bucket == "day":
        return d.isoformat()
    if bucket == "week":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    if bucket == "month":
        return f"{d.year}-{d.month:02d}"
    if bucket == "quarter":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    return str(d.year)


def _num(v: Any) -> float | int | None:
    if v is None:
        return None
    return v if isinstance(v, (int, float)) else float(v)


def _merge(acc: dict[str, Any], row: dict[str, Any], measures: list[_Measure]) -> None:
    for m in measures:
        if m.spec.agg == "avg":
            for suffix in ("_s", "_c"):
                k = m.alias + suffix
                a, b = acc.get(k), _num(row.get(k))
                acc[k] = b if a is None else a if b is None else a + b
            continue
        a, b = acc.get(m.alias), _num(row.get(m.alias))
        if a is None:
            acc[m.alias] = b
        elif b is not None:
            acc[m.alias] = min(a, b) if m.spec.agg == "min" else max(a, b) if m.spec.agg == "max" else a + b


async def run_aggregate(
    connectors: ConnectorRegistry, screens: ScreensFile | None, view: ModelView, req: AggregateRequest, *,
    scope: DataScope | None = None, debug: bool = False,
) -> dict[str, Any]:
    model = view.model
    app = view.app
    primary = req.entity or model.hub
    if primary not in model.entities:
        raise ExplorerError(f"unknown entity {primary!r}")

    # Resolve the fields.
    dims = [_Dim(spec=d, fld=_need(view, d.field), alias=f"d{i}") for i, d in enumerate(req.rows)]
    for d in dims:
        if d.spec.bucket and d.fld.type not in ("date", "datetime"):
            raise ExplorerError(f"{d.fld.key} is not a date: no bucket")
    measures: list[_Measure] = []
    for i, m in enumerate(req.measures):
        fld = _need(view, m.field) if m.field else None
        if fld is not None and m.agg not in ("count", "count_distinct") and fld.type != "number":
            raise ExplorerError(f"{fld.key} is not numeric: {m.agg} needs a number (use count)")
        measures.append(_Measure(spec=m, fld=fld, alias=f"m{i}"))
    if not measures:
        measures.append(_Measure(spec=MeasureSpec(), fld=None, alias="m0"))
    bucketed = any(d.spec.bucket not in (None, "day") for d in dims)
    for m in measures:
        if m.spec.agg == "count_distinct" and bucketed:
            raise ExplorerError("count_distinct cannot be merged across week / month / quarter / year buckets")
    filters = parse_filters(view, req.filters)
    period_fld = _need(view, req.period.field) if req.period else None
    if period_fld is not None and period_fld.type not in ("date", "datetime", "string"):
        raise ExplorerError(f"{period_fld.key} is not a date: no period")

    # The plan: row sources, joins, the entities reached by sub-query.
    plan = Plan(primary=primary, joined=[primary])
    row_entities = [primary] + [d.fld.entity for d in dims]
    plan.many_sources = sorted({e for e in row_entities if e != model.hub and model.entities[e].cardinality == "many"})
    if len(plan.many_sources) > 1:
        raise ExplorerError(f"rows from two many-side entities ({', '.join(plan.many_sources)}) would multiply: keep one")
    for d in dims:
        _join(plan, model, d.fld.entity)
    def reach(entity: str) -> tuple[str, list[tuple[str, str]]] | None:
        """Join a one-side entity; for a many-side one not joined, return the (parent, pairs) of
        the direct relation a sub-query correlates on; None when joined inline."""
        if entity in plan.joined:
            return None
        if model.entities[entity].cardinality == "one" or entity == model.hub:
            _join(plan, model, entity)
            return None
        hops = _path(model, set(plan.joined), entity)
        if len(hops) != 1:
            raise ExplorerError(f"{entity!r} needs a direct relation to the analysed entities ({', '.join(plan.joined)})")
        parent, _child, rel = hops[0]
        return parent, _hop_pairs(rel, entity)
    sub_measures: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    for m in measures:
        if m.fld is not None:
            r = reach(m.fld.entity)
            if r is not None:
                if m.spec.agg == "count_distinct":
                    raise ExplorerError(f"count_distinct on {m.fld.key} needs its entity among the rows")
                sub_measures[m.fld.entity] = r
    sub_filters: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    for f in filters:
        r = reach(f.fld.entity)
        if r is not None:
            sub_filters[f.fld.entity] = r
    if period_fld is not None:
        r = reach(period_fld.entity)
        if r is not None:
            sub_filters[period_fld.entity] = r

    # One pool for everything.
    conn_of = {}
    for e in plan.joined + list(sub_measures) + list(sub_filters):
        conn_of[e] = connectors.sql(model.entities[e].connector or app)
    pools = {c.pool_name for c in conn_of.values()}
    if len(pools) > 1:
        raise ExplorerError(f"the entities run on different pools ({', '.join(sorted(pools))}): they cannot be combined")
    conn = conn_of[primary]
    dialect = conn._resolve_dialect()  # noqa: SLF001 — same family
    trim = conn._pools.trim_strings(conn.pool_name)  # noqa: SLF001

    # Each entity's SQL (scope applied) with binds renamed to be unique.
    binds: dict[str, Any] = {}
    sqls: dict[str, str] = {}
    async def entity_sql(e: str) -> str:
        if e in sqls:
            return sqls[e]
        ent = model.entities[e]
        screen = (screens.screens.get(app) or {}).get(ent.screen) if (screens is not None and ent.screen) else None
        sql, b = await conn_of[e].scoped_select(
            ent.query, dict(ent.params), column_hints=list(screen.columns) if screen is not None and screen.columns else None,
            dictionary_scope=app, scope=scope,
        )
        for k, v in b.items():
            nk = f"q_{e.replace('-', '_')}_{k}"
            sql = re.sub(rf":{re.escape(k)}\b", f":{nk}", sql)
            binds[nk] = v
        sqls[e] = sql
        return sql

    a = plan.alias
    select: list[str] = []
    group: list[str] = []
    for d in dims:
        ref = f"{a(d.fld.entity)}.{_ident(d.fld.name)}"
        select.append(f"{ref} AS {d.alias}")
        group.append(ref)

    def agg_expr(agg: str, ref: str) -> str:
        return {"sum": f"SUM({ref})", "avg": f"SUM({ref})", "min": f"MIN({ref})", "max": f"MAX({ref})",
                "count": f"COUNT({ref})", "count_distinct": f"COUNT(DISTINCT {ref})"}[agg]

    for m in measures:
        if m.fld is None:
            select.append(f"COUNT(*) AS {m.alias}")
            continue
        e = m.fld.entity
        if e in sub_measures:
            parent, pairs = sub_measures[e]
            x = "x_" + e.replace("-", "_")
            corr = " AND ".join(f"{x}.{_ident(ec)} = {a(parent)}.{_ident(pc)}" for ec, pc in pairs)
            inner = f"(SELECT {agg_expr(m.spec.agg, f'{x}.{_ident(m.fld.name)}')} FROM (\n{await entity_sql(e)}\n) {x} WHERE {corr})"
            outer = "MIN" if m.spec.agg == "min" else "MAX" if m.spec.agg == "max" else "SUM"
            if m.spec.agg == "avg":
                cnt = f"(SELECT COUNT({x}.{_ident(m.fld.name)}) FROM (\n{await entity_sql(e)}\n) {x} WHERE {corr})"
                select.append(f"SUM({inner}) AS {m.alias}_s")
                select.append(f"SUM({cnt}) AS {m.alias}_c")
            else:
                select.append(f"{outer}({inner}) AS {m.alias}")
            continue
        ref = f"{a(e)}.{_ident(m.fld.name)}"
        if m.spec.agg == "avg":
            select.append(f"SUM({ref}) AS {m.alias}_s")
            select.append(f"COUNT({ref}) AS {m.alias}_c")
        else:
            select.append(f"{agg_expr(m.spec.agg, ref)} AS {m.alias}")

    # WHERE: inline predicates on joined entities, EXISTS for the sub-query ones.
    where: list[str] = []
    exists_preds: dict[str, list[str]] = {}
    n = 0
    def add_pred(f_fld: ExplorerField, op: str, value: Any, col_alias: str) -> str:
        nonlocal n
        names: list[str] = []
        values = value if isinstance(value, list) else [value]
        if op in ("empty", "notEmpty"):
            values = []
        for v in values:
            name = f"f{n}"
            n += 1
            binds[name] = _typed(f_fld, v, trim)
            names.append(name)
        return _predicate(f"{col_alias}.{_ident(f_fld.name)}", f_fld, op, names, dialect, trim)
    for f in filters:
        e = f.fld.entity
        if e in sub_filters:
            exists_preds.setdefault(e, []).append(add_pred(f.fld, f.op, f.value, "x_" + e.replace("-", "_")))
        else:
            where.append(add_pred(f.fld, f.op, f.value, a(e)))
    if period_fld is not None and req.period is not None:
        e = period_fld.entity
        target = exists_preds.setdefault(e, []) if e in sub_filters else where
        col_alias = "x_" + e.replace("-", "_") if e in sub_filters else a(e)
        if req.period.from_ is not None:
            target.append(add_pred(period_fld, "ge", req.period.from_.isoformat(), col_alias))
        if req.period.to is not None:
            hi: Any = req.period.to.isoformat()
            if period_fld.type == "datetime":
                target.append(add_pred(period_fld, "lt", (req.period.to + timedelta(days=1)).isoformat(), col_alias))
            else:
                target.append(add_pred(period_fld, "le", hi, col_alias))
    for e, preds in exists_preds.items():
        parent, pairs = sub_filters[e]
        x = "x_" + e.replace("-", "_")
        corr = " AND ".join(f"{x}.{_ident(ec)} = {a(parent)}.{_ident(pc)}" for ec, pc in pairs)
        where.append(f"EXISTS (SELECT 1 FROM (\n{await entity_sql(e)}\n) {x} WHERE {corr} AND {' AND '.join(preds)})")

    # FROM + joins.
    parts = [f"SELECT {', '.join(select)}", f"FROM (\n{await entity_sql(primary)}\n) {a(primary)}"]
    for j in plan.joins:
        on = " AND ".join(f"{a(j.entity)}.{_ident(ec)} = {a(j.parent)}.{_ident(pc)}" for ec, pc in j.pairs)
        parts.append(f"LEFT JOIN (\n{await entity_sql(j.entity)}\n) {a(j.entity)} ON {on}")
    if where:
        parts.append("WHERE " + "\n  AND ".join(where))
    if group:
        parts.append("GROUP BY " + ", ".join(group))
    sql = "\n".join(parts)

    _cols, raw, scan_truncated = await conn.run_select(sql, binds, max_rows=SCAN_CAP)

    # Buckets + merge.
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in raw:
        up = {str(k).upper(): v for k, v in r.items()}
        key = tuple(_bucket_label(up.get(d.alias.upper()), d.spec.bucket, d.fld) for d in dims)
        acc = groups.get(key)
        if acc is None:
            acc = {d.spec.key: key[i] for i, d in enumerate(dims)}
            groups[key] = acc
        _merge(acc, {k.lower(): v for k, v in up.items()}, measures)
    out_rows: list[dict[str, Any]] = []
    for acc in groups.values():
        row = {d.spec.key: acc[d.spec.key] for d in dims}
        for m in measures:
            if m.spec.agg == "avg":
                s, c = acc.get(m.alias + "_s"), acc.get(m.alias + "_c")
                row[m.spec.key] = (s / c) if (s is not None and c) else None
            else:
                row[m.spec.key] = acc.get(m.alias)
        out_rows.append(row)

    # Sort: requested keys, else the dimensions ascending, else the first measure descending.
    sort = [(s.key, s.dir) for s in req.sort] or [(d.spec.key, "asc") for d in dims] or [(measures[0].spec.key, "desc")]
    known = {d.spec.key for d in dims} | {m.spec.key for m in measures}
    for k, _d in sort:
        if k not in known:
            raise ExplorerError(f"cannot sort on {k!r}: not in the result")
    for k, direction in reversed(sort):
        out_rows.sort(key=lambda r: (r[k] is None, r[k] if r[k] is not None else 0), reverse=(direction == "desc"))
    total_groups = len(out_rows)
    truncated = total_groups > req.limit
    out_rows = out_rows[: req.limit]

    columns: list[dict[str, Any]] = []
    for d in dims:
        c = {"key": d.spec.key, "kind": "dimension", "field": d.fld.key, "entity": d.fld.entity, "label": d.fld.label, "type": d.fld.type}
        if d.spec.bucket:
            c["bucket"] = d.spec.bucket
        if d.fld.rule and not d.spec.bucket:
            c["rule"] = d.fld.rule
        if d.fld.format and not d.spec.bucket:
            c["format"] = d.fld.format
        columns.append(c)
    for m in measures:
        c = {"key": m.spec.key, "kind": "measure", "aggregate": m.spec.agg, "type": "number",
             "field": m.fld.key if m.fld else None, "entity": m.fld.entity if m.fld else primary, "label": m.fld.label if m.fld else ""}
        if m.fld and m.fld.format and m.spec.agg in ("sum", "avg", "min", "max"):
            c["format"] = m.fld.format
        columns.append(c)
    result: dict[str, Any] = {
        "mode": "aggregate", "entity": primary, "columns": columns, "rows": out_rows,
        "groups": total_groups, "scanned": len(raw), "truncated": truncated, "scan_truncated": scan_truncated,
    }
    if debug:
        result["sql"] = sql
        result["binds"] = {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in binds.items()}
    return result


def _need(view: ModelView, key: str) -> ExplorerField:
    f = view.field(key)
    if f is None:
        raise ExplorerError(f"unknown field {key!r}")
    return f
