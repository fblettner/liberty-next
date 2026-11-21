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


def _nested(mapping: Any) -> int:
    """How many items a ``{scope: {id: item}}`` mapping holds."""
    return sum(len(v or {}) for v in (mapping or {}).values()) if isinstance(mapping, dict) else 0


@router.get("/studio/overview", summary="What the installation is built of")
async def studio_overview(request: Request, _: Superuser) -> dict[str, Any]:
    """The studio's home: how much of each building block the loaded configuration holds, what the
    integrity check finds, and the last changes — one call, read from the application state."""
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
    versions = getattr(state, "config_versions", None)
    recent = []
    for v in (versions.list_versions() if versions is not None else [])[:8]:
        recent.append({"file": v.rel_path, "who": v.who, "when": v.created_at, "source": v.source})
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
        "recent": recent,
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
