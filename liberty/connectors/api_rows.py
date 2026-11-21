"""The rows of an API endpoint, served the way a query's are — so a screen can read an endpoint.

A screen reads a named query of an SQL connector: the grid asks for it and receives the columns
(:class:`~liberty.connectors.sql.StreamMeta`), the rows by chunks (``StreamRows``) and a last line
(``StreamDone``). An endpoint whose answer holds a list of records can feed the same grid: it says
where the list is (``rows_field``), how its records' keys become column names (``row_map``), where
the columns' titles are (``labels_field``) and where the server says there is more
(``more_field``) — :class:`~liberty.connectors.config.EndpointDef`.

What differs from a query, and is by design:

* **the filters are the endpoint's parameters** — a request parameter that is not one of them is
  dropped (the screen's filter operators, a filter on a column the endpoint cannot filter), and
  the columns say which of them can be filtered;
* **the row limit is the endpoint's own**, capped again here — and the screen's when the endpoint
  names the parameter that carries it (``limit_param``); there is no paging — the answer says it
  is truncated when the server says there is more;
* **the row-level data scope** is applied to the rows returned, with the rules' own modes (a rule
  whose column the rows do not carry is ignored or denies, as for a query);
* sort, group and totals happen in the browser on what was returned.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from liberty.auth.scope import DataScope
from liberty.connectors.api import APIConnector, extract_path
from liberty.connectors.base import ConnectorError
from liberty.connectors.config import ColumnHint, EndpointDef
from liberty.connectors.dictionary import DictionaryFile
from liberty.connectors.sql import Column, StreamDone, StreamMeta, StreamRows, _apply_column_hints

DEFAULT_MAX_ROWS = 1000
DEFAULT_CHUNK_SIZE = 100


class ApiRowsError(ConnectorError):
    """The endpoint's call failed, or its answer does not hold the rows it says it holds."""


def rows_endpoint(conn: Any, name: str) -> EndpointDef | None:
    """*name* on *conn* when it is an endpoint a screen can read (an API connector's endpoint with
    a ``rows_field``), else ``None`` — never raises: the caller decides what an unknown name is."""
    if not isinstance(conn, APIConnector):
        return None
    endpoint = next((e for e in conn.config.endpoints if e.name == name), None)
    return endpoint if endpoint is not None and endpoint.rows_field else None


def read_permission(connectors: Any, connector: str, query: str) -> str:
    """The permission that lets a caller read *query* on *connector*: ``api:…`` when it is an
    endpoint a screen can read, else the query's ``sql:…`` — one definition for the route, the
    screen catalog and the menus."""
    try:
        conn = connectors.get(connector) if connectors is not None else None
    except Exception:  # noqa: BLE001 — an unknown / unlicensed connector: the SQL answer, as before
        conn = None
    return f"api:{connector}:{query}" if rows_endpoint(conn, query) is not None else f"sql:{connector}:{query}"


def endpoint_params(endpoint: EndpointDef, params: dict[str, Any] | None) -> dict[str, Any]:
    """The request's parameters the endpoint has — matched without regard to case (a screen sends
    its filters by column name), everything else dropped."""
    names = {p.name.lower(): p.name for p in endpoint.params}
    out: dict[str, Any] = {}
    for key, value in (params or {}).items():
        actual = names.get(str(key).lower())
        if isinstance(value, str):
            # a screen's value may come padded — a lookup on a database that stores codes blank-padded
            # (JD Edwards: ``'         E'``) offers them as stored; an API takes the value itself
            value = value.strip()
        if actual is not None and value not in (None, ""):
            out[actual] = value
    return out


def _records(endpoint: EndpointDef, parsed: Any) -> list[dict[str, Any]]:
    found = extract_path(parsed, endpoint.rows_field)
    if found is None:
        return []
    if not isinstance(found, list):
        raise ApiRowsError(f"{endpoint.name}: '{endpoint.rows_field}' is not a list in the answer")
    records = [r for r in found if isinstance(r, dict)]
    if not endpoint.row_map:
        return records
    return [{column: extract_path(r, key) if "." in key else r.get(key) for column, key in endpoint.row_map.items()} for r in records]


