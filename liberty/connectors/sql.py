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

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)

# ── Oracle write-time null coalesce helpers ────────────────────────────────────────────────
# When the pool flag ``coalesce_nulls`` is set (auto-on for Oracle), the SQL connector inspects
# the target table once and replaces ``None`` bind values with type-appropriate sentinels —
# ``''`` for CHAR/NCHAR/VARCHAR2/NVARCHAR2/CLOB, ``0`` for NUMBER/BINARY_FLOAT/INTEGER. That's
# v1's behaviour for JDE (whose NCHAR columns are NOT NULL with implicit space-padding; a NULL
# bind would either fail the constraint or be coerced to NULL — Oracle treats ``''`` and NULL
# as identical for VARCHAR2 anyway, so the coalesce is a no-op there; it's strictly meaningful
# for CHAR/NCHAR and NUMBER NOT NULL columns).

# Match the target table in a write statement. Handles unquoted / "quoted" identifiers and an
# optional schema prefix. The pattern is intentionally narrow — multi-statement queries or
# unusual DML shapes return None and the coalesce step is skipped (the operator can still use
# COALESCE / NVL in the SQL by hand).
_ORACLE_TARGET_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|MERGE\s+INTO|DELETE\s+FROM)\s+"
    r'(?:"?(?P<owner>[A-Za-z_][\w$]*)"?\s*\.\s*)?'
    r'"?(?P<table>[A-Za-z_][\w$]*)"?',
    re.IGNORECASE,
)


def _oracle_target_table(sql: str) -> tuple[str | None, str] | None:
    m = _ORACLE_TARGET_RE.search(sql)
    if not m:
        return None
    owner = m.group("owner")
    table = m.group("table")
    return (owner.upper() if owner else None, table.upper())


# Per-(pool, owner, table) cache of {column_name: simple_type} where simple_type is one of
# "char" / "number" / "other". Populated lazily on first write to a table — refreshed on
# registry rebuild via :func:`reset_oracle_column_cache`. Module-level so all SQLConnectors
# sharing a pool also share the cache.
_ORACLE_COL_TYPES_CACHE: dict[tuple[str, str | None, str], dict[str, str]] = {}


def reset_oracle_column_cache() -> None:
    """Drop the Oracle column-type cache. Called by ``ConnectorRegistry.aclose`` so a reload
    sees fresh column metadata after a hot-reload that may swap pools or schemas."""
    _ORACLE_COL_TYPES_CACHE.clear()


# Per-(pool, audit_table_name) set of audit tables we've verified exist (or just created)
# this process. The check is one SELECT 1 / catch UndefinedTable per fresh deployment per
# audit table; once verified we skip it for every subsequent write. Cleared by
# ``reset_audit_table_cache`` on hot-reload (a swapped pool may target a different DB).
_AUDIT_TABLES_VERIFIED: set[tuple[str, str]] = set()


def reset_audit_table_cache() -> None:
    """Drop the audit-table existence cache. Called by ``ConnectorRegistry.aclose``."""
    _AUDIT_TABLES_VERIFIED.clear()


_ORACLE_CHAR_TYPES = {"CHAR", "NCHAR", "VARCHAR2", "NVARCHAR2", "VARCHAR", "CLOB", "NCLOB", "LONG"}
_ORACLE_NUMBER_TYPES = {"NUMBER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE", "INTEGER", "INT"}

# ── write-time type coercion + form-rule resolution ────────────────────────────────────────
# Frontend submits everything as strings (HTML form inputs); asyncpg / oracledb won't auto-coerce
# a ``"123"`` bind into an INTEGER column. v2's SQL connector consults the column's resolved
# format (dictionary entry's ``format`` + the column hint's override) and coerces the bind into
# the right Python type — same path for the dialog Save, the batch-edit grid, the AI tool.

# Dictionary `format` → coercion family. Free-text values not in this map pass through unchanged
# (the DB will error with its own message — clearer than a guess gone wrong).
_INTEGER_FORMATS = {"integer", "number"}
_DECIMAL_FORMATS = {"decimal", "currency"}
_DATE_FORMATS = {"date"}
_DATETIME_FORMATS = {"datetime", "timestamp"}
_BOOLEAN_FORMATS = {"boolean"}
# JDE Julian dates — stored as integers ``CYYDDD`` in JD Edwards (C = century - 19, YY = year-in-
# century, DDD = day-of-year). 2026-05-18 → 126138. The conversion is symmetric on read (Phase 5
# `DynamicResultMapper` parity) and on write (this module): a Python date/datetime / ISO string
# / already-Julian int all collapse to the integer CYYDDD form before binding.
_JDEDATE_FORMATS = {"jdedate"}


def _to_jde_julian(d: Any) -> int | None:
    """``date | datetime | ISO string | int`` → JDE Julian ``CYYDDD`` integer; ``None`` when
    the input can't be parsed. ``int`` passes through (already in Julian form); a string of
    digits is treated as a pre-converted Julian integer (operator pasted one directly).
    Out-of-range years (< 1900) return ``None`` — JDE dates earlier than the epoch can't
    survive the encoding."""
    from datetime import date as _date
    if isinstance(d, int):
        return d if d >= 0 else None
    if isinstance(d, str):
        s = d.strip()
        if s.isdigit():
            return int(s)
        try:
            d = _date.fromisoformat(s[:10])
        except ValueError:
            return None
    if isinstance(d, datetime):
        d = d.date()
    if not isinstance(d, _date):
        return None
    if d.year < 1900:
        return None
    return (d.year - 1900) * 1000 + d.timetuple().tm_yday


def _coerce_value(value: Any, fmt: str | None) -> Any:
    """Coerce *value* to the Python type implied by *fmt*. Pass-through when *fmt* is unknown
    or the value is already the right type. Empty string → ``None`` so a blank field in the
    form lands as SQL NULL, not as ``''`` (Oracle treats them the same on VARCHAR2 but not
    on NUMBER / DATE; Postgres errors loudly on ``''`` for numeric/date). Coercion failures
    leave the original value — the DB will raise with a clear message naming the column,
    which beats a generic "could not coerce" Python error.

    Non-string inputs are also handled: a ``datetime`` flows through to a ``date`` for a
    ``format = "date"`` column; ``date`` / ``datetime`` / ISO-string / pre-converted int
    all collapse to the JDE Julian integer for ``format = "jdedate"``.
    """
    from datetime import date as _date
    if value is None:
        return None
    if isinstance(value, str) and value == "":
        return None
    f = (fmt or "").strip().lower()
    if not f:
        return value
    try:
        if f in _JDEDATE_FORMATS:
            # Always convert — JDE stores integers, never dates/strings. None on a parse
            # failure surfaces as SQL NULL, matching the "empty string → None" convention.
            return _to_jde_julian(value)
        if f in _INTEGER_FORMATS:
            if isinstance(value, bool):
                return int(value)  # True/False → 1/0
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return value
            v = str(value).strip()
            # Allow "123.0" to parse as int (a number column may receive a float-looking
            # string from the frontend); reject decimal parts that aren't zero.
            if "." in v or "e" in v.lower():
                fv = float(v)
                if fv.is_integer():
                    return int(fv)
                # Non-integer value into an integer column — let the DB reject it loudly.
                return fv
            return int(v)
        if f in _DECIMAL_FORMATS:
            if isinstance(value, (int, float)):
                return value
            return float(str(value).strip())
        if f in _DATETIME_FORMATS:
            if isinstance(value, datetime):
                return value
            if isinstance(value, _date):
                return datetime.combine(value, datetime.min.time())
            # ISO 8601 with optional 'T' separator; reject anything else.
            v = str(value).strip().replace("T", " ")
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                return value
        if f in _DATE_FORMATS:
            if isinstance(value, datetime):
                return value.date()  # drop the time
            if isinstance(value, _date):
                return value
            v = str(value).strip()
            # Accept either "YYYY-MM-DD" or a full datetime (drop the time).
            try:
                return _date.fromisoformat(v[:10])
            except ValueError:
                return value
        if f in _BOOLEAN_FORMATS:
            if isinstance(value, bool):
                return value
            # No rule attached (would have routed through ``rule.true_value`` on the frontend).
            # Fall back to a permissive string-to-bool: "true"/"y"/"1" → True, "false"/"n"/"0" → False.
            v = str(value).strip().lower()
            if v in {"true", "t", "yes", "y", "1"}:
                return True
            if v in {"false", "f", "no", "n", "0"}:
                return False
            return value
    except (ValueError, TypeError, ArithmeticError):
        # Surface the raw value — the DB driver's error is more actionable than ours.
        return value
    return value


