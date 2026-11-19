"""A screen's row written on the server — what the record form does in the browser, for a
caller that has no browser (the assistant's ``screen_write`` tool).

The browser's save lives in ``frontend/src/pages/TableView/saveScreenRow.ts`` (the main table,
then each 1:1 related table of ``Screen.column_groups``) and in the form's own checks
(``ScreenDialog.tsx``: what is visible, required, disabled, forced by a ``default_when``). This
module is their twin, line for line where it matters:

* the row is **read first** through the screen's read query — the caller's data scope, the same
  formats — so an update binds the original values (``:<COL>_ORIGINAL``) exactly as the form
  does and never trusts a caller's idea of the row;
* every statement goes through :func:`liberty.web.connectors._run_sql`, the function behind
  ``POST /api/sql``: dictionary rules, ``write_when``, sequences, ``editable_when``, the audit
  table, the data scope, the change package, the notification rules — nothing is written again;
* the field rules are evaluated on the fields the screen route serves
  (:func:`liberty.web.screens._resolve_screen_field`), so a value the form would not let a user
  enter (a disabled field, a code outside its list, a missing required field) is refused here —
  a list being an enum or a LOOKUP, whose rows are read as the form reads them and checked as
  the grid's import checks them.

After the write the screen's save hooks run (the form's ``on_save``, then ``on_insert`` /
``on_update`` / ``on_delete``), on the server, through :mod:`liberty.screens.actions` — as the
form runs them, with the record as written. What it still refuses: a form that runs actions when
it opens (``on_load``). As in the browser, the main write, the related tables and the hooks are
separate statements, not one transaction: a hook that fails leaves the record written, and the
answer says so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from liberty.auth.principal import Principal
from liberty.connectors.base import ConnectorError, find_bind_params
from liberty.screens.config import Screen, ScreensFile

Row = dict[str, Any]
_ORIGINAL = "_ORIGINAL"
MASK = "••••••"
READ_CAP = 500                 # the most rows read to find the one record a key names


class ScreenWriteError(Exception):
    """A write the screen does not allow, said in words for the user."""


@dataclass(slots=True)
class WriteContext:
    """Who writes and through what — the pieces ``POST /api/sql`` takes from the request."""

    connectors: Any                     # ConnectorRegistry (its ``dictionary`` resolves the fields)
    screens: ScreensFile | None
    principal: Principal
    changesets: Any = None              # the change-packages store, when the server has one
    notify_ctx: tuple[Any, Any] | None = None
    language: str | None = None
    pool: str | None = None             # the environment picked for the page (X-Liberty-Pool)
    state: Any = None                   # the application state (shared actions, settings) — the save hooks' steps


# -- the form's helpers (dialogHelpers.ts) ------------------------------------- #


def value_for(name: str, row: Mapping[str, Any]) -> Any:
    """Case-insensitive read of a row (Postgres lowercases what the screens name in upper case)."""
    if name in row:
        return row[name]
    low = name.lower()
    for k, v in row.items():
        if str(k).lower() == low:
            return v
    return None


def _has(name: str, row: Mapping[str, Any]) -> bool:
    low = name.lower()
    return any(str(k).lower() == low for k in row)


def _empty(v: Any) -> bool:
    return v is None or v == ""


def eval_conditions(rules: list[dict[str, Any]] | None, values: Mapping[str, Any]) -> bool:
    """Every ``{field, value}`` rule holds (AND); values compared trimmed, a list = membership.
    No rule = False (the caller falls back to the static flag) — as ``evalConditions``."""
    if not rules:
        return False
    for r in rules:
        live = value_for(str(r.get("field") or ""), values)
        live_s = ("" if live is None else str(live)).strip()
        want = r.get("value")
        if isinstance(want, list):
            if not any(str(x).strip() == live_s for x in want):
                return False
        elif live_s != str(want).strip():
            return False
    return True


def _matched_default_when(rules: list[dict[str, Any]] | None, values: Mapping[str, Any]) -> dict[str, Any] | None:
    for r in rules or []:
        if eval_conditions([{"field": r.get("field"), "value": r.get("value")}], values):
            return r
    return None


def field_state(field: Mapping[str, Any], values: Mapping[str, Any], mode: str) -> dict[str, bool]:
    """``fieldStateOf``: a ``*_when`` list, when present, replaces the static flag."""
    visible = eval_conditions(field.get("visible_when"), values) if field.get("visible_when") else not field.get("hidden")
    required = eval_conditions(field.get("required_when"), values) if field.get("required_when") else bool(field.get("required"))
    disabled = eval_conditions(field.get("disabled_when"), values) if field.get("disabled_when") else bool(field.get("disabled"))
    mode_locked = bool(field.get("disable_on_add")) if mode == "add" else bool(field.get("disable_on_edit"))
    dw = _matched_default_when(field.get("default_when"), values)
    forced = dw is not None and dw.get("lock") is not False
    return {"visible": bool(visible), "required": required, "disabled": disabled or mode_locked or forced}


def active_rule(field: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any] | None:
    """The field's rule for these values: the first ``rules_when`` that holds, else the base rule."""
    return active_rule_and_binds(field, values)[0]


