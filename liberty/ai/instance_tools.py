"""Tools that read the running installation — what it holds and the playbooks the apps ship.

``describe_instance`` summarises the apps (connectors), their menus, screens, dashboards,
explorer models and nomaflow jobs so the assistant can answer "what is there" / "where do I
find" without guessing; ``list_playbooks`` / ``load_playbook`` expose the Markdown procedures
of ``ai.toml`` (``[ai.<app>.playbooks.<id>]``) — the model reads one when the user asks how to
do something in an application, and follows it. All read ``app.state`` at call time through
the ``state`` callable, so a reload is seen; nothing here writes."""

from __future__ import annotations

from typing import Any, Callable

from liberty.ai.tools import Tool, tool


def build_instance_tools(state: Callable[[], Any], *, allowed: list[str] | None = None) -> list[Tool]:
    allow = set(allowed or [])

    def _visible(app: str) -> bool:
        return not allow or app in allow

    def _ai_config() -> Any:
        return getattr(state(), "ai_config", None)

    def describe_instance() -> dict[str, Any]:
        """Describe this Liberty installation: the applications (connectors) with their menus,
        screens, dashboards, explorer models, the scheduled jobs and the playbooks available.

        Call it when the user asks what exists, where something is, or which application /
        screen / job handles a subject. Returns ids and labels only (no data, no SQL).
        """
        st = state()
        connectors = getattr(st, "connectors", None)
        screens = getattr(getattr(st, "screens", None), "screens", {}) or {}
        menus = getattr(getattr(st, "menus", None), "menus", {}) or {}
        apps: list[dict[str, Any]] = []
        for name in sorted(connectors.names()) if connectors is not None else []:
            if not _visible(name):
                continue
            conn = connectors.get(name)
            app: dict[str, Any] = {"id": name, "type": type(conn).__name__.replace("Connector", "").lower()}
            menu = menus.get(name)
            if menu is not None:
                if menu.label:
                    app["label"] = menu.label     # the app's display name lives on its menu
                app["menu"] = [
                    {"id": it.id, "label": it.label, **({"type": it.type} if it.type else {}), **({"target": it.target} if it.target else {})}
                    for it in menu.items
                ]
            by_id = screens.get(name) or {}
            if by_id:
                app["screens"] = [{"id": sid, "label": s.label or s.description or sid} for sid, s in by_id.items()]
            apps.append(app)
        out: dict[str, Any] = {"apps": apps}
        dashboards = getattr(st, "dashboards", None)
        if dashboards is not None and hasattr(dashboards, "iter_dashboards"):
            out["dashboards"] = [
                {"id": d.qualified_id, "label": getattr(d, "label", None) or did}
                for scope, did, d in dashboards.iter_dashboards() if _visible(scope)
            ]
        explorer = getattr(st, "explorer", None)
        if explorer is not None and hasattr(explorer, "items"):
            out["explorer_models"] = [
                {"app": app, "id": mid, "label": m.label or mid, "entities": sorted(m.entities)}
                for app, mid, m in explorer.items() if _visible(app)
            ]
        jobs = getattr(st, "jobs", None)
        registry = getattr(jobs, "registry", None)
        if registry is not None:
            out["jobs"] = [
                {"id": j.id, **({"description": j.description} if j.description else {}),
                 "schedule": j.schedule, "enabled": j.enabled, "steps": [s.name for s in j.steps]}
                for j in registry.jobs()
            ]
        cfg = _ai_config()
        if cfg is not None:
            out["playbooks"] = [
                {"app": app, "id": pid, "title": pb.title}
                for app, pid, pb in cfg.playbooks() if _visible(app)
            ]
        return out

    def list_playbooks() -> list[dict[str, Any]]:
        """List the playbooks — step-by-step procedures the applications ship for their
        operators (how to create a user, how to duplicate a role…). Call it when the user
        asks HOW to do something in an application; then load the one that fits.
        """
        cfg = _ai_config()
        if cfg is None:
            return []
        return [
            {"app": app, "id": pid, "title": pb.title, **({"description": pb.description} if pb.description else {})}
            for app, pid, pb in cfg.playbooks() if _visible(app)
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
        if pb is None or not _visible(app):
            raise KeyError(f"no playbook {app}/{id} — call list_playbooks for the available ones")
        return {"app": app, "id": id, "title": pb.title, "body": pb.body}

    return [
        tool(describe_instance),
        tool(list_playbooks),
        tool(load_playbook, summary_keys=("app", "id")),
    ]
