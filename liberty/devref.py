"""The developer reference (C12): what the running code is made of, read from the code itself — no
hand-kept list. The Developer page (``/admin/dev/*``) shows it.

Configuration (this module's first view): every key of :class:`liberty.config.Settings` (app.toml)
with its type, default and description, **where the UI edits it** (:data:`UI_EDITORS`, built from the
App editor's key sets plus the other screens that write app.toml) or **why it stays in the file**
(:data:`FILE_ONLY`), and **the code that reads it** — a scan of ``liberty/`` for
``.<section>.<key>`` and, in the modules that take the section object (``AISettings``…),
``.<key>``. A key that is neither editable nor explained is a gap ("every setting editable in the
UI", plan priority 1).
"""

from __future__ import annotations

import json
import re
import typing
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from liberty.config import Settings

_ROOT = Path(__file__).resolve().parent

# Screens other than Settings → App that write a key of app.toml.
_OTHER_EDITORS: dict[str, str] = {
    "app.log_levels": "Monitoring → Server log (levels per module)",
    "reports.branding": "Settings → Reports → Branding",
    "changesets.post_apply": "nomaflow → Changes → Post-apply steps",
    "promotion.targets": "Settings → Package → Promotion",
}

# Keys that stay in the file (or the environment) on purpose, with the reason shown on the page.
FILE_ONLY: dict[str, str] = {
    "crypto.master_key": "the key every ENC: value is encrypted with — set in the environment (LIBERTY_MASTER_KEY); changing it from the UI would make every stored secret unreadable",
    "auth.jwt_secret": "signs every session — set in the environment (LIBERTY_JWT_SECRET); changing it signs everyone out",
    "auth.jwt_algorithm": "HS256 is the only algorithm the token service supports",
}
# The deployment layout: where the configuration files are. Set by the installation (LIBERTY_APPS_DIR,
# the Docker volumes); pointing one at another place from a screen would show an empty configuration.
_LAYOUT = "deployment layout — follows LIBERTY_APPS_DIR / the install; changed in app.toml, then a restart"
for _key in ("connectors.config_path", "connectors.dictionary_path", "menus.config_path", "screens.config_path",
             "charts.config_path", "actions.config_path", "dashboards.config_path", "explorer.config_path",
             "theme.config_path", "ai.config_path", "jobs.config_path", "reports.templates_config_path",
             "notify.config_path", "auth.toml_path", "app.static_dir"):
    FILE_ONLY[_key] = _LAYOUT


def _app_editor_keys() -> dict[str, str]:
    from liberty.web import admin
    out: dict[str, str] = {}
    for section, keys in (("app", admin._APP_SAFE_KEYS), ("ai", admin._AI_SAFE_KEYS), ("license", admin._LICENSE_SAFE_KEYS),
                          ("oidc", admin._OIDC_SAFE_KEYS), ("notify", admin._NOTIFY_SAFE_KEYS),
                          ("auth", admin._AUTH_SAFE_KEYS), ("jobs", admin._JOBS_SAFE_KEYS),
                          ("changesets", admin._CHANGESETS_SAFE_KEYS)):
        for k in keys:
            out[f"{section}.{k}"] = "Settings → App"
    return out


def ui_editors() -> dict[str, str]:
    """``"section.key"`` → the screen that edits it."""
    return {**_app_editor_keys(), **_OTHER_EDITORS}


def _type_name(ann: Any) -> str:
    text = getattr(ann, "__name__", None) if isinstance(ann, type) else None
    return text or str(ann).replace("typing.", "").replace("pathlib.", "")


def _default(field: Any) -> Any:
    if field.default_factory is not None:
        try:
            value = field.default_factory()
        except Exception:  # noqa: BLE001 — a factory needing context: show nothing
            return None
    else:
        value = field.default
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _sections() -> list[tuple[str, type[BaseModel]]]:
    out = []
    for name, f in Settings.model_fields.items():
        ann = f.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            out.append((name, ann))
    return out


