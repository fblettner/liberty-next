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
from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncIterator

_log = logging.getLogger(__name__)

# ── Oracle write-time null coalesce helpers ────────────────────────────────────────────────
# When the pool flag ``coalesce_nulls`` is set (operator opts in per pool — typically Oracle
# pools with NOT-NULL string columns like JD Edwards' NCHAR-heavy F00xxx tables), the SQL
# connector inspects the target table once and replaces empty bind values (``None`` *or* ``""``
# — the frontend submits ``""`` for blank inputs) with type-appropriate sentinels: a single
# space for CHAR / NCHAR / VARCHAR2 / NVARCHAR2 / CLOB / NCLOB / LONG, ``0`` for NUMBER /
# FLOAT / BINARY_FLOAT / BINARY_DOUBLE / INTEGER. Oracle treats ``""`` as ``NULL`` on every
# string type, so binding ``""`` to a NCHAR NOT NULL column still violates the constraint —
# only a real character (the space) does. The flag is off by default; not every Oracle
# deployment uses CHAR/NCHAR, so the choice is left explicit per pool.

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


# Per-(pool, owner, table) cache of column metadata. Each entry is
# ``{column_name: {"kind": str, "length": int | None}}`` where ``kind`` is one of
# ``"char_fixed"`` (CHAR / NCHAR — space-padded by Oracle), ``"char"`` (VARCHAR2 / NVARCHAR2
# / CLOB / NCLOB / LONG — not padded), ``"number"``, or ``"other"``. ``length`` carries the
# declared CHAR length and is only meaningful for ``"char_fixed"`` (None elsewhere — VARCHAR2
# binds don't pad). Populated lazily on first write to a table — refreshed on registry rebuild
# via :func:`reset_oracle_column_cache`. Module-level so all SQLConnectors sharing a pool also
# share the cache.
_ORACLE_COL_TYPES_CACHE: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}


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


# Fixed-width CHAR family — Oracle space-pads stored values to the column's declared length.
# Comparisons against a VARCHAR2 bind use non-blank-padded semantics, so a trimmed read value
# bound back into a WHERE clause won't match its CHAR(N) source unless the bind is re-padded
# to N spaces. ``_pad_char_binds`` does that under the ``trim_strings`` pool flag.
_ORACLE_FIXED_CHAR_TYPES = {"CHAR", "NCHAR"}
# Variable-width / large character storage — never padded; padding a bind here would BREAK
# the comparison (stored "JDE" ≠ bind "JDE       " under non-blank-padded VARCHAR2 semantics).
_ORACLE_VAR_CHAR_TYPES = {"VARCHAR2", "NVARCHAR2", "VARCHAR", "CLOB", "NCLOB", "LONG"}
# Union — used by the empty-bind coalesce path (every Oracle string column treats ``""`` as
# NULL, so all of these need the single-space sentinel for NOT-NULL columns).
_ORACLE_CHAR_TYPES = _ORACLE_FIXED_CHAR_TYPES | _ORACLE_VAR_CHAR_TYPES
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
# JDE times — stored as integers ``HHMMSS`` in JD Edwards (14:30:52 → 143052, midnight → 0).
# Symmetric with jdedate: a Python time/datetime / "HH:MM:SS" string / already-HHMMSS int all
# collapse to the integer form on write, and decode back to "HH:MM:SS" on read.
_JDETIME_FORMATS = {"jdetime"}


def _to_jde_time(t: Any) -> int | None:
    """``time | datetime | "HH:MM:SS" string | HHMMSS int`` → JDE ``HHMMSS`` integer; ``None``
    when the input can't be parsed. ``int`` passes through (already in HHMMSS form); a string of
    digits is treated as a pre-converted HHMMSS integer. A "HH:MM:SS" (or "HH:MM") string is
    parsed to its components. Out-of-range components return ``None``."""
    from datetime import time as _time
    if isinstance(t, bool):
        return None
    if isinstance(t, int):
        return t if 0 <= t <= 235959 else None
    if isinstance(t, datetime):
        t = t.time()
    if isinstance(t, str):
        s = t.strip()
        if s.isdigit():
            n = int(s)
            return n if 0 <= n <= 235959 else None
        # "HH:MM[:SS]" — accept a bare HH:MM (seconds default to 0).
        parts = s.split(":")
        if 2 <= len(parts) <= 3 and all(p.isdigit() for p in parts):
            hh, mm = int(parts[0]), int(parts[1])
            ss = int(parts[2]) if len(parts) == 3 else 0
            if 0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60:
                return hh * 10000 + mm * 100 + ss
        # An ISO datetime string ("YYYY-MM-DD HH:MM:SS") — pull the time off the back.
        try:
            return _to_jde_time(datetime.fromisoformat(s.replace("T", " ")).time())
        except ValueError:
            return None
    if isinstance(t, _time):
        return t.hour * 10000 + t.minute * 100 + t.second
    return None


def _from_jde_time(v: Any) -> str | int | None:
    """Inverse of :func:`_to_jde_time` — JDE ``HHMMSS`` integer → ``"HH:MM:SS"`` string. The
    frontend renders/edits the string; the write path re-encodes via ``_coerce_value(.., "jdetime")``.
    Pass-through on ``None``; out-of-range components return the raw value so bad data stays visible."""
    if v is None:
        return None
    try:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
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
    if not (0 <= n <= 235959):
        return v
    hh, mm, ss = n // 10000, (n // 100) % 100, n % 100
    if mm >= 60 or ss >= 60:
        return v
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


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
        if f in _JDETIME_FORMATS:
            # Same contract as jdedate: JDE stores HHMMSS integers. A SYSDATE-stamped ``now``
            # (datetime) lands here for a ``format = "jdetime"`` audit column and becomes HHMMSS.
            return _to_jde_time(value)
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
# CURRENT_DATE (alias of SYSDATE) and NN (alias of SEQUENCE) were retired in v2 — the migrator
# normalises them, so the runtime only recognises the survivors.
_RULES_NOW = {"SYSDATE"}
_RULES_SEQUENCE = {"SEQUENCE"}
# Statements with a SET/VALUES clause that a DEFAULT can fill — DELETE binds only a WHERE key,
# so a default there would corrupt the predicate. (SEQUENCE/NN stays INSERT-only — see
# ``_resolve_sequences`` — because a sequence on UPDATE would re-issue a new number every save.)
_RULES_DEFAULTABLE = {"INSERT", "UPDATE", "MERGE"}
# Statements that have a SET/VALUES clause at all — the only ones where a form rule (LOGIN /
# SYSDATE / BOOLEAN / DEFAULT) should *substitute* a bind. A DELETE is WHERE-only: every bind is a
# key match against the stored row, so stamping SYSDATE/LOGIN there would corrupt the predicate
# (e.g. a jdedate key bound to *today* instead of the row's saved effective date). DELETE binds get
# type coercion only — same treatment as an UPDATE's ``_ORIGINAL`` WHERE binds.
_SET_CLAUSE_STATEMENTS = {"INSERT", "UPDATE", "MERGE"}
_RULES_PASSWORD = {"PASSWORD"}
_RULES_DEFAULT = {"DEFAULT"}  # use the entry's `default` when the bind is missing/empty
# v1 parity: a column-hint-level ``rules = "DISABLED"`` opts out of an inherited dictionary
# rule on this specific screen. Used to keep SEQUENCE from refiring on UPDATE (we already
# scope SEQUENCE to INSERT, so this is mostly belt-and-braces — but the operator may want it
# on PASSWORD / SYSDATE too, e.g. an import screen that bulk-loads pre-computed values).
_RULES_DISABLED = {"DISABLED"}


# ── Predefined query tokens (``{{LOGIN}}`` / ``{{SYSDATE}}`` …) ────────────────────────────────
# A writable query embeds these where it wants an automatic, server-resolved value — making the
# stamp an EXPLICIT decision of the query (``ULUPMJ = {{SYSDATE}}``) instead of an implicit side
# effect of the column's data-dictionary rule (which is overloaded for display / default / format
# elsewhere). Resolved at execute time, **bound** (never string-substituted — SQL-injection-safe),
# and — for an ``COL = {{TOKEN}}`` assignment — coerced to the assigned column's DD format, so
# ``{{SYSDATE}}`` on a ``jdedate`` column lands as the CYYDDD integer and ``{{SYSTIME}}`` on a
# ``jdetime`` column as HHMMSS. A normal ``:BIND`` stays value-driven (screen value wins, then
# default / DD rule); a token is always the server value (not screen-overridable). ``LOGIN`` is the
# authenticated principal (uppercased, server-trusted). Unknown ``{{x}}`` is left untouched.
#
# ``JDEDATE`` / ``JDETIME`` ALWAYS produce the JDE Julian date (CYYDDD) / time (HHMMSS) integer,
# independent of any column-format lookup — use these on JDE audit columns (``ULUPMJ`` / ``ULUPMT``):
# their physical name (table-alias prefix + data item) isn't in the dictionary (only the data item
# ``UPMJ`` / ``UPMT`` is) and they aren't screen fields, so ``{{SYSDATE}}``'s adapt-to-column
# coercion can't resolve a format for them and would emit a raw date/time (which oracledb rejects).
# ``SYSDATE`` / ``SYSTIME`` stay adaptive — for a plain date/datetime column or one whose format IS
# resolvable via a column hint / the dictionary.
#
# ``PID`` / ``JOBN`` are the JDE audit provenance constants: ``PID`` → the connector name
# (uppercased), ``JOBN`` → ``"LIBERTY"``. Strings, no format dependency.
_PREDEFINED_TOKENS = {
    "LOGIN", "USER", "SYSDATE", "SYSTIME", "SYSTIMESTAMP", "NOW", "JDEDATE", "JDETIME", "PID", "JOBN",
}
#: The ``{{JOBN}}`` constant — Liberty as the writing job/workstation in JDE audit columns.
_JOBN_VALUE = "LIBERTY"
# Formats whose "not applicable" (write_when=false) neutral value is ``0``; everything else is a
# single space (JDE CHAR NOT-NULL safe — padded to width on Oracle, matching the coalesce step's
# ``" "`` sentinel for empties). See ``_neutral_for_format`` / ``SQLConnector._apply_write_when``.
_NEUTRAL_NUMERIC_FORMATS = {"integer", "number", "decimal", "currency", "jdedate", "jdetime"}


