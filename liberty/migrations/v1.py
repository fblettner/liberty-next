"""Transform v1 (`ly_*` metadata tables) into v2 ``connectors.toml`` content.

These are pure functions over plain row dicts — the DB reading lives in
:mod:`liberty.migrations.source`, so the transformation is easy to unit-test.

v1 → v2 mapping
---------------
* ``ly_query`` (logical name/label) + ``ly_qry_sql`` (per dbtype × CRUD: pool, SQL,
  ORDER BY) → one v2 **SQL connector per ``query_pool``**, each carrying the queries
  that ran against that pool. A query becomes ``[[connectors.<pool>.queries]]`` named
  ``<query_label>_<crud>``, with ``sql`` = ``query_sqlquery`` (+ ``ORDER BY <query_orderby>``
  for reads); v1's per-``query_dbtype`` SQL variants collapse to a ``sql = { default = …,
  oracle = … }`` dialect map (one distinct statement → a plain string; ``--dbtype`` keeps one).
  v1's ``query_crud`` is a REST verb — ``GET`` (read), ``POST``/``PUT``/``PATCH``/``DELETE``
  (write); a non-read crud → ``writable = true``. ``ly_tbl_col`` (table widgets) / ``ly_dlg_col``
  (form fields), via ``ly_tables.tbl_query_id`` / ``ly_dlg_frm.frm_query_id``, → each read
  query's ``columns`` display hints (title from ``col_label``/``ly_dictionary.dd_label``,
  ``hidden`` from ``col_visible``, ``format`` from ``col_type``). The pool's connection is
  filled by :func:`migrate_pools` (from ``ly_applications``) or left as a ``${LIBERTY_DB_URL_…}`` stub.
* ``ly_api_conn`` (base URL + creds) → a v2 **API connector**; ``ly_api`` rows pointing at it
  (``api_conn_id``) → its ``[[connectors.<conn>.endpoints]]`` (method, path = ``api_url`` —
  relative or absolute, v2 resolves both — body, headers from ``ly_api_header``, params from
  ``ly_api_params``). ``ly_api`` rows with no ``api_conn_id`` go into a single ``legacy_api``
  connector (``base_url = ""``, absolute-URL paths). v1's ``conn_password`` is an ``ENC:`` blob,
  carried over **verbatim** — v2 decrypts it at runtime with ``[crypto] master_key``.
* ``ly_applications`` → ``[pools.*]`` (one per ``apps_pool``) via :func:`migrate_pools` — a real
  SQLAlchemy URL from ``apps_dbtype``/``apps_host``/``apps_port``/``apps_database`` (or ``apps_jdbc``),
  the DB password a ``${MIGRATED_PW_<NAME>}`` placeholder; v1's reserved ``default`` pool is skipped.
* ``ly_menus`` (+ ``ly_menus_l``) → ``[menus.<app>]`` via :func:`migrate_menus` — the app's
  navigation tree (flat, items linked by ``parent``), each query-backed node resolved through
  ``ly_tables``/``ly_dlg_frm`` → ``ly_query`` to the matching read query's v2 name; goes in
  ``config/menus.toml``, not ``connectors.toml``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Mapping
from urllib.parse import quote as _urlquote

from liberty.connectors.base import find_bind_params

_log = logging.getLogger(__name__)

# v1's `query_crud` uses REST-style verbs — GET (read), POST/PUT/PATCH (write), DELETE; some
# older rows use SQL keywords (SELECT/INSERT/UPDATE/MERGE). A query is a *read* iff its crud is
# one of these (else it's treated as a mutation: `writable = true`, no display column hints).
_READ_CRUD = {"GET", "SELECT", "READ"}
# crud values that mean "update an existing row" — their `_put` WHERE clause is rewritten to bind
# the key columns as `:<col>_ORIGINAL` (so editing a key column still finds the row to update).
_UPDATE_CRUD = {"PUT", "PATCH", "UPDATE"}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str | None, *, fallback: str = "x") -> str:
    """Lower-case, ``[a-z0-9_]``-only identifier. Empty → *fallback*."""
    if not text:
        return fallback
    s = _SLUG_RE.sub("_", str(text).strip().lower()).strip("_")
    if not s:
        return fallback
    if s[0].isdigit():
        s = f"x{s}"
    return s


def _uniquify(name: str, taken: set[str]) -> str:
    if name not in taken:
        taken.add(name)
        return name
    n = 2
    while f"{name}_{n}" in taken:
        n += 1
    final = f"{name}_{n}"
    taken.add(final)
    return final


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    """TOML has no null — strip ``None`` values (and empty strings for optional text)."""
    return {k: v for k, v in d.items() if v is not None and v != ""}


# --------------------------------------------------------------------------- #
# SQL queries  (ly_query + ly_qry_sql)
# --------------------------------------------------------------------------- #


# v1 `query_dbtype` → SQLAlchemy backend name (`generic` becomes the v2 `default` variant).
_DBTYPE_TO_DIALECT = {
    "generic": "default",
    "postgres": "postgresql", "postgresql": "postgresql", "pg": "postgresql",
    "oracle": "oracle",
    "mysql": "mysql", "mariadb": "mysql",
    "sqlserver": "mssql", "mssql": "mssql", "sqlserveur": "mssql",
    "sqlite": "sqlite",
}


def _dialect_name(dbtype: Any) -> str:
    key = str(dbtype or "").strip().lower()
    return _DBTYPE_TO_DIALECT.get(key, key or "default")


# When a read query has `filter`-flagged columns (v1's col_filter), wrap it so the value the
# TableView sends for each such column actually pre-filters server-side: `SELECT * FROM (<orig>)
# lib_flt WHERE …`. Each column C gets a `:C` value bind and a `:C_op` operator bind. The bind and the
# column are CAST to text in the predicate: that pins the parameter's type (so a NULL bind — an
# *unset* filter — doesn't trip "could not determine data type of parameter" on asyncpg) and gives
# uniform comparison regardless of the column's real type (numbers/dates compare on their text form;
# the in-grid TanStack filters do the type-aware range filtering on the loaded page). `LIKE`/`LOWER`/
# `CAST(… AS VARCHAR(n))` are portable across PostgreSQL / Oracle / SQLite. A NULL/empty `:C` matches
# everything (= "no filter"), so omitting the unset filters is fine.
_FILTER_OPS = ("contains", "equals", "notEquals", "startsWith", "endsWith")


def _filter_predicate(col: str, vchar: str) -> str:
    pv = f"CAST(:{col} AS {vchar})"        # the value, as text (also pins the bind's type)
    po = f"CAST(:{col}_op AS {vchar})"     # the operator, as text
    cv = f"CAST(lib_flt.{col} AS {vchar})"    # the column, as text
    branches = (
        f"{pv} IS NULL OR {pv} = ''",
        f"COALESCE({po}, 'contains') = 'contains' AND LOWER({cv}) LIKE LOWER('%' || {pv} || '%')",
        f"{po} = 'equals'     AND {cv} = {pv}",
        f"{po} = 'notEquals'  AND {cv} <> {pv}",
        f"{po} = 'startsWith' AND LOWER({cv}) LIKE LOWER({pv} || '%')",
        f"{po} = 'endsWith'   AND LOWER({cv}) LIKE LOWER('%' || {pv})",
    )
    return "  AND (" + " OR ".join(f"({b})" for b in branches) + ")"


def _wrap_with_filters(base_sql: str, cols: list[str], *, dialect: str = "default") -> str:
    vchar = "VARCHAR2(4000)" if dialect == "oracle" else "VARCHAR(4000)"
    preds = "\n".join(_filter_predicate(c, vchar) for c in cols)
    return f"SELECT * FROM (\n{base_sql}\n) lib_flt\nWHERE 1=1\n{preds}"


def _outermost_select_columns(sql: str) -> set[str] | None:
    """Best-effort extractor for the column names a SELECT exposes — used to validate that
    each ``filter = true`` hint references a column the result actually has. Returns ``None``
    when the SQL is too complex to parse (then the caller skips the validation and emits the
    filter as-is — preserving the old behaviour rather than dropping a potentially-valid one).

    Handles the shapes the v1 migration produces: a single top-level SELECT (or SELECT DISTINCT)
    with comma-separated column expressions, each of which is either ``COL``, ``T.COL``,
    ``expr AS ALIAS``, ``expr ALIAS``, or ``(subquery) ALIAS``. The alias is the last identifier
    in the entry (after AS if present). FROM nesting inside parens is handled by tracking depth.
    """
    # Find the outer SELECT keyword (skipping nested SELECTs inside parens — track depth).
    # We're looking for ``SELECT[DISTINCT] <cols> FROM`` where both keywords are at depth 0.
    depth = 0
    select_start = -1
    i = 0
    sl = sql
    sl_upper = sl.upper()
    while i < len(sl):
        c = sl[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        elif depth == 0 and select_start < 0:
            # Match SELECT as a whole word — followed by whitespace, not part of a longer ident.
            if sl_upper.startswith("SELECT", i) and (i == 0 or not (sl[i - 1].isalnum() or sl[i - 1] == "_")):
                after = i + len("SELECT")
                if after < len(sl) and (sl[after].isspace() or sl[after] == "\n"):
                    select_start = after
                    break
        i += 1
    if select_start < 0:
        return None
    # Skip an optional DISTINCT/ALL right after SELECT.
    rest = sl[select_start:].lstrip()
    for kw in ("DISTINCT", "ALL"):
        if rest.upper().startswith(kw) and len(rest) > len(kw) and rest[len(kw)].isspace():
            rest = rest[len(kw):].lstrip()
            break
    # Find the matching FROM at depth 0 from where we are now.
    depth = 0
    from_pos = -1
    for j in range(len(rest)):
        c = rest[j]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        elif depth == 0 and rest.upper().startswith("FROM", j) and (j == 0 or not (rest[j - 1].isalnum() or rest[j - 1] == "_")):
            after = j + len("FROM")
            if after < len(rest) and (rest[after].isspace() or rest[after] == "\n"):
                from_pos = j
                break
    if from_pos < 0:
        return None
    cols_text = rest[:from_pos]
    # Split on top-level commas (skip commas inside parens — function args, CASTs, …).
    parts: list[str] = []
    buf: list[str] = []
    d = 0
    for ch in cols_text:
        if ch == '(':
            d += 1
        elif ch == ')':
            d -= 1
        if ch == ',' and d == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    out: set[str] = set()
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Strip a trailing AS alias clause and use the alias.
        m = re.search(r"\bAS\s+([A-Za-z_]\w*)\s*$", p, re.IGNORECASE)
        if m:
            out.add(m.group(1))
            continue
        # Else the last identifier in the entry is the alias / column name. Handles `T.COL`,
        # `expr ALIAS`, and bare `COL`. Anything purely expression-like (no trailing identifier)
        # ends up with no alias — we skip it; that column won't be filter-able anyway.
        m = re.search(r"([A-Za-z_]\w*)\s*$", p)
        if m:
            out.add(m.group(1))
    return out if out else None


def _drop_filter_for_missing_cols(
    hints: list[dict[str, Any]], sql_variants: dict[str, str], *, connector: str, query: str,
) -> None:
    """Mutate *hints* — strip ``filter = true`` from any hint whose ``name`` doesn't appear in
    every dialect variant's outer SELECT. v1's metadata occasionally points at a column not in the
    result (operator edited the SQL but not the column hint, or the migrator's drill-resolution
    landed on the wrong column for a multi-column dd) — without this check, ``_wrap_with_filters``
    binds ``lib_flt.<missing>`` and the SQL parser errors at run time. When the SQL is too complex
    to parse, the hint is left alone (the runtime might still fail but we don't know any better)."""
    exposed_by_dia: dict[str, set[str] | None] = {dia: _outermost_select_columns(s) for dia, s in sql_variants.items()}
    # Use the intersection of every dialect's columns — only keep `filter` for a column that
    # exists in *every* dialect's SELECT.
    dia_sets = [s for s in exposed_by_dia.values() if s is not None]
    if not dia_sets:
        return  # couldn't parse — leave hints alone
    exposed = set.intersection(*dia_sets) if len(dia_sets) > 1 else dia_sets[0]
    # Case-insensitive set — Postgres folds unquoted identifiers, Oracle uppercases. Match either way.
    exposed_lower = {c.lower() for c in exposed}
    for h in hints:
        if not h.get("filter"):
            continue
        if str(h.get("name") or "").lower() not in exposed_lower:
            _log.warning(
                "column hints: %s.%s — filter column %r is not in the query's SELECT output; "
                "dropping `filter = true` to avoid a broken `lib_flt.%s` WHERE clause at run time",
                connector, query, h.get("name"), h.get("name"),
            )
            del h["filter"]


# A read query used as a lookup target (v1 ly_lookup.lkp_query_id) needs its declared params
# (ly_lkp_params) to *actually filter* — v1's SQLs return every row and the framework narrowed
# them in code. Wrap with `SELECT * FROM (…) lib_lkp WHERE (param IS NULL OR <col> = param)` per
# declared param: a NULL/unset bind matches every row, an explicit value narrows the result. The
# bind and column are CAST to text, same as `_wrap_with_filters` — pins the asyncpg bind type
# and makes the comparison portable across PG / Oracle / SQLite. The wrapped column name *is*
# the param name (UDC: SY → DRSY DRSY, aliased as SY in the SELECT — so the outer wrap can
# reference `lib_lkp.SY` regardless of the inner alias).
def _lookup_param_predicate(param: str, vchar: str) -> str:
    pv = f"CAST(:{param} AS {vchar})"
    cv = f"CAST(lib_lkp.{param} AS {vchar})"
    return f"  AND ({pv} IS NULL OR {pv} = '' OR {cv} = {pv})"


def _wrap_with_lookup_params(base_sql: str, params: list[str], *, dialect: str = "default") -> str:
    vchar = "VARCHAR2(4000)" if dialect == "oracle" else "VARCHAR(4000)"
    preds = "\n".join(_lookup_param_predicate(p, vchar) for p in params)
    return f"SELECT * FROM (\n{base_sql}\n) lib_lkp\nWHERE 1=1\n{preds}"


def _simplify_upsert(sql: str) -> str:
    """v1's ``_post`` SQL for some tables is an *upsert* — PostgreSQL ``INSERT … ON CONFLICT … DO
    UPDATE …`` or Oracle ``MERGE INTO … WHEN NOT MATCHED THEN INSERT (…) VALUES (…)`` — so an Excel
    re-import would replace existing rows. v2 decides update-vs-insert in the TableView's batch-edit
    model (the import matches imported rows against the loaded ones on the query's ``key_columns``),
    so the ``_post`` query only needs to *insert*. Collapse it to a plain ``INSERT INTO t (cols)
    VALUES (:cols)`` — far easier to maintain (a future query builder will generate these). Anything
    that isn't an upsert is returned unchanged."""
    if re.search(r"(?i)\bON\s+CONFLICT\b", sql):  # PostgreSQL upsert → drop the ON CONFLICT … tail
        return re.split(r"(?i)\s*\bON\s+CONFLICT\b", sql, maxsplit=1)[0].rstrip()
    m = re.match(r"(?is)\s*MERGE\s+INTO\s+(\S+)", sql)  # Oracle MERGE → rebuild the INSERT half
    if m:
        ins = re.search(r"(?is)\bWHEN\s+NOT\s+MATCHED\s+THEN\s+INSERT\s*\(([^)]*)\)\s*VALUES\b", sql)
        if ins:
            cols = [c.strip() for c in ins.group(1).split(",") if c.strip()]
            if cols:
                names = ",\n  ".join(cols)
                binds = ",\n  ".join(f":{c}" for c in cols)
                return f"INSERT INTO {m.group(1)}\n  (\n  {names}\n  )\nVALUES\n  (\n  {binds}\n  )"
    return sql


def _upsert_to_update(sql: str) -> str:
    """The `_put` counterpart of :func:`_simplify_upsert` — v1 often registered an *upsert* under
    the ``PUT`` crud (the FormsTable's "update" action ran an ``INSERT … ON CONFLICT`` / ``MERGE``).
    v2's TableView already separates update from insert (and :func:`_simplify_upsert` handles the
    insert side), so collapse the update side to a plain ``UPDATE t SET … WHERE <key cols> = :<col>``
    — the WHERE is the conflict / ``ON`` columns; :func:`_rewrite_put_where` then turns those into
    ``:<col>_ORIGINAL``. PostgreSQL `EXCLUDED.col` references and Oracle `src.col` / `<alias>.col`
    references become plain ``:col``. Anything that isn't an upsert is returned unchanged."""
    pg = re.match(
        r"(?is)\s*INSERT\s+INTO\s+(\S+).*?\bON\s+CONFLICT\s*\(([^)]*)\)\s*DO\s+UPDATE\s+SET\s+(.*)$", sql
    )
    if pg:
        table, keys_raw, set_body = pg.group(1), pg.group(2), pg.group(3).strip()
        set_body = re.sub(r"(?i)\bEXCLUDED\.(\w+)", r":\1", set_body)
        keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
        where = " AND ".join(f"{k} = :{k}" for k in keys)
        return f"UPDATE {table}\nSET {set_body}\nWHERE {where}"
    m = re.match(r"(?is)\s*MERGE\s+INTO\s+(\S+)(?:\s+(\w+))?\s+USING\b", sql)
    if m:
        table, alias = m.group(1), m.group(2)
        on_m = re.search(r"(?is)\bON\s*\((.*?)\)\s*WHEN\s+MATCHED\b", sql)
        set_m = re.search(r"(?is)\bWHEN\s+MATCHED\s+THEN\s+UPDATE\s+SET\s+(.*?)\bWHEN\s+NOT\s+MATCHED\b", sql)
        if on_m and set_m:
            def _deref(s: str) -> str:
                if alias:
                    s = re.sub(rf"\b{re.escape(alias)}\.", "", s)
                return re.sub(r"\bsrc\.(\w+)", r":\1", s).strip()
            return f"UPDATE {table}\nSET {_deref(set_m.group(1))}\nWHERE {_deref(on_m.group(1))}"
    return sql


def _rewrite_put_where(sql: str) -> str:
    """In an `UPDATE … SET … WHERE …` statement, rebind **every** parameter in the WHERE clause
    from `:<col>` to `:<col>_ORIGINAL` (the SET clause is untouched — it keeps `:<col>` for the new
    value). v1's `_put` queries reuse `:<col>` in both SET and WHERE, so editing a column the WHERE
    matches on (typically the key) would otherwise look for the *new* value and find nothing; the
    TableView sends the row's pre-edit values under `:<col>_ORIGINAL`, so the WHERE now matches the
    right row. Only what comes after the first `WHERE` is rewritten; no `WHERE`, no `:params` there,
    or already rewritten → returned unchanged."""
    m = re.search(r"\bWHERE\b", sql, re.IGNORECASE)
    if not m:
        return sql
    head, where = sql[: m.start()], sql[m.start():]
    for p in find_bind_params(where):
        if p.endswith("_ORIGINAL"):
            continue  # idempotent — don't `_ORIGINAL`-suffix twice
        where = re.sub(rf"(?<!:):{re.escape(p)}\b", f":{p}_ORIGINAL", where)
    return head + where


def _sql_value(variants: dict[str, str]) -> str | dict[str, str]:
    """Collapse a {dialect: sql} map to v2's `sql`: a plain string when there's one
    distinct statement, else `{default: …, <dialect>: …}` (default = the v1 `generic`
    variant, else `postgresql`, else the first; identical variants aren't repeated)."""
    if len(variants) == 1:
        return next(iter(variants.values()))
    default_sql = variants.get("default") or variants.get("postgresql") or next(iter(variants.values()))
    out: dict[str, str] = {"default": default_sql}
    for dn, s in variants.items():
        if dn != "default" and s != default_sql:
            out[dn] = s
    return out if len(out) > 1 else out["default"]  # all variants identical → a plain string


def migrate_sql_queries(
    queries: Iterable[Mapping[str, Any]],
    sql_rows: Iterable[Mapping[str, Any]],
    *,
    dbtype: str | None = None,
    connector_prefix: str = "",
    column_hints: Mapping[int, list[dict[str, Any]]] | None = None,
    column_filters: Mapping[int, Mapping[str, list[dict[str, str]]]] | None = None,
    column_visibility: Mapping[int, Mapping[str, list[dict[str, Any]]]] | None = None,
    table_meta: Mapping[int, Mapping[str, Any]] | None = None,
    key_columns: Mapping[int, list[str]] | None = None,
    lookup_params: Mapping[int, list[str]] | None = None,
) -> dict[str, Any]:
    """Build the ``{pools, connectors}`` dict for the SQL side.

    One v2 query per ``(query_pool, query_id, query_crud)``; the per-``query_dbtype`` SQL
    variants become a dialect map (see :class:`~liberty.connectors.config.QueryDef`).

    Args:
        queries: rows from ``ly_query`` (``query_id``, ``query_label``, ``query_type``).
        sql_rows: rows from ``ly_qry_sql`` (``query_id``, ``query_dbtype``, ``query_crud``,
            ``query_pool``, ``query_sqlquery``, ``query_orderby``).
        dbtype: if given, only migrate rows with this ``query_dbtype`` (→ plain-string SQL).
        connector_prefix: prepended to the per-pool connector name (e.g. ``"v1_"``).
        column_hints: ``{query_id: [column-hint dict]}`` (from :func:`migrate_column_hints`) —
            attached to each emitted query as its ``columns`` display hints. A read query that has
            ``filter``-flagged columns (v1's ``col_filter``) is also wrapped — ``SELECT * FROM (<orig>)
            lib_flt WHERE …`` — so the value the TableView sends for each such column (a ``:<col>`` bind
            plus an optional ``:<col>_op`` operator bind) pre-filters server-side.
        column_filters: ``{query_id: {col_name: [{"source", "column"}, …]}}`` (from
            :func:`migrate_table_filters`) — cascading-filter dependencies, merged onto the matching
            column hint as ``filter_from`` (v1's ``ly_tbl_filters``).
        column_visibility: ``{query_id: {col_name: [{"field", "value"}, …]}}`` (from
            :func:`migrate_column_visibility`) — conditional-rendering rules, merged onto the matching
            column hint as ``visible_when`` (v1's ``cdn_*``).
        table_meta: ``{query_id: {"description"?, "auto_load"?}}`` (from :func:`migrate_table_meta`) —
            the v1 table/form friendly label → the read query's ``description``, ``tbl_auto_load`` →
            ``auto_load``.
        key_columns: ``{query_id: [col, …]}`` (from :func:`migrate_key_columns`) — the row-key columns
            (v1's ``col_key``); attached to the read query as ``key_columns`` (the TableView's Excel
            import uses them to decide update-vs-insert).
        lookup_params: ``{query_id: [param_name, …]}`` (from :func:`migrate_lookup_param_names`) —
            for queries used as a lookup target (v1's ``ly_lookup.lkp_query_id``), wrap the read SQL
            with ``WHERE (:P IS NULL OR <col> = :P)`` per declared param. v1's lookup queries didn't
            carry their own WHERE (the framework filtered rows in code), so the wrapped form is what
            actually narrows the result server-side; the params are also declared on ``QueryDef.params``
            so the SQL connector binds them. NULL/blank params match every row → backward-compatible
            with callers that don't pass anything.
    SQL is also touched per crud (v2's TableView already separates update/insert, so v1's upsert
    queries are split apart): a ``_post`` upsert (PostgreSQL ``INSERT … ON CONFLICT`` / Oracle
    ``MERGE``) collapses to a plain ``INSERT`` (:func:`_simplify_upsert`); a ``_put`` upsert collapses
    to a plain ``UPDATE`` (:func:`_upsert_to_update`); and every ``_put``'s WHERE is rebound to
    ``:<col>_ORIGINAL`` for every parameter it references (:func:`_rewrite_put_where` — so editing the
    key still finds the row, since the TableView sends the row's pre-edit values under those names).
    """
    labels = {int(q["query_id"]): (q.get("query_label") or "") for q in queries}
    tmeta = {int(k): dict(v) for k, v in (table_meta or {}).items()}
    rows = [dict(r) for r in sql_rows]
    if dbtype:
        rows = [r for r in rows if (r.get("query_dbtype") or "").lower() == dbtype.lower()]

    pools: dict[str, dict[str, Any]] = {}
    connectors: dict[str, dict[str, Any]] = {}
    names_per_connector: dict[str, set[str]] = {}
    groups: dict[tuple[str, int, str], dict[str, str]] = {}  # (conn, query_id, crud) → {dialect: raw sql}
    orderbys: dict[tuple[str, int, str], str] = {}           # (conn, query_id, crud) → ORDER BY clause
    order: list[tuple[str, int, str]] = []

    for r in rows:
        sql = (r.get("query_sqlquery") or "").strip()
        if not sql:
            continue
        qid = int(r["query_id"])
        crud = str(r.get("query_crud") or "SELECT").upper()
        pool = str(r.get("query_pool") or "default").strip() or "default"
        conn_name = f"{connector_prefix}{slugify(pool, fallback='default')}"
        if conn_name not in connectors:
            connectors[conn_name] = {"type": "sql", "pool": conn_name, "queries": []}
            names_per_connector[conn_name] = set()
            # Stub pool — fill in the real URL (or set the named env var).
            pools.setdefault(conn_name, {"url": "${LIBERTY_DB_URL_" + conn_name.upper() + "}", "pool_pre_ping": True})
        key = (conn_name, qid, crud)
        if key not in groups:
            groups[key] = {}
            order.append(key)
        groups[key].setdefault(_dialect_name(r.get("query_dbtype")), sql)  # first row of a dialect wins
        orderby = (r.get("query_orderby") or "").strip()
        if orderby and crud in _READ_CRUD:
            orderbys.setdefault(key, orderby)

    for key in order:
        conn_name, qid, crud = key
        is_read = crud in _READ_CRUD
        label = labels.get(qid, "")
        base = slugify(f"{label}_{crud}" if label else f"q{qid}_{crud}", fallback=f"q{qid}_{crud.lower()}")
        hints = (column_hints or {}).get(qid) if is_read else None  # display hints only make sense for result sets
        cfilters = (column_filters or {}).get(qid) if is_read else None  # cascading-filter deps per column (v1 ly_tbl_filters)
        cvis = (column_visibility or {}).get(qid) if is_read else None     # conditional-render rules per column (v1 cdn_*)
        if hints and (cfilters or cvis):
            for h in hints:
                deps = (cfilters or {}).get(h["name"])
                if deps:
                    h["filter_from"] = [dict(d) for d in deps]
                rules = (cvis or {}).get(h["name"])
                if rules:
                    h["visible_when"] = [dict(r) for r in rules]
        tm_full = tmeta.get(qid)  # v1 ly_tables row meta (label/auto-load on reads, audit on writes)
        tm = tm_full if is_read else None  # display hints only apply to the read companion
        audit_table = (tm_full or {}).get("audit_table") if not is_read else None  # AUD_<table> on writes only
        kcs = (key_columns or {}).get(qid, []) if is_read else []  # the result's identity (v1 col_key)
        # Sanity-check the filter columns against the actual SELECT output — a hint that names a
        # column the SQL doesn't expose would produce a broken `lib_flt.X` WHERE clause that errors
        # at run time (502 on the route). This catches inconsistencies between v1's `ly_tbl_col`
        # and the v1 SQL itself, plus any flt_target the drill-filter resolver couldn't translate.
        if hints:
            _drop_filter_for_missing_cols(hints, groups[key], connector=conn_name, query=base)
        # Lookup-target params (v1 ly_lkp_params) — declarative names the lookup callers bind. We
        # wrap the read SQL with `(:P IS NULL OR <col> = :P)` so they actually narrow server-side
        # (v1's SQL didn't have its own WHERE — the framework filtered in code). Only applies to
        # *read* queries; a writable query isn't a lookup target.
        lkp_params = list((lookup_params or {}).get(qid, [])) if is_read else []
        is_update = crud in _UPDATE_CRUD
        # build each dialect variant. read + filter columns → `SELECT * FROM (…) lib_flt WHERE <filters>`.
        # a read that's a lookup target → `SELECT * FROM (…) lib_lkp WHERE <params>` (wraps independently of
        # filter_cols — a query can be both a TableView source AND a lookup target). an UPDATE-crud
        # query: an upsert there collapses to a plain UPDATE, then its WHERE is rebound to
        # `:<col>_ORIGINAL`. an INSERT-crud upsert (`_post`) collapses to a plain INSERT. then
        # ORDER BY is appended (to the outer query, when the read was wrapped).
        ob = orderbys.get(key, "")
        def _variant(raw: str, dia: str) -> str:
            # v2 (Phase 8): the runtime applies the filter wrap dynamically — see
            # :meth:`SQLConnector._apply_filter_wrap`. The migrator no longer bakes the
            # ``SELECT * FROM (…) lib_flt WHERE 1=1 AND <preds>`` envelope into the stored
            # SQL: it kept *every* filter column's predicate in the stored TOML even when
            # the caller wasn't actually filtering, CAST every value to VARCHAR(4000)
            # (killing indexes), and made the query unusable in a wizard. The runtime now
            # consults ``QueryDef.columns`` to find which columns are ``filter = true``,
            # reads the caller's bind values to discover which are *actually set*, and
            # builds a type-aware predicate per active column only (text → operator-aware;
            # number/date/boolean/jdedate → typed equals, index-friendly).
            #
            # We still emit the *lookup-param* wrap (separate concern: a query is a lookup
            # target whose params narrow it at fetch time; the wrap is fixed per query,
            # not per caller) and the upsert simplifications.
            if is_update:
                s = _rewrite_put_where(_upsert_to_update(raw))
            elif is_read:
                s = raw
            else:
                s = _simplify_upsert(raw)
            if lkp_params:
                s = _wrap_with_lookup_params(s, lkp_params, dialect=dia)
            return f"{s}\nORDER BY {ob}" if ob else s
        variants = {dia: _variant(raw, dia) for dia, raw in groups[key].items()}
        # Declare the lookup params on `QueryDef.params` so the SQL connector accepts the binds and
        # the runtime knows they're optional (blank → SQL NULL → the WHERE branch lets every row through).
        param_defs = [{"name": p} for p in lkp_params]
        # Phase 3 — ``columns`` / ``auto_load`` / ``audit`` / ``key_columns`` have moved off
        # ``QueryDef`` onto :class:`Screen`. ``migrate_screens`` consumes the same source data
        # (column_hints / table_meta / key_columns) and emits the matching ``Screen`` fields
        # there (audit_table / auto_load / key_columns / columns). The unused locals here
        # (``tm`` / ``kcs`` / ``audit_table`` / ``hints``) stay so the existing data plumb
        # keeps working for ``migrate_screens``' calls in ``migrate_cli`` and tests.
        _unused = (tm, kcs, audit_table, hints)
        connectors[conn_name]["queries"].append(
            _drop_none({
                "name": _uniquify(base, names_per_connector[conn_name]),
                "label": label or None,
                "description": (tm or {}).get("description") or None,
                "writable": None if is_read else True,  # GET/SELECT → omit (default false); POST/PUT/DELETE/… → writable
                "sql": _sql_value(variants),
                "params": (param_defs or None),
            })
        )

    return {"pools": pools, "connectors": connectors}


# --------------------------------------------------------------------------- #
# Column hints  (ly_tbl_col / ly_dlg_col → QueryDef.columns)  + dictionary  (ly_dictionary → dictionary.toml)
# --------------------------------------------------------------------------- #

# v1's single-char flags. `col_visible` ∈ _HIDDEN_FLAGS → hidden; `col_filter` / `tbl_auto_load` ∈ _YES_FLAGS → on.
_HIDDEN_FLAGS = {"N", "n", "0", "F", "f", "FALSE", "false", "NO", "no", "OFF", "off"}
_YES_FLAGS = {"Y", "y", "1", "T", "t", "TRUE", "true", "YES", "yes", "ON", "on"}
# `col_type` / `dd_type` values that carry no useful display information (the default) — drop them.
_FORMAT_NOOP = {"", "text", "varchar", "varchar2", "nvarchar", "string", "char", "clob"}


def _column_format(col_type: Any) -> str | None:
    t = str(col_type or "").strip().lower()
    return t if t and t not in _FORMAT_NOOP else None


def migrate_lookup_param_names(
    lookup_rows: Iterable[Mapping[str, Any]],
    lookup_params_rows: Iterable[Mapping[str, Any]],
) -> dict[int, list[str]]:
    """Crosswalk v1's ``ly_lookup`` (lkp_id → lkp_query_id) + ``ly_lkp_params`` (lkp_id, dd_id) into
    ``{query_id: [param_name, …]}`` keyed on the query the lookup points at. Fed to
    :func:`migrate_sql_queries` (``lookup_params=…``) so each lookup-target read query gets wrapped
    with a ``WHERE`` on its declared params and the params are declared on ``QueryDef.params``.
    Multiple lookups pointing at the same query get their param lists unioned (file order preserved)."""
    lkp_to_qid: dict[str, int] = {}
    for r in lookup_rows:
        lid = str(r.get("lkp_id") or "").strip()
        qid = r.get("lkp_query_id")
        if lid and qid is not None:
            try:
                lkp_to_qid[lid] = int(qid)
            except (TypeError, ValueError):
                continue
    out: dict[int, list[str]] = {}
    for r in lookup_params_rows:
        lid = str(r.get("lkp_id") or "").strip()
        name = str(r.get("dd_id") or "").strip()
        if not lid or not name or lid not in lkp_to_qid:
            continue
        lst = out.setdefault(lkp_to_qid[lid], [])
        if name not in lst:
            lst.append(name)
    return out


def migrate_column_hints(
    tbl_col_rows: Iterable[Mapping[str, Any]],
    dlg_col_rows: Iterable[Mapping[str, Any]] = (),
    *,
    extra_filter_cols: Mapping[int, Iterable[str]] | None = None,
    read_sql_by_qid: Mapping[int, str] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    """Build ``{query_id: [column-hint dict]}`` from v1's ``ly_tbl_col`` / ``ly_dlg_col`` rows.

    Each row maps a query's result column (``col_target``) to a v2 hint
    (see :class:`~liberty.connectors.config.ColumnHint`): ``name`` = ``col_target``; ``dd`` =
    ``col_dd_id`` *only when it differs from* ``name``; ``label`` only when an explicit
    ``col_label`` overrides the dictionary; ``hidden`` when ``col_visible`` reads false;
    ``filter`` when ``col_filter`` reads true; ``format`` only when an explicit ``col_type``
    overrides the dictionary. Table-widget columns take precedence over form-field columns;
    the first occurrence of each ``(query_id, col_target)`` wins, so the per-query list keeps
    ``col_seq`` order.

    **Whitelist behaviour** (v1 parity): when *read_sql_by_qid* gives the SELECT for a query,
    every result column NOT in v1's ``ly_tbl_col`` for that query gets a ``hidden = True``
    hint appended. v1's TableView only showed columns listed in ``ly_tbl_col`` — unlisted
    result columns were hidden. Without this, internal / audit columns the v1 SQL returns
    but the operator never wanted in the grid (e.g. APPS_CTRY_ID, APPS_JDBC on
    SETTINGS_APPLICATIONS) leak into v2.

    Args:
        tbl_col_rows / dlg_col_rows: rows from :func:`liberty.migrations.source.read_column_hints`
            (``query_id``, ``col_target``, ``col_dd_id``, ``col_label``, ``col_seq``,
            ``col_visible``, ``col_type``, ``col_filter``, ``col_id``).
        extra_filter_cols: optional ``{query_id: [col_target, …]}`` from
            :func:`migrate_drill_filter_columns` — every named column is forced to
            ``filter = True`` on its destination's read query, regardless of v1's
            ``col_filter`` flag. A column with no existing hint gets a minimal one
            (``{name, filter}``).
        read_sql_by_qid: optional ``{query_id: read_sql}`` — when given, drives the
            whitelist hide step above. Skipped for queries with no ly_tbl_col hints at
            all (preserves "no whitelist → show every column" for connectors-only setups).
    """
    out: dict[int, list[dict[str, Any]]] = {}
    seen: set[tuple[int, str]] = set()
    # Queries that had at least one ly_tbl_col row — these are "whitelisted" screens.
    # Queries with no tbl_col rows AT ALL keep v1's "show every result column" behaviour.
    tbl_qids: set[int] = set()
    for r in tbl_col_rows:
        qid_raw = r.get("query_id")
        if qid_raw is not None:
            try:
                tbl_qids.add(int(qid_raw))
            except (TypeError, ValueError):
                pass
    for r in (*tbl_col_rows, *dlg_col_rows):
        qid_raw = r.get("query_id")
        target = str(r.get("col_target") or "").strip()
        if qid_raw is None or not target:
            continue
        qid = int(qid_raw)
        key = (qid, target.lower())
        if key in seen:
            continue
        seen.add(key)
        hint: dict[str, Any] = {"name": target}
        dd = str(r.get("col_dd_id") or "").strip()
        if dd and dd != target:
            hint["dd"] = dd
        col_label = str(r.get("col_label") or "").strip()
        if col_label and col_label.lower() != target.lower():
            hint["label"] = col_label  # explicit per-column override of the dictionary
        if str(r.get("col_visible") or "").strip() in _HIDDEN_FLAGS:
            hint["hidden"] = True
        if str(r.get("col_filter") or "").strip() in _YES_FLAGS:
            hint["filter"] = True  # surface this column in the TableView filter panel (v1 col_filter)
        fmt = _column_format(r.get("col_type"))
        if fmt:
            hint["format"] = fmt  # explicit per-column override of the dictionary's format
        out.setdefault(qid, []).append(hint)

    # v1 whitelist: hide result columns the operator never listed in ly_tbl_col. Skipped
    # for queries with no tbl_col rows at all (no whitelist intent → show everything).
    if read_sql_by_qid:
        for qid in tbl_qids:
            sql = read_sql_by_qid.get(qid)
            if not sql:
                continue
            result_cols = _outermost_select_columns(sql)
            if not result_cols:
                continue
            hints = out.setdefault(qid, [])
            listed = {str(h["name"]).lower() for h in hints}
            for col in result_cols:
                if col.lower() in listed:
                    continue
                hints.append({"name": col, "hidden": True})
                listed.add(col.lower())

    # Force `filter = True` on each drill-target column — these are the column names the
    # v1 context menu uses as `flt_target` (the param on the destination), so the URL drill
    # (`?COL=value` from ResultTable's NavigateAction) actually lands in the destination's
    # filter panel. A column with no existing hint gets a minimal `{name, filter}` row, so
    # `_wrap_with_filters` still picks it up and binds `:COL` server-side.
    if extra_filter_cols:
        for qid, cols in extra_filter_cols.items():
            try:
                qid_i = int(qid)
            except (TypeError, ValueError):
                continue
            hints = out.setdefault(qid_i, [])
            by_lower = {str(h["name"]).lower(): h for h in hints}
            for c in cols:
                cn = str(c or "").strip()
                if not cn:
                    continue
                existing = by_lower.get(cn.lower())
                if existing is not None:
                    existing["filter"] = True
                else:
                    new = {"name": cn, "filter": True}
                    hints.append(new)
                    by_lower[cn.lower()] = new
    return out


def migrate_drill_filter_columns(
    val_rows: Iterable[Mapping[str, Any]],
    filter_rows: Iterable[Mapping[str, Any]] = (),
    tables_rows: Iterable[Mapping[str, Any]] = (),
    dlg_frm_rows: Iterable[Mapping[str, Any]] = (),
    tbl_col_rows: Iterable[Mapping[str, Any]] = (),
    dlg_col_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[int, list[str]]:
    """``{query_id: [col_target, …]}`` — for every v1 context-menu drill, the columns on the
    *destination* read query that the drill binds (``ly_ctx_filters.flt_target``). Fed into
    :func:`migrate_column_hints` (as ``extra_filter_cols``) so each becomes ``filter = True``
    on the destination, which makes :func:`migrate_sql_queries`' ``_wrap_with_filters`` actually
    bind ``:COL`` server-side. Without this, the frontend's URL drill (``/sql/{c}/{q}?COL=value``,
    emitted by :func:`migrate_context_menus` as a NavigateAction) would land in a TableView with
    no filter slot and the value would be silently dropped.

    Both ``flt_type='DD'`` (dynamic — copied from the firing row) and ``flt_type='VALUE'`` (static
    literal from the menu item) are treated the same: in both cases the destination must accept
    a bind for ``flt_target``. The columns are deduped per query, in first-seen order.

    **Dictionary-key resolution.** ``ly_ctx_filters.flt_target`` is the dictionary key (``dd_id``)
    on the destination, *not* always the SQL column name. v1's framework resolved that via
    ``ly_tbl_col.col_dd_id`` at runtime; v2's wrapper has to bind ``lib_flt.<col_target>`` literally,
    so we translate at migration time: when ``flt_target`` matches an existing column hint's
    ``col_dd_id`` (and *not* an actual ``col_target`` on that query — that hits first), we substitute
    the column's ``col_target``. Without this, queries like ``sod_summary_users_get`` (column
    ``CFD_APPS_ID`` with ``dd_id = APPS_ID``) get wrapped with a bogus ``lib_flt.APPS_ID`` filter
    and the SQL parser errors at run time (502 from the route).

    Args:
        val_rows: rows from ``ly_ctx_val`` — gives us ``(ctx_id, val_id) → val_component,
            val_component_id`` so we can resolve each drill's destination ``query_id``.
        filter_rows: rows from ``ly_ctx_filters`` — ``(ctx_id, val_id, flt_target)``.
        tables_rows: rows from ``ly_tables`` — for ``FormsTable`` items: ``tbl_id → tbl_query_id``.
        dlg_frm_rows: rows from ``ly_dlg_frm`` — for ``FormsDialog`` items: ``frm_id → frm_query_id``.
        tbl_col_rows / dlg_col_rows: rows from ``ly_tbl_col`` / ``ly_dlg_col`` — the column hints
            for each query (``query_id``, ``col_target``, ``col_dd_id``). Used to resolve a
            ``flt_target`` that names a dictionary key rather than a column.
    """
    # Resolve each (ctx_id, val_id) → destination query_id via tbl_qid / frm_qid maps.
    tbl_qid: dict[int, int] = {}
    for r in tables_rows:
        try:
            tbl_id = int(r["tbl_id"])
            qid = r.get("tbl_query_id")
            if qid is not None:
                tbl_qid[tbl_id] = int(qid)
        except (KeyError, TypeError, ValueError):
            continue
    frm_qid: dict[int, int] = {}
    for r in dlg_frm_rows:
        try:
            frm_id = int(r["frm_id"])
            qid = r.get("frm_query_id")
            if qid is not None:
                frm_qid[frm_id] = int(qid)
        except (KeyError, TypeError, ValueError):
            continue
    target_qid_by_val: dict[tuple[int, int], int] = {}
    for v in val_rows:
        ctx_id_raw, val_id_raw = v.get("ctx_id"), v.get("val_id")
        comp = (v.get("val_component") or "").strip()
        comp_id_raw = v.get("val_component_id")
        if ctx_id_raw is None or val_id_raw is None or comp_id_raw is None:
            continue
        try:
            ctx_id, val_id, comp_id = int(ctx_id_raw), int(val_id_raw), int(comp_id_raw)
        except (TypeError, ValueError):
            continue
        target_qid: int | None = None
        if comp == "FormsTable":
            target_qid = tbl_qid.get(comp_id)
        elif comp == "FormsDialog":
            target_qid = frm_qid.get(comp_id)
        if target_qid is not None:
            target_qid_by_val[(ctx_id, val_id)] = target_qid

    # Build per-query maps from the destination's column hints — used to translate `flt_target`
    # when it's a dictionary key rather than a column name. Two maps so the lookup is cheap:
    # `cols_by_qid[qid] = {col_target_lower: col_target}` for "is this already a column?",
    # `dd_by_qid[qid] = {dd_id_lower: col_target}` for "which column has this dictionary key?".
    cols_by_qid: dict[int, dict[str, str]] = {}
    dd_by_qid: dict[int, dict[str, str]] = {}
    for r in (*tbl_col_rows, *dlg_col_rows):
        qid_raw = r.get("query_id")
        col = str(r.get("col_target") or "").strip()
        if qid_raw is None or not col:
            continue
        try:
            qid = int(qid_raw)
        except (TypeError, ValueError):
            continue
        cols_by_qid.setdefault(qid, {}).setdefault(col.lower(), col)
        dd = str(r.get("col_dd_id") or "").strip()
        if dd and dd.lower() != col.lower():
            # First-occurrence wins — if two columns share a dd, the earlier one is used. This
            # matches the v1 ordering (col_seq); ambiguity is rare in practice.
            dd_by_qid.setdefault(qid, {}).setdefault(dd.lower(), col)

    def _resolve(target_qid: int, raw: str) -> str:
        """Translate `raw` (the v1 flt_target) into a SQL column name on the destination."""
        cols = cols_by_qid.get(target_qid, {})
        # 1) If `raw` is already a real column on the destination, use it as-is.
        hit = cols.get(raw.lower())
        if hit:
            return hit
        # 2) If `raw` matches a dictionary key, swap to that column's col_target.
        via_dd = dd_by_qid.get(target_qid, {}).get(raw.lower())
        if via_dd:
            _log.warning(
                "drill filter: destination query %s — `flt_target = %r` matches `col_dd_id` for "
                "column %r; using the column name (without this the wrapper would bind a non-existent column)",
                target_qid, raw, via_dd,
            )
            return via_dd
        # 3) Fall through with a warning — the operator will need to hand-fix this SQL.
        _log.warning(
            "drill filter: destination query %s — `flt_target = %r` doesn't match any known column "
            "or dictionary key on it; emitting as-is (the resulting SQL may fail at run time)",
            target_qid, raw,
        )
        return raw

    out: dict[int, list[str]] = {}
    seen: dict[int, set[str]] = {}
    for r in filter_rows:
        ctx_id_raw, val_id_raw = r.get("ctx_id"), r.get("val_id")
        raw_target = str(r.get("flt_target") or "").strip()
        if ctx_id_raw is None or val_id_raw is None or not raw_target:
            continue
        try:
            ctx_id, val_id = int(ctx_id_raw), int(val_id_raw)
        except (TypeError, ValueError):
            continue
        target_qid = target_qid_by_val.get((ctx_id, val_id))
        if target_qid is None:
            continue
        target = _resolve(target_qid, raw_target)
        s = seen.setdefault(target_qid, set())
        if target.lower() in s:
            continue
        s.add(target.lower())
        out.setdefault(target_qid, []).append(target)
    return out


def migrate_nested_tab_filter_columns(
    dlg_col_rows: Iterable[Mapping[str, Any]],
    dlg_filter_rows: Iterable[Mapping[str, Any]] = (),
    table_rows: Iterable[Mapping[str, Any]] = (),
    dlg_frm_rows: Iterable[Mapping[str, Any]] = (),
    tbl_col_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[int, list[str]]:
    """``{query_id: [col_target, …]}`` — for every nested-tab param-bind whose target column lives
    on a different query (a v1 ``FormsTable`` or ``FormsDialog`` inside a dialog form), the
    columns on that nested query that the bind references.

    **Why this exists.** v1's nested tabs (FormsTable / FormsDialog inside a dialog form) carried
    their param bindings in ``ly_dlg_filters`` — same shape as the field-level lookup binds — but
    the target of each bind is a column on the *nested* query, not the parent's. v2's runtime
    sends the bind as a URL param to the nested ``read_query``, and SQLAlchemy's ``text()`` only
    binds what the SQL actually references. So unless the nested query's column is filter-flagged
    (which causes :func:`migrate_sql_queries`'s ``_wrap_with_filters`` to add the ``:NAME`` +
    ``:NAME_op`` binds), the param value is silently dropped at runtime and the nested table /
    form returns *every row*, not narrowed by the parent's PK.

    Mirrors :func:`migrate_drill_filter_columns` for row-menu drills, but reads from
    ``ly_dlg_filters`` (the field-binds table) keyed by the *dlg_col* carrying the FormsTable /
    FormsDialog widget. The target query_id is resolved via the widget's ``col_component_id``:
    ``FormsTable`` → ``ly_tables.tbl_id → tbl_query_id``; ``FormsDialog`` → ``ly_dlg_frm.frm_id
    → frm_query_id``.

    Dictionary-key resolution is the same: ``ly_dlg_filters.flt_target`` is the dictionary key
    (``dd_id``) on the destination, *not* always the SQL column. We translate at migration time
    when the target's ``col_dd_id`` matches but its ``col_target`` differs (without this, queries
    like ``settings_audit_get`` with column ``AUD_APPS_ID`` and dd ``APPS_ID`` would be wrapped
    with a bogus ``lib_flt.APPS_ID`` filter that points at a non-existent column).

    Args:
        dlg_col_rows: rows from ``ly_dlg_col`` — every field row, including those with
            ``col_component`` set to FormsTable / FormsDialog (the nested-tab widgets).
        dlg_filter_rows: rows from ``ly_dlg_filters`` — the param-bind rules per (frm_id, col_id).
        table_rows: rows from ``ly_tables`` — for ``FormsTable`` widgets: ``tbl_id → tbl_query_id``.
        dlg_frm_rows: rows from ``ly_dlg_frm`` — for ``FormsDialog`` widgets: ``frm_id →
            frm_query_id``.
        tbl_col_rows: rows from ``ly_tbl_col`` — column hints for resolving a ``flt_target`` that
            names a dictionary key (col_dd_id) rather than an SQL column (col_target) on the
            *target* query. Only the destination's column rows matter; ``dlg_col_rows`` is already
            in scope for FormsDialog targets.
    """
    # Resolve the target query_id for each (FormsTable | FormsDialog) widget.
    tbl_qid: dict[int, int] = {}
    for r in table_rows:
        try:
            tid = int(r["tbl_id"]); qid = r.get("tbl_query_id")
            if qid is not None:
                tbl_qid[tid] = int(qid)
        except (KeyError, TypeError, ValueError):
            continue
    frm_qid: dict[int, int] = {}
    for r in dlg_frm_rows:
        try:
            fid = int(r["frm_id"]); qid = r.get("frm_query_id")
            if qid is not None:
                frm_qid[fid] = int(qid)
        except (KeyError, TypeError, ValueError):
            continue
    # (frm_id, col_id) → target_qid, for every nested-widget col.
    target_qid_by_widget: dict[tuple[int, int], int] = {}
    for c in dlg_col_rows:
        comp = (c.get("col_component") or "").strip()
        if comp not in ("FormsTable", "FormsDialog"):
            continue
        try:
            frm = int(c["frm_id"]); col = int(c["col_id"])
            ref = c.get("col_component_id")
            if ref is None:
                continue
            ref_id = int(ref)
        except (KeyError, TypeError, ValueError):
            continue
        if comp == "FormsTable":
            qid = tbl_qid.get(ref_id)
        else:
            qid = frm_qid.get(ref_id)
        if qid is not None:
            target_qid_by_widget[(frm, col)] = qid

    # Build a dd_id → col_target map per target query (using BOTH ly_tbl_col + ly_dlg_col so we
    # resolve targets regardless of whether the destination is a FormsTable or FormsDialog).
    col_by_dd: dict[int, dict[str, str]] = {}            # query_id → {dd_id: col_target}
    cols_by_query: dict[int, set[str]] = {}              # query_id → {col_target.lower(), …}
    for r in (*tbl_col_rows, *dlg_col_rows):
        try:
            qid = int(r["query_id"])
        except (KeyError, TypeError, ValueError):
            continue
        target = (r.get("col_target") or "").strip()
        if target:
            cols_by_query.setdefault(qid, set()).add(target.lower())
        dd = (r.get("col_dd_id") or "").strip()
        if dd and target:
            col_by_dd.setdefault(qid, {}).setdefault(dd, target)

    def _resolve(target_qid: int, raw: str) -> str:
        # If raw names a real column on the target, use it as-is (case-insensitive); else if
        # raw is a dd_id we know maps to a different col_target on this query, substitute.
        if raw.lower() in cols_by_query.get(target_qid, set()):
            return raw
        return col_by_dd.get(target_qid, {}).get(raw, raw)

    # Walk every dlg_filter row whose (frm_id, col_id) belongs to a nested widget.
    out: dict[int, list[str]] = {}
    seen: dict[int, set[str]] = {}
    for f in dlg_filter_rows:
        frm_raw, col_raw = f.get("frm_id"), f.get("col_id")
        raw_target = str(f.get("flt_target") or "").strip()
        if frm_raw is None or col_raw is None or not raw_target:
            continue
        try:
            key = (int(frm_raw), int(col_raw))
        except (TypeError, ValueError):
            continue
        target_qid = target_qid_by_widget.get(key)
        if target_qid is None:
            continue
        target = _resolve(target_qid, raw_target)
        s = seen.setdefault(target_qid, set())
        if target.lower() in s:
            continue
        s.add(target.lower())
        out.setdefault(target_qid, []).append(target)
    return out


def migrate_key_columns(
    tbl_col_rows: Iterable[Mapping[str, Any]],
    dlg_col_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[int, list[str]]:
    """``{query_id: [col_target, …]}`` for the columns flagged ``col_key = 'Y'`` in v1's
    ``ly_tbl_col`` / ``ly_dlg_col`` (the row-identifying columns). Passed to
    :func:`migrate_sql_queries` (as ``key_columns``) — it rewrites the matching ``_put`` query's
    WHERE clause to bind these as ``:<col>_ORIGINAL``. ``ly_query.query_id`` is shared across cruds,
    so a query's key columns (read from its table/form widget) also apply to its ``_put`` variant.
    Table-widget rows take precedence; per ``query_id`` the order follows ``col_seq``, deduped."""
    out: dict[int, list[str]] = {}
    seen: set[tuple[int, str]] = set()
    for r in (*tbl_col_rows, *dlg_col_rows):
        if str(r.get("col_key") or "").strip() not in _YES_FLAGS:
            continue
        qid_raw, target = r.get("query_id"), str(r.get("col_target") or "").strip()
        if qid_raw is None or not target:
            continue
        qid = int(qid_raw)
        if (qid, target.lower()) in seen:
            continue
        seen.add((qid, target.lower()))
        out.setdefault(qid, []).append(target)
    return out


def migrate_table_filters(
    tbl_filter_rows: Iterable[Mapping[str, Any]],
    dlg_filter_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[int, dict[str, list[dict[str, str]]]]:
    """Build ``{query_id: {col_target: [{"source", "column"}, …]}}`` from v1's ``ly_tbl_filters`` /
    ``ly_dlg_filters`` rows (already joined to their column's ``col_target`` and query id by
    :func:`liberty.migrations.source.read_table_filters`). Each rule maps a column's filter dropdown
    to a *source* filter (``flt_source``) and the lookup-result column to match it against
    (``flt_target`` → ``column``). Passed to :func:`migrate_sql_queries` (as ``column_filters``),
    which attaches it to the matching column hint as ``filter_from``. Table-widget rows win over form
    rows for the same ``(query_id, col_target)``; duplicate ``(source, column)`` pairs are dropped.

    Args:
        tbl_filter_rows / dlg_filter_rows: rows with ``query_id``, ``col_target``, ``src``, ``tgt``.
    """
    out: dict[int, dict[str, list[dict[str, str]]]] = {}
    seen: set[tuple[int, str, str, str]] = set()
    seen_cols: set[tuple[int, str]] = set()  # a (query, col) the table-widget side already covered
    for is_table, rows in ((True, tbl_filter_rows), (False, dlg_filter_rows)):
        for r in rows:
            qid_raw = r.get("query_id")
            target = str(r.get("col_target") or "").strip()
            source = str(r.get("src") or "").strip()
            column = str(r.get("tgt") or "").strip()
            if qid_raw is None or not target or not source or not column:
                continue
            qid = int(qid_raw)
            if not is_table and (qid, target.lower()) in seen_cols:
                continue  # the table widget already defined this column's cascading filters
            key = (qid, target.lower(), source.lower(), column.lower())
            if key in seen:
                continue
            seen.add(key)
            if is_table:
                seen_cols.add((qid, target.lower()))
            out.setdefault(qid, {}).setdefault(target, []).append({"source": source, "column": column})
    return out


_CDN_EQUAL_OPS = {"EQUAL", "EQ", "=", "=="}              # operators the migration distils into a value set
_CDN_EMPTY_OPS = {"EMPTY", "ISNULL", "IS NULL", "NULL", "BLANK", ""}  # "or unset" — v2's default, no constraint


def _cdn_to_field_groups(
    cdn_param_rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[int, dict[str, set[str]]], dict[int, list[str]], set[int]]:
    """Parse ``ly_cdn_params`` rows into a usable shape, *once* — both
    :func:`migrate_column_visibility` (grid columns) and :func:`migrate_screens` (dialog fields)
    consume the result.

    Returns ``(values_per_cdn, order_per_cdn, bad_cdn_ids)``:

    * ``values_per_cdn[cid][FIELD] = {v, …}`` — the EQUAL values seen for each field on cid.
      ``FIELD`` is the upper-cased ``cdn_dd_id`` (resolution to a v2 column / field name happens
      at the caller, with its own per-query / per-frm dd→target map).
    * ``order_per_cdn[cid] = [FIELD, …]`` — first-seen order within each cdn, so AND-ed
      predicates emit deterministically.
    * ``bad_cdn_ids`` — cdns that mix in operators v2 can't represent (NOT_EQUAL / LIKE / …);
      the caller leaves the parent column/field unconstrained (always-visible / static flag).
    """
    values: dict[int, dict[str, set[str]]] = {}
    order: dict[int, list[str]] = {}
    bad: set[int] = set()
    for r in cdn_param_rows:
        cid_raw, dd = r.get("cdn_id"), str(r.get("cdn_dd_id") or "").strip()
        if cid_raw is None or not dd:
            continue
        cid = int(cid_raw)
        fld = dd.upper()
        if fld not in values.setdefault(cid, {}):
            values[cid][fld] = set()
            order.setdefault(cid, []).append(fld)
        op = str(r.get("cdn_operator") or "").strip().upper()
        val = r.get("cdn_value")
        sval = "" if val is None else str(val).strip()
        if op in _CDN_EQUAL_OPS:
            if sval:                       # EQUAL '' ≈ EMPTY → contributes nothing
                values[cid][fld].add(sval)
        elif op in _CDN_EMPTY_OPS:
            pass                            # "or unset" — already v2's default for an absent value
        else:
            bad.add(cid)                    # NOT_EQUAL / LIKE / range / … — we don't model these
    return values, order, bad


def _cdn_resolve(
    cid: int,
    values_per_cdn: Mapping[int, Mapping[str, set[str]]],
    order_per_cdn: Mapping[int, list[str]],
    bad_cdn_ids: set[int],
    dd_to_name: Mapping[str, str],
) -> list[dict[str, Any]] | None:
    """Materialise one cdn_id into a list of ``{"field", "value"}`` predicates (AND-ed) using
    a per-context ``dd_to_name`` resolver (column-on-the-same-query for grid, field-on-the-same-frm
    for dialog). Returns ``None`` when ``cid`` is in *bad_cdn_ids* (parent stays unconstrained);
    returns ``[]`` when the cdn parses cleanly but all its predicates are EMPTY-only (v2 default)."""
    if cid in bad_cdn_ids:
        return None
    conds: list[dict[str, Any]] = []
    for fld in order_per_cdn.get(cid, []):
        vals = values_per_cdn.get(cid, {}).get(fld) or set()
        if not vals:
            continue
        field = dd_to_name.get(fld) or fld
        conds.append({"field": field, "value": sorted(vals)})
    return conds


def migrate_column_visibility(
    tbl_col_rows: Iterable[Mapping[str, Any]],
    dlg_col_rows: Iterable[Mapping[str, Any]] = (),
    cdn_param_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[int, dict[str, list[dict[str, Any]]]]:
    """Distil v1's conditional column rendering (``ly_tbl_col.col_cdn_id`` / ``ly_dlg_col.col_cdn_id``
    → ``ly_cdn_params`` predicates) into ``{query_id: {col_target: [{"field", "value"}, …]}}`` — the
    v2 ``ColumnHint.visible_when`` shape.

    v1's condition graph is a general predicate engine (AND/OR groups, EQUAL/EMPTY/… operators).
    v2's ``visible_when`` is the much simpler "this set of filter values keeps the column" — so this
    is **best-effort**, biased toward keeping the column visible:

    * a column has one condition (its ``col_cdn_id``); its predicates group by field. (v1's
      ``ly_tbl_col_cdn`` / ``ly_dlg_col_cdn`` link tables — extra OR branches — aren't read: in the
      shipped apps they only carry redundant/no-op mappings v2 can't represent.)
    * a predicate ``<field> EQUAL <v>`` contributes ``<v>`` to that field's allowed set;
      ``<field> EMPTY`` (or ``EQUAL ''``) contributes nothing (v2 already keeps a column when the
      filter is unset). Any *other* operator (NOT_EQUAL, LIKE, GREATER, …) → the whole column is
      skipped (left always-visible) — a wrong hide is worse than no hide. The emitted conditions are
      AND-ed; a field with no ``EQUAL`` value → not emitted.
    * the predicate's ``cdn_dd_id`` is resolved to a screen-filter column name via the column whose
      ``col_dd_id`` equals it on the same query, else ``cdn_dd_id`` verbatim.

    Args:
        tbl_col_rows / dlg_col_rows: rows from :func:`liberty.migrations.source.read_column_hints`
            (they carry ``col_target``, ``col_dd_id``, ``col_cdn_id``, ``query_id``).
        cdn_param_rows: rows from :func:`liberty.migrations.source.read_column_conditions`
            (``ly_cdn_params``: ``cdn_id``, ``cdn_dd_id``, ``cdn_operator``, ``cdn_value``).
    """
    # 1. parse the cdn graph once (shared with migrate_screens)
    values, order, bad = _cdn_to_field_groups(cdn_param_rows)
    # 2. per query: {col_dd_id(upper): col_target} (for resolving a predicate's field to a screen column)
    dd_index: dict[int, dict[str, str]] = {}
    cols: list[tuple[int, str, int | None]] = []   # (query_id, col_target, col_cdn_id)
    for r in (*tbl_col_rows, *dlg_col_rows):
        q_raw, tgt = r.get("query_id"), str(r.get("col_target") or "").strip()
        if q_raw is None or not tgt:
            continue
        q = int(q_raw)
        cdd = str(r.get("col_dd_id") or "").strip()
        if cdd:
            dd_index.setdefault(q, {}).setdefault(cdd.upper(), tgt)
        cid = r.get("col_cdn_id")
        cols.append((q, tgt, int(cid) if cid is not None else None))
    # 3. resolve each column with a condition
    out: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for q, tgt, cid in cols:
        if cid is None:
            continue
        resolved = _cdn_resolve(cid, values, order, bad, dd_index.get(q, {}))
        if resolved is None:
            _log.warning("migration: column %s.%s uses a condition operator v2 can't model — left always-visible", q, tgt)
            continue
        if resolved:
            out.setdefault(q, {}).setdefault(tgt, resolved)
    return out


def migrate_table_meta(
    tables_rows: Iterable[Mapping[str, Any]],
    dlg_frm_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[int, dict[str, Any]]:
    """Build ``{query_id: {"description"?: str, "auto_load"?: bool}}`` from v1's ``ly_tables`` /
    ``ly_dlg_frm`` rows — the table/form's friendly label (``tbl_label`` / ``frm_label``) becomes the
    v2 query's ``description`` (shown as the screen title instead of the technical query name) and
    ``tbl_auto_load = 'Y'`` becomes ``auto_load = true`` (the TableView runs it on open). A table
    widget wins over a form for the same ``query_id``; the first row for a query wins.

    Args:
        tables_rows: rows from ``ly_tables`` (``tbl_query_id``, ``tbl_label``, ``tbl_auto_load``).
        dlg_frm_rows: rows from ``ly_dlg_frm`` (``frm_query_id``, ``frm_label``).
    """
    out: dict[int, dict[str, Any]] = {}
    for r in dlg_frm_rows:  # forms first so a table widget can overwrite
        qid_raw = r.get("frm_query_id")
        if qid_raw is None:
            continue
        label = str(r.get("frm_label") or "").strip()
        if label:
            out[int(qid_raw)] = {"description": label}
    for r in tables_rows:
        qid_raw = r.get("tbl_query_id")
        if qid_raw is None:
            continue
        qid = int(qid_raw)
        entry: dict[str, Any] = {}
        label = str(r.get("tbl_label") or "").strip()
        if label:
            entry["description"] = label
        if str(r.get("tbl_auto_load") or "").strip() in _YES_FLAGS:
            entry["auto_load"] = True
        # AUD audit (slice 5): tbl_audit = 'Y' → propagate the AUD table name onto the table's
        # writable companion queries (`_put` / `_post` / `_delete`). The SQL connector reads
        # ``QueryDef.audit`` at execute time and mirrors the bound row into ``AUD_<TBL_DB_NAME>``.
        # When tbl_db_name is missing (a v1 screen wired only to a query, no underlying table
        # name) the migrator skips audit — the operator can fill it in via the Connectors builder.
        if str(r.get("tbl_audit") or "").strip() in _YES_FLAGS:
            db_name = str(r.get("tbl_db_name") or "").strip()
            if db_name:
                entry["audit_table"] = f"AUD_{db_name.upper()}"
        if entry:
            out[qid] = entry  # table widget wins; an empty row leaves any form label in place
    return out


def migrate_dictionary(
    dictionary_rows: Iterable[Mapping[str, Any]],
    dictionary_l_rows: Iterable[Mapping[str, Any]] = (),
    enum_rows: Iterable[Mapping[str, Any]] = (),
    enum_val_rows: Iterable[Mapping[str, Any]] = (),
    enum_val_l_rows: Iterable[Mapping[str, Any]] = (),
    lookup_rows: Iterable[Mapping[str, Any]] = (),
    sql_rows: Iterable[Mapping[str, Any]] = (),
    dictionary_filters_rows: Iterable[Mapping[str, Any]] = (),
    lookup_params_rows: Iterable[Mapping[str, Any]] = (),
    sequence_rows: Iterable[Mapping[str, Any]] = (),
    *,
    default_language: str = "en",
    connector_name: str | None = None,
) -> dict[str, Any]:
    """Build the ``dictionary.toml`` dict from v1's ``ly_dictionary`` (+ ``ly_dictionary_l``,
    ``ly_enum``/``ly_enum_val``/``ly_enum_val_l``, ``ly_lookup``).

    One ``[entries.<dd_id>]`` per ``ly_dictionary`` row: ``label`` = ``dd_label``, ``format`` =
    a non-trivial ``dd_type``, ``rules``/``rules_values``/``default`` carried over verbatim, and
    ``[entries.<dd_id>.l]`` = ``{lng_id: lng_label}`` from the ``ly_dictionary_l`` rows. The
    display rules ``dd_rules = "ENUM"`` and ``"LOOKUP"`` reference small data sets that travel
    along as ``[enums.<enum_id>]`` (from ``ly_enum``/``ly_enum_val``, with ``[enums.*.values.l]``
    translations from ``ly_enum_val_l``) and ``[lookups.<lkp_id>]`` (from ``ly_lookup``, with the
    ``lkp_query_id`` resolved through *sql_rows* to the matching read query's v2 name). The
    SQL connector reads them at result time and emits a ``Column.rule`` for the frontend to
    render (see :meth:`DictionaryFile.resolve_rule`). If *connector_name* is given (v1
    dictionaries were per-app), all three sections nest under
    ``[connectors.<connector_name>.{entries,enums,lookups}]`` so two apps don't clash on a
    ``dd_id``/``enum_id``/``lkp_id``.

    Args:
        dictionary_rows: rows from ``ly_dictionary`` (``dd_id``, ``dd_label``, ``dd_type``,
            ``dd_rules``, ``dd_rules_values``, ``dd_default``).
        dictionary_l_rows: rows from ``ly_dictionary_l`` (``dd_id``, ``lng_id``, ``lng_label``).
        enum_rows / enum_val_rows / enum_val_l_rows: rows from ``ly_enum`` / ``ly_enum_val`` /
            ``ly_enum_val_l`` (see :func:`liberty.migrations.source.read_dictionary_rules`).
        lookup_rows: rows from ``ly_lookup`` (``lkp_id``, ``lkp_description``, ``lkp_query_id``,
            ``lkp_dd_id`` — the value column, ``lkp_dd_label`` — the display column, ``lkp_dd_group``).
        sql_rows: rows from ``ly_qry_sql`` joined with ``ly_query`` (``query_id``, ``query_label``,
            ``query_crud``) — resolves ``lkp_query_id`` to the v2 read query name.
        sequence_rows: rows from ``ly_sequence`` (``seq_id``, ``seq_query_id``, ``seq_dd_id``).
            When a dictionary entry's ``dd_rules`` is ``"SEQUENCE"`` / ``"NN"``, the
            ``dd_rules_values`` is the matching ``seq_id``; we translate it to the v2 read
            query name (resolved through *sql_rows* — same logic as lookups). The SQL
            connector runs that named query at INSERT time to fetch the next number.
        default_language: the language of ``ly_dictionary.dd_label`` (v1's base labels) — ``"en"``.
        connector_name: nest the migrated sections under this connector (default: top-level).
    """
    # entries  -------------------------------------------------------------- #
    translations: dict[str, dict[str, str]] = {}
    for r in dictionary_l_rows:
        dd = str(r.get("dd_id") or "").strip()
        lng = str(r.get("lng_id") or "").strip()
        lbl = str(r.get("lng_label") or "").strip()
        if dd and lng and lbl:
            translations.setdefault(dd, {})[lng] = lbl

    # Static lookup-param bindings per dictionary entry — v1's ly_dictionary_filters with
    # flt_type='VALUE'. Other types (FIELD/DD/…) are dynamic and bind at form/table runtime;
    # they're picked up by the table / form migrators when those land. Only VALUE rows live at
    # the dictionary level — that's what feeds DictionaryEntry.lookup_params here.
    lookup_params: dict[str, dict[str, str]] = {}
    for r in dictionary_filters_rows:
        if str(r.get("flt_type") or "").strip().upper() != "VALUE":
            continue
        dd = str(r.get("dd_id") or "").strip()
        target = str(r.get("flt_target") or "").strip()
        value = r.get("flt_value")
        if not dd or not target or value is None or str(value).strip() == "":
            continue
        lookup_params.setdefault(dd, {})[target] = str(value)

    # Pre-scan sql_rows once for the seq_id → v2 query name lookup (same name resolution as
    # :func:`migrate_menus` / the lookup branch below). v1's ``ly_sequence`` rows become a v2
    # ``[sequences.<id>]`` section — first-class entities in the dictionary registry — and
    # dictionary entries with ``dd_rules = "SEQUENCE"`` / ``"NN"`` carry the sequence id (a
    # slug of ``seq_label``) in ``rules_values``. The SQL connector resolves the id to the
    # named query at INSERT time via :meth:`DictionaryFile.find_sequence`.
    _seq_rows_by_id: dict[int, Mapping[str, Any]] = {}
    for s in sequence_rows:
        try:
            sid = int(s["seq_id"])
        except (KeyError, TypeError, ValueError):
            continue
        _seq_rows_by_id[sid] = s
    # Map v1 query_id → (label, read-crud, pool) — the migrated query name = slug(label_crud).
    _q_label_for_seq: dict[int, str] = {}
    _read_crud_for_seq: dict[int, str] = {}
    _q_pool_for_seq: dict[int, str] = {}
    for r in sql_rows:
        qid_raw = r.get("query_id")
        if qid_raw is None:
            continue
        qid_int = int(qid_raw)
        _q_label_for_seq.setdefault(qid_int, str(r.get("query_label") or ""))
        crud = str(r.get("query_crud") or "").upper()
        if crud in _READ_CRUD and qid_int not in _read_crud_for_seq:
            _read_crud_for_seq[qid_int] = crud
        pool = str(r.get("query_pool") or "").strip()
        if pool and qid_int not in _q_pool_for_seq:
            _q_pool_for_seq[qid_int] = pool

    def _resolve_q_name(qid_int: int) -> str | None:
        crud = _read_crud_for_seq.get(qid_int, "GET")
        label = _q_label_for_seq.get(qid_int)
        if not label:
            return None
        return slugify(f"{label}_{crud}", fallback=f"q{qid_int}_{crud.lower()}")

    # Build the migrated ``sequences`` dict. Sequence id is a slug of seq_label so the
    # builder UI shows human-readable names; falls back to ``seq_<sid>`` when label is blank.
    # An orphan sequence (no resolvable query) is dropped — the operator can re-add by hand if
    # needed. ``connector`` is set when the sequence's query lives on a different pool than
    # the migrated app (only meaningful in a multi-app dictionary.toml).
    sequences: dict[str, dict[str, Any]] = {}
    _seq_id_to_v2: dict[int, str] = {}
    _seq_taken: set[str] = set()
    for sid, s in _seq_rows_by_id.items():
        qid_raw = s.get("seq_query_id")
        if qid_raw is None:
            continue
        try:
            qid_int = int(qid_raw)
        except (TypeError, ValueError):
            continue
        q_name = _resolve_q_name(qid_int)
        if q_name is None:
            continue
        # Slug for the v2 sequence id. v1 stored a label like "Get ACT_UKID from SOD_ACTIVITIES";
        # slugify → "get_act_ukid_from_sod_activities". Dedup with a "_2" suffix if needed.
        raw_label = str(s.get("seq_label") or "").strip()
        v2_seq_id = slugify(raw_label, fallback=f"seq_{sid}")
        if v2_seq_id in _seq_taken:
            v2_seq_id = _uniquify(v2_seq_id, _seq_taken)
        _seq_taken.add(v2_seq_id)
        _seq_id_to_v2[sid] = v2_seq_id
        out_seq: dict[str, Any] = {"query": q_name}
        if raw_label:
            out_seq["description"] = raw_label
        # Only emit ``connector`` when it differs from the migrated app the dictionary belongs
        # to (parallels :class:`LookupDef`'s connector field — same logic).
        seq_pool = _q_pool_for_seq.get(qid_int)
        if seq_pool and connector_name and slugify(seq_pool, fallback=seq_pool) != connector_name:
            out_seq["connector"] = slugify(seq_pool, fallback=seq_pool)
        sequences[v2_seq_id] = out_seq

    def _resolve_seq_id(rules_values_raw: str | None) -> str | None:
        """Translate a v1 ``dd_rules_values`` numeric seq_id into the v2 sequence id. Returns
        ``None`` when the value isn't numeric or the sequence wasn't migrated (caller falls
        back to keeping the raw value, same as the prior implementation)."""
        if rules_values_raw is None or rules_values_raw == "":
            return None
        try:
            sid = int(str(rules_values_raw).strip())
        except (TypeError, ValueError):
            return None
        return _seq_id_to_v2.get(sid)

    entries: dict[str, dict[str, Any]] = {}
    for r in dictionary_rows:
        dd = str(r.get("dd_id") or "").strip()
        if not dd:
            continue
        rules = str(r.get("dd_rules") or "").strip() or None
        fmt = _column_format(r.get("dd_type"))
        # v1's `dd_rules = "PASSWORD"` marks a credential column. v2's frontend keys the masked
        # widget (PasswordInput, "leave blank to keep" placeholder, blank-on-submit skip) off the
        # entry's `format = "password"`, not the `rules` field — see ScreenDialog's `isPassword`
        # and SchemaForm's `sub.format === 'password'` branch. Setting both keeps the rule's intent
        # (a future PASSWORD rule could trigger validation / strength meter / etc.) while letting
        # the existing masking path fire automatically — no per-field hand-edit needed.
        if rules == "PASSWORD" and not fmt:
            fmt = "password"
        rules_values = str(r.get("dd_rules_values") or "").strip() or None
        # v1's `dd_rules = "SEQUENCE"` / `"NN"` carries a numeric seq_id in dd_rules_values
        # that points at ``ly_sequence.seq_id``; v2 ports ``ly_sequence`` to a first-class
        # ``[sequences.<id>]`` section and the entry's rules_values becomes the v2 sequence id
        # (a slug of seq_label). The SQL connector resolves it via DictionaryFile.find_sequence
        # at INSERT time. Orphan seq_id → kept verbatim (operator notices the migration warning).
        if rules in ("SEQUENCE", "NN") and rules_values:
            resolved = _resolve_seq_id(rules_values)
            if resolved is not None:
                rules_values = resolved
        entry = _drop_none({
            "label": str(r.get("dd_label") or "").strip() or None,
            "format": fmt,
            "rules": rules,
            "rules_values": rules_values,
            "default": str(r.get("dd_default") or "").strip() or None,
        })
        if dd in translations:
            entry["l"] = translations[dd]
        if dd in lookup_params:
            entry["lookup_params"] = lookup_params[dd]
        entries[dd] = entry
    # entries that exist only as translations (no ly_dictionary row) — keep them too
    for dd, l in translations.items():
        if dd not in entries:
            entries[dd] = {"l": l}

    # enums  ---------------------------------------------------------------- #
    enum_translations: dict[tuple[str, str], dict[str, str]] = {}
    for r in enum_val_l_rows:
        eid = str(r.get("enum_id") or "").strip()
        val = str(r.get("val_enum") or "").strip()
        lng = str(r.get("lng_id") or "").strip()
        lbl = str(r.get("lng_label") or "").strip()
        if eid and val and lng and lbl:
            enum_translations.setdefault((eid, val), {})[lng] = lbl
    enum_values_by_id: dict[str, list[dict[str, Any]]] = {}
    for r in enum_val_rows:
        eid = str(r.get("enum_id") or "").strip()
        val = str(r.get("val_enum") or "").strip()
        if not eid or not val:
            continue
        v: dict[str, Any] = _drop_none({"value": val, "label": str(r.get("val_label") or "").strip() or None})
        if (eid, val) in enum_translations:
            v["l"] = enum_translations[(eid, val)]
        enum_values_by_id.setdefault(eid, []).append(v)
    enums: dict[str, dict[str, Any]] = {}
    for r in enum_rows:
        eid = str(r.get("enum_id") or "").strip()
        if not eid:
            continue
        d: dict[str, Any] = _drop_none({"label": str(r.get("enum_label") or "").strip() or None})
        vals = enum_values_by_id.get(eid)
        if vals:
            d["values"] = vals
        enums[eid] = d
    # enum-values whose enum_id wasn't in ly_enum (orphaned) — preserve them anyway
    for eid, vals in enum_values_by_id.items():
        if eid not in enums:
            enums[eid] = {"values": vals}

    # lookups  -------------------------------------------------------------- #
    # ly_lookup.lkp_query_id → the *read* variant migrate_sql_queries gives that query: same logic
    # as migrate_menus, so both stay in sync.
    query_label: dict[int, str] = {}
    query_pool: dict[int, str] = {}
    read_crud: dict[int, str] = {}
    for r in sql_rows:
        qid_raw = r.get("query_id")
        if qid_raw is None:
            continue
        qid = int(qid_raw)
        query_label.setdefault(qid, str(r.get("query_label") or ""))
        if qid not in query_pool:
            pool = str(r.get("query_pool") or "").strip()
            if pool:
                query_pool[qid] = pool
        c = str(r.get("query_crud") or "").upper()
        if c in _READ_CRUD and qid not in read_crud:
            read_crud[qid] = c
    # Per-lookup parameter names — v1's ly_lkp_params (one row per (lkp_id, dd_id)). Split by
    # ``lkp_dir``: ``IN`` (or null — v1 default) → ``params`` (the :placeholder names the lookup's
    # query expects). ``OUT`` → ``return_params`` (extra dd_ids the picked row writes back to
    # the form, beyond the headline value/label — v2 LookupDef.return_params). Preserve v1 order.
    lkp_param_names: dict[str, list[str]] = {}
    lkp_return_names: dict[str, list[str]] = {}
    for r in lookup_params_rows:
        lid = str(r.get("lkp_id") or "").strip()
        name = str(r.get("dd_id") or "").strip()
        if not lid or not name:
            continue
        direction = str(r.get("lkp_dir") or "").strip().upper()
        target = lkp_return_names if direction == "OUT" else lkp_param_names
        lst = target.setdefault(lid, [])
        if name not in lst:
            lst.append(name)

    lookups: dict[str, dict[str, Any]] = {}
    for r in lookup_rows:
        lid = str(r.get("lkp_id") or "").strip()
        if not lid:
            continue
        value_col = str(r.get("lkp_dd_id") or "").strip()
        label_col = str(r.get("lkp_dd_label") or "").strip()
        qid = r.get("lkp_query_id")
        target: str | None = None
        lkp_connector: str | None = None
        if qid is not None:
            q = int(qid)
            crud = read_crud.get(q, "GET")
            target = slugify(f"{query_label.get(q) or f'q{q}'}_{crud}", fallback=f"q{q}_{crud.lower()}")
            pool = query_pool.get(q)
            if pool:  # the connector the lookup query lives on — may differ from the asking connector
                lkp_connector = slugify(pool, fallback=pool)
        if not value_col or not label_col or not target:
            continue  # an unresolvable lookup — operator will fix it by hand or remove it
        out_lkp = _drop_none({
            "description": str(r.get("lkp_description") or "").strip() or None,
            "connector": lkp_connector,
            "query": target,
            "value": value_col,
            "label": label_col,
            "group": str(r.get("lkp_dd_group") or "").strip() or None,
        })
        if lid in lkp_param_names:
            out_lkp["params"] = lkp_param_names[lid]
        if lid in lkp_return_names:
            out_lkp["return_params"] = lkp_return_names[lid]
        lookups[lid] = out_lkp

    # output  --------------------------------------------------------------- #
    section: dict[str, Any] = {"entries": entries}
    if enums:
        section["enums"] = enums
    if lookups:
        section["lookups"] = lookups
    if sequences:
        section["sequences"] = sequences
    if connector_name:
        return {"default_language": default_language, "connectors": {connector_name: section}}
    return {"default_language": default_language, **section}


# --------------------------------------------------------------------------- #
# Menus  (ly_menus / ly_menus_l → menus.toml)
# --------------------------------------------------------------------------- #

# `menu_component` values that open a query-backed screen (a TableView in v2). Others
# (Dashboard, Chart, …) aren't a query screen — their `menu_component_id` points at a
# different table, so we don't try to resolve them; they become folder placeholders.
_QUERY_COMPONENTS = {"FORMSTABLE", "FORMSGRID", "FORMS", "FORM", "TABLE", "GRID", "TABLEFORM"}
_FORM_COMPONENTS = {"FORMS", "FORM"}  # of those, the ones that resolve via ly_dlg_frm first


def migrate_menus(
    menu_rows: Iterable[Mapping[str, Any]],
    menu_l_rows: Iterable[Mapping[str, Any]] = (),
    tables_rows: Iterable[Mapping[str, Any]] = (),
    dlg_frm_rows: Iterable[Mapping[str, Any]] = (),
    sql_rows: Iterable[Mapping[str, Any]] = (),
    *,
    app_name: str,
    app_label: str | None = None,
) -> dict[str, Any]:
    """Build the ``menus.toml`` dict from v1's ``ly_menus`` (+ ``ly_menus_l``) for one app.

    Produces ``{"menus": {<app_name>: {"label"?: …, "items": [flat menu items]}}}`` — items in
    ``ly_menus`` order (``menu_seq_ukid`` is a sortable dotted path), each linked to its parent
    by ``parent``. A node with a query-backed ``menu_component`` becomes a ``type = "query"``
    leaf whose ``target`` is resolved through ``ly_tables.tbl_id`` / ``ly_dlg_frm.frm_id`` →
    ``ly_query.query_id`` → ``slugify(<query_label>_<read CRUD>)`` — i.e. the exact name
    :func:`migrate_sql_queries` gives that query's *read* variant (``GET`` preferred, then
    ``SELECT``; ``GET`` when the query isn't in *sql_rows*). The connector is left off when it equals
    *app_name* (the default), but spelled out (``connector = "<slug of query_pool>"``) when the query
    lives on a different v2 connector — common since one v1 app can have queries against several pools.
    A node with no component is a folder; one whose component can't be resolved
    (``Dashboard`` etc.) becomes a folder placeholder for the operator to wire up. Item ids are
    slugs of the labels, deduped. (v1's ``ly_menus_filters`` — per-node role/param filters — isn't
    migrated yet; the schema's ``roles``/``params`` are there for hand-editing.)

    Args:
        menu_rows: ``ly_menus`` (``menu_seq_ukid``, ``menu_parent_id``, ``menu_component``,
            ``menu_component_id``, ``menu_label``).
        menu_l_rows: ``ly_menus_l`` (``lng_id``, ``lng_seq_ukid``, ``lng_label``).
        tables_rows / dlg_frm_rows: ``ly_tables`` (``tbl_id`` → ``tbl_query_id``) / ``ly_dlg_frm``
            (``frm_id`` → ``frm_query_id``).
        sql_rows: ``ly_qry_sql`` joined with ``ly_query`` (``query_id``, ``query_label``,
            ``query_crud``) — gives the label and read CRUD that name the migrated query.
        app_name: the connector this menu belongs to (``[menus.<app_name>]``).
        app_label: optional display name for the app (default at render time: the connector name).
    """
    tbl_to_query = {
        int(r["tbl_id"]): int(r["tbl_query_id"])
        for r in tables_rows if r.get("tbl_id") is not None and r.get("tbl_query_id") is not None
    }
    frm_to_query = {
        int(r["frm_id"]): int(r["frm_query_id"])
        for r in dlg_frm_rows if r.get("frm_id") is not None and r.get("frm_query_id") is not None
    }
    query_label: dict[int, str] = {}
    read_crud: dict[int, str] = {}  # query_id → the crud its read variant migrated under (GET / SELECT / READ)
    query_conn: dict[int, str] = {}  # query_id → the v2 connector name (slug of its v1 query_pool)
    for r in sql_rows:
        qid_raw = r.get("query_id")
        if qid_raw is None:
            continue
        qid = int(qid_raw)
        query_label.setdefault(qid, str(r.get("query_label") or ""))
        query_conn.setdefault(qid, slugify(str(r.get("query_pool") or "default").strip() or "default", fallback="default"))
        crud = str(r.get("query_crud") or "").upper()
        if crud in _READ_CRUD and qid not in read_crud:
            read_crud[qid] = crud  # source rows arrive ordered by crud → GET wins over SELECT

    def resolve_query(component: str, component_id: Any) -> tuple[str, int] | None:
        """(migrated query name, query_id) for a query-backed menu component, or None."""
        if component_id is None or component.upper() not in _QUERY_COMPONENTS:
            return None
        cid = int(component_id)
        primary, secondary = (
            (frm_to_query, tbl_to_query) if component.upper() in _FORM_COMPONENTS else (tbl_to_query, frm_to_query)
        )
        qid = primary.get(cid)
        if qid is None:
            qid = secondary.get(cid)  # some v1 menus mix the two up
        if qid is None:
            return None
        crud = read_crud.get(qid, "GET")
        return slugify(f"{query_label.get(qid) or f'q{qid}'}_{crud}", fallback=f"q{qid}_{crud.lower()}"), qid

    translations: dict[str, dict[str, str]] = {}
    for r in menu_l_rows:
        seq = str(r.get("lng_seq_ukid") or "").strip()
        lng = str(r.get("lng_id") or "").strip()
        lbl = str(r.get("lng_label") or "").strip()
        if seq and lng and lbl:
            translations.setdefault(seq, {})[lng] = lbl

    rows = sorted((dict(r) for r in menu_rows), key=lambda r: str(r.get("menu_seq_ukid") or ""))
    seq_to_id: dict[str, str] = {}
    taken: set[str] = set()
    items: list[dict[str, Any]] = []
    for r in rows:
        seq = str(r.get("menu_seq_ukid") or "").strip()
        if not seq:
            continue
        label = str(r.get("menu_label") or "").strip() or seq
        item_id = _uniquify(slugify(label, fallback="item"), taken)
        seq_to_id[seq] = item_id
        parent_seq = str(r.get("menu_parent_id") or "").strip()
        parent_id = seq_to_id.get(parent_seq) if parent_seq and parent_seq != "0" else None
        item: dict[str, Any] = {"id": item_id, "label": label}
        if parent_id:
            item["parent"] = parent_id
        if seq in translations:
            item["l"] = translations[seq]
        resolved = resolve_query(str(r.get("menu_component") or "").strip(), r.get("menu_component_id"))
        if resolved:
            target, qid = resolved
            item["type"] = "query"
            item["target"] = target
            conn = query_conn.get(qid)
            if conn and conn != app_name:  # the query lives on another connector → spell it out
                item["connector"] = conn
        items.append(item)

    app: dict[str, Any] = {"items": items}
    if app_label:
        app["label"] = app_label
    return {"menus": {app_name: app}}


# --------------------------------------------------------------------------- #
# Pools  (ly_applications → [pools.*])
# --------------------------------------------------------------------------- #

_DEFAULT_PORT = {"postgresql": 5432, "oracle": 1521, "mysql": 3306, "mssql": 1433}
# v1 only ever talks Postgres or Oracle; the mysql/mssql rows are here for completeness.
_DRIVER = {
    "postgresql": "postgresql+asyncpg",
    "oracle": "oracle+oracledb",
    "mysql": "mysql+asyncmy",
    "mssql": "mssql+aioodbc",
}
_JDBC_PATTERNS = (
    re.compile(r"jdbc:postgresql://(?P<host>[^:/]+)(?::(?P<port>\d+))?/(?P<db>[^?;]+)", re.I),
    re.compile(r"jdbc:oracle:thin:@//?(?P<host>[^:/]+):(?P<port>\d+)/(?P<db>[^?;]+)", re.I),  # service-name form
    re.compile(r"jdbc:oracle:thin:@(?P<host>[^:/]+):(?P<port>\d+):(?P<db>[^?;]+)", re.I),     # SID form
)


def _pw_placeholder(pool_name: str) -> str:
    """`${MIGRATED_PW_<POOL>}` — used as the pool's ``password`` when v1's ``apps_password`` isn't an
    ``ENC:`` value to carry over; the operator sets the env var (or recovers it via the v1 secret)."""
    return "${MIGRATED_PW_" + pool_name.upper() + "}"


def _db_url(dialect: str, user: str, host: str, port: int, database: str) -> str | None:
    """A SQLAlchemy async URL **without a password** — the password is emitted as the pool's separate
    ``password`` field (so URL-special chars in it never break parsing; see :class:`PoolConfig`)."""
    driver = _DRIVER.get(dialect)
    if not driver:
        return None
    auth = _urlquote(user, safe="")
    if dialect == "oracle":
        return f"{driver}://{auth}@{host}:{port}/?service_name={database}"
    return f"{driver}://{auth}@{host}:{port}/{database}"


def _parse_jdbc(jdbc: str) -> tuple[str, int, str] | None:
    """Best-effort: pull (host, port, database/service) out of a v1 ``apps_jdbc`` string."""
    for rx in _JDBC_PATTERNS:
        m = rx.search(jdbc or "")
        if m:
            return m["host"], int(m["port"]) if m["port"] else 0, m["db"].strip()
    return None


# Which v1 `query_crud` values map to which v2 `_<suffix>` companion on a screen. v1 mixes
# REST verbs (GET/POST/PUT/PATCH/DELETE) with SQL keywords (SELECT/INSERT/UPDATE/MERGE) — the
# migration normalises to the GET/PUT/POST/DELETE quartet that matches the connector builder's
# Tables view + `connectorTables.ts::groupQueriesByTable` on the frontend.
_SCREEN_CRUD_MAP: dict[str, str] = {
    # read — used as `read_query`
    "GET": "GET", "SELECT": "GET", "READ": "GET",
    # update — `update_query`
    "PUT": "PUT", "PATCH": "PUT", "UPDATE": "PUT",
    # insert — `insert_query`
    "POST": "POST", "INSERT": "POST", "MERGE": "POST",
    # delete — `delete_query`
    "DELETE": "DELETE", "REMOVE": "DELETE",
}


def migrate_context_menus(
    ctx_rows: Iterable[Mapping[str, Any]],
    val_rows: Iterable[Mapping[str, Any]] = (),
    filter_rows: Iterable[Mapping[str, Any]] = (),
    tables_rows: Iterable[Mapping[str, Any]] = (),
    dlg_frm_rows: Iterable[Mapping[str, Any]] = (),
    sql_rows: Iterable[Mapping[str, Any]] = (),
    *,
    app_name: str,
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, dict[str, Any]]]:
    """Build ``({tbl_id: [NavigateAction dict, …]}, {tbl_id: promotable-dialog})`` from v1's
    row-context-menu tables (``ly_ctxmenus`` / ``ly_ctx_val`` / ``ly_ctx_filters``).

    The first dict feeds :func:`migrate_screens` as each screen's ``row_menu``. The second is a
    *candidate* for ``Screen.row_click_screen`` promotion: when a screen has no own ``tbl_frm_id``
    and its ctx menu carries a **single** ``FormsDialog`` item, that item is the conventional
    v1 "Display Properties" / "Edit details" action — migrate_screens converts it into a row-click
    target (the row's PK columns bind into the named target screen's read_query, the dialog opens
    as a modal) and drops it from the menu. The shape is ``{action_id, target_qid, binds}`` —
    target_qid is the FormsDialog's resolved v2 query_id and ``action_id`` is the slug
    migrate_context_menus assigned the item so migrate_screens can drop it by id.

    v1 context menus are **shared** — one ``ctx_id`` can be referenced by several
    ``ly_tables.tbl_ctx_id`` rows (e.g. libnsx1 reuses one menu across "Security - Roles" and
    "Audit - Lookup"). v2's current ``Screen.row_menu`` is inline per-screen, so we **copy** the
    resolved menu into each referencing screen — same actions land twice, no sharing on the wire
    but identical behaviour. (Promoting to a shared ``[contextual_menus.<id>]`` pool is a later
    follow-up if real config files start showing redundancy.)

    Each ``ly_ctx_val`` row becomes a ``NavigateAction``:

    * ``id``: ``slugify(val_label)`` (with a ``_2``/``_3`` dedupe inside the same menu)
    * ``label``: ``val_label`` (translations in ``ly_ctx_val_l`` not migrated yet — v2's Action
      union has no per-language label field; slice follow-up)
    * ``to``: the v2 read query name of the target. Resolved by ``val_component``:

      - ``FormsTable`` → ``val_component_id`` is a ``ly_tables.tbl_id``; its ``tbl_query_id``
        + read CRUD verb (GET/SELECT/READ) → the migrated v2 name (same name
        :func:`migrate_sql_queries` emits).
      - ``FormsDialog`` → ``val_component_id`` is a ``ly_dlg_frm.frm_id``; its ``frm_query_id``
        gives the underlying query name. v2 navigates to the destination screen's TableView
        (the dialog opens via row-click once there — closest approximation we can give until
        a dedicated "open dialog" action lands).

    * ``connector``: the slug of that target query's ``query_pool`` — set explicitly so
      cross-pool drills (e.g. NOMAJDE → jdedwards) keep working when the row menu is itself on
      a screen of another connector.
    * ``param_binds``: from ``ly_ctx_filters`` — ``flt_type='DD'`` → ``{param, source}``,
      ``flt_type='VALUE'`` → ``{param, value}``. Same shape as :class:`ParamBind` everywhere
      else in v2.

    Items whose target can't be resolved (orphan ``val_component_id``, missing read CRUD in
    ``ly_qry_sql``) are skipped with a logged warning rather than silently dropped — easier to
    spot in the migration summary.

    Args:
        ctx_rows: rows from ``ly_ctxmenus``.
        val_rows: rows from ``ly_ctx_val`` (menu items, in ``val_seq`` order).
        filter_rows: rows from ``ly_ctx_filters`` (per-item ParamBinds).
        tables_rows: rows from ``ly_tables`` — the table-id → query-id map for FormsTable items
            *and* the ``tbl_ctx_id`` references that drive which tbl_id gets which row_menu.
        dlg_frm_rows: rows from ``ly_dlg_frm`` — the frm-id → query-id map for FormsDialog items.
        sql_rows: rows from ``ly_qry_sql`` joined with ``ly_query`` (query name + pool resolver).
        app_name: only used to detect "same-pool" — a target pool equal to ``app_name`` lets us
            *omit* the connector field from the emitted action (a no-op default), keeping the
            migrated TOML terse.
    """
    # ── resolve query_id → (v2 read query name, pool slug) ──────────────────
    # Mirrors migrate_screens' logic — but only the GET/SELECT/READ companion (a drill target
    # is a *read*, never a write). The v2 name keeps the raw v1 ``query_crud`` verbatim, same
    # as migrate_sql_queries (e.g. v1 SELECT → ``users_list_select``).
    name_by_qid: dict[int, str] = {}
    pool_by_qid: dict[int, str] = {}
    label_by_qid: dict[int, str] = {}
    for r in sql_rows:
        qid = r.get("query_id")
        if qid is None:
            continue
        try:
            qid = int(qid)
        except (TypeError, ValueError):
            continue
        label = (r.get("query_label") or "").strip()
        if label:
            label_by_qid.setdefault(qid, label)
        raw_crud = str(r.get("query_crud") or "").upper()
        our_crud = _SCREEN_CRUD_MAP.get(raw_crud)
        if our_crud != "GET":
            continue
        name_crud = raw_crud or our_crud
        v2_name = slugify(
            f"{label_by_qid.get(qid) or f'q{qid}'}_{name_crud}",
            fallback=f"q{qid}_{name_crud.lower()}",
        )
        name_by_qid.setdefault(qid, v2_name)
        pool = (r.get("query_pool") or "").strip()
        if pool:
            pool_by_qid.setdefault(qid, slugify(pool, fallback=pool))

    # ── tbl_id → query_id (for FormsTable items) ────────────────────────────
    tbl_qid: dict[int, int] = {}
    for r in tables_rows:
        try:
            tbl_id = int(r["tbl_id"])
            qid = r.get("tbl_query_id")
            if qid is not None:
                tbl_qid[tbl_id] = int(qid)
        except (KeyError, TypeError, ValueError):
            continue
    # ── frm_id → query_id (for FormsDialog items) ───────────────────────────
    frm_qid: dict[int, int] = {}
    for r in dlg_frm_rows:
        try:
            frm_id = int(r["frm_id"])
            qid = r.get("frm_query_id")
            if qid is not None:
                frm_qid[frm_id] = int(qid)
        except (KeyError, TypeError, ValueError):
            continue

    # ── group filters by (ctx_id, val_id) → list of bind dicts ──────────────
    binds_by_val: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for r in filter_rows:
        ctx_id_raw, val_id_raw = r.get("ctx_id"), r.get("val_id")
        if ctx_id_raw is None or val_id_raw is None:
            continue
        target_p = (r.get("flt_target") or "").strip()
        if not target_p:
            continue
        ftype = (r.get("flt_type") or "").strip().upper()
        b: dict[str, Any] | None = None
        if ftype == "VALUE":
            v = r.get("flt_value")
            if v is None or str(v).strip() == "":
                continue
            b = {"param": target_p, "value": str(v)}
        elif ftype == "DD":
            src = (r.get("flt_source") or "").strip()
            if not src:
                continue
            b = {"param": target_p, "source": src}
        # Other flt_type values (FIELD / etc.) — Phase 6 follow-up; skip for now.
        if b is None:
            continue
        binds_by_val.setdefault((int(ctx_id_raw), int(val_id_raw)), []).append(b)

    # ── per ctx_id, build the list of NavigateAction dicts ──────────────────
    actions_by_ctx: dict[int, list[dict[str, Any]]] = {}
    # Track FormsDialog candidates per ctx_id so we can decide "exactly one ⇒ promotable" after
    # the loop. Each entry mirrors what migrate_screens needs: the action's slug id (to drop the
    # menu item), the target's resolved v2 query_id (to find the matching v2 screen), and the
    # resolved binds (the parent-row → target-:param mapping).
    dialog_candidates_by_ctx: dict[int, list[dict[str, Any]]] = {}
    for v in val_rows:
        ctx_id_raw, val_id_raw = v.get("ctx_id"), v.get("val_id")
        if ctx_id_raw is None or val_id_raw is None:
            continue
        ctx_id, val_id = int(ctx_id_raw), int(val_id_raw)
        comp = (v.get("val_component") or "").strip()
        comp_id_raw = v.get("val_component_id")
        if comp_id_raw is None:
            continue
        try:
            comp_id = int(comp_id_raw)
        except (TypeError, ValueError):
            continue
        # Resolve the target query
        target_qid: int | None = None
        if comp == "FormsTable":
            target_qid = tbl_qid.get(comp_id)
        elif comp == "FormsDialog":
            target_qid = frm_qid.get(comp_id)
        if target_qid is None:
            _log.warning(
                "migration: context menu %s/%s targets %s id %s — can't resolve to a v2 query, skipping",
                ctx_id, val_id, comp or "(unknown)", comp_id,
            )
            continue
        target_name = name_by_qid.get(target_qid)
        if not target_name:
            _log.warning(
                "migration: context menu %s/%s target query_id %s has no GET/SELECT companion in ly_qry_sql, skipping",
                ctx_id, val_id, target_qid,
            )
            continue
        target_pool = pool_by_qid.get(target_qid)
        label = (v.get("val_label") or "").strip()
        # action id: slug of the label; dedupe within the same ctx menu
        taken = {a["id"] for a in actions_by_ctx.get(ctx_id, [])}
        action_id = _uniquify(slugify(label, fallback=f"val_{val_id}"), taken)
        action: dict[str, Any] = {
            "id": action_id,
            "type": "navigate",
            "to": target_name,
        }
        if label:
            action["label"] = label
        # Spell out `connector` only when it differs from the app — matches the migrate_screens
        # convention (same-pool screens leave the field implicit).
        if target_pool and target_pool != app_name:
            action["connector"] = target_pool
        binds = binds_by_val.get((ctx_id, val_id))
        if binds:
            action["param_binds"] = binds
        actions_by_ctx.setdefault(ctx_id, []).append(action)
        # Record FormsDialog items for the post-loop "single-FormsDialog → promotable" check.
        if comp == "FormsDialog":
            dialog_candidates_by_ctx.setdefault(ctx_id, []).append({
                "action_id": action_id,
                "target_qid": target_qid,
                "binds": list(binds or []),
                "target_pool": target_pool,
            })

    # ── per tbl_id (with tbl_ctx_id set), inline the resolved menu ──────────
    # The lookup table contains *every* ctx_id even when no items resolved — emit an empty list
    # rather than nothing, so the screen migration knows the screen had a menu (even if all
    # items got dropped — the operator sees the empty row_menu in the builder and notices).
    seen_ctx_ids: set[int] = set()
    for c in ctx_rows:
        cid = c.get("ctx_id")
        if cid is not None:
            try:
                seen_ctx_ids.add(int(cid))
            except (TypeError, ValueError):
                pass
    out: dict[int, list[dict[str, Any]]] = {}
    promotable_by_tbl: dict[int, dict[str, Any]] = {}
    for r in tables_rows:
        try:
            tbl_id = int(r["tbl_id"])
        except (KeyError, TypeError, ValueError):
            continue
        cid_raw = r.get("tbl_ctx_id")
        if cid_raw is None:
            continue
        try:
            cid = int(cid_raw)
        except (TypeError, ValueError):
            continue
        if cid not in seen_ctx_ids:
            _log.warning(
                "migration: tbl_id %s references ctx_id %s but no matching ly_ctxmenus row — skipping",
                tbl_id, cid,
            )
            continue
        items = actions_by_ctx.get(cid, [])
        if items:
            out[tbl_id] = items
        # A FormsDialog candidate is promotable when it's the *only* FormsDialog on the ctx menu —
        # if there are several, picking one is ambiguous and the operator should hand-wire via the
        # builder. (We still keep the menu items as-is in that case.)
        dialogs = dialog_candidates_by_ctx.get(cid, [])
        if len(dialogs) == 1:
            promotable_by_tbl[tbl_id] = dialogs[0]
    return out, promotable_by_tbl


def _params_to_prompt_fields(params: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert a v1 action's migrated ``ly_act_params`` rows (as :func:`migrate_actions`
    emits them under ``[migrated_actions.<app>.<slug>].params``) into v2 :class:`PromptField`
    dicts ready to attach to a ``RunQueryAction`` / ``CallApiAction`` / ``NavigateAction``.

    Mapping:

    * ``map_dir = 'OUT'`` → skipped (SP return value, not an input).
    * ``map_display = 'N'`` → ``hidden = True`` (still posted to the action, just not shown).
    * ``map_default`` → ``default``.
    * ``filters`` (the v1 ly_act_params_filters rows already collapsed by ``migrate_actions``
      into ``{param, source}`` / ``{param, value}`` dicts) → ``lookup_param_binds``. v2 only
      uses these when the prompt field's ``dd`` resolves to a LOOKUP rule — but the bind list
      survives so the operator picks a LOOKUP-typed dd and gets the cascading filter for free.

    What we **don't** auto-carry: ``map_rules`` / ``map_rules_values`` (LOOKUP / ENUM / SEQUENCE
    / SYSDATE / …). v1 declared these inline on the param; v2 wires them through the shared
    dictionary via :attr:`PromptField.dd`. Auto-creating dictionary entries on the fly is out
    of scope for this slice — the operator picks the right dd in the builder. The widget defaults
    to plain text until they do.
    """
    out: list[dict[str, Any]] = []
    for p in params:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        if not name:
            continue
        direction = (p.get("direction") or "").strip().upper()
        if direction == "OUT":
            continue
        entry: dict[str, Any] = {"name": name}
        if (p.get("display") or "").strip().upper() == "N":
            entry["hidden"] = True
        default = p.get("default")
        if default not in (None, ""):
            entry["default"] = str(default)
        filters = p.get("filters")
        if isinstance(filters, list):
            binds = [f for f in filters if isinstance(f, dict) and f.get("param")]
            if binds:
                entry["lookup_param_binds"] = [dict(b) for b in binds]
        out.append(entry)
    return out


def migrate_actions(
    action_rows: Iterable[Mapping[str, Any]],
    task_rows: Iterable[Mapping[str, Any]] = (),
    branch_rows: Iterable[Mapping[str, Any]] = (),
    param_rows: Iterable[Mapping[str, Any]] = (),
    task_param_rows: Iterable[Mapping[str, Any]] = (),
    param_filter_rows: Iterable[Mapping[str, Any]] = (),
    sql_rows: Iterable[Mapping[str, Any]] = (),
    *,
    app_name: str,
) -> dict[str, Any]:
    """Dump v1's named-action workflows into a ``[migrated_actions.<app>.<slug>]`` block per
    action. **Not loadable by v2 at runtime** — v2's :class:`Action` discriminated union is flat
    sequential (run_query / call_api / navigate / set_field / confirm / notify / refresh), with
    no branch graph or LOOP construct. v1's actions are richer (IF tasks fork to branch ids,
    LOOP tasks iterate over arrays, action-level params with type rules). The migrator captures
    the v1 shape faithfully as documented TOML so an operator can read each workflow and hand-
    wire the parts v2 supports as ``on_save`` / toolbar action chains in the screen builder.

    libnsx1 has no rows in these tables — toolbar buttons live in v1's frontend code there.
    libnjde carries the 7 NOMAJDE actions: Create Role / Delete Role / Import Security from
    User / Merge Roles / Create User / Delete User / Reset Password.

    Args:
        action_rows: ``ly_actions`` — one per workflow (``act_id``, ``act_label``).
        task_rows: ``ly_act_tasks`` — sequence of tasks per action (``evt_id``, ``evt_seq``,
            ``evt_type``, ``evt_label``, ``evt_query_id``, ``evt_query_crud``, ``evt_api_id``,
            ``evt_brc_id``, ``evt_brc_true``, ``evt_brc_false``, ``evt_loop``, ``evt_loop_array``).
        branch_rows: ``ly_act_branch`` — branch ids → labels (referenced by ``evt_brc_id`` /
            ``evt_brc_true`` / ``evt_brc_false`` on tasks).
        param_rows: ``ly_act_params`` — action-level input params (the workflow's "arguments").
        task_param_rows: ``ly_act_tasks_params`` — per-task param bindings (resolve to an action
            param or a literal). Same shape used everywhere else for parameter passing.
        param_filter_rows: ``ly_act_params_filters`` — filter rules for the input params (the
            value pickers' WHERE binds — same shape as ly_dlg_filters).
        sql_rows: ``ly_qry_sql`` joined with ``ly_query`` — used to resolve ``evt_query_id`` +
            ``evt_query_crud`` to a v2 query name (matching what :func:`migrate_sql_queries`
            emits). An unresolvable task keeps just the v1 ids + a ``warning`` field so the
            operator notices.
        app_name: the app these actions belong to — the TOML block is keyed under it.
    """
    # ── resolve (query_id, raw_crud) → v2 query name (matches migrate_sql_queries) ─────────
    name_by_qid_crud: dict[tuple[int, str], str] = {}
    label_by_qid: dict[int, str] = {}
    for r in sql_rows:
        qid = r.get("query_id")
        if qid is None:
            continue
        try:
            qid = int(qid)
        except (TypeError, ValueError):
            continue
        label = (r.get("query_label") or "").strip()
        if label:
            label_by_qid.setdefault(qid, label)
        raw_crud = str(r.get("query_crud") or "").strip().upper()
        our_crud = _SCREEN_CRUD_MAP.get(raw_crud)
        if not our_crud:
            continue
        # v2 name uses the **raw** crud, same as migrate_sql_queries (so a v1 SELECT becomes
        # `..._select`, not `..._get`).
        name_crud = raw_crud or our_crud
        v2_name = slugify(f"{label_by_qid.get(qid) or f'q{qid}'}_{name_crud}", fallback=f"q{qid}_{name_crud.lower()}")
        name_by_qid_crud.setdefault((qid, raw_crud), v2_name)

    # ── group rows by action ────────────────────────────────────────────────────────────────
    tasks_by_act: dict[int, list[Mapping[str, Any]]] = {}
    for r in task_rows:
        aid = r.get("act_id")
        if aid is None:
            continue
        tasks_by_act.setdefault(int(aid), []).append(r)
    branches_by_act: dict[int, dict[int, str]] = {}    # {act_id: {brc_id: label}}
    for r in branch_rows:
        aid, bid = r.get("act_id"), r.get("brc_id")
        if aid is None or bid is None:
            continue
        label = (r.get("brc_label") or "").strip()
        branches_by_act.setdefault(int(aid), {})[int(bid)] = label
    params_by_act: dict[int, list[Mapping[str, Any]]] = {}
    for r in param_rows:
        aid = r.get("act_id")
        if aid is None:
            continue
        params_by_act.setdefault(int(aid), []).append(r)
    task_params_by_evt: dict[tuple[int, int], list[Mapping[str, Any]]] = {}
    for r in task_param_rows:
        aid, eid = r.get("act_id"), r.get("evt_id")
        if aid is None or eid is None:
            continue
        task_params_by_evt.setdefault((int(aid), int(eid)), []).append(r)
    param_filters_by_var: dict[tuple[int, str], list[Mapping[str, Any]]] = {}
    for r in param_filter_rows:
        aid = r.get("act_id")
        var = (r.get("map_var") or "").strip()
        if aid is None or not var:
            continue
        param_filters_by_var.setdefault((int(aid), var), []).append(r)

    # ── build the TOML dict, action by action ──────────────────────────────────────────────
    actions: dict[str, dict[str, Any]] = {}
    taken: set[str] = set()
    for ar in action_rows:
        try:
            act_id = int(ar["act_id"])
        except (KeyError, TypeError, ValueError):
            continue
        label = (ar.get("act_label") or "").strip()
        sid = _uniquify(slugify(label, fallback=f"action_{act_id}"), taken)
        out: dict[str, Any] = {"id": sid, "v1_act_id": act_id}
        if label:
            out["label"] = label

        # Branches: a flat list of {brc_id, brc_label} so the operator can read the graph.
        branches = branches_by_act.get(act_id, {})
        if branches:
            out["branches"] = [{"id": bid, "label": branches[bid]} for bid in sorted(branches)]

        # Action-level input params — the workflow's "arguments". Carry the v1 metadata verbatim;
        # the operator decides whether each becomes a ScreenField or a constant on the wired action.
        params = params_by_act.get(act_id, [])
        if params:
            params_out: list[dict[str, Any]] = []
            for p in params:
                var = (p.get("map_var") or "").strip()
                if not var:
                    continue
                entry: dict[str, Any] = {"name": var}
                for src, dst in (
                    ("map_dir", "direction"), ("map_display", "display"),
                    ("map_rules", "rules"), ("map_rules_values", "rules_values"),
                    ("map_default", "default"),
                ):
                    v = (p.get(src) or "")
                    if isinstance(v, str):
                        v = v.strip()
                    if v:
                        entry[dst] = v
                # Filter rules (ly_act_params_filters) — same VALUE/DD shape as ly_dlg_filters.
                flts = param_filters_by_var.get((act_id, var), [])
                if flts:
                    fbinds: list[dict[str, Any]] = []
                    for f in flts:
                        ftype = (f.get("flt_type") or "").strip().upper()
                        target_p = (f.get("flt_target") or "").strip()
                        if not target_p:
                            continue
                        if ftype == "VALUE":
                            v = f.get("flt_value")
                            if v is None or str(v).strip() == "":
                                continue
                            fbinds.append({"param": target_p, "value": str(v)})
                        elif ftype == "DD":
                            src_p = (f.get("flt_source") or "").strip()
                            if not src_p:
                                continue
                            fbinds.append({"param": target_p, "source": src_p})
                    if fbinds:
                        entry["filters"] = fbinds
                params_out.append(entry)
            if params_out:
                out["params"] = params_out

        # Tasks: in seq order, each carries the v1 evt_type + label + branch refs + query-or-api
        # resolution. Querying gets ``query = "<v2-name>"`` (when resolvable) so the operator can
        # paste it into a run_query action. An unresolvable task keeps ``v1_query_id`` + a warning.
        tasks_out: list[dict[str, Any]] = []
        for t in tasks_by_act.get(act_id, []):
            try:
                evt_id = int(t["evt_id"])
            except (KeyError, TypeError, ValueError):
                continue
            ttype = (t.get("evt_type") or "").strip().upper()
            task: dict[str, Any] = {"seq": int(t.get("evt_seq") or 0), "v1_evt_id": evt_id}
            if ttype:
                task["type"] = ttype
            tlabel = (t.get("evt_label") or "").strip()
            if tlabel:
                task["label"] = tlabel
            # Branch membership / IF jumps (only present on tasks that have them).
            brc_id = t.get("evt_brc_id")
            if brc_id is not None and str(brc_id).strip() != "":
                try:
                    task["belongs_to_branch"] = int(brc_id)
                except (TypeError, ValueError):
                    pass
            for src, dst in (("evt_brc_true", "on_true_branch"), ("evt_brc_false", "on_false_branch")):
                v = t.get(src)
                if v is not None and str(v).strip() != "":
                    try:
                        task[dst] = int(v)
                    except (TypeError, ValueError):
                        pass
            # LOOP: carry the array source verbatim so the operator can re-implement (v2 has no
            # built-in LOOP; the manual translation is "iterate in the calling code").
            loop = str(t.get("evt_loop") or "").upper()
            if loop in _YES_FLAGS:
                task["loop"] = True
                arr = (t.get("evt_loop_array") or "").strip()
                if arr:
                    task["loop_over"] = arr

            # Resolve the query / api target — same convention as migrate_sql_queries' naming.
            qid_raw = t.get("evt_query_id")
            if qid_raw is not None and str(qid_raw).strip() != "":
                try:
                    qid = int(qid_raw)
                except (TypeError, ValueError):
                    qid = None
                if qid is not None:
                    raw_crud = (t.get("evt_query_crud") or "").strip().upper()
                    v2 = name_by_qid_crud.get((qid, raw_crud))
                    if v2:
                        task["query"] = v2
                    else:
                        task["v1_query_id"] = qid
                        task["v1_query_crud"] = raw_crud or None
                        task["warning"] = f"v1 query_id {qid} (crud={raw_crud!r}) — no matching v2 query in connectors.toml"
            api_id = t.get("evt_api_id")
            if api_id is not None and str(api_id).strip() != "":
                try:
                    task["v1_api_id"] = int(api_id)
                except (TypeError, ValueError):
                    pass

            # Per-task param bindings — value vs source vs reference-to-action-param. The
            # convention varies on v1 (map_type can be DD / VALUE / FIELD / INPUT etc.); we
            # capture the raw shape so the operator can read it.
            tparams = task_params_by_evt.get((act_id, evt_id), [])
            if tparams:
                pb_out: list[dict[str, Any]] = []
                for p in tparams:
                    var = (p.get("map_var") or "").strip()
                    if not var:
                        continue
                    mt = (p.get("map_type") or "").strip().upper()
                    mv = (p.get("map_value") or "").strip()
                    entry: dict[str, Any] = {"param": var}
                    # Treat ``INPUT.<...>`` map_value as a reference to an action-level input param.
                    if mv.upper().startswith("INPUT."):
                        entry["source"] = mv.split(".", 1)[1] or mv
                    elif mt == "DD" and mv:
                        entry["source"] = mv
                    elif mv:
                        entry["value"] = mv
                    if mt and mt not in ("DD", "VALUE"):
                        entry["v1_map_type"] = mt   # FIELD / INPUT / etc. — operator decides
                    pb_out.append(entry)
                if pb_out:
                    task["param_binds"] = pb_out
            tasks_out.append(task)
        if tasks_out:
            out["tasks"] = tasks_out

        actions[sid] = out
    return {"migrated_actions": {app_name: actions}}


def attach_actions_to_screens(
    screens_data: dict[str, Any],
    actions_data: dict[str, Any],
    event_rows: Iterable[Mapping[str, Any]] = (),
    table_rows: Iterable[Mapping[str, Any]] = (),
    *,
    app_name: str,
) -> dict[str, Any]:
    """Attach v1 actions to v2 screens via the **event junction** (``ly_evt_cpt``).

    v1's action attachment lives in ``ly_evt_cpt`` — each row says "event *N* on component
    *C* (a FormsDialog frm or FormsTable tbl) fires action *A*". Two event kinds in libnjde:

    * ``FormsDialog`` evt_id 1 = the dialog's Save → action's tasks become extra writes after
      v2's main update/insert. Maps to ``Screen.dialog.on_save``.
    * ``FormsTable`` evt_id 2 / 3 = a row was inserted / deleted via the table. Currently maps
      to ``Screen.dialog.on_save`` too (the typical NOMAJDE flow is Add Row → dialog opens →
      Save fires the chain). A separate ``Screen.on_insert`` / ``on_delete`` hook for inline
      batch-edit would be a future slice.

    For each event row we resolve the target screen (a v2 screen whose ``tbl_frm_id`` =
    evt_cpt_id for FormsDialog, or whose ``tbl_id`` = evt_cpt_id for FormsTable), build a v2
    Action chain from the action's tasks, and append it to ``dialog.on_save``. **The first
    task is skipped when its query already matches the screen's update/insert query** —
    avoids the duplicate INSERT that would otherwise happen (the dialog Save itself runs
    that query as the main row write; the action chain handles the *additional* related-
    table writes).

    Modifies ``screens_data`` in place and returns it. Idempotent — re-running scrubs any
    prior auto-attached entries (id starting ``migrated_``) before re-attaching, so multiple
    migration runs don't duplicate the chain.

    Previous slice's keyword/base heuristic on ``Screen.actions`` is **gone** — it placed
    buttons on the wrong screens (NOMAJDE's "Create Role" landed on f0092's toolbar instead
    of firing automatically on Save). The cleanup of those prior heuristic entries happens
    here too: any ``Screen.actions`` element whose ``id`` starts with ``migrated_`` gets
    dropped on this run so the screens.toml is clean.
    """
    screens = (screens_data.get("screens") or {}).get(app_name) or {}
    actions = (actions_data.get("migrated_actions") or {}).get(app_name) or {}

    # Cleanup pass — always run, even when no events / no actions: drop every prior auto-
    # attached entry (id starts with ``migrated_``) from each lifecycle hook we own. Re-running
    # the migration is idempotent. Hand-wired entries (without the prefix) survive untouched.
    HOOK_PATHS = [
        ("screen", "actions"),
        ("screen", "on_insert"),
        ("screen", "on_update"),
        ("screen", "on_delete"),
        ("dialog", "on_load"),
        ("dialog", "on_save"),
        ("dialog", "on_cancel"),
    ]
    for scr in screens.values():
        for owner, key in HOOK_PATHS:
            target = scr if owner == "screen" else scr.get("dialog")
            if not isinstance(target, dict) or not isinstance(target.get(key), list):
                continue
            kept = [a for a in target[key] if not (isinstance(a, dict) and isinstance(a.get("id"), str) and a["id"].startswith("migrated_"))]
            if kept:
                target[key] = kept
            else:
                target.pop(key, None)

    if not screens or not actions or not event_rows:
        return screens_data

    # Index: v1 frm_id → v2 screen id (via the v2 screen whose tbl_frm_id matches the frm).
    # Also v1 tbl_id → v2 screen id. Both rely on the v1 ``ly_tables`` rows that ``migrate_screens``
    # consumed.
    sid_by_frm: dict[int, str] = {}
    sid_by_tbl: dict[int, str] = {}
    for r in table_rows:
        try:
            t_id = int(r["tbl_id"])
        except (KeyError, TypeError, ValueError):
            continue
        # Locate the v2 screen for this v1 tbl_id by matching the read_query name. The
        # migration's ``sid_by_tbl_id`` map is internal to ``migrate_screens``; we re-derive
        # it here from the table rows (slugified tbl_db_name / tbl_label) and resolve to a
        # screen that survived the migration (some tbl rows get dropped — e.g. no readable
        # GET). ``migrate_screens`` always emits the same slug for a given (tbl_db_name, tbl_label)
        # in iteration order, so we can re-derive by scanning the already-built screens dict.
        seed = (r.get("tbl_db_name") or r.get("tbl_label") or "").strip().lower()
        # Best-effort: pick the screen whose id starts with the slugified seed.
        for sid in screens:
            if seed and (sid == seed or sid.startswith(f"{seed}_")):
                sid_by_tbl[t_id] = sid
                break
        try:
            frm_id_raw = r.get("tbl_frm_id")
            if frm_id_raw is not None and t_id in sid_by_tbl:
                sid_by_frm[int(frm_id_raw)] = sid_by_tbl[t_id]
        except (TypeError, ValueError):
            pass

    # ── action_id → list of v2 Action dicts ────────────────────────────────────────────────
    # Each v1 task becomes a v2 ``run_query`` (when its target query resolves) or is dropped
    # (API-only / IF / LOOP — those aren't representable in v2's flat Action union yet).
    actions_by_v1_id: dict[int, dict[str, Any]] = {}
    for slug, a in actions.items():
        v1_act_id = a.get("v1_act_id")
        if v1_act_id is None:
            continue
        try:
            actions_by_v1_id[int(v1_act_id)] = a
        except (TypeError, ValueError):
            continue

    def _action_chain(a: Mapping[str, Any], skip_query: str | None = None) -> list[dict[str, Any]]:
        """Convert a migrated v1 action's tasks into a list of v2 ``Action`` dicts. ``skip_query``
        drops the first task whose query matches (used to avoid re-running the screen's own
        update/insert when the dialog's main save is already the action's first step).

        v1 action-level ``ly_act_params`` (the workflow's "arguments") become
        :class:`PromptField`\\ s on the **first emitted task**: the operator fills the inputs
        once, the values feed into the chain's resolution context, and every subsequent task's
        ``ParamBind {source: '<NAME>'}`` reads from the merged prompt values. Output-only
        params (``map_dir = 'OUT'``) are skipped — those are SP returns, not user inputs."""
        v1_act_id = int(a["v1_act_id"])
        slug = a.get("id") or f"action_{v1_act_id}"
        prompt_fields = _params_to_prompt_fields(a.get("params") or [])
        prompt_attached = False
        out: list[dict[str, Any]] = []
        skip_consumed = False
        step = 0
        for t in a.get("tasks") or []:
            q = t.get("query")
            if not q:
                # API / IF / LOOP — not representable in v2's Action union yet. Surface a notify
                # placeholder so the operator notices, then keep going.
                if t.get("type") in {"API"}:
                    out.append({
                        "id": f"migrated_{v1_act_id}_{step}",
                        "type": "notify",
                        "label": t.get("label") or f"{slug} step {t.get('seq', step)}",
                        "message": f"v1 task {t.get('label')!r} uses an API call — see migrated_actions.toml + wire via builder.",
                        "tone": "warn",
                    })
                    step += 1
                continue
            if not skip_consumed and skip_query and q == skip_query:
                skip_consumed = True   # the dialog's main save already runs this
                continue
            entry: dict[str, Any] = {
                "id": f"migrated_{v1_act_id}_{step}",
                "type": "run_query",
                "query": q,
            }
            lbl = t.get("label")
            if lbl:
                entry["label"] = lbl
            pb = t.get("param_binds")
            if pb:
                entry["param_binds"] = list(pb)
            if prompt_fields and not prompt_attached:
                # Attach prompts to the first task only — fires once per chain.
                entry["prompt_fields"] = list(prompt_fields)
                prompt_attached = True
            out.append(entry)
            step += 1
        # Edge case: a chain with prompt_fields but every task either skipped or non-query (API-only).
        # The notify placeholder above carries no prompt_fields (it's a stub variant). The prompt is
        # silently dropped — operator notices via the warning + migrated_actions.toml entry.
        return out

    # ── walk the event junction; attach each action's chain to the right screen ────────────
    # FormsDialog events go on the screen's dialog.on_save; FormsTable events also map there
    # (the typical NOMAJDE flow is Add Row → dialog opens → Save fires the action). Each
    # (screen, hook, act_id) bucket is filled once — dedup so when both a FormsDialog and the
    # matching FormsTable point at the same action, we don't wire the chain twice on the same
    # hook. Different hooks (dialog.on_save vs Screen.on_insert) can carry the same action.
    attached: dict[tuple[str, str], set[int]] = {}
    # Per-event-kind hook mapping. v1's evt_id meanings:
    #   FormsDialog evt 1 = dialog Save
    #   FormsTable  evt 2 = row insert
    #   FormsTable  evt 3 = row delete
    # We map each to a distinct v2 hook so the right chain fires at the right moment — the
    # previous "everything onto dialog.on_save" model double-fired Delete chains on Save.
    HOOK_BY_EVENT: dict[tuple[str, int], tuple[str, str]] = {
        ("FormsDialog", 1): ("dialog_on_save", "screen_actions"),  # primary, fallback
        ("FormsTable", 2):  ("screen_on_insert", "screen_on_insert"),
        ("FormsTable", 3):  ("screen_on_delete", "screen_on_delete"),
    }
    for r in event_rows:
        comp = (r.get("evt_component") or "").strip()
        try:
            cpt_id = int(r["evt_cpt_id"])
            act_id = int(r["evt_act_id"])
            evt_kind = int(r.get("evt_id") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        hook_kinds = HOOK_BY_EVENT.get((comp, evt_kind))
        if hook_kinds is None:
            continue
        if comp == "FormsDialog":
            target_sid = sid_by_frm.get(cpt_id)
        else:
            target_sid = sid_by_tbl.get(cpt_id)
        if not target_sid or target_sid not in screens:
            continue
        primary_hook, fallback_hook = hook_kinds
        if act_id in attached.get((target_sid, primary_hook), set()):
            continue   # already wired on this hook
        action = actions_by_v1_id.get(act_id)
        if not action:
            continue
        scr = screens[target_sid]
        # For dialog save events, skip the first task if it duplicates the screen's main
        # update/insert query (the dialog Save already runs that). Other hooks (on_insert /
        # on_delete) fire AFTER the main row mutation, so the action's full chain runs.
        skip = None
        if primary_hook == "dialog_on_save":
            skip = scr.get("update_query") or scr.get("insert_query")
        chain = _action_chain(action, skip_query=skip)
        if not chain:
            continue
        # Pick the destination by hook + whether a dialog exists. dialog.on_save only works
        # if the screen has a dialog; otherwise fall back to Screen.actions so the operator
        # sees a hand-wirable hook (mirrors how row_menu attaches when there's no dialog).
        dlg = scr.get("dialog")
        chosen_hook = primary_hook
        if primary_hook == "dialog_on_save" and not isinstance(dlg, dict):
            chosen_hook = fallback_hook
        if chosen_hook == "dialog_on_save" and isinstance(dlg, dict):
            dlg.setdefault("on_save", []).extend(chain)
        elif chosen_hook == "screen_on_insert":
            scr.setdefault("on_insert", []).extend(chain)
        elif chosen_hook == "screen_on_delete":
            scr.setdefault("on_delete", []).extend(chain)
        else:   # screen_actions fallback
            scr.setdefault("actions", []).extend(chain)
        attached.setdefault((target_sid, chosen_hook), set()).add(act_id)
    return screens_data


def migrate_screens(
    table_rows: Iterable[Mapping[str, Any]],
    dialog_rows: Iterable[Mapping[str, Any]] = (),
    frm_rows: Iterable[Mapping[str, Any]] = (),
    tab_rows: Iterable[Mapping[str, Any]] = (),
    tab_l_rows: Iterable[Mapping[str, Any]] = (),
    col_rows: Iterable[Mapping[str, Any]] = (),
    filter_rows: Iterable[Mapping[str, Any]] = (),
    sql_rows: Iterable[Mapping[str, Any]] = (),
    cdn_param_rows: Iterable[Mapping[str, Any]] = (),
    row_menus: Mapping[int, list[dict[str, Any]]] | None = None,
    promotable_dialogs: Mapping[int, Mapping[str, Any]] | None = None,
    actions_data: Mapping[str, Any] | None = None,
    sequence_rows: Iterable[Mapping[str, Any]] = (),
    column_hints: Mapping[int, list[dict[str, Any]]] | None = None,
    key_columns: Mapping[int, list[str]] | None = None,
    *,
    app_name: str,
) -> dict[str, Any]:
    """Build the ``screens.toml`` dict for one app from v1's table+dialog stack.

    One Screen per ``ly_tables`` row, keyed by the slug of ``tbl_db_name`` (preferred — it's the
    stable name v1 carried for cross-references) or ``tbl_label`` (fallback). Duplicates are
    de-duped with ``_2``/``_3`` suffixes. The screen's CRUD query refs are resolved from
    *sql_rows*: each (query_id, query_crud) yields a v2 query name via the same slugify rule
    :func:`migrate_sql_queries` uses, so the screen's ``read_query`` / ``update_query`` /
    ``insert_query`` / ``delete_query`` *are exactly* the names that already live in
    ``connectors.toml``.

    When ``tbl_frm_id`` is set, the screen also gets a ``dialog`` — tabs from *tab_rows* (in
    ``tab_seq`` order, translations from *tab_l_rows*), fields from *col_rows* (in ``col_seq``
    order, skipping placeholder rows with no ``col_target``), per-field ``visible_when``
    conditions from each field's ``col_cdn_id`` resolved against *cdn_param_rows* (same shape as
    :func:`migrate_column_visibility`, but keyed by ``(frm_id, col_id)`` — the field-resolver
    is each frm's own ``col_dd_id → col_target`` map), and per-field ``lookup_param_binds``
    from *filter_rows* (``flt_type='DD'`` → dynamic ``source``, ``flt_type='VALUE'`` → static
    ``value``). Without ``tbl_frm_id`` the screen is read-only / grid-edit only.

    Args:
        table_rows: ``ly_tables`` — one row per screen.
        dialog_rows: ``ly_dialogs`` — read but not yet used (kept for forward compat / debug).
        frm_rows: ``ly_dlg_frm`` — links a form to its (optional) read query and dialog group.
        tab_rows: ``ly_dlg_tab`` (+ tab_l_rows: ``ly_dlg_tab_l``).
        col_rows: ``ly_dlg_col`` — the form's fields.
        filter_rows: ``ly_dlg_filters`` — per-field parameter bindings for the field's lookup.
        sql_rows: ``ly_qry_sql`` joined with ``ly_query``.
        app_name: the app these screens belong to (matches a connector name). The screen's
            ``connector`` field is set only when the query's pool resolves to a *different*
            connector (e.g. NOMAJDE app with screens on the ``jdedwards`` connector).
    """
    # ── resolve query names per (query_id, our_crud) ──────────────────────────
    # The v2 *name* must match what migrate_sql_queries emits — and that uses the **raw** v1
    # ``query_crud`` verbatim (so a v1 SELECT becomes ``…_select`` in connectors.toml, even
    # though we classify it as a GET on the screen). The *slot* we route the name into
    # (read/update/insert/delete) uses the normalised verb via _SCREEN_CRUD_MAP.
    crud_by_qid: dict[int, dict[str, str]] = {}                     # {qid: {GET/PUT/POST/DELETE: v2_name}}
    pool_by_qid: dict[int, str] = {}                                 # {qid: connector slug}
    label_by_qid: dict[int, str] = {}
    for r in sql_rows:
        qid = r.get("query_id")
        if qid is None:
            continue
        try:
            qid = int(qid)
        except (TypeError, ValueError):
            continue
        label = (r.get("query_label") or "").strip()
        if label:
            label_by_qid.setdefault(qid, label)
        raw_crud = str(r.get("query_crud") or "").upper()
        our_crud = _SCREEN_CRUD_MAP.get(raw_crud)
        if not our_crud:
            continue
        # Build the v2 name from the **raw** crud (matches migrate_sql_queries' naming exactly);
        # an empty raw crud falls back to the normalised slot — same fallback path as migrate_sql_queries.
        name_crud = raw_crud or our_crud
        v2_name = slugify(f"{label_by_qid.get(qid) or f'q{qid}'}_{name_crud}", fallback=f"q{qid}_{name_crud.lower()}")
        crud_by_qid.setdefault(qid, {}).setdefault(our_crud, v2_name)
        pool = (r.get("query_pool") or "").strip()
        if pool:
            pool_by_qid.setdefault(qid, slugify(pool, fallback=pool))

    # ── group dialog children by (frm_id, …) ──────────────────────────────────
    tabs_by_frm: dict[int, list[Mapping[str, Any]]] = {}
    for r in tab_rows:
        frm_id = r.get("frm_id")
        if frm_id is None:
            continue
        tabs_by_frm.setdefault(int(frm_id), []).append(r)
    tab_l_by_key: dict[tuple[int, int], dict[str, str]] = {}        # {(frm_id, tab_id): {lng: lbl}}
    for r in tab_l_rows:
        frm_id, tab_id = r.get("frm_id"), r.get("tab_id")
        lng = (r.get("lng_id") or "").strip()
        lbl = (r.get("lng_label") or "").strip()
        if frm_id is None or tab_id is None or not lng or not lbl:
            continue
        tab_l_by_key.setdefault((int(frm_id), int(tab_id)), {})[lng] = lbl

    cols_by_tab: dict[tuple[int, int], list[Mapping[str, Any]]] = {}
    for r in col_rows:
        frm_id, tab_id = r.get("frm_id"), r.get("tab_id")
        if frm_id is None or tab_id is None:
            continue
        cols_by_tab.setdefault((int(frm_id), int(tab_id)), []).append(r)

    binds_by_col: dict[tuple[int, int], list[Mapping[str, Any]]] = {}  # {(frm_id, col_id): [filter rows]}
    for r in filter_rows:
        frm_id, col_id = r.get("frm_id"), r.get("col_id")
        if frm_id is None or col_id is None:
            continue
        binds_by_col.setdefault((int(frm_id), int(col_id)), []).append(r)

    # Per-frm `col_dd_id(upper) → col_target` index for resolving conditional-render predicates'
    # cdn_dd_id back into a field name on the same form. Built once.
    dd_to_target_by_frm: dict[int, dict[str, str]] = {}
    for r in col_rows:
        frm_id_raw = r.get("frm_id")
        target = str(r.get("col_target") or "").strip()
        dd_id = str(r.get("col_dd_id") or "").strip()
        if frm_id_raw is None or not target or not dd_id:
            continue
        dd_to_target_by_frm.setdefault(int(frm_id_raw), {}).setdefault(dd_id.upper(), target)
    # Parse the cdn graph once — both grid columns (migrate_column_visibility) and dialog fields
    # share the same predicate engine; we just resolve the predicates' `cdn_dd_id` against the
    # current frm's dd→target map instead of the query's column map.
    cdn_values, cdn_order, cdn_bad = _cdn_to_field_groups(cdn_param_rows)

    # Frm → its read query id (for nested ``FormsDialog`` resolution: a tab whose single dlg_col
    # carries ``col_component='FormsDialog'`` with ``col_component_id`` = the nested ``frm_id``).
    # Its CRUD companions are then looked up via ``crud_by_qid``.
    frm_query_by_frm: dict[int, int] = {}
    for r in frm_rows:
        fid_raw = r.get("frm_id")
        qid_raw = r.get("frm_query_id")
        if fid_raw is None or qid_raw is None:
            continue
        try:
            frm_query_by_frm[int(fid_raw)] = int(qid_raw)
        except (TypeError, ValueError):
            pass

    # tbl_id → its v2 connector (for nested ``FormsTable`` resolution — the activity_log /
    # audit_trail tabs of v1's SETTINGS_APPLICATIONS reference another ly_tables row by id;
    # we emit a NestedTableTab pointing at that table's slug, plus the explicit connector
    # when it differs from the parent screen's).
    tbl_pool_by_tbl: dict[int, str] = {}
    for tr in table_rows:
        try:
            ti = int(tr["tbl_id"])
            tq = int(tr["tbl_query_id"])
        except (TypeError, ValueError, KeyError):
            continue
        pool = pool_by_qid.get(tq)
        if pool:
            tbl_pool_by_tbl[ti] = pool

    # Pre-bucket every form's columns regardless of tab — used for migrating the *nested*
    # form's fields (where the nested frm_id's children land on whatever tab_id v1 used,
    # which we don't care about for inline rendering).
    cols_by_frm_all: dict[int, list[Mapping[str, Any]]] = {}
    for r in col_rows:
        fid = r.get("frm_id")
        if fid is None:
            continue
        cols_by_frm_all.setdefault(int(fid), []).append(r)

    # v1 numeric ``seq_id`` → v2 sequence id (slug of ``seq_label``) — same mapping
    # ``migrate_dictionary`` builds for top-level entries. Used when a screen field has
    # ``col_rules = 'SEQUENCE'`` / ``'NN'`` so the runtime can resolve the rule through
    # ``DictionaryFile.find_sequence`` at INSERT time. Lookups + enums need no translation
    # (v2 keeps their numeric v1 IDs verbatim as string keys), only SEQUENCE does.
    _seq_id_to_v2_for_screens: dict[int, str] = {}
    _q_label_by_qid: dict[int, str] = {}
    _q_read_crud_by_qid: dict[int, str] = {}
    for r in sql_rows:
        qid_raw = r.get("query_id")
        if qid_raw is None:
            continue
        qid_int = int(qid_raw)
        _q_label_by_qid.setdefault(qid_int, str(r.get("query_label") or ""))
        crud = str(r.get("query_crud") or "").upper()
        if crud in _READ_CRUD and qid_int not in _q_read_crud_by_qid:
            _q_read_crud_by_qid[qid_int] = crud
    _seq_taken_for_screens: set[str] = set()
    for s in sequence_rows:
        try:
            sid = int(s["seq_id"])
            sqid_raw = s.get("seq_query_id")
            if sqid_raw is None:
                continue
            sqid = int(sqid_raw)
        except (KeyError, TypeError, ValueError):
            continue
        label = str(s.get("seq_label") or "").strip()
        v2_seq_id = slugify(label, fallback=f"seq_{sid}")
        if v2_seq_id in _seq_taken_for_screens:
            v2_seq_id = _uniquify(v2_seq_id, _seq_taken_for_screens)
        _seq_taken_for_screens.add(v2_seq_id)
        _seq_id_to_v2_for_screens[sid] = v2_seq_id

    def _resolve_screen_rule(col_rules: str | None, raw_rv: str | None) -> tuple[str | None, str | None]:
        """Translate a v1 ``col_rules`` / ``col_rules_values`` pair into v2 form. LOOKUP / ENUM /
        BOOLEAN keep their rules_values verbatim (v2 keeps v1's numeric IDs as string keys);
        SEQUENCE / NN need the seq_id → v2 sequence id translation. Returns ``(rules, rules_values)``
        with either side ``None`` when v1 left it blank."""
        rules = (col_rules or "").strip().upper() or None
        rv = (raw_rv or "").strip() or None
        if rules in ("SEQUENCE", "NN") and rv:
            try:
                sid = int(rv)
            except (TypeError, ValueError):
                pass
            else:
                if sid in _seq_id_to_v2_for_screens:
                    rv = _seq_id_to_v2_for_screens[sid]
        return rules, rv

    def _migrate_field_row(
        c: Mapping[str, Any], owning_frm: int,
        *, column_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Build one **layout-only** field dict from a ly_dlg_col row (Phase 2). Display metadata
        (``dd`` / ``label`` / ``format`` / ``rules`` / ``rules_values`` / ``default`` /
        ``lookup_param_binds``) is captured into *column_overrides* under the column's name so
        the caller can merge it onto ``Screen.columns`` — single source of truth, no more
        duplication between dialog form and grid editor.

        Returns ``None`` for placeholder rows (no ``col_target`` — v1 used those for layout).
        ``owning_frm`` threads the right frm-id through the lookup_param_binds + visible_when
        resolvers (parent form ≠ nested form, but the per-frm dd→target map is keyed by frm_id
        either way)."""
        target = (c.get("col_target") or "").strip()
        if not target:
            return None
        field: dict[str, Any] = {"name": target}
        # v1 keeps grid visibility (ly_tbl_col.col_visible) and dialog visibility
        # (ly_dlg_col.col_visible) independent — same for col_disabled and col_required.
        # We emit field overrides only when the dialog's flag DIFFERS from the column's
        # default (which comes from ly_tbl_col via migrate_column_hints). When they match,
        # the field inherits cleanly. When they differ (audit columns hidden in grid but
        # visible in dialog; user/password hidden in grid but visible in dialog), the field
        # explicitly overrides.
        dlg_hidden = str(c.get("col_visible") or "Y").upper() in _HIDDEN_FLAGS
        dlg_disabled = str(c.get("col_disabled") or "").upper() in _YES_FLAGS
        dlg_required = str(c.get("col_required") or "").upper() in _YES_FLAGS
        # Look up the matching column's current grid-side defaults from migrate_column_hints.
        owning_qid = frm_query_by_frm.get(owning_frm)
        col_hidden = False
        col_disabled = False
        col_required = False
        if owning_qid is not None and column_hints:
            for h in column_hints.get(owning_qid, []) or []:
                if str(h.get("name") or "").upper() == target.upper():
                    col_hidden = bool(h.get("hidden"))
                    col_disabled = bool(h.get("disabled"))
                    col_required = bool(h.get("required"))
                    break
        if dlg_hidden != col_hidden:
            field["hidden"] = dlg_hidden
        if dlg_disabled != col_disabled:
            field["disabled"] = dlg_disabled
        if dlg_required != col_required:
            field["required"] = dlg_required
        if c.get("col_colspan") not in (None, 0):
            try:
                field["colspan"] = int(c["col_colspan"])
            except (TypeError, ValueError):
                pass
        # Conditional visibility (v1 col_cdn_id → ly_cdn_params) is per-record (form-state),
        # not per-table — stays on the field.
        cid_raw = c.get("col_cdn_id")
        if cid_raw is not None:
            try:
                cid = int(cid_raw)
            except (TypeError, ValueError):
                cid = None
            if cid is not None:
                resolved = _cdn_resolve(cid, cdn_values, cdn_order, cdn_bad, dd_to_target_by_frm.get(owning_frm, {}))
                if resolved is None:
                    _log.warning(
                        "migration: screen field %s on frm %s uses a condition operator v2 can't model — left unconditionally visible",
                        target, owning_frm,
                    )
                elif resolved:
                    field["visible_when"] = resolved
        # ── per-column display metadata (lands on Screen.columns, not on the field) ───────
        if column_overrides is not None:
            ov: dict[str, Any] = {}
            dd_id = (c.get("col_dd_id") or "").strip()
            if dd_id and dd_id != target:
                ov["dd"] = dd_id
            col_label = (c.get("col_label") or "").strip()
            if col_label:
                ov["label"] = col_label
            default_v = (c.get("col_default") or "").strip()
            if default_v:
                ov["default"] = default_v
            col_type_v = _column_format(c.get("col_type"))
            if col_type_v:
                ov["format"] = col_type_v
            rules_v, rules_values_v = _resolve_screen_rule(
                c.get("col_rules"), c.get("col_rules_values"),
            )
            if rules_v:
                ov["rules"] = rules_v
            if rules_values_v:
                ov["rules_values"] = rules_values_v
            # ``hidden`` / ``disabled`` / ``required`` are emitted on the *field* above (per
            # dlg_col). The column's grid-side defaults come from ``ly_tbl_col`` via
            # ``migrate_column_hints``, kept independent so a column hidden in the grid can
            # still appear in the dialog (audit timestamps are a common case).
            # Field-level lookup parameter bindings (v1 ly_dlg_filters). Same shape as field
            # binds in v1 — same shape as ColumnHint.lookup_param_binds in v2.
            binds: list[dict[str, Any]] = []
            try:
                cid_int = int(c["col_id"])
            except (TypeError, ValueError, KeyError):
                cid_int = None
            if cid_int is not None:
                for f in binds_by_col.get((owning_frm, cid_int), []):
                    target_p = (f.get("flt_target") or "").strip()
                    if not target_p:
                        continue
                    ftype = (f.get("flt_type") or "").strip().upper()
                    if ftype == "VALUE":
                        v = f.get("flt_value")
                        if v is None or str(v).strip() == "":
                            continue
                        binds.append({"param": target_p, "value": str(v)})
                    elif ftype == "DD":
                        src = (f.get("flt_source") or "").strip()
                        if not src:
                            continue
                        binds.append({"param": target_p, "source": src})
            if binds:
                ov["lookup_param_binds"] = binds
            if ov:
                # case-insensitive dedup against the column hints already on the screen — the
                # caller merges this overlay in (dialog overrides win where they diverge).
                column_overrides[target] = ov
        return field

    def _detect_nested(cols_for_tab: list[Mapping[str, Any]]) -> tuple[str | None, int | None, int | None]:
        """If the tab is a v1 nested container — a single dlg_col row whose ``col_component``
        names a child kind (``FormsDialog`` / ``FormsTable``) — return ``(kind, ref_id,
        host_col_id)`` so the caller can read its ly_dlg_filters for the param binds. Returns
        ``(None, None, None)`` for a regular field-list tab."""
        host: Mapping[str, Any] | None = None
        for c in cols_for_tab:
            comp = (c.get("col_component") or "").strip()
            if comp in ("FormsDialog", "FormsTable"):
                # If more than one such row, give up — that's not a shape v2 currently models.
                if host is not None:
                    return None, None, None
                host = c
        if host is None:
            return None, None, None
        try:
            ref = int(host["col_component_id"])
            cid = int(host["col_id"])
        except (TypeError, ValueError, KeyError):
            return None, None, None
        return (host.get("col_component") or "").strip(), ref, cid

    # Pre-index migrated actions by their v1 act_id so the InputAction migration below can
    # look the action up by ``col_component_id``. Empty when no actions are migrated.
    actions_by_v1_id: dict[int, Mapping[str, Any]] = {}
    if actions_data:
        for a in ((actions_data.get("migrated_actions") or {}).get(app_name) or {}).values():
            v1_id = a.get("v1_act_id")
            if v1_id is None:
                continue
            try:
                actions_by_v1_id[int(v1_id)] = a
            except (TypeError, ValueError):
                pass

    def _input_action_to_button(c: Mapping[str, Any]) -> dict[str, Any] | None:
        """Convert a v1 ``ly_dlg_col`` row with ``col_component='InputAction'`` into a single
        v2 Action suitable for a ``FormTab.actions`` button. The button's label = ``col_label``
        (falls back to the action's label); the underlying Action picks the action's first
        task with a v2 query as a ``run_query`` (with param_binds verbatim), or a ``notify``
        placeholder when the action is API-only (NOMAJDE's Reset Password) — same fallback
        the event-driven attach uses for unresolved cases."""
        comp_id_raw = c.get("col_component_id")
        if comp_id_raw is None:
            return None
        try:
            v1_act_id = int(comp_id_raw)
        except (TypeError, ValueError):
            return None
        a = actions_by_v1_id.get(v1_act_id)
        # The column's own label wins (v1 lets the operator override the button label per-tab
        # placement); fall back to the action's label / id when the column carries no label.
        col_label = (c.get("col_label") or "").strip()
        action_label = (a or {}).get("label") if a else None
        button_label = col_label or action_label or f"action_{v1_act_id}"
        # The button's id encodes the col_id so multiple InputActions on the same tab don't
        # collide (the same action could in theory be placed twice with different labels).
        try:
            col_id = int(c.get("col_id") or 0)
        except (TypeError, ValueError):
            col_id = 0
        btn_id = f"input_action_{v1_act_id}_{col_id}"
        if a is None:
            # Action wasn't migrated (e.g. v1 schema had col_component_id pointing nowhere) —
            # emit a notify placeholder so the button still appears with a clear message.
            return {
                "id": btn_id,
                "type": "notify",
                "label": f"{button_label} (unresolved)",
                "message": f"v1 InputAction references missing action {v1_act_id}.",
                "tone": "warn",
            }
        # Find the first task that resolved to a v2 query; that's the button's primary step.
        # Multi-task workflows surface a "(1/N)" hint in the label so the operator notices the
        # full chain isn't wired (see ``migrated_actions.toml`` for the rest).
        tasks = a.get("tasks") or []
        primary = next((t for t in tasks if t.get("query")), None)
        query_count = sum(1 for t in tasks if t.get("query"))
        label = button_label
        if query_count > 1:
            label = f"{button_label} (1/{query_count})"
        if primary:
            entry: dict[str, Any] = {
                "id": btn_id,
                "type": "run_query",
                "label": label,
                "query": primary["query"],
            }
            pb = primary.get("param_binds")
            if pb:
                entry["param_binds"] = list(pb)
            # v1 ``ly_act_params`` → v2 ``prompt_fields``: the manual workflow asks the operator
            # for its inputs before firing (NOMAJDE "Create Role" needs AUUSER/JOBN/MUSE/PID/UPMJ).
            # See :func:`_params_to_prompt_fields` for the mapping caveats — output params drop,
            # inline rules (LOOKUP/ENUM) need a dd in the builder for richer widgets.
            prompts = _params_to_prompt_fields(a.get("params") or [])
            if prompts:
                entry["prompt_fields"] = prompts
            return entry
        # API-only / fully unresolved — notify placeholder pointing the operator at the dump.
        return {
            "id": btn_id,
            "type": "notify",
            "label": f"{button_label} (needs wiring)",
            "message": f"v1 action {a.get('id') or v1_act_id!r} uses API calls — see migrated_actions.toml + wire via the builder.",
            "tone": "warn",
        }

    def _binds_for_col(owning_frm: int, col_id: int) -> list[dict[str, Any]]:
        """Resolve a parent dlg_col's ly_dlg_filters into v2 ParamBind dicts — same shape as
        the field-level binds, but here the binds carry the parent → nested mapping for the
        nested form/table's read query."""
        binds: list[dict[str, Any]] = []
        for f in binds_by_col.get((owning_frm, col_id), []):
            target_p = (f.get("flt_target") or "").strip()
            if not target_p:
                continue
            ftype = (f.get("flt_type") or "").strip().upper()
            if ftype == "VALUE":
                v = f.get("flt_value")
                if v is None or str(v).strip() == "":
                    continue
                binds.append({"param": target_p, "value": str(v)})
            elif ftype == "DD":
                src = (f.get("flt_source") or "").strip()
                if not src:
                    continue
                binds.append({"param": target_p, "source": src})
        return binds

    # ── pre-pass: assign final screen ids per ly_tables row ───────────────────
    # Done first so a nested ``FormsTable`` tab (col_component='FormsTable', col_component_id =
    # another tbl_id) can resolve to the *final* slug of its sibling — even when the sibling
    # appears later in table_rows. Skip logic mirrors the main loop's so we don't reserve a slug
    # we'd abandon (no resolvable read query → no screen).
    sid_by_tbl_id: dict[int, str] = {}
    taken: set[str] = set()
    for r in table_rows:
        try:
            t_id = int(r["tbl_id"])
        except (TypeError, ValueError, KeyError):
            continue
        tq = r.get("tbl_query_id")
        try:
            tq_int = int(tq) if tq is not None else None
        except (TypeError, ValueError):
            tq_int = None
        if tq_int is None or not (crud_by_qid.get(tq_int) or {}).get("GET"):
            continue
        seed = (r.get("tbl_db_name") or r.get("tbl_label") or "").strip()
        sid_by_tbl_id[t_id] = _uniquify(slugify(seed, fallback=f"screen_{t_id}"), taken)

    # ── build one Screen per ly_tables row ────────────────────────────────────
    screens: dict[str, dict[str, Any]] = {}
    # Phase 2 — overrides collected from each nested-form tab's fields. Keyed by the *nested
    # screen's* read-query qid (resolved against ``crud_by_qid``); merged in a post-pass below
    # so the nested screen's ``columns`` picks up the dialog's per-field metadata (dd / label /
    # format / rules / default / lookup_param_binds) even though the dlg_col rows live under
    # the parent screen's frm.
    _nested_overrides_by_qid: dict[int, dict[str, dict[str, Any]]] = {}
    for r in table_rows:
        try:
            tbl_id = int(r["tbl_id"])
        except (TypeError, ValueError, KeyError):
            continue
        tbl_q = r.get("tbl_query_id")
        if tbl_q is None:
            continue
        try:
            tbl_q = int(tbl_q)
        except (TypeError, ValueError):
            continue
        cruds = crud_by_qid.get(tbl_q) or {}
        read_q = cruds.get("GET")
        if not read_q:
            # Without a resolvable read query the screen would be broken at runtime; skip.
            continue

        sid = sid_by_tbl_id[tbl_id]   # set by the pre-pass with the same skip logic

        screen: dict[str, Any] = {
            "id": sid,
            "label": (r.get("tbl_label") or "").strip() or None,
            "description": (r.get("tbl_label") or "").strip() or None,
            "read_query": read_q,
        }
        # Only spell out `connector` when it differs from app_name (matches menus.toml style).
        screen_connector = pool_by_qid.get(tbl_q)
        if screen_connector and screen_connector != app_name:
            screen["connector"] = screen_connector
        for our_crud, key in (("PUT", "update_query"), ("POST", "insert_query"), ("DELETE", "delete_query")):
            if our_crud in cruds:
                screen[key] = cruds[our_crud]

        if str(r.get("tbl_auto_load") or "").upper() in _YES_FLAGS:
            screen["auto_load"] = True
        # Phase 3 — v1's ``tbl_audit = 'Y'`` translates to ``Screen.audit_table = "AUD_<TBL>"``
        # (was QueryDef.audit pre-Phase-3). The screen-level audit table drives the SQL
        # connector's audit mirror via the route layer's thread-through.
        if str(r.get("tbl_audit") or "").upper() in _YES_FLAGS:
            db = (r.get("tbl_db_name") or "").strip()
            if db:
                screen["audit_table"] = f"AUD_{db.upper()}"
        if str(r.get("tbl_editable") or "Y").upper() in _HIDDEN_FLAGS:  # `'N'` → not editable
            screen["editable"] = False
        if str(r.get("tbl_uploadable") or "").upper() in _YES_FLAGS:
            screen["uploadable"] = True
        # Phase 3 — ``key_columns`` (v1 col_key) moved off QueryDef onto Screen. The
        # migration's ``migrate_key_columns`` helper produces ``{tbl_query_id: [col, …]}``
        # which the caller threads in here.
        if key_columns:
            kcs = key_columns.get(tbl_q)
            if kcs:
                screen["key_columns"] = list(kcs)

        # Phase 1 mirror of QueryDef.columns onto the screen. Same hints the read query already
        # carries — copied here so the runtime can shift to ``Screen.columns`` as the source of
        # truth in Phase 2 without re-migrating, and so two screens sharing one read query can
        # diverge their column ordering / labels / hidden sets without forking the SQL. Hints
        # are emitted as plain dicts (the same shape :func:`migrate_column_hints` produces) and
        # round-trip through Pydantic's ``ColumnHint`` validation on load.
        if column_hints:
            hints = column_hints.get(tbl_q)
            if hints:
                screen["columns"] = [dict(h) for h in hints]

        # Phase 2 — per-column overrides extracted from each dialog ly_dlg_col row by
        # ``_migrate_field_row``. Keyed by the column ``name`` (the ``col_target``); merged
        # into the screen's ``columns`` list after the dialog loop so the dialog's per-field
        # metadata (dd / label / format / rules / rules_values / default / lookup_param_binds)
        # lives once, on ``Screen.columns``, instead of being duplicated onto every ScreenField.
        column_overrides: dict[str, dict[str, Any]] = {}

        # Dialog: only when the table widget is wired to a dialog form.
        frm_id_raw = r.get("tbl_frm_id")
        if frm_id_raw is not None:
            try:
                frm_id = int(frm_id_raw)
            except (TypeError, ValueError):
                frm_id = None
            if frm_id is not None and frm_id in tabs_by_frm:
                tabs: list[dict[str, Any]] = []
                seen_tab_ids: set[str] = set()
                for t in tabs_by_frm[frm_id]:
                    raw_tab_id = t.get("tab_id")
                    if raw_tab_id is None:
                        continue
                    tab_v1_id = int(raw_tab_id)
                    tab_label = (t.get("tab_label") or "").strip()
                    tab_v2_id = _uniquify(slugify(tab_label, fallback=f"tab_{tab_v1_id}"), seen_tab_ids)
                    tab_out: dict[str, Any] = {"id": tab_v2_id}
                    if tab_label:
                        tab_out["label"] = tab_label
                    l = tab_l_by_key.get((frm_id, tab_v1_id))
                    if l:
                        tab_out["l"] = l
                    cols_for_tab = cols_by_tab.get((frm_id, tab_v1_id), [])
                    if str(t.get("tab_disable_add") or "").upper() in _YES_FLAGS:
                        tab_out["hide_on_add"] = True
                    if str(t.get("tab_disable_edit") or "").upper() in _YES_FLAGS:
                        tab_out["hide_on_edit"] = True

                    # Per-tab InputAction buttons (v2's port of v1's ``col_component='InputAction'``)
                    # are *cross-cutting*: they can coexist with a nested FormsTable on the same
                    # tab (NOMAJDE Role dialog's "Roles" tab has Import Security + Merge Roles
                    # buttons AND a nested roles-by-user table). Collected up-front so every tab
                    # type — form / nested_form / nested_table — can carry them via the shared
                    # ``_ScreenTabBase.actions`` field.
                    tab_actions: list[dict[str, Any]] = []
                    for c in cols_for_tab:
                        if (c.get("col_component") or "").strip() == "InputAction":
                            btn = _input_action_to_button(c)
                            if btn is not None:
                                tab_actions.append(btn)

                    # Nested-tab detection: a v1 tab whose single dlg_col carries a child-widget
                    # component name (FormsDialog → inline editable form / FormsTable → inline
                    # related-rows table) becomes a v2 NestedFormTab / NestedTableTab. The parent
                    # dlg_col's ly_dlg_filters carry the param binds (parent column → nested
                    # query param). Without this branch the tab would render empty in v2 — the
                    # operator complaint that drove this slice.
                    nested_kind, nested_ref, nested_host_col = _detect_nested(cols_for_tab)
                    nested_handled = False
                    if nested_kind == "FormsDialog" and nested_ref is not None:
                        target_frm = nested_ref
                        target_qid = frm_query_by_frm.get(target_frm)
                        target_cruds = crud_by_qid.get(target_qid) if target_qid is not None else None
                        if target_cruds and target_cruds.get("GET"):
                            tab_out["type"] = "nested_form"
                            tab_out["read_query"] = target_cruds["GET"]
                            for crud_src, dst_key in (("PUT", "update_query"), ("POST", "insert_query")):
                                if crud_src in target_cruds:
                                    tab_out[dst_key] = target_cruds[crud_src]
                            # `connector` only when it differs from the *parent screen's*
                            # effective connector (the screen.connector field, else app_name) —
                            # matches the menus.toml / row-menu convention.
                            target_pool = pool_by_qid.get(target_qid) if target_qid is not None else None
                            effective_parent_conn = screen.get("connector") or app_name
                            if target_pool and target_pool != effective_parent_conn:
                                tab_out["connector"] = target_pool
                            if t.get("tab_cols") not in (None, 0):
                                try:
                                    tab_out["cols"] = int(t["tab_cols"])
                                except (TypeError, ValueError):
                                    pass
                            # Nested fields: every col_row on the *target* frm regardless of tab —
                            # v1 nested forms typically have just one tab and v2 renders them
                            # inline so tab boundaries inside the nested form are irrelevant.
                            nested_fields: list[dict[str, Any]] = []
                            # Nested-form columns belong to the *nested* screen, not the parent;
                            # collect their overrides separately and stash them onto the nested
                            # target screen below (so its ``Screen.columns`` carries them too).
                            # When the nested target isn't a known screen, the overrides simply
                            # don't land anywhere — which is fine, the nested form runs against
                            # the nested screen's own columns at render time.
                            nested_overrides: dict[str, dict[str, Any]] = {}
                            for c in cols_by_frm_all.get(target_frm, []):
                                fr = _migrate_field_row(c, owning_frm=target_frm, column_overrides=nested_overrides)
                                if fr is not None:
                                    nested_fields.append(fr)
                            tab_out["fields"] = nested_fields
                            # Stash for the post-loop merge — keyed by the nested target's tbl_id
                            # (resolved below once all screen ids exist).
                            if nested_overrides:
                                _nested_overrides_by_qid.setdefault(target_qid, {}).update(nested_overrides) if target_qid is not None else None
                            pb = _binds_for_col(frm_id, nested_host_col) if nested_host_col is not None else []
                            if pb:
                                tab_out["param_binds"] = pb
                            nested_handled = True
                    elif nested_kind == "FormsTable" and nested_ref is not None:
                        target_sid = sid_by_tbl_id.get(nested_ref)
                        if target_sid:
                            tab_out["type"] = "nested_table"
                            tab_out["screen"] = target_sid
                            target_pool = tbl_pool_by_tbl.get(nested_ref)
                            effective_parent_conn = screen.get("connector") or app_name
                            if target_pool and target_pool != effective_parent_conn:
                                tab_out["connector"] = target_pool
                            pb = _binds_for_col(frm_id, nested_host_col) if nested_host_col is not None else []
                            if pb:
                                tab_out["param_binds"] = pb
                            nested_handled = True

                    if not nested_handled:
                        # Plain form tab (the default kind). DD cols become fields; InputAction
                        # cols are skipped here (already collected into ``tab_actions`` above).
                        if t.get("tab_cols") not in (None, 0):
                            try:
                                tab_out["cols"] = int(t["tab_cols"])
                            except (TypeError, ValueError):
                                pass
                        fields: list[dict[str, Any]] = []
                        for c in cols_for_tab:
                            if (c.get("col_component") or "").strip() == "InputAction":
                                continue   # already in tab_actions
                            fr = _migrate_field_row(c, owning_frm=frm_id, column_overrides=column_overrides)
                            if fr is not None:
                                fields.append(fr)
                        tab_out["fields"] = fields
                    # ``actions`` lives on every tab kind via _ScreenTabBase — emit it after the
                    # type-specific branch so a nested_form / nested_table tab can carry buttons
                    # too (v1's Role dialog "Roles" tab had a FormsTable + 2 InputActions in one).
                    if tab_actions:
                        tab_out["actions"] = tab_actions
                    # Normalize tab cols ≥ max(field.colspan). v1 effectively inferred the grid
                    # width from the widest colspan and ignored tab_cols when they disagreed (a
                    # tab with tab_cols=1 + a 3-span field rendered as 3 columns). v2 uses
                    # ``cols`` literally as the CSS grid column count, so a colspan exceeding it
                    # forces messy implicit-grid columns at render time. Bump cols up here so
                    # the explicit grid covers every field's span without surprise.
                    tab_fields = tab_out.get("fields") or []
                    if isinstance(tab_fields, list) and tab_fields:
                        max_cs = 1
                        for fr in tab_fields:
                            try:
                                cs = int(fr.get("colspan") or 1)
                            except (TypeError, ValueError):
                                cs = 1
                            if cs > max_cs:
                                max_cs = cs
                        try:
                            cur_cols = int(tab_out.get("cols") or 1)
                        except (TypeError, ValueError):
                            cur_cols = 1
                        if max_cs > cur_cols:
                            tab_out["cols"] = max_cs
                    tabs.append(tab_out)
                if tabs:
                    screen["dialog"] = {"tabs": tabs}

        # Phase 2 — merge each dialog-field's display-metadata overlay onto the screen's
        # ``columns`` list (Screen.columns is single source of truth for dd / label / format /
        # rules / rules_values / default / lookup_param_binds; the ScreenField only carries
        # layout). Order: keep the existing column-hints' order, append new columns at the end.
        # A dialog field for a column the tbl_col list didn't hint at adds a fresh ColumnHint.
        if column_overrides:
            existing = screen.get("columns") or []
            by_name = {h["name"]: h for h in existing if "name" in h}
            for col_name, ov in column_overrides.items():
                if col_name in by_name:
                    # dlg-row metadata wins where it differs (closer to operator intent).
                    by_name[col_name].update(ov)
                else:
                    new_hint = {"name": col_name, **ov}
                    existing.append(new_hint)
                    by_name[col_name] = new_hint
            screen["columns"] = existing

        # Row context menu (slice 6 follow-up) — `tbl_ctx_id` points at a v1 ``ly_ctxmenus`` row
        # whose items :func:`migrate_context_menus` has already resolved to NavigateActions keyed
        # by ``tbl_id``. Inline the list onto the screen; an empty entry is left off so the
        # builder shows "no row-menu actions yet".
        #
        # Drop self-referential entries: v1's contextual menu often carried a "Display Properties"
        # item that opened the same FormsDialog the row click *also* opens. In v2 a row click
        # opens the Screen's own dialog, so a NavigateAction targeting this screen's own
        # ``read_query`` (on the same connector) is now redundant — strip it so the menu only
        # holds genuine drill-aways (other queries / cross-connector navigation).
        if row_menus is not None and tbl_id in row_menus:
            own_query = screen.get("read_query")
            own_conn = screen.get("connector")  # None when same as app_name (implicit)
            items = [
                it for it in row_menus[tbl_id]
                # Same shape as ``NavigateAction.model_dump`` — keys may be absent. A non-navigate
                # action can't be self-referential by definition (no `to`), so it always survives.
                if not (
                    it.get("type") == "navigate"
                    and it.get("to") == own_query
                    and it.get("connector") == own_conn
                )
            ]
            if items:
                screen["row_menu"] = items

        screens[sid] = screen

    # ── post-pass: merge nested-form column overrides onto each nested target screen ─────────
    # Phase 2 — a nested_form tab on screen A points at the nested screen B's read_query; the
    # parent A's dlg_col rows for B's fields carry per-column display metadata that belongs on
    # B's ``columns`` list (so editing the nested form sees the same dd/rule/default as opening
    # screen B standalone). Resolve B's screen id via its read query's qid → tbl_id, then merge.
    if _nested_overrides_by_qid:
        sid_by_qid: dict[int, str] = {}
        for r in table_rows:
            try:
                tq_int = int(r["tbl_query_id"]); ti_int = int(r["tbl_id"])
            except (TypeError, ValueError, KeyError):
                continue
            sid_for_qid = sid_by_tbl_id.get(ti_int)
            if sid_for_qid:
                sid_by_qid.setdefault(tq_int, sid_for_qid)
        for qid, overrides in _nested_overrides_by_qid.items():
            nested_sid = sid_by_qid.get(qid)
            if not nested_sid or nested_sid not in screens:
                continue
            nested_screen = screens[nested_sid]
            nested_cols = nested_screen.get("columns") or []
            by_name = {h["name"]: h for h in nested_cols if "name" in h}
            for col_name, ov in overrides.items():
                if col_name in by_name:
                    by_name[col_name].update(ov)
                else:
                    new_hint = {"name": col_name, **ov}
                    nested_cols.append(new_hint)
                    by_name[col_name] = new_hint
            nested_screen["columns"] = nested_cols

    # ── post-pass: promote a "Display Properties"-style ctx menu item into a row-click target ──
    # When a screen has no own dialog and its v1 ctx menu carries *exactly one* FormsDialog item
    # (the conventional v1 pattern — e.g. NOMASX1 security_users → "Display Properties"), promote
    # that item to ``Screen.row_click_screen`` + ``row_click_binds`` so the row click opens the
    # target screen's dialog as a modal, and drop the now-redundant menu entry. The target must be
    # an *existing* v2 screen with a dialog (and an update_query) — otherwise leave the menu alone.
    if promotable_dialogs:
        # Map v2 read_query name → screen id, for the "find a screen by its read query" lookup.
        # Built once, used to resolve the target qid below.
        sid_by_read_query: dict[str, str] = {}
        for sid, scr in screens.items():
            rq = scr.get("read_query")
            if rq:
                sid_by_read_query[rq] = sid
        for tbl_id_promote, cand in promotable_dialogs.items():
            sid = sid_by_tbl_id.get(int(tbl_id_promote))
            if not sid or sid not in screens:
                continue
            scr = screens[sid]
            if scr.get("dialog"):
                continue  # parent has its own dialog — promotion is a fallback only
            try:
                target_qid = int(cand["target_qid"])
            except (KeyError, TypeError, ValueError):
                continue
            target_get = (crud_by_qid.get(target_qid) or {}).get("GET")
            target_sid = sid_by_read_query.get(target_get) if target_get else None
            if not target_sid:
                # The target query isn't an own v2 screen (maybe the migrator dropped it for lack
                # of CRUD companions). Without a target screen there's nothing to render — leave
                # the ctx menu alone, the operator can wire something via the builder.
                continue
            target_screen = screens[target_sid]
            if not target_screen.get("dialog") or not target_screen.get("update_query"):
                continue
            scr["row_click_screen"] = target_sid
            # `connector` only when it differs from this screen's effective connector
            # (matches the menus / row-menu convention).
            target_pool = cand.get("target_pool")
            own_eff_conn = scr.get("connector") or app_name
            if target_pool and target_pool != own_eff_conn:
                scr["row_click_connector"] = target_pool
            binds = cand.get("binds") or []
            if binds:
                scr["row_click_binds"] = list(binds)
            # Drop the redundant menu item — same id the migrate_context_menus loop assigned.
            action_id = cand.get("action_id")
            if action_id and isinstance(scr.get("row_menu"), list):
                scr["row_menu"] = [a for a in scr["row_menu"] if a.get("id") != action_id]
                if not scr["row_menu"]:
                    del scr["row_menu"]

    return {"screens": {app_name: screens}}


def migrate_pools(
    applications: Iterable[Mapping[str, Any]],
    *,
    db_schemas: Iterable[Mapping[str, Any]] = (),
    connector_prefix: str = "",
) -> dict[str, Any]:
    """Build the ``{pools}`` dict from v1's ``ly_applications`` rows — one
    ``[pools.<slug-of-apps_pool>]`` per app with a real SQLAlchemy async URL when the
    connection details are known (``apps_host``/``apps_port``/``apps_database`` or a
    parseable ``apps_jdbc``), else the ``${LIBERTY_DB_URL_<NAME>}`` env-var stub.

    The DB password is emitted as the pool's separate ``password`` field (kept out of the URL, so
    URL-special chars in it never break parsing — see :class:`PoolConfig`): v1's ``apps_password``
    ``ENC:`` value is carried over **verbatim** (v2 decrypts it at runtime with the crypto master
    key, exactly as v1 reads it from the table), else a ``${MIGRATED_PW_<NAME>}`` env-var stub. v1's
    reserved ``default`` pool — its framework/definition DB — is **skipped**: v2 reserves
    ``[pools.default]`` for v2's own framework DB (the ``ly2_*`` tables). When several apps share a
    pool name, the first wins. ``apps_dbtype`` becomes the pool's explicit ``dialect`` (so v2 picks
    the right per-dialect SQL variant); ``apps_pool_min`` / ``apps_pool_max`` become ``pool_size`` /
    ``max_overflow`` (the burst above ``pool_size``); ``apps_limit`` becomes ``max_rows`` (the pool's
    default SELECT row cap). v1's ``ly_db_schema`` rows (``db_schemas``) become each pool's ``schemas``
    map (``sch_name → sch_target``) — the ``#SCHEMA.<name>#`` placeholder substitution.

    Args:
        applications: rows from ``ly_applications`` (``apps_name``, ``apps_pool``,
            ``apps_dbtype``, ``apps_jdbc``, ``apps_user``, ``apps_host``, ``apps_port``,
            ``apps_database``, ``apps_pool_max``, ``apps_limit``, …).
        db_schemas: rows from ``ly_db_schema`` (``sch_pool``, ``sch_name``, ``sch_target``).
        connector_prefix: prepended to the pool name (e.g. ``"v1_"``).
    """
    # group ly_db_schema by the v2 pool name → {sch_name: sch_target}
    schemas_by_pool: dict[str, dict[str, str]] = {}
    for r in db_schemas:
        pn = f"{connector_prefix}{slugify(str(r.get('sch_pool') or '').strip(), fallback='')}"
        sn, st = str(r.get("sch_name") or "").strip(), str(r.get("sch_target") or "").strip()
        if pn and sn and st:
            schemas_by_pool.setdefault(pn, {})[sn] = st

    pools: dict[str, dict[str, Any]] = {}
    for a in applications:
        name = f"{connector_prefix}{slugify(a.get('apps_pool'), fallback='')}"
        if not name or name == "default":  # v2 reserves [pools.default] for its own framework DB
            continue
        if name in pools:  # several apps can map to the same pool name → first wins
            continue
        dialect = _dialect_name(a.get("apps_dbtype"))
        if dialect == "default":  # no apps_dbtype on the row → leave dialect unset (v2 derives it from the URL)
            dialect = ""
        user = str(a.get("apps_user") or "").strip()
        host = str(a.get("apps_host") or "").strip()
        database = str(a.get("apps_database") or "").strip()

        url: str | None = None
        if user and host and database:
            try:
                port = int(a.get("apps_port") or 0) or _DEFAULT_PORT.get(dialect or "postgresql", 5432)
            except (TypeError, ValueError):
                port = _DEFAULT_PORT.get(dialect or "postgresql", 5432)
            url = _db_url(dialect or "postgresql", user, host, port, database)
        if url is None and user:
            parsed = _parse_jdbc(str(a.get("apps_jdbc") or ""))
            if parsed:
                h, p, d = parsed
                url = _db_url(dialect or "postgresql", user, h, p or _DEFAULT_PORT.get(dialect or "postgresql", 5432), d)

        entry: dict[str, Any] = {"url": url or ("${LIBERTY_DB_URL_" + name.upper() + "}"), "pool_pre_ping": True}
        if url is not None:
            # the DB password as a separate field (kept out of the URL): carry v1's `ENC:` value
            # verbatim — v2 decrypts it at runtime via the crypto master key, exactly as v1 reads it
            # from `apps_password` — else (plaintext / blank) leave a `${MIGRATED_PW_<NAME>}` stub.
            apw = str(a.get("apps_password") or "").strip()
            entry["password"] = apw if apw.startswith("ENC:") else _pw_placeholder(name)
        if dialect:
            entry["dialect"] = dialect
        # v1's apps_pool_min / apps_pool_max → SQLAlchemy's pool_size (kept-open connections) and
        # max_overflow (burst above that). Either may be missing; clamp to sane bounds.
        def _int(v: Any) -> int | None:
            try:
                return int(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None
        pmin, pmax = _int(a.get("apps_pool_min")), _int(a.get("apps_pool_max"))
        if pmin is not None and 0 < pmin <= 100:
            entry["pool_size"] = pmin
        if pmax is not None and 0 < pmax <= 1000:
            base = entry.get("pool_size", 5)  # SQLAlchemy's default pool_size
            entry["max_overflow"] = max(0, pmax - base)
        limit = _int(a.get("apps_limit"))
        if limit is not None and limit > 0:
            entry["max_rows"] = limit  # v1's per-app row cap → the pool's default `max_rows`
        sch = schemas_by_pool.pop(name, None)
        if sch:
            entry["schemas"] = sch  # ly_db_schema → #SCHEMA.<name># substitution
        pools[name] = entry
    # ly_db_schema rows for a pool that has no ly_applications row → scaffold a stub carrying the schemas
    for name, sch in schemas_by_pool.items():
        pools.setdefault(name, {"url": "${LIBERTY_DB_URL_" + name.upper() + "}", "pool_pre_ping": True})["schemas"] = sch
    # The framework pool — used by v2 for its own ly2_* tables when [auth] backend = "db", and
    # as the fallback pool for any connector that doesn't name one. Always emitted so a fresh
    # migration produces a runnable connectors.toml (operator points it at their framework DB
    # via $LIBERTY_DB_URL).
    pools.setdefault("default", {
        "url": "${LIBERTY_DB_URL:-sqlite+aiosqlite:///./liberty.db}",
        "pool_pre_ping": True,
    })
    return {"pools": pools}


# --------------------------------------------------------------------------- #
# API endpoints  (ly_api_conn + ly_api + ly_api_header + ly_api_params)
# --------------------------------------------------------------------------- #

_LEGACY_CONNECTOR = "legacy_api"


def migrate_api(
    conns: Iterable[Mapping[str, Any]],
    apis: Iterable[Mapping[str, Any]],
    headers: Iterable[Mapping[str, Any]] = (),
    params: Iterable[Mapping[str, Any]] = (),
    *,
    connector_prefix: str = "",
) -> dict[str, Any]:
    """Build the ``{connectors}`` dict for the API side.

    Args:
        conns: rows from ``ly_api_conn`` (``conn_id``, ``conn_label``, ``conn_url``,
            ``conn_user``, ``conn_password``).
        apis: rows from ``ly_api`` (``api_id``, ``api_label``, ``api_method``, ``api_url``,
            ``api_body``, ``api_conn_id``, ``api_user``, ``api_password``).
        headers: rows from ``ly_api_header`` (``api_id``, ``hdr_key``, ``hdr_value``).
        params: rows from ``ly_api_params`` (``api_id``, ``map_var``, ``map_value``).
    """
    headers_by_api: dict[int, list[tuple[str, str]]] = {}
    for h in headers:
        headers_by_api.setdefault(int(h["api_id"]), []).append((str(h["hdr_key"]), str(h["hdr_value"])))
    params_by_api: dict[int, list[dict[str, Any]]] = {}
    for p in params:
        params_by_api.setdefault(int(p["api_id"]), []).append(
            _drop_none({"name": slugify(p.get("map_var")), "default": p.get("map_value") or None})
        )

    connectors: dict[str, dict[str, Any]] = {}
    conn_name_by_id: dict[int, str] = {}
    names_per_connector: dict[str, set[str]] = {}
    taken_connector_names: set[str] = set()

    for c in conns:
        cid = int(c["conn_id"])
        name = _uniquify(f"{connector_prefix}{slugify(c.get('conn_label'), fallback=f'conn{cid}')}", taken_connector_names)
        conn_name_by_id[cid] = name
        user = (c.get("conn_user") or "").strip()
        # v1 stores conn_password as an "ENC:" value — carry it over verbatim; v2 decrypts it
        # at runtime via [crypto] master_key. (A plaintext legacy value is carried over as-is.)
        password = (c.get("conn_password") or "").strip() or None
        connectors[name] = _drop_none({
            "type": "api",
            "base_url": (c.get("conn_url") or "").strip(),
            "auth_type": "basic" if (user or password) else "none",
            "auth_username": user or None,
            "auth_password": password,
            "endpoints": [],
        })
        names_per_connector[name] = set()

    for a in apis:
        aid = int(a["api_id"])
        cid = a.get("api_conn_id")
        if cid is not None and int(cid) in conn_name_by_id:
            conn_name = conn_name_by_id[int(cid)]
        else:  # connectionless ly_api → one shared connector with empty base_url
            conn_name = f"{connector_prefix}{_LEGACY_CONNECTOR}"
            if conn_name not in connectors:
                connectors[conn_name] = {"type": "api", "base_url": "", "auth_type": "none", "endpoints": []}
                names_per_connector[conn_name] = set()
        ep_name = _uniquify(slugify(a.get("api_label"), fallback=f"ep{aid}"), names_per_connector[conn_name])
        ep_headers = {k: v for k, v in headers_by_api.get(aid, [])}
        ep_params = params_by_api.get(aid, [])
        connectors[conn_name]["endpoints"].append(
            _drop_none({
                "name": ep_name,
                "label": a.get("api_label") or None,
                "method": str(a.get("api_method") or "GET").upper(),
                "path": (a.get("api_url") or "").strip(),
                "body": (a.get("api_body") or "").strip() or None,
                "headers": ep_headers or None,
                "params": ep_params or None,
            })
        )

    # drop connectors that ended up with no endpoints
    connectors = {n: c for n, c in connectors.items() if c.get("endpoints")}
    return {"connectors": connectors}


# --------------------------------------------------------------------------- #
# merge + render
# --------------------------------------------------------------------------- #


def merge_connectors(*parts: Mapping[str, Any]) -> dict[str, Any]:
    """Combine several ``{pools, connectors}`` dicts; later parts win on name clashes
    (a warning-worthy event the caller can detect by comparing key counts)."""
    pools: dict[str, Any] = {}
    connectors: dict[str, Any] = {}
    for part in parts:
        pools.update(part.get("pools") or {})
        connectors.update(part.get("connectors") or {})
    out: dict[str, Any] = {}
    if pools:
        out["pools"] = pools
    out["connectors"] = connectors
    return out


def render_toml(data: Mapping[str, Any]) -> str:
    """Render a ``{pools, connectors}`` dict as ``connectors.toml`` text."""
    import tomli_w

    return tomli_w.dumps(dict(data))
