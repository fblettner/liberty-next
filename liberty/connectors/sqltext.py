"""Pure SQL-text helpers of the SQL connector — string in, string out, no connection, no connector state:
the Oracle write target of a statement, a SET assignment removed, ORDER BY split / hoisted out of a wrap, the
aggregate (summary / Explorer) and filter wraps, the WHERE-bind scan, the ``#SCHEMA.<KEY>#`` / ``#DBLINK.<KEY>#``
placeholders. Moved verbatim from :mod:`liberty.connectors.sql`, which imports them back (every name stays
importable from there)."""

from __future__ import annotations

import re

from liberty.connectors.base import ConnectorError, StatementNotAllowedError

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

def _remove_set_assignment(sql: str, col: str) -> tuple[str, bool]:
    """Remove a ``<col> = :<bind>`` assignment from an UPDATE's SET clause.

    Used to preserve an unchanged PASSWORD on UPDATE: dropping the assignment
    (and its adjacent comma) leaves the stored value untouched instead of
    overwriting it with NULL. Operates only on the SET clause (before the first
    ``WHERE``), matches case-insensitively, and removes the trailing comma for a
    mid-list assignment or the leading comma for the last one. Returns
    ``(new_sql, removed?)`` — ``removed`` is False when no assignment matched."""
    upper = sql.upper()
    widx = upper.find(" WHERE ")
    head, tail = (sql[:widx], sql[widx:]) if widx != -1 else (sql, "")
    c = re.escape(col)
    # mid-list: "<col> = :bind ,"  → drop assignment + trailing comma
    new, n = re.subn(rf"\b{c}\s*=\s*:\w+\s*,\s*", "", head, count=1, flags=re.IGNORECASE)
    if not n:
        # last in list: ", <col> = :bind"  → drop leading comma + assignment
        new, n = re.subn(rf",\s*\b{c}\s*=\s*:\w+\s*", " ", head, count=1, flags=re.IGNORECASE)
    return (new + tail, bool(n))

# ``COL = {{TOKEN}}`` — captures the assigned column (group 1, possibly schema/table-qualified or
# quoted) so the token coerces to that column's format. Then bare ``{{TOKEN}}`` (VALUES position,
# WHERE, …) resolves to the token's natural Python type.
_ASSIGN_TOKEN_RE = re.compile(
    r"""([A-Za-z_][\w."`\[\]]*?)\s*=\s*\{\{\s*([A-Za-z_]\w*)\s*\}\}""", re.IGNORECASE,
)

_TEXT_FORMATS = {"text", "textarea", "email", "url", "color", "password"}

_ORDER_ALIAS = re.compile(r"(?<![\w.\"])([A-Za-z_][\w]*)\.(?=[A-Za-z_\"])")

def _hoisted_order_by(order_clause: str) -> str:
    """The ORDER BY re-attached OUTSIDE a wrap (``SELECT * FROM (…) lib_flt WHERE …``) can only
    name the derived table's output columns: an author's ``ORDER BY s.company_name`` would fail
    with "missing FROM-clause entry for table s" once a filter or a scope wraps the query. Strip
    the ``alias.`` qualifiers (a qualified name is an output column of the inner SELECT unless the
    author aliased it — then the clause was wrong under a wrap anyway). Expressions such as
    ``SUM(f.quantity)`` stay the author's problem: order by the output alias instead."""
    return _ORDER_ALIAS.sub("", order_clause)

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

# ── summary (aggregate) wrap ──────────────────────────────────────────────────────
# A screen can declare a "summary" view: parent rows are GROUP BY <dimensions> with a
# COUNT(*), expanding one lazily loads its detail rows. Both wrap the screen's read SQL as a
# derived table — same shape as the filter wrap above — so the count is computed in the DB
# over the whole result, not the row-capped grid. Dimension names come from operator config
# (the screen's summary block, relayed by the frontend) and are identifier-validated here.
_AGG_IDENT = re.compile(r"^[A-Za-z0-9_]+$")

def _validate_agg_column(name: str) -> str:
    """Reject anything that isn't a bare SQL identifier — the dimension is interpolated into
    the GROUP BY / WHERE, so it must not carry expressions, quotes, or whitespace."""
    if not _AGG_IDENT.match(name or ""):
        raise StatementNotAllowedError(f"summary: invalid group column {name!r}")
    return name

def _bucket_expr(col_ref: str, bucket: str, dialect: str) -> str:
    """Truncate a date/timestamp *col_ref* to *bucket* granularity, per dialect."""
    if dialect == "oracle":
        unit = {"day": None, "month": "MM", "year": "YYYY"}.get(bucket)
        return f"TRUNC({col_ref})" if unit is None else f"TRUNC({col_ref}, '{unit}')"
    # postgres / sqlite — DATE_TRUNC; cast to date so the dimension reads as a clean day/period.
    return f"DATE_TRUNC('{bucket}', {col_ref})::date"

def _dim_ref(name: str, bucket: str | None, owner: str, dialect: str) -> str:
    """Reference expression for a dimension (bucketed or raw), UNQUOTED so it folds
    case-insensitively against the derived table's columns (matches the filter wrap)."""
    ref = f"{owner}.{_validate_agg_column(name)}"
    return _bucket_expr(ref, bucket, dialect) if bucket else ref

