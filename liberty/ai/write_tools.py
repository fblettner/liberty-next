"""Write tools — the assistant changes a record through its screen: ``screen_form`` (what a
screen lets its user enter and run), ``screen_write`` (add, change or delete one record) and
``screen_action`` (run one of the screen's actions — a row menu's "Close", a toolbar's or a form
tab's button — on the server, through :mod:`liberty.screens.actions`).

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


def build_write_tools(state: Callable[[], Any]) -> list[Tool]:
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

    return [
        tool(screen_form, summary_keys=("app", "screen"), permission=WRITE_PERMISSION),
        # the record's key is in the summary: the usage record says WHICH record the assistant changed
        tool(screen_write, summary_keys=("operation", "app", "screen", "key"), permission=WRITE_PERMISSION, read_only=False),
        tool(screen_action, summary_keys=("action", "app", "screen", "key"), permission=WRITE_PERMISSION, read_only=False),
    ]
