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
from typing import Any

from liberty.ai.tools import Tool, tool
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
) -> list[Tool]:
    """Build the connector-backed tool set.

    Args:
        connectors: the live registry.
        allowed: if non-empty, restrict to these connector names.
        include_api: also offer ``api_call`` (API endpoints may have side effects).
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
                          "params": q.get("params"), "bind_params": q.get("bind_params")})
                for q in flatten_query_metas(desc) if not q.get("writable", False)
            ]
        if include_api and desc["type"] == "api":
            return [
                _compact({"name": e["name"], "label": e.get("label"), "description": e.get("description"),
                          "method": e.get("method"), "params": e.get("params")})
                for e in desc["endpoints"]
            ]
        return []

    def _catalog() -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
        return [
            (desc, _entries(desc)) for desc in connectors.describe()
            if _visible(desc["name"]) and (desc["type"] == "sql" or (include_api and desc["type"] == "api"))
        ]

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

    async def sql_query(connector: str, query: str, params: dict[str, Any] | None = None, limit: int | None = None) -> dict[str, Any]:
        """Run a named, read-only SQL query on a configured SQL connector.

        Queries are predefined in the connector config; you only supply parameter
        values. Writable (data-mutating) queries are refused. Call list_connectors
        first to learn the connector + query names and which params they accept.

        The result holds the first rows only (a capped number); "truncated": true means more
        rows exist — the rows you got are then a sample, never a basis for a count or a total.

        Args:
            connector: connector name (from list_connectors).
            query: query name on that connector.
            params: optional {param: value} for the query's :bind parameters.
            limit: fewer rows than the cap, when a few are enough (a sample, a lookup).
        """
        conn = _sql(connector)
        qdef = conn.get_query(query)  # raises QueryNotFoundError
        if qdef.writable:
            raise ConnectorError(f"query {connector}.{query} is writable and not available to the assistant")
        cap = row_cap if limit is None else max(1, min(int(limit), row_cap))
        # The streaming path: a server-side cursor, closed once the cap is reached — execute()
        # would have the driver fetch the whole result before the cap cuts it.
        out: dict[str, Any] = {"connector": connector, "query": query, "columns": [], "rows": [], "row_count": 0, "truncated": False}
        async for ev in conn.execute_stream(query, params or {}, max_rows=cap, chunk_size=min(cap, 500)):
            if isinstance(ev, StreamMeta):
                out["columns"] = [c.to_dict() for c in ev.columns]
            elif isinstance(ev, StreamRows):
                out["rows"].extend(ev.rows)
            elif isinstance(ev, StreamDone):
                out["row_count"], out["truncated"], out["duration_ms"] = ev.total, ev.truncated, round(ev.duration_ms, 3)
        if out["truncated"]:
            out["note"] = (
                f"Only the first {out['row_count']} rows are returned; more exist. Do not count or add them up: "
                "narrow the query with its params, or use the Data Explorer tools for a count, a total or a ranking."
            )
        return out

    async def api_call(endpoint: str, connector: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a named endpoint on a configured API connector.

        Endpoints and their auth are predefined in the connector config; you only
        supply parameter values. Call list_connectors first for the names.

        Args:
            connector: connector name (from list_connectors).
            endpoint: endpoint name on that connector.
            params: optional {param: value} for the endpoint's {{placeholders}}.
        """
        conn = _api(connector)
        result = await conn.call(endpoint, params or {})
        return result.to_dict()

    tools: list[Tool] = [
        tool(list_connectors, summary_keys=()),
        tool(sql_query, summary_keys=("connector", "query")),
    ]
    if include_api:
        tools.append(tool(api_call, summary_keys=("connector", "endpoint")))
    return tools
