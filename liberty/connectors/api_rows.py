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


def api_connector(connectors: Any, connector: str) -> APIConnector | None:
    """*connector* when it is an API connector, else ``None`` — an unknown / unlicensed one included."""
    try:
        conn = connectors.get(connector) if connectors is not None else None
    except Exception:  # noqa: BLE001 — an unknown / unlicensed connector: the SQL answer, as before
        return None
    return conn if isinstance(conn, APIConnector) else None


def endpoint_of(conn: Any, name: str | None) -> EndpointDef | None:
    """The endpoint *name* of an API connector, else ``None``."""
    if not isinstance(conn, APIConnector) or not name:
        return None
    return next((e for e in conn.config.endpoints if e.name == name), None)


def query_permission(connectors: Any, connector: str, query: str) -> str:
    """The permission that lets a caller run *query* on *connector* through a screen: ``api:…``
    when the connector is an API connector (the query is one of its endpoints — a screen's read
    or one of its saves), else the query's ``sql:…`` — one definition for the routes, the screen
    catalog, the menus and the server-side save."""
    return f"api:{connector}:{query}" if api_connector(connectors, connector) is not None else f"sql:{connector}:{query}"


read_permission = query_permission


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


def columns_for(endpoint: EndpointDef, hints: list[ColumnHint] | None, prefix: int = 0,
                row: dict[str, Any] | None = None) -> dict[str, str]:
    """Which column of a screen feeds each variable of the endpoint that saves it, when the screen
    is not read from that endpoint (``Screen.save_connector``: a query's columns, a form's
    variables): the column carrying the variable's dictionary item — its hint's ``dd``, or, on a
    ``dd_prefix`` connector, its name minus the prefix (``ABALPH`` → ``ALPH``; from the hints, else
    from the names the *row* was sent with). ``{variable: column}``, both lower-cased; a variable
    that has a column of its own name is not listed — that column is it."""
    wanted = {p.name.lower() for p in endpoint.params}
    own = {h.name.lower() for h in hints or []} | {str(k).lower() for k in row or {}}
    out: dict[str, str] = {}
    names = [(h.name, h.dd) for h in hints or []] + [(str(k), None) for k in row or {} if not str(k).upper().endswith("_ORIGINAL")]
    for name, dd in names:
        low = name.lower()
        for item in ((dd or "").lower(), low[prefix:] if prefix and len(low) > prefix else ""):
            if item and item in wanted and item not in own:
                out.setdefault(item, low)
    return out


