"""``theme.toml`` — per-deployment branding (the operator's colours + app name).

The UI is themed entirely through CSS custom properties (``--blue-main``, ``--bg-base``, …)
defined in ``frontend/src/index.css`` (a dark default + a ``.theme-light`` override). This module
lets an operator brand the whole install: pick a built-in **preset** (a curated accent palette),
set a **primary colour** (the dominant accent — buttons, active states, links), and a custom
**app name**. The resolved set of CSS-variable overrides is served by ``GET /api/theme`` and the
frontend applies them on top of the base stylesheet at load — so dark/light still toggles per
user, but the brand accent is the operator's.

Storage mirrors the other per-section configs (``charts.toml`` / ``dashboards.toml``): a tiny
``[theme]`` table in ``theme.toml`` under ``LIBERTY_APPS_DIR``. A missing file → the built-in
default (Liberty blue), so an un-branded install looks exactly as it did before this landed.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── built-in presets ─────────────────────────────────────────────────────────────────────
# Each preset is a curated map of CSS-variable overrides (no ``--`` prefix — added on the wire).
# They override the *primary* accent family (the ``blue-*`` group the whole UI uses for its
# main accent) so a preset re-skins the dominant surfaces without touching the dark/light
# bg/text tuning. ``default`` is empty → the index.css values (Liberty blue) show through.
PRESETS: dict[str, dict[str, Any]] = {
    "default": {"label": "Liberty (blue)", "primary": "#007AFF"},
    "ocean":   {"label": "Ocean (cyan)",   "primary": "#0EA5E9"},
    "violet":  {"label": "Violet",         "primary": "#7C3AED"},
    "emerald": {"label": "Emerald",        "primary": "#10B981"},
    "crimson": {"label": "Crimson",        "primary": "#E11D48"},
    "slate":   {"label": "Slate (graphite)", "primary": "#475569"},
}
DEFAULT_PRESET = "default"


# ── font choices ─────────────────────────────────────────────────────────────────────────
# Curated UI-font families for the Theme editor. Each maps an id → a label + a full CSS
# font-family stack (the first family is the intended one, the rest are graceful fallbacks).
# ``dm-sans`` is the built-in default (loaded in index.html); the other web fonts are loaded
# from the same Google Fonts link, and ``system`` / ``georgia`` are always present natively —
# so every choice renders without an extra round-trip. The frontend's ``fonts.sans`` token is
# ``var(--font-sans, <dm-sans stack>)``, so emitting ``--font-sans`` re-skins the whole UI.
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
FONT_CHOICES: dict[str, dict[str, str]] = {
    "dm-sans":     {"label": "DM Sans (default)", "stack": f"'DM Sans', {SANS}"},
    "inter":       {"label": "Inter",             "stack": f"'Inter', {SANS}"},
    "roboto":      {"label": "Roboto",            "stack": f"'Roboto', {SANS}"},
    "source-sans": {"label": "Source Sans 3",     "stack": f"'Source Sans 3', {SANS}"},
    "system":      {"label": "System",            "stack": SANS},
    "georgia":     {"label": "Georgia (serif)",   "stack": "Georgia, 'Times New Roman', Times, serif"},
}
DEFAULT_FONT = "dm-sans"


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Parse ``#rgb`` / ``#rrggbb`` → (r, g, b). Raises ``ValueError`` on anything else."""
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"not a hex colour: {value!r}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _primary_vars(primary_hex: str) -> dict[str, str]:
    """Derive the ``blue-*`` accent family from a single primary hex — same alpha ratios the
    index.css dark palette uses (main / 0.15 bg / 0.25 hover / 0.35 border). The ``-bg`` /
    ``-border`` variants are rgba so they sit nicely over either the dark or light surface."""
    r, g, b = _hex_to_rgb(primary_hex)
    rgba = lambda a: f"rgba({r},{g},{b},{a})"  # noqa: E731 — local shorthand
    return {
        "blue-main": primary_hex,
        "blue-bg": rgba(0.15),
        "blue-bg-hover": rgba(0.25),
        "blue-border": rgba(0.35),
        "shadow-focus": f"0 0 0 3px {rgba(0.18)}",
    }


