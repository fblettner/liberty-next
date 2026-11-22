"""The studio: shared actions seen as SERVICES — steps composed in the action designer, run on
their own and exposed as API endpoints (``EndpointDef.action``). Three routes behind the page:
what exists, a run with inputs, and "expose as an endpoint"."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field

from liberty.connectors.config import load_connectors_file
from liberty.web.admin._base import Superuser, router
from liberty.web.admin.builders import ConnectorsParsedBody, put_connectors_parsed
from liberty.web.admin.bundles import reload_connectors

#: the connector a service is exposed on when the installation has no API connector of its own
STUDIO_CONNECTOR = "studio"


def _served(connectors: Any) -> dict[str, list[dict[str, Any]]]:
    """``{action id: [the endpoints it serves]}``."""
    out: dict[str, list[dict[str, Any]]] = {}
    for name in (connectors.names() if connectors is not None else []):
        for ep in getattr(getattr(connectors.get(name), "config", None), "endpoints", None) or []:
            if getattr(ep, "action", None):
                out.setdefault(ep.action, []).append({"connector": name, "endpoint": ep.name, "label": ep.label, "writable": bool(ep.writable)})
    return out


@router.get("/studio/services", summary="List the studio's services")
async def studio_services(request: Request, _: Superuser) -> dict[str, Any]:
    """Every shared action with its inputs and the endpoints it serves, and the API connectors a
    service can be exposed on."""
    from liberty.screens.actions import shared_inputs

    state = request.app.state
    connectors = getattr(state, "connectors", None)
    served = _served(connectors)
    actions = getattr(getattr(state, "actions", None), "actions", None) or {}
    api_connectors = [n for n in (connectors.names() if connectors is not None else [])
                      if getattr(getattr(connectors.get(n), "config", None), "type", None) == "api"]
    return {
        "services": [{"id": aid, "label": a.label, "description": a.description, "steps": len(a.steps or []),
                      "inputs": shared_inputs(a), "endpoints": served.get(aid, [])} for aid, a in sorted(actions.items())],
        "connectors": api_connectors, "default_connector": STUDIO_CONNECTOR,
    }


def _tests_totals(state: Any) -> dict[str, Any] | None:
    try:
        from liberty.apptests import store

        return store.totals(state.settings)
    except Exception:  # noqa: BLE001 — the overview opens without them
        return None


def _version() -> str | None:
    try:
        from importlib.metadata import version

        return version("liberty-next")
    except Exception:  # noqa: BLE001 — a source checkout without metadata
        return None


async def _app_summaries(request: Request, principal: Any) -> list[dict[str, Any]]:
    """Each application at a glance — from its map: how much it holds and what is loose in it."""
    first = await studio_map(request, principal, app=None)
    out = []
    for name in first["apps"]:
        m = first if first["app"] == name else await studio_map(request, principal, app=name)
        out.append({
            "app": name, "screens": len(m["screens"]), "forms": sum(1 for s in m["screens"] if s["form"]),
            "menu": sum(1 for i in m["menu"] if i["type"]), "data": sum(1 for d in m["data"] if d["used"]),
            "services": len(m["services"]), "dashboards": len(m["dashboards"]),
            "unreached": sum(1 for s in m["screens"] if not s["reached"]), "unused": sum(1 for d in m["data"] if not d["used"]),
            "missing": sum(1 for d in m["data"] if d["missing"]) + sum(1 for v in m["services"] if v["missing"]),
        })
    return out


def _nested(mapping: Any) -> int:
    """How many items a ``{scope: {id: item}}`` mapping holds."""
    return sum(len(v or {}) for v in (mapping or {}).values()) if isinstance(mapping, dict) else 0


@router.get("/studio/overview", summary="What the installation is built of")
async def studio_overview(request: Request, _: Superuser) -> dict[str, Any]:
    """The studio's home: how much of each building block the loaded configuration holds, what the
    integrity check finds — one call, read from the application state."""
    from liberty.web.integrity import check_integrity

    state = request.app.state
    connectors = getattr(state, "connectors", None)
    sql = api = queries = endpoints = 0
    for name in (connectors.names() if connectors is not None else []):
        cfg = getattr(connectors.get(name), "config", None)
        if getattr(cfg, "type", None) == "api":
            api += 1
            endpoints += len(getattr(cfg, "endpoints", None) or [])
        else:
            sql += 1
            queries += len(getattr(cfg, "queries", None) or [])
            queries += sum(1 for t in getattr(cfg, "tables", None) or [] for slot in ("get", "put", "post", "delete") if getattr(t, slot, None))
    dictionary = getattr(connectors, "dictionary", None)
    entries = len(getattr(dictionary, "entries", None) or {}) + sum(
        len(getattr(section, "entries", None) or {}) for section in (getattr(dictionary, "connectors", None) or {}).values())
    screens = getattr(getattr(state, "screens", None), "screens", None) or {}
    forms = sum(1 for smap in screens.values() for sc in (smap or {}).values() if getattr(sc, "dialog", None) is not None)
    menus = getattr(getattr(state, "menus", None), "menus", None) or {}
    actions = getattr(getattr(state, "actions", None), "actions", None) or {}
    registry = getattr(getattr(state, "jobs", None), "registry", None)
    jobs = getattr(registry, "jobs", None)
    jobs = jobs() if callable(jobs) else (jobs or [])
    try:
        issues = check_integrity(state)
    except Exception:  # noqa: BLE001 — the home must open even when the check cannot run
        issues = []
    return {
        "counts": {
            "connectors": sql + api, "sql_connectors": sql, "api_connectors": api, "queries": queries, "endpoints": endpoints,
            "dictionary": entries, "screens": _nested(screens), "forms": forms,
            "menus": len(menus), "menu_items": sum(len(getattr(m, "items", None) or []) for m in menus.values()),
            "services": len(actions), "exposed": sum(1 for ref in _served(connectors) if ref in actions),
            "charts": _nested(getattr(getattr(state, "charts", None), "charts", None)),
            "dashboards": _nested(getattr(getattr(state, "dashboards", None), "dashboards", None)),
            "explorer": _nested(getattr(getattr(state, "explorer", None), "explorer", None)),
            "jobs": len(jobs), "notifications": len(getattr(getattr(state, "notification_rules", None), "notifications", None) or {}),
        },
        "integrity": {"errors": sum(1 for i in issues if i.severity == "error"), "warnings": sum(1 for i in issues if i.severity == "warning")},
        "apps": await _app_summaries(request, _),
        "tests": _tests_totals(state),
        "version": _version(),
    }


def _steps(actions: Any) -> Any:
    """Every action of a list, nested ones included (chain / loop steps, both branches of an if)."""
    for a in actions or []:
        yield a
        for field in ("steps", "then_steps", "else_steps"):
            yield from _steps(getattr(a, field, None))


def _screen_actions(screen: Any) -> Any:
    for hook in ("actions", "row_menu", "on_insert", "on_update", "on_delete", "on_duplicate"):
        yield from _steps(getattr(screen, hook, None))
    dialog = getattr(screen, "dialog", None)
    for hook in ("on_load", "on_save", "on_cancel"):
        yield from _steps(getattr(dialog, hook, None))
    for tab in getattr(dialog, "tabs", None) or []:
        yield from _steps(getattr(tab, "actions", None))


@router.get("/studio/map", summary="The map of an application")
async def studio_map(request: Request, _: Superuser, app: str | None = None) -> dict[str, Any]:
    """One application as a whole: its menu, its screens, the queries and endpoints each screen
    reads, saves through and calls, the services its screens call, its dashboards — and what
    nothing uses (a screen out of the menu, a query no screen reads). The links are in the items
    themselves (a screen names its data and its services); the page draws them."""
    state = request.app.state
    connectors = getattr(state, "connectors", None)
    all_screens = getattr(getattr(state, "screens", None), "screens", None) or {}
    menus = getattr(getattr(state, "menus", None), "menus", None) or {}
    dashboards = getattr(getattr(state, "dashboards", None), "dashboards", None) or {}
    actions = getattr(getattr(state, "actions", None), "actions", None) or {}
    apps = sorted(set(all_screens) | set(menus))
    if not app or app not in apps:
        app = apps[0] if apps else None
    if app is None:
        return {"apps": [], "app": None, "menu": [], "screens": [], "data": [], "services": [], "dashboards": []}
    names = set(connectors.names()) if connectors is not None else set()

    def config(name: str) -> Any:
        return getattr(connectors.get(name), "config", None) if name in names else None

    def kind(connector: str) -> str:
        return "endpoint" if getattr(config(connector), "type", None) == "api" else "query"

    data: dict[str, dict[str, Any]] = {}

    def use(connector: str | None, name: str | None) -> str | None:
        if not connector or not name:
            return None
        key = f"{connector}.{name}"
        data.setdefault(key, {"key": key, "connector": connector, "name": name, "kind": kind(connector), "used": True})
        return key

    # ── the menu, in display order with its depth ──────────────────────────────────────────
    items = list(getattr(menus.get(app), "items", None) or [])
    by_parent: dict[str | None, list[Any]] = {}
    for it in items:
        by_parent.setdefault(it.parent, []).append(it)
    menu: list[dict[str, Any]] = []

    def walk(parent: str | None, depth: int) -> None:
        for it in by_parent.get(parent, []):
            menu.append({"id": it.id, "label": it.label, "depth": depth, "type": it.type, "target": it.target,
                         "connector": it.connector})
            walk(it.id, depth + 1)
    walk(None, 0)
    listed = {it.id for it in items}
    for it in items:                                   # an entry whose parent is gone: still shown
        if it.parent is not None and it.parent not in listed:
            menu.append({"id": it.id, "label": it.label, "depth": 0, "type": it.type, "target": it.target, "connector": it.connector})
    in_menu = {m["target"] for m in menu if m["type"] == "screen" and m["target"]}
    menu_dashboards = {m["target"] for m in menu if m["type"] == "dashboard" and m["target"]}

    # ── the screens, each with what it uses ──────────────────────────────────────────────
    screens: list[dict[str, Any]] = []
    called: dict[str, list[str]] = {}
    nested_in: set[str] = set()
    for sid, sc in sorted((all_screens.get(app) or {}).items()):
        reads_on = sc.connector or app
        saves_on = getattr(sc, "save_connector", None) or reads_on
        read = use(reads_on, sc.read_query)
        writes = [k for k in (use(saves_on, getattr(sc, slot, None)) for slot in ("insert_query", "update_query", "delete_query")) if k]
        calls: list[str] = []
        services: list[str] = []
        for a in _screen_actions(sc):
            if a.type == "run_query":
                calls.append(use(a.connector or reads_on, a.query) or "")
            elif a.type == "call_api":
                calls.append(use(a.connector, a.endpoint) or "")
            elif a.type == "call_action" and a.ref:
                services.append(a.ref)
                called.setdefault(a.ref, []).append(sid)
        nested = [t.screen if getattr(t, "type", None) == "nested_table" else t.form_screen
                  for t in getattr(getattr(sc, "dialog", None), "tabs", None) or [] if getattr(t, "type", None) in ("nested_table", "nested_form")]
        nested_in.update(n for n in nested if n)
        screens.append({"id": sid, "label": sc.label or sid, "connector": reads_on, "read": read, "writes": list(dict.fromkeys(writes)),
                        "calls": list(dict.fromkeys(c for c in calls if c)), "services": list(dict.fromkeys(services)),
                        "nested": [n for n in dict.fromkeys(nested) if n], "form": getattr(sc, "dialog", None) is not None,
                        "read_only": bool(getattr(sc, "read_only", False)), "in_menu": sid in in_menu})
    # reached = what Integrity counts (a menu entry, another screen's form, row click or navigate action, an
    # Explorer entity): one rule, so "nothing opens" here and "orphan screen" there never disagree
    from liberty.web.integrity import reached_screens

    opened = {sid for a, sid in reached_screens(state) if a == app}
    for sc in screens:
        sc["reached"] = sc["in_menu"] or sc["id"] in nested_in or sc["id"] in opened

    # ── the application's own data nothing here reads (its connector's queries / endpoints) ──
    own = config(app)
    for q in getattr(own, "queries", None) or []:
        data.setdefault(f"{app}.{q.name}", {"key": f"{app}.{q.name}", "connector": app, "name": q.name, "kind": "query", "used": False})
    for t in getattr(own, "tables", None) or []:
        for slot in ("get", "put", "post", "delete"):
            if getattr(t, slot, None):
                name = f"{t.name}_{slot}"
                data.setdefault(f"{app}.{name}", {"key": f"{app}.{name}", "connector": app, "name": name, "kind": "query", "used": False})
    for e in getattr(own, "endpoints", None) or []:
        data.setdefault(f"{app}.{e.name}", {"key": f"{app}.{e.name}", "connector": app, "name": e.name, "kind": "endpoint", "used": False})
    described: dict[str, dict[str, tuple[Any, bool, Any]]] = {}

    def describe(connector: str) -> dict[str, tuple[Any, bool, Any]]:
        """``{name: (label, writable, serving action)}`` of a connector's queries (a table's slots are
        ``<table>_get`` / ``_put`` / ``_post`` / ``_delete``, labelled as the table) and endpoints."""
        if connector not in described:
            cfg, out = config(connector), {}
            for x in [*(getattr(cfg, "queries", None) or []), *(getattr(cfg, "endpoints", None) or [])]:
                out[x.name] = (x.label, bool(x.writable), getattr(x, "action", None))
            for t in getattr(cfg, "tables", None) or []:
                for slot in ("get", "put", "post", "delete"):
                    part = getattr(t, slot, None)
                    if part is not None:
                        out[f"{t.name}_{slot}"] = (getattr(part, "label", None) or t.label, bool(getattr(part, "writable", False)), None)
            described[connector] = out
        return described[connector]

    for entry in data.values():                        # the label and what serves an endpoint, where the connector says
        label, writable, action = describe(entry["connector"]).get(entry["name"], (None, False, None))
        entry["label"], entry["writable"], entry["service"] = label, writable, action
        entry["missing"] = entry["name"] not in describe(entry["connector"])

    served = _served(connectors)
    services = [{"id": ref, "label": getattr(actions.get(ref), "label", None), "steps": len(getattr(actions.get(ref), "steps", None) or []),
                 "screens": sorted(set(sids)), "endpoints": served.get(ref, []), "missing": ref not in actions}
                for ref, sids in sorted(called.items())]
    return {
        "apps": apps, "app": app, "menu": menu, "screens": screens,
        "data": sorted(data.values(), key=lambda d: (not d["used"], d["key"])), "services": services,
        "dashboards": [{"id": did, "label": getattr(d, "label", None) or did, "in_menu": did in menu_dashboards or f"{app}.{did}" in menu_dashboards}
                       for did, d in sorted((dashboards.get(app) or {}).items())],
    }


class StudioRunBody(BaseModel):
    action: str
    inputs: dict[str, Any] = Field(default_factory=dict)


@router.post("/studio/run", summary="Run a service")
async def studio_run(body: StudioRunBody, request: Request, principal: Superuser) -> dict[str, Any]:
    """Run the shared action *as saved*, with *inputs*, for the signed-in administrator — the
    studio's "Run": the answer it would give as an endpoint (what it returns, its messages), the
    steps it ran and how long it took. A step that fails is reported, not raised."""
    from liberty.screens.actions import ActionInputError, run_shared_action

    state = request.app.state
    given = {k: v for k, v in body.inputs.items() if v not in (None, "")}
    started = time.perf_counter()
    try:
        result = await run_shared_action(state, state.connectors, body.action, given, principal, origin=f"studio {body.action}")
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no shared action {body.action!r} — save it first") from None
    except ActionInputError as exc:
        return {"ok": False, "error": str(exc), "duration_ms": 0.0}
    out = result.to_dict()
    out["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    return out


class StudioExposeBody(BaseModel):
    action: str
    connector: str = STUDIO_CONNECTOR
    name: str | None = None           # the endpoint's name; blank = the action's id
    label: str | None = None
    writable: bool = False


@router.post("/studio/expose", summary="Expose a service as an endpoint")
async def studio_expose(body: StudioExposeBody, request: Request, principal: Superuser) -> dict[str, Any]:
    """Create the endpoint that serves the shared action: on *connector* (an API connector — the
    ``studio`` connector is created when the installation has none), with the action's inputs as
    its parameters. 404 when the action is not defined, 409 when the endpoint's name is taken."""
    from liberty.screens.actions import shared_inputs

    state = request.app.state
    shared = (getattr(getattr(state, "actions", None), "actions", None) or {}).get(body.action)
    if shared is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no shared action {body.action!r} — save it first")
    cfile = load_connectors_file(Path(state.settings.connectors.config_path))
    conns = {name: c.model_dump(exclude_defaults=True) for name, c in cfile.connectors.items()}
    connector = (body.connector or STUDIO_CONNECTOR).strip()
    if connector not in conns:
        if connector != STUDIO_CONNECTOR:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown connector {connector!r}")
        # nothing is called over HTTP on it: every endpoint is served by an action
        conns[connector] = {"type": "api", "base_url": "http://localhost", "show_in_switcher": False}
    target = conns[connector]
    if target.get("type") != "api":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{connector!r} is not an API connector: it takes no endpoint")
    name = (body.name or body.action).strip()
    existing = target.setdefault("endpoints", [])
    if any(isinstance(e, dict) and e.get("name") == name for e in existing):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"connector {connector!r} already has an endpoint {name!r}")
    params = [{"name": i["name"], **({"label": i["label"]} if i["label"] else {}), **({"required": True} if i["required"] else {})}
              for i in shared_inputs(shared)]
    existing.append({
        "name": name, "action": body.action, "label": body.label or shared.label or name,
        **({"description": shared.description} if shared.description else {}), "group": "Studio",
        **({"writable": True} if body.writable else {}), **({"params": params} if params else {}),
    })
    await put_connectors_parsed(ConnectorsParsedBody(connectors=conns), request, principal)
    await reload_connectors(request, principal)
    return {"connector": connector, "endpoint": name, "permission": f"api:{connector}:{name}", "path": f"/api/http/{connector}/{name}"}
