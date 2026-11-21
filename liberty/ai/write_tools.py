"""Write tools — the assistant changes a record through its screen: ``screen_form`` (what a
screen lets its user enter and run), ``screen_write`` (add, change or delete one record) and
``screen_action`` (run one of the screen's actions — a row menu's "Close", a toolbar's or a form
tab's button — on the server, through :mod:`liberty.screens.actions`). And it runs a job:
``job_form`` (a job as its page shows it) and ``job_run`` ("Run now"), through the functions of
the job page's own routes (``/api/jobs``).

Nothing is written by another path than the screen's own: the tool calls
:mod:`liberty.screens.save`, the server-side twin of the record form — the row is read first,
the field rules of the form are applied (required, disabled, the values of a list), and each
statement runs through the function behind ``POST /api/sql`` (dictionary rules, the audit
table, the data scope, the change package, the notification rules).

Three locks, all needed: ``[ai] write_tools = true`` (off by default), the permission
``ai:write`` for the user, and the ``sql:<connector>:<query>`` permission of each query the
write runs — what the screen itself asks.

Who confirms depends on where the assistant runs (:data:`liberty.ai.tools.current_channel`):

* in the Liberty chat the tool **proposes**: everything is checked, nothing is written, and the
  user gets a card with the record and an Apply button (``POST /ai/screen-write``);
* over MCP the client (Claude) asks its user before it calls a tool that is not read-only, so
  the tool **writes**.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from liberty.ai.tools import Tool, ToolPermissionError, caller, current_channel, hides_sensitive, tool
from liberty.connectors.api import current_pool
from liberty.screens.actions import ChainDeps, check_inputs, does_something, entity_key, fill_text, outline, run_chain
from liberty.screens.save import (
    ScreenWriteError, WriteContext, _binds_of, connector_of, delete_row, field_state, find_screen, form_field_names,
    hidden_columns, is_password, masked, operations, parent_links, read_row, resolved_fields, save_hooks, save_row, value_for,
)

_log = logging.getLogger(__name__)

WRITE_PERMISSION = "ai:write"
OPERATIONS = ("insert", "update", "delete")
ORIGIN = "AI assistant"       # how a change package names the entries the assistant wrote
ENUM_MAX = 60                 # the most values of a list shown by screen_form


def write_context(state: Any, principal: Any, *, language: str | None = None, pool: str | None = None) -> WriteContext:
    """The write context of *principal* on the live application state."""
    return WriteContext(
        connectors=state.connectors, screens=getattr(state, "screens", None), principal=principal,
        changesets=getattr(state, "changesets_db", None),
        notify_ctx=(getattr(state, "notification_rules", None), getattr(state, "notify", None)),
        language=language, pool=pool, state=state, hide_sensitive=hides_sensitive(state), origin=ORIGIN,
        allowed=frozenset(getattr(getattr(getattr(state, "settings", None), "ai", None), "allowed_connectors", None) or ()),
    )


async def perform(
    ctx: WriteContext, *, app: str, screen: str, operation: str, values: dict[str, Any] | None = None,
    key: dict[str, Any] | None = None, params: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One write through a screen — the tool's and the Apply route's single way in."""
    if operation not in OPERATIONS:
        raise ScreenWriteError("operation is insert, update or delete")
    if operation == "delete":
        out = await delete_row(ctx, app, screen, key or {}, params=params, dry_run=dry_run, inputs=inputs)
    else:
        out = await save_row(ctx, app, screen, "add" if operation == "insert" else "edit", values or {},
                             key=key, params=params, dry_run=dry_run, inputs=inputs)
    if out.get("written"):
        _log.info("AI write: %s %s %s/%s key=%s (%s row)", ctx.principal.username, operation, app, screen,
                  out.get("key") or out.get("assigned") or "", out.get("rowcount"))
    return out


MANY_MAX = 50                 # the most records one screen_write_many call takes


