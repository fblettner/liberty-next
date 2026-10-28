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


# ── API: every route of the running app ─────────────────────────────────────────────────────────

_IMPERATIVE = re.compile(r"""(?:require_permission\(\s*\w+\s*,\s*|has_permission\(\s*)f?["']([^"']+)["']""")


def _access(route: Any) -> tuple[str, list[str]]:
    """``(level, permissions)`` — level is public / signed-in / superuser / permission / role, read from
    the route's dependencies; the permissions also come from the handler's own checks
    (``require_permission(principal, f"sql:{connector}:{query}")``)."""
    from liberty.auth import dependencies as deps
    level, perms = "public", []
    stack = list(getattr(route, "dependant", None).dependencies if getattr(route, "dependant", None) else [])
    while stack:
        d = stack.pop()
        call = getattr(d, "call", None)
        if call is deps.require_superuser:
            level = "superuser"
        elif call is deps.get_current_principal and level == "public":
            level = "signed-in"
        elif getattr(call, "__qualname__", "").startswith(("require_permission.", "require_role.")):
            cells = {c.cell_contents for c in (call.__closure__ or ()) if isinstance(c.cell_contents, str)}
            kind = "permission" if call.__qualname__.startswith("require_permission.") else "role"
            perms += [f"{kind}:{c}" if kind == "role" else c for c in sorted(cells)]
            if level in ("public", "signed-in"):
                level = kind
        stack.extend(getattr(d, "dependencies", []) or [])
    try:
        import inspect
        src = inspect.getsource(route.endpoint)
        for m in _IMPERATIVE.finditer(src):
            if m.group(1) not in perms:
                perms.append(m.group(1))
    except (OSError, TypeError):
        pass
    if perms and level == "signed-in":
        level = "permission"
    return level, perms


_CATEGORIES: dict[str, str] = {
    "auth": "Access", "access": "Access",
    "connectors": "Workspace", "screens": "Workspace", "menus": "Workspace", "charts": "Workspace",
    "dashboards": "Workspace", "actions": "Workspace", "export": "Workspace", "views": "Workspace",
    "theme": "Workspace", "license": "Workspace", "dictgen": "Workspace",
    "explorer": "Features", "reports": "Features", "notifications": "Features", "plugins": "Features",
    "jobs": "nomaflow & deployment", "changesets": "nomaflow & deployment", "promotion": "nomaflow & deployment",
    "admin": "Settings", "logs": "Operations", "retention": "Operations", "developer": "Operations", "meta": "Operations",
    "ai": "Assistant",
}


def _group(file: str, path: str) -> tuple[str, str]:
    """``(group, category)`` of a route: its module (``web/jobs.py`` → jobs, ``auth/routes.py`` → auth),
    the config builders of ``web/admin.py`` by section (``/admin/config/screens/…`` → settings · screens)."""
    parts = file.split("/")
    stem = parts[-1].removesuffix(".py") if parts else "other"
    if stem == "routes" and len(parts) > 2:
        stem = parts[-2]
    elif stem == "main":
        stem = "meta"
    if stem == "admin":
        segs = path.strip("/").split("/")
        if len(segs) > 2 and segs[1] == "config":
            return f"settings · {segs[2]}", "Settings"
        return "admin", "Settings"
    return stem, _CATEGORIES.get(stem, "Other")


def _flat_routes(routes: Any, prefix: str = "") -> Any:
    """``(prefix, APIRoute)`` for every route, descending into included routers (FastAPI ≥ 0.16x keeps
    them wrapped — ``original_router`` + ``include_context.prefix`` — instead of copying the routes)."""
    from fastapi.routing import APIRoute
    for r in routes:
        if isinstance(r, APIRoute):
            yield prefix, r
        elif getattr(r, "original_router", None) is not None:
            ctx = getattr(r, "include_context", None)
            yield from _flat_routes(r.original_router.routes, prefix + (getattr(ctx, "prefix", "") or ""))


def _resolve(schema: Any, comps: dict[str, Any]) -> Any:
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 20:
        schema = comps.get(schema["$ref"].rsplit("/", 1)[-1], {})
        seen += 1
    return schema if isinstance(schema, dict) else {}


def _type_of(schema: Any, comps: dict[str, Any]) -> str:
    sch = _resolve(schema, comps)
    if "$ref" in (schema or {}):
        return schema["$ref"].rsplit("/", 1)[-1]
    for alt in ("anyOf", "oneOf"):
        if alt in sch:
            return " | ".join(_type_of(a, comps) for a in sch[alt])
    t = sch.get("type", "any")
    if t == "array":
        return f"{_type_of(sch.get('items', {}), comps)}[]"
    if sch.get("enum"):
        return " | ".join(json.dumps(e) for e in sch["enum"][:6])
    return str(t)


