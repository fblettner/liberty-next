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

import asyncio
import json
import logging
from typing import Annotated, Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from liberty.auth.dependencies import CurrentPrincipal
from liberty.connectors import ConnectorRegistry
from liberty.connectors.base import ConnectorError, detect_statement_type
from liberty.connectors.config import ColumnHint
from liberty.connectors.introspect import introspect_pool, list_pool_schemas
from liberty.connectors.sql import StreamDone, StreamMeta, StreamRows
from liberty.screens import Screen, ScreensFile
from liberty.web.deps import get_connectors, get_screens, public_connector, request_language, require_permission
from liberty.web.errors import http_for_connector_error

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["connectors"])

Connectors = Annotated[ConnectorRegistry, Depends(get_connectors)]
Screens = Annotated[ScreensFile, Depends(get_screens)]


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #


@router.get(
    "/connectors",
    summary="List connectors",
    responses={
        401: {"description": "Missing / invalid access token."},
    },
)
async def list_connectors(principal: CurrentPrincipal, connectors: Connectors) -> dict[str, Any]:
    """Returns the public view of every loaded connector — kind (``sql`` / ``api``),
    available queries / endpoints (filtered to those the caller can use), description,
    and labels. **Never** returns SQL text, credentials, or connection strings.

    Filtering: a connector is included only if the caller can use at least one of its
    queries / endpoints. Within each connector, queries / endpoints the caller can't use
    are dropped. The result is the same shape the AI assistant's ``list_connectors``
    tool returns."""
    out = [c for c in (public_connector(d, principal) for d in connectors.describe()) if c is not None]
    return {"connectors": out}


@router.get(
    "/connectors/{connector}",
    summary="Describe connector",
    responses={
        401: {"description": "Missing / invalid access token."},
        404: {"description": "Connector doesn't exist, or the caller can't use any of its queries / endpoints."},
    },
)
async def describe_connector(connector: str, principal: CurrentPrincipal, connectors: Connectors) -> dict[str, Any]:
    """One connector's public view — same shape as a list-element from
    ``GET /api/connectors`` but narrowed to a single id. Returns 404 when the caller has
    no usable queries / endpoints (rather than 403) so probing for connector names
    yields no information about what exists vs. what's forbidden."""
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
    screens: ScreensFile | None = None, changesets: Any = None,
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
    # Change-package capture — AFTER the write has committed (cross-DB: control pool ≠ this
    # connector, so it can't share the write's transaction). Best-effort: a capture failure is
    # logged but never undoes the already-committed write — the audit table stays the immutable
    # trail. No-op for SELECTs / untracked screens (capture_write guards both).
    if changesets is not None and screen is not None and getattr(screen, "change_tracked", False):
        from liberty.changesets.capture import capture_write
        try:
            await capture_write(
                changesets, connector=connector, query=query,
                statement_type=result.statement_type, params=params, screen=screen, user=user,
            )
        except Exception as exc:  # noqa: BLE001 — capture must never fail a committed write
            _log.error(
                "change capture failed for %s.%s (write committed but NOT packaged): %s",
                connector, query, exc,
            )
    return result.to_dict()


