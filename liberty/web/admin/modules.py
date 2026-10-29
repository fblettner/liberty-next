"""``/admin`` — module catalogues (catalogue v2): list, preview, apply (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, _drop_none, _reinject_action_type, _snapshot_config, router
from pydantic import BaseModel, ValidationError

from liberty.actions import load_actions
from liberty.actions.config import parse_actions
from liberty.ai.appconfig import parse_ai_config
from liberty.charts import load_charts
from liberty.charts.config import parse_charts
from liberty.dashboards import load_dashboards
from liberty.dashboards.config import parse_dashboards

# ── module catalogues (catalogue v2): list, preview, apply ───────────────────────────────────
# A module (``[[modules]]`` in the presets files, :mod:`liberty.modules`) is applied in one pass:
# its tables' live columns are read, :func:`liberty.module_apply.build_plan` says what is new and
# what exists already (never overwritten), then the new read queries / screens / menu items are
# written additively, the dictionary entries created under the app's scope, and the runtime reloaded.

def _modules_dir(request: Request) -> Path:
    from liberty.presets import presets_dir
    return presets_dir(request.app.state.settings.connectors.config_path)


def _module_or_404(request: Request, module_id: str):
    from liberty.modules import ModuleError, find_module
    try:
        module = find_module(_modules_dir(request), module_id)
    except ModuleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if module is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown module {module_id!r}")
    return module


async def _module_plan(request: Request, module: Any, *, replace: bool = False):
    """The plan of *module* against the live tables and the current configuration files."""
    from sqlalchemy.exc import SQLAlchemyError

    from liberty.connectors.base import ConnectorError
    from liberty.connectors.introspect import introspect_pool, resolve_schema_token
    from liberty.module_apply import build_plan
    from liberty.web.rename import _load_doc

    settings = request.app.state.settings
    registry = request.app.state.connectors
    try:
        conn = registry.sql(module.connector)
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # every table of the module (the screens' and their joined lookups'), read once
    wanted: dict[str, str | None] = {}
    for s in module.screens:
        wanted.setdefault(s.table.upper(), module.screen_schema(s))
        for j in s.joins:
            wanted.setdefault(j.table.upper(), j.schema_ or module.screen_schema(s))
    live: dict[str, list[str] | None] = {}
    for table_name, schema in wanted.items():
        owner = resolve_schema_token(schema, registry.pools.schemas(conn.pool_name)) if schema else None
        try:
            info = await introspect_pool(registry.pools, conn.pool_name, only_schema=owner, name_like=table_name)
        except SQLAlchemyError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"schema introspection failed: {exc}") from exc
        table = next((t for t in info.get("tables", []) if str(t.get("name", "")).upper() == table_name), None)
        live[table_name] = [str(c.get("name", "")) for c in table.get("columns", [])] if table else None
    connectors = _load_doc("connectors", Path(settings.connectors.config_path))
    conn_doc = (connectors.get("connectors") or {}).get(module.connector) or {}
    screens = _load_doc("screens", Path(settings.screens.config_path))
    menus = _load_doc("menus", Path(settings.menus.config_path))
    return build_plan(
        module, live,
        existing_tables={str(t.get("name", "")).lower() for t in conn_doc.get("tables", [])},
        existing_screens=set(((screens.get("screens") or {}).get(module.app) or {})),
        existing_menu_ids={str(i.get("id")) for i in (((menus.get("menus") or {}).get(module.app) or {}).get("items") or [])},
        replace=replace,
    )


@router.get("/assistant/modules", summary="List module catalogues")
async def list_modules(request: Request, _: Superuser, connector: str | None = None) -> dict[str, Any]:
    """The modules of the catalogue files (``[[modules]]`` beside the presets): id, label, group,
    connector, app and their screens (id, table, label)."""
    from liberty.modules import ModuleError, load_modules
    try:
        modules = load_modules(_modules_dir(request), connector)
    except ModuleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return {"modules": [{
        "id": m.id, "label": m.label, "l": m.l, "description": m.description, "group": m.group,
        "connector": m.connector, "app": m.app,
        "screens": [{"id": s.id, "table": s.table, "label": s.label, "details": [d.screen for d in s.details]} for s in m.screens],
    } for m in modules]}


@router.post("/assistant/modules/{module_id}/preview", summary="Preview applying a module")
async def preview_module(module_id: str, request: Request, _: Superuser, replace: bool = False) -> dict[str, Any]:
    """What applying the module would do — per screen its table, the resolved column prefix, the
    columns found and the module's data items the table lacks, new / exists for the query, the
    screen and the menu items — and the errors that block it (a table or a key column not found).
    Reads the live schema; writes nothing. ``?replace=1`` previews rewriting the module's own
    entries that exist (``replace``) instead of keeping them (``exists``)."""
    plan = await _module_plan(request, _module_or_404(request, module_id), replace=replace)
    return plan.summary()


@router.post("/assistant/modules/{module_id}/apply", summary="Apply a module")
async def apply_module(module_id: str, request: Request, _: Superuser, replace: bool = False) -> dict[str, Any]:
    """Apply the module: the new read queries, screens (read-only, with their detail tabs) and menu
    items written additively — what exists is kept as it is — then the dictionary entries of its
    tables (the JDE data dictionary's labels, formats and UDC lookups) under the app's scope, and
    the runtime reloaded. 422 with the plan when it has errors (nothing written). ``?replace=1``
    rewrites the module's own queries, screens and menu items that exist — a screen customised
    since its last apply loses those changes."""
    from liberty.module_apply import write_plan
    from liberty.runtime import rebuild_runtime
    from liberty.web.dictgen import create_entries

    module = _module_or_404(request, module_id)
    plan = await _module_plan(request, module, replace=replace)
    if plan.errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"message": "the module cannot be applied", **plan.summary()})
    settings = request.app.state.settings
    try:
        write_plan(plan, connectors_path=settings.connectors.config_path, screens_path=settings.screens.config_path,
                   menus_path=settings.menus.config_path, snapshot=lambda p: _snapshot_config(request, Path(p)))
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"the module's configuration does not validate: {exc}") from exc
    # the new queries must exist before the dictionary scan reads through the connector
    await rebuild_runtime(request.app)
    state = request.app.state
    dictionary = await create_entries(registry=state.connectors, settings=state.settings, scope=module.app,
                                      sources=plan.dictionary_sources, versions=getattr(state, "config_versions", None))
    if dictionary["created"]:
        await rebuild_runtime(request.app, sections={"connectors"})
    return {**plan.summary(), "dictionary": dictionary}


@router.get("/config/charts/parsed", summary="Get charts config")
async def get_charts_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``charts.toml`` parsed and normalised — ``{path, charts: {<id>: ChartConfig
    dict}}``. A missing file → an empty chart set. Default-valued keys are dropped (so a saved
    chart with all spec defaults round-trips cleanly without empty noise)."""
    path = Path(request.app.state.settings.charts.config_path)
    cfg = load_charts(path)
    # Nested ``{scope: {id: chart}}``; ``id`` + ``connector`` are the path keys, so they're dropped
    # from each body (re-injected by parse_charts on the way back in). ``exclude_defaults`` is
    # ``False`` on purpose: the editor preview needs every field present (e.g. ``aggregation``
    # defaults to ``"sum"`` — stripping it left the canvas with an undefined aggregation and the
    # chart silently rendered no bars).
    return {
        "path": str(path),
        "charts": {
            scope: {
                cid: c.model_dump(exclude_defaults=False, exclude_none=True, exclude={"id", "connector"})
                for cid, c in by_id.items()
            }
            for scope, by_id in cfg.charts.items()
        },
    }


class ChartsBody(BaseModel):
    # Nested ``{scope: {id: chart-dict}}``.
    charts: dict[str, dict[str, Any]]


@router.put("/config/charts/parsed", summary="Update charts config")
async def put_charts_parsed(body: ChartsBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate the submitted dict against :class:`ChartsFile`, then rewrite ``charts.toml`` via
    ``tomli-w`` (same perf pattern as :func:`put_screens_parsed`). Charts is currently small so
    the gain here is mostly future-proofing — keeps the per-PUT-endpoint behaviour consistent.
    Re-parses the resulting text before writing. Does not reload — call ``POST /admin/reload``."""
    try:
        validated = parse_charts({"charts": body.charts})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid charts: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid charts: {exc}") from exc

    normalized = validated.model_dump(exclude_defaults=False, exclude_none=True)
    # id + connector are the table keys (path-derived) — keep them out of the written body.
    for by_id in normalized.get("charts", {}).values():
        for chart in by_id.values():
            chart.pop("id", None)
            chart.pop("connector", None)
    import tomli_w
    new_text = tomli_w.dumps(normalized)
    try:
        parse_charts(tomllib.loads(new_text))   # belt-and-braces re-validation
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting charts are invalid: {exc}") from exc

    path = Path(request.app.state.settings.charts.config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


class FromPresetBody(BaseModel):
    app: str
    preset: str
    # the model being edited — its declared entities are kept and completed
    model: dict[str, Any] = {}
    # create the dictionary entries the preset's tables lack (the assistant's Dictionary step,
    # filed under the app) — the fields then show their description, not their column name
    dictionary: bool = True


@router.post("/explorer/from-preset", summary="Fill an explorer model from a preset")
async def explorer_from_preset(body: FromPresetBody, request: Request, _: Superuser) -> dict[str, Any]:
    """The Liberty Next → Build → Explorer "apply a preset": the preset's tables as entities (the base table
    the hub), its joins as relations resolved on the discovered columns. Returns the model body
    to put in the editor + the warnings (a join column not found, a table that does not run)."""
    from liberty.explorer.config import ExplorerModel
    from liberty.explorer.model import materialize_preset
    from liberty.presets import find_preset, presets_dir
    preset = find_preset(presets_dir(request.app.state.settings.connectors.config_path), body.preset)
    if preset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no preset {body.preset!r}")
    try:
        model = ExplorerModel.model_validate({**_drop_none(body.model), "preset": body.preset, "id": "draft", "app": body.app})
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid model: {exc}") from exc
    filled, warnings = await materialize_preset(
        request.app.state.connectors, getattr(request.app.state, "screens", None), body.app, model, preset,
        language=request.headers.get("x-liberty-lang"),
    )
    out: dict[str, Any] = {"model": _explorer_model_dump(filled), "warnings": warnings}
    if body.dictionary:
        from liberty.web.dictgen import create_entries_and_reload, model_sources
        out["dictionary"] = await create_entries_and_reload(request, scope=body.app, sources=model_sources(filled, None))
    return out


class DescribeEntityBody(BaseModel):
    app: str
    entity: dict[str, Any]


@router.post("/explorer/describe-entity", summary="Discover an explorer entity's columns")
async def explorer_describe_entity(body: DescribeEntityBody, request: Request, _: Superuser) -> dict[str, Any]:
    """The entity editor's column list for a **table** entity (a query entity runs the query
    itself): ``{columns: [{name, type, label, key, hidden, format}], error}``."""
    from liberty.explorer.config import ExplorerEntity
    from liberty.explorer.model import describe_entity
    try:
        ent = ExplorerEntity.model_validate({**_drop_none(body.entity), "id": body.entity.get("id") or "draft"})
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid entity: {exc}") from exc
    view = await describe_entity(request.app.state.connectors, getattr(request.app.state, "screens", None), body.app, ent, language=request.headers.get("x-liberty-lang"))
    return {"columns": [{"name": f.name, "type": f.type, "label": f.label, "hidden": f.hidden, "format": f.format} for f in view.fields], "error": view.error}


@router.get("/config/explorer/parsed", summary="Get explorer config")
async def get_explorer_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``explorer.toml`` parsed — ``{path, explorer: {<app>: {<model>: model dict}}}``;
    a missing file is an empty set. Relations keep their ``from`` key; entity ids are the table keys."""
    from liberty.explorer import load_explorer
    path = Path(request.app.state.settings.explorer.config_path)
    cfg = load_explorer(path)
    out: dict[str, dict[str, Any]] = {}
    for app, mid, m in cfg.items():
        out.setdefault(app, {})[mid] = _explorer_model_dump(m)
    return {"path": str(path), "explorer": out}