class ThemeConfig(BaseModel):
    """The ``[theme]`` table. Everything optional — an empty file is the Liberty default."""

    model_config = ConfigDict(extra="forbid")

    preset: str = Field(default=DEFAULT_PRESET, description="Built-in palette id (see PRESETS). Unknown → default.")
    app_name: str | None = Field(default=None, description="Override the app name shown in the title / login.")
    primary_color: str | None = Field(
        default=None,
        description="Hex primary/accent colour. Wins over the preset's primary; derives the blue-* family.",
    )
    font_family: str | None = Field(
        default=None,
        description="UI font family id (see FONT_CHOICES). Unknown / unset → the built-in default (DM Sans).",
    )
    font_scale: float | None = Field(
        default=None, ge=0.75, le=1.5,
        description="Global text-size multiplier (1.0 = default). Scales every UI font size proportionally.",
    )
    vars: dict[str, str] = Field(
        default_factory=dict,
        description="Raw CSS-variable overrides (key without `--`), applied last. Escape hatch for power users.",
    )


class ThemeFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: ThemeConfig = Field(default_factory=ThemeConfig)


def parse_theme(data: dict[str, Any]) -> ThemeFile:
    return ThemeFile.model_validate(data or {})


def load_theme(path: Path | str) -> ThemeFile:
    """Load ``theme.toml``; a missing file yields the built-in default (an un-branded install)."""
    path = Path(path)
    if not path.exists():
        return ThemeFile()
    with path.open("rb") as fh:
        return parse_theme(tomllib.load(fh))


def resolve_theme(cfg: ThemeConfig) -> dict[str, Any]:
    """Flatten a :class:`ThemeConfig` into what the frontend applies: the chosen preset's primary
    (unless ``primary_color`` overrides it) expanded to the ``blue-*`` family, then any raw
    ``vars`` on top. Returns ``{preset, app_name, vars}`` — ``vars`` keyed *without* the ``--``."""
    preset = cfg.preset if cfg.preset in PRESETS else DEFAULT_PRESET
    primary = cfg.primary_color or PRESETS[preset]["primary"]
    out: dict[str, str] = {}
    try:
        out.update(_primary_vars(primary))
    except ValueError:
        # Bad hex → fall back to the preset's known-good primary rather than emit nothing.
        out.update(_primary_vars(PRESETS[preset]["primary"]))
    # Font family — emit --font-sans (the whole UI reads it via the fonts.sans token). An unknown
    # id is ignored so the frontend's built-in default shows through.
    if cfg.font_family and cfg.font_family in FONT_CHOICES:
        out["font-sans"] = FONT_CHOICES[cfg.font_family]["stack"]
    # Text-size multiplier — emit --font-scale only when it actually deviates from 1.0 (every
    # fontSize token is calc(var(--font-scale, 1) * Npx), so omitting it leaves the default).
    if cfg.font_scale is not None and abs(cfg.font_scale - 1.0) > 1e-6:
        out["font-scale"] = f"{cfg.font_scale:g}"
    out.update(cfg.vars)  # raw overrides win
    return {"preset": preset, "app_name": cfg.app_name, "vars": out}


def preset_choices() -> list[dict[str, str]]:
    """``[{id, label, primary}]`` for the editor's preset dropdown — ``primary`` lets the editor
    preview a preset client-side (derive the same accent family) without a server round-trip."""
    return [{"id": pid, "label": str(p["label"]), "primary": str(p["primary"])} for pid, p in PRESETS.items()]


def font_choices() -> list[dict[str, str]]:
    """``[{id, label, stack}]`` for the editor's font dropdown — ``stack`` lets the editor preview a
    font client-side (set ``--font-sans`` directly) without a server round-trip."""
    return [{"id": fid, "label": f["label"], "stack": f["stack"]} for fid, f in FONT_CHOICES.items()]
