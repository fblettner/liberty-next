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
    page is branded BEFORE the user has a token. Edited through **Liberty Next → Administer → Theme**; the SPA
    re-fetches this endpoint right after a save so changes appear without a reload."""
    cfg = load_theme(_theme_path(request)).theme
    resolved = resolve_theme(cfg)
    resolved["presets"] = preset_choices()
    resolved["fonts"] = font_choices()
    # UI defaults ([app] in app.toml) — public like the rest: a period preset and two sizes.
    s = request.app.state.settings
    resolved["ui"] = {
        "default_period": s.app.default_period,
        "default_page_size": s.app.default_page_size,
        "dashboard_cache_seconds": s.app.dashboard_cache_seconds,
        "tabs_multi": s.app.tabs_multi,
        "tabs_close_on_app_switch": s.app.tabs_close_on_app_switch,
        "tabs_max": s.app.tabs_max,
        "drill_inline": s.app.drill_inline,
    }
    return resolved


# The installable web app's manifest, named after the instance (the theme's app name, else
# ``[app] name``) — public like the theme, so a browser installs it from the sign-in page.
manifest_router = APIRouter(tags=["theme"])


@manifest_router.get("/manifest.webmanifest", summary="Web app manifest", include_in_schema=False)
async def web_manifest(request: Request):
    from fastapi.responses import JSONResponse
    cfg = load_theme(_theme_path(request)).theme
    resolved = resolve_theme(cfg)
    name = resolved.get("app_name") or request.app.state.settings.app.name
    accent = resolved.get("vars", {}).get("blue-main") or "#007AFF"
    return JSONResponse({
        # Relative to the manifest's own URL, so they hold under a sub-path ([app] base_path) too.
        "id": "./", "name": name, "short_name": name[:12], "description": "Liberty Next",
        "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#0B0D12", "theme_color": accent if str(accent).startswith("#") else "#007AFF",
        # PNGs (the browsers' install check wants 192 and 512 px), the maskable one for Android's
        # shaped launchers, the SVG for the rest — all in frontend/public
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            {"src": "favicon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
        ],
    }, media_type="application/manifest+json")
