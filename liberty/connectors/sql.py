"""SQLConnector — runs named SQL queries with runtime schema discovery.

Ported from nomaubl's ``SqlConnectorClient`` (the JDBC version), adapted to
SQLAlchemy 2.0 async. The defining trait of v2: no ``ly_*`` table holds the
column layout. ``cursor.description`` *is* the schema — add a column to a query
and it appears in the response with zero config edits.

Safety model (also from nomaubl):

* the leading keyword must be in :data:`~liberty.connectors.base.ALLOWED_STATEMENTS`
  (``SELECT/INSERT/UPDATE/DELETE/MERGE``) — ``DROP``/``ALTER``/``TRUNCATE``/… are
  rejected before a connection is opened;
* mutating statements additionally require ``writable = true`` on that specific
  query, so a typo in a notification rule cannot fire ``DELETE FROM …``;
* parameters are bound via SQLAlchemy ``text()`` — never string-substituted into
  the SQL — and any ``:name`` token the caller didn't supply is bound to SQL
  ``NULL`` so ``WHERE (:status IS NULL OR status = :status)`` style optional
  filters work out of the box.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from liberty.connectors.base import (
    ALLOWED_STATEMENTS,
    WRITE_STATEMENTS,
    QueryNotFoundError,
    StatementNotAllowedError,
    WriteNotAllowedError,
    detect_statement_type,
    find_bind_params,
)
from liberty.connectors.config import ColumnHint, QueryDef, SqlConnectorConfig
from liberty.connectors.db import PoolRegistry
from liberty.connectors.dictionary import DictionaryFile

# A best-effort PostgreSQL OID → type-name map. ``cursor.description`` exposes the
# type *code*; for asyncpg that's the pg_type OID. We surface a friendly label
# when we recognise it and ``None`` otherwise — the column *name* (always
# available via ``result.keys()``) is what callers actually depend on.
_PG_OID_NAMES: dict[int, str] = {
    16: "boolean",
    20: "bigint",
    21: "smallint",
    23: "integer",
    25: "text",
    114: "json",
    700: "real",
    701: "double precision",
    1042: "char",
    1043: "varchar",
    1082: "date",
    1083: "time",
    1114: "timestamp",
    1184: "timestamptz",
    1700: "numeric",
    2950: "uuid",
    3802: "jsonb",
}


@dataclass(slots=True)
class Column:
    """One result column: its name and a best-effort type label (may be ``None``),
    plus optional display hints carried over from the query's ``columns`` config
    (label/hidden/width/align/format — see :class:`~liberty.connectors.config.ColumnHint`)."""

    name: str
    type: str | None = None
    label: str | None = None
    hidden: bool = False
    width: int | None = None
    align: str | None = None
    format: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.label is not None:
            d["label"] = self.label
        if self.hidden:
            d["hidden"] = True
        if self.width is not None:
            d["width"] = self.width
        if self.align is not None:
            d["align"] = self.align
        if self.format is not None:
            d["format"] = self.format
        return d


@dataclass(slots=True)
class QueryResult:
    """Outcome of :meth:`SQLConnector.execute`.

    For ``SELECT``: :attr:`columns` and :attr:`rows` are populated, :attr:`rowcount`
    is ``-1``. For ``INSERT/UPDATE/DELETE/MERGE``: :attr:`rowcount` carries the
    affected-row count and :attr:`rows` is empty.
    """

    connector: str
    query: str
    statement_type: str
    columns: list[Column] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    rowcount: int = -1
    duration_ms: float = 0.0
    truncated: bool = False

    @property
    def row_count(self) -> int:
        """Number of rows returned (``len(rows)`` for SELECT, else :attr:`rowcount`)."""
        return len(self.rows) if self.statement_type == "SELECT" else self.rowcount

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector": self.connector,
            "query": self.query,
            "statement_type": self.statement_type,
            "columns": [c.to_dict() for c in self.columns],
            "rows": self.rows,
            "row_count": self.row_count,
            "rowcount": self.rowcount,
            "truncated": self.truncated,
            "duration_ms": round(self.duration_ms, 3),
        }


class SQLConnector:
    """A connector exposing a fixed set of named queries against one pool."""

    def __init__(
        self,
        name: str,
        config: SqlConnectorConfig,
        pools: PoolRegistry,
        *,
        dictionary: DictionaryFile | None = None,
    ) -> None:
        self.name = name
        self.config = config
        self.pool_name = config.pool
        self.max_rows = config.max_rows
        self._pools = pools
        self._dict = dictionary or DictionaryFile()
        self._queries: dict[str, QueryDef] = {q.name: q for q in config.queries}
        self._dialect: str | None = None  # lazily resolved from the pool

    def _resolve_dialect(self) -> str:
        if self._dialect is None:
            self._dialect = self._pools.dialect(self.pool_name)  # may raise UnknownPoolError
        return self._dialect

    # -- introspection ----------------------------------------------------- #

    @property
    def query_names(self) -> list[str]:
        return list(self._queries)

    def get_query(self, name: str) -> QueryDef:
        try:
            return self._queries[name]
        except KeyError:
            raise QueryNotFoundError(
                f"Connector {self.name!r} has no query {name!r}. "
                f"Available: {self.query_names or '(none)'}."
            ) from None

    def describe(self) -> dict[str, Any]:
        """Metadata only — no credentials, no pool URL. Feeds the CLI / settings UI / AI tool.
        Statement-type / bind-param introspection uses the dialect-independent (``default``)
        SQL variant; ``dialects`` lists which per-dialect variants the query carries; each
        query's ``columns`` carry the *resolved* hints (label/format from the dictionary, in
        its default language, with any inline overrides applied)."""
        lang = self._dict.default_language
        return {
            "name": self.name,
            "type": "sql",
            "pool": self.pool_name,
            "queries": [
                {
                    "name": q.name,
                    "label": q.label,
                    "description": q.description,
                    "writable": q.writable,
                    "statement_type": detect_statement_type(q.default_sql),
                    "dialects": q.dialects,
                    "params": [
                        {"name": p.name, "label": p.label, "default": p.default}
                        for p in q.params
                    ],
                    "columns": [_hint_to_dict(h, self._dict, lang) for h in q.columns],
                    "bind_params": find_bind_params(q.default_sql),
                    "sql": q.sql,
                }
                for q in self._queries.values()
            ],
        }

    # -- execution --------------------------------------------------------- #

    def _build_params(self, sql_text: str, qdef: QueryDef, params: dict[str, Any] | None) -> dict[str, Any]:
        merged: dict[str, Any] = {p.name: p.default for p in qdef.params if p.default is not None}
        if params:
            merged.update(params)
        # Bind every :name token the caller didn't supply to SQL NULL.
        bound = {name: merged.get(name) for name in find_bind_params(sql_text)}
        # Keep explicitly declared params too (a default for a token referenced
        # only inside a string literal would otherwise be dropped — harmless,
        # SQLAlchemy ignores binds it doesn't see).
        for p in qdef.params:
            bound.setdefault(p.name, merged.get(p.name))
        return bound

    async def execute(
        self, query_name: str, params: dict[str, Any] | None = None, *, language: str | None = None
    ) -> QueryResult:
        """Run *query_name* with *params*; raises on bad input, returns on success.

        The SQL variant matching the pool's database is selected (``QueryDef.sql_for``).
        Result-column display hints (``QueryDef.columns``) are resolved against the shared
        dictionary in *language* (default: the dictionary's ``default_language``). Raises
        :class:`QueryNotFoundError`, :class:`StatementNotAllowedError`, :class:`WriteNotAllowedError`,
        or :class:`UnknownPoolError`; database errors propagate as the underlying SQLAlchemy exception.
        """
        lang = language or self._dict.default_language
        qdef = self.get_query(query_name)
        sql_text = qdef.sql_for(self._resolve_dialect())
        stmt_type = detect_statement_type(sql_text)
        if stmt_type not in ALLOWED_STATEMENTS:
            raise StatementNotAllowedError(
                f"{self.name}.{query_name}: statement type "
                f"{stmt_type or '(unknown)'!r} is not allowed "
                f"(allowed: {', '.join(sorted(ALLOWED_STATEMENTS))})."
            )
        if stmt_type in WRITE_STATEMENTS and not qdef.writable:
            raise WriteNotAllowedError(
                f"{self.name}.{query_name}: {stmt_type} requires 'writable = true' "
                "on the query definition."
            )

        engine: AsyncEngine = self._pools.engine(self.pool_name)
        bound = self._build_params(sql_text, qdef, params)
        stmt = text(sql_text)
        is_select = stmt_type == "SELECT"

        started = time.perf_counter()
        if is_select:
            async with engine.connect() as conn:
                result = await conn.execute(stmt, bound)
                columns = _apply_column_hints(_columns_from_result(result), qdef.columns, self._dict, lang)
                rows: list[dict[str, Any]] = []
                truncated = False
                for row in result.mappings():
                    if len(rows) >= self.max_rows:
                        truncated = True
                        break
                    rows.append(dict(row))
            duration_ms = (time.perf_counter() - started) * 1000.0
            return QueryResult(
                connector=self.name,
                query=query_name,
                statement_type=stmt_type,
                columns=columns,
                rows=rows,
                rowcount=-1,
                duration_ms=duration_ms,
                truncated=truncated,
            )

        async with engine.begin() as conn:
            result = await conn.execute(stmt, bound)
            rowcount = result.rowcount
        duration_ms = (time.perf_counter() - started) * 1000.0
        return QueryResult(
            connector=self.name,
            query=query_name,
            statement_type=stmt_type,
            rowcount=rowcount,
            duration_ms=duration_ms,
        )


def _resolve_hint(h: ColumnHint, dictionary: DictionaryFile, language: str | None, *, type_: str | None = None) -> Column:
    """A :class:`Column` from a hint: ``label``/``format`` come from the hint if set, else
    from the shared dictionary entry (key = ``h.dd`` or, when unset, ``h.name``; ``dd = ""``
    opts out) in *language*; ``hidden``/``width``/``align`` are always the hint's."""
    label, fmt = h.label, h.format
    if (label is None or fmt is None) and h.dictionary_key:
        dlabel, dfmt = dictionary.resolve(h.dictionary_key, language)
        if label is None:
            label = dlabel
        if fmt is None:
            fmt = dfmt
    return Column(name=h.name, type=type_, label=label, hidden=h.hidden, width=h.width, align=h.align, format=fmt)


def _hint_to_dict(h: ColumnHint, dictionary: DictionaryFile, language: str | None) -> dict[str, Any]:
    """A configured hint as a dict for ``describe()`` — name + the *resolved* label/format
    + any hidden/width/align + the ``dd`` ref (if set). No ``type`` (a hint isn't a result column)."""
    d = _resolve_hint(h, dictionary, language).to_dict()
    d.pop("type", None)
    if h.dd is not None:
        d["dd"] = h.dd
    return d


def _apply_column_hints(
    discovered: list[Column], hints: list[ColumnHint], dictionary: DictionaryFile, language: str | None
) -> list[Column]:
    """Overlay the query's ``columns`` hints on the discovered columns: reorder to the hint
    order, attach label/hidden/width/align/format (label & format pulled from the shared
    dictionary when not given inline — see :func:`_resolve_hint`). Columns with no hint keep
    their discovery order and follow the hinted ones; a hint for a column the query didn't
    return is ignored (a stale hint never fabricates a column)."""
    if not hints:
        return discovered
    remaining = {c.name: c for c in discovered}  # insertion-ordered → preserves discovery order
    ordered: list[Column] = []
    for h in hints:
        col = remaining.pop(h.name, None)
        if col is None:
            continue
        ordered.append(_resolve_hint(h, dictionary, language, type_=col.type))
    ordered.extend(remaining.values())
    return ordered


def _columns_from_result(result: Any) -> list[Column]:
    """Build :class:`Column` list from a SELECT result — names always, types best-effort."""
    names = list(result.keys())
    type_by_name: dict[str, str | None] = {}
    raw_desc = getattr(getattr(result, "cursor", None), "description", None)
    if raw_desc:
        for entry in raw_desc:
            try:
                col_name = entry[0]
                type_code = entry[1]
            except (TypeError, IndexError, KeyError):
                continue
            label: str | None = None
            if isinstance(type_code, int):
                label = _PG_OID_NAMES.get(type_code)
            elif isinstance(type_code, str):
                label = type_code
            elif type_code is not None:
                label = getattr(type_code, "__name__", None)
            type_by_name[col_name] = label
    return [Column(name=n, type=type_by_name.get(n)) for n in names]
