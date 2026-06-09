"""``/api/actions`` — the shared-action catalog the runtime resolves ``call_action`` against.

A screen's ``call_action`` names a shared action by id; the client-side action runner needs the
referenced action's ``steps`` + ``prompt_fields`` to inline + run them. This route serves that
catalog to any authenticated user (the definitions are config, not data — the per-step gates
[``sql:…`` / ``plugin:…`` / ``api:…``] still apply when a step actually runs).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from liberty.actions import SharedActionsFile
from liberty.auth.dependencies import CurrentPrincipal

router = APIRouter(prefix="/api", tags=["actions"])


def get_actions(request: Request) -> SharedActionsFile:
    return request.app.state.actions


Actions = Annotated[SharedActionsFile, Depends(get_actions)]


@router.get("/actions", summary="List shared actions")
async def list_actions(principal: CurrentPrincipal, actions: Actions) -> dict[str, Any]:
    """Every shared action (id / label / description / prompt_fields / steps) — the runtime inlines
    the matching one when a screen fires ``call_action``."""
    return {
        "actions": [
            a.model_dump(mode="json", exclude_none=True) for a in actions.actions.values()
        ],
    }


@router.get("/actions/{action_id}", summary="Get one shared action")
async def get_action(action_id: str, principal: CurrentPrincipal, actions: Actions) -> dict[str, Any]:
    action = actions.actions.get(action_id)
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown shared action: {action_id}")
    return action.model_dump(mode="json", exclude_none=True)
