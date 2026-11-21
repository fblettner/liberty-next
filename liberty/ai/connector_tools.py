"""Built-in tools that expose the Phase 1 connectors to the assistant.

* ``list_connectors`` — discovery: which connectors / read queries / endpoints exist
  (descriptions only — no SQL, no credentials). The tool *result* carries the live
  catalog, so tool *descriptions* stay byte-stable (prompt-cache friendly). Everything
  on a small install; on a large one an overview, narrowed with ``connector`` / ``search``.
* ``sql_query`` — run a named query on a SQL connector. **Read-only**: queries
  flagged ``writable`` are refused, so the assistant can never mutate data.
* ``api_call`` — call a named endpoint on an API connector. Off by default
  (``[ai] api_tool``) since API endpoints can have side effects.

Tool descriptions deliberately don't name specific connectors — that keeps them
stable across config changes and lets ``list_connectors`` be the source of truth.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from liberty.ai.tools import Tool, caller, may, require, tool
from liberty.auth.scope import column_items
from liberty.connectors import APIConnector, ConnectorRegistry, SQLConnector, flatten_query_metas
from liberty.connectors.api import current_pool
from liberty.connectors.base import ConnectorError
from liberty.connectors.sql import StreamDone, StreamMeta, StreamRows


# list_connectors returns everything while it fits in this many characters (JSON); above, an
# overview — the model then narrows with `connector` or `search`. A large install's full list is
# several hundred queries: it was cut by the tool-result cap, and paid again on every round.
LIST_MAX_CHARS = 12_000
# The most queries / endpoints one `search` returns.
SEARCH_MAX_RESULTS = 40


def _compact(d: dict[str, Any]) -> dict[str, Any]:
    """Without the empty values (None, "", [])."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _haystack(entry: dict[str, Any]) -> str:
    return " ".join(str(entry.get(k) or "") for k in ("name", "label", "description")).casefold()


def _as_item(key: str, dd_of: dict[str, str]) -> str:
    """A `filters` key as the dictionary bind to send: a screen column's name becomes the item
    its hint gives it (``DTSY`` → ``SY``), the range suffix kept; anything else as given."""
    k = str(key)
    for suffix in ("_from", "_to"):
        if k.endswith(suffix) and k[: -len(suffix)].lower() in dd_of:
            return dd_of[k[: -len(suffix)].lower()] + suffix
    return dd_of.get(k.lower(), k)


def _filter_item(key: str) -> str:
    """The column / dictionary item a `filters` key names, without its range suffix, lower-cased."""
    k = str(key)
    for suffix in ("_from", "_to"):
        if k.endswith(suffix):
            k = k[: -len(suffix)]
            break
    return k.lower()


def _size(obj: Any) -> int:
    return len(json.dumps(obj, default=str))


# The most rows one sql_query call returns unless ``[ai] sql_max_rows`` says otherwise.
DEFAULT_SQL_MAX_ROWS = 200


