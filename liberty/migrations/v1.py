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


def migrate_sql_queries(
    queries: Iterable[Mapping[str, Any]],
    sql_rows: Iterable[Mapping[str, Any]],
    *,
    dbtype: str | None = None,
    connector_prefix: str = "",
) -> dict[str, Any]:
    """Build the ``{pools, connectors}`` dict for the SQL side.

    Args:
        queries: rows from ``ly_query`` (``query_id``, ``query_label``, ``query_type``).
        sql_rows: rows from ``ly_qry_sql`` (``query_id``, ``query_dbtype``, ``query_crud``,
            ``query_pool``, ``query_sqlquery``, ``query_orderby``).
        dbtype: if given, only migrate rows with this ``query_dbtype``.
        connector_prefix: prepended to the per-pool connector name (e.g. ``"v1_"``).
    """
    labels = {int(q["query_id"]): (q.get("query_label") or "") for q in queries}
    rows = [dict(r) for r in sql_rows]
    if dbtype:
        rows = [r for r in rows if (r.get("query_dbtype") or "").lower() == dbtype.lower()]

    # How many dbtypes does each (query_id, crud) have? → only suffix when ambiguous.
    dbtype_count: dict[tuple[int, str], set[str]] = {}
    for r in rows:
        key = (int(r["query_id"]), str(r.get("query_crud") or "").upper())
        dbtype_count.setdefault(key, set()).add((r.get("query_dbtype") or "").lower())

    pools: dict[str, dict[str, Any]] = {}
    connectors: dict[str, dict[str, Any]] = {}
    names_per_connector: dict[str, set[str]] = {}

    for r in rows:
        sql = (r.get("query_sqlquery") or "").strip()
        if not sql:
            continue
        qid = int(r["query_id"])
        crud = str(r.get("query_crud") or "SELECT").upper()
        db = (r.get("query_dbtype") or "").lower()
        pool = str(r.get("query_pool") or "default").strip() or "default"
        conn_name = f"{connector_prefix}{slugify(pool, fallback='default')}"

        if conn_name not in connectors:
            connectors[conn_name] = {"type": "sql", "pool": conn_name, "queries": []}
            names_per_connector[conn_name] = set()
            # Stub pool — fill in the real URL (or set the named env var).
            pools.setdefault(conn_name, {"url": "${LIBERTY_DB_URL_" + conn_name.upper() + "}", "pool_pre_ping": True})

        label = labels.get(qid, "")
        base = slugify(f"{label}_{crud}" if label else f"q{qid}_{crud}", fallback=f"q{qid}_{crud.lower()}")
        if len(dbtype_count.get((qid, crud), set())) > 1 and db:
            base = f"{base}_{slugify(db)}"
        name = _uniquify(base, names_per_connector[conn_name])

        orderby = (r.get("query_orderby") or "").strip()
        if orderby and crud == "SELECT":
            sql = f"{sql}\nORDER BY {orderby}"

        connectors[conn_name]["queries"].append(
            _drop_none({
                "name": name,
                "label": label or None,
                "writable": True if crud in _WRITE_CRUD else None,  # omit when false (default)
                "sql": sql,
            })
        )

    return {"pools": pools, "connectors": connectors}


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
        connectors[name] = _drop_none({
            "type": "api",
            "base_url": (c.get("conn_url") or "").strip(),
            "auth_type": "basic" if user else "none",
            "auth_username": user or None,
            # v1 stored the password encrypted; the operator re-supplies it via this env var.
            "auth_password": ("${MIGRATED_SECRET_" + name.upper() + "}") if user else None,
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
