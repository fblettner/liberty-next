"""Built-in tools that expose the Phase 1 connectors to the assistant.

* ``list_connectors`` — discovery: which connectors / queries / endpoints exist
  (descriptions only — no SQL, no credentials). The model is told to call this
  first, so the tool *result* carries the live catalog and tool *descriptions*
  stay byte-stable (prompt-cache friendly).
* ``sql_query`` — run a named query on a SQL connector. **Read-only**: queries
  flagged ``writable`` are refused, so the assistant can never mutate data.
* ``api_call`` — call a named endpoint on an API connector. Off by default
  (``[ai] api_tool``) since API endpoints can have side effects.

Tool descriptions deliberately don't name specific connectors — that keeps them
stable across config changes and lets ``list_connectors`` be the source of truth.
"""

from __future__ import annotations

from typing import Any

from liberty.ai.tools import Tool, tool
from liberty.connectors import APIConnector, ConnectorRegistry, SQLConnector
from liberty.connectors.base import ConnectorError


def build_connector_tools(
    connectors: ConnectorRegistry,
    *,
    allowed: list[str] | None = None,
    include_api: bool = False,
) -> list[Tool]:
    """Build the connector-backed tool set.

    Args:
        connectors: the live registry.
        allowed: if non-empty, restrict to these connector names.
        include_api: also offer ``api_call`` (API endpoints may have side effects).
    """
    allow = set(allowed or [])

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
        return conn

    def list_connectors() -> dict[str, Any]:
        """List the connectors available to you, with their queries / endpoints.

        Always call this FIRST before sql_query or api_call so you know the exact
        connector and query/endpoint names and what parameters they take. Returns
        descriptions only — never SQL text or credentials.
        """
        out: list[dict[str, Any]] = []
        for desc in connectors.describe():
            if not _visible(desc["name"]):
                continue
            if desc["type"] == "sql":
                queries = [
                    {
                        "name": q["name"],
                        "label": q.get("label"),
                        "description": q.get("description"),
                        "params": q.get("params", []),
                        "bind_params": q.get("bind_params", []),
                        "read_only": not q.get("writable", False),
                    }
                    for q in desc["queries"]
                ]
                out.append({"name": desc["name"], "type": "sql", "queries": queries})
            elif include_api and desc["type"] == "api":
                endpoints = [
                    {
                        "name": e["name"],
                        "label": e.get("label"),
                        "description": e.get("description"),
                        "method": e.get("method"),
                        "params": e.get("params", []),
                    }
                    for e in desc["endpoints"]
                ]
                out.append({"name": desc["name"], "type": "api", "endpoints": endpoints})
        return {"connectors": out}

    async def sql_query(connector: str, query: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run a named, read-only SQL query on a configured SQL connector.

        Queries are predefined in the connector config; you only supply parameter
        values. Writable (data-mutating) queries are refused. Call list_connectors
        first to learn the connector + query names and which params they accept.

        Args:
            connector: connector name (from list_connectors).
            query: query name on that connector.
            params: optional {param: value} for the query's :bind parameters.
        """
        conn = _sql(connector)
        qdef = conn.get_query(query)  # raises QueryNotFoundError
        if qdef.writable:
            raise ConnectorError(f"query {connector}.{query} is writable and not available to the assistant")
        result = await conn.execute(query, params or {})
        return result.to_dict()

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
