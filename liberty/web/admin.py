"""``/admin`` routes — operational endpoints (superuser only).

* ``POST /admin/reload`` rebuilds the :class:`ConnectorRegistry` from
  ``connectors.toml`` on disk (and re-reads ``menus.toml``), swaps them into
  ``app.state`` (also rebuilding the auth backend — for the DB backend, against the
  new pool registry), then disposes the old registry. New connector definitions, edited queries and
  menu changes take effect immediately for subsequent requests. The AI assistant's
  connector tools still reference the previous registry until the app restarts;
  in-flight requests keep using whichever registry they started with.
* ``GET /admin/config/connectors`` returns the raw ``connectors.toml`` text.
* ``PUT /admin/config/connectors`` validates the submitted TOML (parsed against
  the connector schema) and, only if it's valid, writes it back to disk. It does
  *not* reload — call ``POST /admin/reload`` afterwards to apply.
* ``GET /admin/config/schema`` returns the JSON Schema of the structured-config
  models (currently ``pool``) — the config-builder UI renders its forms from it.
* ``GET/PUT /admin/config/pools`` — the structured ``[pools.*]`` view: GET returns
  ``{name: PoolConfig dict}``; PUT validates each against ``PoolConfig`` and surgically
  rewrites only the ``[pools.*]`` tables in ``connectors.toml`` (comments/formatting of the
  rest preserved, via ``tomlkit``). PUT does *not* reload — call ``POST /admin/reload`` after.
  (First slice of the Phase-7 config builders — the same shape will grow to connectors, queries, …)
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Any

import tomlkit
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from liberty.auth.authstore import build_auth_backend
from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.connectors import load_connectors
from liberty.connectors.config import PoolConfig, load_connectors_file, parse_connectors
from liberty.licensing import verify_license
from liberty.menus import load_menus

router = APIRouter(prefix="/admin", tags=["admin"])

Superuser = Annotated[Principal, Depends(require_superuser)]


@router.post("/reload")
async def reload_connectors(request: Request, _: Superuser) -> dict[str, object]:
    settings = request.app.state.settings
    old = request.app.state.connectors
    license_result = verify_license(settings.license.key)
    new = load_connectors(
        settings.connectors.config_path,
        dictionary_path=settings.connectors.dictionary_path,
        master_key=settings.crypto.master_key,
        license=license_result,
    )
    request.app.state.license = license_result
    request.app.state.connectors = new
    request.app.state.menus = load_menus(settings.menus.config_path)
    request.app.state.auth_backend = build_auth_backend(settings, new.pools)
    await old.aclose()
    return {
        "reloaded": True,
        "connectors": new.names(),
        "pools": new.pools.names(),
        "dictionary_entries": len(new.dictionary.entries),
        "menu_apps": list(request.app.state.menus.menus),
        "license_mode": license_result.mode,
    }


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


# ── structured config: the Phase-7 builders (pools first) ──────────────────────────────────
@router.get("/config/schema")
async def get_config_schema(_: Superuser) -> dict[str, Any]:
    """JSON Schema of the structured-config models — the builder UI renders forms from it."""
    return {"pool": PoolConfig.model_json_schema()}


@router.get("/config/pools")
async def get_pools_config(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``[pools.*]`` as ``{name: PoolConfig dict}`` (a missing file → no pools)."""
    path = Path(request.app.state.settings.connectors.config_path)
    cfg = load_connectors_file(path)
    return {"path": str(path), "pools": {name: p.model_dump() for name, p in cfg.pools.items()}}


class PoolsBody(BaseModel):
    pools: dict[str, dict[str, Any]]


@router.put("/config/pools")
async def put_pools_config(body: PoolsBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate each pool against :class:`PoolConfig`, then rewrite *only* the ``[pools.*]`` tables
    of ``connectors.toml`` (everything else — comments, the ``[connectors.*]`` tables, formatting —
    is left byte-for-byte intact via ``tomlkit``). Does not reload — call ``POST /admin/reload``."""
    # validate + normalise (drop default-valued keys so the file stays terse)
    new_pools: dict[str, dict[str, Any]] = {}
    for name, raw in body.pools.items():
        try:
            new_pools[name] = PoolConfig.model_validate(raw).model_dump(exclude_defaults=True)
        except ValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"pool {name!r}: {exc}") from exc

    path = Path(request.app.state.settings.connectors.config_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    pools = doc.get("pools")
    if pools is None:
        pools = tomlkit.table(is_super_table=True)
        doc["pools"] = pools
    for stale in [n for n in list(pools.keys()) if n not in new_pools]:
        del pools[stale]
    for name, vals in new_pools.items():
        if name in pools:                       # update in place — preserve any comments on the table
            existing = pools[name]
            for k in [k for k in list(existing.keys()) if k not in vals]:
                del existing[k]
            for k, v in vals.items():
                existing[k] = v
        else:
            pools[name] = vals                  # tomlkit renders a fresh [pools.<name>] table

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return {"saved": True, "path": str(path)}