# Dictionary-entry rules that trigger a server-side substitution at INSERT / UPDATE time.
# Same set v1's FormsDialog/FormsTable evaluated on Save — re-implemented here so the dialog,
# batch-edit grid, and any future API caller all get it consistently.
_RULES_LOGIN = {"LOGIN"}
_RULES_NOW = {"SYSDATE", "CURRENT_DATE"}
_RULES_SEQUENCE = {"SEQUENCE", "NN"}
_RULES_PASSWORD = {"PASSWORD"}
_RULES_DEFAULT = {"DEFAULT"}  # use the entry's `default` when the bind is missing/empty
# v1 parity: a column-hint-level ``rules = "DISABLED"`` opts out of an inherited dictionary
# rule on this specific screen. Used to keep SEQUENCE from refiring on UPDATE (we already
# scope SEQUENCE to INSERT, so this is mostly belt-and-braces — but the operator may want it
# on PASSWORD / SYSDATE too, e.g. an import screen that bulk-loads pre-computed values).
_RULES_DISABLED = {"DISABLED"}


# ── Runtime filter wrap (v1-style: wrap only what the caller filtered, type-aware) ────────────
# v2 (Phase 8): the migrator no longer bakes filter predicates into the stored SQL. Instead
# :meth:`SQLConnector._apply_filter_wrap` reads ``QueryDef.columns`` to find columns marked
# ``filter = true``, reads the caller's params to find which were *actually* sent, and
# generates one predicate per active column. Text columns get the operator-aware
# LOWER/LIKE wrap; number/date/boolean/jdedate get a typed equals (index-friendly).
# The migrator stored the alternative — every filter column's full operator wrap *always*
# present — but that killed indexes (universal CAST AS VARCHAR(4000)) and made every
# stored SELECT unreadable in a wizard.
_FILTER_OPS_TEXT = ("contains", "equals", "notEquals", "startsWith", "endsWith")
_TEXT_FORMATS = {"text", "textarea", "email", "url", "color", "password"}


def _from_jde_julian(v: Any) -> str | int | None:
    """Inverse of :func:`_to_jde_julian` — JDE Julian ``CYYDDD`` integer → ISO date string
    ``"YYYY-MM-DD"``. The frontend renders the date string; the dialog's edit path parses
    it back to a Python ``date`` and ``_coerce_value(..., "jdedate")`` re-encodes the CYYDDD
    integer for the round-trip. Pass-through on ``None``; integers ≤ 0 / out-of-range
    return the original raw value (so an operator can spot bad data instead of seeing a
    silently-corrupted display).

    Numeric values arriving as strings or floats are normalised first (some DB drivers
    return DECIMAL columns as Decimal, others as int). The CYYDDD encoding is:
    ``year = 1900 + jde_int // 1000`` and ``day_of_year = jde_int % 1000``.
    """
    from datetime import date as _date, timedelta
    if v is None:
        return None
    try:
        if isinstance(v, bool):
            return v  # pass through — booleans aren't JDE dates
        if isinstance(v, (int,)):
            n = v
        elif isinstance(v, float):
            n = int(v)
        elif isinstance(v, str):
            s = v.strip()
            if not s.isdigit():
                return v
            n = int(s)
        else:
            n = int(str(v))
    except (TypeError, ValueError):
        return v
    if n <= 0:
        return v   # 0 is "no date" in JDE; surface the raw value to make it visible
    century_year, day = divmod(n, 1000)
    year = 1900 + century_year
    if day < 1 or day > 366:
        return v
    try:
        return (_date(year, 1, 1) + timedelta(days=day - 1)).isoformat()
    except ValueError:
        return v