async def perform_many(
    ctx: WriteContext, *, app: str, screen: str, operation: str, records: list[dict[str, Any]],
    params: dict[str, Any] | None = None, dry_run: bool = False,
) -> dict[str, Any]:
    """Several records of one screen, one operation — each written as ``perform`` writes one.

    Checked first, all of them: a record the screen refuses stops the whole batch before anything
    is written, and the answer names every refused record with its reason. Written then one after
    the other; a statement that fails stops there — the records before it stay written, as in
    the grid, and the answer says how far it went."""
    if operation not in OPERATIONS:
        raise ScreenWriteError("operation is insert, update or delete")
    if not isinstance(records, list) or not records:
        raise ScreenWriteError("give the records to write: a list of {values, key}")
    if len(records) > MANY_MAX:
        raise ScreenWriteError(f"{len(records)} records: at most {MANY_MAX} in one batch — for more, the grid's Excel import is the way")
    items = [{"values": dict(r.get("values") or {}), "key": dict(r.get("key") or {})} for r in records if isinstance(r, dict)]
    if len(items) != len(records):
        raise ScreenWriteError("each record is an object {values, key}")

    previews: list[dict[str, Any]] = []
    refused: list[str] = []
    for i, r in enumerate(items, start=1):
        try:
            previews.append(await perform(ctx, app=app, screen=screen, operation=operation, values=r["values"], key=r["key"], params=params, dry_run=True))
        except ScreenWriteError as exc:
            refused.append(f"record {i}: {exc}")
    if refused:
        raise ScreenWriteError(f"{len(refused)} of {len(items)} records are refused, nothing was written — " + " | ".join(refused))
    if dry_run:
        return {"operation": operation, "screen": f"{app}/{screen}", "count": len(items), "records": previews, "written": 0}

    results: list[dict[str, Any]] = []
    for i, r in enumerate(items, start=1):
        try:
            results.append(await perform(ctx, app=app, screen=screen, operation=operation, values=r["values"], key=r["key"], params=params))
        except ScreenWriteError as exc:
            return {"operation": operation, "screen": f"{app}/{screen}", "count": len(items), "written": len(results), "records": results,
                    "stopped_at": i, "error": str(exc),
                    "note": f"records 1 to {i - 1} are written, record {i} failed and the following were not attempted — tell the user exactly this"}
    return {"operation": operation, "screen": f"{app}/{screen}", "count": len(items), "written": len(results), "records": results}


def screen_actions(sc: Any, state: Any) -> list[tuple[str, Any]]:
    """The actions of a screen that run something on the server, each with what it runs on:
    ``record`` (the row menu, a form tab's button) or ``screen`` (the toolbar). A pure drill or
    link is the browser's and is left out."""
    found: list[tuple[str, Any]] = [("record", a) for a in sc.row_menu or []]
    found += [("screen", a) for a in sc.actions or []]
    for tab in (sc.dialog.tabs if sc.dialog else []):
        found += [("form", a) for a in getattr(tab, "actions", None) or []]
    return [(on, a) for on, a in found if does_something([a], state)]


async def perform_action(
    ctx: WriteContext, *, app: str, screen: str, action: str, key: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None, params: dict[str, Any] | None = None, dry_run: bool = False,
) -> dict[str, Any]:
    """Run one action of a screen — the tool's and the Apply route's single way in. With
    *dry_run* the record is read and everything that can be checked is; nothing runs."""
    sc = find_screen(ctx, app, screen)
    connector = connector_of(sc, app)
    if not ctx.principal.has_permission(f"sql:{connector}:{sc.read_query}"):
        raise ScreenWriteError(f"permission denied: the signed-in user may not open {app}/{screen}. Tell the user.")
    offered = screen_actions(sc, ctx.state)
    match = next(((on, a) for on, a in offered if a.id.lower() == str(action).lower()), None)
    if match is None:
        names = ", ".join(a.id for _on, a in offered) or "none"
        raise ScreenWriteError(f"no action {action!r} on {app}/{screen} that runs here — its actions: {names}")
    on, act = match
    row: dict[str, Any] = {}
    if on != "screen":
        row = await read_row(ctx, app, sc, key or {}, params)
    check_inputs([act], ctx.state, inputs)
    # a form tab's button is a write hook of the form: its steps land in the change package of a tracked screen
    change = {"app": app, "screen": sc.id, "entity_key": entity_key(sc, row)} if (on == "form" and sc.change_tracked) else None
    # what the screen's messages may show: the record without the values flagged sensitive
    shown = masked(row, resolved_fields(ctx, app, sc), hidden_columns(ctx, app, sc, row)) if row else {}
    deps = ChainDeps(ctx=ctx, default_connector=connector, inputs=inputs or {}, change_context=change, navigates=True, display=shown)
    plan = outline([act], ctx.state)
    preview: dict[str, Any] = {
        "action": act.id, "label": act.label or act.id, "screen": f"{app}/{screen}",
        **({"key": {k: value_for(k, row) for k in sc.effective_key_columns()}} if row else {}),
        **({"confirms": [fill_text(m, {"INPUT": dict(inputs or {})}, shown, deps) for m in plan["confirms"]]} if plan.get("confirms") else {}),
        "does": plan.get("does", []),
    }
    if dry_run:
        return {**preview, "ran": False, **({"record": shown} if row else {})}
    result = await run_chain([act], row, deps, initial_input=row if on == "form" else {})
    out = {k: v for k, v in preview.items() if k not in ("confirms", "does")}
    out.update({"ran": True, **result.to_dict()})
    _log.info("AI action: %s ran %s on %s/%s key=%s ok=%s", ctx.principal.username, act.id, app, screen, out.get("key") or "", out.get("ok"))
    return out


