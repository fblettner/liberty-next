"""The query engine — one dialect-neutral statement per request, aggregate or detail.

The request names fields of the model (``entity.COLUMN``, or ``calc.NAME`` for a calculated
field it declares), as rows (dimensions, a date one with a bucket), measures (an aggregate of a
numeric field, or ``count(*)``) or detail columns, filters (a flat bag), a period on a date
field, a sort and a Top N or a page. The plan:

* **Row sources** are the primary entity (the hub, unless the request names another) plus
  the entities of the dimensions / detail columns. At most one of them may be ``many`` — rows
  never multiply twice.
* Every other entity a dimension, measure, filter or calculated field needs is joined through
  its relations: a ``one`` entity is LEFT JOINed; a ``many`` entity that is not a row source is
  reached by a **correlated sub-query** for a measure (``SUM((SELECT SUM(x.COL) FROM (…) x WHERE
  x.K = p.K))``) and by ``EXISTS`` for a filter — it needs a direct relation to a joined entity.
* Each entity is its connector query, row-level scope already applied
  (:meth:`SQLConnector.scoped_select`); every entity of one request must run on one pool.
* Aggregate mode: SQL groups at day level and sums; Python does the date buckets (week / month /
  quarter / year), the merge of the groups that collapse, ``avg`` from sum + count, the sort
  and the Top N — the statement is the same on Oracle, Postgres and SQLite. Detail mode: the
  rows themselves, counted and paged. Column references are UNQUOTED so each dialect's
  identifier folding resolves them; the result keys are upper-cased.
* Calculated fields (:mod:`liberty.explorer.calc`) compile to SQL inside the same statement; a
  numeric one is a measure (or a dimension), any other a dimension. A drill from an aggregate
  cell turns the cell's dimension values into detail filters (a bucket label back into a range).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from liberty.auth.scope import DataScope
from liberty.connectors.registry import ConnectorRegistry
from liberty.connectors.sql import SQLConnector, _coerce_value, _filter_sql_type, _from_jde_julian, _to_jde_julian
from liberty.explorer.calc import CalcError, Compiler, Node, check_name, date_ref, render, result_type
from liberty.explorer.config import Aggregate, ExplorerModel, ExplorerRelation
from liberty.explorer.model import ExplorerField, ModelView
from liberty.screens.config import ScreensFile

Bucket = Literal["day", "week", "month", "quarter", "year"]
SCAN_CAP = 50_000
MAX_LIMIT = 10_000
MAX_PAGE = 5_000
_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
_TEXT_OPS = {"contains", "equals", "notEquals", "startsWith", "endsWith", "in", "empty", "notEmpty"}
_ORDER_OPS = {"equals", "notEquals", "lt", "le", "gt", "ge", "in", "empty", "notEmpty"}


class ExplorerError(ValueError):
    """A request the model cannot serve (unknown field, two many-side row sources…) — a 422."""


# ── request shapes ─────────────────────────────────────────────────────────────


class CalcSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    label: str | None = None
    expr: str


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


class DrillCell(BaseModel):
    """One dimension value of an aggregate cell: ``value`` is the cell's label (a bucket label
    for a bucketed date), ``None`` for an empty group."""

    model_config = ConfigDict(extra="forbid")
    field: str
    bucket: Bucket | None = None
    value: Any = None


class _BaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str | None = Field(default=None, description="The primary entity; the hub when omitted.")
    calculated: list[CalcSpec] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict, description="`key`, `key__op`, `key__from`, `key__to`; a list value = IN.")
    period: PeriodSpec | None = None
    sort: list[SortSpec] = Field(default_factory=list)
    debug: bool = False


class AggregateRequest(_BaseRequest):
    rows: list[DimSpec] = Field(default_factory=list)
    measures: list[MeasureSpec] = Field(default_factory=list)
    limit: int = Field(default=1000, ge=1, le=MAX_LIMIT)


class DetailRequest(_BaseRequest):
    columns: list[str] = Field(default_factory=list, description="Field keys; empty = the primary entity's visible fields.")
    drill: list[DrillCell] = Field(default_factory=list, description="An aggregate cell's dimension values, turned into filters.")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=MAX_PAGE)


class CalcTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str | None = None
    calculated: list[CalcSpec] = Field(default_factory=list)
    name: str


# ── buckets ────────────────────────────────────────────────────────────────────


def _as_day(v: Any, fld: ExplorerField) -> date | None:
    if v is None:
        return None
    if (fld.format or "").lower() == "jdedate":
        v = _from_jde_julian(v)
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def bucket_label(v: Any, bucket: Bucket | None, fld: ExplorerField) -> Any:
    """A day-level value (date / datetime / ISO text / JDE Julian) → the bucket's label, or the
    value itself normalised to ISO when there is no bucket."""
    if v is None:
        return None
    d = _as_day(v, fld)
    if d is None:
        return v
    if bucket is None:
        if isinstance(v, datetime) and fld.type == "datetime":
            return v.isoformat(timespec="seconds")
        if isinstance(v, str) and fld.type == "datetime":
            return v
        return d.isoformat()
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


def bucket_range(label: str, bucket: Bucket) -> tuple[date, date]:
    """The inverse of :func:`bucket_label`: the first and last day of the bucket."""
    try:
        if bucket == "day":
            d = date.fromisoformat(label[:10])
            return d, d
        if bucket == "week":
            y, w = label.split("-W")
            start = date.fromisocalendar(int(y), int(w), 1)
            return start, start + timedelta(days=6)
        if bucket == "month":
            y, m = (int(x) for x in label.split("-"))
            return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
        if bucket == "quarter":
            y, q = label.split("-Q")
            m = (int(q) - 1) * 3 + 1
            return date(int(y), m, 1), date(int(y), m + 2, calendar.monthrange(int(y), m + 2)[1])
        y = int(label)
        return date(y, 1, 1), date(y, 12, 31)
    except (ValueError, AttributeError) as exc:
        raise ExplorerError(f"{label!r} is not a {bucket} label") from exc


def drill_filters(cells: list[DrillCell]) -> dict[str, Any]:
    """An aggregate cell's dimension values as a filter bag: an equality per plain dimension
    (``empty`` for a null group), a day range per bucketed date."""
    bag: dict[str, Any] = {}
    for c in cells:
        if c.bucket:
            if c.value is None:
                bag[c.field] = None
                bag[f"{c.field}__op"] = "empty"
                continue
            lo, hi = bucket_range(str(c.value), c.bucket)
            bag[f"{c.field}__from"] = lo.isoformat()
            bag[f"{c.field}__to"] = hi.isoformat()
        elif c.value is None:
            bag[c.field] = None
            bag[f"{c.field}__op"] = "empty"
        else:
            bag[c.field] = c.value
            bag[f"{c.field}__op"] = "equals"
    return bag


# ── the builder ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class _Join:
    entity: str
    parent: str
    pairs: list[tuple[str, str]]     # (entity column, parent column)


@dataclass(slots=True)
class _Filter:
    fld: ExplorerField
    op: str
    value: Any


@dataclass(slots=True)
class _Measure:
    spec: MeasureSpec
    fld: ExplorerField | None
    alias: str


@dataclass(slots=True)
class _Dim:
    spec: DimSpec
    fld: ExplorerField
    alias: str


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


def _ident(name: str) -> str:
    if not _IDENT.match(name or ""):
        raise ExplorerError(f"invalid column name {name!r}")
    return name


def _alias(entity: str) -> str:
    return "e_" + entity.replace("-", "_")


def _sub_alias(entity: str) -> str:
    return "x_" + entity.replace("-", "_")


def _num(v: Any) -> float | int | None:
    if v is None:
        return None
    return v if isinstance(v, (int, float)) else float(v)


def _display(v: Any, fld: ExplorerField) -> Any:
    """A detail / sample value for the wire: JDE Julian → ISO, dates → ISO text."""
    if v is None:
        return None
    if fld.type in ("date", "datetime"):
        return bucket_label(v, None, fld)
    return v


class _Builder:
    """Everything one request needs from the model: the resolved fields (model + calculated),
    the plan (row sources, joins, sub-query entities), the entity SQL with the scope applied,
    the predicates. :meth:`aggregate`, :meth:`detail` and :meth:`calc_test` finish the job."""

    def __init__(
        self, connectors: ConnectorRegistry, screens: ScreensFile | None, view: ModelView, *,
        entity: str | None, calculated: list[CalcSpec], scope: DataScope | None,
    ) -> None:
        self.connectors = connectors
        self.screens = screens
        self.view = view
        self.model = view.model
        self.app = view.app
        self.scope = scope
        self.primary = entity or self.model.hub
        if self.primary not in self.model.entities:
            raise ExplorerError(f"unknown entity {self.primary!r}")
        self.joined: list[str] = [self.primary]
        self.joins: list[_Join] = []
        self.sub: dict[str, tuple[str, list[tuple[str, str]]]] = {}   # many-side entity → (parent, pairs)
        self.binds: dict[str, Any] = {}
        self._sqls: dict[str, str] = {}
        self._n = 0
        # One pool for everything: resolved lazily per entity, checked against the primary's.
        self.conn: SQLConnector = connectors.sql(self.model.entities[self.primary].connector or self.app)
        self.dialect = self.conn._resolve_dialect()  # noqa: SLF001 — same family
        self.trim = self.conn._pools.trim_strings(self.conn.pool_name)  # noqa: SLF001
        # Calculated fields: compiled now (their references are plan-independent aliases), the
        # entities they touch are reached when the plan is laid out.
        self.calc: dict[str, tuple[ExplorerField, Node]] = {}
        self.calc_entities: set[str] = set()
        compiler = Compiler(self.dialect, self._resolve_for_calc)
        for c in calculated:
            try:
                check_name(c.name)
            except CalcError as exc:
                raise ExplorerError(str(exc)) from exc
            if c.name.upper() in {k.upper() for k in self.calc}:
                raise ExplorerError(f"calculated field {c.name!r} declared twice")
            try:
                node = compiler.compile(c.expr)
            except CalcError as exc:
                raise ExplorerError(f"calculated field {c.name}: {exc}") from exc
            t = result_type(node)
            fld = ExplorerField(
                key=f"calc.{c.name}", entity="calc", name=c.name, label=c.label or c.name, type=t,
                kind="measure" if t == "number" else "dimension", aggregate="sum" if t == "number" else None,
            )
            self.calc[c.name.upper()] = (fld, node)
            self.calc_entities |= node.fields

    # ── fields ──────────────────────────────────────────────────────────────────
    def field(self, key: str) -> ExplorerField:
        ent, _, name = key.partition(".")
        if ent == "calc":
            hit = self.calc.get(name.upper())
            if hit is None:
                raise ExplorerError(f"unknown calculated field {name!r}")
            return hit[0]
        f = self.view.field(key)
        if f is None:
            raise ExplorerError(f"unknown field {key!r}")
        return f

    def _resolve_for_calc(self, name: str) -> tuple[ExplorerField, str]:
        key = name if "." in name else f"{self.primary}.{name}"
        ent, _, col = key.partition(".")
        if ent == "calc":
            raise KeyError(name)      # a calculated field cannot use another one
        f = self.view.field(key)
        if f is None:
            raise KeyError(name)
        return f, date_ref(f, f"{_alias(f.entity)}.{_ident(f.name)}")

    def ref(self, fld: ExplorerField, *, owner: str | None = None) -> str:
        """The SQL expression of a field: ``alias.COL``, or the calculated expression."""
        if fld.entity == "calc":
            return render(self.calc[fld.name.upper()][1])
        return f"{owner or _alias(fld.entity)}.{_ident(fld.name)}"

    # ── plan ────────────────────────────────────────────────────────────────────
    def lay_out(self, row_entities: list[str]) -> None:
        """Row sources (≤ one many-side besides the hub), joined in order; the calculated
        fields' entities joined too (a many-side one must be a row source)."""
        many = sorted({e for e in row_entities if e != self.model.hub and self.model.entities[e].cardinality == "many"})
        if len(many) > 1:
            raise ExplorerError(f"rows from two many-side entities ({', '.join(many)}) would multiply: keep one")
        for e in row_entities:
            self._join(e)
        for e in self.calc_entities:
            if e not in self.joined:
                if self.model.entities[e].cardinality == "many" and e != self.model.hub:
                    raise ExplorerError(f"a calculated field uses {e!r}, a many-side entity that is not among the rows")
                self._join(e)

    def _join(self, entity: str) -> None:
        for parent, child, rel in _path(self.model, set(self.joined), entity):
            self.joins.append(_Join(entity=child, parent=parent, pairs=_hop_pairs(rel, child)))
            self.joined.append(child)

    def reach(self, entity: str) -> bool:
        """Make *entity* available: joined inline (``True``) — a one-side entity is LEFT JOINed —
        or through a sub-query correlated on a direct relation (``False``)."""
        if entity == "calc" or entity in self.joined:
            return True
        if self.model.entities[entity].cardinality == "one" or entity == self.model.hub:
            self._join(entity)
            return True
        if entity in self.sub:
            return False
        hops = _path(self.model, set(self.joined), entity)
        if len(hops) != 1:
            raise ExplorerError(f"{entity!r} needs a direct relation to the analysed entities ({', '.join(self.joined)})")
        parent, _child, rel = hops[0]
        self.sub[entity] = (parent, _hop_pairs(rel, entity))
        return False

    def connector_of(self, entity: str) -> SQLConnector:
        c = self.connectors.sql(self.model.entities[entity].connector or self.app)
        if c.pool_name != self.conn.pool_name:
            raise ExplorerError(f"{entity!r} runs on pool {c.pool_name!r}, the analysis on {self.conn.pool_name!r}: they cannot be combined")
        return c

    async def entity_sql(self, e: str) -> str:
        if e in self._sqls:
            return self._sqls[e]
        ent = self.model.entities[e]
        screen = (self.screens.screens.get(self.app) or {}).get(ent.screen) if (self.screens is not None and ent.screen) else None
        conn = self.connector_of(e)
        query = ent.query or ""
        if ent.table_sql is not None:
            query = f"_explorer_{e}"
            conn = conn.with_query(query, ent.table_sql)
        sql, b = await conn.scoped_select(
            query, dict(ent.params), column_hints=list(screen.columns) if screen is not None and screen.columns else None,
            dictionary_scope=self.app, scope=self.scope,
        )
        for k, v in b.items():
            nk = f"q_{e.replace('-', '_')}_{k}"
            sql = re.sub(rf":{re.escape(k)}\b", f":{nk}", sql)
            self.binds[nk] = v
        self._sqls[e] = sql
        return sql

    def correlation(self, entity: str) -> str:
        parent, pairs = self.sub[entity]
        x = _sub_alias(entity)
        return " AND ".join(f"{x}.{_ident(ec)} = {_alias(parent)}.{_ident(pc)}" for ec, pc in pairs)

    async def from_clause(self) -> str:
        parts = [f"FROM (\n{await self.entity_sql(self.primary)}\n) {_alias(self.primary)}"]
        for j in self.joins:
            on = " AND ".join(f"{_alias(j.entity)}.{_ident(ec)} = {_alias(j.parent)}.{_ident(pc)}" for ec, pc in j.pairs)
            parts.append(f"LEFT JOIN (\n{await self.entity_sql(j.entity)}\n) {_alias(j.entity)} ON {on}")
        return "\n".join(parts)

    # ── filters ─────────────────────────────────────────────────────────────────
    def parse_filters(self, bag: dict[str, Any]) -> list[_Filter]:
        """The flat bag → typed filters. ``key`` is the value, ``key__op`` the operator (default:
        ``contains`` on text, ``equals`` otherwise, ``in`` for a list), ``key__from`` / ``key__to``
        a range on a date or number."""
        out: list[_Filter] = []
        ops = {k[:-4]: str(v) for k, v in bag.items() if k.endswith("__op")}
        for k, v in bag.items():
            if k.endswith("__op"):
                continue
            base, suffix = (k[:-6], "from") if k.endswith("__from") else (k[:-4], "to") if k.endswith("__to") else (k, None)
            fld = self.field(base)
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

    def _typed(self, fld: ExplorerField, v: Any) -> Any:
        """A filter value as the Python type the driver expects for the column."""
        if fld.type == "number":
            return _coerce_value(v, "number") if isinstance(v, str) else v
        if fld.type == "date":
            if (fld.format or "").lower() == "jdedate":
                return _to_jde_julian(v)
            return date.fromisoformat(str(v)[:10]) if isinstance(v, str) else v
        if fld.type == "datetime":
            return datetime.fromisoformat(str(v)) if isinstance(v, str) else v
        return v.strip() if (self.trim and isinstance(v, str)) else v

    def predicate(self, fld: ExplorerField, op: str, value: Any, *, owner: str | None = None) -> str:
        col = self.ref(fld, owner=owner)
        values = [] if op in ("empty", "notEmpty") else (value if isinstance(value, list) else [value])
        names: list[str] = []
        for v in values:
            name = f"f{self._n}"
            self._n += 1
            self.binds[name] = self._typed(fld, v)
            names.append(name)
        is_text = fld.type == "string"
        ref = f"TRIM({col})" if (is_text and self.trim) else col
        if op == "empty":
            return f"({col} IS NULL OR TRIM({col}) = '')" if is_text else f"{col} IS NULL"
        if op == "notEmpty":
            return f"({col} IS NOT NULL AND TRIM({col}) <> '')" if is_text else f"{col} IS NOT NULL"
        if op == "in":
            return f"{ref} IN ({', '.join(f':{b}' for b in names)})"
        b = names[0]
        pv = f"CAST(:{b} AS {_filter_sql_type(None, self.dialect)})" if is_text else f":{b}"
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

    async def where_clause(self, filters: list[_Filter], period: PeriodSpec | None) -> str:
        """Inline predicates on joined entities; ``EXISTS`` on a sub-query for a many-side
        entity that is not among the rows."""
        where: list[str] = []
        exists: dict[str, list[str]] = {}
        absent: list[str] = []

        def place(fld: ExplorerField, op: str, value: Any) -> None:
            if self.reach(fld.entity):
                where.append(self.predicate(fld, op, value))
            elif op == "empty":
                # "no value" on a many-side entity that is not among the rows = no related row at all
                absent.append(fld.entity)
            else:
                exists.setdefault(fld.entity, []).append(self.predicate(fld, op, value, owner=_sub_alias(fld.entity)))

        for f in filters:
            place(f.fld, f.op, f.value)
        if period is not None:
            fld = self.field(period.field)
            if fld.type not in ("date", "datetime", "string"):
                raise ExplorerError(f"{fld.key} is not a date: no period")
            if period.from_ is not None:
                place(fld, "ge", period.from_.isoformat())
            if period.to is not None:
                if fld.type == "datetime":
                    place(fld, "lt", (period.to + timedelta(days=1)).isoformat())
                else:
                    place(fld, "le", period.to.isoformat())
        for e, preds in exists.items():
            where.append(f"EXISTS (SELECT 1 FROM (\n{await self.entity_sql(e)}\n) {_sub_alias(e)} WHERE {self.correlation(e)} AND {' AND '.join(preds)})")
        for e in dict.fromkeys(absent):
            where.append(f"NOT EXISTS (SELECT 1 FROM (\n{await self.entity_sql(e)}\n) {_sub_alias(e)} WHERE {self.correlation(e)})")
        return ("WHERE " + "\n  AND ".join(where)) if where else ""

    # ── aggregate ───────────────────────────────────────────────────────────────
    async def aggregate(self, req: AggregateRequest, *, debug: bool) -> dict[str, Any]:
        dims = [_Dim(spec=d, fld=self.field(d.field), alias=f"d{i}") for i, d in enumerate(req.rows)]
        for d in dims:
            if d.spec.bucket and d.fld.type not in ("date", "datetime"):
                raise ExplorerError(f"{d.fld.key} is not a date: no bucket")
        measures: list[_Measure] = []
        for i, m in enumerate(req.measures):
            fld = self.field(m.field) if m.field else None
            if fld is not None and m.agg not in ("count", "count_distinct") and fld.type != "number":
                raise ExplorerError(f"{fld.key} is not numeric: {m.agg} needs a number (use count)")
            measures.append(_Measure(spec=m, fld=fld, alias=f"m{i}"))
        if not measures:
            measures.append(_Measure(spec=MeasureSpec(), fld=None, alias="m0"))
        bucketed = any(d.spec.bucket not in (None, "day") for d in dims)
        if bucketed and any(m.spec.agg == "count_distinct" for m in measures):
            raise ExplorerError("count_distinct cannot be merged across week / month / quarter / year buckets")
        filters = self.parse_filters(req.filters)
        self.lay_out([self.primary] + [d.fld.entity for d in dims if d.fld.entity != "calc"])

        def agg_expr(agg: str, ref: str) -> str:
            return {"sum": f"SUM({ref})", "avg": f"SUM({ref})", "min": f"MIN({ref})", "max": f"MAX({ref})",
                    "count": f"COUNT({ref})", "count_distinct": f"COUNT(DISTINCT {ref})"}[agg]

        select: list[str] = []
        group: list[str] = []
        for d in dims:
            self.reach(d.fld.entity)
            ref = self.ref(d.fld)
            select.append(f"{ref} AS {d.alias}")
            group.append(ref)
        for m in measures:
            if m.fld is None:
                select.append(f"COUNT(*) AS {m.alias}")
                continue
            if self.reach(m.fld.entity):
                ref = self.ref(m.fld)
                if m.spec.agg == "avg":
                    select.append(f"SUM({ref}) AS {m.alias}_s")
                    select.append(f"COUNT({ref}) AS {m.alias}_c")
                else:
                    select.append(f"{agg_expr(m.spec.agg, ref)} AS {m.alias}")
                continue
            if m.spec.agg == "count_distinct":
                raise ExplorerError(f"count_distinct on {m.fld.key} needs its entity among the rows")
            e = m.fld.entity
            x = _sub_alias(e)
            body = f"FROM (\n{await self.entity_sql(e)}\n) {x} WHERE {self.correlation(e)}"
            inner = f"(SELECT {agg_expr(m.spec.agg, self.ref(m.fld, owner=x))} {body})"
            outer = "MIN" if m.spec.agg == "min" else "MAX" if m.spec.agg == "max" else "SUM"
            if m.spec.agg == "avg":
                select.append(f"SUM({inner}) AS {m.alias}_s")
                select.append(f"SUM((SELECT COUNT({self.ref(m.fld, owner=x)}) {body})) AS {m.alias}_c")
            else:
                select.append(f"{outer}({inner}) AS {m.alias}")
        where = await self.where_clause(filters, req.period)
        parts = [f"SELECT {', '.join(select)}", await self.from_clause()]
        if where:
            parts.append(where)
        if group:
            parts.append("GROUP BY " + ", ".join(group))
        sql = "\n".join(parts)
        _cols, raw, scan_truncated = await self.conn.run_select(sql, self.binds, max_rows=SCAN_CAP)

        groups: dict[tuple[Any, ...], dict[str, Any]] = {}
        for r in raw:
            low = {str(k).lower(): v for k, v in r.items()}
            key = tuple(bucket_label(low.get(d.alias), d.spec.bucket, d.fld) if d.fld.type in ("date", "datetime") else low.get(d.alias) for d in dims)
            acc = groups.get(key)
            if acc is None:
                acc = {d.spec.key: key[i] for i, d in enumerate(dims)}
                groups[key] = acc
            _merge(acc, low, measures)
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

        sort = [(s.key, s.dir) for s in req.sort] or [(d.spec.key, "asc") for d in dims] or [(measures[0].spec.key, "desc")]
        known = {d.spec.key for d in dims} | {m.spec.key for m in measures}
        for k, _d in sort:
            if k not in known:
                raise ExplorerError(f"cannot sort on {k!r}: not in the result")
        _sort_rows(out_rows, sort)
        total_groups = len(out_rows)
        out_rows = out_rows[: req.limit]

        columns: list[dict[str, Any]] = []
        for d in dims:
            c = {"key": d.spec.key, "kind": "dimension", "field": d.fld.key, "entity": d.fld.entity, "label": d.fld.label, "type": d.fld.type}
            if d.spec.bucket:
                c["bucket"] = d.spec.bucket
            else:
                if d.fld.rule:
                    c["rule"] = d.fld.rule
                if d.fld.format:
                    c["format"] = d.fld.format
            columns.append(c)
        for m in measures:
            c = {"key": m.spec.key, "kind": "measure", "aggregate": m.spec.agg, "type": "number",
                 "field": m.fld.key if m.fld else None, "entity": m.fld.entity if m.fld else self.primary, "label": m.fld.label if m.fld else ""}
            if m.fld and m.fld.format and m.spec.agg in ("sum", "avg", "min", "max"):
                c["format"] = m.fld.format
            columns.append(c)
        result: dict[str, Any] = {
            "mode": "aggregate", "entity": self.primary, "columns": columns, "rows": out_rows,
            "groups": total_groups, "scanned": len(raw), "truncated": total_groups > req.limit, "scan_truncated": scan_truncated,
        }
        if debug:
            result["sql"] = sql
            result["binds"] = _jsonable_binds(self.binds)
        return result

    # ── detail ──────────────────────────────────────────────────────────────────
    async def detail(self, req: DetailRequest, *, debug: bool) -> dict[str, Any]:
        keys = list(req.columns) or [f.key for ev in self.view.entities if ev.entity.id == self.primary for f in ev.fields if not f.hidden]
        if not keys:
            raise ExplorerError("no column to show")
        flds = [self.field(k) for k in keys]
        bag = {**drill_filters(req.drill), **req.filters}
        filters = self.parse_filters(bag)
        self.lay_out([self.primary] + [f.entity for f in flds if f.entity != "calc"])
        select = []
        for i, f in enumerate(flds):
            self.reach(f.entity)
            select.append(f"{self.ref(f)} AS c{i}")
        where = await self.where_clause(filters, req.period)
        body = "\n".join(p for p in (await self.from_clause(), where) if p)
        # Sort: the requested keys (among the columns), else the primary's key columns when
        # shown, else the first column.
        by_key = {f.key: i for i, f in enumerate(flds)}
        sort = [(s.key, s.dir) for s in req.sort]
        if not sort:
            pk = [f"{self.primary}.{k.upper()}" for k in self.model.entities[self.primary].key]
            sort = [(k, "asc") for k in pk if k in by_key] or [(flds[0].key, "asc")]
        order = []
        for k, d in sort:
            if k not in by_key:
                raise ExplorerError(f"cannot sort on {k!r}: not among the columns")
            order.append(f"c{by_key[k]} {'DESC' if d == 'desc' else 'ASC'}")
        offset = (req.page - 1) * req.page_size
        paging = (f"LIMIT {req.page_size} OFFSET {offset}" if self.dialect == "sqlite"
                  else f"OFFSET {offset} ROWS FETCH NEXT {req.page_size} ROWS ONLY")
        sql = f"SELECT {', '.join(select)}\n{body}\nORDER BY {', '.join(order)}\n{paging}"
        count_sql = f"SELECT COUNT(*) AS n\n{body}"
        _c, counted, _t = await self.conn.run_select(count_sql, self.binds, max_rows=1)
        total = int(next(iter(counted[0].values()))) if counted else 0
        _cols, raw, _trunc = await self.conn.run_select(sql, self.binds, max_rows=req.page_size)
        rows = []
        for r in raw:
            low = {str(k).lower(): v for k, v in r.items()}
            rows.append({f.key: _display(low.get(f"c{i}"), f) for i, f in enumerate(flds)})
        columns = []
        for f in flds:
            c = {"key": f.key, "kind": "column", "field": f.key, "entity": f.entity, "label": f.label, "type": f.type}
            if f.rule:
                c["rule"] = f.rule
            if f.format:
                c["format"] = f.format
            columns.append(c)
        result: dict[str, Any] = {"mode": "detail", "entity": self.primary, "columns": columns, "rows": rows, "total": total, "page": req.page, "page_size": req.page_size}
        if debug:
            result["sql"] = sql
            result["binds"] = _jsonable_binds(self.binds)
        return result

    # ── calculated field test ───────────────────────────────────────────────────
    async def calc_test(self, name: str, *, debug: bool) -> dict[str, Any]:
        fld, node = self.calc.get(name.upper()) or (None, None)
        if fld is None or node is None:
            raise ExplorerError(f"unknown calculated field {name!r}")
        self.lay_out([self.primary])
        sql = f"SELECT {render(node)} AS c0\n{await self.from_clause()}"
        sql += "\nLIMIT 8" if self.dialect == "sqlite" else "\nFETCH FIRST 8 ROWS ONLY"
        _cols, raw, _t = await self.conn.run_select(sql, self.binds, max_rows=8)
        values = [_display(next(iter(r.values())), fld) for r in raw]
        out: dict[str, Any] = {"name": fld.name, "type": fld.type, "kind": fld.kind, "values": values}
        if debug:
            out["sql"] = sql
        return out


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


