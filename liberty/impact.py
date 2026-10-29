"""Impact — the configuration as one graph, for the Developer page's Impact view.

Every configuration entity (pool, connector, query, API endpoint, dictionary entry / lookup /
sequence / enum, screen, menu item, chart, dashboard, shared action, Explorer model, nomaflow job,
notification rule, an app's AI block) is a node; its edges are the references the dependency walker
already follows for Package & Deploy (:func:`liberty.web.dependencies._resolve` — one source, so what
the view shows is what a package carries). Read forward, an edge says what an entity **uses**; read
backward, what **uses it** — followed transitively, the blast radius of a change: a query → the
screens reading it → the menu items opening them, the jobs and dashboards running it.

Built from ``app.state`` on demand (no cache: a reload changes it); the raw files the walker reads
(jobs, Explorer, notifications, AI) are parsed once per build.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from liberty.web import dependencies as deps

Key = tuple[str, str | None, str]           # (kind, scope, name) — the walker's identity

# Display order of the kinds (the Impact list's groups)
KINDS = ("pool", "connector", "query", "api_endpoint", "dictionary_entry", "lookup", "sequence", "enum",
         "screen", "menu_item", "chart", "dashboard", "action", "explorer_model", "job", "notification_rule", "ai")
# Never reported as unused: entry points nothing is expected to point at (opened from a menu, a page,
# a schedule), and dictionary entries — a column reaches its entry by name (``dd_prefix`` too), which no
# reference records
ROOTS = {"menu_item", "job", "notification_rule", "ai", "pool", "explorer_model", "dictionary_entry"}
_DICT_COLLECTIONS = {"dictionary_entry": "entries", "lookup": "lookups", "sequence": "sequences", "enum": "enums"}


@dataclass
class Graph:
    nodes: set[Key] = field(default_factory=set)                       # entities that exist
    uses: dict[Key, list[tuple[Key, str]]] = field(default_factory=dict)
    used_by: dict[Key, list[tuple[Key, str]]] = field(default_factory=dict)
    info: dict[Key, tuple[str | None, str | None]] = field(default_factory=dict)   # (label, description)

    def exists(self, key: Key) -> bool:
        return key in self.nodes


def entities(state: Any) -> list[Key]:
    """Every configuration entity of the running install."""
    out: list[Key] = []
    cr = getattr(state, "connectors", None)
    if cr is not None:
        pools = getattr(cr, "pools", None)
        out += [("pool", None, p) for p in sorted((getattr(pools, "_configs", None) or {}))]
        for cname in sorted(cr.names()):
            out.append(("connector", None, cname))
            cfg = getattr(cr.get(cname), "config", None)
            if hasattr(cfg, "iter_named_queries"):
                out += [("query", cname, q) for q, _ in cfg.iter_named_queries()]
            out += [("api_endpoint", cname, e.name) for e in (getattr(cfg, "endpoints", None) or []) if getattr(e, "name", None)]
    d = deps._get_dictionary(state)
    if d is not None:
        sections = [("shared", d)] + list((getattr(d, "connectors", None) or {}).items())
        for kind, coll in _DICT_COLLECTIONS.items():
            for scope, section in sections:
                out += [(kind, scope, n) for n in sorted(getattr(section, coll, None) or {})]
    screens = getattr(state, "screens", None)
    for app, smap in sorted((getattr(screens, "screens", None) or {}).items()):
        out += [("screen", app, sid) for sid in sorted(smap)]
    menus = getattr(state, "menus", None)
    for app, menu in sorted((getattr(menus, "menus", None) or {}).items()):
        out += [("menu_item", app, it.id) for it in (getattr(menu, "items", None) or [])]
    charts = getattr(state, "charts", None)
    for scope, cmap in sorted((getattr(charts, "charts", None) or {}).items()):
        out += [("chart", scope, cid) for cid in sorted(cmap)]
    dashboards = getattr(state, "dashboards", None)
    for scope, dmap in sorted((getattr(dashboards, "dashboards", None) or {}).items()):
        out += [("dashboard", scope, did) for did in sorted(dmap if isinstance(dmap, dict) else {})]
    actions = getattr(state, "actions", None)
    out += [("action", None, a) for a in sorted(getattr(actions, "actions", None) or {})]
    for app, models in sorted((deps.raw_config(state, "explorer.toml").get("explorer") or {}).items()):
        if isinstance(models, dict):
            out += [("explorer_model", app, m) for m, v in sorted(models.items()) if isinstance(v, dict)]
    out += [("job", None, j["id"]) for j in deps.raw_config(state, "jobs.toml").get("jobs") or []
            if isinstance(j, dict) and j.get("id")]
    out += [("notification_rule", None, r) for r, v in sorted((deps.raw_config(state, "notifications.toml").get("notifications") or {}).items())
            if isinstance(v, dict)]
    out += [("ai", None, a) for a, v in sorted((deps.raw_config(state, "ai.toml").get("ai") or {}).items()) if isinstance(v, dict)]
    return out


def _identity(state: Any, seed: deps.Seed) -> Key:
    """A reference's node — a dashboard named without its scope (a widget, a legacy seed) resolved to
    the scope holding it, as the walker does."""
    if seed.kind == "dashboard" and not seed.scope:
        scope, _, name = seed.name.partition(".")
        if name:
            return ("dashboard", scope, name)
        for s, dmap in (getattr(getattr(state, "dashboards", None), "dashboards", None) or {}).items():
            if isinstance(dmap, dict) and seed.name in dmap:
                return ("dashboard", s, seed.name)
    return (seed.kind, seed.scope, seed.name)


def _text(config: dict[str, Any], lang: str | None) -> tuple[str | None, str | None]:
    """An entity's label and description from its configuration — the *lang* translation when it has
    one (``l = {fr = "…"}`` or ``l.fr.label``)."""
    label = config.get("label") or config.get("title")
    desc = config.get("description")
    tr = (config.get("l") or {}).get(lang) if lang and isinstance(config.get("l"), dict) else None
    if isinstance(tr, str):
        label = tr
    elif isinstance(tr, dict):
        label = tr.get("label") or label
        desc = tr.get("description") or desc
    clean = lambda v: " ".join(v.split()) if isinstance(v, str) and v.strip() else None  # noqa: E731
    return clean(label), clean(desc)


def short_reason(referrer: Key, reason: str) -> str:
    """The relation alone: the walker's reason names the referrer first (``query jde.get_x · its
    connector``, ``screen nomajde.jde_health.read_query``) — what follows its name."""
    import re
    kind, scope, name = referrer
    for who in (f"{scope}.{name}" if scope else name, name):
        # the name as a whole word: ``s`` is not found inside "something"
        m = re.search(rf"(?<![\w]){re.escape(who)}(?![\w])", reason)
        if m:
            return reason[m.end():].strip(" .·:")
    return reason


def build_graph(state: Any, lang: str | None = None) -> Graph:
    """Every entity and its direct references, both ways."""
    g = Graph()
    deps._raw_memo = {}
    try:
        keys = entities(state)
        g.nodes = set(keys)
        for key in keys:
            kind, scope, name = key
            dep, refs = deps._resolve(state, deps.Seed(kind=kind, name=name, scope=scope))
            if dep is not None and isinstance(dep.config, dict):
                g.info[key] = _text(dep.config, lang)
            if kind == "menu_item":
                # the walker ties every item (folders too) to its app's connector, to package the app;
                # what an item depends on is its target
                refs = [(sd, r) for sd, r in refs if r.endswith(" target")]
            elif kind in ("query", "api_endpoint", "chart") and scope:
                # owned by its connector: a change there (its pool, its base URL) reaches it
                refs = [*refs, (deps.Seed("connector", scope), f"{kind} {scope}.{name} · its connector")]
            seen: set[Key] = set()
            for seed, reason in refs:
                target = _identity(state, seed)
                if target == key or target in seen:
                    continue
                seen.add(target)
                g.uses.setdefault(key, []).append((target, reason))
                g.used_by.setdefault(target, []).append((key, reason))
    finally:
        deps._raw_memo = None
    # a screen without a label of its own is named by the menu item opening it
    for key in g.nodes:
        if key[0] == "screen" and not (g.info.get(key) or (None, None))[0]:
            item = next((r for r, why in g.used_by.get(key, []) if r[0] == "menu_item" and why.endswith(" target")), None)
            if item is not None and (g.info.get(item) or (None, None))[0]:
                g.info[key] = (g.info[item][0], (g.info.get(key) or (None, None))[1])
    return g


def link(key: Key) -> dict[str, Any]:
    """Where the entity is edited — the deep-link shape of find-usages (``deepLinkToUrl``)."""
    kind, scope, name = key
    if kind == "screen":
        return {"editor": "screens", "app": scope, "screen": name}
    if kind in ("query", "api_endpoint"):
        return {"editor": "connectors", "connector": scope, "query": name}
    if kind == "connector":
        return {"editor": "connectors", "connector": name}
    if kind in _DICT_COLLECTIONS:
        return {"editor": "dictionary", "kind": _DICT_COLLECTIONS[kind], "scope": scope, "name": name}
    if kind == "menu_item":
        return {"editor": "menus", "app": scope, "item": name}
    if kind == "chart":
        return {"editor": "charts", "scope": scope, "chart": name}
    if kind == "dashboard":
        return {"editor": "dashboards", "dashboard": f"{scope}.{name}" if scope else name}
    if kind == "job":
        return {"editor": "jobs", "job": name}
    return {"editor": {"pool": "pools", "action": "actions", "explorer_model": "explorer",
                       "notification_rule": "notifications", "ai": "ai"}.get(kind, kind)}


def _node(g: Graph, key: Key) -> dict[str, Any]:
    kind, scope, name = key
    label, description = g.info.get(key) or (None, None)
    return {"kind": kind, "scope": scope, "name": name, "label": label, "description": description,
            "missing": not g.exists(key), "link": link(key)}


def catalogue(state: Any, lang: str | None = None) -> dict[str, Any]:
    """Every entity with its counts of direct references both ways; *unused* = nothing points at it
    (entry points — menu items, jobs, rules, pools — excepted)."""
    g = build_graph(state, lang)
    order = {k: i for i, k in enumerate(KINDS)}
    rows = []
    for key in sorted(g.nodes, key=lambda k: (order.get(k[0], 99), k[1] or "", k[2])):
        used_by = len(g.used_by.get(key, []))
        rows.append({**_node(g, key), "uses": len(g.uses.get(key, [])), "used_by": used_by,
                     "unused": used_by == 0 and key[0] not in ROOTS})
    broken = sorted({t for refs in g.uses.values() for t, _ in refs if not g.exists(t)})
    return {"entities": rows, "total": len(rows), "kinds": [k for k in KINDS if any(r["kind"] == k for r in rows)],
            "broken": [_node(g, k) for k in broken]}


def impact(state: Any, kind: str, name: str, scope: str | None = None, *, lang: str | None = None, max_depth: int = 8) -> dict[str, Any]:
    """One entity: what it uses (direct, then the whole closure by kind) and what uses it — a tree
    walked backwards (each entity once, at its shortest distance), with the reached entities counted
    by kind (the blast radius)."""
    g = build_graph(state, lang)
    key: Key = (kind, scope or None, name)
    if not g.exists(key) and key not in g.used_by:
        raise KeyError(f"{kind} {scope + '.' if scope else ''}{name}: not found")

    uses = [{**_node(g, t), "reason": short_reason(key, r)} for t, r in g.uses.get(key, [])]
    closure: dict[Key, int] = {}
    todo = deque((t, 1) for t, _ in g.uses.get(key, []))
    while todo:
        t, depth = todo.popleft()
        if t in closure or t == key:
            continue
        closure[t] = depth
        todo.extend((n, depth + 1) for n, _ in g.uses.get(t, []))

    tree: list[dict[str, Any]] = []
    index: dict[Key, int] = {key: -1}
    queue = deque([(key, 0)])
    truncated = False
    while queue:
        node, depth = queue.popleft()
        for ref, reason in g.used_by.get(node, []):
            if ref in index:
                continue
            if depth + 1 > max_depth:
                truncated = True
                continue
            index[ref] = len(tree)
            tree.append({**_node(g, ref), "depth": depth + 1, "parent": index[node], "reason": short_reason(ref, reason),
                         "fan_in": len(g.used_by.get(ref, []))})
            queue.append((ref, depth + 1))

    def count(keys: list[Key]) -> dict[str, int]:
        out: dict[str, int] = {}
        for k in keys:
            out[k[0]] = out.get(k[0], 0) + 1
        return {k: out[k] for k in KINDS if k in out}

    return {
        "entity": {**_node(g, key), "uses": len(uses), "used_by": len(g.used_by.get(key, []))},
        "uses": uses,
        "closure": [{**_node(g, t), "depth": d} for t, d in sorted(closure.items(), key=lambda x: (x[1], KINDS.index(x[0][0]) if x[0][0] in KINDS else 99, x[0][2]))],
        "closure_by_kind": count(list(closure)),
        "used_by": tree,
        "reach_by_kind": count([(n["kind"], n["scope"], n["name"]) for n in tree]),
        "truncated": truncated,
    }
