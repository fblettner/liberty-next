"""What each menu entry needs, worked out from the loaded configuration.

For :mod:`liberty.auth.implied`: a role that allows a menu folder (read, or read and change) is
given what the screens under it use, without a person listing queries.

For one entry the walk starts at its target and follows the same references as the package
builder (:mod:`liberty.web.dependencies` — one knowledge of "what a screen uses"):

* a screen → its read query, the lists behind its dropdowns (the dictionary's lookups, their
  queries), its chart, its export sheets, the screens of its record form's tabs, what its
  actions and hooks run (shared actions included);
* a dashboard → the queries of its widgets and filters; a page → its own permission; the Data
  Explorer page → the application's models.

Two levels come out of it:

* **read** — everything above that does not write;
* **change** — what writes or acts: the queries and endpoints marked ``writable`` (the screen's
  update / insert / delete, a writing action), the functions its actions call
  (``plugin:<callable>``: what a function does is not known, so it is never given by "read"),
  and **running a job** (``job:<id>``, for an entry that is a job): "read" never starts anything.

Following a row click to another screen gives that screen's READ only: changing it needs a
grant on its own entry. An endpoint served by a shared action is a gate of its own — the walk
stops at the endpoint.
"""

from __future__ import annotations

from typing import Any

from liberty.auth.implied import MenuAccess
from liberty.web.dependencies import Dependency, Seed, _as_endpoint, _resolve

_SOURCES = ("menus", "screens", "connectors", "dashboards", "charts", "actions", "explorer")
_FOLLOWED = {"menu_item", "screen", "dashboard", "chart", "action", "query", "dictionary_entry", "lookup", "sequence", "enum"}


def _plugins(config: Any, out: set[str]) -> None:
    """The functions a screen's or a shared action's steps call, at any depth."""
    if isinstance(config, dict):
        if config.get("type") == "call_plugin" and isinstance(config.get("callable"), str) and config["callable"]:
            out.add(f"plugin:{config['callable']}")
        for v in config.values():
            _plugins(v, out)
    elif isinstance(config, list):
        for v in config:
            _plugins(v, out)


def _walk(state: Any, start: Seed, memo: dict[tuple[str, str | None, str], tuple[Dependency | None, list[tuple[Seed, str]]]]) -> tuple[set[str], set[str]]:
    read: set[str] = set()
    change: set[str] = set()
    # key → whether it was walked with "may change" (a second visit only matters when it widens)
    seen: dict[tuple[str, str | None, str], bool] = {}
    work: list[tuple[Seed, bool]] = [(start, True)]
    while work:
        seed, full = work.pop()
        seed = _as_endpoint(state, seed)
        key = (seed.kind, seed.scope, seed.name)
        if key in seen and (seen[key] or not full):
            continue
        seen[key] = full
        if seed.kind == "job":
            if full:
                change.add(f"job:{seed.name}")           # running it: never given by "read"
            continue
        if seed.kind not in _FOLLOWED and seed.kind != "api_endpoint":
            continue                                    # connectors, pools: nothing a caller runs
        if key not in memo:
            memo[key] = _resolve(state, seed)
        dep, refs = memo[key]
        if dep is None:
            continue
        if seed.kind in ("query", "api_endpoint"):
            perm = f"{'sql' if seed.kind == 'query' else 'api'}:{seed.scope}:{seed.name}"
            if dep.config.get("writable"):
                if full:
                    change.add(perm)
            else:
                read.add(perm)
            if seed.kind == "api_endpoint":
                continue                                # served by an action: the endpoint is the gate
        if full and seed.kind in ("screen", "action"):
            _plugins(dep.config, change)
        for ref, reason in refs:
            work.append((ref, full and not reason.endswith(" row_click")))
    return read, change


def build(state: Any) -> MenuAccess:
    """What every entry of every application's menu needs."""
    from liberty.web.menus import EXPLORER_PAGE, _explorer_models, page_permission

    access = MenuAccess()
    menus = getattr(getattr(state, "menus", None), "menus", None) or {}
    explorer = getattr(state, "explorer", None)
    memo: dict[tuple[str, str | None, str], tuple[Dependency | None, list[tuple[Seed, str]]]] = {}
    for app, app_menu in menus.items():
        items = list(getattr(app_menu, "items", None) or [])
        parent = {it.id: it.parent for it in items}
        for it in items:
            if not it.type or not it.target:
                continue                                # a folder: it gives through its entries
            chain: list[str] = []
            node: str | None = it.id
            while node and node not in chain:           # a loop in the parents ends the chain
                chain.append(node)
                node = parent.get(node)
            read: set[str] = set()
            change: set[str] = set()
            if it.type == "page":
                perm = page_permission(it.target)
                if perm:
                    read.add(perm)
                if it.target == EXPLORER_PAGE:
                    read.update(f"explorer:{app}:{m}" for m in _explorer_models(explorer, app))
            elif it.type != "url":
                read, change = _walk(state, Seed("menu_item", it.id, app), memo)
                if it.type == "dashboard":
                    read.add(f"dashboard:{it.target}")
            access.add(app, it.id, tuple(chain), read, change)
    return access


def access_of(state: Any) -> MenuAccess:
    """The answer for the configuration *state* holds now: built once, and again as soon as a
    reload replaces one of the objects it was read from. The objects themselves are kept with
    the answer (compared by identity): an address is never taken for another object's."""
    sources = tuple(getattr(state, name, None) for name in _SOURCES)
    cached = getattr(state, "_menu_access", None)
    if cached is not None and len(cached[0]) == len(sources) and all(a is b for a, b in zip(cached[0], sources, strict=True)):
        return cached[1]
    access = build(state)
    state._menu_access = (sources, access)
    return access
