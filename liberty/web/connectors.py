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
from liberty.connectors.config import ColumnHint
from liberty.connectors.introspect import introspect_pool, list_pool_schemas
from liberty.screens import Screen, ScreensFile
from liberty.web.deps import get_connectors, get_screens, public_connector, request_language, require_permission
from liberty.web.errors import http_for_connector_error

router = APIRouter(prefix="/api", tags=["connectors"])

Connectors = Annotated[ConnectorRegistry, Depends(get_connectors)]
Screens = Annotated[ScreensFile, Depends(get_screens)]


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


def _find_screen_for_query(
    screens: ScreensFile, connector: str, query: str,
) -> tuple[Screen | None, str | None, str | None]:
    """Look up the screen whose ``read_query`` / ``update_query`` / ``insert_query`` /
    ``delete_query`` matches *(connector, query)*. Returns ``(screen, slot, app)`` — the slot
    name (``read`` / ``update`` / ``insert`` / ``delete``) for behaviour selection, plus the
    *app* (the dict key in ``screens.toml``) so the route can use it as the dictionary scope
    (operator metadata lives under one app's section even when the screen runs against a
    different data pool). When several screens reference the same query, the first hit wins.
    ``(None, None, None)`` when nothing matches."""
    slots = (("read_query", "read"), ("update_query", "update"),
             ("insert_query", "insert"), ("delete_query", "delete"))
    for app, app_screens in screens.screens.items():
        for s in app_screens.values():
            eff = s.connector or app
            if eff != connector:
                continue
            for attr, slot in slots:
                if getattr(s, attr) == query:
                    return s, slot, app
    return None, None, None


def _column_hints_for(screen: Screen | None) -> list[ColumnHint] | None:
    """Phase 3 — the SQL connector's runtime helpers (filter wrap / write-time coercion /
    SEQUENCE / result-column hint application) read column metadata from the matching
    :class:`Screen.columns`. Returns ``None`` when no screen → connector behaves as a thin
    SQL runner with no per-screen hints (back-compat for connector-only deployments)."""
    if screen is None or not screen.columns:
        return None
    return list(screen.columns)


async def _run_sql(
    connectors: ConnectorRegistry, connector: str, query: str, params: dict[str, Any], *,
    language: str | None = None, max_rows: int | None = None, user: str | None = None,
    screens: ScreensFile | None = None,
) -> dict[str, Any]:
    """Run *query* on *connector* with *params*. When a matching :class:`Screen` is found,
    thread its per-screen behaviour (column hints, ``audit_table``, ``max_rows``, dictionary
    scope = the screen's app) into the SQL connector. A query with no screen runs unadorned
    — no audit, no filter wrap, no per-screen rule resolution; dictionary lookup by bind name
    still applies for write-side rule coercion using the connector's own name as scope."""
    screen, _slot, screen_app = (
        _find_screen_for_query(screens, connector, query) if screens else (None, None, None)
    )
    column_hints = _column_hints_for(screen)
    audit_table = screen.audit_table if screen else None
    screen_max_rows = screen.max_rows if screen else None
    # Dictionary scope follows the screen's app (where operator metadata lives), not the
    # data-pool connector. Falls back to the connector's own name when there's no matching
    # screen (a query opened by an AI tool / external caller / dashboard widget — those don't
    # have a screen and the connector resolves rules against its own scope).
    dict_scope = screen_app if screen is not None else None
    try:
        conn = connectors.sql(connector)
        # `user` is recorded on the audit row when the screen carries `audit_table`; otherwise
        # it's ignored. Pulled from the JWT principal — never the request body.
        result = await conn.execute(
            query, params, language=language, max_rows=max_rows, user=user,
            column_hints=column_hints, audit_table=audit_table, screen_max_rows=screen_max_rows,
            dictionary_scope=dict_scope,
        )
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"query failed: {type(exc).__name__}: {exc}") from exc
    return result.to_dict()


