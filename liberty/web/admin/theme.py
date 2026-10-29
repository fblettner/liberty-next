"""``/admin`` — theme.toml (per-deployment branding) (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, _snapshot_config, router
from pydantic import BaseModel, ValidationError

from liberty.theme import font_choices, load_theme, parse_theme, preset_choices, resolve_theme

# ── theme.toml (per-deployment branding — see liberty/theme.py) ───────────────────────────────


@router.get("/config/theme/parsed", summary="Get theme config")
async def get_theme_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``theme.toml`` ``[theme]`` table + the built-in preset choices + the resolved
    CSS vars (so the editor can show a live preview without re-deriving the palette client-side).
    A missing file → the built-in default."""
    path = Path(request.app.state.settings.theme.config_path)
    cfg = load_theme(path).theme
    return {
        "path": str(path),
        "theme": cfg.model_dump(exclude_none=True),
        "presets": preset_choices(),
        "fonts": font_choices(),
        "resolved": resolve_theme(cfg),
    }


class ThemeBody(BaseModel):
    theme: dict[str, Any]


@router.put("/config/theme/parsed", summary="Update theme config")
async def put_theme_parsed(body: ThemeBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate the submitted ``[theme]`` table against :class:`ThemeFile`, then rewrite
    ``theme.toml`` via ``tomli-w``. No reload needed — the frontend re-fetches ``GET /api/theme``
    after a save (theme isn't part of the connector registry). Returns the resolved vars so the
    caller can apply them immediately."""
    try:
        validated = parse_theme({"theme": body.theme})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid theme: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid theme: {exc}") from exc

    normalized = validated.model_dump(exclude_none=True)
    import tomli_w
    new_text = tomli_w.dumps(normalized)
    try:
        parse_theme(tomllib.loads(new_text))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting theme is invalid: {exc}") from exc

    path = Path(request.app.state.settings.theme.config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path), "resolved": resolve_theme(validated.theme)}