def build_connector_tools(
    connectors: ConnectorRegistry,
    *,
    allowed: list[str] | None = None,
    include_api: bool = False,
    max_rows: int = DEFAULT_SQL_MAX_ROWS,
    screens: Callable[[], Any] | None = None,
    hide_sensitive: Callable[[], bool] | None = None,
) -> list[Tool]:
    """Build the connector-backed tool set.

    Args:
        connectors: the live registry.
        allowed: if non-empty, restrict to these connector names.
        include_api: also offer ``api_call`` (API endpoints may have side effects).
        screens: ``() -> ScreensFile`` — ``sql_query`` runs a SELECT through the web layer's
            ``select_events``, the function the screens' own route uses: with the catalogue, a
            query behind a screen gets that screen's column hints and filters.
        max_rows: the most rows ``sql_query`` returns (``[ai] sql_max_rows``) — its own cap, not
            the pool's: a pool sized for the grid (500 000 rows, streamed) would otherwise be
            read in full into one tool result.
    """
    allow = set(allowed or [])
    row_cap = max(1, int(max_rows))

    def _visible(name: str) -> bool:
        return not allow or name in allow

    def _sql(name: str) -> SQLConnector:
        conn = connectors.get(name)  # raises UnknownConnectorError
        if not isinstance(conn, SQLConnector):
            raise ConnectorError(f"{name!r} is not a SQL connector")
        if not _visible(name):
            raise ConnectorError(f"connector {name!r} is not available to the assistant")
        return conn

    def _api(name: str) -> APIConnector:
        conn = connectors.get(name)
        if not isinstance(conn, APIConnector):
            raise ConnectorError(f"{name!r} is not an API connector")
        if not _visible(name):
            raise ConnectorError(f"connector {name!r} is not available to the assistant")
        return conn.for_pool(current_pool.get())   # the environment of the page the chat runs on

    def _entries(desc: dict[str, Any]) -> list[dict[str, Any]]:
        """What the assistant can call on one connector: its read queries (a writable one is
        refused by sql_query, so listing it only costs tokens), or its endpoints. Empty fields
        are left out."""
        if desc["type"] == "sql":
            return [
                _compact({"name": q["name"], "label": q.get("label"), "description": q.get("description"),
                          "params": [_compact(p) for p in q.get("params") or []], "bind_params": q.get("bind_params")})
                for q in flatten_query_metas(desc)
                if not q.get("writable", False) and may(f"sql:{desc['name']}:{q['name']}")
            ]
        if include_api and desc["type"] == "api":
            return [
                _compact({"name": e["name"], "label": e.get("label"), "description": e.get("description"),
                          "method": e.get("method"), "params": [_compact(p) for p in e.get("params") or []],
                          # an endpoint that changes data is called with api_run, not api_call
                          "changes_data": True if e.get("writable") else None,
                          # an endpoint that holds rows is read like a query: sql_query, its parameters as filters
                          "rows": True if e.get("rows_field") and not e.get("writable") else None})
                for e in desc["endpoints"] if may(f"api:{desc['name']}:{e['name']}")
            ]
        return []

    def _catalog() -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
        """Each connector with what the caller may call on it — a connector with nothing the
        caller may call is not listed at all (no name enumeration)."""
        out = [
            (desc, _entries(desc)) for desc in connectors.describe()
            if _visible(desc["name"]) and (desc["type"] == "sql" or (include_api and desc["type"] == "api"))
        ]
        return out if caller() is None else [(d, e) for d, e in out if e]

    def list_connectors(connector: str | None = None, search: str | None = None) -> dict[str, Any]:
        """The connectors available to you and their read queries / endpoints: the exact names
        sql_query and api_call take, with their parameters. Descriptions only — never SQL text
        or credentials.

        On a small installation, calling it with no argument lists everything. On a large one it
        returns an overview (each connector with how many queries it has): call it again with
        `search` to find a query by keyword, or with `connector` for one connector's list.

        Args:
            connector: only this connector (a name from the overview).
            search: keywords — only the queries / endpoints whose name, label or description
                contains every word (e.g. "address book", "f0092", "run failed").
        """
        catalog = _catalog()
        if connector:
            catalog = [(d, e) for d, e in catalog if d["name"] == connector]
            if not catalog:
                raise ConnectorError(f"no connector {connector!r} — call list_connectors with no argument for the names")

        def _block(desc: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
            return {"name": desc["name"], "type": desc["type"], ("queries" if desc["type"] == "sql" else "endpoints"): entries}

        if search and search.strip():
            words = search.casefold().split()
            out: list[dict[str, Any]] = []
            found = 0
            for desc, entries in catalog:
                hits = [e for e in entries if all(w in _haystack(e) for w in words)]
                room = SEARCH_MAX_RESULTS - found
                found += len(hits)
                if hits and room > 0:
                    out.append(_block(desc, hits[:room]))
            res: dict[str, Any] = {"connectors": out, "matches": found}
            if found > SEARCH_MAX_RESULTS:
                res["note"] = f"{found} matches, the first {SEARCH_MAX_RESULTS} shown — add a word to narrow."
            elif not found:
                res["note"] = "No match. Try other words (a table name, a subject), or describe_instance / search_catalog for screens."
            return res

        full = {"connectors": [_block(d, e) for d, e in catalog]}
        if _size(full) <= LIST_MAX_CHARS:
            return full
        if connector:
            # one connector, still too long: names and labels only
            desc, entries = catalog[0]
            return {
                "connectors": [_block(desc, [_compact({"name": e["name"], "label": e.get("label")}) for e in entries])],
                "note": "A long list: names and labels only. Call again with `search` to get a query's parameters.",
            }
        return {
            "connectors": [{"name": d["name"], "type": d["type"], "count": len(e)} for d, e in catalog],
            "note": (
                "An overview: too many queries to list at once. Call list_connectors again with `search` (keywords) "
                "to find a query, or with `connector` for one connector's list. A screen's read query is in "
                "describe_instance; the page context names the query of the page the user is on."
            ),
        }

    async def sql_query(
        connector: str, query: str, params: dict[str, Any] | None = None, filters: dict[str, Any] | None = None, limit: int | None = None,
    ) -> dict[str, Any]:
        """Run a named, read-only SQL query on a configured SQL connector — the way its screen
        runs it: same labels and formats, same filters, the user's data scope.

        Queries are predefined in the connector config; you supply parameter values and
        filters. Writable (data-mutating) queries are refused.

        The result holds the first rows only (a capped number); "truncated": true means more
        rows exist — the rows you got are then a sample, never a basis for a count or a total.
        To find a record, or the rows of one value, do NOT read the table: pass `filters`.

        Args:
            connector: connector name (from list_connectors) — a screen's query is on the screen's connector, which may differ from its application.
            query: query name on that connector.
            params: optional {param: value} for the query's :bind parameters.
            filters: {column: value} — only the rows where that result column equals the value, e.g. {"DTSY": "01"}; a column name of the result or its dictionary item; "<column>_from" / "<column>_to" give a range.
            limit: fewer rows than the cap, when a few are enough (a sample, a lookup).
        """
        from fastapi import HTTPException

        from liberty.web.connectors import DD_PARAM_PREFIX, _column_hints_for, _find_screen_for_query, select_events   # how a screen runs a SELECT

        from liberty.connectors.api_rows import api_connector, query_permission, rows_endpoint

        # the permission before the lookup, like the HTTP route: no name enumeration
        require(query_permission(connectors, connector, query), f"the query {connector}.{query}")
        cap = row_cap if limit is None else max(1, min(int(limit), row_cap))
        api = api_connector(connectors, connector)
        endpoint = rows_endpoint(api, query) if api is not None else None
        if api is not None:
            # an API endpoint that holds rows (what a screen on an API connector reads): its filters
            # are the endpoint's own parameters, nothing else can narrow it
            if endpoint is None or endpoint.writable:
                raise ConnectorError(f"{connector}.{query} is not an endpoint that holds rows — call it with api_call (or api_run when it changes data)")
            names = {p.name.lower(): p.name for p in endpoint.params}
            unknown = sorted(k for k in (filters or {}) if str(k).lower() not in names)
            if unknown:
                raise ConnectorError(
                    f"filter {', '.join(unknown)}: {connector}.{query} is read from an API — it filters only on its parameters: "
                    f"{', '.join(p.name for p in endpoint.params) or '(none)'}")
            filters, params = None, {**(params or {}), **(filters or {})}
        else:
            qdef = _sql(connector).get_query(query)  # raises QueryNotFoundError
            if qdef.writable:
                raise ConnectorError(f"query {connector}.{query} is writable and not available to the assistant")
        # `filters` are the framework's dictionary binds (what a dashboard filter sends): the
        # connector finds the column carrying each one, types and trims the value. A screen
        # column carries the item its hint names (DTSY → SY): a filter given by column name is
        # sent under that item.
        catalog = screens() if screens is not None else None
        hints = _column_hints_for(_find_screen_for_query(catalog, connector, query)[0]) if catalog is not None else None
        dd_of = {h.name.lower(): (h.dd or h.name) for h in hints or [] if h.dd != ""}
        wanted = {_as_item(k, dd_of): v for k, v in (filters or {}).items()}
        request = {**(params or {}), **{f"{DD_PARAM_PREFIX}{k}": v for k, v in wanted.items()}}
        who = caller()
        try:
            conn, events = select_events(
                connectors, connector, query, request, max_rows=cap, chunk_size=min(cap, 500),
                user=who.username if who is not None else None, screens=catalog, pool=current_pool.get(),
            )
        except HTTPException as exc:
            raise ConnectorError(str(exc.detail)) from exc
        out: dict[str, Any] = {"connector": connector, "query": query, "columns": [], "rows": [], "row_count": 0, "truncated": False}
        # Which dictionary items the result columns carry — the connector's own rule (the screen
        # hint's dd, else the column's name and, on a dd_prefix connector, the name minus the prefix).
        prefix = int(getattr(conn.config, "dd_prefix", 0) or 0)
        carried: set[str] = set()
        async for ev in events():
            if isinstance(ev, StreamMeta):
                out["columns"] = [c.to_dict() for c in ev.columns]
                for c in ev.columns:
                    carried |= column_items(c.name.lower(), dd_of, prefix)
            elif isinstance(ev, StreamRows):
                out["rows"].extend(ev.rows)
            elif isinstance(ev, StreamDone):
                out["row_count"], out["truncated"], out["duration_ms"] = ev.total, ev.truncated, round(ev.duration_ms, 3)
        # A dictionary bind no column carries is ignored by the connector (right for a dashboard
        # widget, wrong here: the model would take every row for the filtered ones).
        missed = sorted({k for k in wanted if _filter_item(k) not in carried})
        if missed:
            raise ConnectorError(
                f"filter {', '.join(missed)} matches no column of {connector}.{query} — it was not applied. "
                f"The columns are: {', '.join(c['name'] for c in out['columns'])}"
            )
        # Sensitive data (``[ai] hide_sensitive``): the columns flagged by the installation never
        # leave — dropped from the columns and from every row, and named so the model can say so.
        hidden = [c["name"] for c in out["columns"] if c.get("sensitive")]
        for c in out["columns"]:
            c.pop("sensitive", None)
        if hidden and hide_sensitive is not None and hide_sensitive():
            gone = set(hidden)
            # a hidden column is filtered on a whole value only: a range would let its values be
            # found out row count by row count
            keys = {c.lower() for c in hidden} | {dd_of.get(c.lower(), c).lower() for c in hidden}
            ranged = sorted(k for k in (filters or {}) if str(k).endswith(("_from", "_to")) and _filter_item(k) in keys)
            if ranged:
                raise ConnectorError(
                    f"{', '.join(ranged)}: this column is marked sensitive on this installation — it can be filtered only on a whole "
                    "value the user gives, not on a range. Tell the user.")
            out["columns"] = [c for c in out["columns"] if c["name"] not in gone]
            out["rows"] = [{k: v for k, v in row.items() if k not in gone} for row in out["rows"]]
            out["hidden_columns"] = hidden
            out["hidden_note"] = (
                "These columns hold data this installation marks as sensitive: their values are not given to you. "
                "If the user asks for them, say so and point them to the screen; do not look for them another way."
            )
        if out["truncated"]:
            out["note"] = (
                f"Only the first {out['row_count']} rows are returned; more exist. Do not count or add them up: "
                "narrow with `filters` (or the query's params), or use the Data Explorer tools for a count, a total or a ranking."
            )
        return out

    async def api_call(endpoint: str, connector: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a named endpoint on a configured API connector — one that only reads. An
        endpoint list_connectors marks ``changes_data`` is called with api_run instead.

        Endpoints and their auth are predefined in the connector config; you only
        supply parameter values. Call list_connectors first for the names.

        Args:
            connector: connector name (from list_connectors).
            endpoint: endpoint name on that connector.
            params: optional {param: value} for the endpoint's {{placeholders}}.
        """
        require(f"api:{connector}:{endpoint}", f"the endpoint {connector}.{endpoint}")
        conn = _api(connector)
        if conn.get_endpoint(endpoint).writable:
            raise ValueError(f"{connector}.{endpoint} changes data (changes_data): it is called with api_run, which the user confirms — "
                             "if api_run is not among your tools, tell the user you can only read here")
        result = await conn.call(endpoint, params or {}, untrusted=True)
        return result.to_dict()

    tools: list[Tool] = [
        tool(list_connectors, summary_keys=()),
        tool(sql_query, summary_keys=("connector", "query")),
    ]
    if include_api:
        tools.append(tool(api_call, summary_keys=("connector", "endpoint")))
    return tools