def _job_request(state: Any) -> Any:
    """What the job routes read of a request — the application state and the headers — so the
    tools call the routes' own functions (``/api/jobs``, the job page a user runs a job from)
    instead of doing their work again."""
    from types import SimpleNamespace

    return SimpleNamespace(app=SimpleNamespace(state=state), headers={})


async def job_page(state: Any, principal: Any, job_id: str) -> dict[str, Any]:
    """The job as its page shows it to this user (``GET /api/jobs/{id}``): the description, the
    parameters a user may set with their choices, the steps that need an uploaded file, whether
    it is running, its last runs. Needs ``job:<id>``."""
    from fastapi import HTTPException

    from liberty.web.jobs import user_get_job

    try:
        return await user_get_job(job_id, _job_request(state), principal)
    except HTTPException as exc:
        raise ScreenWriteError(_job_refusal(exc, job_id)) from exc


def _job_refusal(exc: Any, job_id: str) -> str:
    if exc.status_code == 403:
        return f"permission denied: the signed-in user may not run the job {job_id} (missing job:{job_id}). Tell the user."
    return str(exc.detail)


async def perform_job_run(
    state: Any, principal: Any, *, job: str, params: dict[str, Any] | None = None, preset: str | None = None,
    files: dict[str, str] | None = None, dry_run: bool = False,
) -> dict[str, Any]:
    """Run a job for the user the way its page does (``POST /api/jobs/{id}/run``): the declared
    parameters only, a value among a parameter's choices, a preset of the job, never while it is
    already running. A step that loads a file the user brings takes it from *files*
    (``{step: the path the job's upload returned}`` — the route accepts nothing else). With
    *dry_run* the job is read and the request checked for what can be checked without firing;
    nothing runs, and the answer says which steps need a file (``needs_files``)."""
    from fastapi import HTTPException

    from liberty.web.jobs import user_run

    from liberty.jobs.files import UPLOAD_SUFFIXES

    page = await job_page(state, principal, job)
    info = page["job"]
    needs = list(info.get("uploads") or [])
    files = {str(k): str(v) for k, v in (files or {}).items()}
    # a bare name is a file of this job's upload area (what an upload from Claude Desktop returns)
    from liberty.jobs.files import job_dir, safe_name
    for step, value in list(files.items()):
        if "/" not in value and "\\" not in value:
            files[step] = str(job_dir(state.settings, job) / "uploads" / safe_name(value))
    stray = sorted(s for s in files if s not in {u["step"] for u in needs})
    if stray:
        raise ScreenWriteError(f"{', '.join(stray)}: not a step of the job {job} that takes a file")
    declared = {p["name"]: p for p in info.get("params") or []}
    unknown = sorted(k for k in (params or {}) if k not in declared)
    if unknown:
        raise ScreenWriteError(f"{', '.join(unknown)}: not a parameter of the job {job} — its parameters: {', '.join(declared) or 'none'}")
    missing = sorted(n for n, p in declared.items() if p.get("required") and not ((params or {}).get(n) or p.get("default")))
    if missing:
        raise ScreenWriteError(f"the job {job} needs a value for: {', '.join(missing)}")
    if info.get("in_flight"):
        raise ScreenWriteError(f"the job {job} is already running: wait for it to finish")
    preview = {"job": job, **({"description": info["description"]} if info.get("description") else {}),
               "params": dict(params or {}), **({"preset": preset} if preset else {}),
               # the steps that load a file the user brings, and the kinds of file they take
               **({"needs_files": needs, "accept": sorted(UPLOAD_SUFFIXES)} if needs else {})}
    if dry_run:
        return {**preview, "ran": False}
    lacking = [u["step"] for u in needs if u["step"] not in files]
    if lacking:
        raise ScreenWriteError(
            f"the job {job} needs a file for: {', '.join(lacking)} ({', '.join(sorted(UPLOAD_SUFFIXES))}). In the Liberty chat the user "
            "chooses it on the card. From Claude Desktop with the Liberty extension: local_files, then send_file, then job_run with "
            "`files`. Without either, the user runs the job from its page in Liberty.")
    try:
        out = await user_run(job, _job_request(state), principal, {
            "params": dict(params or {}), **({"preset": preset} if preset else {}), **({"files": files} if files else {})})
    except HTTPException as exc:
        raise ScreenWriteError(_job_refusal(exc, job)) from exc
    _log.info("AI job run: %s fired %s run=%s", principal.username, job, out.get("run_id"))
    return {**preview, "ran": True, "run_id": out.get("run_id"), "status": out.get("status"),
            "follow": "job_form on this job shows the run's state; the user sees it under Nomaflow → Job Runs"}


