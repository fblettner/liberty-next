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

import re
from typing import Any, Iterable, Mapping
from urllib.parse import quote as _urlquote

from liberty.connectors.base import find_bind_params

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
    table_meta: Mapping[int, Mapping[str, Any]] | None = None,
    key_columns: Mapping[int, list[str]] | None = None,
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
        table_meta: ``{query_id: {"description"?, "auto_load"?}}`` (from :func:`migrate_table_meta`) —
            the v1 table/form friendly label → the read query's ``description``, ``tbl_auto_load`` →
            ``auto_load``.
        key_columns: ``{query_id: [col, …]}`` (from :func:`migrate_key_columns`) — the row-key columns
            (v1's ``col_key``); attached to the read query as ``key_columns`` (the TableView's Excel
            import uses them to decide update-vs-insert).
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
        tm = tmeta.get(qid) if is_read else None  # v1 ly_tables: friendly label + auto-load
        kcs = (key_columns or {}).get(qid, []) if is_read else []  # the result's identity (v1 col_key)
        filter_cols = [h["name"] for h in hints if h.get("filter")] if hints else []
        is_update = crud in _UPDATE_CRUD
        # build each dialect variant. read + filter columns → `SELECT * FROM (…) lib_flt WHERE <filters>`.
        # an UPDATE-crud query: an upsert there collapses to a plain UPDATE, then its WHERE is rebound
        # to `:<col>_ORIGINAL`. an INSERT-crud upsert (`_post`) collapses to a plain INSERT. then
        # ORDER BY is appended (to the outer query, when the read was wrapped).
        ob = orderbys.get(key, "")
        def _variant(raw: str, dia: str) -> str:
            if filter_cols:
                s = _wrap_with_filters(raw, filter_cols, dialect=dia)
            elif is_update:
                s = _rewrite_put_where(_upsert_to_update(raw))
            elif is_read:
                s = raw
            else:
                s = _simplify_upsert(raw)
            return f"{s}\nORDER BY {ob}" if ob else s
        variants = {dia: _variant(raw, dia) for dia, raw in groups[key].items()}
        connectors[conn_name]["queries"].append(
            _drop_none({
                "name": _uniquify(base, names_per_connector[conn_name]),
                "label": label or None,
                "description": (tm or {}).get("description") or None,
                "auto_load": True if (tm or {}).get("auto_load") else None,
                "key_columns": (kcs or None),  # omit when empty — used by the Excel-import match-by-key
                "writable": None if is_read else True,  # GET/SELECT → omit (default false); POST/PUT/DELETE/… → writable
                "sql": _sql_value(variants),
                "columns": (hints or None),  # omit when empty
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


def migrate_column_hints(
    tbl_col_rows: Iterable[Mapping[str, Any]],
    dlg_col_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[int, list[dict[str, Any]]]:
    """Build ``{query_id: [column-hint dict]}`` from v1's ``ly_tbl_col`` / ``ly_dlg_col`` rows.

    Each row maps a query's result column (``col_target``) to a v2 hint
    (see :class:`~liberty.connectors.config.ColumnHint`): ``name`` = ``col_target``; ``dd`` =
    ``col_dd_id`` *only when it differs from* ``name`` (when equal — the common case — it's omitted;
    the connector looks the dictionary entry up under the column name); ``label`` only when an
    explicit ``col_label`` overrides the dictionary; ``hidden`` when ``col_visible`` reads false;
    ``filter`` when ``col_filter`` reads true (table widgets only — surfaces the column in the
    TableView filter panel); ``format`` only when an explicit ``col_type`` overrides the dictionary.
    Table-widget columns take precedence over form-field columns; the first occurrence of each
    ``(query_id, col_target)`` wins, so the per-query list keeps ``col_seq`` order. (Labels themselves
    live in the shared dictionary — see :func:`migrate_dictionary`.)

    Args:
        tbl_col_rows / dlg_col_rows: rows from :func:`liberty.migrations.source.read_column_hints`
            (``query_id``, ``col_target``, ``col_dd_id``, ``col_label``, ``col_seq``,
            ``col_visible``, ``col_type``, ``col_filter``, ``col_id``).
    """
    out: dict[int, list[dict[str, Any]]] = {}
    seen: set[tuple[int, str]] = set()
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

    entries: dict[str, dict[str, Any]] = {}
    for r in dictionary_rows:
        dd = str(r.get("dd_id") or "").strip()
        if not dd:
            continue
        entry = _drop_none({
            "label": str(r.get("dd_label") or "").strip() or None,
            "format": _column_format(r.get("dd_type")),
            "rules": str(r.get("dd_rules") or "").strip() or None,
            "rules_values": str(r.get("dd_rules_values") or "").strip() or None,
            "default": str(r.get("dd_default") or "").strip() or None,
        })
        if dd in translations:
            entry["l"] = translations[dd]
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
    read_crud: dict[int, str] = {}
    for r in sql_rows:
        qid_raw = r.get("query_id")
        if qid_raw is None:
            continue
        qid = int(qid_raw)
        query_label.setdefault(qid, str(r.get("query_label") or ""))
        c = str(r.get("query_crud") or "").upper()
        if c in _READ_CRUD and qid not in read_crud:
            read_crud[qid] = c
    lookups: dict[str, dict[str, Any]] = {}
    for r in lookup_rows:
        lid = str(r.get("lkp_id") or "").strip()
        if not lid:
            continue
        value_col = str(r.get("lkp_dd_id") or "").strip()
        label_col = str(r.get("lkp_dd_label") or "").strip()
        qid = r.get("lkp_query_id")
        target: str | None = None
        if qid is not None:
            q = int(qid)
            crud = read_crud.get(q, "GET")
            target = slugify(f"{query_label.get(q) or f'q{q}'}_{crud}", fallback=f"q{q}_{crud.lower()}")
        if not value_col or not label_col or not target:
            continue  # an unresolvable lookup — operator will fix it by hand or remove it
        lookups[lid] = _drop_none({
            "description": str(r.get("lkp_description") or "").strip() or None,
            "query": target,
            "value": value_col,
            "label": label_col,
            "group": str(r.get("lkp_dd_group") or "").strip() or None,
        })

    # output  --------------------------------------------------------------- #
    section: dict[str, Any] = {"entries": entries}
    if enums:
        section["enums"] = enums
    if lookups:
        section["lookups"] = lookups
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
