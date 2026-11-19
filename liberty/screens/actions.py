"""A screen's actions run on the server — the twin of the browser's ``actionRunner.ts``, for a
caller that has no browser (the assistant's ``screen_action`` tool, and the save hooks of
``screen_write``).

A screen's row menu ("Validate", "Close"), its toolbar, a form tab's buttons and its save hooks
(``on_insert`` / ``on_update`` / ``on_delete``, the form's ``on_save``) are lists of actions:
chains of ``run_query`` / ``call_api`` / ``call_plugin`` / ``call_action`` steps with ``if`` /
``loop`` / ``return``, a ``confirm`` before, a ``notify`` after. This module walks them the way
the browser does — the same context (``INPUT``, each step's ``first_row`` / ``rows``, ``loop``),
the same bind resolution (a literal, a row column, a dotted path, a ``#LOGIN_USER#`` built-in, a
default), the same ``stop_on_error`` — and each step that does something goes through the code
behind the route the browser would call: :func:`liberty.web.connectors._run_sql`,
:func:`liberty.web.connectors.call_http`, :func:`liberty.web.plugins.run_plugin_callable`,
:func:`liberty.notify.events.fire_action_rule`. So the permissions (``sql:`` / ``api:`` /
``plugin:``), the dictionary rules and the change-package capture are those of the screen.

What a server cannot do is said, not faked:

* ``confirm`` — whoever called has already been asked (the chat's card, the MCP client): the
  message is kept in the result;
* a prompt (``prompt_fields``) — its values are given up front (``inputs``); a required one that
  is missing refuses the run before anything is done;
* ``notify`` — its message is returned; ``refresh`` means nothing here;
* ``navigate`` / ``open_url`` — the screen would open something: noted, and a ``navigate`` ends
  the chain where the browser would leave the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from liberty.screens.save import ScreenWriteError, WriteContext, _require, _run, with_upper

Row = dict[str, Any]
_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.$@#]+)\}|#([A-Z][A-Z0-9_.]*)#")
_PROMPTABLE = ("run_query", "call_api", "call_plugin", "navigate", "chain", "call_action")
# the steps that only move the browser: an action made of nothing else has nothing to run here
_BROWSER_ONLY = ("navigate", "open_url", "refresh", "set_field", "return", "notify", "confirm")


class ActionInputError(ScreenWriteError):
    """A prompt of the action needs a value that was not given."""


@dataclass(slots=True)
class ChainResult:
    ok: bool = True
    error: str | None = None                                  # what stopped the chain
    errors: list[str] = field(default_factory=list)           # failed steps the chain went past (stop_on_error = false)
    messages: list[str] = field(default_factory=list)         # the notify messages
    confirmed: list[str] = field(default_factory=list)        # the confirm messages, taken as accepted
    ran: list[str] = field(default_factory=list)              # the steps that did something
    skipped: list[str] = field(default_factory=list)          # what only a browser does
    returned: Row = field(default_factory=dict)               # return / set_field values

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok and not self.errors}
        for k, name in (("error", "error"), ("errors", "errors"), ("messages", "messages"), ("confirmed", "confirmed"),
                        ("ran", "steps"), ("skipped", "skipped"), ("returned", "returned")):
            v = getattr(self, k)
            if v:
                out[name] = v
        return out


@dataclass(slots=True)
class ChainDeps:
    ctx: WriteContext
    default_connector: str
    inputs: Mapping[str, Any] = field(default_factory=dict)   # the prompts' values, given up front
    change_context: dict[str, Any] | None = None              # a change-tracked screen's save hooks
    navigates: bool = True                                    # a row menu / toolbar leaves the page; a save hook cannot
    origin: str | None = None
    replay_override: bool | None = None
    stack: tuple[str, ...] = ()
    # the record as it may be shown: what a confirm / notify message is filled from, when it
    # differs from the record the steps bind (values flagged sensitive replaced by a marker)
    display: Mapping[str, Any] | None = None


# -- sources, binds, conditions (actionRunner.ts) --------------------------------- #


def _ci(obj: Mapping[str, Any], key: str) -> Any:
    if key in obj:
        return obj[key]
    low = key.lower()
    for k, v in obj.items():
        if str(k).lower() == low:
            return v
    return None


def _builtin(key: str, deps: ChainDeps) -> Any:
    k = key.upper()
    who = deps.ctx.principal
    now = datetime.now(timezone.utc)
    if k in ("LOGIN_USER", "LOGIN", "USER"):
        return who.username
    if k in ("LOGIN_EMAIL", "EMAIL"):
        return who.email
    if k in ("LANGUAGE", "LANG"):
        return deps.ctx.language
    if k in ("POOL", "ENVIRONMENT"):
        return deps.ctx.pool
    if k.startswith("URL."):
        urls = getattr(getattr(getattr(deps.ctx.state, "settings", None), "app", None), "external_urls", None) or {}
        return urls.get(k[4:])
    if k in ("SYSDATE", "CURRENT_DATE", "TODAY"):
        return now.strftime("%Y-%m-%d")
    if k in ("NOW", "CURRENT_TIMESTAMP"):
        return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
    if k == "CURRENT_TIME":
        return now.strftime("%H:%M:%S")
    return None


def resolve_source(path: str, chain: Row, form: Mapping[str, Any], deps: ChainDeps) -> Any:
    """A bind's ``source``: a built-in, a plain name (the chain context, then the row / form), or
    a dotted path into the context (``INPUT.x``, ``step.first_row.col``, ``step.rows.0.col``,
    ``loop.col``) — segments matched whatever their case."""
    if not path:
        return None
    if path.startswith("#") and path.endswith("#") and len(path) > 2:
        return _builtin(path[1:-1], deps)
    if "." not in path:
        return chain[path] if path in chain else _ci(form, path)
    cur: Any = chain
    for seg in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            if not seg.isdigit() or int(seg) >= len(cur):
                return None
            cur = cur[int(seg)]
        elif isinstance(cur, Mapping):
            cur = _ci(cur, seg)
        else:
            return None
    return cur


def resolve_binds(binds: Any, chain: Row, form: Mapping[str, Any], deps: ChainDeps) -> dict[str, str]:
    """``resolveBinds``: a literal wins; else the source; else the default (a literal, a built-in
    or a path). An empty result is dropped — the parameter is not sent."""
    out: dict[str, str] = {}
    for b in binds or []:
        if b.value is not None and b.value != "":
            out[b.param] = str(b.value)
            continue
        if b.source:
            v = resolve_source(b.source, chain, form, deps)
            if v is not None and str(v) != "":
                out[b.param] = str(v)
                continue
        d = getattr(b, "default", None)
        if d is not None and d != "":
            d = str(d)
            if (d.startswith("#") and d.endswith("#") and len(d) > 2) or "." in d:
                dv = resolve_source(d, chain, form, deps)
                if dv is not None and str(dv) != "":
                    out[b.param] = str(dv)
            else:
                out[b.param] = d
    return out


def _truthy(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in ("", "n", "no", "0", "false", "null")


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def eval_condition(cond: Any, chain: Row, form: Mapping[str, Any], deps: ChainDeps) -> bool:
    value = resolve_source(cond.source, chain, form, deps)
    cmp = "" if cond.value is None else str(cond.value)
    op = cond.operator
    if op == "equals":
        return ("" if value is None else str(value)) == cmp
    if op == "not_equals":
        return ("" if value is None else str(value)) != cmp
    if op == "truthy":
        return _truthy(value)
    if op == "falsy":
        return not _truthy(value)
    if op == "has_rows":
        return isinstance(value, list) and len(value) > 0
    if op == "no_rows":
        return not isinstance(value, list) or len(value) == 0
    if op == "greater_than":
        return _number(value) > _number(cond.value)
    if op == "less_than":
        return _number(value) < _number(cond.value)
    return False


def fill_text(template: str, chain: Row, form: Mapping[str, Any], deps: ChainDeps) -> str:
    """A confirm / notify message with its ``{FIELD}`` / ``{INPUT.x}`` / ``#POOL#`` placeholders."""
    def one(m: re.Match[str]) -> str:
        v = resolve_source(m.group(1) or f"#{m.group(2)}#", chain, form, deps)
        return "" if v is None else str(v)
    return _PLACEHOLDER.sub(one, template or "")