@lru_cache(maxsize=1)
def _sources() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Every module of the package as ``(relative path, lines)`` — read once per process."""
    files = []
    for p in sorted(_ROOT.rglob("*.py")):
        if "migrations" in p.parts or p.parent == _ROOT and p.name in ("config.py", "devref.py"):
            continue
        try:
            files.append((str(p.relative_to(_ROOT.parent)), tuple(p.read_text(encoding="utf-8").splitlines())))
        except (OSError, UnicodeDecodeError):
            continue
    return tuple(files)


_GENERIC = frozenset({"config_path", "max_tokens", "toml_path", "retention_days"})


@lru_cache(maxsize=1)
def _index() -> dict[str, list[tuple[int, int]]]:
    """Every ``.name`` / ``"name"`` of the package → its ``(file index, line)`` — one pass, so a key
    only looks at the lines that mention it. Comments and RST docstrings (``…``) are left out."""
    from collections import defaultdict
    tok = re.compile(r"\.(\w+)\b|[\"'](\w+)[\"']")
    out: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for fi, (_, lines) in enumerate(_sources()):
        for i, line in enumerate(lines, 1):
            if line.lstrip().startswith("#") or "``" in line:
                continue
            for a, b in tok.findall(line):
                hits = out[a or b]
                if not hits or hits[-1] != (fi, i):
                    hits.append((fi, i))
    return dict(out)


@lru_cache(maxsize=None)
def _mentions(fi: int, cls_name: str) -> bool:
    return any(cls_name in line for line in _sources()[fi][1])


def _readers(section: str, cls_name: str, key: str, limit: int = 40) -> list[dict[str, Any]]:
    # settings.app.log_file · getattr(settings.app, "log_file", …) · notify.enabled / notify_cfg.enabled
    strong = re.compile(rf"\.{section}\.{key}\b|getattr\([\w.]*\b{section}\s*,\s*[\"']{key}[\"']|\b{section}[\"'](?:,\s*None)?\)\s*,\s*[\"']{key}[\"']|\b{section}\w*\.{key}\b")
    weak = re.compile(rf"\.{key}\b")
    # A distinctive name (smtp_host, username_claim…) is the key wherever it is read as an attribute;
    # a generic one (enabled, pool, model…) only in the modules that take the section object.
    distinctive = "_" in key and key not in _GENERIC
    sources = _sources()
    out: list[dict[str, Any]] = []
    for fi, i in _index().get(key, []):
        path, lines = sources[fi]
        line = lines[i - 1]
        if strong.search(line):
            out.append({"file": path, "line": i, "snippet": line.strip()[:200], "via": None})
        elif (distinctive or _mentions(fi, cls_name)) and weak.search(line) and "Field(" not in line:
            out.append({"file": path, "line": i, "snippet": line.strip()[:200], "via": cls_name if _mentions(fi, cls_name) else "attribute"})
        if len(out) >= limit:
            break
    return out
    return out


def config_reference() -> list[dict[str, Any]]:
    """Every app.toml key with its editor / reason / readers (a fresh copy of the cached scan)."""
    import copy
    return copy.deepcopy(_config_reference())


@lru_cache(maxsize=1)
def _config_reference() -> list[dict[str, Any]]:
    editors = ui_editors()
    out: list[dict[str, Any]] = []
    for section, cls in _sections():
        hints = typing.get_type_hints(cls)
        for name, field in cls.model_fields.items():
            key = f"{section}.{name}"
            readers = _readers(section, cls.__name__, name)
            out.append({
                "key": key, "section": section, "name": name,
                "type": _type_name(hints.get(name, field.annotation)),
                "default": _default(field),
                "description": field.description,
                "editor": editors.get(key),
                "file_only": FILE_ONLY.get(key),
                "readers": readers,
            })
    return out


def config_gaps() -> list[str]:
    """The keys neither editable in the UI nor explained as file-only."""
    return [r["key"] for r in config_reference() if not r["editor"] and not r["file_only"]]