def _stream_sql_ndjson(
    connectors: ConnectorRegistry, connector: str, query: str, params: dict[str, Any], *,
    language: str | None = None, max_rows: int | None = None, user: str | None = None,
    screens: ScreensFile | None = None, chunk_size: int | None = None,
) -> StreamingResponse:
    """Stream *query*'s rows as NDJSON (``application/x-ndjson``). Same per-screen prep as
    :func:`_run_sql` — column hints, ``screen_max_rows``, dictionary scope — except writes are
    rejected (streaming is SELECT-only; the SQLConnector enforces this and raises). The
    response body is a sequence of newline-delimited JSON objects:

      * ``{"kind":"meta","columns":[…],"cap":N,"chunk_size":N}`` — once at the top.
      * ``{"kind":"rows","rows":[…],"sent":N}`` — repeatedly, ``chunk_size`` rows each.
      * ``{"kind":"done","total":N,"truncated":bool,"duration_ms":…}`` — exactly once.
      * ``{"kind":"error","detail":…}`` — emitted instead of the ``done`` line if the query
        raises mid-stream. The HTTP status is still 200 (the headers have already been sent
        by then); the consumer's NDJSON parser sees the error line and reports it as a
        terminal event.

    Errors raised **before** any byte ships (permission check, missing connector / query,
    non-SELECT) propagate as standard HTTP errors via ``http_for_connector_error``.
    """
    # ── prep (mirrors _run_sql) — done synchronously so any failure raises before we start
    # streaming. Once the generator below begins yielding, headers have been sent and we
    # can't change the status code; preflight everything we can up here.
    screen, _slot, screen_app = (
        _find_screen_for_query(screens, connector, query) if screens else (None, None, None)
    )
    column_hints = _column_hints_for(screen)
    screen_max_rows = screen.max_rows if screen else None
    dict_scope = screen_app if screen is not None else None
    try:
        conn = connectors.sql(connector)
        # Reject non-SELECT up front so we don't 200-then-error. ``execute_stream`` also
        # raises, but doing it here gives a clean 405 / 422 instead of an NDJSON error line.
        qdef = conn.get_query(query)
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    if detect_statement_type(qdef.default_sql) != "SELECT":
        raise HTTPException(
            status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="Streaming is SELECT-only; use POST /api/sql/{c}/{q} for writes.",
        )

    async def gen() -> AsyncIterator[bytes]:
        """Render each StreamEvent to a single NDJSON line (UTF-8 bytes, trailing ``\\n``).
        Wraps the connector's async generator with an exception handler so a mid-stream
        failure becomes a terminal ``error`` event instead of a hung connection."""
        try:
            async for ev in conn.execute_stream(
                query, params, language=language, max_rows=max_rows, user=user,
                column_hints=column_hints, screen_max_rows=screen_max_rows,
                dictionary_scope=dict_scope, chunk_size=chunk_size or 100,
            ):
                # The three concrete event types render to plain dicts; ``json.dumps`` with
                # ``default=str`` handles dates / datetimes / Decimals the same way the
                # non-streaming response does (FastAPI uses jsonable_encoder which falls back
                # to str() for unknown types — keep parity).
                if isinstance(ev, StreamMeta):
                    payload = {
                        "kind": "meta",
                        "columns": [c.to_dict() for c in ev.columns],
                        "cap": ev.cap,
                        "chunk_size": ev.chunk_size,
                    }
                elif isinstance(ev, StreamRows):
                    payload = {"kind": "rows", "rows": ev.rows, "sent": ev.sent}
                elif isinstance(ev, StreamDone):
                    payload = {
                        "kind": "done",
                        "total": ev.total,
                        "truncated": ev.truncated,
                        "duration_ms": round(ev.duration_ms, 3),
                        "rowcount": ev.rowcount,
                    }
                else:
                    # Defensive — keeps a future StreamEvent variant from silently breaking
                    # the stream (the consumer would see an unknown ``kind`` field).
                    payload = {"kind": "unknown"}
                yield (json.dumps(payload, default=str) + "\n").encode("utf-8")
        except (ConnectorError, SQLAlchemyError) as exc:
            # The HTTP headers have already gone out (``StreamingResponse`` flushes on the
            # first yield) so we can't switch to a 4xx/5xx. Emit a terminal error line; the
            # consumer's NDJSON parser sees ``{"kind":"error",…}`` and treats it as end-of-
            # stream + reports the detail. Logged at warning level so an operator sees it
            # in the server log without scraping the response body.
            _log.warning(
                "execute_stream %s.%s failed mid-stream: %s",
                connector, query, exc,
            )
            yield (json.dumps({
                "kind": "error",
                "detail": f"{type(exc).__name__}: {exc}",
            }) + "\n").encode("utf-8")

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        # Hint downstream proxies (nginx) to not buffer. Without this, the operator's first
        # chunk may sit in the proxy buffer until the *whole* response lands — defeats the
        # purpose of streaming.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.get(
    "/sql/{connector}/_schemas",
    summary="List pool schemas",
)
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
    except asyncio.CancelledError:
        _log.info("schema list on %r cancelled (client disconnect / shutdown)", connector)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="schema list cancelled") from None
    except SQLAlchemyError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"schema list failed: {type(exc).__name__}: {exc}",
        ) from exc