def _neutral_for_format(fmt: str | None) -> Any:
    """The value written for a column whose ``write_when`` does not hold — i.e. it's irrelevant to
    this row's kind. ``0`` for numerics (incl. JDE jdedate/jdetime), a single space otherwise.
    Mirrors JDE's own convention (an N/A security column is stored blank / 0)."""
    if fmt and fmt.lower() in _NEUTRAL_NUMERIC_FORMATS:
        return 0
    return " "


def _write_when_holds(rules: list[Any], field_value: Any) -> bool:
    """True when EVERY ``write_when`` rule holds (AND) — a rule holds when the discriminator's
    current value is in the rule's allowed set (trim-tolerant; value may be a scalar or a list). A
    rule whose ``field`` is absent from the row is treated as holding (fail-open): ``write_when``
    must never neutralise a column just because its discriminator wasn't supplied, so it can't
    regress a write that doesn't carry that field."""
    for r in rules:
        v = field_value(r.field)
        if v is None:
            continue  # discriminator not present → can't disqualify; fail open
        allowed = r.value if isinstance(r.value, list) else [r.value]
        if str(v).strip() not in {str(a).strip() for a in allowed}:
            return False
    return True
# ``COL = {{TOKEN}}`` — captures the assigned column (group 1, possibly schema/table-qualified or
# quoted) so the token coerces to that column's format. Then bare ``{{TOKEN}}`` (VALUES position,
# WHERE, …) resolves to the token's natural Python type.
_ASSIGN_TOKEN_RE = re.compile(
    r"""([A-Za-z_][\w."`\[\]]*?)\s*=\s*\{\{\s*([A-Za-z_]\w*)\s*\}\}""", re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"""\{\{\s*([A-Za-z_]\w*)\s*\}\}""")


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
    if f in ("integer", "number", "jdedate", "jdetime"):
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


def _build_filter_predicate(name: str, fmt: str | None, op: str, dialect: str, *, trim_text: bool = False) -> str:
    """One filter predicate against ``lib_flt.<name>`` for *op* using a bind named ``:<name>``.

    Text columns get the operator-aware ``LOWER(col) LIKE LOWER(...)`` family for
    contains/startsWith/endsWith plus ``=`` / ``<>`` for equals/notEquals. Crucially, we
    **don't CAST the column side** — Postgres can use a functional index on ``LOWER(col)``
    if defined, and the planner has the option to choose between seq-scan and index-scan;
    the previous universal CAST AS VARCHAR(4000) on every column kept it from ever using
    an index.

    ``trim_text`` (the pool's ``trim_strings`` flag) wraps the *text* column side in ``TRIM``.
    JDE stores CHAR(N) blank-padded, so ``DTSY`` comes back ``'01        '``; an ``equals``
    filter binding ``'01'`` would never match the raw column. The pool flag already trims the
    *result* rows — this makes the *filter* honour it too, so a query author no longer has to
    hand-write ``TRIM(DTSY)`` in the SELECT just to make its filters work. (Number / date columns
    aren't space-padded, so trimming is text-only.)

    Non-text columns (number / date / boolean / jdedate) only use ``equals``. The wrap
    helper coerces the bind to the right *Python* type before passing it down (so a
    string ``"10"`` becomes ``int(10)``) — asyncpg then sends the proper type code and
    the column's btree index applies natively. No SQL CAST on either side: pre-Phase-8
    we had ``col = CAST(:name AS INTEGER)`` which forced asyncpg's strict-type check to
    reject the string ``"10"`` even though Postgres would have accepted it.
    """
    text_format = (fmt or "").lower() in _TEXT_FORMATS or fmt is None or fmt == ""
    col_ref = f"TRIM(lib_flt.{name})" if (text_format and trim_text) else f"lib_flt.{name}"
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
    """Replace empty bind values (``None`` *or* ``""``) in *bound* with type-appropriate
    sentinels based on *col_types* — the column type map from :func:`_oracle_target_table` +
    ``ALL_TAB_COLUMNS``.

    Oracle treats the empty string ``''`` as ``NULL`` on every string type (CHAR / NCHAR /
    VARCHAR2 / NVARCHAR2 / CLOB / NCLOB / LONG), so binding ``""`` to a NOT-NULL column still
    fails with ORA-01400. The only safe "blank but non-NULL" value is a single space, which
    Oracle space-pads on CHAR/NCHAR and stores verbatim on VARCHAR2/NVARCHAR2 — same convention
    v1's framework used against JDE tables.

    Bind names that don't match any column pass through unchanged (filter operator binds,
    extras the migration added, etc.). The migration's ``:<COL>_ORIGINAL`` rebind for ``_put``
    queries' WHERE strips the suffix to find the source column type."""
    if not col_types:
        return bound
    out = dict(bound)
    for k, v in list(out.items()):
        if v is not None and v != "":
            continue
        base = k.upper()
        if base.endswith("_ORIGINAL"):
            base = base[: -len("_ORIGINAL")]
        meta = col_types.get(base)
        kind = (meta or {}).get("kind")
        if kind in ("char", "char_fixed"):
            out[k] = " "  # space, not '' — Oracle treats '' as NULL on every string type
        elif kind == "number":
            out[k] = 0
    return out


def _jsonable_number(v: Any) -> Any:
    """Serialize a driver ``Decimal`` (Postgres ``numeric`` / ``decimal``, Oracle ``NUMBER``) as a
    JSON **number** instead of the string ``default=str`` / FastAPI's ``jsonable_encoder`` would
    emit. Without this a ``numeric`` column comes back as ``"123"`` and a chart / numeric consumer
    breaks — which is why queries grew ``CAST(col AS INTEGER)`` workarounds. Now they don't need to:

      * integral decimal (``Decimal('123')`` / ``Decimal('123.00')``) → ``int`` — lossless;
      * fractional (``Decimal('1.5')``) → ``float`` — JS / charts use floats anyway;
      * non-finite (``NaN`` / ``Infinity``) → ``str`` — not representable as a JSON number.

    Everything else (str / int / float / date / bytes / None) passes through untouched."""
    if isinstance(v, Decimal):
        if not v.is_finite():
            return str(v)
        return int(v) if v == v.to_integral_value() else float(v)
    return v


_WHERE_BIND_RE = re.compile(
    r"(?:"
    r"(\w+(?:\.\w+)?)\s*(?:=|<=|>=|<>|!=|<|>)\s*:(\w+)"     # COL <op> :BIND
    r"|:(\w+)\s*(?:=|<=|>=|<>|!=|<|>)\s*(\w+(?:\.\w+)?)"     # :BIND <op> COL
    r"|(\w+(?:\.\w+)?)\s+LIKE\s+:(\w+)"                       # COL LIKE :BIND
    r")",
    re.IGNORECASE,
)


def _where_bind_columns(sql: str) -> dict[str, str]:
    """Map a bind name → the column it's COMPARED to in *sql* (``RLTOROLE = :FROMUSER`` →
    ``{"FROMUSER": "RLTOROLE"}``). Lets :func:`_pad_char_binds` pad a WHERE/JOIN bind to the
    compared CHAR column's width even when the bind name differs from the column — the common case
    for an operator-authored query (a duplicate-relationship INSERT…SELECT WHERE ``RLTOROLE =
    :FROMUSER``) where ``_pad_char_binds``'s name-match can't reach it. Column qualifier
    (``A.RLTOROLE``) is dropped; bind names upper-cased to match the bound dict."""
    out: dict[str, str] = {}
    for m in _WHERE_BIND_RE.finditer(sql or ""):
        col, bind = (m.group(1), m.group(2)) if m.group(2) else \
                    (m.group(4), m.group(3)) if m.group(3) else (m.group(5), m.group(6))
        if not col or not bind:
            continue
        out.setdefault(bind.upper(), col.split(".")[-1].upper())
    return out


