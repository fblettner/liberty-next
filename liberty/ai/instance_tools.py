"""Tools that read the running installation — what it holds and the playbooks the apps ship.

``describe_instance`` summarises the apps (connectors), their menus, screens, dashboards,
explorer models and nomaflow jobs so the assistant can answer "what is there" / "where do I
find" without guessing; ``list_playbooks`` / ``load_playbook`` expose the Markdown procedures
of ``ai.toml`` (``[ai.<app>.playbooks.<id>]``) — the model reads one when the user asks how to
do something in an application, and follows it. All read ``app.state`` at call time through
the ``state`` callable, so a reload is seen; nothing here writes."""

from __future__ import annotations

from typing import Any, Callable

from liberty.ai.tools import Tool, caller, may, tool

# The most results one search_catalog call returns.
SEARCH_MAX_RESULTS = 40


def build_instance_tools(state: Callable[[], Any], *, allowed: list[str] | None = None) -> list[Tool]:
    allow = set(allowed or [])

    def _visible(app: str) -> bool:
        return not allow or app in allow

    def _ai_config() -> Any:
        return getattr(state(), "ai_config", None)

    def _menu_items(app: str, menu: Any, screens_file: Any, dashboards: Any) -> list[Any]:
        """The menu entries the caller sees — the menu route's own rule (``menu:`` / ``dashboard:``
        / ``job:`` overlays on the target's permission); a folder stays while an entry under it does."""
        who = caller()
        if who is None or who.is_superuser:
            return list(menu.items)
        from liberty.web.menus import _keeper, app_is_theirs   # the one definition of menu visibility

        st = state()
        explorer = getattr(st, "explorer", None)
        keep = _keeper(who, dashboards, app, screens_file, charts=getattr(st, "charts", None), explorer=explorer)
        if not app_is_theirs(who, app, menu, keep, dashboards, explorer):
            return []      # links that need no permission do not make the application theirs
        kept = {it.id for it in menu.items if it.type is not None and keep(it, it.connector or app)}
        grew = True
        while grew:      # folders, innermost first
            grew = False
            for it in menu.items:
                if it.type is None and it.id not in kept and any(c.parent == it.id and c.id in kept for c in menu.items):
                    kept.add(it.id)
                    grew = True
        return [it for it in menu.items if it.id in kept]

    def _playbook_apps() -> set[str] | None:
        """The apps whose playbooks the caller may read: those holding something they can open
        (plus ``*``); None = no restriction."""
        if caller() is None:
            return None
        return {a["id"] for a in describe_instance()["apps"]} | {"*"}

    def describe_instance(app: str | None = None) -> dict[str, Any]:
        """Describe this Liberty installation: the applications (connectors) with their menus,
        screens, dashboards, explorer models, the scheduled jobs and the playbooks available.

        Call it when the user asks what exists, where something is, or which application /
        screen / job handles a subject. Returns ids and labels only (no data, no SQL).

        Args:
            app: only this application (its menus, screens, dashboards, models, playbooks) —
                pass the app of the page context when the question is about it. The jobs are
                listed without it only.
        """
        st = state()
        who = caller()
        restricted = who is not None and not who.is_superuser
        explorer = getattr(st, "explorer", None)
        models = [
            (a, mid, m) for a, mid, m in (explorer.items() if explorer is not None and hasattr(explorer, "items") else [])
            if may(f"explorer:{a}:{mid}")
        ]
        model_apps = {a for a, _, _ in models}

        def _wanted(name: str) -> bool:
            return _visible(name) and (not app or name == app)

        connectors = getattr(st, "connectors", None)
        screens = getattr(getattr(st, "screens", None), "screens", {}) or {}
        menus = getattr(getattr(st, "menus", None), "menus", {}) or {}
        apps: list[dict[str, Any]] = []
        for name in sorted(connectors.names()) if connectors is not None else []:
            if not _wanted(name):
                continue
            conn = connectors.get(name)
            entry: dict[str, Any] = {"id": name, "type": type(conn).__name__.replace("Connector", "").lower()}
            menu = menus.get(name)
            if menu is not None:
                if menu.label:
                    entry["label"] = menu.label     # the app's display name lives on its menu
                items = _menu_items(name, menu, getattr(st, "screens", None), getattr(st, "dashboards", None))
                if items:
                    entry["menu"] = [
                        {"id": it.id, "label": it.label, **({"type": it.type} if it.type else {}), **({"target": it.target} if it.target else {})}
                        for it in items
                    ]
            # a screen = its read query's permission, as the screens catalogue does
            by_id = {sid: s for sid, s in (screens.get(name) or {}).items() if may(f"sql:{s.connector or name}:{s.read_query}")}
            if by_id:
                # the read query by its exact name: the model must not guess `<screen>_get`
                # … and the connector that query is on, when it is not the application itself
                entry["screens"] = [
                    {"id": sid, "label": s.label or s.description or sid, "read_query": s.read_query,
                     **({"connector": s.connector} if s.connector and s.connector != name else {})}
                    for sid, s in by_id.items()
                ]
            if restricted and not (entry.get("menu") or entry.get("screens") or name in model_apps):
                continue      # nothing of this application is the caller's: not even its name
            apps.append(entry)
        if app and not apps:
            raise KeyError(f"no application {app!r} — call describe_instance with no argument for the list")
        shown = {a["id"] for a in apps}
        out: dict[str, Any] = {"apps": apps}
        dashboards = getattr(st, "dashboards", None)
        if dashboards is not None and hasattr(dashboards, "iter_dashboards"):
            out["dashboards"] = [
                {"id": d.qualified_id, "label": getattr(d, "label", None) or did}
                for scope, did, d in dashboards.iter_dashboards()
                if _wanted(scope) and not (who is not None and who.is_denied(f"dashboard:{d.qualified_id}"))
                and (not restricted or scope in shown)
            ]
        if explorer is not None and hasattr(explorer, "items"):
            out["explorer_models"] = [
                {"app": a, "id": mid, "label": m.label or mid, "entities": sorted(m.entities)}
                for a, mid, m in models if _wanted(a)
            ]
        jobs = getattr(st, "jobs", None)
        registry = getattr(jobs, "registry", None)
        if registry is not None and not app:
            out["jobs"] = [
                {"id": j.id, **({"description": j.description} if j.description else {}),
                 "schedule": j.schedule, "enabled": j.enabled, "steps": [s.name for s in j.steps]}
                for j in registry.jobs() if may(f"job:{j.id}")     # the permission that runs it
            ]
        cfg = _ai_config()
        if cfg is not None:
            out["playbooks"] = [
                {"app": a, "id": pid, "title": pb.title}
                for a, pid, pb in cfg.playbooks()
                if _visible(a) and (not app or a in (app, "*")) and (not restricted or a in shown or a == "*")
            ]
        return out

    def search_catalog(query: str) -> dict[str, Any]:
        """Find things of this installation by keyword: screens (with their read query), menu
        entries, dashboards, explorer models, jobs and playbooks whose id, label or description
        contains every word. Quicker than describe_instance when you look for one subject
        ("address book", "licence", "sales load"). For the connector queries themselves, use
        list_connectors with `search`.

        Args:
            query: the keywords, in the language of the labels or by id (e.g. "f0092", "users role").
        """
        words = query.casefold().split()
        if not words:
            raise ValueError("give at least one keyword")
        full = describe_instance()
        hits: list[dict[str, Any]] = []

        def _add(kind: str, item: dict[str, Any], *, app_id: str | None = None) -> None:
            text = " ".join(str(v) for v in item.values() if isinstance(v, (str, int))).casefold()
            if all(w in text for w in words):
                hits.append({"kind": kind, **({"app": app_id} if app_id else {}), **item})

        for a in full["apps"]:
            label = a.get("label")
            _add("application", {"id": a["id"], **({"label": label} if label else {})})
            for s in a.get("screens", []):
                _add("screen", s, app_id=a["id"])
            for m in a.get("menu", []):
                _add("menu", m, app_id=a["id"])
        for d in full.get("dashboards", []):
            _add("dashboard", d)
        for m in full.get("explorer_models", []):
            _add("explorer_model", {**m, "entities": ", ".join(m["entities"])})
        for j in full.get("jobs", []):
            _add("job", {**j, "steps": ", ".join(str(s) for s in j["steps"])})
        cfg = _ai_config()
        mine = {a["id"] for a in full["apps"]} | {"*"}
        for a, pid, pb in (cfg.playbooks() if cfg is not None else []):
            if _visible(a) and (caller() is None or a in mine):
                _add("playbook", {"app": a, "id": pid, "title": pb.title, **({"description": pb.description} if pb.description else {})})
        out: dict[str, Any] = {"matches": len(hits), "results": hits[:SEARCH_MAX_RESULTS]}
        if len(hits) > SEARCH_MAX_RESULTS:
            out["note"] = f"{len(hits)} matches, the first {SEARCH_MAX_RESULTS} shown — add a word to narrow."
        elif not hits:
            out["note"] = "No match. Try other words, or list_connectors with `search` for a connector query."
        return out

    def list_playbooks() -> list[dict[str, Any]]:
        """List the playbooks — step-by-step procedures the applications ship for their
        operators (how to create a user, how to duplicate a role…). Call it when the user
        asks HOW to do something in an application; then load the one that fits.
        """
        cfg = _ai_config()
        if cfg is None:
            return []
        mine = _playbook_apps()
        return [
            {"app": app, "id": pid, "title": pb.title, **({"description": pb.description} if pb.description else {})}
            for app, pid, pb in cfg.playbooks() if _visible(app) and (mine is None or app in mine)
        ]

    def load_playbook(app: str, id: str) -> dict[str, Any]:
        """Load one playbook's full text (Markdown). Follow its steps, in order, and tell the
        user which playbook you are following.

        Args:
            app: the application the playbook belongs to (from list_playbooks).
            id: the playbook id (from list_playbooks).
        """
        cfg = _ai_config()
        block = getattr(cfg, "ai", {}).get(app) if cfg is not None else None
        pb = block.playbooks.get(id) if block is not None else None
        mine = _playbook_apps()
        if pb is None or not _visible(app) or (mine is not None and app not in mine):
            raise KeyError(f"no playbook {app}/{id} — call list_playbooks for the available ones")
        return {"app": app, "id": id, "title": pb.title, "body": pb.body}

    return [
        tool(describe_instance, summary_keys=("app",)),
        tool(search_catalog, summary_keys=("query",)),
        tool(list_playbooks),
        tool(load_playbook, summary_keys=("app", "id")),
    ]