def _apply_aggregate_wrap(
    sql_text: str, dims: list[tuple[str, str | None]], dialect: str, *, count_alias: str = "_count",
) -> str:
    """Wrap *sql_text* as ``SELECT <dims>, COUNT(*) AS _count FROM (<body>) lib_agg
    GROUP BY <dims> ORDER BY <dims>``. The inner ORDER BY is dropped (meaningless under
    aggregation)."""
    body, _order = _split_order_by(sql_text)
    selects, groups = [], []
    for name, bucket in dims:
        expr = _dim_ref(name, bucket, "lib_agg", dialect)
        selects.append(f"{expr} AS {name}")
        groups.append(expr)
    select_list = ", ".join(selects + [f"COUNT(*) AS {count_alias}"])
    group_list = ", ".join(groups)
    return (
        f"SELECT {select_list}\nFROM (\n{body}\n) lib_agg\n"
        f"GROUP BY {group_list}\nORDER BY {group_list}"
    )

def _apply_aggregate_detail_wrap(
    sql_text: str, dims: list[tuple[str, str | None, str]], dialect: str,
) -> str:
    """Wrap *sql_text* to the rows of ONE summary group: ``SELECT * FROM (<body>) lib_det
    WHERE <dim = :bind …> [ORDER BY <original>]``. *dims* carries ``(column, bucket, bind_name)``."""
    body, order = _split_order_by(sql_text)
    preds = [f"({_dim_ref(name, bucket, 'lib_det', dialect)} = :{bind})" for name, bucket, bind in dims]
    out = f"SELECT * FROM (\n{body}\n) lib_det\nWHERE {' AND '.join(preds)}"
    if order:
        out += f"\nORDER BY {order}"
    return out

def _filter_sql_type(fmt: str | None, dialect: str) -> str:
    """SQL type to cast a filter bind into for a non-text column — picked so the comparison
    can use a btree index on the column. Falls back to a wide VARCHAR when the format is
    unknown (matches the historic v1 behaviour but only for one bind, not every column)."""
    f = (fmt or "").strip().lower()
    if f in ("integer", "number", "jdedate", "jdetime", "jdenumber"):
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

def _build_filter_predicate(name: str, fmt: str | None, op: str, dialect: str, *, trim_text: bool = False, bind: str | None = None) -> str:
    """One filter predicate against ``lib_flt.<name>`` for *op* using a bind named ``:<name>``
    (or *bind* — the range ops ``gte`` / ``lte`` / ``lt`` bind ``<name>_from`` / ``<name>_to``).

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
    b = bind or name
    if op in ("gte", "lte", "lt"):
        # A range bound (a dashboard / filter-panel period on a date column): typed like the
        # column; a text date column (ISO strings) compares as text, which orders correctly.
        # ``lt`` is a date-only end bound on a date-time column (the next day, exclusive).
        rhs = f"CAST(:{b} AS {_filter_sql_type(None, dialect)})" if text_format else f":{b}"
        return f"{col_ref} {({'gte': '>=', 'lte': '<=', 'lt': '<'})[op]} {rhs}"
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

# `#DBLINK.<NAME>#` (or bare `#DBLINK#`) in a query's SQL, replaced at execution time with the pool's
# `dblinks` mapping — a DB-link suffix appended to a table name (e.g. `F0092@ORCLPROD`). UNLIKE the
# schema token, an unmapped / empty name resolves to "" (the placeholder is dropped), so a pool with
# no DB links configured runs the query locally. Case-insensitive on the literal and the name.
_DBLINK_PLACEHOLDER = re.compile(r"#DBLINK(?:\.([A-Za-z0-9_]+))?#", re.IGNORECASE)

# a non-empty replacement must be a bare DB-link ref — `@name`, optionally domain-qualified
# (`@ORCLPROD.WORLD`) — a config-injection guard so nothing but a link suffix can reach the SQL.
_DBLINK_VALUE = re.compile(r"@[A-Za-z0-9_$#.]+")

def _apply_dblink_placeholders(sql: str, dblinks: dict[str, str], *, connector: str, query: str, pool: str) -> str:
    """Replace ``#DBLINK.<NAME>#`` (and bare ``#DBLINK#`` → the ``""`` key) in *sql* with the pool's
    ``dblinks`` mapping. A name with **no mapping, or an empty value, resolves to ``""``** — the
    placeholder is dropped (so the same query runs locally on a pool without DB links; this is the
    feature's whole point). A non-empty value that isn't a bare ``@link`` reference raises
    :class:`ConnectorError` (config-injection guard). When the query has no placeholders, *sql* is
    returned unchanged (safe to call unconditionally)."""
    if "#" not in sql:  # cheap fast path — no `#…#` token at all
        return sql
    lookup = {k.upper(): v for k, v in dblinks.items()}

    def _sub(m: re.Match[str]) -> str:
        key = (m.group(1) or "").upper()
        value = (lookup.get(key) or "").strip()
        if not value:  # unmapped or empty → drop the placeholder (dblink is optional by design)
            return ""
        if not _DBLINK_VALUE.fullmatch(value):
            raise ConnectorError(
                f"{connector}.{query}: dblink mapping {key or '(default)'!r} = {value!r} (pool {pool!r}) "
                "is not a bare DB-link reference — must be like '@ORCLPROD' or '@ORCLPROD.WORLD'"
            )
        return value

    return _DBLINK_PLACEHOLDER.sub(_sub, sql)