def _split_order_by(sql_text: str) -> tuple[str, str]:
    """Split *sql_text* at the **last top-level** ``ORDER BY`` (depth 0 in parens). Returns
    ``(body, order_clause)`` — the clause does *not* include the ``ORDER BY`` keyword.

    Used by :meth:`SQLConnector._apply_filter_wrap` so the dynamic wrap can re-attach the
    operator-supplied ordering after the wrap: the inner SELECT's ORDER BY would otherwise
    be lost (derived tables have unordered semantics in standard SQL).

    Best-effort and idempotent: when no top-level ORDER BY is found, returns
    ``(sql_text, "")`` so the caller can compose ``body + order_clause`` unconditionally.
    Skips ORDER BYs inside subqueries / function calls / window clauses by tracking paren depth.
    """
    s = sql_text.rstrip().rstrip(";").rstrip()
    depth = 0
    last_idx = -1
    sl_upper = s.upper()
    # Walk forward, recording the *last* top-level ORDER BY. We can't naively walk
    # backwards because that would misinterpret "ORDER BY" appearing inside a string
    # literal — but we don't need string-literal tracking for v1-migrated SQL (no
    # operator writes "ORDER BY" inside a string).
    i = 0
    n = len(s)
    while i < n - 7:
        c = s[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and sl_upper[i:i + 8] == "ORDER BY" and (i == 0 or not s[i - 1].isalnum()) and (i + 8 == n or not s[i + 8].isalnum()):
            last_idx = i
        i += 1
    if last_idx < 0:
        return s, ""
    return s[:last_idx].rstrip(), s[last_idx + len("ORDER BY"):].strip()


def _filter_sql_type(fmt: str | None, dialect: str) -> str:
    """SQL type to cast a filter bind into for a non-text column — picked so the comparison
    can use a btree index on the column. Falls back to a wide VARCHAR when the format is
    unknown (matches the historic v1 behaviour but only for one bind, not every column)."""
    f = (fmt or "").strip().lower()
    if f in ("integer", "number", "jdedate"):
        return "NUMBER" if dialect == "oracle" else "INTEGER"
    if f in ("decimal", "currency"):
        return "NUMBER" if dialect == "oracle" else "NUMERIC"
    if f in ("date",):
        return "DATE"
    if f in ("datetime", "timestamp"):
        return "TIMESTAMP"
    if f == "boolean":
        # Oracle has no native BOOLEAN type for SQL expressions outside PL/SQL; use a
        # short VARCHAR cast so the comparison works for the rule's true/false values.
        return "VARCHAR2(20)" if dialect == "oracle" else "BOOLEAN"
    return "VARCHAR2(4000)" if dialect == "oracle" else "VARCHAR(4000)"


def _build_filter_predicate(name: str, fmt: str | None, op: str, dialect: str) -> str:
    """One filter predicate against ``lib_flt.<name>`` for *op* using a bind named ``:<name>``.

    Text columns get the operator-aware ``LOWER(col) LIKE LOWER(...)`` family for
    contains/startsWith/endsWith plus ``=`` / ``<>`` for equals/notEquals. Crucially, we
    **don't CAST the column side** — Postgres can use a functional index on ``LOWER(col)``
    if defined, and the planner has the option to choose between seq-scan and index-scan;
    the previous universal CAST AS VARCHAR(4000) on every column kept it from ever using
    an index.

    Non-text columns (number / date / boolean / jdedate) only use ``equals``. The wrap
    helper coerces the bind to the right *Python* type before passing it down (so a
    string ``"10"`` becomes ``int(10)``) — asyncpg then sends the proper type code and
    the column's btree index applies natively. No SQL CAST on either side: pre-Phase-8
    we had ``col = CAST(:name AS INTEGER)`` which forced asyncpg's strict-type check to
    reject the string ``"10"`` even though Postgres would have accepted it.
    """
    text_format = (fmt or "").lower() in _TEXT_FORMATS or fmt is None or fmt == ""
    col_ref = f"lib_flt.{name}"
    if text_format:
        # The bind side still casts to a wide VARCHAR — asyncpg can't infer the type of a
        # bind otherwise (Postgres needs the cast to know whether to compare as text vs
        # bytea vs jsonb on an untyped TEXT column when the param type isn't pinned). The
        # column side stays as-is so the planner is free to use an index.
        pv = f"CAST(:{name} AS {_filter_sql_type(None, dialect)})"
        if op == "equals":
            return f"{col_ref} = {pv}"
        if op == "notEquals":
            return f"{col_ref} <> {pv}"
        if op == "startsWith":
            return f"LOWER({col_ref}) LIKE LOWER({pv} || '%')"
        if op == "endsWith":
            return f"LOWER({col_ref}) LIKE LOWER('%' || {pv})"
        # default → "contains"
        return f"LOWER({col_ref}) LIKE LOWER('%' || {pv} || '%')"
    # Non-text: equals-only. The bind is *Python-coerced* upstream (see
    # ``SQLConnector._apply_filter_wrap``) so asyncpg gets an actual int / float / date /
    # bool. No SQL CAST — letting the driver send the proper type code is what makes the
    # column's native btree index apply, *and* avoids asyncpg's strict-type rejection of
    # a string value against an INTEGER-typed param.
    return f"{col_ref} = :{name}"



def _coalesce_oracle_nulls(bound: dict[str, Any], col_types: dict[str, str]) -> dict[str, Any]:
    """Replace ``None`` values in *bound* with type-appropriate sentinels based on *col_types*.
    Bind names that don't match any column pass through unchanged (filter operator binds,
    extras the migration added, etc.). The migration's ``:<COL>_ORIGINAL`` rebind for ``_put``
    queries' WHERE strips the suffix to find the source column type."""
    if not col_types:
        return bound
    out = dict(bound)
    for k, v in list(out.items()):
        if v is not None:
            continue
        base = k.upper()
        if base.endswith("_ORIGINAL"):
            base = base[: -len("_ORIGINAL")]
        t = col_types.get(base)
        if t == "char":
            out[k] = ""
        elif t == "number":
            out[k] = 0
    return out

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from liberty.connectors.base import (
    ALLOWED_STATEMENTS,
    WRITE_STATEMENTS,
    ConnectorError,
    QueryNotFoundError,
    StatementNotAllowedError,
    WriteNotAllowedError,
    detect_statement_type,
    find_bind_params,
)
from liberty.connectors.config import ColumnHint, QueryDef, SqlConnectorConfig
from liberty.connectors.db import PoolRegistry
from liberty.connectors.dictionary import DictionaryFile

# Internal sentinel raised by `test_run(dry_run=True)` on a write — forces `engine.begin()`'s
# context manager to roll back; caught outside the `async with`. Not part of the public surface.
class _DryRunRollback(Exception):
    pass


# `#SCHEMA.<NAME>#` (or bare `#SCHEMA#`) in a query's SQL, replaced at execution time with the pool's
# `schemas` mapping (v1's ly_db_schema). Case-insensitive on the literal and the name.
_SCHEMA_PLACEHOLDER = re.compile(r"#SCHEMA(?:\.([A-Za-z0-9_]+))?#", re.IGNORECASE)
# the replacement must be a plain (optionally dotted: catalog.schema) identifier — a config-injection guard
_SCHEMA_NAME = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?")


def _apply_schema_placeholders(sql: str, schemas: dict[str, str], *, connector: str, query: str, pool: str) -> str:
    """Replace ``#SCHEMA.<NAME>#`` (and bare ``#SCHEMA#`` → the ``""`` key) in *sql* with the pool's
    ``schemas`` mapping. A referenced name with no mapping — or a mapping that isn't a plain
    identifier — raises :class:`ConnectorError` (the SQL would otherwise be silently wrong). When the
    query has no placeholders, *sql* is returned unchanged (so this is safe to call unconditionally)."""
    if "#" not in sql:  # cheap fast path — no `#…#` token at all
        return sql
    lookup = {k.upper(): v for k, v in schemas.items()}

    def _sub(m: re.Match[str]) -> str:
        key = (m.group(1) or "").upper()
        if key not in lookup:
            raise ConnectorError(
                f"{connector}.{query}: query references {m.group(0)} but pool {pool!r} has no schema "
                f"mapping for {key or '(default)'!r} — add it to [pools.{pool}] schemas in connectors.toml"
            )
        value = lookup[key].strip()
        if not _SCHEMA_NAME.fullmatch(value):
            raise ConnectorError(
                f"{connector}.{query}: schema mapping {key!r} = {value!r} (pool {pool!r}) is not a plain "
                "identifier — must be like 'SY920' or 'db.schema'"
            )
        return value

    return _SCHEMA_PLACEHOLDER.sub(_sub, sql)


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
    (label/hidden/width/align/format — see :class:`~liberty.connectors.config.ColumnHint`)
    and an optional ``rule`` (resolved from the dictionary entry — BOOLEAN's true-value, an ENUM's
    value→label map, or a LOOKUP reference; see :meth:`DictionaryFile.resolve_rule`).

    ``dd`` is the dictionary key the column was resolved against (the v1 ``col_dd_id``). The wire
    payload exposes it so the frontend can do cross-table mapping — Phase 8 slice 3b's dashboard
    filters use it to find which column to bind on each widget's query (``dd = "APPS_ID"`` on
    ``USR_APPS_ID`` / ``RLU_APPS_ID`` / ``CFD_APPS_ID``, etc., share one filter)."""

    name: str
    type: str | None = None
    label: str | None = None
    hidden: bool = False
    filter: bool = False
    filter_from: list[dict[str, str]] = field(default_factory=list)
    visible_when: list[dict[str, Any]] = field(default_factory=list)
    width: int | None = None
    align: str | None = None
    format: str | None = None
    rule: dict[str, Any] | None = None
    dd: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.label is not None:
            d["label"] = self.label
        if self.hidden:
            d["hidden"] = True
        if self.filter:
            d["filter"] = True
        if self.filter_from:
            d["filter_from"] = self.filter_from
        if self.visible_when:
            d["visible_when"] = self.visible_when
        if self.width is not None:
            d["width"] = self.width
        if self.align is not None:
            d["align"] = self.align
        if self.dd is not None:
            d["dd"] = self.dd
        if self.format is not None:
            d["format"] = self.format
        if self.rule is not None:
            d["rule"] = self.rule
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

    # Hard ceiling on any row cap (a per-request override can't ask for more) — keeps a runaway
    # "give me everything" from OOM-ing the server.
    HARD_MAX_ROWS = 1_000_000
    DEFAULT_MAX_ROWS = 1000

    def __init__(
        self,
        name: str,
        config: SqlConnectorConfig,
        pools: PoolRegistry,
        *,
        dictionary: DictionaryFile | None = None,
        pool_max_rows: int | None = None,
    ) -> None:
        self.name = name
        self.config = config
        self.pool_name = config.pool
        # effective default row cap: this connector's, else the pool's, else DEFAULT_MAX_ROWS
        self.max_rows = config.max_rows if config.max_rows is not None else (
            pool_max_rows if pool_max_rows is not None else self.DEFAULT_MAX_ROWS
        )
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

    def _companion(self, qdef: QueryDef, suffix: str, explicit: str | None) -> str | None:
        """The name of the ``writable`` companion query for *qdef* — the *explicit* value if set,
        else the ``<base>_get`` → ``<base><suffix>`` query (if it exists on this connector and is
        writable, the migration's convention). ``None`` otherwise."""
        if explicit is not None:
            cand = self._queries.get(explicit)
            return explicit if cand is not None and cand.writable else None
        n = qdef.name
        if n[-4:].lower() == "_get":
            cand_name = n[:-4] + suffix
            cand = self._queries.get(cand_name)
            if cand is not None and cand.writable:
                return cand_name
        return None

    def update_query_for(self, qdef: QueryDef) -> str | None:
        """The ``writable`` query that updates one row of *qdef*'s result (``QueryDef.update_query``
        or the ``_get`` → ``_put`` companion)."""
        return self._companion(qdef, "_put", qdef.update_query)

    def insert_query_for(self, qdef: QueryDef) -> str | None:
        """The ``writable`` query that inserts a row into *qdef*'s table (``_post`` companion)."""
        return self._companion(qdef, "_post", qdef.insert_query)

    def delete_query_for(self, qdef: QueryDef) -> str | None:
        """The ``writable`` query that deletes one row of *qdef*'s result (``_delete`` companion)."""
        return self._companion(qdef, "_delete", qdef.delete_query)

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
                    "auto_load": q.auto_load,
                    "key_columns": q.key_columns,
                    "writable": q.writable,
                    "update_query": self.update_query_for(q),
                    "insert_query": self.insert_query_for(q),
                    "delete_query": self.delete_query_for(q),
                    "statement_type": detect_statement_type(q.default_sql),
                    "dialects": q.dialects,
                    "params": [
                        {"name": p.name, "label": p.label, "default": p.default}
                        for p in q.params
                    ],
                    "columns": [_hint_to_dict(h, self._dict, lang, connector=self.name) for h in q.columns],
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

    def _apply_filter_wrap(
        self, sql_text: str, qdef: QueryDef, params: dict[str, Any] | None, *, stmt_type: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """v1-style **runtime** filter wrap. Inspects *params* for values bound to columns
        the query marks ``filter = true`` (in ``QueryDef.columns``), and emits a wrap with
        a predicate per *active* column only — type-aware: text columns get the full
        operator family (contains / equals / notEquals / startsWith / endsWith), non-text
        columns get a typed equals.

        Each column's effective format is resolved the same way ``_apply_column_hints``
        and ``_column_meta_map`` do it: the column hint's own ``format`` wins, else the
        dictionary entry's (``col.dd`` or, when unset, ``col.name``). Without this, a
        column whose only source of format is the dictionary (the common case for
        ``extra_filter_cols`` hints that the migration emits from drill / nested-tab
        binds) would render as text and break on a numeric column —
        ``LOWER(integer)`` is undefined on Postgres.

        Phase 8: replaces the migrator's static wrap that bundled every filter column's
        full operator predicate into the stored SQL whether the caller used it or not.
        Side effect: indexes can finally be used (no universal CAST AS VARCHAR(4000) on
        the column side) and the stored SELECT is small enough to round-trip through a
        wizard.

        Only fires on SELECT — writes don't have filter columns to wrap. Returns
        *sql_text* unchanged when:
          - the query has no filter-flagged columns, OR
          - the caller didn't bind a value for any of them, OR
          - it's not a SELECT.

        ORDER BY at the top level is preserved across the wrap (the inner SELECT's
        ORDER BY would otherwise be lost — derived tables are unordered in standard SQL).

        **Returns** ``(wrapped_sql, params)``. The returned params are a *new* dict (the
        caller's input is never mutated) carrying the original keys plus any active-filter
        bind values coerced to the right Python type for the column's format — asyncpg's
        strict-type check rejects a string against an INTEGER-typed bind, so the wrap
        helper is the place to convert ``"10"`` → ``int(10)`` before binding. Non-active
        params and any other binds pass through untouched."""
        if stmt_type != "SELECT" or not qdef.columns:
            return sql_text, params
        active: list[tuple[str, str | None, str, Any]] = []
        for col in qdef.columns:
            if not col.filter:
                continue
            v = (params or {}).get(col.name)
            if v is None or v == "":
                continue
            op = str((params or {}).get(f"{col.name}_op", "") or "").strip() or "contains"
            if op not in _FILTER_OPS_TEXT:
                op = "contains"
            # Effective format — hint's own wins, else the dictionary entry's. ``dd = ""``
            # opts out of dictionary lookup (parallels the SELECT-side hint resolver).
            fmt = col.format
            if fmt is None and col.dd != "":
                key = (col.dd or col.name)
                entry = self._dict.find_entry(key, connector=self.name)
                if entry is not None and entry.format:
                    fmt = entry.format
            # Python-coerce the value for non-text columns. Text-y formats are left as
            # strings (the wrap's LOWER/LIKE want a string; ``_coerce_value(..., "text")``
            # is a no-op anyway). For number / date / boolean / jdedate the value flows
            # to asyncpg as a real Python int / date / bool — the SQL has no CAST so
            # asyncpg gets to send the column's native type code and the index applies.
            coerced = v if (fmt or "").lower() in _TEXT_FORMATS or not fmt else _coerce_value(v, fmt)
            active.append((col.name, fmt, op, coerced))
        if not active:
            return sql_text, params
        dialect = self._resolve_dialect()
        preds = [_build_filter_predicate(name, fmt, op, dialect) for name, fmt, op, _ in active]
        body, order_clause = _split_order_by(sql_text)
        where_clause = " AND ".join(f"({p})" for p in preds)
        out = f"SELECT * FROM (\n{body}\n) lib_flt\nWHERE 1=1 AND {where_clause}"
        if order_clause:
            out += f"\nORDER BY {order_clause}"
        # Merge the coerced active-filter values into a fresh params dict so the
        # downstream ``_build_params`` picks them up (it reads the dict by bind name).
        new_params: dict[str, Any] = dict(params or {})
        for name, _fmt, _op, coerced in active:
            new_params[name] = coerced
        return out, new_params

    def _column_meta_map(self, qdef: QueryDef) -> dict[str, dict[str, str | None]]:
        """Build a column-name → ``{format, rule, rules_values, default}`` map for *qdef*.

        Column hints (``qdef.columns``) — the per-screen layer that can override the
        dictionary entry's ``format`` — are picked up first; for every other column name
        used as a bind in the SQL we fall back to ``find_entry(name)`` so write queries
        (``_post`` / ``_put`` / ``_delete``, which the migration emits *without* a
        ``columns`` block — the layout lives on the matching ``_get``) still get rule
        resolution + coercion. Keys are upper-cased: the migration's binds (``:USR_ID``)
        and the v1 dictionary's dd_ids both use uppercase; the resolved key matches the
        bind name regardless of how the surrounding SQL was cased.

        Each hint's ``dd`` (or its ``name`` when ``dd`` is unset) is the dictionary lookup
        key; ``dd = ""`` opts out (the column carries no dictionary semantics — keep the
        format override only). Dictionary fallback uses the bind name itself, since
        :func:`migrate_sql_queries` binds columns by their result-column name.
        """
        from liberty.connectors.dictionary import infer_false_value
        out: dict[str, dict[str, str | None]] = {}
        seen: set[str] = set()

        def _entry_meta(entry: Any, fmt_override: str | None) -> dict[str, str | None]:
            """Pull (fmt, rule, rules_values, default, false_value) off a DictionaryEntry,
            inferring the BOOLEAN false counterpart when the operator didn't set one."""
            if entry is None:
                return {"fmt": fmt_override, "rule": None, "rules_values": None, "default": None, "false_value": None}
            rule_up = entry.rules.upper() if entry.rules else None
            false_v: str | None = None
            if rule_up == "BOOLEAN":
                false_v = entry.false_value or infer_false_value(entry.rules_values or "Y")
            return {
                "fmt": fmt_override or entry.format,
                "rule": rule_up,
                "rules_values": entry.rules_values,
                "default": entry.default,
                "false_value": false_v,
            }

        for col in qdef.columns:
            key = (col.dd if col.dd else col.name) if col.dd != "" else None
            entry = self._dict.find_entry(key, connector=self.name) if key else None
            out[col.name.upper()] = _entry_meta(entry, col.format)
            seen.add(col.name.upper())
        # Fallback: for write queries (no ``columns`` hint block), look up the dictionary
        # directly by every bind name the SQL references. The migration's _post/_put/_delete
        # use uppercase v1 dd_ids verbatim, so a direct ``find_entry(name)`` resolves cleanly.
        sql_for = qdef.sql_for(self._resolve_dialect())
        for name in find_bind_params(sql_for):
            up = name.upper()
            base = up[: -len("_ORIGINAL")] if up.endswith("_ORIGINAL") else up
            if base in seen:
                continue
            entry = self._dict.find_entry(base, connector=self.name)
            if entry is None:
                continue
            out[base] = _entry_meta(entry, None)
            seen.add(base)
        return out

    def _apply_form_rules(
        self, bound: dict[str, Any], qdef: QueryDef, *, stmt_type: str, user: str | None,
    ) -> dict[str, Any]:
        """Resolve form-layer rules + coerce types on the bound params, **synchronously**.

        Steps, in order:

        1. **LOGIN** → stamp the caller's username (``"anonymous"`` when unauthenticated).
        2. **SYSDATE / CURRENT_DATE** → stamp ``datetime.now(UTC)`` (one value per call so
           every audit column in the same write lands on the same instant).
        3. **PASSWORD** → Argon2-hash a non-empty value; blank/missing pass through as NULL
           (the dialog already strips blank password fields from the submit body for UPDATE
           — keeping the existing hash; INSERT with an empty password lands as NULL).
        4. **BOOLEAN** → bind ← ``false_value`` when null/empty (the dialog usually sends the
           proper value on uncheck, but the migration / batch-edit grid may not yet — this is
           the safety net for the Y/N case where the DB doesn't accept NULL). ``true_value``
           passes through; only the empty/null side is substituted.
        5. **DEFAULT** → on INSERT only, when the bind is missing/empty, use the entry's
           ``default`` value.
        6. **Type coercion** → strings to the matching Python type for ``format`` ∈
           {integer/number/decimal/currency/date/datetime/timestamp/boolean/jdedate}.

        ``SEQUENCE`` / ``NN`` is handled separately by :meth:`_resolve_sequences` because it
        needs a DB connection (and must run inside the same write transaction).

        ``_ORIGINAL``-suffixed binds (the migration's ``_put`` WHERE-clause re-bindings) **skip
        the form-rule step** (the WHERE wants the row's pre-edit values, untouched) but **do
        get type coercion** (Postgres still won't compare ``'10' = INTEGER 10``).
        """
        if stmt_type not in WRITE_STATEMENTS:
            return bound
        meta = self._column_meta_map(qdef)
        if not meta:
            return bound
        out = dict(bound)
        now: datetime | None = None
        from liberty.auth.password import hash_password  # local import — avoids auth-on-startup wiring
        for k, v in list(out.items()):
            ku = k.upper()
            is_original = ku.endswith("_ORIGINAL")
            col_name = ku[: -len("_ORIGINAL")] if is_original else ku
            m = meta.get(col_name)
            if m is None:
                continue
            fmt = m["fmt"]
            rule = m["rule"]
            default = m["default"]

            # Form-rule substitution only applies to SET-clause binds (not WHERE _ORIGINAL).
            # DISABLED is the v1 per-screen opt-out — short-circuits *every* rule below so a
            # specific column hint can defuse an inherited dictionary rule (e.g. a bulk-import
            # screen that wants SYSDATE / SEQUENCE to *not* fire because the rows carry their
            # own pre-computed audit/PK values). Coercion still applies below.
            if not is_original and rule not in _RULES_DISABLED:
                if rule in _RULES_LOGIN:
                    # v1 stored audit usernames as uppercase ("ADMIN" not "admin"). Match
                    # that — the audit tables + downstream reporting all rely on it.
                    out[k] = (user or "anonymous").upper()
                    continue
                if rule in _RULES_NOW:
                    # One ``now()`` per call, so every audit column in the same write lands on
                    # the same instant. Coerce to the matching Python type for the column's
                    # format (date / datetime / jdedate) so the driver picks the right SQL type.
                    if now is None:
                        now = datetime.now(timezone.utc).replace(tzinfo=None)
                    out[k] = _coerce_value(now, fmt) if fmt else now
                    continue
                if rule in _RULES_PASSWORD:
                    if v not in (None, ""):
                        out[k] = hash_password(str(v))
                    else:
                        # Blank password: leave as NULL — the dialog drops blank passwords from
                        # UPDATE's SET (migrated _put binds :PASSWORD only when the user typed one).
                        out[k] = None
                    continue
                # BOOLEAN — substitute the inferred / explicit `false_value` when the dialog
                # sends null on uncheck. The dialog's checkbox knows the rule.true_value (from
                # the read query's Column.rule) and now also rule.false_value, so it usually
                # sends the right value already; this safety net catches paths that don't (a
                # screen whose read result hasn't loaded the rule yet, the batch-edit grid, an
                # external API caller). ``true_value`` itself passes through untouched.
                if rule == "BOOLEAN" and (v is None or v == ""):
                    fv = m.get("false_value")
                    if fv is not None:
                        out[k] = fv
                        continue  # don't also fall into DEFAULT — BOOLEAN's "false" *is* the default
                # DEFAULT — only on INSERT, only when the user didn't supply a value.
                if (
                    stmt_type == "INSERT" and (v is None or v == "")
                    and default is not None and rule not in _RULES_SEQUENCE
                ):
                    out[k] = default
                    v = default

            # Type coercion (every kind of bind, including _ORIGINAL). Runs after the rule
            # substitution so a SYSDATE result also lands as the right shape for the column.
            # BOOLEAN-ruled columns skip coercion: the rule's ``true_value`` / ``false_value``
            # *are* the strings the DB column stores (varchar/char, not Postgres bool), so
            # coercing "Y" → Python ``True`` would break the column type (asyncpg's
            # ``expected str, got bool`` on a string column). The pure ``format = "boolean"``
            # case without a rule still coerces (a real PG bool column needs a Python bool).
            if rule != "BOOLEAN":
                out[k] = _coerce_value(out[k], fmt)
        return out

    async def _resolve_sequences(
        self, conn: Any, bound: dict[str, Any], qdef: QueryDef, *, stmt_type: str, language: str,
    ) -> dict[str, Any]:
        """Run any ``SEQUENCE`` / ``NN`` rule queries in *conn* (the open write transaction)
        and substitute the result into the matching bind. Only fires on INSERT and only when
        the bind is missing/empty — an explicit value from the caller wins.

        ``rules_values`` is the **sequence id** (a key into ``DictionaryFile.sequences`` —
        v1's ``ly_sequence`` ported to a first-class registry entity). The SequenceDef's
        ``query`` names a v2 read query expected to return one row with the next number as
        the first column. For backwards-compat with pre-Phase-8 migrations that put the
        query name directly in ``rules_values``, we fall back to looking up the value as a
        query if no sequence with that id exists.

        Doing this in the *same* connection as the INSERT keeps the read + write atomic — a
        concurrent insert that picks up the same number would have to commit in between, which
        the surrounding ``engine.begin()`` block serialises against.
        """
        if stmt_type != "INSERT":
            return bound
        meta = self._column_meta_map(qdef)
        if not meta:
            return bound
        out = dict(bound)
        for k, v in list(out.items()):
            if k.upper().endswith("_ORIGINAL"):
                continue
            m = meta.get(k.upper())
            if m is None:
                continue
            if m["rule"] not in _RULES_SEQUENCE:
                continue
            if v not in (None, ""):
                continue  # caller supplied an explicit value
            seq_ref = m["rules_values"]
            if not seq_ref:
                _log.warning(
                    "%s.%s: SEQUENCE rule on column %s has no rules_values — bind left NULL",
                    self.name, qdef.name, k,
                )
                continue
            # Sequence id → SequenceDef → query name. Falls through to "treat the value as a
            # query name" for legacy / hand-edited dictionaries that don't yet have the
            # ``[sequences.*]`` block — keeps the prior Phase-8 wiring working.
            seq_ref_str = str(seq_ref).strip()
            seq_def = self._dict.find_sequence(seq_ref_str, connector=self.name)
            seq_query_name = seq_def.query if seq_def is not None else seq_ref_str
            seq_qdef = self._queries.get(seq_query_name)
            if seq_qdef is None:
                _log.warning(
                    "%s.%s: SEQUENCE rule on column %s references unknown %s %r — bind left NULL",
                    self.name, qdef.name, k,
                    "sequence" if seq_def is None else "query",
                    seq_ref_str,
                )
                continue
            seq_sql = _apply_schema_placeholders(
                seq_qdef.sql_for(self._resolve_dialect()), self._pools.schemas(self.pool_name),
                connector=self.name, query=seq_qdef.name, pool=self.pool_name,
            )
            # Bind whatever the sequence query references from the *current* row — ``text()`` only
            # binds names it sees in the SQL, so a sequence that narrows by APPS_ID picks it up
            # automatically without extra param plumbing. Missing names → SQL NULL.
            seq_bound = {name: out.get(name) for name in find_bind_params(seq_sql)}
            try:
                r = await conn.execute(text(seq_sql), seq_bound)
                row = r.first()
                if row is None:
                    _log.warning(
                        "%s.%s: SEQUENCE query %r returned no rows — bind %s left NULL",
                        self.name, qdef.name, seq_query_name, k,
                    )
                    continue
                # Take the first column of the first row (sequence queries are MAX(col)+1 by
                # convention — one row, one column). Coerce defensively in case the query
                # returns a string somehow.
                next_val = row[0]
                if isinstance(next_val, str) and next_val.strip().isdigit():
                    next_val = int(next_val)
                out[k] = next_val
            except Exception as exc:  # noqa: BLE001 — log + fall through, NULL is acceptable here
                _log.warning(
                    "%s.%s: SEQUENCE query %r failed for column %s: %s — bind left NULL",
                    self.name, qdef.name, seq_query_name, k, exc,
                )
        return out

    def _row_cap(self, qdef: QueryDef, override: int | None) -> int:
        """Effective row cap for one SELECT: a per-request *override* if given, else the query's
        ``max_rows``, else this connector's (which already folds in the pool's) — clamped to
        ``[1, HARD_MAX_ROWS]``."""
        cap = override if override is not None else (qdef.max_rows if qdef.max_rows is not None else self.max_rows)
        return max(1, min(int(cap), self.HARD_MAX_ROWS))

    async def execute(
        self, query_name: str, params: dict[str, Any] | None = None, *, language: str | None = None,
        max_rows: int | None = None, user: str | None = None,
    ) -> QueryResult:
        """Run *query_name* with *params*; raises on bad input, returns on success.

        The SQL variant matching the pool's database is selected (``QueryDef.sql_for``).
        Result-column display hints (``QueryDef.columns``) are resolved against the shared
        dictionary in *language* (default: the dictionary's ``default_language``). *max_rows*
        overrides the configured row cap for this call (query → connector → pool → 1000), clamped
        to ``[1, HARD_MAX_ROWS]``. *user* is the caller's username — recorded on the audit row
        when ``QueryDef.audit`` is set; defaults to ``"anonymous"`` for unauthenticated paths.
        Raises :class:`QueryNotFoundError`, :class:`StatementNotAllowedError`,
        :class:`WriteNotAllowedError`, or :class:`UnknownPoolError`; database errors propagate as the
        underlying SQLAlchemy exception.
        """
        lang = language or self._dict.default_language
        qdef = self.get_query(query_name)
        cap = self._row_cap(qdef, max_rows)
        sql_text = _apply_schema_placeholders(
            qdef.sql_for(self._resolve_dialect()), self._pools.schemas(self.pool_name),
            connector=self.name, query=query_name, pool=self.pool_name,
        )
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
        # Phase 8: dynamic filter wrap. On SELECT, if any ``filter = true`` column has a
        # caller-supplied value, wrap the inner SELECT with a per-column predicate (text →
        # operator-aware, non-text → equals with a Python-coerced bind). Skipped if no
        # filters are active so the stored SELECT runs unwrapped most of the time. The
        # wrap helper returns a params dict whose active-filter values are coerced to
        # the proper Python type (str → int / date / bool); ``_build_params`` runs
        # afterwards against the (possibly-wrapped) SQL so it picks up the new ``:NAME``
        # binds with their coerced values.
        sql_text, params = self._apply_filter_wrap(sql_text, qdef, params, stmt_type=stmt_type)
        bound = self._build_params(sql_text, qdef, params)
        # Resolve form-layer rules (LOGIN / SYSDATE / PASSWORD / DEFAULT) + coerce string
        # binds to the matching Python type for the column's resolved format. SEQUENCE / NN
        # is deferred to inside the write transaction (it needs the DB). On SELECT this is
        # a no-op for the filter binds (they're already typed via the runtime CAST in the
        # wrap, and SYSDATE/etc. only fire on writes anyway).
        bound = self._apply_form_rules(bound, qdef, stmt_type=stmt_type, user=user)
        stmt = text(sql_text)
        is_select = stmt_type == "SELECT"

        started = time.perf_counter()
        if is_select:
            async with engine.connect() as conn:
                result = await conn.execute(stmt, bound)
                columns = _apply_column_hints(_columns_from_result(result), qdef.columns, self._dict, lang, connector=self.name)
                rows: list[dict[str, Any]] = []
                truncated = False
                for row in result.mappings():
                    if len(rows) >= cap:
                        truncated = True
                        break
                    rows.append(dict(row))
            # Oracle CHAR/NCHAR columns are space-padded to their declared width; the trailing
            # spaces leak into UI labels and form fields and make editing painful (cursor lands
            # after the padding, search/sort sees "Test    " ≠ "Test"). v1 stripped automatically;
            # v2 does the same when the pool flag is set (auto-on for Oracle, see ``PoolRegistry.
            # trim_strings``). The trim only touches strings — numbers, dates, bytes pass through.
            if self._pools.trim_strings(self.pool_name):
                for row in rows:
                    for k, v in row.items():
                        if isinstance(v, str):
                            row[k] = v.rstrip()
            # JDE Julian dates → ISO ``"YYYY-MM-DD"`` strings on read. v1's `DynamicResultMapper`
            # parity (deferred until now). Symmetric with ``_coerce_value(value, "jdedate")``
            # on writes: the form sends an ISO date, we re-encode to CYYDDD on bind. Without
            # this every JDE date in NOMAJDE showed as a raw integer in the grid (UPMJ etc.).
            # Match columns case-insensitively — the resolved Column carries the discovered
            # case (Postgres → lowercase) while the row dict uses the same.
            _jdedate_cols = [c.name for c in columns if (c.format or "").lower() == "jdedate"]
            if _jdedate_cols:
                for row in rows:
                    for cn in _jdedate_cols:
                        if cn in row:
                            row[cn] = _from_jde_julian(row[cn])
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

        # Oracle write-time null coalesce — see the helpers at the top of the module. Lookup
        # the target table's column types once (cached), replace None bind values with the
        # right sentinel. Skipped silently if the SQL shape doesn't match a single-target
        # write (the operator can still hand-roll COALESCE / NVL in unusual shapes).
        if self._pools.coalesce_nulls(self.pool_name):
            target = _oracle_target_table(sql_text)
            if target is not None:
                owner, table = target
                key = (self.pool_name, owner, table)
                col_types = _ORACLE_COL_TYPES_CACHE.get(key)
                if col_types is None:
                    try:
                        introspect_sql = (
                            "SELECT column_name, data_type FROM all_tab_columns "
                            "WHERE table_name = :t"
                        ) + (" AND owner = :o" if owner else "")
                        introspect_params: dict[str, Any] = {"t": table}
                        if owner:
                            introspect_params["o"] = owner
                        async with engine.connect() as introspect_conn:
                            r = await introspect_conn.execute(text(introspect_sql), introspect_params)
                            col_types = {}
                            for row in r.mappings():
                                cname = str(row.get("column_name") or "").upper()
                                dtype = str(row.get("data_type") or "").upper()
                                if not cname:
                                    continue
                                if dtype in _ORACLE_CHAR_TYPES:
                                    col_types[cname] = "char"
                                elif dtype in _ORACLE_NUMBER_TYPES:
                                    col_types[cname] = "number"
                                else:
                                    col_types[cname] = "other"
                            _ORACLE_COL_TYPES_CACHE[key] = col_types
                    except Exception as e:
                        _log.warning(
                            "oracle column introspection failed for %s.%s on pool %r: %s — "
                            "writes proceed without null coalesce",
                            owner or "?", table, self.pool_name, e,
                        )
                        col_types = {}
                bound = _coalesce_oracle_nulls(bound, col_types)

        async with engine.begin() as conn:
            # SEQUENCE / NN — run the named "next number" query in the *same* transaction as
            # the INSERT so a concurrent insert can't grab the same value (the surrounding
            # ``engine.begin()`` block serialises). A missing/failing sequence logs and falls
            # through with NULL (the DB will then reject the row if the column is NOT NULL).
            bound = await self._resolve_sequences(conn, bound, qdef, stmt_type=stmt_type, language=lang)
            result = await conn.execute(stmt, bound)
            rowcount = result.rowcount
            # AUD audit (v1's tbl_audit = 'Y' → migrated as QueryDef.audit = "AUD_<table>"). The
            # mirror INSERT runs in the *same* transaction so a successful write + failing audit
            # rolls back together — a missing/misshapen AUD table is loud, not silently dropped.
            # Pass the *coerced/resolved* bound params (post LOGIN/SYSDATE/PASSWORD/SEQUENCE) so
            # the audit mirror matches what actually hit the DB.
            if qdef.audit:
                await self._write_audit(conn, qdef, stmt_type, bound, user, main_sql=sql_text)
        duration_ms = (time.perf_counter() - started) * 1000.0
        return QueryResult(
            connector=self.name,
            query=query_name,
            statement_type=stmt_type,
            rowcount=rowcount,
            duration_ms=duration_ms,
        )

    async def test_run(
        self, sql_text: str, params: dict[str, Any] | None = None, *,
        max_rows: int | None = None, dry_run: bool = True,
    ) -> QueryResult:
        """Run a **free-form SQL string** against this connector's pool — no name lookup, no
        ``writable`` gate (the caller's privilege is the gate; the route is superuser-only).
        The same safety floor as :meth:`execute` applies: the leading keyword must be in
        :data:`~liberty.connectors.base.ALLOWED_STATEMENTS` (``DROP``/``ALTER``/``TRUNCATE``
        rejected). ``#SCHEMA.<NAME>#`` placeholders are still substituted, so the operator can
        paste the same SQL they keep in the TOML and have it run.

        *dry_run* is the safety net for the editor's "Run" button: a write statement runs
        inside a transaction that's rolled back on completion — same effect as a DBA's
        ``BEGIN; …; ROLLBACK;``, so the operator can see the rowcount + verify the statement
        parses without actually mutating the database. SELECTs ignore it (read-only). Set to
        ``False`` to commit a test write (typically when the operator clicks a second
        "Commit" confirmation).

        Returns the same :class:`QueryResult` shape as :meth:`execute` — but the ``query``
        field is the literal ``"<test-run>"`` so the result is obviously not a named query.
        """
        sql_text = _apply_schema_placeholders(
            sql_text, self._pools.schemas(self.pool_name),
            connector=self.name, query="<test-run>", pool=self.pool_name,
        )
        stmt_type = detect_statement_type(sql_text)
        if stmt_type not in ALLOWED_STATEMENTS:
            raise StatementNotAllowedError(
                f"{self.name} test-run: statement type {stmt_type or '(unknown)'!r} is not allowed "
                f"(allowed: {', '.join(sorted(ALLOWED_STATEMENTS))})."
            )
        cap = max(1, min(int(max_rows if max_rows is not None else self.max_rows), self.HARD_MAX_ROWS))
        # Bind every :name token from the SQL — missing values default to SQL NULL, same as execute()
        bound = {name: (params or {}).get(name) for name in find_bind_params(sql_text)}
        stmt = text(sql_text)
        is_select = stmt_type == "SELECT"
        engine = self._pools.engine(self.pool_name)
        started = time.perf_counter()
        if is_select:
            async with engine.connect() as conn:
                result = await conn.execute(stmt, bound)
                # No QueryDef → no column hints to overlay; we still expose discovered names + types.
                columns = _columns_from_result(result)
                rows: list[dict[str, Any]] = []
                truncated = False
                for row in result.mappings():
                    if len(rows) >= cap:
                        truncated = True
                        break
                    rows.append(dict(row))
            duration_ms = (time.perf_counter() - started) * 1000.0
            return QueryResult(
                connector=self.name, query="<test-run>", statement_type=stmt_type,
                columns=columns, rows=rows, rowcount=-1, duration_ms=duration_ms, truncated=truncated,
            )
        # Write — wrap in a transaction. With `dry_run`, raising `_DryRunRollback` out of
        # `engine.begin()`'s context manager triggers a rollback (SQLAlchemy's documented behaviour
        # when the block exits with an exception); we catch it outside and report the rowcount.
        # Without dry_run the block exits normally and the transaction commits.
        rowcount = -1
        try:
            async with engine.begin() as conn:
                result = await conn.execute(stmt, bound)
                rowcount = result.rowcount
                if dry_run:
                    raise _DryRunRollback()
        except _DryRunRollback:
            pass
        duration_ms = (time.perf_counter() - started) * 1000.0
        return QueryResult(
            connector=self.name, query="<test-run>", statement_type=stmt_type,
            rowcount=rowcount, duration_ms=duration_ms,
        )

    async def _ensure_audit_table(self, conn, audit_name: str, main_sql: str | None) -> None:
        """v1 parity — create the AUD_<table> companion on the first write to it. v1's
        ``tbl_audit = 'Y'`` flag never required the operator to pre-create the audit table;
        the framework did it lazily. v2 keeps the same: a metadata-table probe checks
        existence; on miss we ``CREATE TABLE AS SELECT * FROM <source> WHERE 1=0`` with
        three extra audit columns (AUD_ACTION / AUD_USER / AUD_DATE).

        **The probe MUST be a metadata-table query, not a ``SELECT 1 FROM <audit>``.**
        Postgres aborts the entire transaction the instant a statement references a
        non-existent relation (``current transaction is aborted, commands ignored until
        end of transaction block``); the subsequent ``CREATE TABLE`` is then rejected and
        the whole audited write rolls back. ``information_schema.tables`` /
        ``all_tables`` / ``sqlite_master`` return zero rows on a miss without poisoning
        the transaction.

        The source table is parsed from *main_sql* (the write statement that triggered this
        audit) — same parser that already powers Oracle's null-coalesce step. When we can't
        identify a source (unusual DML shape), we log a warning and let the audit INSERT
        fail loudly: the v2 contract is "audit + main write succeed together or both
        roll back", and silently dropping the audit would break that.

        Verified-tables cache is process-wide and pool-scoped — once we've confirmed
        AUD_LICENSE_CSI exists on ``pools.nomasx1``, we don't probe again until a
        ``ConnectorRegistry.aclose`` clears the cache (hot-reload).
        """
        cache_key = (self.pool_name, audit_name.upper())
        if cache_key in _AUDIT_TABLES_VERIFIED:
            return
        dialect = self._resolve_dialect()
        # Split off an optional ``schema.audit_name`` prefix. The migration only ever emits
        # ``AUD_<TBL_DB_NAME>`` (no schema), but a hand-edited config might qualify it.
        if "." in audit_name:
            schema_part, table_part = audit_name.split(".", 1)
        else:
            schema_part, table_part = None, audit_name
        # Identifier-case normalisation per dialect — Postgres folds unquoted to lowercase,
        # Oracle to uppercase, SQLite is case-preserving but case-insensitive on lookup.
        # The metadata tables are case-sensitive on the *value*, so we match the dialect's
        # convention. (A quoted mixed-case identifier wouldn't survive ``audit_name`` being
        # a plain Python string anyway — operators using mixed case wire audit by hand.)
        if dialect == "postgresql":
            probe_sql = (
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = :t" + (" AND table_schema = :s" if schema_part else "") + " LIMIT 1"
            )
            params: dict[str, Any] = {"t": table_part.lower()}
            if schema_part:
                params["s"] = schema_part.lower()
        elif dialect == "oracle":
            probe_sql = (
                "SELECT 1 FROM all_tables "
                "WHERE table_name = :t" + (" AND owner = :s" if schema_part else "") + " AND ROWNUM = 1"
            )
            params = {"t": table_part.upper()}
            if schema_part:
                params["s"] = schema_part.upper()
        elif dialect == "sqlite":
            probe_sql = "SELECT 1 FROM sqlite_master WHERE type='table' AND name = :t LIMIT 1"
            params = {"t": table_part}
        else:
            # Unknown dialect — let the audit INSERT itself raise if the table is missing.
            _log.warning(
                "audit: existence probe for %s isn't supported on dialect %r — assuming the "
                "table exists; the audit INSERT will surface a missing-table error if it doesn't",
                audit_name, dialect,
            )
            _AUDIT_TABLES_VERIFIED.add(cache_key)
            return
        try:
            r = await conn.execute(text(probe_sql), params)
            if r.first() is not None:
                _AUDIT_TABLES_VERIFIED.add(cache_key)
                return
        except Exception as e:  # noqa: BLE001 — permissions / no metadata access / weird driver
            _log.warning(
                "audit: probe for %s on pool %r returned an unexpected error: %s — the audit "
                "INSERT will surface the real error if it fails",
                audit_name, self.pool_name, e,
            )
            _AUDIT_TABLES_VERIFIED.add(cache_key)  # don't keep probing every write
            return

        source = _oracle_target_table(main_sql) if main_sql else None
        if source is None:
            _log.warning(
                "audit: cannot auto-create %s — couldn't identify the source table from the "
                "write statement on %s.%s. Create the audit table by hand (or simplify the SQL "
                "to a single-target write).",
                audit_name, self.name, "(qdef)",
            )
            return
        source_owner, source_table = source
        qualified_source = f"{source_owner}.{source_table}" if source_owner else source_table
        # Dialect-specific DDL — the column list comes from the source table's schema; the
        # three audit columns are appended via ``CAST(NULL AS ...) AS AUD_*``. Postgres uses
        # ``VARCHAR``; Oracle uses ``VARCHAR2``. SQLite (dev) is permissive on types and
        # accepts ``VARCHAR``. Other dialects (MySQL, MSSQL) — operator wires audit by hand.
        dialect = self._resolve_dialect()
        if dialect == "oracle":
            varchar = "VARCHAR2"
        elif dialect in ("postgresql", "sqlite"):
            varchar = "VARCHAR"
        else:
            _log.warning(
                "audit: auto-create for %s on dialect %r isn't supported — create the AUD_ "
                "table by hand or use ``dialog.on_save`` actions for custom audit.",
                audit_name, dialect,
            )
            return
        # ``CAST(NULL AS …)`` makes the audit columns nullable on both engines without a
        # follow-up ALTER. Aliases ensure the column names land regardless of the dialect's
        # rules around column derivation from constant expressions.
        ddl = (
            f"CREATE TABLE {audit_name} AS SELECT t.*, "
            f"CAST(NULL AS {varchar}(20)) AS AUD_ACTION, "
            f"CAST(NULL AS {varchar}(100)) AS AUD_USER, "
            f"CAST(NULL AS TIMESTAMP) AS AUD_DATE "
            f"FROM {qualified_source} t WHERE 1=0"
        )
        try:
            await conn.execute(text(ddl))
            _log.info(
                "audit: auto-created %s on pool %r (from %s) — v1 tbl_audit='Y' parity",
                audit_name, self.pool_name, qualified_source,
            )
            _AUDIT_TABLES_VERIFIED.add(cache_key)
        except Exception as exc:  # noqa: BLE001 — let the caller see the audit INSERT failure
            _log.warning(
                "audit: auto-create of %s from %s on pool %r failed: %s — the audit INSERT "
                "will surface the underlying error", audit_name, qualified_source, self.pool_name, exc,
            )

    async def _write_audit(
        self, conn, qdef: QueryDef, stmt_type: str, params: dict[str, Any], user: str | None,
        *, main_sql: str | None = None,
    ) -> None:
        """Mirror a writable execute into ``qdef.audit`` (v1's AUD_<table> pattern). Logs the
        bound row's *uppercase* params (the v1 convention — the migrated _put/_post/_delete SQLs
        bind columns as ``:USR_ID`` etc.) and three audit columns:

        * ``AUD_ACTION`` — the statement type (``INSERT`` / ``UPDATE`` / ``DELETE``)
        * ``AUD_USER`` — the caller's username (or ``"anonymous"`` if the call wasn't authenticated)
        * ``AUD_DATE`` — UTC timestamp captured server-side

        Columns are taken from ``params`` (uppercase keys, not ending in ``_ORIGINAL`` — those are
        only WHERE rebinds for the main UPDATE). The AUD table is auto-created from the source
        table's schema on first write (v1 parity — operators never ran DDL to set up audit, the
        framework did it lazily) via :meth:`_ensure_audit_table`; *main_sql* identifies the
        source table for that step. The migration emits ``audit = "AUD_<TBL_DB_NAME>"`` on
        writable companions when v1's ``tbl_audit = 'Y'`` is set, so the names line up."""
        cols: dict[str, Any] = {}
        for k, v in (params or {}).items():
            if not k or not k.isupper() or k.endswith("_ORIGINAL"):
                continue
            cols.setdefault(k, v)
        if not cols and stmt_type != "DELETE":
            # nothing to audit — leave a footprint so an operator who expected audit knows why nothing landed
            _log.warning(
                "audit: %s.%s — no uppercase params to log into %s; skipping the audit row",
                self.name, qdef.name, qdef.audit,
            )
            return
        # v1 parity: lazily create AUD_<table> from the source table's schema if it doesn't
        # exist yet. Runs in the same write transaction as the main statement so a failure
        # rolls everything back together.
        await self._ensure_audit_table(conn, qdef.audit or "", main_sql)
        # Build `INSERT INTO <audit> (col1, col2, …, AUD_ACTION, AUD_USER, AUD_DATE)
        #                    VALUES (:col1, :col2, …, :_AUD_ACTION, :_AUD_USER, :_AUD_DATE)`
        # Reserved bind names start with `_aud_` so they can't collide with the row's columns.
        col_list = list(cols)
        # ``_aud_date`` is naive UTC: the audit column is ``TIMESTAMP`` (without time zone)
        # — matches the auto-create DDL above and what v1's AUD_<table>s used. Binding a
        # tz-aware ``datetime.now(UTC)`` against a tz-naive column trips asyncpg with
        # "can't subtract offset-naive and offset-aware datetimes" and rolls the whole
        # audited write back (auto-create + main + audit are one transaction). The same
        # naive-UTC convention runs for SYSDATE in ``_apply_form_rules``, so the two
        # paths stay consistent.
        bind_params = {
            **cols,
            "_aud_action": stmt_type,
            "_aud_user": user or "anonymous",
            "_aud_date": datetime.now(timezone.utc).replace(tzinfo=None),
        }
        col_sql = ", ".join([*col_list, "AUD_ACTION", "AUD_USER", "AUD_DATE"])
        val_sql = ", ".join([f":{c}" for c in col_list] + [":_aud_action", ":_aud_user", ":_aud_date"])
        # qdef.audit is the table name; we don't validate it here — it's operator config, same as
        # other table names embedded in SQL. The migration only ever emits ``AUD_<UPPER>`` so the
        # surface is small in practice.
        audit_sql = f"INSERT INTO {qdef.audit} ({col_sql}) VALUES ({val_sql})"
        await conn.execute(text(audit_sql), bind_params)


def _resolve_hint(
    h: ColumnHint, dictionary: DictionaryFile, language: str | None, *,
    connector: str | None = None, type_: str | None = None, name: str | None = None,
) -> Column:
    """A :class:`Column` from a hint: ``label``/``format`` come from the hint if set, else
    from the dictionary entry (key = ``h.dd`` or, when unset, ``h.name``; ``dd = ""`` opts out)
    in *language* — *connector*'s per-connector section first, then the shared top-level entries.
    The entry's display ``rule`` (BOOLEAN / ENUM / LOOKUP — see :meth:`DictionaryFile.resolve_rule`)
    is resolved too and attached for the frontend. ``hidden``/``width``/``align`` are always the
    hint's. *name* overrides the column name (used when the result reports a different case than
    the hint — see :func:`_apply_column_hints`)."""
    label, fmt = h.label, h.format
    rule: dict[str, Any] | None = None
    if h.dictionary_key:
        entry = dictionary.find_entry(h.dictionary_key, connector=connector)
        if entry is not None:
            if label is None:
                label = entry.label_for(language)
            if fmt is None:
                fmt = entry.format
            rule = dictionary.resolve_rule(entry, connector=connector, language=language)
    return Column(
        name=name or h.name, type=type_, label=label, hidden=h.hidden, filter=h.filter,
        filter_from=[{"source": d.source, "column": d.column} for d in h.filter_from],
        visible_when=[r.as_dict() for r in h.visible_when_rules],
        width=h.width, align=h.align, format=fmt, rule=rule,
        # Only surface `dd` when it's *explicitly* set on the hint (a non-empty override). For
        # `dd = None` (default — dictionary lookup happens by column name) or `dd = ""` (operator
        # opted out of the dictionary), don't pollute the wire. Phase-8 dashboard filters key on
        # this to cross-map columns by dictionary key (`APPS_ID` matches USR_APPS_ID / RLU_APPS_ID
        # / CFD_APPS_ID across queries).
        dd=h.dd if h.dd else None,
    )


def _hint_to_dict(h: ColumnHint, dictionary: DictionaryFile, language: str | None, *, connector: str | None = None) -> dict[str, Any]:
    """A configured hint as a dict for ``describe()`` — name + the *resolved* label/format
    + any hidden/width/align + the ``dd`` ref (if set). No ``type`` (a hint isn't a result column)."""
    d = _resolve_hint(h, dictionary, language, connector=connector).to_dict()
    d.pop("type", None)
    if h.dd is not None:
        d["dd"] = h.dd
    return d


def _apply_column_hints(
    discovered: list[Column], hints: list[ColumnHint], dictionary: DictionaryFile, language: str | None,
    *, connector: str | None = None,
) -> list[Column]:
    """Overlay the query's ``columns`` hints on the discovered columns: reorder to the hint
    order, attach label/hidden/width/align/format (label & format pulled from the dictionary —
    *connector*'s section then the shared one — when not given inline; see :func:`_resolve_hint`).
    Columns with no hint keep their discovery order and follow the hinted ones; a hint for a
    column the query didn't return is ignored (a stale hint never fabricates a column).

    A hint matches a result column **case-insensitively** — the database folds unquoted
    identifiers (PostgreSQL → lowercase, Oracle → uppercase), while v1's migrated hints are
    uppercase. The emitted column keeps the *discovered* name so it still lines up with the
    keys in each result row."""
    if not hints:
        return discovered
    remaining = {c.name: c for c in discovered}        # insertion-ordered → preserves discovery order
    by_lower = {c.name.lower(): c for c in discovered}  # case-insensitive hint → column lookup
    ordered: list[Column] = []
    for h in hints:
        col = by_lower.get(h.name.lower())
        if col is None or col.name not in remaining:    # stale hint, or already consumed
            continue
        del remaining[col.name]
        ordered.append(_resolve_hint(h, dictionary, language, connector=connector, type_=col.type, name=col.name))
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
