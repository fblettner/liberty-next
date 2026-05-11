"""FastAPI dependencies + helpers for the web layer.

The connector routes are permission-gated, but the required permission string
depends on path params (``sql:{connector}:{query}``), so the check is done
imperatively in the handler via :func:`require_permission` rather than as a
``Depends`` factory. Everything else (the registry, the principal) comes through
ordinary dependencies.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from liberty.auth.principal import Principal
from liberty.connectors import ConnectorRegistry


def get_connectors(request: Request) -> ConnectorRegistry:
    return request.app.state.connectors


def require_permission(principal: Principal, permission: str) -> None:
    """Raise 403 unless *principal* holds *permission* (glob-aware)."""
    if not principal.has_permission(permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {permission}")


def public_query(query: dict) -> dict:
    """A SQL-query descriptor with the SQL text stripped — safe for HTTP responses."""
    return {k: v for k, v in query.items() if k != "sql"}


def public_connector(desc: dict, principal: Principal) -> dict | None:
    """Filter one connector descriptor to what *principal* may use; ``None`` if nothing."""
    name = desc["name"]
    if desc["type"] == "sql":
        queries = [
            public_query(q) for q in desc["queries"]
            if principal.has_permission(f"sql:{name}:{q['name']}")
        ]
        if not queries:
            return None
        return {"name": name, "type": "sql", "queries": queries}
    if desc["type"] == "api":
        endpoints = [
            e for e in desc["endpoints"]
            if principal.has_permission(f"api:{name}:{e['name']}")
        ]
        if not endpoints:
            return None
        return {"name": name, "type": "api", "base_url": desc.get("base_url"),
                "auth_type": desc.get("auth_type"), "endpoints": endpoints}
    return None  # pragma: no cover - guarded by the discriminated union