def _sort_rows(rows: list[dict[str, Any]], sort: list[tuple[str, str]]) -> None:
    """Stable multi-key sort (least significant key first), nulls last whatever the direction."""
    for k, direction in reversed(sort):
        nulls = [r for r in rows if r[k] is None]
        rest = [r for r in rows if r[k] is not None]
        rest.sort(key=lambda r: r[k], reverse=(direction == "desc"))
        rows[:] = rest + nulls


def _jsonable_binds(binds: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in binds.items()}


# ── entry points ───────────────────────────────────────────────────────────────


async def run_aggregate(
    connectors: ConnectorRegistry, screens: ScreensFile | None, view: ModelView, req: AggregateRequest, *,
    scope: DataScope | None = None, debug: bool = False,
) -> dict[str, Any]:
    b = _Builder(connectors, screens, view, entity=req.entity, calculated=req.calculated, scope=scope)
    return await b.aggregate(req, debug=debug)


async def run_detail(
    connectors: ConnectorRegistry, screens: ScreensFile | None, view: ModelView, req: DetailRequest, *,
    scope: DataScope | None = None, debug: bool = False,
) -> dict[str, Any]:
    b = _Builder(connectors, screens, view, entity=req.entity, calculated=req.calculated, scope=scope)
    return await b.detail(req, debug=debug)


async def run_calc_test(
    connectors: ConnectorRegistry, screens: ScreensFile | None, view: ModelView, req: CalcTestRequest, *,
    scope: DataScope | None = None, debug: bool = False,
) -> dict[str, Any]:
    b = _Builder(connectors, screens, view, entity=req.entity, calculated=req.calculated, scope=scope)
    return await b.calc_test(req.name, debug=debug)


__all__ = [
    "AggregateRequest", "CalcSpec", "CalcTestRequest", "DetailRequest", "DimSpec", "DrillCell", "ExplorerError", "MeasureSpec",
    "PeriodSpec", "SortSpec", "bucket_label", "bucket_range", "drill_filters", "run_aggregate", "run_calc_test", "run_detail",
]