@router.get(
    "/sql/{connector}/_schema",
    summary="Introspect pool schema",
)
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
    except asyncio.CancelledError:
        # The full-catalog Oracle/JDE introspection is slow (5+s); a client navigating away or a
        # server shutdown cancels it mid-fetch. That's expected, not an app error — convert to a
        # quiet 503 so uvicorn doesn't dump a CancelledError traceback ("Exception in ASGI
        # application"). The slow walk itself is a separate perf follow-up (cache it).
        _log.info("schema introspection on %r cancelled (client disconnect / shutdown)", connector)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="introspection cancelled") from None
    except SQLAlchemyError as exc:
        # Don't 500 — the editor calls this on focus and gracefully degrades without suggestions.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"schema introspection failed: {type(exc).__name__}: {exc}",
        ) from exc


def _streaming_requested(qp: dict[str, Any]) -> bool:
    """``?_stream=1`` (or any truthy spelling) opts the response into NDJSON streaming
    mode. Lives on the query string so a browser can swap modes without changing the body
    shape; mirrors the existing ``?_limit=N`` convention."""
    raw = qp.get("_stream")
    if raw is None:
        return False
    return str(raw).lower() in {"1", "true", "yes", "y", "on"}


@router.get(
    "/sql/{connector}/{query}",
    summary="Run query (GET)",
    responses={
        401: {"description": "Missing / invalid access token."},
        403: {"description": "Caller lacks ``sql:<connector>:<query>``."},
        404: {"description": "Unknown connector or query."},
        405: {"description": "Query isn't a SELECT — non-SELECT queries must be POSTed."},
        502: {"description": "Upstream DB error (network / syntax / timeout); see ``detail``."},
    },
)
async def sql_query_get(
    connector: str, query: str, request: Request, principal: CurrentPrincipal,
    connectors: Connectors, screens: Screens,
):
    """Run *connector*'s named *query*. Query-string params bind ``:name`` placeholders
    in the SQL; reserved params shape the runtime:

    | Param | Effect |
    |---|---|
    | ``_limit`` | Row cap. Falls back to the screen / connector / pool default (1000). |
    | ``_stream`` | Truthy → response is ``application/x-ndjson``: one ``{kind: meta\\|rows\\|done\\|error}`` line per chunk. Suited to large datasets / SSE-style UIs. |
    | ``_chunk_size`` | NDJSON rows-per-chunk (default 100). |
    | ``_sort`` / ``_dir`` | Server-side sort by a result column. ``_dir`` ∈ {``asc``, ``desc``}. |
    | ``_count`` | Returns ``{total: N}`` only, not the data. |
    | ``_filter`` | JSON-encoded filter tree applied server-side. |

    When the query is the ``read_query`` of a Screen, that screen's column hints +
    ``max_rows`` + audit table + dictionary scope (the screen's app) are applied
    automatically — that's how the same query has Settings-rich behaviour from the
    screen runtime and stays a bare SQL runner when called by the AI assistant."""
    require_permission(principal, f"sql:{connector}:{query}")
    # GET must not mutate — only SELECTs are allowed here; everything else uses POST.
    try:
        qdef = connectors.sql(connector).get_query(query)
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    if detect_statement_type(qdef.default_sql) != "SELECT":
        raise HTTPException(status.HTTP_405_METHOD_NOT_ALLOWED, detail="Non-SELECT queries must be run with POST")
    qp = dict(request.query_params)
    # ``_stream`` / ``_limit`` / ``_chunk_size`` are reserved query keys — pop them out before
    # passing the rest as SQL params. Matches the existing ``_limit`` convention.
    stream = _streaming_requested(qp); qp.pop("_stream", None)
    limit = _as_int(qp.pop("_limit", None))
    chunk_size = _as_int(qp.pop("_chunk_size", None))
    if stream:
        return _stream_sql_ndjson(
            connectors, connector, query, qp,
            language=request_language(request), max_rows=limit, user=principal.username,
            screens=screens, chunk_size=chunk_size,
        )
    return await _run_sql(
        connectors, connector, query, qp,
        language=request_language(request), max_rows=limit, user=principal.username,
        screens=screens,
    )


