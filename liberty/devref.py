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
import os
import re
import typing
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from liberty.config import Settings

_ROOT = Path(__file__).resolve().parent

# Screens other than Liberty Next → Administer → App that write a key of app.toml.
_OTHER_EDITORS: dict[str, str] = {
    "app.log_levels": "Monitoring → Server log (levels per module)",
    "reports.branding": "Liberty Next → Administer → App → PDF branding",
    "changesets.post_apply": "Liberty Next → Deploy → Changes → Post-apply steps",
    "promotion.targets": "Liberty Next → Deploy → Package → Promotion",
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
            out[f"{section}.{k}"] = "Liberty Next → Administer → App"
    return out


def ui_editors() -> dict[str, str]:
    """``"section.key"`` → the screen that edits it."""
    return {**_app_editor_keys(), **_OTHER_EDITORS}


def _type_name(ann: Any) -> str:
    text = getattr(ann, "__name__", None) if isinstance(ann, type) else None
    return text or str(ann).replace("typing.", "").replace("pathlib.", "")


def _portable_path(path: Path) -> str:
    """A default path as the operator would write it: under ``LIBERTY_APPS_DIR`` (or beside it, where
    nomaflow's jobs.toml lives) as ``${LIBERTY_APPS_DIR}/…``, never the absolute path of the machine
    that runs the reference."""
    apps = os.environ.get("LIBERTY_APPS_DIR", "").strip()
    if apps:
        base = Path(apps)
        for root, prefix in ((base, "${LIBERTY_APPS_DIR}"), (base.parent, "${LIBERTY_APPS_DIR}/..")):
            try:
                return f"{prefix}/{path.relative_to(root).as_posix()}"
            except ValueError:
                continue
    return path.as_posix()


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
    if isinstance(value, Path):
        return _portable_path(value)
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
# nomaflow's run check (liberty/web/jobs.py ``_require_run``): ``job:<id>``
_RUN_CHECK = re.compile(r"\b_require_run\(")


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
        elif isinstance(getattr(call, "permission", None), str):
            # a feature's own guard carrying its permission (nomaflow:view / nomaflow:edit)
            perms.append(call.permission)
            if level in ("public", "signed-in"):
                level = "permission"
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
        if _RUN_CHECK.search(src) and "job:{job_id}" not in perms:
            perms.append("job:{job_id}")
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
    the config builders of ``web/admin/`` by section (``/admin/config/screens/…`` → settings · screens)."""
    parts = file.split("/")
    stem = parts[-1].removesuffix(".py") if parts else "other"
    if len(parts) > 2 and parts[-2] == "admin" and parts[-3] == "web":
        stem = "admin"                          # the admin package (web/admin/<section>.py) groups as before
    elif stem == "routes" and len(parts) > 2:
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


# ── Data model: the framework's tables + each connector's declared tables ───────────────────────

_FRAMEWORK_MODELS = (
    ("liberty.auth.models", "accounts"), ("liberty.jobs.models", "nomaflow"), ("liberty.changesets.models", "change packages"),
    ("liberty.userviews.store", "saved views"), ("liberty.notify.store", "notifications"),
)
_FROM = re.compile(r"\bFROM\s+([#\w.$\"\[\]]+)", re.IGNORECASE)


def _framework_tables() -> list[dict[str, Any]]:
    import importlib
    out: list[dict[str, Any]] = []
    for module_name, group in _FRAMEWORK_MODELS:
        mod = importlib.import_module(module_name)
        base = getattr(mod, "Base", None)
        if base is None:
            continue
        docs = {m.local_table.name: (m.class_.__doc__ or "").strip() for m in base.registry.mappers}
        for t in base.metadata.sorted_tables:
            fks = {fk.parent.name: f"{fk.column.table.name}.{fk.column.name}" for fk in t.foreign_keys}
            out.append({
                "id": f"framework:{t.name}", "name": t.name, "owner": "framework", "group": group,
                "label": "", "description": docs.get(t.name, "").split("\n\n")[0],
                "physical": t.name, "physical_raw": t.name, "key": [c.name for c in t.primary_key.columns],
                "related": sorted({v.split(".")[0] for v in fks.values()} - {t.name}),
                "slots": [], "used_by": [{"kind": "framework", "name": module_name}],
                "columns": [{
                    "name": c.name, "type": str(c.type), "label": "", "key": c.primary_key,
                    "nullable": bool(c.nullable), "references": fks.get(c.name), "screens": [], "description": c.comment or "",
                } for c in t.columns],
            })
    return out


def _sql_text(sql: Any) -> str:
    return sql if isinstance(sql, str) else " ".join(str(v) for v in (sql or {}).values())


_SQL_NOISE = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'", re.S)
_OUT_NAME = re.compile(r'(?:\bAS\s+)?"?([A-Za-z_][\w$#]*)"?\s*$', re.I)


def _select_columns(sql: Any) -> list[str]:
    """The output names of a read query's outer SELECT list — each item's alias, else its bare column
    (``t.col`` → ``col``); ``*`` and an unnamed expression are skipped. A per-dialect map reads its first
    variant. For the tables no screen shows: their columns from the SQL itself, without a database."""
    text = sql if isinstance(sql, str) else next(iter((sql or {}).values()), "")
    s = _SQL_NOISE.sub(lambda m: "''" if m.group(0).startswith("'") else " ", str(text))
    depth, start, items = 0, -1, []
    for m in re.finditer(r"[(),]|\bSELECT\b|\bFROM\b", s, re.I):
        tok = m.group(0).upper()
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        elif depth == 0 and tok == "SELECT" and start < 0:
            start = m.end()
        elif depth == 0 and start >= 0 and tok in (",", "FROM"):
            items.append(s[start:m.start()])
            start = m.end()
            if tok == "FROM":
                break
    out: list[str] = []
    for item in items:
        item = re.sub(r"^\s*DISTINCT\s+", "", item, flags=re.I).strip()
        named = _OUT_NAME.search(item)
        if named and not item.endswith("*") and named.group(1).upper() not in out and not item.endswith(")"):
            out.append(named.group(1))
    return out


def _connector_tables(state: Any) -> list[dict[str, Any]]:
    """Every ``[[connectors.<c>.tables]]``: its CRUD slots, the physical table its read query selects
    from, the columns its screens show (with the dictionary's labels), who uses it, its related
    tables (the screens' nested tables)."""
    reg = getattr(state, "connectors", None)
    screens_cfg = getattr(state, "screens", None)
    dictionary = getattr(reg, "dictionary", None) if reg is not None else None
    if reg is None:
        return []
    jobs = raw_config_jobs(state)
    explorer = raw_config_explorer(state)
    from liberty.web.dependencies import raw_config
    # (connector, query) → the dashboards / charts reading it (a widget's connector defaults to its app).
    views: dict[tuple[str, str], list[dict[str, str]]] = {}
    for app, boards in (raw_config(state, "dashboards.toml").get("dashboards") or {}).items():
        for bid, board in (boards or {}).items():
            for w in (board or {}).get("widgets") or [] if isinstance(board, dict) else []:
                if isinstance(w, dict) and w.get("query"):
                    use = {"kind": "dashboard", "name": f"{app}.{bid}"}
                    lst = views.setdefault((str(w.get("connector") or app), str(w["query"])), [])
                    if use not in lst:
                        lst.append(use)
    for scope, charts in (raw_config(state, "charts.toml").get("charts") or {}).items():
        for cid, chart in (charts or {}).items():
            if isinstance(chart, dict) and chart.get("query"):
                views.setdefault((str(chart.get("connector") or scope), str(chart["query"])), []).append(
                    {"kind": "chart", "name": f"{scope}.{cid}"})
    screens: list[tuple[str, str, Any]] = [
        (app, sid, s) for app, by in ((screens_cfg.screens or {}).items() if screens_cfg is not None else []) for sid, s in by.items()]
    query_screen = {}   # (connector, query) → screen ids
    for app, sid, s in screens:
        conn = getattr(s, "connector", None) or app
        for attr in ("read_query", "update_query", "insert_query", "delete_query"):
            q = getattr(s, attr, None)
            if q:
                query_screen.setdefault((conn, q), []).append((app, sid, s))
    out: list[dict[str, Any]] = []
    for cname in reg.names():
        cfg = getattr(reg.get(cname), "config", None)
        tables = getattr(cfg, "tables", None) or []
        prefix = int(getattr(cfg, "dd_prefix", 0) or 0)
        for tdef in tables:
            slots = {s: f"{tdef.name}_{s}" for s in ("get", "put", "post", "delete") if getattr(tdef, s, None) is not None}
            get_sql = _sql_text(getattr(tdef.get, "sql", "")) if tdef.get is not None else ""
            # #SCHEMA.CTL#.F0005#DBLINK.CTL# → F0005 (the placeholders kept in physical_raw); among
            # several FROMs (joins, sub-queries) the one named like the table wins, else the first.
            froms = [(raw.strip('"[]'), re.sub(r"#[^#]*#\.?", "", raw.strip('"[]')).split(".")[-1]) for raw in _FROM.findall(get_sql)]
            raw_physical, physical = next((f for f in froms if f[1].lower() == tdef.name.lower()), froms[0] if froms else ("", ""))
            users: list[dict[str, str]] = []
            cols: dict[str, dict[str, Any]] = {}
            keys: list[str] = []
            related: set[str] = set()
            for q in slots.values():
                for app, sid, s in query_screen.get((cname, q), []):
                    if {"kind": "screen", "name": f"{app}.{sid}"} not in users:
                        users.append({"kind": "screen", "name": f"{app}.{sid}"})
                    keys += [k for k in s.effective_key_columns() if k not in keys]
                    for h in getattr(s, "columns", None) or []:
                        col = cols.setdefault(h.name, {"name": h.name, "type": "", "label": "", "key": False, "nullable": True,
                                                       "references": None, "screens": [], "description": ""})
                        col["key"] = col["key"] or bool(getattr(h, "key", False))
                        col["label"] = col["label"] or (h.label if isinstance(getattr(h, "label", None), str) else "") or ""
                        if f"{app}.{sid}" not in col["screens"]:
                            col["screens"].append(f"{app}.{sid}")
                    dialog = getattr(s, "dialog", None)
                    for tab in (getattr(dialog, "tabs", None) or []) if dialog is not None else []:
                        child = getattr(tab, "screen", None) if getattr(tab, "type", None) == "nested_table" else None
                        child_s = (screens_cfg.screens.get(app) or {}).get(child) if child and screens_cfg is not None else None
                        rq = getattr(child_s, "read_query", None) if child_s is not None else None
                        if rq and rq.endswith("_get"):
                            related.add(rq[:-4])
            for q in slots.values():
                users += [u for u in views.get((cname, q), []) if u not in users]
            # No screen shows this table (a dashboard's or an analysis's query): its columns from the SQL.
            if not cols and tdef.get is not None:
                for name in _select_columns(tdef.get.sql):
                    cols[name] = {"name": name, "type": "", "label": "", "key": False, "nullable": True,
                                  "references": None, "screens": [], "description": ""}
            for job in jobs:
                for step in job.get("steps") or []:
                    if isinstance(step, dict) and step.get("query") in slots.values():
                        users.append({"kind": "job", "name": str(job.get("id"))})
                        break
            for app, models in explorer.items():
                for mid, model in (models or {}).items():
                    ents = (model or {}).get("entities") or {}
                    if any(isinstance(e, dict) and e.get("query") in slots.values() for e in ents.values()):
                        users.append({"kind": "explorer", "name": f"{app}.{mid}"})
            if dictionary is not None:
                for col in cols.values():
                    entry = dictionary.find_entry(col["name"].upper(), connector=cname)
                    if entry is None and prefix and len(col["name"]) > prefix:
                        entry = dictionary.find_entry(col["name"].upper()[prefix:], connector=cname)
                    if entry is not None:
                        col["label"] = col["label"] or (entry.label or "")
                        col["type"] = entry.format or ""
                        col["description"] = getattr(entry, "description", None) or ""
            for col in cols.values():
                col["key"] = col["key"] or col["name"] in keys
            out.append({
                "id": f"{cname}:{tdef.name}", "name": tdef.name, "owner": "connector", "group": cname,
                "label": tdef.label or "", "description": tdef.description or "", "physical": physical, "physical_raw": raw_physical,
                "key": keys, "related": sorted(related - {tdef.name}), "slots": list(slots),
                "used_by": users, "columns": list(cols.values()),
            })
    return out


def raw_config_jobs(state: Any) -> list[dict[str, Any]]:
    from liberty.web.dependencies import raw_config
    return [j for j in raw_config(state, "jobs.toml").get("jobs") or [] if isinstance(j, dict)]


def raw_config_explorer(state: Any) -> dict[str, Any]:
    from liberty.web.dependencies import raw_config
    return raw_config(state, "explorer.toml").get("explorer") or {}


def data_model(state: Any) -> list[dict[str, Any]]:
    """The framework's own tables (SQLAlchemy models) then each connector's declared tables."""
    return _framework_tables() + _connector_tables(state)


# ── command line ──────────────────────────────────────────────────────────────────────────────

def _first_paragraph(text: str | None) -> str | None:
    para = " ".join((text or "").strip().split("\n\n")[0].split())
    # a module docstring opens with its command's name: ``liberty-admin`` — manage …
    para = re.sub(r"^``[\w-]+``\s*[—–-]+\s*", "", para)
    return (para[:1].upper() + para[1:]) or None


def _arguments(parser: Any) -> list[dict[str, Any]]:
    """A parser's arguments and options as argparse holds them (help, value, default, choices)."""
    import argparse
    out: list[dict[str, Any]] = []
    for a in parser._actions:  # noqa: SLF001 — argparse keeps them there
        if isinstance(a, (argparse._HelpAction, argparse._SubParsersAction)) or a.help == argparse.SUPPRESS:  # noqa: SLF001
            continue
        takes_value = a.nargs != 0
        default = a.default if a.default not in (None, argparse.SUPPRESS) and takes_value else None
        out.append({
            "name": ", ".join(a.option_strings) if a.option_strings else (a.metavar or a.dest),
            "positional": not a.option_strings,
            "value": (a.metavar or a.dest.upper()) if takes_value and a.option_strings else None,
            "help": " ".join((a.help or "").split()) or None,
            "default": default if isinstance(default, (str, int, float, bool)) else (str(default) if default is not None else None),
            "required": bool(a.required) or (not a.option_strings and a.nargs not in ("?", "*")),
            "choices": [str(c) for c in a.choices] if a.choices else [],
            "repeat": a.nargs in ("*", "+") or isinstance(a, argparse._AppendAction),  # noqa: SLF001
        })
    return out


def _commands(parser: Any, prog: str) -> list[dict[str, Any]]:
    """The sub-commands of *parser* (nested ones flattened as ``parent child``), each with its usage."""
    import argparse
    out: list[dict[str, Any]] = []
    for a in parser._actions:  # noqa: SLF001
        if not isinstance(a, argparse._SubParsersAction):  # noqa: SLF001
            continue
        helps = {c.dest: c.help for c in a._choices_actions}  # noqa: SLF001
        seen: dict[int, dict[str, Any]] = {}
        for name, sp in a.choices.items():
            if id(sp) in seen:                       # an alias of a command already listed
                seen[id(sp)]["aliases"].append(name)
                continue
            cmd = {"name": name, "aliases": [], "help": " ".join((helps.get(name) or "").split()) or None,
                   "description": _first_paragraph(sp.description) if sp.description != parser.description else None,
                   "usage": " ".join(sp.format_usage().replace("usage:", "", 1).split()),
                   "arguments": _arguments(sp)}
            seen[id(sp)] = cmd
            out.append(cmd)
            for child in _commands(sp, f"{prog} {name}"):
                out.append({**child, "name": f"{name} {child['name']}"})
    return out


def cli_reference() -> list[dict[str, Any]]:
    """Every console script the package installs (its entry points, not a list kept by hand): the
    module, what it does, its global options and each sub-command with its arguments."""
    import inspect
    from importlib import import_module, metadata
    eps = [ep for ep in metadata.entry_points(group="console_scripts")
           if ep.value.startswith("liberty.") and (ep.dist is None or ep.dist.name.lower().replace("_", "-") == "liberty-next")]
    out: list[dict[str, Any]] = []
    for ep in sorted({ep.name: ep for ep in eps}.values(), key=lambda e: e.name):
        mod_name, _, func_name = ep.value.partition(":")
        try:
            module = import_module(mod_name)
        except Exception:  # noqa: BLE001 — an optional extra missing must not hide the others
            continue
        builder = getattr(module, "build_parser", None)
        parser = builder() if callable(builder) else None
        func = getattr(module, func_name.strip(), None)
        doc = _first_paragraph(parser.description if parser is not None else None) \
            or _first_paragraph(inspect.getdoc(func) if func is not None and mod_name == "liberty.main" else module.__doc__)
        target = builder if callable(builder) else func
        try:
            line = inspect.getsourcelines(target)[1] if target is not None else None
        except (OSError, TypeError):
            line = None
        out.append({
            "name": ep.name, "entry": ep.value, "file": f"liberty/{Path(module.__file__ or '').name}" if mod_name.count(".") == 1 else mod_name,
            "line": line, "description": doc,
            "usage": " ".join(parser.format_usage().replace("usage:", "", 1).split()) if parser is not None else ep.name,
            "options": _arguments(parser) if parser is not None else [],
            "commands": _commands(parser, ep.name) if parser is not None else [],
        })
    return out

