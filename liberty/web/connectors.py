"""``/api`` routes — discover connectors and run their queries / endpoints.

| Route | Permission | Notes |
|---|---|---|
| ``GET  /api/connectors`` | authenticated | metadata only, filtered to what you may use; no SQL text, no credentials |
| ``GET  /api/connectors/{connector}`` | authenticated | one connector, same filtering |
| ``GET  /api/sql/{connector}/{query}`` | ``sql:{connector}:{query}`` | SELECT only; params from the query string |
| ``POST /api/sql/{connector}/{query}`` | ``sql:{connector}:{query}`` | any allowed statement; params in the body (``{"params": {...}}`` or a flat ``{name: value}``) |
| ``POST /api/http/{connector}/{endpoint}`` | ``api:{connector}:{endpoint}`` | calls the configured HTTP endpoint; returns the structured result even on upstream failure |

Permission is checked *before* the connector is looked up, so callers can't
enumerate connector/query names they have no access to. The query's ``writable``
flag is an independent gate set in the TOML — a mutating query needs *both* it
and the caller's permission. (The OpenAPI docs at ``/docs`` describe all of this.)
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError

from liberty.auth.dependencies import CurrentPrincipal
from liberty.connectors import ConnectorRegistry
from liberty.connectors.base import ConnectorError, detect_statement_type
from liberty.web.deps import get_connectors, public_connector, request_language, require_permission
from liberty.web.errors import http_for_connector_error

router = APIRouter(prefix="/api", tags=["connectors"])

Connectors = Annotated[ConnectorRegistry, Depends(get_connectors)]


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #


@router.get("/connectors")
async def list_connectors(principal: CurrentPrincipal, connectors: Connectors) -> dict[str, Any]:
    out = [c for c in (public_connector(d, principal) for d in connectors.describe()) if c is not None]
    return {"connectors": out}


@router.get("/connectors/{connector}")
async def describe_connector(connector: str, principal: CurrentPrincipal, connectors: Connectors) -> dict[str, Any]:
    try:
        conn = connectors.get(connector)
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    view = public_connector(conn.describe(), principal)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No accessible items on connector {connector!r}")
    return view


# --------------------------------------------------------------------------- #
# SQL connectors
# --------------------------------------------------------------------------- #


# `_limit` (query string) / `max_rows` (POST body) aren't query params — they override the row cap.
_RESERVED_BODY_KEYS = {"params", "max_rows"}


def _params_from_body(body: dict[str, Any] | None) -> dict[str, Any]:
    if not body:
        return {}
    nested = body.get("params")
    return nested if isinstance(nested, dict) else {k: v for k, v in body.items() if k not in _RESERVED_BODY_KEYS}


def _as_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


async def _run_sql(
    connectors: ConnectorRegistry, connector: str, query: str, params: dict[str, Any], *,
    language: str | None = None, max_rows: int | None = None,
) -> dict[str, Any]:
    try:
        conn = connectors.sql(connector)  # UnknownConnectorError if missing / wrong type
        result = await conn.execute(query, params, language=language, max_rows=max_rows)
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"query failed: {type(exc).__name__}: {exc}") from exc
    return result.to_dict()


@router.get("/sql/{connector}/{query}")
async def sql_query_get(
    connector: str, query: str, request: Request, principal: CurrentPrincipal, connectors: Connectors
) -> dict[str, Any]:
    require_permission(principal, f"sql:{connector}:{query}")
    # GET must not mutate — only SELECTs are allowed here; everything else uses POST.
    try:
        qdef = connectors.sql(connector).get_query(query)
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    if detect_statement_type(qdef.sql) != "SELECT":
        raise HTTPException(status.HTTP_405_METHOD_NOT_ALLOWED, detail="Non-SELECT queries must be run with POST")
    qp = dict(request.query_params)
    limit = _as_int(qp.pop("_limit", None))  # ?_limit=N overrides the row cap; the rest are query params
    return await _run_sql(connectors, connector, query, qp, language=request_language(request), max_rows=limit)


@router.post("/sql/{connector}/{query}")
async def sql_query_post(
    connector: str, query: str, request: Request, principal: CurrentPrincipal, connectors: Connectors,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_permission(principal, f"sql:{connector}:{query}")
    limit = _as_int(body.get("max_rows")) if body else None  # body {"params": …, "max_rows": N}
    return await _run_sql(connectors, connector, query, _params_from_body(body), language=request_language(request), max_rows=limit)


# --------------------------------------------------------------------------- #
# API (HTTP) connectors
# --------------------------------------------------------------------------- #


@router.post("/http/{connector}/{endpoint}")
async def http_call(
    connector: str, endpoint: str, principal: CurrentPrincipal, connectors: Connectors, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    require_permission(principal, f"api:{connector}:{endpoint}")
    try:
        conn = connectors.api(connector)  # UnknownConnectorError if missing / wrong type
        conn.get_endpoint(endpoint)  # EndpointNotFoundError if missing
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    # APIConnector.call never raises — a failed upstream call comes back as a
    # structured ApiResult with success=False; surface it as-is (HTTP 200).
    result = await conn.call(endpoint, _params_from_body(body))
    return result.to_dict()