def _pad_char_binds(
    bound: dict[str, Any], col_types: dict[str, dict[str, Any]],
    where_col_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Right-pad non-empty string binds with spaces to the column's declared width when the
    target is a fixed-width ``CHAR(N)`` / ``NCHAR(N)``. Powers the symmetric round-trip the
    ``trim_strings`` pool flag promises: reads strip trailing whitespace, writes (especially
    DELETE / UPDATE WHERE clauses) pad the bind back so Oracle's non-blank-padded VARCHAR2 vs
    CHAR comparison matches the stored value.

    Without this, a JDE F0092 row read as ``ULUSER="JDE"`` (post-trim) is unfindable on
    DELETE / UPDATE — Oracle compares CHAR(10) ``"JDE       "`` against VARCHAR2 bind ``"JDE"``
    with non-blank-padded semantics → no match, 0 rows affected, 200 OK. The fix lives in the
    bind layer so operators don't have to hand-roll ``RPAD(:col, 10)`` in every WHERE.

    Only touches columns whose ``ALL_TAB_COLUMNS.data_type`` is CHAR / NCHAR. VARCHAR2 stays
    untouched (padding a VARCHAR2 bind would make it NOT match a non-padded stored value).
    Skips binds that aren't non-empty strings (numbers, dates, None, "" — the latter is the
    coalesce step's domain). The migration's ``:<COL>_ORIGINAL`` UPDATE rebind strips the
    suffix to find the source column metadata.

    Over-WIDTH SET values are trimmed to fit. JDE stores a UDC code in F0005's 10-char ``DRKY``
    (often right-justified, so it reads back ``"       DMY"``), but the *consuming* column is sized
    by the data dictionary — ``F00921.ULFRMT`` is ``CHAR(3)``. Writing the padded 10-char value
    there is ``ORA-12899: value too large``. So a non-``_ORIGINAL`` (write) value longer than the
    column is stripped to its logical content; Oracle re-pads ``CHAR`` on storage, and right-
    justified targets are re-justified by the query's own ``LPAD`` (e.g. F00950's MCU). ``_ORIGINAL``
    (WHERE) binds are left alone — they must equal the stored padded value to match. A pure-blank
    value is never stripped to ``""`` (that would become NULL on Oracle — the coalesce step's ``" "``
    sentinel survives)."""
    if not col_types:
        return bound
    out = dict(bound)
    for k, v in list(out.items()):
        if not isinstance(v, str) or not v:
            continue
        base = k.upper()
        is_original = base.endswith("_ORIGINAL")
        if is_original:
            base = base[: -len("_ORIGINAL")]
        meta = col_types.get(base)
        if meta is None and where_col_map:
            # The bind name isn't a column, but the SQL compares it to one (``RLTOROLE = :FROMUSER``)
            # — pad to THAT column's width. A compared bind must equal the stored padded value, so
            # treat it like an ``_ORIGINAL`` WHERE bind (pad only, never trim over-wide).
            mapped = where_col_map.get(base)
            if mapped is not None:
                meta = col_types.get(mapped)
                is_original = True
        if not meta or meta.get("kind") != "char_fixed":
            continue
        length = meta.get("length")
        if not isinstance(length, int) or length <= 0:
            continue
        if len(v) > length:
            # Too wide for the target — only fix SET values (a WHERE bind must keep the stored
            # padded form). Strip the source padding so the logical code fits; keep a blank as a
            # single space (NULL guard). If it's still too long after stripping, it's genuine
            # over-length data — leave it and let the DB raise.
            if not is_original:
                trimmed = v.strip() or " "
                if len(trimmed) <= length:
                    out[k] = trimmed
            continue
        if len(v) == length:
            continue
        out[k] = v.ljust(length)
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
from liberty.connectors.config import ColumnHint, QueryDef, SqlConnectorConfig, _crud_slot_to_querydef
from liberty.connectors.db import PoolRegistry
from liberty.connectors.dictionary import DictionaryEntry, DictionaryFile, SequenceDef

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
    key: bool = False
    # Per-row-mode edit locks (``ColumnHint.disable_on_add`` / ``disable_on_edit``). Surfaced so the
    # grid bulk-editor honours the SAME column setting the dialog does — locked → the cell falls back
    # to its read-only display in that mode. UI-only: the write path is unchanged.
    disable_on_add: bool = False
    disable_on_edit: bool = False
    filter: bool = False
    filter_from: list[dict[str, str]] = field(default_factory=list)
    visible_when: list[dict[str, Any]] = field(default_factory=list)
    # LOOKUP return → target-column fills (``ColumnHint.return_binds``). Each is ``{param, column}``:
    # on a pick, the picked row's ``param`` value is written into the row's ``column``. Drives the
    # grid bulk-edit + Excel import return-fill.
    return_binds: list[dict[str, str]] = field(default_factory=list)
    # Conditional forced defaults (``ColumnHint.default_when``). Each ``{field, value, default}``:
    # when sibling ``field`` == ``value``, this column is forced to ``default`` and locked. Honoured
    # by the grid bulk-edit + the dialog.
    default_when: list[dict[str, Any]] = field(default_factory=list)
    # LOOKUP/ENUM: show only the code column in the grid, not the resolved-label column
    # (``ColumnHint.hide_label``).
    hide_label: bool = False
    # Conditional RULE overrides (``ColumnHint.rules_when``). Each ``{field, value, rule}``: when
    # sibling ``field`` == ``value``, render with ``rule`` (a fully-resolved DisplayRule, or null →
    # plain) instead of the base ``rule``. First match wins; evaluated per row / form state. Every
    # referenced lookup is loaded once (the rule objects just say WHICH lookup), so it's per-cell
    # choice, not per-row fetch.
    rules_when: list[dict[str, Any]] = field(default_factory=list)
    width: int | None = None
    align: str | None = None
    format: str | None = None
    rule: dict[str, Any] | None = None
    dd: str | None = None
    # The related-table write-back group this column belongs to (``ColumnHint.group``). Surfaced so
    # the save path can split a row's writes per table; ``None`` ⇒ writes to the main table.
    group: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.label is not None:
            d["label"] = self.label
        if self.hidden:
            d["hidden"] = True
        if self.key:
            d["key"] = True
        if self.disable_on_add:
            d["disable_on_add"] = True
        if self.disable_on_edit:
            d["disable_on_edit"] = True
        if self.filter:
            d["filter"] = True
        if self.filter_from:
            d["filter_from"] = self.filter_from
        if self.visible_when:
            d["visible_when"] = self.visible_when
        if self.return_binds:
            d["return_binds"] = self.return_binds
        if self.default_when:
            d["default_when"] = self.default_when
        if self.hide_label:
            d["hide_label"] = True
        if self.rules_when:
            d["rules_when"] = self.rules_when
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
        if self.group is not None:
            d["group"] = self.group
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
    # Values the server filled in for binds the caller left empty — currently the SEQUENCE / NN
    # auto-IDs assigned during an INSERT (e.g. a sequence-driven PK). Surfaced so a caller can
    # chain a dependent write against the freshly-assigned key (parent insert → child insert
    # bound to the parent's new PK) without a separate read-back. Keys are uppercased column
    # names. Empty for SELECTs and for writes where the caller supplied every value explicitly.
    resolved_binds: dict[str, Any] = field(default_factory=dict)
    # The FULL final bind set that actually hit the DB on a write — post filter-wrap,
    # form-rule resolution (LOGIN / SYSDATE / DEFAULT / coercion), Oracle bind hygiene, and
    # SEQUENCE/NN. This is what the audit mirror records, and what change-capture must record
    # (so a DD default / audit value filled server-side on an empty bind is part of the package,
    # not the empty value the form sent). Server-side only — NOT in :meth:`to_dict` (it can carry
    # the whole row + resolved secrets; the client never needs it). Empty for SELECTs.
    bound_params: dict[str, Any] = field(default_factory=dict)

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
            "resolved_binds": self.resolved_binds,
        }


@dataclass(slots=True, frozen=True)
class StreamMeta:
    """First event yielded by :meth:`SQLConnector.execute_stream`. Carries the resolved column
    list + the effective row cap + the per-chunk size. Frontend uses ``columns`` to populate
    the grid headers before any data lands; ``cap`` is the upper bound so a progress UI can
    show "X of up to Y rows"."""
    columns: list[Column]
    cap: int
    chunk_size: int


@dataclass(slots=True, frozen=True)
class StreamRows:
    """One chunk of rows from :meth:`SQLConnector.execute_stream`. ``sent`` is the cumulative
    row count *after* this chunk — lets the consumer drive a progress indicator without doing
    its own running sum. The rows are post-processed (trim_strings, JDE-date) just like the
    non-streaming path."""
    rows: list[dict[str, Any]]
    sent: int


@dataclass(slots=True, frozen=True)
class StreamDone:
    """Final event from :meth:`SQLConnector.execute_stream`. ``total`` is the actual row count
    delivered (≤ cap); ``truncated`` is true iff the cap was hit before the query exhausted;
    ``duration_ms`` measures the full streaming span (query open → done). ``rowcount`` is
    always ``-1`` for SELECT — kept on the shape so the consumer can tell streaming SELECT
    apart from a hypothetical streaming write."""
    total: int
    truncated: bool
    duration_ms: float
    rowcount: int = -1


# Union type for the events yielded by :meth:`SQLConnector.execute_stream` — one Meta, then N
# Rows, then exactly one Done. Caller iterates via ``async for ev in conn.execute_stream(...)``
# and switches on type. Web layer renders these to NDJSON lines.
StreamEvent = StreamMeta | StreamRows | StreamDone


def flatten_query_metas(desc: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a sectioned SQL :meth:`SQLConnector.describe` dict back into the single flat
    query-meta list every pre-sections consumer expects: each table's present CRUD slots
    (already named ``<table>_<crud>``) in CRUD order, then custom ``queries``, then
    ``sequences``, then ``lookups``. SQL text is preserved — callers publishing over HTTP
    strip it via :func:`liberty.web.deps.public_query`."""
    out: list[dict[str, Any]] = []
    for tbl in desc.get("tables", []):
        out.extend(tbl.get("slots", []))
    for key in ("queries", "sequences", "lookups"):
        out.extend(desc.get(key, []))
    return out