async def perform_api_run(
    state: Any, principal: Any, *, connector: str, endpoint: str, params: dict[str, Any] | None = None, dry_run: bool = False,
) -> dict[str, Any]:
    """Call an API endpoint that changes data, for the user — what the endpoint's own route does
    (``POST /api/http/{connector}/{endpoint}``: the ``api:`` permission, the change-package
    capture), on a connector the assistant may use. With *dry_run* nothing is sent: the endpoint
    is found, the caller's permission and the values are checked (a value the connector would
    refuse, one outside a parameter's allowed values), and the answer says what would be called."""
    from fastapi import HTTPException

    from liberty.connectors.api import ApiParamError
    from liberty.connectors.base import ConnectorError
    from liberty.web.connectors import call_http

    ai = getattr(getattr(state, "settings", None), "ai", None)
    allowed = frozenset(getattr(ai, "allowed_connectors", None) or ())
    permission = f"api:{connector}:{endpoint}"
    # the permission first, and one answer for "not allowed" and "not there": no name is confirmed
    if not principal.has_permission(permission) or (allowed and connector not in allowed):
        raise ScreenWriteError(f"permission denied: you may not call the endpoint {connector}.{endpoint}")
    try:
        conn = state.connectors.api(connector)
        ep = conn.get_endpoint(endpoint)
    except ConnectorError as exc:
        raise ScreenWriteError(f"{connector}.{endpoint}: no such endpoint ({exc})") from exc
    if not ep.writable:
        raise ScreenWriteError(f"{connector}.{endpoint} only reads: call it with api_call")
    values = {str(k): v for k, v in (params or {}).items()}
    unknown = sorted(k for k in values if k not in {p.name for p in ep.params})
    if unknown:
        raise ScreenWriteError(f"{', '.join(unknown)}: not a parameter of {connector}.{endpoint} — its parameters: "
                               + (", ".join(p.name for p in ep.params) or "none"))
    try:
        preview = conn.preview(endpoint, values)
    except ApiParamError as exc:
        raise ScreenWriteError(str(exc)) from exc
    what = {"connector": connector, "endpoint": endpoint, "label": ep.label, "description": ep.description,
            "method": preview["method"], "params": preview["params"]}
    if dry_run:
        return {**what, "called": False}
    try:
        out = await call_http(state, principal, state.connectors, getattr(state, "screens", None), connector, endpoint, values)
    except HTTPException as exc:
        raise ScreenWriteError(str(exc.detail)) from exc
    return {**what, "called": True, "success": bool(out.get("success")), "status_code": out.get("status_code"), "error": out.get("error"),
            **({"warnings": out["warnings"]} if out.get("warnings") else {}),
            "result": out.get("extracted") if out.get("extracted") is not None else (out.get("mapped") or out.get("json") or out.get("body"))}


def _summary(operation: str, app: str, screen: str, preview: dict[str, Any]) -> str:
    key = ", ".join(f"{k}={v}" for k, v in (preview.get("key") or {}).items())
    values = preview.get("values") or {}
    before = preview.get("before") or {}
    if operation == "delete":
        return f"Delete {app}/{screen} — {key}"
    if operation == "update":
        changes = ", ".join(f"{k}: {before.get(k)!r} → {v!r}" for k, v in values.items())
        return f"Change {app}/{screen} — {key} — {changes}"
    return f"Add to {app}/{screen} — " + ", ".join(f"{k}={v!r}" for k, v in values.items())