def active_rule_and_binds(field: Mapping[str, Any], values: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[Any]]:
    """The rule for these values with ITS lookup binds — a conditional rule (``rules_when``)
    carries its own, the base rule the field's (``activeRulesWhen`` in the form)."""
    for rw in field.get("rules_when") or []:
        if eval_conditions([{"field": rw.get("field"), "value": rw.get("value")}], values):
            return rw.get("rule"), list(rw.get("lookup_param_binds") or [])
    return field.get("rule"), list(field.get("lookup_param_binds") or [])


def with_upper(row: Mapping[str, Any]) -> Row:
    out: Row = dict(row)
    for k, v in row.items():
        out[str(k).upper()] = v
    return out


def original_keys(row: Mapping[str, Any]) -> Row:
    return {f"{k}{_ORIGINAL}": v for k, v in row.items()}


def overlay_ci(base: Mapping[str, Any], top: Mapping[str, Any]) -> Row:
    out: Row = dict(base)
    for k, v in top.items():
        for existing in [e for e in out if e != k and str(e).lower() == str(k).lower()]:
            del out[existing]
        out[k] = v
    return out


def resolve_bind_list(binds: Any, values: Mapping[str, Any]) -> Row:
    """``resolveBindList``: a literal, or another column's value; empty ones dropped, the
    ``#…#`` built-ins left to the server's own rules."""
    out: Row = {}
    for b in binds or []:
        get = b.get if isinstance(b, Mapping) else (lambda k, _b=b: getattr(_b, k, None))     # a model, or its wire dict
        value, source = get("value"), get("source")
        if value is not None and value != "":
            out[get("param")] = str(value)
        elif source and not source.startswith("#"):
            v = value_for(source, values)
            if v is not None and str(v) != "":
                out[get("param")] = str(v)
    return out


# -- the screen ------------------------------------------------------------------ #


def find_screen(ctx: WriteContext, app: str, screen_id: str) -> Screen:
    screen = ((ctx.screens.screens.get(app) if ctx.screens is not None else None) or {}).get(screen_id)
    if screen is None:
        raise ScreenWriteError(f"no screen {app}/{screen_id}")
    return screen


def connector_of(screen: Screen, app: str) -> str:
    return screen.connector or app


def resolved_fields(ctx: WriteContext, app: str, screen: Screen) -> dict[str, dict[str, Any]]:
    """Each column of the screen as the form receives it (label, format, rule, the flags and the
    ``*_when`` rules), keyed by lower-cased name. The dictionary is the application's."""
    from liberty.web.screens import _resolve_screen_field

    hints = {h.name.lower(): h for h in screen.columns}
    return {
        low: _resolve_screen_field({"name": h.name}, column_hints=hints, connector=app,
                                   language=ctx.language, dictionary=ctx.connectors.dictionary)
        for low, h in hints.items()
    }


def form_field_names(screen: Screen) -> set[str] | None:
    """The fields of the record form (its form tabs), lower-cased; None when the screen has no
    form — the grid then edits every column."""
    tabs = [t for t in (screen.dialog.tabs if screen.dialog else []) if getattr(t, "type", "form") == "form"]
    if not tabs:
        return None
    return {f.name.lower() for t in tabs for f in (getattr(t, "fields", None) or [])}