class SQLConnector:
    """A connector exposing a fixed set of named queries against one pool."""

    # Hard ceiling on any row cap (a per-request override can't ask for more) — keeps a runaway
    # "give me everything" from OOM-ing the server.
    HARD_MAX_ROWS = 1_000_000
    DEFAULT_MAX_ROWS = 1000
    # Streaming defaults. ``DEFAULT_CHUNK_SIZE`` is what the route layer falls back to when the
    # caller doesn't supply ``?_chunk_size=N`` — 100 is the sweet spot between "first chunk
    # arrives fast" and "framing overhead is amortised". Clamped to ``[1, MAX_CHUNK_SIZE]`` by
    # ``execute_stream`` so a misconfigured caller can't ask for 10M-row chunks.
    DEFAULT_CHUNK_SIZE = 100
    MAX_CHUNK_SIZE = 5000

    def __init__(
        self,
        name: str,
        config: SqlConnectorConfig,
        pools: PoolRegistry,
        *,
        dictionary: DictionaryFile | None = None,
        pool_max_rows: int | None = None,
        default_language: str = "en",
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
        # App-wide fallback language — used when a request supplies no Accept-Language. Lives on
        # [app] in app.toml; passed through by ConnectorRegistry so it's a single source of truth.
        self._default_language = default_language
        # Unified name → QueryDef index across every section the new SqlConnectorConfig
        # exposes — tables (each present CRUD slot synthesised as ``<table_name>_<crud>``),
        # custom queries, sequences, lookups. Callers see one flat namespace and don't have
        # to know which section a query came from; the section IS the type. Duplicate names
        # raise ValueError at config-build time (caller logs + skips that connector).
        self._queries: dict[str, QueryDef] = dict(config.iter_named_queries())
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
        SQL variant; ``dialects`` lists which per-dialect variants the query carries.

        **Phase 3** — per-screen behaviour (columns / auto_load / audit_table / max_rows /
        key_columns / update_query / insert_query / delete_query) has moved off ``QueryDef``
        onto :class:`liberty.screens.config.Screen`. This output now carries only the SQL-layer
        bits: the statement, declared params, bind names, writable flag, dialects, and
        friendly labels. The frontend reads per-screen behaviour from
        ``GET /api/screens/{app}/{id}`` instead.

        **Sectioned shape** — the output mirrors :class:`SqlConnectorConfig`'s four
        sections: ``tables`` (each a ``{name, label, description, slots}`` where every
        present CRUD slot is a query-meta dict tagged with its ``crud``), plus flat
        ``queries`` (custom), ``sequences`` and ``lookups``. The synthesised slot name
        is ``<table_name>_<crud>``. :func:`liberty.web.deps.public_connector` flattens
        all four sections back into one ``queries`` list for the runtime TableView /
        screen-picker consumers, so they don't need to know about sections."""
        def _qmeta(q: QueryDef) -> dict[str, Any]:
            return {
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
                "bind_params": find_bind_params(q.default_sql),
                "sql": q.sql,
            }

        tables: list[dict[str, Any]] = []
        for tbl in self.config.tables:
            slots: list[dict[str, Any]] = []
            for crud, slot in tbl.slots():
                qname = f"{tbl.name}_{crud}"
                meta = _qmeta(_crud_slot_to_querydef(
                    qname, slot, label=tbl.label, description=tbl.description, crud=crud,
                ))
                meta["crud"] = crud
                slots.append(meta)
            tables.append({
                "name": tbl.name,
                "label": tbl.label,
                "description": tbl.description,
                "slots": slots,
            })

        return {
            "name": self.name,
            "type": "sql",
            "pool": self.pool_name,
            "show_in_switcher": self.config.show_in_switcher,
            "tables": tables,
            "queries": [_qmeta(q) for q in self.config.queries],
            "sequences": [_qmeta(q) for q in self.config.sequences],
            "lookups": [_qmeta(q) for q in self.config.lookups],
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
        self, sql_text: str, qdef: QueryDef, params: dict[str, Any] | None, *,
        stmt_type: str, column_hints: list[ColumnHint] | None = None,
        dict_scope: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """v1-style **runtime** filter wrap. Inspects *params* for values bound to columns
        flagged ``filter = true`` on *column_hints* (the screen's :class:`ColumnHint` list,
        threaded in by the route layer from the matching :class:`Screen` — Phase 3).
        Emits a wrap with a predicate per *active* column only — type-aware: text columns
        get the full operator family (contains / equals / notEquals / startsWith / endsWith),
        non-text columns get a typed equals.

        Each column's effective format is resolved the same way ``_apply_column_hints``
        and ``_column_meta_map`` do it: the column hint's own ``format`` wins, else the
        dictionary entry's (``col.dd`` or, when unset, ``col.name``). Without this, a
        column whose only source of format is the dictionary (the common case for
        ``extra_filter_cols`` hints that the migration emits from drill / nested-tab
        binds) would render as text and break on a numeric column —
        ``LOWER(integer)`` is undefined on Postgres.

        Only fires on SELECT — writes don't have filter columns to wrap. Returns
        *sql_text* unchanged when:
          - no column hints supplied (no screen, or screen has no columns), OR
          - the caller didn't bind a value for any filter-flagged column, OR
          - it's not a SELECT.

        ORDER BY at the top level is preserved across the wrap (the inner SELECT's
        ORDER BY would otherwise be lost — derived tables are unordered in standard SQL).

        **Returns** ``(wrapped_sql, params)``. The returned params are a *new* dict (the
        caller's input is never mutated) carrying the original keys plus any active-filter
        bind values coerced to the right Python type for the column's format — asyncpg's
        strict-type check rejects a string against an INTEGER-typed bind, so the wrap
        helper is the place to convert ``"10"`` → ``int(10)`` before binding. Non-active
        params and any other binds pass through untouched."""
        if stmt_type != "SELECT":
            return sql_text, params
        active: list[tuple[str, str | None, str, Any]] = []
        active_names: set[str] = set()
        for col in column_hints or []:
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
                entry = self._dict.find_entry(key, connector=dict_scope or self.name)
                if entry is not None and entry.format:
                    fmt = entry.format
            # Python-coerce the value for non-text columns. Text-y formats are left as
            # strings (the wrap's LOWER/LIKE want a string; ``_coerce_value(..., "text")``
            # is a no-op anyway). For number / date / boolean / jdedate the value flows
            # to asyncpg as a real Python int / date / bool — the SQL has no CAST so
            # asyncpg gets to send the column's native type code and the index applies.
            coerced = v if (fmt or "").lower() in _TEXT_FORMATS or not fmt else _coerce_value(v, fmt)
            active.append((col.name, fmt, op, coerced))
            active_names.add(col.name.upper())
        # Declared query params (``QueryDef.params``) used as OPTIONAL equality filters — the v1
        # lib_lkp lookup pattern, now applied by the framework so the lookup query stays a plain
        # ``SELECT col… FROM table``. A declared param filters its matching result column when
        # (a) it isn't already a ``:bind`` in the SQL (the author would handle that themselves) and
        # (b) a non-empty value was supplied. ``equals`` semantics (exact match), typed bind, and
        # trim-aware via the shared predicate builder — no CAST/wrap noise in the TOML.
        declared = [pd.name for pd in (getattr(qdef, "params", None) or [])]
        if declared:
            sql_binds = {b.upper() for b in find_bind_params(sql_text)}
            for name in declared:
                up = name.upper()
                if up in sql_binds or up in active_names:
                    continue
                v = (params or {}).get(name)
                if v is None or v == "":
                    continue
                entry = self._dict.find_entry(name, connector=dict_scope or self.name)
                fmt = entry.format if (entry is not None and entry.format) else None
                coerced = v if (fmt or "").lower() in _TEXT_FORMATS or not fmt else _coerce_value(v, fmt)
                active.append((name, fmt, "equals", coerced))
                active_names.add(up)
        if not active:
            return sql_text, params
        dialect = self._resolve_dialect()
        # Honour the pool's ``trim_strings`` on the filter's text columns too (JDE CHAR(N) is
        # blank-padded — without this a query that doesn't hand-TRIM its SELECT can't be filtered).
        trim_text = self._pools.trim_strings(self.pool_name)
        preds = [_build_filter_predicate(name, fmt, op, dialect, trim_text=trim_text) for name, fmt, op, _ in active]
        body, order_clause = _split_order_by(sql_text)
        where_clause = " AND ".join(f"({p})" for p in preds)
        out = f"SELECT * FROM (\n{body}\n) lib_flt\nWHERE 1=1 AND {where_clause}"
        if order_clause:
            out += f"\nORDER BY {order_clause}"
        # Merge the coerced active-filter values into a fresh params dict so the
        # downstream ``_build_params`` picks them up (it reads the dict by bind name).
        new_params: dict[str, Any] = dict(params or {})
        for name, fmt, _op, coerced in active:
            # Symmetric with the predicate's column side: when ``trim_strings`` wraps a text
            # column in ``TRIM()``, trim the *bind* too (both sides — Oracle TRIM is both-sided).
            # JDE codes are space-padded *and* sometimes right-justified, so a lookup value reads
            # back as ``'      01'``; without this it never equals ``TRIM(col) = '01'``. Text
            # columns only — numbers / dates aren't space-padded.
            if trim_text and isinstance(coerced, str) and ((fmt or "").lower() in _TEXT_FORMATS or not fmt):
                coerced = coerced.strip()
            new_params[name] = coerced
        return out, new_params

    def _column_meta_map(
        self, qdef: QueryDef, *, column_hints: list[ColumnHint] | None = None,
        dict_scope: str | None = None,
    ) -> dict[str, dict[str, str | None]]:
        """Build a column-name → ``{format, rule, rules_values, default}`` map for *qdef*.

        Column hints (Phase 3: threaded in by the route layer from the matching screen)
        are picked up first; for every other column name used as a bind in the SQL we fall
        back to ``find_entry(name)`` so write queries (``_post`` / ``_put`` / ``_delete``,
        which the migration emits *without* a ``columns`` block — the layout lives on the
        matching ``_get``) still get rule resolution + coercion. Keys are upper-cased: the
        migration's binds (``:USR_ID``) and the v1 dictionary's dd_ids both use uppercase;
        the resolved key matches the bind name regardless of how the surrounding SQL was cased.

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

        scope = dict_scope or self.name
        for col in (column_hints or []):
            key = (col.dd if col.dd else col.name) if col.dd != "" else None
            entry = self._dict.find_entry(key, connector=scope) if key else None
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
            entry = self._dict.find_entry(base, connector=scope)
            if entry is None:
                continue
            out[base] = _entry_meta(entry, None)
            seen.add(base)
        return out

    def _resolve_query_tokens(
        self, sql_text: str, qdef: QueryDef, *, user: str | None,
        column_hints: list[ColumnHint] | None = None, dict_scope: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Replace ``{{TOKEN}}`` placeholders in *sql_text* with bind parameters and return the
        rewritten SQL plus ``{bind_name: value}`` for those binds (merged into the execute binds
        AFTER form rules, so they're never overridden). For an ``COL = {{TOKEN}}`` assignment the
        value is coerced to the assigned column's DD format (``jdedate`` → CYYDDD, ``jdetime`` →
        HHMMSS, …); a bare token resolves to its natural Python type. Unknown tokens are left as-is
        (a typo surfaces as a SQL error rather than silently vanishing). No-op when the SQL carries
        no ``{{`` — so it's free for the overwhelming majority of queries."""
        if "{{" not in sql_text:
            return sql_text, {}
        meta = self._column_meta_map(qdef, column_hints=column_hints, dict_scope=dict_scope)
        binds: dict[str, Any] = {}
        counter = 0
        now_cell: list[datetime] = []

        def _value(token: str, fmt: str | None) -> Any:
            token = token.upper()
            if token in ("LOGIN", "USER"):
                # Server-trusted authenticated principal (uppercased, v1 convention).
                return (user or "anonymous").upper()
            if token == "PID":
                return self.name.upper()   # JDE program-id provenance → the connector name
            if token == "JOBN":
                return _JOBN_VALUE          # JDE work-station / job → "LIBERTY"
            # Temporal tokens — one ``now()`` per query so all stamps share an instant. Local
            # server time (JDE/Oracle SYSDATE semantics), same as the SYSDATE form rule.
            if not now_cell:
                now_cell.append(datetime.now())
            now = now_cell[0]
            # JDE tokens are always the Julian integer — never depend on a column-format lookup (the
            # audit column's physical name isn't in the dictionary).
            if token == "JDEDATE":
                return _coerce_value(now, "jdedate")   # CYYDDD int
            if token == "JDETIME":
                return _coerce_value(now, "jdetime")   # HHMMSS int
            if fmt:
                return _coerce_value(now, fmt)   # jdedate→CYYDDD, jdetime→HHMMSS, date→date, …
            if token == "SYSDATE":
                return now.date()
            if token == "SYSTIME":
                return now.time().replace(microsecond=0)
            return now                            # SYSTIMESTAMP / NOW

        def _next_bind() -> str:
            nonlocal counter
            counter += 1
            return f"__tok_{counter}"

        def _assign_sub(m: "re.Match[str]") -> str:
            col, tok = m.group(1), m.group(2)
            if tok.upper() not in _PREDEFINED_TOKENS:
                return m.group(0)
            colname = col.split(".")[-1].strip('"`[]').upper()
            fmt = (meta.get(colname) or {}).get("fmt")
            name = _next_bind()
            binds[name] = _value(tok, fmt)
            return f"{col} = :{name}"

        def _bare_sub(m: "re.Match[str]") -> str:
            tok = m.group(1)
            if tok.upper() not in _PREDEFINED_TOKENS:
                return m.group(0)
            name = _next_bind()
            binds[name] = _value(tok, None)
            return f":{name}"

        # Assignment form first (so the column's format drives coercion), then any bare tokens.
        out = _ASSIGN_TOKEN_RE.sub(_assign_sub, sql_text)
        out = _TOKEN_RE.sub(_bare_sub, out)
        return out, binds

    def _apply_form_rules(
        self, bound: dict[str, Any], qdef: QueryDef, *, stmt_type: str, user: str | None,
        column_hints: list[ColumnHint] | None = None, dict_scope: str | None = None,
    ) -> dict[str, Any]:
        """Resolve form-layer rules + coerce types on the bound params, **synchronously**.

        Steps, in order:

        1. **LOGIN** → stamp the caller's username (``"anonymous"`` when unauthenticated).
        2. **SYSDATE / CURRENT_DATE** → stamp ``datetime.now()`` in **local server time**
           (one value per call so every audit column in the same write lands on the same
           instant; local because JDE/Oracle SYSDATE is local — UTC would write the wrong
           day/time near midnight on a non-UTC server).
        3. **PASSWORD** → Argon2-hash a non-empty value; blank/missing pass through as NULL
           (the dialog already strips blank password fields from the submit body for UPDATE
           — keeping the existing hash; INSERT with an empty password lands as NULL).
        4. **BOOLEAN** → bind ← ``false_value`` when null/empty (the dialog usually sends the
           proper value on uncheck, but the migration / batch-edit grid may not yet — this is
           the safety net for the Y/N case where the DB doesn't accept NULL). ``true_value``
           passes through; only the empty/null side is substituted.
        5. **DEFAULT** → on any SET-clause write (INSERT/UPDATE/MERGE), when the bind is
           missing/empty, use the entry's ``default`` value. This lets audit constants
           (PID/JOBN) re-stamp on UPDATE; editable defaults are untouched because the form
           sends their non-empty current value.
        6. **Type coercion** → strings to the matching Python type for ``format`` ∈
           {integer/number/decimal/currency/date/datetime/timestamp/boolean/jdedate/jdetime}.

        ``SEQUENCE`` / ``NN`` is handled separately by :meth:`_resolve_sequences` because it
        needs a DB connection (and must run inside the same write transaction).

        ``_ORIGINAL``-suffixed binds (the migration's ``_put`` WHERE-clause re-bindings) **skip
        the form-rule step** (the WHERE wants the row's pre-edit values, untouched) but **do
        get type coercion** (Postgres still won't compare ``'10' = INTEGER 10``).
        """
        if stmt_type not in WRITE_STATEMENTS:
            return bound
        meta = self._column_meta_map(qdef, column_hints=column_hints, dict_scope=dict_scope)
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
            if not is_original and stmt_type in _SET_CLAUSE_STATEMENTS and rule not in _RULES_DISABLED:
                if rule in _RULES_LOGIN:
                    # LOGIN is the server-trusted authenticated principal, NOT a data value — it
                    # overwrites any caller-supplied value (anti-spoof: a client must not be able to
                    # set the audit username). v1 stored these uppercase ("ADMIN" not "admin") —
                    # match that, the audit tables + downstream reporting rely on it.
                    out[k] = (user or "anonymous").upper()
                    continue
                if rule in _RULES_NOW and (v is None or v == ""):
                    # SYSDATE / CURRENT_DATE is a DATA value (an audit timestamp OR a defaulted
                    # business date) — fill ONLY when the caller didn't pass one, so a passed value
                    # always wins (an editable date the operator set, e.g. F95921.RLEFFDATE in a
                    # date picker). Audit columns aren't dialog fields, so the form never sends them
                    # (the bind is absent) — they still re-stamp on every write. Same "fill-if-absent"
                    # contract as BOOLEAN / DEFAULT below. (LOGIN above stays forced — identity, not
                    # data.) A passed value falls through to type coercion.
                    #
                    # One ``now()`` per call, so every audit column in the same write lands on the
                    # same instant. Coerce to the matching Python type for the column's format (date /
                    # datetime / jdedate / jdetime) so the driver picks the right SQL type. **Local
                    # server time**, not UTC: "today" means the server's day — JDE (like Oracle's
                    # SYSDATE) stamps local time, and a UTC ``now`` would write the wrong CYYDDD date /
                    # HHMMSS time for the hours either side of midnight when the server isn't on UTC.
                    if now is None:
                        now = datetime.now()
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
                # DEFAULT — on any SET-clause write (INSERT/UPDATE/MERGE), only when the caller
                # didn't supply a value. This is what makes audit constants (e.g. PID="LIBERTY",
                # JOBN) re-stamp on UPDATE the way LOGIN/SYSDATE already do — a blank bind on the
                # update means the form didn't carry the audit value, so the DD default fills it.
                # Editable defaults (FSTP="Y", OUTQ="QPRINT", …) are unaffected: the dialog sends
                # their non-empty current value on update, so this only fires if one is cleared to
                # blank — in which case the configured default is the right fallback anyway.
                if (
                    stmt_type in _RULES_DEFAULTABLE and (v is None or v == "")
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

    def _apply_write_when(
        self, bound: dict[str, Any], qdef: QueryDef, *, stmt_type: str,
        column_hints: list[ColumnHint] | None = None, dict_scope: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Neutralise columns whose ``write_when`` does not hold for this row — write the
        type-neutral value (blank / 0) instead of whatever the data-dictionary default left, so a
        field irrelevant to the row's kind isn't dirtied by an inherited default. Opt-in: a column
        with no ``write_when`` is untouched (zero regression for existing screens). Runs AFTER the
        form rules so it OVERRIDES any DD default that fired. Only SET-clause statements write
        columns — a DELETE's binds are WHERE keys, left alone.

        The discriminator value is read from the bound (written) value first, else *context* (the
        full submitted params — a derived selector like ``SEC_TYPE`` may be submitted but not itself
        a written column)."""
        if stmt_type not in _SET_CLAUSE_STATEMENTS:
            return bound
        hints = column_hints or []
        if not any(h.write_when_rules for h in hints):
            return bound
        meta = self._column_meta_map(qdef, column_hints=column_hints, dict_scope=dict_scope)
        ctx_upper = {str(k).upper(): v for k, v in (context or {}).items()}

        def _field_value(field: str) -> Any:
            fu = field.upper()
            return bound[fu] if fu in bound else ctx_upper.get(fu)

        out = dict(bound)
        for h in hints:
            rules = h.write_when_rules
            if not rules:
                continue
            col = h.name.upper()
            if col not in out:
                continue  # not a written column on this query
            if _write_when_holds(rules, _field_value):
                continue  # applicable → keep the bound value (and its DD default) as-is
            out[col] = _neutral_for_format((meta.get(col) or {}).get("fmt"))
        return out

    async def _resolve_sequences(
        self, conn: Any, bound: dict[str, Any], qdef: QueryDef, *, stmt_type: str, language: str,
        column_hints: list[ColumnHint] | None = None, dict_scope: str | None = None,
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
        meta = self._column_meta_map(qdef, column_hints=column_hints, dict_scope=dict_scope)
        # ``dd_by_col``: column name (upper) → dd id (upper). Drives the dd_id-keyed sequence
        # lookup — a column with a sequence registered under its dd auto-fires at INSERT time
        # even when the dd's own rule is something else (e.g. APPS_ID is a LOOKUP target *and*
        # gets an auto-ID from ``[sequences.get_apps_id_from_…]``).
        dd_by_col: dict[str, str] = {}
        for col in (column_hints or []):
            ddk = (col.dd if col.dd else col.name) if col.dd != "" else ""
            if ddk:
                dd_by_col[col.name.upper()] = ddk.upper()
        scope = dict_scope or self.name
        out = dict(bound)
        for k, v in list(out.items()):
            if k.upper().endswith("_ORIGINAL"):
                continue
            # "An explicit value from the caller wins — never overwrite." But a SEQUENCE column is
            # numeric and JDE has no NULLs, so the form posts an unassigned key as ``0`` (e.g.
            # AUSEQNO=0), not NULL/"". A sequence number is never legitimately 0 (they start at 1),
            # so treat 0 / "0" as UNASSIGNED here too — otherwise the posted 0 blocks the sequence
            # from firing and the row lands with seq 0. Only a real, non-zero value short-circuits.
            zero_numeric = isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0
            zero_string = isinstance(v, str) and v.strip() == "0"
            if v not in (None, "") and not zero_numeric and not zero_string:
                continue

            # Two lookup paths to find a sequence for this bind:
            #   1. via the dd_id (v1 ``ly_sequence.seq_dd_id`` — fires for any column whose dd
            #      a sequence targets, regardless of the dd's own rule).
            #   2. via the dictionary entry's ``rules = "SEQUENCE"`` / ``"NN"`` → ``rules_values``
            #      (the sequence id, looked up via :meth:`find_sequence`).
            seq_def: SequenceDef | None = None
            dd = dd_by_col.get(k.upper(), k.upper())
            seq_def = self._dict.find_sequence_by_dd_id(dd, connector=scope)
            seq_query_name: str | None = seq_def.query if seq_def is not None else None
            if seq_def is None:
                m = meta.get(k.upper())
                if m is None or m["rule"] not in _RULES_SEQUENCE:
                    continue
                seq_ref = m["rules_values"]
                if not seq_ref:
                    _log.warning(
                        "%s.%s: SEQUENCE rule on column %s has no rules_values — bind left NULL",
                        self.name, qdef.name, k,
                    )
                    continue
                # Sequence id → SequenceDef → query name. Falls back to "treat rules_values as
                # the query name" for legacy / hand-edited dictionaries.
                seq_ref_str = str(seq_ref).strip()
                seq_def = self._dict.find_sequence(seq_ref_str, connector=scope)
                seq_query_name = seq_def.query if seq_def is not None else seq_ref_str
            seq_qdef = self._queries.get(seq_query_name or "")
            if seq_qdef is None:
                _log.warning(
                    "%s.%s: SEQUENCE on column %s references unknown query %r — bind left NULL",
                    self.name, qdef.name, k, seq_query_name,
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
                # Take the first column of the first row — sequence queries return the
                # *next* number directly (``SELECT COALESCE(MAX(col), 0) + 1 ...``). The
                # migration rewrites v1's ``MAX(col)``-only pattern to include ``+ 1`` so
                # operator-written and migrated queries follow one rule.
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

    def _row_cap(self, qdef: QueryDef, override: int | None, screen_max_rows: int | None = None) -> int:
        """Effective row cap for one SELECT: per-request *override* > per-screen *screen_max_rows*
        > this connector's (which already folds in the pool's) > 1000 fallback — clamped to
        ``[1, HARD_MAX_ROWS]``. Phase 3 — the per-query cap moved to ``Screen.max_rows``;
        ``qdef`` is kept for signature symmetry."""
        _ = qdef
        cap = override if override is not None else (screen_max_rows if screen_max_rows is not None else self.max_rows)
        return max(1, min(int(cap), self.HARD_MAX_ROWS))

    def _debug_log_sql(self, query_name: str, stmt_type: str, sql_text: str, bound: dict[str, Any]) -> None:
        """When the pool's ``debug_sql`` flag is on, log the statement *as it reaches the driver*:
        the resolved SQL (schema placeholders + filter wrap applied) and the final bound params
        (post trim / coalesce / pad / sequence). Logged at WARNING so it surfaces on the console
        even under ``log_level = "warning"`` — the flag is opt-in per pool, so this is intentional,
        not noise. The flag check is cheap; building the message only happens when it's on."""
        if not self._pools.debug_sql(self.pool_name):
            return
        _log.warning(
            "[debug_sql] %s.%s (%s) on pool %r\n  SQL: %s\n  binds: %r",
            self.name, query_name, stmt_type, self.pool_name, sql_text, bound,
        )

    async def execute(
        self, query_name: str, params: dict[str, Any] | None = None, *, language: str | None = None,
        max_rows: int | None = None, user: str | None = None,
        column_hints: list[ColumnHint] | None = None, audit_table: str | None = None,
        screen_max_rows: int | None = None, dictionary_scope: str | None = None,
    ) -> QueryResult:
        """Run *query_name* with *params*; raises on bad input, returns on success.

        Phase 3 — per-screen behaviour (column hints / audit table / row cap) is threaded in
        by the route layer from the matching :class:`Screen` rather than read off ``QueryDef``:

          * ``column_hints`` — the screen's ``ColumnHint`` list, drives result-column display
            (label/format/rule/filter/etc.), the runtime filter wrap, and the rule machinery
            for write-side coercion / SEQUENCE resolution.
          * ``audit_table`` — when non-empty, mirror successful writes into the named audit
            table in the same transaction (v1's AUD_<table> pattern).
          * ``screen_max_rows`` — per-screen SELECT row cap; the *max_rows* kwarg (per-request
            override) still wins.

        The SQL variant matching the pool's database is selected (``QueryDef.sql_for``).
        *language* drives dictionary resolution (default: the dictionary's ``default_language``).
        *user* is the caller's username — recorded on the audit row when ``audit_table`` is set;
        defaults to ``"anonymous"`` for unauthenticated paths. Raises :class:`QueryNotFoundError`,
        :class:`StatementNotAllowedError`, :class:`WriteNotAllowedError`, or
        :class:`UnknownPoolError`; database errors propagate as the underlying SQLAlchemy exception.
        """
        lang = language or self._default_language
        # For a cross-pool screen (its connector ≠ its app), the dictionary entries live under
        # the *app's* scope in dictionary.toml — not this SQL connector's scope. The route
        # layer threads it in via ``dictionary_scope`` when the query is run through a screen;
        # otherwise we fall back to the connector's own name (its own dictionary section, if any).
        dict_scope = dictionary_scope or self.name
        qdef = self.get_query(query_name)
        cap = self._row_cap(qdef, max_rows, screen_max_rows)
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
        sql_text, params = self._apply_filter_wrap(
            sql_text, qdef, params, stmt_type=stmt_type, column_hints=column_hints,
            dict_scope=dict_scope,
        )
        # Predefined ``{{TOKEN}}`` placeholders (LOGIN / SYSDATE / SYSTIME / …) → bind params,
        # format-coerced to the assigned column. Runs after the filter wrap so a wrapped SELECT is
        # covered too; the resolved binds are merged AFTER form rules so they're never overridden.
        sql_text, token_binds = self._resolve_query_tokens(
            sql_text, qdef, user=user, column_hints=column_hints, dict_scope=dict_scope,
        )
        bound = self._build_params(sql_text, qdef, params)
        # Resolve form-layer rules (LOGIN / SYSDATE / PASSWORD / DEFAULT) + coerce string
        # binds to the matching Python type for the column's resolved format. SEQUENCE / NN
        # is deferred to inside the write transaction (it needs the DB). On SELECT this is
        # a no-op for the filter binds (they're already typed via the runtime CAST in the
        # wrap, and SYSDATE/etc. only fire on writes anyway).
        bound = self._apply_form_rules(
            bound, qdef, stmt_type=stmt_type, user=user, column_hints=column_hints,
            dict_scope=dict_scope,
        )
        # write_when: a column not applicable to this row's kind is written as its neutral value
        # (blank / 0), overriding any DD default the form rules just applied. ``params`` is the full
        # submitted set, so a derived discriminator (e.g. SEC_TYPE) that isn't itself a written
        # column is still available to evaluate the condition.
        bound = self._apply_write_when(
            bound, qdef, stmt_type=stmt_type, column_hints=column_hints,
            dict_scope=dict_scope, context=params,
        )
        bound.update(token_binds)  # server-resolved tokens win, untouched by form rules
        stmt = text(sql_text)
        is_select = stmt_type == "SELECT"

        started = time.perf_counter()
        if is_select:
            self._debug_log_sql(query_name, stmt_type, sql_text, bound)
            async with engine.connect() as conn:
                result = await conn.execute(stmt, bound)
                columns = _apply_column_hints(
                    _columns_from_result(result), column_hints or [], self._dict, lang, connector=dict_scope,
                )
                rows: list[dict[str, Any]] = []
                truncated = False
                for row in result.mappings():
                    if len(rows) >= cap:
                        truncated = True
                        break
                    # Decimal → JSON number (so numeric columns don't serialize as strings).
                    rows.append({k: _jsonable_number(v) for k, v in row.items()})
            # Oracle CHAR/NCHAR columns are space-padded to their declared width; the trailing
            # spaces leak into UI labels and form fields and make editing painful (cursor lands
            # after the padding, search/sort sees "Test    " ≠ "Test"). v1 stripped automatically;
            # v2 does the same when the pool flag is set (auto-on for Oracle, see ``PoolRegistry.
            # trim_strings``). The trim only touches strings — numbers, dates, bytes pass through.
            #
            # ``rstrip`` only (NOT ``strip``): a right-justified JDE code (KY in F0005, MCU
            # everywhere) is *left*-padded, and a live read is an edit-then-write-back round trip —
            # stripping the leading padding would make ``_pad_char_binds`` (which only right-pads)
            # restore the wrong justification on save. Leading padding is preserved here and trimmed
            # *only* where it's safe: the filter bind, for a comparison that's never written back
            # (see ``_apply_filter_wrap``). The nomaflow copy path can strip both sides because its
            # target is a clean Postgres replica, never written back to JDE (see coercion.py's
            # ``strip_both_columns``).
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
            # JDE HHMMSS times → "HH:MM:SS" strings on read — symmetric with jdedate above.
            _jdetime_cols = [c.name for c in columns if (c.format or "").lower() == "jdetime"]
            if _jdetime_cols:
                for row in rows:
                    for cn in _jdetime_cols:
                        if cn in row:
                            row[cn] = _from_jde_time(row[cn])
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

        # Oracle write-time bind hygiene — both transformers consult the same Oracle column
        # metadata, so the introspection runs once when either pool flag is on:
        #
        #   * ``coalesce_nulls`` → :func:`_coalesce_oracle_nulls` replaces empty string binds
        #     with " " for CHAR-family columns and 0 for NUMBER columns. Fixes ORA-01400 on
        #     INSERTs / UPDATEs against NOT-NULL Oracle string columns (JDE F-tables typically).
        #   * ``trim_strings`` → :func:`_pad_char_binds` right-pads non-empty string binds to
        #     the declared CHAR(N) width for fixed-width CHAR / NCHAR columns. The symmetric
        #     partner of the reads-side trim — without it, a trimmed bind doesn't match the
        #     space-padded stored value on a DELETE / UPDATE WHERE clause, the SQL returns
        #     200 OK with 0 rows affected, and the operator is left wondering why their delete
        #     "worked" but the row is still there. See [[project-jde-pool-settings]].
        #
        # The introspection REQUIRES both ``owner`` (schema) AND ``table``. With ``owner=None``
        # the ``all_tab_columns`` query would match the same table across every schema the
        # connection user can see (JDE: PRODDTA / DEMODTA / TESTDTA …) and pollute the cache
        # with mixed column metadata. We skip + warn instead so the operator notices the SQL
        # needs a ``#SCHEMA.<NAME>#`` placeholder.
        wants_coalesce = self._pools.coalesce_nulls(self.pool_name)
        wants_pad = self._pools.trim_strings(self.pool_name)
        if wants_coalesce or wants_pad:
            target = _oracle_target_table(sql_text)
            if target is None:
                col_types: dict[str, dict[str, Any]] = {}
            else:
                owner, table = target
                if owner is None:
                    _log.warning(
                        "oracle bind hygiene skipped for table %r on pool %r: no schema in SQL — "
                        "use ``#SCHEMA.<NAME>#`` to qualify it, otherwise ``all_tab_columns`` "
                        "returns the same table from every schema in scope.",
                        table, self.pool_name,
                    )
                    col_types = {}
                else:
                    key = (self.pool_name, owner, table)
                    col_types = _ORACLE_COL_TYPES_CACHE.get(key) or {}
                    if not col_types:
                        try:
                            introspect_sql = (
                                "SELECT column_name, data_type, char_length "
                                "FROM all_tab_columns "
                                "WHERE owner = :o AND table_name = :t"
                            )
                            async with engine.connect() as introspect_conn:
                                r = await introspect_conn.execute(
                                    text(introspect_sql), {"o": owner, "t": table},
                                )
                                col_types = {}
                                for row in r.mappings():
                                    cname = str(row.get("column_name") or "").upper()
                                    dtype = str(row.get("data_type") or "").upper()
                                    raw_len = row.get("char_length")
                                    try:
                                        clen: int | None = int(raw_len) if raw_len is not None else None
                                    except (TypeError, ValueError):
                                        clen = None
                                    if not cname:
                                        continue
                                    if dtype in _ORACLE_FIXED_CHAR_TYPES:
                                        col_types[cname] = {"kind": "char_fixed", "length": clen}
                                    elif dtype in _ORACLE_VAR_CHAR_TYPES:
                                        col_types[cname] = {"kind": "char", "length": None}
                                    elif dtype in _ORACLE_NUMBER_TYPES:
                                        col_types[cname] = {"kind": "number", "length": None}
                                    else:
                                        col_types[cname] = {"kind": "other", "length": None}
                                _ORACLE_COL_TYPES_CACHE[key] = col_types
                        except Exception as e:
                            _log.warning(
                                "oracle column introspection failed for %s.%s on pool %r: %s — "
                                "writes proceed without bind hygiene",
                                owner, table, self.pool_name, e,
                            )
                            col_types = {}
            if wants_coalesce:
                bound = _coalesce_oracle_nulls(bound, col_types)
            if wants_pad:
                # ``where_col_map`` lets a WHERE/JOIN bind whose NAME differs from the column it's
                # compared to (``RLTOROLE = :FROMUSER``) still pad to that CHAR column's width.
                bound = _pad_char_binds(bound, col_types, where_col_map=_where_bind_columns(sql_text))

        async with engine.begin() as conn:
            # SEQUENCE / NN — run the named "next number" query in the *same* transaction as
            # the INSERT so a concurrent insert can't grab the same value (the surrounding
            # ``engine.begin()`` block serialises). A missing/failing sequence logs and falls
            # through with NULL (the DB will then reject the row if the column is NOT NULL).
            pre_seq = dict(bound)
            bound = await self._resolve_sequences(
                conn, bound, qdef, stmt_type=stmt_type, language=lang, column_hints=column_hints,
                dict_scope=dict_scope,
            )
            # The binds the sequence machinery just filled (empty → value) — these are the
            # freshly-assigned auto-IDs (typically the PK). Surfaced on the result so a caller
            # can bind a dependent child insert to the parent's new key in the same Save.
            seq_assigned = {
                k.upper(): v for k, v in bound.items()
                if pre_seq.get(k) in (None, "") and v not in (None, "")
                and not k.upper().endswith("_ORIGINAL")
            }
            self._debug_log_sql(query_name, stmt_type, sql_text, bound)
            result = await conn.execute(stmt, bound)
            rowcount = result.rowcount
            # 0-row write watch — a successful SQL execute that didn't touch any row is almost
            # always a config bug worth surfacing: a DELETE/UPDATE whose WHERE doesn't match
            # the stored value (CHAR vs VARCHAR2 padding, case mismatch), an INSERT that
            # silently no-ops because the target table is partitioned away, an action chain
            # pointing at the wrong query variant (``_put`` instead of ``_post``), etc.
            # Logged as a WARN so it shows up in the server console without making the call
            # itself fail (some writes legitimately affect 0 rows — e.g. an idempotent
            # cleanup that runs whether the row exists or not).
            if stmt_type in WRITE_STATEMENTS and rowcount == 0:
                _log.warning(
                    "%s.%s: %s affected 0 rows — check the WHERE clause (CHAR padding, "
                    "case, missing bind) or the target query variant. SQL=%r bound=%r",
                    self.name, query_name, stmt_type, sql_text, bound,
                )
            # AUD audit (v1's tbl_audit = 'Y' → migrated as Screen.audit_table = "AUD_<table>",
            # threaded in by the route layer). The mirror INSERT runs in the *same* transaction
            # so a successful write + failing audit rolls back together — a missing/misshapen
            # AUD table is loud, not silently dropped. Pass the *coerced/resolved* bound params
            # (post LOGIN/SYSDATE/PASSWORD/SEQUENCE) so the audit mirror matches what actually
            # hit the DB.
            if audit_table:
                await self._write_audit(
                    conn, qdef, stmt_type, bound, user, audit_table=audit_table, main_sql=sql_text,
                )
        duration_ms = (time.perf_counter() - started) * 1000.0
        return QueryResult(
            connector=self.name,
            query=query_name,
            statement_type=stmt_type,
            rowcount=rowcount,
            duration_ms=duration_ms,
            resolved_binds=seq_assigned,
            bound_params=bound,
        )

    async def execute_stream(
        self, query_name: str, params: dict[str, Any] | None = None, *, language: str | None = None,
        max_rows: int | None = None, user: str | None = None,
        column_hints: list[ColumnHint] | None = None, screen_max_rows: int | None = None,
        dictionary_scope: str | None = None, chunk_size: int = DEFAULT_CHUNK_SIZE,  # type: ignore[name-defined]
    ) -> AsyncIterator[StreamEvent]:
        """Run a **SELECT** query and yield :class:`StreamMeta`, then N :class:`StreamRows`,
        then exactly one :class:`StreamDone`. Server-side cursor under the hood via SQLAlchemy
        ``conn.stream()`` — rows trickle in as the DB advances rather than buffering the whole
        result first. Works on both asyncpg (Postgres) and oracledb-async (Oracle) — both
        report ``supports_server_side_cursors = True``; the chunk size maps to asyncpg's
        cursor fetch size + Oracle's ``arraysize`` respectively. SQLite (aiosqlite) effectively
        buffers but still produces the expected event sequence — fine for tests.

        Same prep as :meth:`execute` (filter wrap, form rules, schema placeholders, allow-list
        + writable gate). Streaming is **SELECT-only**: any other statement type raises
        :class:`StatementNotAllowedError` even if ``writable = true``. Writes don't fit the
        streaming protocol (no row stream + need transactional commit) — caller falls back to
        :meth:`execute` for those.

        Args:
          chunk_size: rows per :class:`StreamRows` event. Clamped to
            ``[1, MAX_CHUNK_SIZE]``; the default matches ``DEFAULT_CHUNK_SIZE`` (100, see the
            class constants). The route layer plumbs through a ``?_chunk_size=N`` query param.

        Yields events that the web layer renders to NDJSON lines (``application/x-ndjson``):

          * ``{"kind":"meta","columns":[…],"cap":N,"chunk_size":N}`` — once at the top.
          * ``{"kind":"rows","rows":[…],"sent":N}`` — repeatedly, ``len(rows) <= chunk_size``.
            ``sent`` is the cumulative count *after* this chunk so the consumer drives a
            progress indicator without its own running sum.
          * ``{"kind":"done","total":N,"truncated":bool,"duration_ms":…}`` — exactly once at
            the end. ``truncated = true`` iff the row cap was hit before the cursor exhausted.

        Errors propagate as exceptions; the route layer catches and emits a final
        ``{"kind":"error","detail":…}`` line so a half-streamed response still terminates
        cleanly (the chunked transfer is closed and the consumer's NDJSON parser sees the line).
        """
        # ── prep — mirrors execute() for SELECT (kept inline for now; refactoring the shared
        # prep into a helper would touch every call site and isn't worth the diff for this slice).
        lang = language or self._default_language
        dict_scope = dictionary_scope or self.name
        qdef = self.get_query(query_name)
        cap = self._row_cap(qdef, max_rows, screen_max_rows)
        chunk = max(1, min(int(chunk_size or self.DEFAULT_CHUNK_SIZE), self.MAX_CHUNK_SIZE))
        sql_text = _apply_schema_placeholders(
            qdef.sql_for(self._resolve_dialect()), self._pools.schemas(self.pool_name),
            connector=self.name, query=query_name, pool=self.pool_name,
        )
        stmt_type = detect_statement_type(sql_text)
        if stmt_type != "SELECT":
            # Streaming is SELECT-only — writes don't fit the protocol (need a single
            # transactional commit; no row stream). Caller falls back to :meth:`execute`.
            raise StatementNotAllowedError(
                f"{self.name}.{query_name}: execute_stream only supports SELECT, got "
                f"{stmt_type or '(unknown)'!r}."
            )
        sql_text, params = self._apply_filter_wrap(
            sql_text, qdef, params, stmt_type=stmt_type, column_hints=column_hints,
            dict_scope=dict_scope,
        )
        bound = self._build_params(sql_text, qdef, params)
        bound = self._apply_form_rules(
            bound, qdef, stmt_type=stmt_type, user=user, column_hints=column_hints,
            dict_scope=dict_scope,
        )
        _ = lang  # lang used only by columns hint resolution + audit (none here on SELECT)
        stmt = text(sql_text)
        engine: AsyncEngine = self._pools.engine(self.pool_name)
        _trim = self._pools.trim_strings(self.pool_name)
        self._debug_log_sql(query_name, stmt_type, sql_text, bound)
        started = time.perf_counter()

        # Server-side cursor via conn.stream(). The ``stream_results=True`` execution option
        # tells SQLAlchemy "don't pre-buffer"; asyncpg honours this via a Postgres cursor,
        # oracledb-async via REF cursor + arraysize. The fetch batch size we ask for matches
        # the chunk size so each cursor.fetchmany() roughly corresponds to one chunk we
        # forward to the client.
        async with engine.connect() as conn:
            # ``execution_options`` rides through `stream()`'s kwarg — SQLAlchemy's async
            # `Connection.execution_options()` is itself a coroutine, awkward to chain.
            # ``stream_results=True`` enables the server-side cursor; ``max_row_buffer``
            # caps the per-fetch batch (asyncpg cursor fetch size + oracledb arraysize).
            result = await conn.stream(
                stmt, bound,
                execution_options={"stream_results": True, "max_row_buffer": chunk},
            )
            columns = _apply_column_hints(
                _columns_from_result(result), column_hints or [], self._dict, lang, connector=dict_scope,
            )
            # JDE Julian date columns — same case-insensitive convention as execute().
            _jdedate_cols = {c.name for c in columns if (c.format or "").lower() == "jdedate"}
            _jdetime_cols = {c.name for c in columns if (c.format or "").lower() == "jdetime"}
            yield StreamMeta(columns=columns, cap=cap, chunk_size=chunk)

            sent = 0
            truncated = False
            async for partition in result.mappings().partitions(chunk):
                if sent >= cap:
                    truncated = True
                    break
                # Build the outgoing chunk row by row — coerce each row + apply trim_strings +
                # JDE date conversion inline so the consumer sees fully-post-processed rows
                # (same shape as the QueryResult.rows on the non-streaming path).
                out: list[dict[str, Any]] = []
                for raw in partition:
                    if sent + len(out) >= cap:
                        truncated = True
                        break
                    row = {k: _jsonable_number(v) for k, v in raw.items()}   # Decimal → JSON number
                    if _trim:
                        for k, v in row.items():
                            if isinstance(v, str):
                                row[k] = v.rstrip()   # trailing only — preserve left-padding for write-back (see execute())
                    if _jdedate_cols:
                        for cn in _jdedate_cols:
                            if cn in row:
                                row[cn] = _from_jde_julian(row[cn])
                    if _jdetime_cols:
                        for cn in _jdetime_cols:
                            if cn in row:
                                row[cn] = _from_jde_time(row[cn])
                    out.append(row)
                if out:
                    sent += len(out)
                    yield StreamRows(rows=out, sent=sent)
                if truncated:
                    break

        duration_ms = (time.perf_counter() - started) * 1000.0
        yield StreamDone(total=sent, truncated=truncated, duration_ms=duration_ms)

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
            self._debug_log_sql("<test-run>", stmt_type, sql_text, bound)
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
                    # Decimal → JSON number (so numeric columns don't serialize as strings).
                    rows.append({k: _jsonable_number(v) for k, v in row.items()})
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
        self._debug_log_sql("<test-run>", stmt_type, sql_text, bound)
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
        *, audit_table: str, main_sql: str | None = None,
    ) -> None:
        """Mirror a writable execute into *audit_table* (v1's AUD_<table> pattern). Phase 3 —
        the audit table name is threaded in by the route layer from the matching
        :class:`Screen.audit_table` (was ``QueryDef.audit`` pre-Phase-3). Logs the bound
        row's *uppercase* params (the v1 convention — the migrated _put/_post/_delete SQLs
        bind columns as ``:USR_ID`` etc.) and three audit columns:

        * ``AUD_ACTION`` — the statement type (``INSERT`` / ``UPDATE`` / ``DELETE``)
        * ``AUD_USER`` — the caller's username (or ``"anonymous"`` if the call wasn't authenticated)
        * ``AUD_DATE`` — local server-time timestamp captured server-side

        Columns are taken from ``params`` (uppercase keys, not ending in ``_ORIGINAL`` — those are
        only WHERE rebinds for the main UPDATE). The AUD table is auto-created from the source
        table's schema on first write (v1 parity — operators never ran DDL to set up audit, the
        framework did it lazily) via :meth:`_ensure_audit_table`; *main_sql* identifies the
        source table for that step."""
        cols: dict[str, Any] = {}
        for k, v in (params or {}).items():
            if not k or not k.isupper() or k.endswith("_ORIGINAL"):
                continue
            cols.setdefault(k, v)
        if not cols and stmt_type != "DELETE":
            # nothing to audit — leave a footprint so an operator who expected audit knows why nothing landed
            _log.warning(
                "audit: %s.%s — no uppercase params to log into %s; skipping the audit row",
                self.name, qdef.name, audit_table,
            )
            return
        # v1 parity: lazily create AUD_<table> from the source table's schema if it doesn't
        # exist yet. Runs in the same write transaction as the main statement so a failure
        # rolls everything back together.
        await self._ensure_audit_table(conn, audit_table, main_sql)
        # Build `INSERT INTO <audit> (col1, col2, …, AUD_ACTION, AUD_USER, AUD_DATE)
        #                    VALUES (:col1, :col2, …, :_AUD_ACTION, :_AUD_USER, :_AUD_DATE)`
        # Reserved bind names start with `_aud_` so they can't collide with the row's columns.
        col_list = list(cols)
        # ``_aud_date`` is naive **local** server time: the audit column is ``TIMESTAMP``
        # (without time zone) — matches the auto-create DDL above and what v1's AUD_<table>s
        # used. ``datetime.now()`` is naive (no tzinfo) so it binds cleanly against the
        # tz-naive column (a tz-aware ``now`` trips asyncpg with "can't subtract offset-naive
        # and offset-aware datetimes" and rolls the whole audited write back — auto-create +
        # main + audit are one transaction). Local, not UTC, so it agrees with the SYSDATE
        # rule in ``_apply_form_rules`` and reads consistently next to UPMJ/UPMT in the DB.
        bind_params = {
            **cols,
            "_aud_action": stmt_type,
            "_aud_user": user or "anonymous",
            "_aud_date": datetime.now(),
        }
        col_sql = ", ".join([*col_list, "AUD_ACTION", "AUD_USER", "AUD_DATE"])
        val_sql = ", ".join([f":{c}" for c in col_list] + [":_aud_action", ":_aud_user", ":_aud_date"])
        audit_sql = f"INSERT INTO {audit_table} ({col_sql}) VALUES ({val_sql})"
        await conn.execute(text(audit_sql), bind_params)


def _resolve_conditional_rule(
    rules: str | None, rules_values: str | None, dictionary: DictionaryFile, *,
    connector: str | None, language: str | None,
) -> dict[str, Any] | None:
    """Resolve a ``rules_when`` entry's (rules, rules_values) into a DisplayRule the same way the
    base rule resolves — a synthetic dictionary entry → :meth:`resolve_rule`. Blank ``rules`` → None
    (a plain input, no widget). Carries no entry-level ``lookup_params`` (the conditional lookup is a
    DIFFERENT lookup than the column's DD entry; its dynamic params come from ``lookup_param_binds``)."""
    r = rules.strip().upper() if rules else None
    if not r:
        return None
    synth = DictionaryEntry(rules=r, rules_values=(rules_values.strip() if rules_values else None))
    return dictionary.resolve_rule(synth, connector=connector, language=language)


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
    entry = dictionary.find_entry(h.dictionary_key, connector=connector) if h.dictionary_key else None
    if entry is not None:
        if label is None:
            label = entry.label_for(language)
        if fmt is None:
            fmt = entry.format
    # The hint's OWN ``rules`` override wins over the dictionary entry's rule — e.g. APPS_ID is a
    # global LOOKUP in the dictionary but THIS screen overrides it to SEQUENCE (it creates the row).
    # Without honouring the override the grid kept rendering the LOOKUP. Build a synthetic entry so
    # the override resolves (SEQUENCE / NN resolve to no display rule → the grid shows the raw value).
    hint_rules = h.rules.strip().upper() if h.rules else None
    if hint_rules:
        synth = DictionaryEntry(
            label=(entry.label if entry else None),
            format=fmt or (entry.format if entry else None),
            rules=hint_rules,
            rules_values=(h.rules_values.strip() if h.rules_values else None),
            lookup_params=(entry.lookup_params if entry else {}),
            false_value=(entry.false_value if entry else None),
            default=(entry.default if entry else None),
        )
        rule = dictionary.resolve_rule(synth, connector=connector, language=language)
    elif entry is not None:
        rule = dictionary.resolve_rule(entry, connector=connector, language=language)
    return Column(
        name=name or h.name, type=type_, label=label, hidden=h.hidden, key=h.key, filter=h.filter,
        disable_on_add=h.disable_on_add, disable_on_edit=h.disable_on_edit,
        filter_from=[{"source": d.source, "column": d.column} for d in h.filter_from],
        visible_when=[r.as_dict() for r in h.visible_when_rules],
        return_binds=[{"param": b.param, "column": b.column} for b in h.return_binds],
        default_when=[w.as_dict() for w in h.default_when],
        hide_label=h.hide_label,
        rules_when=[
            {"field": w.field, "value": w.value,
             "rule": _resolve_conditional_rule(w.rules, w.rules_values, dictionary, connector=connector, language=language),
             # Per-rule binds (this rule's lookup gets these — the grid binds them per distinct
             # value when fetching, so a parameterized conditional lookup resolves per row).
             "lookup_param_binds": [b.model_dump(mode="json", exclude_none=True) for b in w.lookup_param_binds],
             "return_binds": [{"param": b.param, "column": b.column} for b in w.return_binds]}
            for w in h.rules_when
        ],
        width=h.width, align=h.align, format=fmt, rule=rule,
        # Only surface `dd` when it's *explicitly* set on the hint (a non-empty override). For
        # `dd = None` (default — dictionary lookup happens by column name) or `dd = ""` (operator
        # opted out of the dictionary), don't pollute the wire. Phase-8 dashboard filters key on
        # this to cross-map columns by dictionary key (`APPS_ID` matches USR_APPS_ID / RLU_APPS_ID
        # / CFD_APPS_ID across queries).
        dd=h.dd if h.dd else None,
        group=h.group or None,
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
