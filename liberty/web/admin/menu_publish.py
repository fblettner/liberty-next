"""A saved Explorer analysis or dashboard published in a menu.

``POST /admin/menus/publish`` adds one entry to an application's menu: a ``page`` leaf whose target
opens the Explorer on that analysis (``/explorer/<app>/<model>/a/<id>``) or that dashboard
(``…/d/<id>``) — its result at once, the definition one click away. The object has to be **shared**:
a menu entry is for everybody who may use the model, and a private analysis would open empty for
them. The entry is shown to who holds ``explorer:<app>:<model>``
(:func:`liberty.web.menus.page_permission`). Written like any menu change: validated, the file
snapshotted, the configuration reloaded.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from liberty.menus import load_menus
from liberty.menus.config import MenusFile
from liberty.web.admin._base import Superuser, router
from liberty.web.admin.builders import MenusBody, put_menus_parsed
from liberty.web.admin.bundles import reload_connectors

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_KINDS = {"analysis": "a", "dashboard": "d"}


def explorer_target(app: str, model: str, kind: str, obj_id: str) -> str:
    """The route that opens the Explorer on a saved object."""
    return f"/explorer/{app}/{model}/{_KINDS[kind]}/{obj_id}"


class PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app: str = Field(description="The application of the Explorer model.")
    model: str
    kind: Literal["analysis", "dashboard"]
    id: str = Field(description="The saved object's id.")
    menu_app: str = Field(description="The application whose menu takes the entry (often the same).")
    label: str = Field(min_length=1, max_length=80)
    parent: str | None = Field(default=None, description="A folder of that menu; none = at the top.")
    icon: str | None = None


@router.post("/menus/publish", summary="Publish an analysis or a dashboard in a menu")
async def publish_to_menu(body: PublishBody, request: Request, principal: Superuser) -> dict[str, Any]:
    """Add the menu entry and reload. 404: no such model or saved object; 422: the object is private
    (share it first), or the folder does not exist; 409: the same target is already in that menu."""
    from liberty.explorer.store import KIND_ANALYSIS, KIND_DASHBOARD, AnalysisStore

    state = request.app.state
    explorer = getattr(state, "explorer", None)
    if explorer is None or explorer.get(body.app, body.model) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No explorer model {body.app}/{body.model}.")
    if not _ID.match(body.id) or not _ID.match(body.app) or not _ID.match(body.model):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The id is not one a menu target can carry.")
    views = getattr(state, "user_views", None)
    if views is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="The saved analyses are unavailable.")
    key, kind = f"{body.app}/{body.model}", (KIND_ANALYSIS if body.kind == "analysis" else KIND_DASHBOARD)
    # "" = nobody's own: only the shared objects are seen — what every user of the model will see
    if await AnalysisStore(views).get(key, kind, "", body.id) is None:
        mine = await AnalysisStore(views).get(key, kind, principal.username, body.id)
        if mine is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="It is private: share it first — a menu entry is for everybody who may use the model.")
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No saved {body.kind} {body.id!r} in {key}.")

    target = explorer_target(body.app, body.model, body.kind, body.id)
    mfile = load_menus(Path(state.settings.menus.config_path))
    menus = {app: am.model_dump(exclude_defaults=True) for app, am in mfile.menus.items()}
    items: list[dict[str, Any]] = menus.setdefault(body.menu_app, {}).setdefault("items", [])
    if any(it.get("type") == "page" and it.get("target") == target for it in items):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"The menu of {body.menu_app} already has an entry for it.")
    if body.parent and not any(it.get("id") == body.parent and not it.get("type") for it in items):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"No folder {body.parent!r} in the menu of {body.menu_app}.")
    taken = {str(it.get("id")) for it in items}
    base = re.sub(r"[^a-z0-9_]+", "_", f"explorer_{body.id}".lower()).strip("_")
    item_id, n = base, 2
    while item_id in taken:
        item_id, n = f"{base}_{n}", n + 1
    item: dict[str, Any] = {"id": item_id, "label": body.label.strip(), "type": "page", "target": target,
                            "icon": body.icon or ("layoutdashboard" if body.kind == "dashboard" else "compass")}
    if body.parent:
        item["parent"] = body.parent
    items.append(item)
    try:
        MenusFile.model_validate({"menus": menus})
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"The menu would not be valid: {exc}") from exc
    await put_menus_parsed(MenusBody(menus=menus), request, principal)
    await reload_connectors(request, principal)
    return {"menu_app": body.menu_app, "item": item}
