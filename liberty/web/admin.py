"""``/admin`` routes — operational endpoints (superuser only).

* ``POST /admin/reload`` rebuilds the :class:`ConnectorRegistry` from
  ``connectors.toml`` on disk and swaps it into ``app.state`` (also re-pointing the
  auth database at the new pool registry), then disposes the old registry. New
  connector definitions and edited queries take effect immediately for subsequent
  requests. The AI assistant's connector tools still reference the previous
  registry until the app restarts; in-flight requests keep using whichever
  registry they started with.
* ``GET /admin/config/connectors`` returns the raw ``connectors.toml`` text.
* ``PUT /admin/config/connectors`` validates the submitted TOML (parsed against
  the connector schema) and, only if it's valid, writes it back to disk. It does
  *not* reload — call ``POST /admin/reload`` afterwards to apply.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from liberty.auth.db import AuthDatabase
from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.connectors import load_connectors
from liberty.connectors.config import parse_connectors

router = APIRouter(prefix="/admin", tags=["admin"])

Superuser = Annotated[Principal, Depends(require_superuser)]


@router.post("/reload")
async def reload_connectors(request: Request, _: Superuser) -> dict[str, object]:
    settings = request.app.state.settings
    old = request.app.state.connectors
    new = load_connectors(settings.connectors.config_path)
    request.app.state.connectors = new
    request.app.state.auth_db = AuthDatabase(new.pools, settings.auth.pool)
    await old.aclose()
    return {"reloaded": True, "connectors": new.names(), "pools": new.pools.names()}


@router.get("/config/connectors")
async def get_connectors_config(request: Request, _: Superuser) -> dict[str, str]:
    path = Path(request.app.state.settings.connectors.config_path)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return {"path": str(path), "content": content}


class ConfigBody(BaseModel):
    content: str


@router.put("/config/connectors")
async def put_connectors_config(body: ConfigBody, request: Request, _: Superuser) -> dict[str, object]:
    # Validate before writing — a syntactically or schema-invalid file must not land on disk.
    try:
        parsed = tomllib.loads(body.content)
    except tomllib.TOMLDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid TOML: {exc}") from exc
    try:
        parse_connectors(parsed)
    except Exception as exc:  # pydantic ValidationError or similar
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid connector config: {exc}") from exc
    path = Path(request.app.state.settings.connectors.config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content, encoding="utf-8")
    return {"saved": True, "path": str(path)}
