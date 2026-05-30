"""``GET /api/theme`` — the resolved per-deployment branding, for the frontend to apply.

Public (no auth): it's just colours + an app name, needed before login so the sign-in page is
branded too. The structured editor (superuser) lives under ``/admin/config/theme/parsed`` in
:mod:`liberty.web.admin`. Theme changes don't touch the connector registry, so there's no
``/admin/reload`` step — the frontend just re-fetches ``/api/theme`` after a save.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from liberty.theme import font_choices, load_theme, preset_choices, resolve_theme

router = APIRouter(prefix="/api", tags=["theme"])


def _theme_path(request: Request):
    return request.app.state.settings.theme.config_path


@router.get(
    "/theme",
    summary="Get theme",
    responses={
        200: {
            "description": (
                "Returns ``{preset, app_name, vars, presets, fonts}``. ``vars`` is a flat map of "
                "CSS custom-property names (no ``--`` prefix) → values that the SPA applies to the "
                "document root; ``presets`` is the catalogue of named themes the operator can pick "
                "from; ``fonts`` is the curated font catalogue."
            )
        },
    },
)
async def get_theme(request: Request) -> dict[str, Any]:
    """The resolved theme — ``{preset, app_name, vars, presets}``. ``vars`` is a flat map of CSS
    custom properties (keys without the ``--`` prefix) the frontend sets on the document root.

    **Public** — no auth header required. The sign-in page calls this on first render so the
    page is branded BEFORE the user has a token. Edited through **Settings → Theme**; the SPA
    re-fetches this endpoint right after a save so changes appear without a reload."""
    cfg = load_theme(_theme_path(request)).theme
    resolved = resolve_theme(cfg)
    resolved["presets"] = preset_choices()
    resolved["fonts"] = font_choices()
    return resolved
