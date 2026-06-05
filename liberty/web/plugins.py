"""``/api/plugins`` — list + invoke server-side plugin callables.

The ``call_plugin`` screen action fires a ``module:function`` entry point inline — the SAME
callables nomaflow runs as ``python`` job steps, now reachable from a screen the way ``/api/sql``
makes a query reachable. Two routes:

* ``GET  /api/plugins/callables`` — the discovered ``j_*`` catalog, for the action editor's picker.
* ``POST /api/plugins/run`` — resolve + invoke ``{callable, params}`` and return the result.

Authorisation mirrors ``/api/sql``: any authenticated user, gated per-callable by the
``plugin:<module:function>`` permission (glob-aware — an admin grants ``plugin:*`` or
``plugin:nomajde.security:*``). The run route also refuses any callable not present under
``plugins/`` (the discovery set), so the ``module:function`` string can't import an arbitrary
module. Runs synchronously — long batch work should target a nomaflow job instead.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from liberty.auth.dependencies import CurrentPrincipal
from liberty.connectors import ConnectorRegistry
from liberty.jobs.discover import discover_callables
from liberty.jobs.steps.base import RunContext, StepResult
from liberty.jobs.triggers import ManualTrigger
from liberty.plugins.invoke import PluginInvocationError, invoke_callable
from liberty.web.deps import get_connectors, require_permission

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

Connectors = Annotated[ConnectorRegistry, Depends(get_connectors)]


def _plugins_root() -> Path:
    """``${LIBERTY_APPS_DIR}/../plugins`` (same resolution as ``ensure_plugins_on_sys_path`` and
    the Job Designer's callable catalog); ``./plugins`` when the env var is unset."""
    apps = os.environ.get("LIBERTY_APPS_DIR", "").strip()
    return Path(apps).parent / "plugins" if apps else Path("plugins")


@router.get("/callables", summary="List plugin callables")
async def list_plugin_callables(principal: CurrentPrincipal) -> dict[str, Any]:
    """The ``j_*`` callables discovered under ``plugins/`` — the catalog the call_plugin action's
    picker reads. Readable by any authenticated user (the picker only SUGGESTS; running one still
    needs the ``plugin:<callable>`` permission)."""
    root = _plugins_root()
    catalog = discover_callables(root)
    return {
        "plugins_root": str(root.resolve()) if root.is_dir() else None,
        "callables": [c.to_dict() for c in catalog],
    }


@router.post("/run", summary="Run a plugin callable")
async def run_plugin(
    request: Request,
    principal: CurrentPrincipal,
    connectors: Connectors,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Invoke ``{callable, params}`` synchronously and return its result.

    Response shape (read by the call_plugin action when ``bind_result``):
    ``{success, callable, rows_affected, extras, rows, first_row}`` — a dict-returning callable's
    value lands in ``extras`` / ``first_row`` so a later chain step can bind ``<step>.first_row.<k>``.
    """
    body = body or {}
    ref = body.get("callable")
    if not isinstance(ref, str) or ":" not in ref:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="`callable` must be 'module:function'"
        )
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="`params` must be an object")

    require_permission(principal, f"plugin:{ref}")

    # Refuse anything not discovered under plugins/ — bounds the callable to real plugin entry
    # points (the module:function string can't be used to import an arbitrary module).
    known = {c.callable for c in discover_callables(_plugins_root())}
    if ref not in known:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown plugin callable: {ref}")

    settings = getattr(request.app.state, "settings", None)
    ctx = RunContext(
        run_id=f"action:{principal.username}",
        job_id="__action__",
        trigger=ManualTrigger(triggered_by=principal.username),
    )
    try:
        result: StepResult = await invoke_callable(
            ref,
            dict(params),
            injections=(("connectors", connectors), ("ctx", ctx), ("settings", settings)),
        )
    except PluginInvocationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface any callable error as a 400 with its message
        _log.exception("plugin run failed: callable=%s user=%s", ref, principal.username)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{ref}: {exc}") from exc

    extras = result.extras or {}
    rows = [extras] if extras else []
    return {
        "success": True,
        "callable": ref,
        "rows_affected": result.rows_affected,
        "extras": extras,
        "rows": rows,
        "first_row": extras,
    }