def _example(schema: Any, comps: dict[str, Any], depth: int = 0) -> Any:
    """An example value for a JSON schema (the OpenAPI one FastAPI builds from the Pydantic models)."""
    sch = _resolve(schema, comps)
    if depth > 6:
        return None
    if "example" in sch:
        return sch["example"]
    if "default" in sch and sch["default"] is not None:
        return sch["default"]
    if sch.get("enum"):
        return sch["enum"][0]
    for alt in ("anyOf", "oneOf"):
        if alt in sch:
            options = [a for a in sch[alt] if _resolve(a, comps).get("type") != "null"]
            return _example(options[0], comps, depth + 1) if options else None
    t = sch.get("type")
    if t == "object" or "properties" in sch:
        props = sch.get("properties") or {}
        return {k: _example(v, comps, depth + 1) for k, v in props.items()}
    if t == "array":
        return [_example(sch.get("items", {}), comps, depth + 1)]
    return {"string": "string", "integer": 0, "number": 0, "boolean": False}.get(str(t))


def _meaningful(schema: Any, comps: dict[str, Any]) -> bool:
    """A schema worth showing — not ``{}`` nor a bare ``dict[str, Any]``."""
    sch = _resolve(schema, comps)
    return bool(sch) and not (sch.get("type") == "object" and not sch.get("properties") and sch.get("additionalProperties") in (True, None))


def _openapi_detail(op: dict[str, Any], comps: dict[str, Any]) -> dict[str, Any]:
    params = [{"name": p.get("name"), "in": p.get("in"), "type": _type_of(p.get("schema", {}), comps),
               "required": bool(p.get("required")), "description": p.get("description") or ""}
              for p in op.get("parameters", [])]
    body = ((op.get("requestBody") or {}).get("content") or {})
    body_schema = (body.get("application/json") or next(iter(body.values()), {}) if body else {}).get("schema")
    request = None
    if body_schema is not None:
        sch = _resolve(body_schema, comps)
        req = set(sch.get("required") or [])
        request = {
            "type": _type_of(body_schema, comps),
            "content": next(iter(body), "application/json"),
            "fields": [{"name": k, "type": _type_of(v, comps), "required": k in req,
                        "description": _resolve(v, comps).get("description") or v.get("description") or ""}
                       for k, v in (sch.get("properties") or {}).items()],
            "example": _example(body_schema, comps) if "properties" in sch or "$ref" in (body_schema or {}) else None,
        }
    response = None
    for code in ("200", "201"):
        content = ((op.get("responses") or {}).get(code) or {}).get("content") or {}
        sch = (content.get("application/json") or {}).get("schema")
        if sch is not None and _meaningful(sch, comps):
            response = {"code": code, "type": _type_of(sch, comps), "example": _example(sch, comps)}
            break
        if content and "application/json" not in content:
            response = {"code": code, "type": next(iter(content)), "example": None}
            break
    return {"parameters": params, "request": request, "response": response}


def api_reference(app: Any) -> list[dict[str, Any]]:
    """Every HTTP route of *app*: method, path, summary, group (the router tag), access, the handler."""
    import inspect

    spec = app.openapi()
    comps = (spec.get("components") or {}).get("schemas") or {}
    out: list[dict[str, Any]] = []
    for prefix, route in _flat_routes(app.routes):
        level, perms = _access(route)
        try:
            file = str(Path(inspect.getsourcefile(route.endpoint) or "").resolve().relative_to(_ROOT.parent))
            line = inspect.getsourcelines(route.endpoint)[1]
        except (OSError, TypeError, ValueError):
            file, line = "", 0
        doc = inspect.getdoc(route.endpoint) or ""
        for method in sorted(m for m in (route.methods or ()) if m != "HEAD"):
            out.append({
                "method": method, "path": prefix + route.path, "summary": route.summary or route.name.replace("_", " "),
                "description": doc,
                "group": _group(file, prefix + route.path)[0], "category": _group(file, prefix + route.path)[1],
                "tag": route.tags[0] if route.tags else "",
                "access": level, "permissions": perms,
                "file": file, "line": line, "in_schema": bool(route.include_in_schema),
                "params": [p.name for p in route.dependant.path_params + route.dependant.query_params],
                **_openapi_detail(((spec.get("paths") or {}).get(prefix + route.path) or {}).get(method.lower()) or {}, comps),
            })
    out.sort(key=lambda r: (r["path"], r["method"]))
    return out