def write_params(endpoint: EndpointDef, params: dict[str, Any] | None, columns: dict[str, str] | None = None, *,
                 changed_only: bool = False) -> dict[str, Any]:
    """A saved row's values for the endpoint that saves it. A screen sends the row by column name,
    upper-cased, with what the row held when it was loaded under ``<COL>_ORIGINAL``: a variable
    takes the value of its own name (so a variable ``AN8_ORIGINAL`` gets the loaded key), without
    regard to case — or of the column *columns* gives it (:func:`columns_for`).

    A variable the row does NOT carry is left out — it keeps its default, and a function that
    serves the endpoint is told it was not given (a field nobody sent is not typed blank in the
    form). One the row carries BLANK is given blank: the user cleared it — unless it has a
    default, which then applies as it always did.

    *changed_only* (an UPDATE through an endpoint served by a function): a variable whose column
    still holds what the row was loaded with (``<COL>_ORIGINAL``) is left out too — the function
    types only what the user changed, as the user would (a form re-checks a field typed again:
    JDE warned of a duplicate name on every save). A required variable — the key — is always given."""
    given = {str(k).lower(): v for k, v in (params or {}).items()}

    def clean(value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value     # a code read from a database that pads it (JD Edwards)

    out: dict[str, Any] = {}
    for p in endpoint.params:
        low = p.name.lower()
        key = low if low in given else (columns or {}).get(low)
        if key is None or key not in given:
            continue
        value = clean(given[key])
        was = f"{key}_original"
        if changed_only and not p.required and was in given and str(clean(given[was]) if given[was] is not None else "") == str(value if value is not None else ""):
            continue
        if value in (None, ""):
            if p.default is None:
                out[p.name] = ""
            continue
        out[p.name] = value
    return out


async def api_write(conn: APIConnector, endpoint_name: str, params: dict[str, Any] | None, *, statement_type: str = "CALL",
                    columns: dict[str, str] | None = None) -> dict[str, Any]:
    """Call the endpoint that saves a screen's row and answer as a statement does (``rowcount`` 1,
    the values the endpoint maps out of its answer — ``response_map`` — as ``resolved_binds``, so
    a number the server assigned reaches the form). Raises :class:`ApiRowsError` when the call
    fails or the server refuses (the endpoint's ``error_fields``): the message is the server's."""
    endpoint = endpoint_of(conn, endpoint_name)
    if endpoint is None:
        raise ApiRowsError(f"{conn.name}.{endpoint_name}: no such endpoint")
    if not endpoint.writable:
        raise ApiRowsError(f"{conn.name}.{endpoint_name}: this endpoint is not marked as changing data (writable) — a screen cannot save through it")
    started = time.perf_counter()
    # an update served by a function types only what changed; an HTTP endpoint's template needs every value
    only_changed = statement_type == "UPDATE" and bool(endpoint.callable)
    result = await conn.call(endpoint_name, write_params(endpoint, params, columns, changed_only=only_changed), untrusted=True)
    if not result.success:
        said = (result.body or "").strip() if result.json is None else ""
        raise ApiRowsError(f"{result.error or f'HTTP {result.status_code}'}" + (f" — {said[:300]}" if said else ""))
    out: dict[str, Any] = {
        "connector": conn.name, "query": endpoint_name, "statement_type": statement_type, "columns": [], "rows": [],
        "row_count": 1, "rowcount": 1, "truncated": False, "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "resolved_binds": {str(k).upper(): v for k, v in (result.mapped or {}).items() if v is not None},
    }
    if result.warnings:
        out["warnings"] = result.warnings
    return out


def sensitive_names(dictionary: DictionaryFile, names: list[str], hints: list[ColumnHint] | None, dictionary_scope: str | None) -> set[str]:
    """Which of the columns *names* hold data flagged sensitive (returned as given): the screen
    column's own ``sensitive`` when it is set, else the dictionary item the column carries (its
    ``dd``, or its name) — :meth:`SQLConnector.sensitive_columns` without a connector's prefix."""
    by_name = {h.name.lower(): h for h in hints or []}
    out: set[str] = set()
    for name in names:
        h = by_name.get(name.lower())
        if h is not None and h.sensitive is not None:
            if h.sensitive:
                out.add(name)
            continue
        if h is not None and h.dd == "":
            continue
        item = (h.dd if h is not None and h.dd else None) or name
        entry = next((e for e in (dictionary.find_entry(k, connector=dictionary_scope) for k in dict.fromkeys((item, item.upper(), item.lower()))) if e is not None), None)
        if entry is not None and getattr(entry, "sensitive", False):
            out.add(name)
    return out


def _means_empty(endpoint: EndpointDef, result: Any) -> bool:
    """A failed call that is the server's way of saying "nothing to list" (``EndpointDef.empty_on``):
    one of the texts is in its error, or is its HTTP status."""
    if not endpoint.empty_on:
        return False
    said = f"{result.error or ''} {(result.body or '')[:2000]}".lower()
    return any(t.strip() and (t.strip() == str(result.status_code) or t.strip().lower() in said) for t in endpoint.empty_on)


def _records(endpoint: EndpointDef, parsed: Any) -> list[dict[str, Any]]:
    found = extract_path(parsed, endpoint.rows_field)
    if found is None:
        # the list is not where the endpoint says: an empty answer — unless the answer plainly holds
        # something else at its top, which is a wrong ``rows_field`` and would show as a screen that
        # stays empty for no visible reason (a server whose documentation names the key differently)
        first = str(endpoint.rows_field or "").split(".")[0]
        if isinstance(parsed, dict) and parsed and first and first not in parsed:
            raise ApiRowsError(f"{endpoint.name}: the answer has no '{first}' — it holds: {', '.join(list(parsed)[:8])}. "
                               "Check where the endpoint says its rows are (rows_field).")
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
    if not names and not rows and hints:
        # nothing to list, and no map to know the columns from: the screen's own, so its grid is drawn empty
        names = [h.name for h in hints]
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
    flagged = sensitive_names(dictionary, [c.name for c in columns], hints, dictionary_scope)
    for c in columns:
        if c.name in flagged:
            c.sensitive = True                                    # left out of what the AI assistant reads, as for a query
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
    # the server refuses when it has nothing to list (``empty_on``): an empty list
    empty = not result.success and _means_empty(endpoint, result)
    if not result.success and not empty:
        # the server's own words when it gave some (an HTTP 500 with a text body), after the status
        said = (result.body or "").strip() if result.json is None else ""
        raise ApiRowsError(f"{conn.name}.{endpoint_name}: {result.error or f'HTTP {result.status_code}'}" + (f" — {said[:300]}" if said else ""))
    answer = None if empty else result.json
    rows = _records(endpoint, answer)
    more = bool(extract_path(answer, endpoint.more_field)) if endpoint.more_field else False
    if scope is not None:
        dd_of = {h.name.lower(): (h.dd or h.name) for h in column_hints or [] if h.dd != ""}
        rows = [r for r in rows if scope.allows_row(r, dd_of, connector=conn.name)]
    cap = max(1, min(max_rows or screen_max_rows or DEFAULT_MAX_ROWS, 100_000))
    truncated = more or len(rows) > cap
    columns = _columns(endpoint, answer, rows, column_hints, dictionary, language, dictionary_scope)
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