def parent_links(ctx: WriteContext, app: str, screen: Screen) -> dict[str, str]:
    """The columns of *screen* that a parent record gives when the screen is a tab of another
    screen's form: ``{column (lower): "app/parent.SOURCE"}``, from the tab's ``param_binds``. In
    the form such a column is not typed — often hidden and disabled — the parent's value is
    seeded into it on Add. Here nobody seeds it: the caller must give it."""
    out: dict[str, str] = {}
    for sid, parent in ((ctx.screens.screens.get(app) if ctx.screens is not None else None) or {}).items():
        for tab in (parent.dialog.tabs if parent.dialog else []):
            target = getattr(tab, "screen", None) or getattr(tab, "form_screen", None)
            if target != screen.id or getattr(tab, "connector", None) not in (None, "", app):
                continue
            for b in getattr(tab, "param_binds", None) or []:
                if b.source and not b.source.startswith("#"):
                    out.setdefault(b.param.lower(), f"{app}/{sid}.{b.source}")
    return out


def is_password(field: Mapping[str, Any] | None) -> bool:
    return str((field or {}).get("format") or "").lower() == "password"


def blockers(screen: Screen, operation: str) -> list[str]:
    """Why *operation* (insert | update | delete) cannot be done on this screen from here."""
    out: list[str] = []
    if screen.read_only:
        out.append("the screen is read-only")
    query = {"insert": screen.insert_query, "update": screen.update_query, "delete": screen.delete_query}[operation]
    if not query:
        out.append(f"the screen has no {operation} query")
    if operation == "insert" and screen.disable_add:
        out.append("adding a record is disabled on this screen")
    if operation != "delete" and screen.dialog is not None and screen.dialog.on_load:
        out.append("its form runs actions when it opens (on_load) that only the screen itself runs today — do it in the screen")
    return out


def operations(ctx: WriteContext, app: str, screen: Screen) -> dict[str, Any]:
    """What may be done on the screen by this caller: per operation, True or the reasons why not."""
    connector = connector_of(screen, app)
    out: dict[str, Any] = {}
    for op in ("insert", "update", "delete"):
        why = blockers(screen, op)
        query = {"insert": screen.insert_query, "update": screen.update_query, "delete": screen.delete_query}[op]
        if query and not ctx.principal.has_permission(f"sql:{connector}:{query}"):
            why.append(f"the signed-in user may not (missing sql:{connector}:{query})")
        out[op] = True if not why else why
    return out


def _require(ctx: WriteContext, connector: str, query: str) -> None:
    if not ctx.principal.has_permission(f"sql:{connector}:{query}"):
        raise ScreenWriteError(f"permission denied: the signed-in user may not run {connector}.{query} (missing sql:{connector}:{query}). Tell the user.")


def _binds_of(ctx: WriteContext, connector: str, query: str | None) -> set[str]:
    """The ``:NAME`` parameters *query* reads, upper-cased, without the ``_ORIGINAL`` ones."""
    if not query:
        return set()
    try:
        qdef = ctx.connectors.sql(connector).get_query(query)
    except ConnectorError as exc:
        raise ScreenWriteError(str(exc)) from exc
    names = {n.upper() for n in find_bind_params(qdef.default_sql)}
    return {n for n in names if not n.endswith(_ORIGINAL)}


async def _run(ctx: WriteContext, connector: str, query: str, params: Row, *, action_context: dict[str, Any] | None = None) -> dict[str, Any]:
    from fastapi import HTTPException

    from liberty.web.connectors import _run_sql

    try:
        return await _run_sql(
            ctx.connectors, connector, query, params, language=ctx.language, user=ctx.principal.username,
            scope=ctx.principal.data_scope, verbose_errors=ctx.principal.is_superuser, screens=ctx.screens,
            changesets=ctx.changesets, pool=ctx.pool, notify_ctx=ctx.notify_ctx, action_context=action_context,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, default=str)
        raise ScreenWriteError(f"{connector}.{query}: {detail}") from exc


def _same(a: Any, b: Any) -> bool:
    return ("" if a is None else str(a)).strip() == ("" if b is None else str(b)).strip()