# -- what an action is made of (for the form's description and the chat's card) ------- #


def _shared(deps_or_state: Any, ref: str) -> Any:
    actions = getattr(getattr(deps_or_state, "actions", None), "actions", None) or {}
    return actions.get(ref)


def _children(a: Any) -> list[Any]:
    out: list[Any] = []
    for k in ("steps", "then_steps", "else_steps"):
        out += list(getattr(a, k, None) or [])
    return out


def outline(actions: Any, state: Any, _seen: tuple[str, ...] = ()) -> dict[str, Any]:
    """What running *actions* involves, read from their definition (nothing is run): the values a
    prompt asks (``needs``), the confirmations (raw messages), the steps that do something
    (``does``) and what only a browser does (``browser``)."""
    needs: list[dict[str, Any]] = []
    confirms: list[str] = []
    does: list[str] = []
    browser: list[str] = []

    def prompt(fields: Any) -> None:
        for f in fields or []:
            if getattr(f, "hidden", False):
                continue
            entry = {"name": f.name, **({"label": f.label} if f.label else {}), **({"required": True} if f.required else {}),
                     **({"default": f.default} if f.default not in (None, "") else {})}
            if entry["name"] not in {n["name"] for n in needs}:
                needs.append(entry)

    def walk(a: Any, seen: tuple[str, ...]) -> None:
        if a.type in _PROMPTABLE:
            prompt(getattr(a, "prompt_fields", None))
        if a.type == "confirm":
            confirms.append(a.message)
        elif a.type == "run_query":
            does.append(f"query {a.connector or '<the screen connector>'}.{a.query}")
        elif a.type == "call_api":
            does.append(f"API {a.connector}.{a.endpoint}")
        elif a.type == "call_plugin":
            does.append(f"plugin {a.callable}")
        elif a.type == "send_notification":
            does.append(f"notification {a.rule}")
        elif a.type == "call_action":
            sh = _shared(state, a.ref)
            if sh is None or a.ref in seen:
                does.append(f"shared action {a.ref} (undefined)" if sh is None else f"shared action {a.ref} (cycle)")
            else:
                prompt(sh.prompt_fields)
                for st in sh.steps or []:
                    walk(st, (*seen, a.ref))
        elif a.type in ("navigate", "open_url"):
            browser.append(f"{a.type} {getattr(a, 'to', None) or getattr(a, 'screen', None) or getattr(a, 'url', '')}".strip())
        for child in _children(a):
            walk(child, seen)

    for a in actions or []:
        walk(a, _seen)
    return {k: v for k, v in (("needs", needs), ("confirms", confirms), ("does", does), ("browser", browser)) if v}