@router.get("/sql/{connector}/_schemas")
async def sql_pool_schemas(
    connector: str, principal: CurrentPrincipal, connectors: Connectors,
) -> dict[str, Any]:
    """List the non-system schema names on the connector's pool. **No tables, no columns** —
    just the schema names. Used by the CRUD wizard's schema picker so an Oracle pool with
    many schemas doesn't have to enumerate every table up front (the unscoped table walk on
    JDE's PS920 + SY920 + DTA920 + CTL920 + CRP920 + PRD920 takes 10+ seconds; this returns
    in milliseconds). Pairs with the ``?schema=<sch>`` filter on ``/_schema`` below.

    **Superuser only** — same restriction as the table walk."""
    if not principal.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Superuser only")
    try:
        conn = connectors.sql(connector)
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    try:
        return await list_pool_schemas(connectors.pools, conn.pool_name)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"schema list failed: {type(exc).__name__}: {exc}",
        ) from exc


@router.get("/sql/{connector}/_schema")
async def sql_pool_schema(
    connector: str, request: Request, principal: CurrentPrincipal, connectors: Connectors,
) -> dict[str, Any]:
    """Introspect the connector's pool — list its tables/views + their columns. Powers the
    Phase-7 SQL editor's autocomplete (Monaco CompletionItemProvider) and the wizard's table
    picker. **Superuser only** — this leaks every accessible table on the pool, which is fine
    for an operator using the config builder (the only consumer) but not for a regular caller
    who only knows about named queries. Returns 502 on connection failure so the frontend can
    degrade gracefully (autocomplete just doesn't show suggestions).

    ``?schema=<sch>`` (optional) scopes the walk to a single schema — the CRUD wizard uses
    this so an Oracle pool with many owners doesn't fan out across every one on every
    introspection. The autocomplete path leaves it unset and gets the full catalog.

    ``?name_like=<pattern>`` (optional) is a SQL-LIKE-style filter on table / view names
    (``F009%`` matches every JDE F009-prefixed table). Names that don't match are dropped
    *before* the per-table column fetch, which is the slow step — a 2000-table SY920
    narrowed to ``F009%`` (≈10 tables) returns in well under a second instead of 5+s.
    Empty matches all.
    """
    if not principal.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Superuser only")
    try:
        conn = connectors.sql(connector)  # UnknownConnectorError → 404; wrong-type → 404
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    only_schema = request.query_params.get("schema") or None
    name_like = request.query_params.get("name_like") or None
    try:
        return await introspect_pool(
            connectors.pools, conn.pool_name,
            only_schema=only_schema, name_like=name_like,
        )
    except SQLAlchemyError as exc:
        # Don't 500 — the editor calls this on focus and gracefully degrades without suggestions.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"schema introspection failed: {type(exc).__name__}: {exc}",
        ) from exc


@router.get("/sql/{connector}/{query}")
async def sql_query_get(
    connector: str, query: str, request: Request, principal: CurrentPrincipal,
    connectors: Connectors, screens: Screens,
) -> dict[str, Any]:
    require_permission(principal, f"sql:{connector}:{query}")
    # GET must not mutate — only SELECTs are allowed here; everything else uses POST.
    try:
        qdef = connectors.sql(connector).get_query(query)
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    if detect_statement_type(qdef.default_sql) != "SELECT":
        raise HTTPException(status.HTTP_405_METHOD_NOT_ALLOWED, detail="Non-SELECT queries must be run with POST")
    qp = dict(request.query_params)
    limit = _as_int(qp.pop("_limit", None))  # ?_limit=N overrides the row cap; the rest are query params
    return await _run_sql(
        connectors, connector, query, qp,
        language=request_language(request), max_rows=limit, user=principal.username,
        screens=screens,
    )


@router.post("/sql/{connector}/{query}")
async def sql_query_post(
    connector: str, query: str, request: Request, principal: CurrentPrincipal,
    connectors: Connectors, screens: Screens,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_permission(principal, f"sql:{connector}:{query}")
    limit = _as_int(body.get("max_rows")) if body else None  # body {"params": …, "max_rows": N}
    return await _run_sql(
        connectors, connector, query, _params_from_body(body),
        language=request_language(request), max_rows=limit, user=principal.username,
        screens=screens,
    )


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
