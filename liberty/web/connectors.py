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
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from liberty.auth.dependencies import CurrentPrincipal
from liberty.connectors import ConnectorRegistry
from liberty.connectors.base import ConnectorError, detect_statement_type
from liberty.connectors.config import ColumnHint
from liberty.connectors.introspect import introspect_pool, list_pool_schemas, resolve_schema_token
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
_RESERVED_BODY_KEYS = {"params", "max_rows", "_change_context"}


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
    group_slots = (("update_query", "update"), ("insert_query", "insert"), ("delete_query", "delete"))
    for app, app_screens in screens.screens.items():
        for s in app_screens.values():
            eff = s.connector or app
            if eff == connector:
                for attr, slot in slots:
                    if getattr(s, attr) == query:
                        return s, slot, app
            # Column-group write queries (the 1:1 related-table CRUD) also resolve to this screen —
            # so the group write inherits the screen's column hints and dictionary scope, and a group
            # column's ``dd`` mapping (e.g. ULMUSE → MUSE) fires its LOGIN/SYSDATE/etc. rule just like
            # the main write. A group may target a DIFFERENT connector than the screen, so match on
            # the group's own connector, not the screen's.
            for grp in getattr(s, "column_groups", None) or []:
                g_conn = getattr(grp, "connector", None) or eff
                if g_conn != connector:
                    continue
                for attr, _slot in group_slots:
                    if getattr(grp, attr, None) == query:
                        # Marked ``group`` (not the CRUD slot) so the route knows to take only the
                        # screen's column hints + dict scope from this match — NOT its audit_table
                        # (which is the MAIN table's) or its change-tracking (keyed on the main row).
                        return s, "group", app
    return None, None, None


def _resolve_action_screen(screens: ScreensFile | None, action_context: dict[str, Any] | None) -> Screen | None:
    """The originating :class:`Screen` for a tagged screen-action call. The frontend tags each
    action backend call (run_query / call_api / call_plugin) fired from a change-tracked screen's
    lifecycle hook with ``{app, screen}`` so the write/invocation is captured into THAT screen's
    package (the action's own query/endpoint isn't a screen). ``None`` when untagged or not found."""
    if not screens or not action_context:
        return None
    app = action_context.get("app")
    sid = action_context.get("screen")
    if not app or not sid:
        return None
    return (screens.screens.get(app) or {}).get(sid)