def does_something(actions: Any, state: Any) -> bool:
    """Whether *actions* run anything on the server (a pure drill / link does not)."""
    return bool(outline(actions, state).get("does"))


def check_inputs(actions: Any, state: Any, inputs: Mapping[str, Any] | None) -> None:
    """Refuse before anything runs when a required prompt value is missing."""
    missing = [n["name"] for n in outline(actions, state).get("needs", [])
               if n.get("required") and n.get("default") in (None, "") and _ci(inputs or {}, n["name"]) in (None, "")]
    if missing:
        raise ActionInputError(f"this needs a value for: {', '.join(missing)} — give them in `inputs`")


# -- the runner ------------------------------------------------------------------- #


def _prompt_values(fields: Any, deps: ChainDeps) -> Row:
    out: Row = {}
    for f in fields or []:
        v = _ci(deps.inputs, f.name)
        if v in (None, "") and f.default not in (None, ""):
            v = f.default
        if v in (None, ""):
            if f.required and not getattr(f, "hidden", False):
                raise ActionInputError(f"this needs a value for: {f.name} — give it in `inputs`")
            continue
        out[f.name] = v
    return out


def _bind_rows(chain: Row, step_id: str, rows: Any, first: Any = None) -> None:
    rows = rows if isinstance(rows, list) else []
    chain[step_id] = {"rows": rows, "first_row": first if first is not None else (rows[0] if rows else {}), "success": True}