async def read_row(ctx: WriteContext, app: str, screen: Screen, key: Mapping[str, Any], params: Mapping[str, Any] | None = None) -> Row:
    """The one row of the screen that *key* names, as the screen reads it (the caller's data
    scope, the screen's formats) and as the browser would hold it (JSON values)."""
    from fastapi import HTTPException

    from liberty.ai.connector_tools import _as_item
    from liberty.auth.scope import column_items
    from liberty.connectors.sql import StreamRows
    from liberty.web.connectors import DD_PARAM_PREFIX, select_events

    connector = connector_of(screen, app)
    _require(ctx, connector, screen.read_query)
    key_columns = screen.effective_key_columns()
    if not key_columns:
        raise ScreenWriteError(f"the screen {app}/{screen.id} declares no key column: a record cannot be identified — do it in the screen")
    # every key column is named; one that is blank in the record is given as "" (JDE keys often hold blanks)
    missing = [k for k in key_columns if not _has(k, key)]
    if missing or all(_empty(value_for(k, key)) for k in key_columns):
        raise ScreenWriteError(
            f"the key needs {', '.join(missing) or 'at least one value'} (the key columns are {', '.join(key_columns)}; "
            'a part that is blank in the record is given as an empty string "")')
    # Narrow in SQL with the dictionary binds a dashboard filter uses (typed, TRIMmed like the
    # scope wrap). The connector binds an item on the FIRST column carrying it: a key column
    # that is not that first carrier (two columns of the result share its item) is left to the
    # match below.
    dd_of = {h.name.lower(): (h.dd or h.name) for h in screen.columns if h.dd != ""}
    try:
        prefix = int(getattr(ctx.connectors.sql(connector).config, "dd_prefix", 0) or 0)
    except ConnectorError as exc:
        raise ScreenWriteError(str(exc)) from exc
    request: Row = dict(params or {})
    unfiltered: list[str] = []
    for k in key_columns:
        if _empty(value_for(k, key)):
            unfiltered.append(k)                # a blank part cannot narrow the read: matched below
            continue
        item = _as_item(k, dd_of)
        first = next((h.name for h in screen.columns if item.lower() in column_items(h.name.lower(), dd_of, prefix)), k)
        if first.lower() == k.lower():
            request[f"{DD_PARAM_PREFIX}{item}"] = value_for(k, key)
        else:
            unfiltered.append(k)
    try:
        _conn, events = select_events(
            ctx.connectors, connector, screen.read_query, request, max_rows=READ_CAP, chunk_size=READ_CAP,
            language=ctx.language, user=ctx.principal.username, screens=ctx.screens,
            screen_hint=(app, screen.id), pool=ctx.pool, scope=ctx.principal.data_scope,
        )
        rows: list[Row] = []
        async for ev in events():
            if isinstance(ev, StreamRows):
                rows.extend(ev.rows)
    except HTTPException as exc:
        raise ScreenWriteError(f"{connector}.{screen.read_query}: {exc.detail}") from exc
    except ConnectorError as exc:
        raise ScreenWriteError(f"{connector}.{screen.read_query}: {exc}") from exc
    # the filter narrows; the match is decided here, on the key columns themselves
    found = [r for r in rows if all(_has(k, r) and _same(value_for(k, r), value_for(k, key)) for k in key_columns)]
    shown = ", ".join(f"{k}={value_for(k, key)}" for k in key_columns)
    if not found and len(rows) >= READ_CAP:
        raise ScreenWriteError(
            f"the record {shown} cannot be identified from here in {app}/{screen.id}: "
            f"{', '.join(unfiltered) or 'its key'} could not be used to narrow the read — do it in the screen")
    if not found:
        raise ScreenWriteError(f"no record {shown} in {app}/{screen.id} (or the signed-in user may not see it)")
    if len(found) > 1:
        raise ScreenWriteError(f"{len(found)} records match {shown} in {app}/{screen.id}: the key is not unique — do it in the screen")
    return json.loads(json.dumps(found[0], default=str))      # as the NDJSON stream hands it to the form


def masked(row: Mapping[str, Any], fields: Mapping[str, Mapping[str, Any]]) -> Row:
    """*row* without its secrets — what may be shown back."""
    return {k: (MASK if is_password(fields.get(str(k).lower())) and not _empty(v) else v) for k, v in row.items()}


# -- the save hooks ------------------------------------------------------------------ #


def save_hooks(screen: Screen, operation: str) -> list[Any]:
    """The actions a save runs after its write, in the form's order: the form's ``on_save``, then
    the screen's row hook."""
    row_hook = {"insert": screen.on_insert, "update": screen.on_update, "delete": screen.on_delete}[operation]
    on_save = list(screen.dialog.on_save) if (operation != "delete" and screen.dialog is not None) else []
    return [*on_save, *(row_hook or [])]