def _find_group_for_query(screen: Screen, connector: str, query: str, app: str | None) -> Any | None:
    """The :class:`ColumnGroup` on *screen* whose write query is *(connector, query)* — used to
    capture a 1:1 related-table write into the change package with the GROUP's key (the related
    table's key, e.g. the FK) rather than the main row's. The group's effective connector follows
    the same fallback as :func:`_find_screen_for_query` (``group.connector`` → screen's →
    ``app``). ``None`` when no group matches."""
    eff = screen.connector or (app or "")
    for grp in getattr(screen, "column_groups", None) or []:
        g_conn = getattr(grp, "connector", None) or eff
        if g_conn != connector:
            continue
        if query in (
            getattr(grp, "update_query", None),
            getattr(grp, "insert_query", None),
            getattr(grp, "delete_query", None),
        ):
            return grp
    return None


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
    action_context: dict[str, Any] | None = None,
    screen_hint: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Run *query* on *connector* with *params*. When a matching :class:`Screen` is found,
    thread its per-screen behaviour (column hints, ``audit_table``, ``max_rows``, dictionary
    scope = the screen's app) into the SQL connector. A query with no screen runs unadorned
    — no audit, no filter wrap, no per-screen rule resolution; dictionary lookup by bind name
    still applies for write-side rule coercion using the connector's own name as scope.

    ``screen_hint`` (``(app, screen_id)``) pins the EXACT screen whose hints to apply, for when
    several screens share one ``read_query`` (e.g. a copied F95921 screen with hidden columns
    tweaked): without it, ``_find_screen_for_query`` returns the FIRST match and the copy's hints
    are ignored. The caller (a screen TableView / a nested-table tab) knows its own screen id."""
    screen, slot, screen_app = (None, None, None)
    if screen_hint and screens:
        app_h, sid_h = screen_hint
        screen = (screens.screens.get(app_h) or {}).get(sid_h)
        if screen is not None:
            slot, screen_app = "read", app_h
    if screen is None:
        screen, slot, screen_app = (
            _find_screen_for_query(screens, connector, query) if screens else (None, None, None)
        )
    # A column-group write resolves to its screen for the COLUMN HINTS + dict scope (so a group
    # column's ``dd`` rule — e.g. ULMUSE → MUSE → LOGIN — fires like the main write). But its audit
    # + change-capture belong to a DIFFERENT table than the screen's main one, so don't inherit
    # those here (the main audit table / main-row key would be wrong for the related row).
    is_group = slot == "group"
    column_hints = _column_hints_for(screen)
    audit_table = None if is_group else (screen.audit_table if screen else None)
    screen_max_rows = screen.max_rows if screen else None
    # Dictionary scope follows the screen's app (where operator metadata lives), not the
    # data-pool connector. When the query has no screen of its OWN but was fired as a screen
    # ACTION (tagged with the originating screen via ``action_context``), resolve the dictionary
    # under THAT screen's app — the same scope its main write uses. So an audit bind named by its
    # dd_id (``:USER`` / ``:PID`` / ``:UPMJ`` …) in a shared-action query gets the LOGIN / DEFAULT
    # / SYSDATE rule + JDE format applied exactly like the source screen's own write, instead of
    # silently no-op'ing because the data-pool connector's scope holds no dictionary. Falls back
    # to the connector's own name when there's no screen AND no action context (a query opened by
    # an AI tool / external caller / dashboard widget — those resolve against the connector scope).
    dict_scope = screen_app if screen is not None else ((action_context or {}).get("app") or None)
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
    # trail. No-op for SELECTs / untracked screens (capture_write guards both). A column-group
    # write (the 1:1 related table) is ALSO captured so the promotion bundle recreates the
    # related row — but keyed on the GROUP's key (its FK), and with no standalone read query
    # (the related table is only JOINed into the screen's read), so apply reports it
    # ``unverified`` rather than skipping it. Same display ``entity`` as the main row so the two
    # group together in the package view; compaction keys on the physical table so they stay
    # distinct ops despite sharing the parent key.
    # A screen ACTION's run_query write (fired from on_save/on_insert/on_update/on_delete). The
    # frontend tags it with the originating change-tracked screen (``action_context``) so it lands in
    # the SAME package as that screen's main write. It targets an arbitrary table (not the screen's
    # own), so there's no natural key / read query — captured with the resolved binds and replayed
    # verbatim (``unverified`` drift) on apply. This path takes precedence over the screen-match
    # capture below so an action query that happens to match some other screen isn't double-captured.
    action_screen = _resolve_action_screen(screens, action_context) if (changesets is not None) else None
    if action_screen is not None and getattr(action_screen, "change_tracked", False):
        from liberty.changesets.capture import capture_write
        # Package scope = the originating screen's connector, so the action write groups with that
        # screen's main write (one bundle for the whole Save) even when it targets another connector.
        app_scope = getattr(action_screen, "connector", None) or (action_context or {}).get("app")
        try:
            await capture_write(
                changesets, connector=connector, query=query,
                statement_type=result.statement_type, params=(result.bound_params or params), user=user,
                key_columns=[], read_query=None,
                entity=getattr(action_screen, "change_entity", None), change_tracked=True,
                application=app_scope, post_apply_ids=list(getattr(action_screen, "post_apply", None) or []),
                # Attribute this action write to the role/row it was fired for (the originating
                # screen's key, resolved client-side) so the package groups it under that record.
                key_override=(action_context or {}).get("entity_key") or None,
                source_action=(action_context or {}).get("action") or None,
            )
        except Exception as exc:  # noqa: BLE001 — capture must never fail a committed write
            _log.error("action change capture failed for %s.%s: %s", connector, query, exc)
    elif changesets is not None and action_context is None and screen is not None and getattr(screen, "change_tracked", False):
        from liberty.changesets.capture import capture_write
        if is_group:
            grp = _find_group_for_query(screen, connector, query, screen_app)
            cap_keys = list(getattr(grp, "key_columns", None) or []) if grp else []
            cap_read_query: str | None = None
        else:
            # ``effective_key_columns()`` — NOT the raw ``key_columns`` list, which is the legacy
            # explicit field and is usually empty now: the row key is ticked per-column via
            # ``ColumnHint.key`` in the Columns tab. Without this the main entry captures no natural
            # key (the package shows it with no ULUSER=…), can't be drift-checked, and can't merge.
            cap_keys = list(screen.effective_key_columns())
            cap_read_query = screen.read_query
        # Capture the FULL resolved binds (DD defaults + LOGIN/SYSDATE + sequences baked in), not
        # the raw request params — otherwise a value the server filled on an empty bind (PID,
        # JOBN, an editable default, an audit stamp) is missing from the package and never lands
        # on apply. Falls back to the request params if the connector didn't surface them.
        cap_params = result.bound_params or params
        try:
            await capture_write(
                changesets, connector=connector, query=query,
                statement_type=result.statement_type, params=cap_params, user=user,
                key_columns=cap_keys, read_query=cap_read_query,
                entity=getattr(screen, "change_entity", None), change_tracked=True,
                post_apply_ids=list(getattr(screen, "post_apply", None) or []),
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
    screen_hint: tuple[str, str] | None = None,
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
    screen, _slot, screen_app = (None, None, None)
    if screen_hint and screens:
        app_h, sid_h = screen_hint
        screen = (screens.screens.get(app_h) or {}).get(sid_h)
        if screen is not None:
            screen_app = app_h
    if screen is None:
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
    # Resolve a ``#SCHEMA.<KEY>#`` token the builder passes from a query's FROM to the pool's real
    # schema — otherwise the owner-match below finds nothing and the wizard shows an empty table.
    if only_schema:
        only_schema = resolve_schema_token(only_schema, connectors.pools.schemas(conn.pool_name))
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
    # ``_screen`` / ``_app`` pin the EXACT screen whose hints to apply when several screens share
    # this read_query (a copied screen with different hidden columns). The TableView / nested-table
    # caller passes its own ids; absent → first-match (the historical behaviour).
    sid = qp.pop("_screen", None); sapp = qp.pop("_app", None)
    screen_hint = (sapp, sid) if (sid and sapp) else None
    if stream:
        return _stream_sql_ndjson(
            connectors, connector, query, qp,
            language=request_language(request), max_rows=limit, user=principal.username,
            screens=screens, chunk_size=chunk_size, screen_hint=screen_hint,
        )
    return await _run_sql(
        connectors, connector, query, qp,
        language=request_language(request), max_rows=limit, user=principal.username,
        screens=screens, screen_hint=screen_hint,
    )


class ImportBody(BaseModel):
    """Batch Excel-import request: validate (``commit=false``) or persist (``commit=true``) a list
    of already-mapped rows through the screen's insert / update queries."""

    model_config = ConfigDict(populate_by_name=True)
    mode: str = Field(default="upsert", description="upsert | insert | update.")
    insert_query: str | None = Field(default=None, description="Named INSERT query (insert / upsert).")
    update_query: str | None = Field(default=None, description="Named UPDATE query (update / upsert).")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="Rows, keyed by column name.")
    commit: bool = Field(default=False, description="false → dry-run validate (rolled back); true → persist the rows.")
    screen_app: str | None = Field(default=None, description="Screen app — pins column hints / audit / dict scope.")
    screen_id: str | None = Field(default=None, description="Screen id (with screen_app).")