def _columns(endpoint: EndpointDef, parsed: Any, rows: list[dict[str, Any]], hints: list[ColumnHint] | None,
             dictionary: DictionaryFile, language: str | None, dictionary_scope: str | None) -> list[Column]:
    names = list(endpoint.row_map) if endpoint.row_map else list(dict.fromkeys(k for r in rows[:50] for k in r))
    titles = extract_path(parsed, endpoint.labels_field) if endpoint.labels_field else None
    key_of = endpoint.row_map or {n: n for n in names}
    discovered = [Column(name=n, label=str(titles[key_of[n]]) if isinstance(titles, dict) and titles.get(key_of[n]) else None) for n in names]
    columns = _apply_column_hints(discovered, hints or [], dictionary, language, connector=dictionary_scope)
    by_name = {c.name: c for c in discovered}
    filterable = {p.name.lower() for p in endpoint.params}
    for c in columns:
        if c.label is None and by_name.get(c.name) is not None:
            c.label = by_name[c.name].label                       # the server's own title, when nothing else names the column
        if c.filter and c.name.lower() not in filterable:
            c.filter = False                                      # the endpoint cannot filter on it: not offered
    return columns


async def api_row_events(
    conn: APIConnector, endpoint_name: str, params: dict[str, Any] | None = None, *,
    dictionary: DictionaryFile, language: str | None = None, max_rows: int | None = None,
    column_hints: list[ColumnHint] | None = None, screen_max_rows: int | None = None,
    dictionary_scope: str | None = None, chunk_size: int = DEFAULT_CHUNK_SIZE, scope: DataScope | None = None,
) -> AsyncIterator[StreamMeta | StreamRows | StreamDone]:
    """Call *endpoint_name* and yield its rows as a query's stream does. The values come from a
    user (``untrusted``). Raises :class:`ApiRowsError` when the call fails."""
    endpoint = rows_endpoint(conn, endpoint_name)
    if endpoint is None:
        raise ApiRowsError(f"{conn.name}.{endpoint_name}: not an endpoint a screen can read (no rows_field)")
    started = time.perf_counter()
    sent_params = endpoint_params(endpoint, params)
    limit = max_rows if max_rows is not None else screen_max_rows
    limit_name = next((p.name for p in endpoint.params if endpoint.limit_param and p.name.lower() == endpoint.limit_param.lower()), None)
    if limit_name is not None and limit is not None:
        # the screen's row limit is the server's: asked for there, instead of cutting what came back
        # (a limit of 0 = the columns only: one row is enough to know them)
        sent_params[limit_name] = str(max(1, min(limit, 100_000)))
    result = await conn.call(endpoint_name, sent_params, untrusted=True)
    if not result.success:
        # the server's own words when it gave some (an HTTP 500 with a text body), after the status
        said = (result.body or "").strip() if result.json is None else ""
        raise ApiRowsError(f"{conn.name}.{endpoint_name}: {result.error or f'HTTP {result.status_code}'}" + (f" — {said[:300]}" if said else ""))
    rows = _records(endpoint, result.json)
    more = bool(extract_path(result.json, endpoint.more_field)) if endpoint.more_field else False
    if scope is not None:
        dd_of = {h.name.lower(): (h.dd or h.name) for h in column_hints or [] if h.dd != ""}
        rows = [r for r in rows if scope.allows_row(r, dd_of, connector=conn.name)]
    cap = max(1, min(max_rows or screen_max_rows or DEFAULT_MAX_ROWS, 100_000))
    truncated = more or len(rows) > cap
    columns = _columns(endpoint, result.json, rows, column_hints, dictionary, language, dictionary_scope)
    # a limit of 0 asks for the columns only (the screen editor's column list), as for a query
    rows = [] if max_rows == 0 else rows[:cap]
    size = max(1, min(chunk_size or DEFAULT_CHUNK_SIZE, 5000))
    yield StreamMeta(columns=columns, cap=cap, chunk_size=size)
    sent = 0
    for start in range(0, len(rows), size):
        chunk = rows[start:start + size]
        sent += len(chunk)
        yield StreamRows(rows=chunk, sent=sent)
    yield StreamDone(total=sent, truncated=truncated, duration_ms=(time.perf_counter() - started) * 1000.0)