def build_write_tools(state: Callable[[], Any], *, include_api: bool = False) -> list[Tool]:
    def _ctx() -> WriteContext:
        who = caller()
        if who is None:
            raise ToolPermissionError("permission denied: a write needs a signed-in user")
        return write_context(state(), who, pool=current_pool.get())

    def screen_form(app: str, screen: str) -> dict[str, Any]:
        """What a screen lets its user add, change or delete — read it BEFORE screen_write.

        Gives the operations open to the signed-in user (and why one is not), the key columns
        that identify a record, and each column the screen writes: its label, whether it is
        required, whether it can be changed, its default, and the values it accepts (a list of
        codes, yes/no, or the query that lists them — read it with sql_query to pick a value: a
        value outside the list is refused when you write). A screen's tabs that are other screens (`related_screens`) are
        written through those screens; a column with `link_to_parent` is the parent record's key,
        to give on an insert (the form fills it by itself; a row without it belongs to nobody).
        `actions` are what the screen offers beyond a save — a
        row menu's "Validate" / "Close", a button — run with screen_action: when the screen has
        an action for what the user asks, use it rather than writing the column yourself.
        `a_save_also_runs` is what the screen runs by itself after an insert, update or delete.

        Args:
            app: the application of the screen (describe_instance / search_catalog).
            screen: the screen id.
        """
        ctx = _ctx()
        try:
            sc = find_screen(ctx, app, screen)
            connector = connector_of(sc, app)
            if not ctx.principal.has_permission(f"sql:{connector}:{sc.read_query}"):
                raise ToolPermissionError(f"permission denied: the signed-in user may not open {app}/{screen}. Tell the user.")
            fields = resolved_fields(ctx, app, sc)
            writes: set[str] = set()
            for q in (sc.insert_query, sc.update_query):
                writes |= _binds_of(ctx, connector, q)
            for g in sc.column_groups or []:
                for q in (g.insert_query, g.update_query):
                    writes |= _binds_of(ctx, g.connector or connector, q)
        except ScreenWriteError as exc:
            raise ValueError(str(exc)) from exc
        keys = [k.upper() for k in sc.effective_key_columns()]
        links = parent_links(ctx, app, sc)
        on_form = form_field_names(sc)
        out_fields: list[dict[str, Any]] = []
        for name in sorted(writes | set(keys)):
            f = fields.get(name.lower()) or {"name": name}
            entry: dict[str, Any] = {"name": name}
            if f.get("label"):
                entry["label"] = f["label"]
            if f.get("format"):
                entry["format"] = f["format"]
            if name in keys:
                entry["key"] = True
            if name not in writes:
                entry["written"] = False
            add, edit = field_state(f, {}, "add"), field_state(f, {}, "edit")
            link = links.get(name.lower())
            if link:
                # a tab of another record's form: the form seeds the parent's value, here it is given
                entry["link_to_parent"] = link
                entry["required_on"] = "insert"
                add = {**add, "disabled": False}
            if f.get("required"):
                entry["required"] = True
            if add["disabled"] and edit["disabled"]:
                entry["read_only"] = True
            elif add["disabled"]:
                entry["read_only_on"] = "insert"
            elif edit["disabled"]:
                entry["read_only_on"] = "update"
            if f.get("hidden") and not f.get("visible_when") and not link:
                entry["hidden"] = True
            if on_form is not None and name.lower() not in on_form:
                entry["on_form"] = False
            if f.get("default") not in (None, "") and not is_password(f):
                entry["default"] = f["default"]
            for cond in ("required_when", "visible_when", "disabled_when", "default_when"):
                if f.get(cond):
                    entry[cond] = f[cond]
            rule = f.get("rule") or {}
            if rule.get("kind") == "enum":
                values = rule.get("values") or []
                entry["values"] = values[:ENUM_MAX]
                if len(values) > ENUM_MAX:
                    entry["values_note"] = f"{len(values)} values, the first {ENUM_MAX} shown"
            elif rule.get("kind") == "boolean":
                entry["values"] = [rule.get("true_value", "Y"), rule.get("false_value")]
            elif rule.get("kind") == "lookup":
                entry["values_from"] = {k: rule[k] for k in ("connector", "query", "value", "label") if rule.get(k)}
            if f.get("return_binds"):
                # picking a value of this list also sets these columns from the picked row: do not give them
                entry["also_fills"] = sorted({str(rb.get("column")) for rb in f["return_binds"] if rb.get("column")})
            if f.get("rules_when"):
                entry["values_depend_on"] = sorted({str(rw.get("field")) for rw in f["rules_when"]})
            if is_password(f):
                entry["secret"] = True
            out_fields.append(entry)
        related = [
            {"tab": t.label or t.id, "screen": getattr(t, "screen", None) or getattr(t, "form_screen", None),
             **({"linked_by": [b.model_dump(exclude_none=True) for b in t.param_binds]} if t.param_binds else {})}
            for t in (sc.dialog.tabs if sc.dialog else []) if getattr(t, "type", "form") != "form"
        ]
        actions = [
            {"id": a.id, "label": a.label or a.id, "on": "screen" if on == "screen" else "record", **outline([a], ctx.state)}
            for on, a in screen_actions(sc, ctx.state)
        ]
        hooks = {op: outline(h, ctx.state) for op in OPERATIONS if (h := save_hooks(sc, op))}
        return {
            "screen": f"{app}/{screen}", "label": sc.label or screen, "connector": connector, "read_query": sc.read_query,
            "key_columns": keys, "operations": operations(ctx, app, sc), "fields": out_fields,
            **({"actions": actions} if actions else {}),
            **({"a_save_also_runs": hooks} if hooks else {}),
            **({"related_tables": [g.id for g in sc.column_groups]} if sc.column_groups else {}),
            **({"related_screens": related} if related else {}),
        }

    async def screen_write(
        app: str, screen: str, operation: str, values: dict[str, Any] | None = None,
        key: dict[str, Any] | None = None, params: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add, change or delete ONE record through its screen — the screen's own queries and
        rules, as if the user had done it in the form. It saves: use it only for what the user
        asked, never to try something. Call screen_form first, and say plainly what you are
        about to write.

        The record of an update or a delete is named by `key` (the screen's key columns); it is
        read first, so give in `values` only the columns that change. Everything is checked the
        way the form checks it: a missing required column, a value outside a list, a column the
        screen does not let its user change are refused with the reason — fix the request or
        tell the user, do not look for another way. In the Liberty chat nothing is written until
        the user accepts the card; a refusal or a skipped card ends the step.

        Args:
            app: the application of the screen.
            screen: the screen id.
            operation: insert, update or delete.
            values: {column: value} to set — every column of a new record, only the changed ones of an update; not used by a delete.
            key: {key column: value} of the record to update or delete — every key column; a part that is blank in the record is "".
            params: {param: value} of the screen's read query, when it needs some (a tab of another record: the parent's key).
            inputs: {name: value} asked by an action the screen runs after the save (screen_form: a_save_also_runs → needs).
        """
        ctx = _ctx()
        proposing = current_channel.get() != "mcp"
        try:
            out = await perform(ctx, app=app, screen=screen, operation=operation, values=values, key=key,
                                params=params, inputs=inputs, dry_run=proposing)
        except ScreenWriteError as exc:
            raise ValueError(str(exc)) from exc
        if not proposing or "note" in out:
            return out
        from liberty.ai.proposal import Proposal, wrap

        return wrap(
            Proposal(kind="screen_write", summary=_summary(operation, app, screen, out), target_path=f"{app}/{screen}", app=app,
                     payload={"app": app, "screen": screen, "operation": operation, "values": values or {},
                              "key": key or {}, "params": params or {}, "inputs": inputs or {}, "preview": out},
                     notes=[f"then runs: {d}" for d in (out.get("then_runs") or {}).get("does", [])]),
            message="Checked and proposed to the user, who applies or skips it on the card: nothing is written yet. Stop here and wait.",
        )

    async def screen_action(
        app: str, screen: str, action: str, key: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None, params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run ONE action of a screen — what its user does from the row menu or a button
        ("Validate", "Close", "Reset password") — with the screen's own steps, on the server. It
        changes things: use it only for what the user asked. screen_form lists the actions of a
        screen (`actions`): what each runs, the confirmation the screen asks, the values it needs.

        An action `on` a record needs `key`; one `on` the screen does not. The screen's
        confirmation is the user's to give: say its message and what will run before you call
        this. In the Liberty chat nothing runs until the user accepts the card. The answer tells
        what ran, the screen's messages, and any step that failed — report a failure as it is.

        Args:
            app: the application of the screen.
            screen: the screen id.
            action: the action id (screen_form → actions).
            key: {key column: value} of the record, for an action on a record.
            inputs: {name: value} the action asks (screen_form → actions → needs).
            params: {param: value} of the screen's read query, when it needs some.
        """
        ctx = _ctx()
        proposing = current_channel.get() != "mcp"
        try:
            out = await perform_action(ctx, app=app, screen=screen, action=action, key=key, inputs=inputs,
                                       params=params, dry_run=proposing)
        except ScreenWriteError as exc:
            raise ValueError(str(exc)) from exc
        if not proposing:
            return out
        from liberty.ai.proposal import Proposal, wrap

        shown = ", ".join(f"{k}={v}" for k, v in (out.get("key") or {}).items())
        return wrap(
            Proposal(kind="screen_action", summary=f"{out['label']} — {app}/{screen}" + (f" — {shown}" if shown else ""),
                     target_path=f"{app}/{screen}", app=app,
                     payload={"app": app, "screen": screen, "action": out["action"], "key": key or {},
                              "inputs": inputs or {}, "params": params or {}, "preview": out},
                     notes=[*out.get("confirms", []), *(f"runs: {d}" for d in out.get("does", []))]),
            message="Checked and proposed to the user, who applies or skips it on the card: nothing has run yet. Stop here and wait.",
        )

    async def job_form(job: str) -> dict[str, Any]:
        """A job the signed-in user may run, as its page shows it — read it BEFORE job_run: what
        the job does, the parameters a user may set (their choices, which are required), whether
        it needs a file the user brings (in the Liberty chat the user chooses it on the card of
        job_run — you do not handle the file), whether it is running now, and its last runs with their state and error. Call it again after job_run to follow
        the run. The jobs of an application are listed by describe_instance / search_catalog.

        Args:
            job: the job id.
        """
        who = caller()
        if who is None:
            raise ToolPermissionError("permission denied: a job needs a signed-in user")
        try:
            page = await job_page(state(), who, job)
        except ScreenWriteError as exc:
            raise ValueError(str(exc)) from exc
        info = page["job"]
        params = [{**{k: p[k] for k in ("name", "label", "required", "multiple") if p.get(k) not in (None, False, "")},
                   **({"default": p["default"]} if p.get("default") not in (None, "") else {}),
                   **({"choices": p["choices"][:ENUM_MAX]} if p.get("choices") else {})} for p in info.get("params") or []]
        runs = [{k: r.get(k) for k in ("run_id", "id", "state", "started_at", "finished_at", "triggered_by", "error_message") if r.get(k) is not None}
                for r in (page.get("runs") or [])[:5]]
        presets = [pr.name for pr in getattr(state().jobs.registry.get(job), "presets", [])]
        return {"job": info["id"], "description": info.get("description"), "params": params,
                **({"presets": presets} if presets else {}),
                **({"needs_uploaded_file": [u["step"] for u in info["uploads"]]} if info.get("uploads") else {}),
                "running": bool(info.get("in_flight")), "last_runs": runs}

    async def job_run(
        job: str, params: dict[str, Any] | None = None, preset: str | None = None, files: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run a job now, for the signed-in user — what "Run now" does on the job's page. It
        starts real work (a load, a refresh, a report): use it only when the user asked for that
        run, and say which job and which parameters before you call it. Call job_form first.

        The run is queued and goes on in the background: the answer gives its id; job_form shows
        its state afterwards. A job that is already running is refused with the reason. In the
        Liberty chat nothing starts until the user accepts the card — and a job that loads a file
        asks the user for it there: propose the run, do not ask them to paste the file.

        Args:
            job: the job id.
            params: {name: value} among the parameters job_form lists; leave one out for the job's own default.
            preset: one of the job's presets (job_form → presets), applied under the params.
            files: {step: file name} for a step that loads a file — the name send_file returned for this job; not used in the Liberty chat (the card asks).
        """
        who = caller()
        if who is None:
            raise ToolPermissionError("permission denied: a job needs a signed-in user")
        proposing = current_channel.get() != "mcp"
        try:
            out = await perform_job_run(state(), who, job=job, params=params, preset=preset, files=files, dry_run=proposing)
        except ScreenWriteError as exc:
            raise ValueError(str(exc)) from exc
        if not proposing:
            return out
        from liberty.ai.proposal import Proposal, wrap

        shown = ", ".join(f"{k}={v}" for k, v in (params or {}).items())
        return wrap(
            Proposal(kind="job_run", summary=f"Run the job {job}" + (f" — {shown}" if shown else "") + (f" (preset {preset})" if preset else ""),
                     target_path=f"job {job}", payload={"job": job, "params": params or {}, "preset": preset, "preview": out},
                     notes=[*([out["description"]] if out.get("description") else []),
                            *(f"needs a file for the step {u['step']}: chosen on this card" for u in out.get("needs_files") or [])]),
            message="Checked and proposed to the user, who applies or skips it on the card"
                    + (" and chooses the file there" if out.get("needs_files") else "") + ": nothing has started yet. Stop here and wait.",
        )

    async def screen_write_many(
        app: str, screen: str, operation: str, records: list[dict[str, Any]], params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add, change or delete SEVERAL records of one screen in one go — the same operation for
        all, each record written as screen_write writes one. Use it when the user asks for a list
        (the roles of a user, the environments, a few codes): one card for the whole list instead
        of one per record. For a single record use screen_write; beyond 50 records the screen's
        Excel import is the way — say so.

        Every record is checked first: if the screen refuses one, nothing is written and the
        answer names each refused record with its reason. Then they are written in order; if a
        statement fails, the ones before it stay written and the answer says where it stopped.

        Args:
            app: the application of the screen.
            screen: the screen id.
            operation: insert, update or delete — the same for every record.
            records: [{values: {column: value}, key: {key column: value}}, …] — values for an insert or an update, key for an update or a delete.
            params: {param: value} of the screen's read query, when it needs some.
        """
        ctx = _ctx()
        proposing = current_channel.get() != "mcp"
        try:
            out = await perform_many(ctx, app=app, screen=screen, operation=operation, records=records, params=params, dry_run=proposing)
        except ScreenWriteError as exc:
            raise ValueError(str(exc)) from exc
        if not proposing:
            return out
        from liberty.ai.proposal import Proposal, wrap

        lines = [_summary(operation, app, screen, p).split(" — ", 1)[-1] for p in out["records"]]
        verb = {"insert": "Add", "update": "Change", "delete": "Delete"}[operation]
        return wrap(
            Proposal(kind="screen_write_many", summary=f"{verb} {out['count']} records — {app}/{screen}", target_path=f"{app}/{screen}", app=app,
                     payload={"app": app, "screen": screen, "operation": operation, "records": records, "params": params or {}, "preview": out},
                     notes=lines[:20] + ([f"… and {len(lines) - 20} more"] if len(lines) > 20 else [])),
            message="Checked and proposed to the user, who applies or skips the whole list on one card: nothing is written yet. Stop here and wait.",
        )

    async def api_run(connector: str, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call an API endpoint that CHANGES data (list_connectors marks it ``changes_data``): it
        submits, creates or runs something on the other system. Use it only for what the user
        asked, and say which endpoint and which values before you call it. An endpoint that only
        reads is called with api_call.

        In the Liberty chat nothing is sent until the user accepts the card. A refusal (a
        permission, a value that is not allowed) comes back with its reason: give it to the user,
        do not try another way.

        Args:
            connector: the connector's name (from list_connectors).
            endpoint: the endpoint's name on that connector.
            params: {parameter: value} among the endpoint's parameters; leave one out for its default.
        """
        who = caller()
        if who is None:
            raise ToolPermissionError("permission denied: a change needs a signed-in user")
        proposing = current_channel.get() != "mcp"
        try:
            out = await perform_api_run(state(), who, connector=connector, endpoint=endpoint, params=params, dry_run=proposing)
        except ScreenWriteError as exc:
            if str(exc).startswith("permission denied"):
                raise ToolPermissionError(str(exc)) from exc
            raise ValueError(str(exc)) from exc
        if not proposing:
            return out
        from liberty.ai.proposal import Proposal, wrap

        shown = ", ".join(f"{k}={v}" for k, v in (out.get("params") or {}).items() if v not in (None, ""))
        return wrap(
            Proposal(kind="api_run", summary=f"Call {connector}.{endpoint}" + (f" ({out['label']})" if out.get("label") else "") + (f" — {shown}" if shown else ""),
                     target_path=f"endpoint {connector}.{endpoint}", connector=connector,
                     payload={"connector": connector, "endpoint": endpoint, "params": params or {}, "preview": out},
                     notes=[*([out["description"]] if out.get("description") else [])]),
            message="Checked and proposed to the user, who applies or skips it on the card: nothing has been sent yet. Stop here and wait.",
        )

    return [
        *([tool(api_run, summary_keys=("connector", "endpoint", "params"), permission=WRITE_PERMISSION, read_only=False)] if include_api else []),
        tool(job_form, summary_keys=("job",), permission=WRITE_PERMISSION),
        tool(job_run, summary_keys=("job", "params", "preset"), permission=WRITE_PERMISSION, read_only=False),
        tool(screen_form, summary_keys=("app", "screen"), permission=WRITE_PERMISSION),
        # the record's key is in the summary: the usage record says WHICH record the assistant changed
        tool(screen_write, summary_keys=("operation", "app", "screen", "key"), permission=WRITE_PERMISSION, read_only=False),
        tool(screen_write_many, summary_keys=("operation", "app", "screen"), permission=WRITE_PERMISSION, read_only=False),
        tool(screen_action, summary_keys=("action", "app", "screen", "key"), permission=WRITE_PERMISSION, read_only=False),
    ]