def _import_error(exc: Exception) -> str:
    """A compact, operator-readable message from a DB error: keep the driver/ORA line, drop the huge
    ``[SQL: …] [parameters: …]`` tail SQLAlchemy appends (it's noise in the per-row report)."""
    msg = f"{type(exc).__name__}: {exc}"
    cut = msg.find("[SQL:")  # SQLAlchemy separates the tail with a newline, not a space
    return (msg[:cut] if cut > 0 else msg).strip()


def _import_row_params(row: dict[str, Any]) -> dict[str, Any]:
    """Bind params for one imported row — exactly what the grid/dialog Save sends (``withUpper`` +
    ``originalKeys``), which the raw mapped row was missing:

    * **UPPERCASE keys** — the JDE queries bind ``:FSSETY`` etc., but the mapped row keys follow the
      result columns' case (often lowercase). Without folding, every bind comes through NULL/blank and
      the WHERE matches nothing — the bug behind "everything validates".
    * **``:<KEY>_ORIGINAL`` WHERE binds** — an UPDATE identifies the row by ``:FSSETY_ORIGINAL`` …; on
      import the row's own (imported) key values ARE that identity (no separate 'old' key), so bind
      each ``<COL>_ORIGINAL`` to the same value. Params a given statement doesn't reference are ignored
      by the bind layer, so this one shape serves INSERT and UPDATE alike."""
    up = {str(k).upper(): v for k, v in row.items()}
    for k, v in list(up.items()):
        up.setdefault(f"{k}_ORIGINAL", v)
    return up


