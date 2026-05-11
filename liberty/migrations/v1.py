"""Transform v1 (`ly_*` metadata tables) into v2 ``connectors.toml`` content.

These are pure functions over plain row dicts — the DB reading lives in
:mod:`liberty.migrations.source`, so the transformation is easy to unit-test.

v1 → v2 mapping
---------------
* ``ly_query`` (logical name/label) + ``ly_qry_sql`` (per dbtype × CRUD: pool,
  SQL, ORDER BY) → one v2 **SQL connector per ``query_pool``**, each carrying the
  queries that ran against that pool. A query becomes ``[[connectors.<pool>.queries]]``
  with ``sql`` = ``query_sqlquery`` (+ ``ORDER BY <query_orderby>`` if set) and
  ``writable = true`` when ``query_crud`` is INSERT/UPDATE/DELETE/MERGE. v1 has
  per-dbtype SQL variants; v2 has one SQL per name, so when a ``(query_id, crud)``
  has more than one dbtype the name gets a ``_<dbtype>`` suffix (or filter with
  ``dbtype=``). The pool's connection isn't in these tables — a ``[pools.<name>]``
  stub with a ``${...}`` URL placeholder is emitted for the operator to fill in.
* ``ly_api_conn`` (base URL + creds) → a v2 **API connector**; ``ly_api`` rows
  pointing at it (``api_conn_id``) → its ``[[connectors.<conn>.endpoints]]`` (method,
  path = ``api_url`` — relative or absolute, v2 resolves both — body, headers from
  ``ly_api_header``, params from ``ly_api_params``). ``ly_api`` rows with no
  ``api_conn_id`` go into a single ``legacy_api`` connector (``base_url = ""``, so
  the endpoint paths must be absolute URLs — which they were in v1). v1 stores
  passwords encrypted; the migration emits ``auth_username`` plaintext and a
  ``${...}`` placeholder for ``auth_password`` (the operator re-supplies the secret).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping
from urllib.parse import quote as _urlquote

_WRITE_CRUD = {"INSERT", "UPDATE", "DELETE", "MERGE"}
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
    """
    labels = {int(q["query_id"]): (q.get("query_label") or "") for q in queries}
    rows = [dict(r) for r in sql_rows]
    if dbtype:
        rows = [r for r in rows if (r.get("query_dbtype") or "").lower() == dbtype.lower()]

    pools: dict[str, dict[str, Any]] = {}
    connectors: dict[str, dict[str, Any]] = {}
    names_per_connector: dict[str, set[str]] = {}
    groups: dict[tuple[str, int, str], dict[str, str]] = {}  # (conn, query_id, crud) → {dialect: sql}
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
        orderby = (r.get("query_orderby") or "").strip()
        if orderby and crud == "SELECT":
            sql = f"{sql}\nORDER BY {orderby}"
        key = (conn_name, qid, crud)
        if key not in groups:
            groups[key] = {}
            order.append(key)
        groups[key].setdefault(_dialect_name(r.get("query_dbtype")), sql)  # first row of a dialect wins

    for key in order:
        conn_name, qid, crud = key
        label = labels.get(qid, "")
        base = slugify(f"{label}_{crud}" if label else f"q{qid}_{crud}", fallback=f"q{qid}_{crud.lower()}")
        connectors[conn_name]["queries"].append(
            _drop_none({
                "name": _uniquify(base, names_per_connector[conn_name]),
                "label": label or None,
                "writable": True if crud in _WRITE_CRUD else None,  # omit when false (default)
                "sql": _sql_value(groups[key]),
            })
        )

    return {"pools": pools, "connectors": connectors}


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
    """`${MIGRATED_PW_<POOL>}` — the DB password is never inlined into connectors.toml;
    the operator sets the env var (or recovers it: `liberty-crypto decrypt <apps_password>`)."""
    return "${MIGRATED_PW_" + pool_name.upper() + "}"


def _db_url(dialect: str, user: str, host: str, port: int, database: str, pool_name: str) -> str | None:
    driver = _DRIVER.get(dialect)
    if not driver:
        return None
    auth = f"{_urlquote(user, safe='')}:{_pw_placeholder(pool_name)}"
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
    connector_prefix: str = "",
) -> dict[str, Any]:
    """Build the ``{pools}`` dict from v1's ``ly_applications`` rows — one
    ``[pools.<slug-of-apps_pool>]`` per app with a real SQLAlchemy async URL when the
    connection details are known (``apps_host``/``apps_port``/``apps_database`` or a
    parseable ``apps_jdbc``), else the ``${LIBERTY_DB_URL_<NAME>}`` env-var stub.

    The DB password is **never inlined** — the URL carries a ``${MIGRATED_PW_<NAME>}``
    placeholder (v1 keeps it ``ENC:``-encrypted in ``apps_password``; the operator sets the
    env var, or recovers it with ``liberty-crypto decrypt``). v1's reserved ``default`` pool
    — its framework/definition DB — is **skipped**: v2 reserves ``[pools.default]`` for v2's
    own framework DB (the ``ly2_*`` tables). When several apps share a pool name, the first
    wins. ``apps_dbtype`` becomes the pool's explicit ``dialect`` (so v2 picks the right
    per-dialect SQL variant); ``apps_pool_max`` becomes ``pool_size`` when sensible.

    Args:
        applications: rows from ``ly_applications`` (``apps_name``, ``apps_pool``,
            ``apps_dbtype``, ``apps_jdbc``, ``apps_user``, ``apps_host``, ``apps_port``,
            ``apps_database``, ``apps_pool_max``, …).
        connector_prefix: prepended to the pool name (e.g. ``"v1_"``).
    """
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
            url = _db_url(dialect or "postgresql", user, host, port, database, name)
        if url is None and user:
            parsed = _parse_jdbc(str(a.get("apps_jdbc") or ""))
            if parsed:
                h, p, d = parsed
                url = _db_url(dialect or "postgresql", user, h, p or _DEFAULT_PORT.get(dialect or "postgresql", 5432), d, name)

        entry: dict[str, Any] = {"url": url or ("${LIBERTY_DB_URL_" + name.upper() + "}"), "pool_pre_ping": True}
        if dialect:
            entry["dialect"] = dialect
        try:
            pool_max = int(a.get("apps_pool_max") or 0)
            if 0 < pool_max <= 100:
                entry["pool_size"] = pool_max
        except (TypeError, ValueError):
            pass
        pools[name] = entry
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