@router.post(
    "/sql/{connector}/{query}",
    summary="Run query (POST)",
    responses={
        401: {"description": "Missing / invalid access token."},
        403: {"description": "Caller lacks ``sql:<connector>:<query>``."},
        404: {"description": "Unknown connector or query."},
        502: {"description": "Upstream DB error (constraint violation, network, timeout, etc.)."},
    },
)
async def sql_query_post(
    connector: str, query: str, request: Request, principal: CurrentPrincipal,
    connectors: Connectors, screens: Screens,
    body: dict[str, Any] | None = None,
):
    """Run *connector*'s named *query* with params from the body. Use this for any
    non-SELECT (INSERT / UPDATE / DELETE / MERGE / DDL) — the GET variant rejects those
    with 405.

    **Body shape (two accepted forms):**

    ```json
    { "params": { "USR_ID": "alice", "USR_PASSWORD": "secret" }, "max_rows": 50 }
    ```
    or — flat (any key that isn't ``params`` / ``max_rows`` is treated as a bind):
    ```json
    { "USR_ID": "alice", "USR_PASSWORD": "secret" }
    ```

    Reserved query-string params (``_stream`` / ``_chunk_size``) work the same as GET.
    Streaming POST is supported but rare in practice — the SPA streams via GET to keep
    the SSE consumer simple."""
    require_permission(principal, f"sql:{connector}:{query}")
    qp = dict(request.query_params)
    stream = _streaming_requested(qp)
    chunk_size = _as_int(qp.get("_chunk_size"))
    limit = _as_int(body.get("max_rows")) if body else None  # body {"params": …, "max_rows": N}
    params = _params_from_body(body)
    if stream:
        return _stream_sql_ndjson(
            connectors, connector, query, params,
            language=request_language(request), max_rows=limit, user=principal.username,
            screens=screens, chunk_size=chunk_size,
        )
    return await _run_sql(
        connectors, connector, query, params,
        language=request_language(request), max_rows=limit, user=principal.username,
        screens=screens, changesets=getattr(request.app.state, "changesets_db", None),
    )


# --------------------------------------------------------------------------- #
# API (HTTP) connectors
# --------------------------------------------------------------------------- #


@router.post(
    "/http/{connector}/{endpoint}",
    summary="Call API endpoint",
    responses={
        200: {"description": "Always — upstream failure is encoded in the ``success`` / ``status_code`` fields of the structured ``ApiResult`` body."},
        401: {"description": "Missing / invalid access token."},
        403: {"description": "Caller lacks ``api:<connector>:<endpoint>``."},
        404: {"description": "Unknown connector or endpoint."},
    },
)
async def http_call(
    connector: str, endpoint: str, principal: CurrentPrincipal, connectors: Connectors, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Forward a call to one of *connector*'s configured HTTP endpoints. The connector
    applies its base URL + auth + retry policy + per-endpoint method / path; the body
    here carries the call's params (mapped to query string / body / path placeholders
    depending on the endpoint config).

    **Upstream failures never raise**: an unreachable / non-2xx upstream comes back as a
    structured ``ApiResult`` with ``success: false`` and the captured ``status_code`` +
    ``error`` so the SPA can render the failure inline without distinguishing transport
    errors from application errors. Liberty-level errors (unknown connector, unknown
    endpoint, missing permission) DO raise as 4xx so an integrator sees them clearly."""
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