@router.post(
    "/sql/{connector}/_import",
    summary="Import rows",
    responses={
        400: {"description": "Bad mode or no insert/update query for the chosen mode."},
        403: {"description": "Caller lacks ``sql:<connector>:<query>`` on the insert/update query."},
        404: {"description": "Unknown connector."},
    },
)
async def sql_import(
    connector: str, request: Request, principal: CurrentPrincipal,
    connectors: Connectors, screens: Screens, body: ImportBody,
) -> dict[str, Any]:
    """Validate or persist a batch of imported rows against *connector*'s screen queries.

    Each row runs through the screen's ``insert_query`` / ``update_query`` with the SAME machinery
    as a normal Save (dictionary coercion, SEQUENCE/NN, audit mirror), so the rowcount and any DB
    error are exactly what a real save sees. With ``commit=false`` each row's write is rolled back
    (a dry-run, via ``execute(dry_run=True)``) — the validation pass. With ``commit=true`` the valid
    rows are written for real, each in its own transaction (same per-row model as the grid Save),
    and change-tracked screens capture them via the standard path.

    ``upsert`` decides per row by a dry-run UPDATE probe: a row that matches → UPDATE, else INSERT —
    no per-screen "select by key" query needed. ``insert`` / ``update`` force the one query.

    Returns ``{committed, mode, valid, invalid, results: [{index, action, ok, error?, rowcount?}]}``.
    """
    mode = (body.mode or "upsert").lower()
    if mode not in ("upsert", "insert", "update"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="mode must be upsert | insert | update.")
    # Permission gate on EVERY query the chosen mode may run.
    needed: set[str] = set()
    if mode in ("upsert", "insert") and body.insert_query:
        needed.add(body.insert_query)
    if mode in ("upsert", "update") and body.update_query:
        needed.add(body.update_query)
    if not needed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"no {'insert' if mode == 'insert' else 'update' if mode == 'update' else 'insert/update'} query for mode {mode!r}.")
    for q in needed:
        require_permission(principal, f"sql:{connector}:{q}")

    # Resolve the screen (explicit hint, else by the insert/update query) for column hints / audit /
    # dictionary scope — so the dry-run coerces + audits exactly like the real Save.
    screen_hint = (body.screen_app, body.screen_id) if (body.screen_app and body.screen_id) else None
    screen, screen_app = (None, None)
    if screen_hint and screens:
        screen = (screens.screens.get(body.screen_app) or {}).get(body.screen_id)  # type: ignore[arg-type]
        screen_app = body.screen_app if screen is not None else None
    if screen is None and screens:
        for q in (body.update_query, body.insert_query):
            if not q:
                continue
            screen, _slot, screen_app = _find_screen_for_query(screens, connector, q)
            if screen is not None:
                break
    column_hints = _column_hints_for(screen)
    audit_table = screen.audit_table if screen else None
    screen_max_rows = screen.max_rows if screen else None
    dict_scope = screen_app if screen is not None else None

    try:
        conn = connectors.sql(connector)
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    lang = request_language(request)
    user = principal.username
    changesets = getattr(request.app.state, "changesets_db", None)

    async def _probe_update(row: dict[str, Any]) -> int:
        """Dry-run the UPDATE to decide upsert direction; rowcount > 0 ⇒ a row matched."""
        res = await conn.execute(
            body.update_query, _import_row_params(row), language=lang, user=user, column_hints=column_hints,  # type: ignore[arg-type]
            audit_table=audit_table, screen_max_rows=screen_max_rows, dictionary_scope=dict_scope, dry_run=True,
        )
        return res.rowcount

    async def _run(query_name: str, row: dict[str, Any]) -> int:
        params = _import_row_params(row)
        if body.commit:
            # Real write through the full path (audit + change capture), same as the grid Save.
            res = await _run_sql(
                connectors, connector, query_name, params, language=lang, user=user,
                screens=screens, changesets=changesets, screen_hint=screen_hint,
            )
            return int(res.get("rowcount") or -1)
        res = await conn.execute(
            query_name, params, language=lang, user=user, column_hints=column_hints,
            audit_table=audit_table, screen_max_rows=screen_max_rows, dictionary_scope=dict_scope, dry_run=True,
        )
        return res.rowcount

    results: list[dict[str, Any]] = []
    for i, row in enumerate(body.rows):
        action: str | None = None
        try:
            if mode == "insert":
                action = "insert"
                # The dry-run INSERT runs the real statement through execute() (dictionary coercion,
                # dialect, CHAR padding, the table's PK), so a row whose key already exists raises the
                # PK/unique violation here exactly as a real save would — no separate probe needed.
                rc = await _run(body.insert_query, row)  # type: ignore[arg-type]
            elif mode == "update":
                action = "update"
                rc = await _run(body.update_query, row)  # type: ignore[arg-type]
                if rc == 0:
                    results.append({"index": i, "action": "update", "ok": False,
                                    "error": "no existing row matches the key columns."})
                    continue
            else:  # upsert — probe, then run the decided side
                matched = (await _probe_update(row)) if body.update_query else 0
                if matched and matched > 0:
                    action = "update"
                    rc = (await _run(body.update_query, row)) if body.commit else matched  # type: ignore[arg-type]
                else:
                    action = "insert"
                    if not body.insert_query:
                        results.append({"index": i, "action": "insert", "ok": False,
                                        "error": "row is new but no insert query is configured."})
                        continue
                    rc = await _run(body.insert_query, row)
            results.append({"index": i, "action": action, "ok": True, "rowcount": rc})
        except (ConnectorError, SQLAlchemyError) as exc:
            results.append({"index": i, "action": action, "ok": False, "error": _import_error(exc)})
    valid = sum(1 for r in results if r["ok"])
    return {"committed": body.commit, "mode": mode, "valid": valid,
            "invalid": len(results) - valid, "results": results}


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
    # ``_change_context`` (when present) marks this as a screen-action run_query fired from a
    # change-tracked screen's lifecycle hook — captured into that screen's package (see _run_sql).
    action_context = body.get("_change_context") if isinstance(body, dict) else None
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
        action_context=action_context if isinstance(action_context, dict) else None,
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
    connector: str, endpoint: str, request: Request, principal: CurrentPrincipal,
    connectors: Connectors, screens: Screens, body: dict[str, Any] | None = None,
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
    call_params = _params_from_body(body)
    result = await conn.call(endpoint, call_params)
    # Change-package capture for a call_api action fired from a change-tracked screen. A tracked
    # screen CAPTURES every call (so the package shows it for review); the action's ``change_replay``
    # opt-in — threaded in the tag as ``replay`` — decides whether APPLY actually re-fires it (an
    # external call's side effects can't be drift-checked). Only a SUCCESSFUL upstream call is
    # captured (a failed one didn't change anything). Best-effort.
    action_context = body.get("_change_context") if isinstance(body, dict) else None
    changesets = getattr(request.app.state, "changesets_db", None)
    action_screen = _resolve_action_screen(screens, action_context) if changesets is not None else None
    if action_screen is not None and getattr(action_screen, "change_tracked", False) and getattr(result, "success", True):
        from liberty.changesets.capture import capture_invocation
        from liberty.changesets.models import Operation
        # Package scope = the originating screen's connector (groups with its main write); the call's
        # own ``connector`` is the API target re-invoked on apply.
        app_scope = getattr(action_screen, "connector", None) or (action_context or {}).get("app")
        try:
            await capture_invocation(
                changesets, application=app_scope, connector=connector,
                operation=Operation.CALL_API.value, target=endpoint, params=call_params,
                entity=getattr(action_screen, "change_entity", None), user=principal.username,
                post_apply_ids=list(getattr(action_screen, "post_apply", None) or []),
                replay=bool((action_context or {}).get("replay", True)),
                key_override=(action_context or {}).get("entity_key") or None,
                source_action=(action_context or {}).get("action") or None,
            )
        except Exception as exc:  # noqa: BLE001 — capture must never fail the (committed) call
            _log.error("call_api change capture failed for %s.%s: %s", connector, endpoint, exc)
    return result.to_dict()