def _explorer_model_dump(m: Any) -> dict[str, Any]:
    d = m.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True, exclude={"id", "app"})
    d["hub"] = m.hub
    for eid, e in d.get("entities", {}).items():
        e.pop("id", None)
        if m.entities[eid].query:
            e.setdefault("query", m.entities[eid].query)
    return d


class ExplorerBody(BaseModel):
    explorer: dict[str, dict[str, dict[str, Any]]]


@router.get("/config/ai/parsed", summary="Get the apps' AI config")
async def get_ai_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``ai.toml`` parsed — ``{path, ai: {<app>: {starters, screens: {<id>: {starters}}}}}``;
    a missing file reads as empty."""
    from liberty.ai.appconfig import load_ai_config
    path = Path(request.app.state.settings.ai.config_path)
    cfg = load_ai_config(path)
    return {"path": str(path), "ai": cfg.model_dump()["ai"]}


class AiBody(BaseModel):
    ai: dict[str, dict[str, Any]]


@router.put("/config/ai/parsed", summary="Update the apps' AI config")
async def put_ai_parsed(body: AiBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate against :class:`AiFile`, rewrite ``ai.toml`` through ``tomli-w`` (the builder
    owns the file; empty lists and empty screen blocks are dropped). Does not reload — call
    ``POST /admin/reload``."""
    try:
        validated = parse_ai_config({"ai": _drop_none(body.ai)})
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid ai config: {exc}") from exc
    normalized: dict[str, Any] = {"ai": {}}
    for app, block in validated.ai.items():
        d: dict[str, Any] = {}
        if block.starters:
            d["starters"] = list(block.starters)
        if block.groups:
            d["groups"] = [g.model_dump() for g in block.groups]
        screens = {sid: {k: v for k, v in (("starters", list(s.starters)), ("groups", [g.model_dump() for g in s.groups])) if v}
                   for sid, s in block.screens.items() if s.starters or s.groups}
        if screens:
            d["screens"] = screens
        if block.playbooks:
            d["playbooks"] = {pid: pb.model_dump(exclude_none=True) for pid, pb in block.playbooks.items()}
        normalized["ai"][app] = d
    import tomli_w
    new_text = tomli_w.dumps(normalized)
    path = Path(request.app.state.settings.ai.config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


@router.put("/config/explorer/parsed", summary="Update explorer config")
async def put_explorer_parsed(body: ExplorerBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate against :class:`ExplorerFile`, rewrite ``explorer.toml`` through ``tomli-w`` (the
    builder owns the file). Does not reload — call ``POST /admin/reload``."""
    from liberty.explorer import parse_explorer
    try:
        validated = parse_explorer({"explorer": _drop_none(body.explorer)})
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid explorer config: {exc}") from exc
    normalized: dict[str, Any] = {"explorer": {}}
    for app, mid, m in validated.items():
        normalized["explorer"].setdefault(app, {})[mid] = _explorer_model_dump(m)
    import tomli_w
    new_text = tomli_w.dumps(normalized)
    try:
        parse_explorer(tomllib.loads(new_text))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting explorer config is invalid: {exc}") from exc
    path = Path(request.app.state.settings.explorer.config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


@router.get("/config/actions/parsed", summary="Get shared actions config")
async def get_actions_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``actions.toml`` parsed — ``{path, actions: {<id>: SharedAction dict}}``. The
    ``id`` is the map key, so it's dropped from each body (re-injected by parse_actions on save)."""
    path = Path(request.app.state.settings.actions.config_path)
    cfg = load_actions(path)
    out: dict[str, Any] = {}
    for aid, a in cfg.actions.items():
        d = a.model_dump(exclude_defaults=True, exclude_none=True, exclude={"id"})
        # Re-inject the Action ``type`` discriminator on every step (+ nested then/else/steps) —
        # exclude_defaults strips it, and the editor's union parser needs it to render each step
        # as its real variant (else an `if` reaches the builder typeless and renders as run_query).
        for s_dict, s_model in zip(d.get("steps", []), a.steps, strict=False):
            _reinject_action_type(s_dict, s_model)
        out[aid] = d
    return {"path": str(path), "actions": out}


class ActionsBody(BaseModel):
    actions: dict[str, dict[str, Any]]


@router.put("/config/actions/parsed", summary="Update shared actions config")
async def put_actions_parsed(body: ActionsBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate the submitted dict against :class:`SharedActionsFile`, then rewrite ``actions.toml``
    via ``tomli-w`` (tomllib+tomli_w, not tomlkit — actions can carry large step trees). Re-parses
    the written text. Does not reload — call ``POST /admin/reload``."""
    try:
        validated = parse_actions({"actions": body.actions})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid actions: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid actions: {exc}") from exc

    # ``exclude_defaults=False`` is intentional — the same reason as screens / menus: an Action's
    # ``type`` discriminator equals its Literal default, so excluding defaults would strip it and
    # the re-validation below (+ the runtime) couldn't pick the union branch.
    normalized = validated.model_dump(exclude_defaults=False, exclude_none=True)
    for action in normalized.get("actions", {}).values():
        action.pop("id", None)   # id is the table key
    import tomli_w
    new_text = tomli_w.dumps(normalized)
    try:
        parse_actions(tomllib.loads(new_text))   # belt-and-braces re-validation
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting actions are invalid: {exc}") from exc

    path = Path(request.app.state.settings.actions.config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


@router.get("/config/notifications/parsed", summary="Get notification rules")
async def get_notifications_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``notifications.toml`` — ``{path, notifications: {<id>: rule dict}}``."""
    from liberty.notify import load_notification_rules

    path = Path(request.app.state.settings.notify.config_path)
    cfg = load_notification_rules(path)
    return {"path": str(path), "notifications": {rid: r.model_dump(exclude_none=True) for rid, r in cfg.notifications.items()}}


class NotificationsBody(BaseModel):
    notifications: dict[str, dict[str, Any]]


@router.put("/config/notifications/parsed", summary="Update notification rules")
async def put_notifications_parsed(body: NotificationsBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate against :class:`NotificationsFile`, rewrite ``notifications.toml`` (tomllib +
    tomli_w — a small file the editor owns) and reload the rules section at once."""
    from liberty.notify import parse_notification_rules

    try:
        validated = parse_notification_rules({"notifications": _drop_none(body.notifications)})
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid notifications: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps(validated.model_dump(exclude_none=True))
    try:
        parse_notification_rules(tomllib.loads(new_text))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting notifications are invalid: {exc}") from exc
    path = Path(request.app.state.settings.notify.config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")
    from liberty.runtime import rebuild_runtime
    await rebuild_runtime(request.app, sections={"notifications"})
    return {"saved": True, "path": str(path)}


@router.get("/config/dashboards/parsed", summary="Get dashboards config")
async def get_dashboards_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``dashboards.toml`` parsed and normalised — ``{path, dashboards: {<id>: dict}}``.
    A missing file → an empty dict. Defaults are dropped so the wire payload stays terse."""
    path = Path(request.app.state.settings.dashboards.config_path)
    cfg = load_dashboards(path)
    # Nested ``{scope: {id: dashboard}}``; ``id`` + ``connector`` are the path keys, dropped here.
    # ``exclude_defaults=False`` — see :func:`get_charts_parsed`; the editor's live widget preview
    # needs every spec field present (e.g. ``aggregation`` default ``"sum"``).
    return {
        "path": str(path),
        "dashboards": {
            scope: {
                did: d.model_dump(exclude_defaults=False, exclude_none=True, exclude={"id", "connector"})
                for did, d in by_id.items()
            }
            for scope, by_id in cfg.dashboards.items()
        },
    }


class DashboardsBody(BaseModel):
    # Nested ``{scope: {id: dashboard-dict}}``.
    dashboards: dict[str, dict[str, Any]]


@router.put("/config/dashboards/parsed", summary="Update dashboards config")
async def put_dashboards_parsed(body: DashboardsBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate against :class:`DashboardsFile` (each dashboard's ``id`` injected from its key,
    each widget's discriminator + grid bounds enforced), then rewrite ``dashboards.toml`` via
    ``tomli-w``. Widget specs ARE discriminated unions (ChartWidget / KpiWidget / …) so
    ``exclude_defaults=False`` is the careful choice — same rationale as
    :func:`put_screens_parsed`. Does not reload."""
    try:
        validated = parse_dashboards({"dashboards": body.dashboards})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid dashboards: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid dashboards: {exc}") from exc

    normalized = validated.model_dump(exclude_defaults=False, exclude_none=True)
    # id + connector are the table keys (path-derived) — keep them out of the written body.
    for by_id in normalized.get("dashboards", {}).values():
        for d in by_id.values():
            d.pop("id", None)
            d.pop("connector", None)
    import tomli_w
    new_text = tomli_w.dumps(normalized)
    try:
        parse_dashboards(tomllib.loads(new_text))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting dashboards are invalid: {exc}") from exc

    path = Path(request.app.state.settings.dashboards.config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}