async def _run_hooks(
    ctx: WriteContext, app: str, screen: Screen, operation: str, row: Mapping[str, Any], inputs: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Run the save hooks on *row* (the record as written), as the form does after its save: the
    same context, the change package of a tracked screen. None when the screen has none."""
    from liberty.screens.actions import ChainDeps, entity_key, run_chain

    hooks = save_hooks(screen, operation)
    if not hooks:
        return None
    change = {"app": app, "screen": screen.id, "entity_key": entity_key(screen, row)} if screen.change_tracked else None
    deps = ChainDeps(ctx=ctx, default_connector=connector_of(screen, app), inputs=inputs or {}, change_context=change, navigates=False)
    result = await run_chain(hooks, row, deps, initial_input=row)
    return result.to_dict()


def _hooks_preview(ctx: WriteContext, screen: Screen, operation: str, inputs: Mapping[str, Any] | None) -> dict[str, Any]:
    """What the save will run after its write — checked before anything is written (a prompt's
    missing value refuses the save, it does not leave it half done)."""
    from liberty.screens.actions import check_inputs, outline

    hooks = save_hooks(screen, operation)
    if not hooks:
        return {}
    check_inputs(hooks, ctx.state, inputs)
    return {"then_runs": outline(hooks, ctx.state)}


def _with_hooks(out: dict[str, Any], ran: dict[str, Any] | None) -> dict[str, Any]:
    if ran is None:
        return out
    out = {k: v for k, v in out.items() if k != "then_runs"}
    out["actions"] = ran
    if not ran.get("ok"):
        out["note"] = "the record was written, but an action the screen runs after the save failed — tell the user exactly this, with the error"
    return out


# -- save ---------------------------------------------------------------------- #


def _normalise(name: str, value: Any, rule: Mapping[str, Any] | None, label: str) -> Any:
    """*value* as the form's widget would send it, or a refusal: a boolean as its two stored
    values, a code among the values of its list."""
    if not rule or _empty(value):
        return value
    kind = rule.get("kind")
    if kind == "boolean":
        true_v, false_v = rule.get("true_value", "Y"), rule.get("false_value")
        if isinstance(value, bool):
            return true_v if value else false_v
        # only a complete yes/no (both stored values known) can be checked — as the import does
        if false_v is None or _same(value, true_v) or _same(value, false_v):
            return value
        raise ScreenWriteError(f"{label} ({name}) is a yes/no field: {true_v!r} or {false_v!r}")
    if kind == "enum":
        allowed = [v.get("value") for v in rule.get("values") or []]
        for a in allowed:
            if _same(value, a):
                return a
        raise ScreenWriteError(f"{label} ({name}): {value!r} is not one of its values — {', '.join(repr(a) for a in allowed)}")
    return value


async def lookup_codes(ctx: WriteContext, rule: Mapping[str, Any], params: Mapping[str, Any]) -> set[str]:
    """The values a LOOKUP rule accepts for these parameters, trimmed — read the way the form and
    the import read them (``fetchLookup`` in ``services/lookups.ts``): the lookup's query run with
    the parameters, on each of its sources, as the caller; then the rows kept where a column named
    like a parameter holds that parameter's value."""
    wanted = {str(k): str(v).strip() for k, v in params.items() if v is not None and str(v) != ""}
    sources = rule.get("sources") if len(rule.get("sources") or []) > 1 else [{"connector": rule.get("connector"), "query": rule.get("query")}]
    codes: set[str] = set()
    for src in sources:
        _require(ctx, src["connector"], src["query"])
        resp = await _run(ctx, src["connector"], src["query"], dict(wanted))
        for row in resp.get("rows") or []:
            if any(_has(k, row) and ("" if value_for(k, row) is None else str(value_for(k, row))).strip() != want for k, want in wanted.items()):
                continue
            v = value_for(str(rule.get("value")), row)
            if v is not None:
                codes.add(str(v).strip())
    return codes


async def _check_lookups(
    ctx: WriteContext, fields: Mapping[str, Mapping[str, Any]], given: Mapping[str, Any], form: Mapping[str, Any],
) -> None:
    """Each given value of a LOOKUP field against its list — the check the grid's import makes
    (``validateImportRow``): the rule that applies to THIS record with its own binds, the value
    compared trimmed, an empty value or an empty list let through, a list that cannot be read
    refused rather than passed."""
    cache: dict[str, set[str]] = {}
    for name, value in given.items():
        f = fields.get(str(name).lower())
        if f is None or _empty(value) or str(value).strip() == "":
            continue
        rule, binds = active_rule_and_binds(f, form)
        if not rule or rule.get("kind") != "lookup":
            continue
        params = {**(rule.get("params") or {}), **resolve_bind_list(binds, form)}
        shown = str(f.get("label") or name)
        key = json.dumps([rule.get("connector"), rule.get("query"), rule.get("value"), rule.get("sources"), params], sort_keys=True, default=str)
        if key not in cache:
            try:
                cache[key] = await lookup_codes(ctx, rule, params)
            except ScreenWriteError as exc:
                raise ScreenWriteError(f"{shown} ({name}): its list of values could not be read — {exc}") from exc
        codes = cache[key]
        if codes and str(value).strip() not in codes:
            raise ScreenWriteError(
                f"{shown} ({name}): {value!r} is not a value of its list ({rule.get('connector')}.{rule.get('query')}"
                + (f", {', '.join(f'{k}={v}' for k, v in params.items())}" if params else "") + ") — read the list with sql_query and use one of its values")


async def save_row(
    ctx: WriteContext, app: str, screen_id: str, mode: str, values: Mapping[str, Any], *,
    key: Mapping[str, Any] | None = None, params: Mapping[str, Any] | None = None, dry_run: bool = False,
    inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Add (*mode* ``add``) or change (``edit``, the record named by *key*) one record of a
    screen. *values* holds the columns to set — on a change, only those that change. With
    *dry_run* everything is checked and the row read, nothing is written: the answer says what
    would be."""
    if mode not in ("add", "edit"):
        raise ScreenWriteError("mode is add or edit")
    screen = find_screen(ctx, app, screen_id)
    operation = "insert" if mode == "add" else "update"
    why = blockers(screen, operation)
    if why:
        raise ScreenWriteError(f"{app}/{screen_id} cannot be written from here: " + "; ".join(why))
    connector = connector_of(screen, app)
    main_query = screen.insert_query if mode == "add" else screen.update_query
    assert main_query is not None
    _require(ctx, connector, main_query)
    fields = resolved_fields(ctx, app, screen)
    group_by_col = {h.name.lower(): h.group for h in screen.columns if h.group}

    def group_of(name: str) -> str | None:
        return group_by_col.get(str(name).lower())

    def is_pw(name: str) -> bool:
        return is_password(fields.get(str(name).lower()))

    def label(name: str) -> str:
        return str((fields.get(str(name).lower()) or {}).get("label") or name)

    saved: Row = await read_row(ctx, app, screen, key or {}, params) if mode == "edit" else {}
    if mode == "edit" and screen.is_row_editable(saved) is not True:
        raise ScreenWriteError(f"this record is read-only in {app}/{screen_id} ({', '.join(r.field for r in screen.editable_when)})")

    # the values under the names the row / the screen use (the model may write them in any case)
    def canonical(name: str) -> str:
        for k in saved:
            if str(k).lower() == str(name).lower():
                return str(k)
        hint = fields.get(str(name).lower())
        return str(hint["name"]) if hint else str(name)

    given: Row = {canonical(k): v for k, v in values.items()}
    if not given and mode == "edit":
        raise ScreenWriteError("nothing to change: give the columns to set in `values`")

    # what the screen writes: the binds of its queries
    groups = list(screen.column_groups or [])
    writable = _binds_of(ctx, connector, main_query)
    for g in groups:
        for q in (g.insert_query, g.update_query):
            writable |= _binds_of(ctx, g.connector or connector, q)
    unknown = sorted(k for k in given if k.upper() not in writable)
    if unknown:
        raise ScreenWriteError(
            f"{', '.join(unknown)}: not written by {app}/{screen_id}. The columns it writes are: {', '.join(sorted(writable))}")

    # the form's state: the loaded row (or the defaults of an Add), then what is set now
    seeded: Row = {}
    form_names = form_field_names(screen)
    if mode == "add":
        for f in fields.values():
            if not _empty(f.get("default")) and not is_password(f):
                seeded[f["name"]] = f["default"]
    else:
        seeded = {k: v for k, v in saved.items() if not is_pw(k)}
    baseline = {low: str(dw.get("default")) for low, f in fields.items()
                if mode == "edit" and (dw := _matched_default_when(f.get("default_when"), seeded)) is not None and dw.get("lock") is False}
    form: Row = overlay_ci(seeded, given)
    applied: Row = {}                       # what a default_when set (forced, or seeded once)
    for _ in range(5):                      # a forced value may trigger another rule
        changed = False
        for low, f in fields.items():
            dw = _matched_default_when(f.get("default_when"), form)
            if dw is None:
                continue
            name, forced = canonical(f["name"]), dw.get("default")
            if dw.get("lock") is not False:
                if name in given and not _same(given[name], forced):
                    raise ScreenWriteError(f"{label(name)} ({name}) is set by the screen to {forced!r} for this record: do not give it another value")
            elif name in given or baseline.get(low) == str(forced):
                continue                    # an editable default: seeded once, never over a value
            if not _same(value_for(name, form), forced):
                form = overlay_ci(form, {name: forced})
                applied[name] = forced
                changed = True
        if not changed:
            break

    # a tab of another record's form: the parent's value is seeded into the link column on Add —
    # here it is given, and it must be (a row without it belongs to no parent)
    links = parent_links(ctx, app, screen) if mode == "add" else {}
    orphan = [f"{label(c)} ({canonical(c)}) — the link to {src}" for c, src in links.items()
              if c.upper() in writable and _empty(value_for(c, form))]
    if orphan:
        raise ScreenWriteError(f"a new record of {app}/{screen_id} belongs to a parent record: give {'; '.join(orphan)}")

    # each given value against its field, as the form would let it be entered
    for name in list(given):
        f = fields.get(name.lower())
        if f is None:
            continue
        state = field_state(f, form, mode)
        before = value_for(name, seeded)
        if mode == "edit" and not state["visible"]:
            raise ScreenWriteError(f"{label(name)} ({name}) does not apply to this record (the screen hides it): it cannot be set")
        if name.lower() in links:
            pass                                # the parent's value: locked in the form, given here
        elif state["disabled"] and not _same(given[name], before):
            raise ScreenWriteError(f"{label(name)} ({name}) cannot be changed on this screen" + (" when adding" if mode == "add" else ""))
        given[name] = _normalise(name, given[name], active_rule(f, form), label(name))
    form = overlay_ci(form, given)
    await _check_lookups(ctx, fields, given, form)

    missing = [f"{label(f['name'])} ({f['name']})" for low, f in fields.items()
               if (form_names is None or low in form_names)
               and (st := field_state(f, form, mode))["visible"] and st["required"] and _empty(value_for(f["name"], form))]
    if missing:
        raise ScreenWriteError(f"required and empty: {', '.join(missing)}")

    # what is sent — an Add: the values and the defaults; a change: the values and what a rule set
    sent: Row = {}
    for name, v in {**(seeded if mode == "add" else {}), **applied, **given}.items():
        if is_pw(name) and _empty(v):
            continue                        # an empty password never overwrites the stored one
        sent[canonical(name)] = v
    if mode == "edit":
        sent = {k: v for k, v in sent.items() if not _same(v, value_for(k, saved)) or is_pw(k)}
        if not sent:
            return {"operation": operation, "screen": f"{app}/{screen_id}", "written": False, "note": "the record already holds these values: nothing to change"}

    main_sent = {k: v for k, v in sent.items() if not group_of(k)}
    by_group: dict[str, Row] = {}
    for k, v in sent.items():
        g = group_of(k)
        if g:
            by_group.setdefault(g, {})[k] = v

    # the related tables this save touches, decided as saveScreenRow does — and allowed before anything is written
    plan: list[tuple[Any, str, bool]] = []          # (group, query, the related row exists)
    for g in groups:
        gvals = by_group.get(g.id, {})
        if not gvals and not (mode == "add" and g.insert_on_add):
            continue
        key_cols = list(g.key_columns or [])
        exists = bool(key_cols) and all(value_for(kc, saved) is not None for kc in key_cols)
        g_query = g.update_query if exists else g.insert_query
        if not g_query:
            continue
        _require(ctx, g.connector or connector, g_query)
        plan.append((g, g_query, exists))

    preview = {
        "operation": operation, "screen": f"{app}/{screen_id}",
        **({"key": {k: value_for(k, saved) for k in screen.effective_key_columns()}} if mode == "edit" else {}),
        "values": masked(sent, fields),
        **({"before": masked({k: value_for(k, saved) for k in sent}, fields)} if mode == "edit" else {}),
        **({"related_tables": [g.id for g, _q, _e in plan]} if plan else {}),
        **_hooks_preview(ctx, screen, operation, inputs),
    }
    if dry_run:
        return {**preview, "written": False}

    if mode == "edit":
        main_saved = {k: v for k, v in saved.items() if not group_of(k) and not is_pw(k)}
        main_params = {**main_saved, **main_sent, **original_keys(main_saved)}
    else:
        main_params = dict(main_sent)
    resp = await _run(ctx, connector, main_query, with_upper(main_params))
    resolved: Row = dict(resp.get("resolved_binds") or {})
    rowcount = int(resp.get("rowcount") or 0)

    done: list[str] = []
    for g, g_query, exists in plan:
        gvals = by_group.get(g.id, {})
        fk = resolve_bind_list(g.param_binds, overlay_ci({**saved, **main_sent}, resolved))
        if exists:
            g_saved = {k: v for k, v in saved.items() if group_of(k) == g.id and not is_pw(k)}
            for kc in g.key_columns or []:
                v = value_for(kc, saved)
                if v is not None:
                    g_saved[kc] = v
            g_params = {**g_saved, **gvals, **fk, **original_keys(g_saved), **original_keys(fk)}
        else:
            g_params = {**gvals, **fk}
        try:
            await _run(ctx, g.connector or connector, g_query, with_upper(g_params))
        except ScreenWriteError as exc:
            raise ScreenWriteError(
                f"the main record was written, then the related table {g.id} failed — {exc}. "
                f"Related tables already written: {', '.join(done) or 'none'}. Tell the user exactly this.") from exc
        done.append(g.id)

    out = {**preview, "written": True, "rowcount": rowcount}
    if resolved:
        out["assigned"] = resolved          # what the server gave (a sequence's new key)
    if rowcount == 0:
        out["note"] = "the statement ran but changed no row — check the record still exists with these key values"
        out.pop("then_runs", None)
        return out
    # the record as the form holds it after its save: what was loaded, what was sent, what the server assigned
    return _with_hooks(out, await _run_hooks(ctx, app, screen, operation, overlay_ci({**saved, **sent}, resolved), inputs))


async def delete_row(
    ctx: WriteContext, app: str, screen_id: str, key: Mapping[str, Any], *,
    params: Mapping[str, Any] | None = None, dry_run: bool = False, inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Delete the record *key* names: the related tables' rows first, then the main one — the
    twin of ``deleteScreenRow``."""
    screen = find_screen(ctx, app, screen_id)
    why = blockers(screen, "delete")
    if why:
        raise ScreenWriteError(f"{app}/{screen_id} cannot be written from here: " + "; ".join(why))
    connector = connector_of(screen, app)
    assert screen.delete_query is not None
    _require(ctx, connector, screen.delete_query)
    fields = resolved_fields(ctx, app, screen)
    row = await read_row(ctx, app, screen, key, params)
    if screen.is_row_editable(row) is not True:
        raise ScreenWriteError(f"this record is read-only in {app}/{screen_id} ({', '.join(r.field for r in screen.editable_when)})")

    plan: list[Any] = []
    for g in screen.column_groups or []:
        if not g.delete_query:
            continue
        key_cols = list(g.key_columns or [])
        if key_cols and not all(value_for(kc, row) is not None for kc in key_cols):
            continue
        _require(ctx, g.connector or connector, g.delete_query)
        plan.append(g)

    preview = {
        "operation": "delete", "screen": f"{app}/{screen_id}",
        "key": {k: value_for(k, row) for k in screen.effective_key_columns()},
        "record": masked(row, fields),
        **({"related_tables": [g.id for g in plan]} if plan else {}),
        **_hooks_preview(ctx, screen, "delete", inputs),
    }
    if dry_run:
        return {**preview, "written": False}
    for g in plan:
        fk = resolve_bind_list(g.param_binds, row)
        await _run(ctx, g.connector or connector, g.delete_query, with_upper({**row, **fk}))
    resp = await _run(ctx, connector, screen.delete_query, with_upper(row))
    rowcount = int(resp.get("rowcount") or 0)
    out = {**preview, "written": True, "rowcount": rowcount}
    out.pop("record", None)
    if rowcount == 0:
        out["note"] = "the statement ran but deleted no row"
        out.pop("then_runs", None)
        return out
    return _with_hooks(out, await _run_hooks(ctx, app, screen, "delete", row, inputs))