async def _run_one(a: Any, chain: Row, form: Mapping[str, Any], deps: ChainDeps, result: ChainResult) -> bool:
    """Run one action; True = the chain stops here (an error, or the page would change)."""
    label = getattr(a, "label", None) or a.id
    stop = getattr(a, "stop_on_error", True) is not False

    def fail(msg: str) -> bool:
        if stop:
            result.ok, result.error = False, msg
            return True
        result.errors.append(msg)
        return False

    if a.type in _PROMPTABLE and getattr(a, "prompt_fields", None):
        chain["INPUT"] = {**chain.get("INPUT", {}), **_prompt_values(a.prompt_fields, deps)}
    try:
        if a.type == "chain":
            for step in a.steps or []:
                if await _run_one(step, chain, form, deps, result):
                    return True
        elif a.type == "call_action":
            shared = _shared(deps.ctx.state, a.ref)
            if shared is None:
                return fail(f"{label}: shared action '{a.ref}' is not defined")
            if a.ref in deps.stack:
                result.ok, result.error = False, f"shared action cycle: {' → '.join((*deps.stack, a.ref))}"
                return True
            defaults: Row = {}
            for p in shared.params or []:
                if p.default is None:
                    continue
                d = p.default
                built = _builtin(d[1:-1], deps) if (d.startswith("#") and d.endswith("#") and len(d) > 2) else None
                defaults[p.name] = built if built is not None else d
            seed = resolve_binds(a.param_binds, chain, form, deps)
            chain["INPUT"] = {**chain.get("INPUT", {}), **defaults, **seed}
            if shared.prompt_fields:
                chain["INPUT"] = {**chain["INPUT"], **_prompt_values(shared.prompt_fields, deps), **seed}
            child = ChainDeps(
                ctx=deps.ctx, default_connector=deps.default_connector, inputs=deps.inputs, change_context=deps.change_context,
                navigates=deps.navigates, origin=deps.origin, stack=(*deps.stack, a.ref), display=deps.display,
                replay_override=deps.replay_override if deps.replay_override is not None else bool(a.change_replay),
            )
            for step in shared.steps or []:
                if await _run_one(step, chain, form, child, result):
                    return True
        elif a.type == "if":
            branch = a.then_steps if eval_condition(a.condition, chain, form, deps) else a.else_steps
            for step in branch or []:
                if await _run_one(step, chain, form, deps, result):
                    return True
        elif a.type == "loop":
            arr = resolve_source(a.source, chain, form, deps)
            if not isinstance(arr, list):
                return fail(f"loop '{a.id}': source '{a.source}' did not resolve to an array")
            saved = chain.get("loop")
            try:
                for elem in arr:
                    chain["loop"] = elem if isinstance(elem, Mapping) else {"value": elem}
                    for step in a.steps or []:
                        if await _run_one(step, chain, form, deps, result):
                            return True
            finally:
                chain["loop"] = saved
        elif a.type == "return":
            for dest, src in (a.bindings or {}).items():
                v = resolve_source(src, chain, form, deps)
                if v is not None:
                    result.returned[dest] = v
        elif a.type == "set_field":
            v = a.value if a.value is not None else (resolve_source(a.source, chain, form, deps) if a.source else None)
            if v is not None:
                result.returned[a.target] = v
        elif a.type == "run_query":
            target = a.connector or deps.default_connector
            bound = resolve_binds(a.param_binds, chain, form, deps)
            _require(deps.ctx, target, a.query)
            tag = {**deps.change_context, "action": deps.origin} if deps.change_context else None
            resp = await _run(deps.ctx, target, a.query, with_upper(bound), action_context=tag)
            kind = resp.get("statement_type")
            result.ran.append(f"{label}: {target}.{a.query}" + (f" ({resp.get('rowcount')} row)" if kind and kind != "SELECT" else ""))
            if a.bind_result:
                _bind_rows(chain, a.id, resp.get("rows"))
        elif a.type == "call_api":
            from liberty.web.connectors import call_http

            bound = resolve_binds(a.param_binds, chain, form, deps)
            replay = deps.replay_override if deps.replay_override is not None else bool(a.change_replay)
            tag = {**deps.change_context, "replay": replay, "action": deps.origin} if deps.change_context else None
            resp = await call_http(deps.ctx.state, deps.ctx.principal, deps.ctx.connectors, deps.ctx.screens,
                                   a.connector, a.endpoint, dict(bound), tag)
            if resp.get("success") is False:
                return fail(f"{label}: API {resp.get('status_code') or ''} {resp.get('error') or ''}".strip())
            result.ran.append(f"{label}: API {a.connector}.{a.endpoint}")
            if a.bind_result:
                payload = resp.get("extracted") if resp.get("extracted") is not None else resp.get("json")
                _bind_rows(chain, a.id, payload if isinstance(payload, list) else ([] if payload is None else [payload]))
        elif a.type == "call_plugin":
            from liberty.web.plugins import run_plugin_callable

            bound = resolve_binds(a.param_binds, chain, form, deps)
            replay = deps.replay_override if deps.replay_override is not None else bool(a.change_replay)
            tag = {**deps.change_context, "replay": replay, "action": deps.origin} if deps.change_context else None
            resp = await run_plugin_callable(deps.ctx.state, deps.ctx.principal, deps.ctx.connectors, a.callable, dict(bound), tag)
            result.ran.append(f"{label}: plugin {a.callable}")
            if a.bind_result:
                _bind_rows(chain, a.id, resp.get("rows"), resp.get("first_row"))
        elif a.type == "notify":
            result.messages.append(fill_text(a.message, chain, deps.display if deps.display is not None else form, deps))
        elif a.type == "send_notification":
            from liberty.notify.events import fire_action_rule

            state = deps.ctx.state
            bound = resolve_binds(a.param_binds, chain, form, deps)
            report = await fire_action_rule(
                getattr(state, "notification_rules", None), getattr(state, "notify", None), rule_id=a.rule,
                payload={**with_upper(form), **with_upper(bound)}, user=deps.ctx.principal.username,
            )
            if report is None:
                return fail(f"{label}: no action rule {a.rule!r} (an enabled rule with trigger kind = action)")
            sent = report.to_dict()
            chain[a.id] = {"rows": [], "first_row": sent, "success": not sent.get("errors")}
            result.ran.append(f"{label}: notification {a.rule}")
            if sent.get("errors"):
                result.messages.append(f"{label}: {'; '.join(str(v) for v in sent['errors'].values())}")
        elif a.type == "confirm":
            result.confirmed.append(fill_text(a.message, chain, deps.display if deps.display is not None else form, deps))
        elif a.type == "refresh":
            pass
        elif a.type == "open_url":
            result.skipped.append(f"{label}: the screen would open {fill_text(a.url, chain, form, deps)}")
        elif a.type == "navigate":
            where = a.screen or a.to or ""
            if deps.navigates:
                result.skipped.append(f"{label}: the screen would open {where}")
                return True                          # the page changes: the rest of the chain does not run
            return fail(f"action '{a.id}' (navigate) — no navigate handler in this context")
        return False
    except ActionInputError:
        raise
    except Exception as exc:  # noqa: BLE001 — a step's failure is the chain's, said with the step's name
        detail = getattr(exc, "detail", None)
        return fail(f"{label}: {detail if isinstance(detail, str) else exc}")


async def run_chain(
    actions: Any, form: Mapping[str, Any], deps: ChainDeps, *, initial_input: Mapping[str, Any] | None = None,
) -> ChainResult:
    """Run *actions* in order with a fresh chain context — ``runChain``. *form* is the firing
    context a plain ``source`` reads (the clicked row, the saved record); *initial_input* seeds
    ``INPUT``."""
    chain: Row = {"INPUT": dict(initial_input or {})}
    result = ChainResult()
    for a in actions or []:
        sub = ChainDeps(
            ctx=deps.ctx, default_connector=deps.default_connector, inputs=deps.inputs, change_context=deps.change_context,
            navigates=deps.navigates, origin=deps.origin or (getattr(a, "label", None) or a.id),
            replay_override=deps.replay_override, stack=deps.stack, display=deps.display,
        )
        if await _run_one(a, chain, form, sub, result):
            break
    return result


def entity_key(screen: Any, row: Mapping[str, Any]) -> dict[str, Any] | None:
    """``entityKeyOf``: the firing row's natural key, from the columns flagged ``key``."""
    out = {c.name.upper(): v for c in screen.columns if c.key and (v := _ci(row, c.name)) not in (None, "")}
    return out or None
