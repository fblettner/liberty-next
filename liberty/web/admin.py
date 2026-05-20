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
from liberty.connectors.config import (
    ApiConnectorConfig,
    ConnectorsFile,
    PoolConfig,
    SqlConnectorConfig,
    load_connectors_file,
    parse_connectors,
)
from liberty.connectors.dictionary import (
    DictionaryFile,
    load_dictionary,
    parse_dictionary,
)
from liberty.framework_enums import FRAMEWORK_ENUMS
from liberty.licensing import verify_license
from liberty.menus import load_menus
from liberty.menus.config import MenusFile, parse_menus
from liberty.screens import Screen, load_screens
from liberty.screens.config import ScreensFile, parse_screens
from liberty.charts import load_charts
from liberty.charts.config import ChartsFile, parse_charts
from liberty.dashboards import load_dashboards
from liberty.dashboards.config import DashboardsFile, parse_dashboards
from liberty.web.rename import RenameError, rename_connector

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
    request.app.state.screens = load_screens(settings.screens.config_path)
    request.app.state.charts = load_charts(settings.charts.config_path)
    request.app.state.dashboards = load_dashboards(settings.dashboards.config_path)
    request.app.state.auth_backend = build_auth_backend(settings, new.pools)
    await old.aclose()
    return {
        "reloaded": True,
        "connectors": new.names(),
        "pools": new.pools.names(),
        "dictionary_entries": len(new.dictionary.entries),
        "menu_apps": list(request.app.state.menus.menus),
        "screen_apps": list(request.app.state.screens.screens),
        "charts": list(request.app.state.charts.charts),
        "dashboards": list(request.app.state.dashboards.dashboards),
        "license_mode": license_result.mode,
    }


# The legacy ``GET / PUT /config/connectors`` raw-TOML endpoints powered the Settings →
# Raw editor (Monaco over the file). Removed — every config section now has a structured
# builder (Pools / Connectors / Dictionary / Menus / Screens / Dashboards) that validates
# its input against the Pydantic schemas before writing, so the raw escape hatch was both
# redundant and a foot-gun (any typo there bypassed every per-section guard). The
# /config/<section>/parsed endpoints stay in place for the structured editors.


# ── ad-hoc SQL test-run for the per-query editor ──────────────────────────────────────────
class TestSqlBody(BaseModel):
    """Body for ``POST /admin/config/connectors/{c}/test-sql``."""

    sql: str
    params: dict[str, Any] | None = None
    max_rows: int | None = None
    # When True (the default) a write statement is rolled back after capturing the rowcount,
    # so the operator can verify it parses + see "would affect N rows" without mutating the DB.
    # Flip to False from the UI's "Commit" confirmation to actually write.
    dry_run: bool = True


@router.post("/config/connectors/{connector}/test-sql")
async def test_sql(connector: str, body: TestSqlBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Run a free-form SQL string against *connector*'s pool — powers the SQL editor's Run
    button in the config builder. **Superuser only** (already gated by the ``Superuser`` dep);
    bypasses the per-query ``writable`` flag (the operator is *editing* the query — they need to
    be able to try the write SQL they're typing). The same statement-type allow-list applies
    (``DROP``/``ALTER``/``TRUNCATE`` rejected). With ``dry_run = True`` (default), a write
    runs in a transaction that's rolled back on completion — the rowcount comes back, the DB
    is unchanged. SELECTs ignore ``dry_run`` and return the rows (capped by ``max_rows``).

    The connector must exist and be a SQL connector (404 otherwise — same convention as the
    other routes). A DB error becomes a 502; an unknown ``#SCHEMA.<X>#`` placeholder a 400.
    """
    # Late import — keeps the connectors-related imports out of the auth-only fast path.
    from liberty.connectors.base import ConnectorError
    from sqlalchemy.exc import SQLAlchemyError
    from liberty.web.errors import http_for_connector_error

    connectors = request.app.state.connectors
    try:
        sql_conn = connectors.sql(connector)  # UnknownConnectorError → 404; wrong-type → 404
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    try:
        result = await sql_conn.test_run(
            body.sql, body.params or {}, max_rows=body.max_rows, dry_run=body.dry_run,
        )
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"query failed: {type(exc).__name__}: {exc}",
        ) from exc
    return result.to_dict()


# ── structured config: the Phase-7 builders ───────────────────────────────────────────────
@router.get("/config/schema")
async def get_config_schema(request: Request, _: Superuser) -> dict[str, Any]:
    """JSON Schema of the structured-config models — the builder UI renders forms from it.
    Each carries its own ``$defs`` (QueryDef / ColumnHint / ParamDef / …) for the UI to resolve.
    ``framework_enums`` is v2's port of v1's ``ly_enum``-for-the-framework (the reusable dropdowns
    that power format/rules/dialect/method/… in the builder) — merged on the fly: the operator's
    ``dictionary.toml`` ``[framework_enums.*]`` section *replaces* the bundled entry for that id,
    so a new "Datasource Type" value can be added without a code change."""
    bundled: dict[str, dict[str, Any]] = {k: dict(v) for k, v in FRAMEWORK_ENUMS.items()}
    overrides = load_dictionary(_dictionary_path(request.app.state.settings)).framework_enums
    for enum_id, ed in overrides.items():
        bundled[enum_id] = {
            "label": ed.label or enum_id,
            "values": [
                {"value": v.value, "label": v.label or v.value, **({"l": v.l} if v.l else {})}
                for v in ed.values
            ],
        }
    return {
        "pool": PoolConfig.model_json_schema(),
        "sql": SqlConnectorConfig.model_json_schema(),
        "api": ApiConnectorConfig.model_json_schema(),
        "dictionary": DictionaryFile.model_json_schema(),
        "menus": MenusFile.model_json_schema(),
        "screens": ScreensFile.model_json_schema(),
        "charts": ChartsFile.model_json_schema(),
        "dashboards": DashboardsFile.model_json_schema(),
        "framework_enums": bundled,
    }


def _dictionary_path(settings: Any) -> Path:
    """Resolve where ``dictionary.toml`` lives — explicit ``[connectors] dictionary_path`` setting,
    else next to ``connectors.toml`` (matching what :func:`load_connectors` does)."""
    explicit = settings.connectors.dictionary_path
    if explicit:
        return Path(explicit)
    return Path(settings.connectors.config_path).with_name("dictionary.toml")


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


@router.get("/config/connectors/parsed")
async def get_connectors_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``[connectors.*]`` as ``{name: connector dict}`` (default-valued keys dropped)."""
    path = Path(request.app.state.settings.connectors.config_path)
    cfg = load_connectors_file(path)
    return {"path": str(path), "connectors": {name: c.model_dump(exclude_defaults=True) for name, c in cfg.connectors.items()}}


class ConnectorsParsedBody(BaseModel):
    connectors: dict[str, dict[str, Any]]


@router.put("/config/connectors/parsed")
async def put_connectors_parsed(body: ConnectorsParsedBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate each connector against the (discriminated) connector schema, then rewrite *only* the
    ``[connectors.*]`` tables of ``connectors.toml`` via ``tomlkit`` — the ``[pools.*]`` tables, the
    comments and the file's overall structure are preserved (a *changed* connector's own subtree is
    re-rendered though, so its inline `columns = [{…}]` arrays may become `[[…]]` tables — functionally
    identical; review in git). Re-validates the whole resulting file before writing. Does not reload."""
    for name, raw in body.connectors.items():
        try:
            ConnectorsFile.model_validate({"connectors": {name: raw}})
        except ValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"connector {name!r}: {exc}") from exc

    path = Path(request.app.state.settings.connectors.config_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    conns = doc.get("connectors")
    if conns is None:
        conns = tomlkit.table(is_super_table=True)
        doc["connectors"] = conns
    for stale in [n for n in list(conns.keys()) if n not in body.connectors]:
        del conns[stale]
    for name, vals in body.connectors.items():
        conns[name] = vals  # replace wholesale — tomlkit re-renders this connector's subtree

    new_text = tomlkit.dumps(doc)
    try:
        parse_connectors(tomllib.loads(new_text))   # belt-and-braces: the whole file must still parse
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting config is invalid: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


@router.get("/config/dictionary/parsed")
async def get_dictionary_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``dictionary.toml`` parsed and normalised — ``{path, dictionary: {default_language,
    entries, enums, lookups, connectors: {<name>: {entries, enums, lookups}}}}``. A missing file →
    an empty dictionary. Default-valued keys are dropped so the wire payload stays terse."""
    path = _dictionary_path(request.app.state.settings)
    cfg = load_dictionary(path)
    return {"path": str(path), "dictionary": cfg.model_dump(exclude_defaults=True)}


class DictionaryBody(BaseModel):
    dictionary: dict[str, Any]


@router.put("/config/dictionary/parsed")
async def put_dictionary_parsed(body: DictionaryBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate the submitted dict against :class:`DictionaryFile`, then rewrite
    ``dictionary.toml`` from scratch via ``tomli-w``.

    **Why not tomlkit here.** ``tomlkit.parse`` is O(n²)-ish on big nested-table files and
    takes ~2 minutes on a 5 k-line / 153 kB ``dictionary.toml`` (525 entries × 3 connectors +
    sequences + enums + lookups + per-language translations). The builder POSTs the *full*
    dictionary on every Save and waits on the response — at 2 minutes per call the UI looks
    like an infinite loop, the browser tab gives up, and the operator can't recover.
    ``tomli-w`` rewrites the same file in ~6 ms.

    Cost: comments / formatting outside the migrated sections are not preserved. The
    dictionary is **generated** content (``liberty-migrate dictionary``) and round-trips
    through this builder — there's no operator hand-edit comments to protect. The other
    PUT endpoints (pools / connectors / menus / screens) keep ``tomlkit`` because those
    files *do* often carry operator comments and are smaller (parse stays sub-second).

    Re-parses the resulting text via :func:`parse_dictionary` before writing as a
    belt-and-braces guard. Does not reload — call ``POST /admin/reload`` afterwards.
    """
    try:
        validated = DictionaryFile.model_validate(body.dictionary)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid dictionary: {exc}") from exc
    normalized = validated.model_dump(exclude_defaults=True)

    import tomli_w
    new_text = tomli_w.dumps(normalized)
    try:
        parse_dictionary(tomllib.loads(new_text))   # belt-and-braces re-validation
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting dictionary is invalid: {exc}") from exc

    path = _dictionary_path(request.app.state.settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


@router.get("/config/menus/parsed")
async def get_menus_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``menus.toml`` parsed and normalised — ``{path, menus: {<app>: AppMenu dict}}``.
    A missing file → an empty menu set. Default-valued keys are dropped."""
    path = Path(request.app.state.settings.menus.config_path)
    cfg = load_menus(path)
    return {"path": str(path), "menus": {name: app.model_dump(exclude_defaults=True) for name, app in cfg.menus.items()}}


class MenusBody(BaseModel):
    menus: dict[str, dict[str, Any]]


@router.put("/config/menus/parsed")
async def put_menus_parsed(body: MenusBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate each app against :class:`AppMenu` (which enforces the v1-style invariants —
    unique ids, parents exist, no cycles, folder-vs-leaf shape), then rewrite ``menus.toml`` via
    ``tomlkit`` — the top-level ``[menus]`` table is replaced wholesale (flat items round-trip
    cleanly through ``tomli-w``-style array-of-tables, which is what `tomlkit` re-renders).
    Re-parses the resulting file before writing. Does not reload — call ``POST /admin/reload``."""
    try:
        MenusFile.model_validate({"menus": body.menus})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid menus: {exc}") from exc

    path = Path(request.app.state.settings.menus.config_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    # Replace the whole `[menus]` table — items are array-of-tables under each app, easier to
    # re-render in one shot than to surgically diff.
    if body.menus:
        doc["menus"] = body.menus
    elif "menus" in doc:
        del doc["menus"]

    new_text = tomlkit.dumps(doc)
    try:
        parse_menus(tomllib.loads(new_text))   # belt-and-braces re-validation
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting menus are invalid: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


@router.get("/config/screens/parsed")
async def get_screens_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``screens.toml`` parsed and normalised — ``{path, screens: {<app>: {<screen_id>:
    Screen dict}}}``. A missing file → an empty screen set. Default-valued keys are dropped (terser
    JSON), **except** the ``type`` discriminator on each tab / action variant which is re-injected
    after the dump — Pydantic's ``exclude_defaults=True`` would otherwise strip it (the Literal
    discriminator's only-allowed value equals its default), leaving the frontend unable to tell
    nested_form / nested_table tabs from plain form ones AND the PUT round-trip unable to validate
    (extra fields on FormTab → 422)."""
    path = Path(request.app.state.settings.screens.config_path)
    cfg = load_screens(path)
    return {
        "path": str(path),
        "screens": {
            app: {sid: _dump_screen(s) for sid, s in screens.items()}
            for app, screens in cfg.screens.items()
        },
    }


def _reinject_action_type(a_dict: dict[str, Any], a_model: Any) -> None:
    """Re-inject the discriminator on an Action dict + every nested sub-list (so the frontend
    union parser picks the right variant when round-tripping). ``model_dump(exclude_defaults=True)``
    strips ``type`` because every variant's ``type: Literal[X] = X`` matches its own default —
    without this fix, an :class:`IfAction` reaches the builder as ``{id, condition, then_steps,
    else_steps}`` with no ``type`` field, and the frontend defaults the type picker to ``run_query``.

    Slice 4d added :class:`ChainAction.steps` / :class:`IfAction.then_steps` / ``else_steps`` /
    :class:`LoopAction.steps` — every one of those is a nested ``list[Action]`` that needs the
    same treatment recursively."""
    a_dict["type"] = a_model.type
    # Recurse into the four step-list-bearing variants. The model attribute name matches the
    # dict key, so a single ``getattr + dict.get`` zip covers each case.
    for nested_key in ("steps", "then_steps", "else_steps"):
        nested_list = a_dict.get(nested_key)
        nested_models = getattr(a_model, nested_key, None)
        if not nested_list or not nested_models:
            continue
        for sub_dict, sub_model in zip(nested_list, nested_models):
            _reinject_action_type(sub_dict, sub_model)


def _dump_screen(s: Screen) -> dict[str, Any]:
    """Default-stripped model dump + re-injected discriminators for every tab / action."""
    d = s.model_dump(exclude_defaults=True)
    # Tabs — discriminator on each ScreenTab variant.
    if s.dialog is not None:
        for tab_dict, tab_model in zip(d.get("dialog", {}).get("tabs", []), s.dialog.tabs):
            tab_dict["type"] = tab_model.type
            # Per-tab actions — recursive so nested ChainAction.steps / IfAction.then_steps /
            # else_steps / LoopAction.steps all keep their discriminators.
            for a_dict, a_model in zip(tab_dict.get("actions", []), tab_model.actions):
                _reinject_action_type(a_dict, a_model)
        # Dialog-level hook chains.
        for hook in ("on_load", "on_save", "on_cancel"):
            for a_dict, a_model in zip(d.get("dialog", {}).get(hook, []), getattr(s.dialog, hook, [])):
                _reinject_action_type(a_dict, a_model)
    # Screen-level action lists.
    for hook in ("actions", "row_menu", "on_insert", "on_update", "on_delete"):
        for a_dict, a_model in zip(d.get(hook, []), getattr(s, hook, [])):
            _reinject_action_type(a_dict, a_model)
    # Fold ``screen.key_columns`` (the old flat list — what pre-Phase-3 migration emitted
    # and what hand-edited screens may still use) into each matching column hint as
    # ``key: True``. The Visual Designer's Columns tab reads ``column.key`` per column;
    # without this fold, an existing ``key_columns = [...]`` line would silently disappear
    # from the UI on load. Saving back through PUT then writes ``key: true`` on the columns
    # and drops the legacy list — file converges to the new shape after one round-trip.
    if s.key_columns:
        # Case-insensitive match, preserve the operator's original case for any leftover.
        key_set = {k.upper() for k in s.key_columns}
        dumped_cols = d.get("columns")
        if isinstance(dumped_cols, list):
            matched: set[str] = set()
            for c in dumped_cols:
                if isinstance(c, dict):
                    nm = str(c.get("name", "")).upper()
                    if nm in key_set:
                        c["key"] = True
                        matched.add(nm)
            leftover = [k for k in s.key_columns if k.upper() not in matched]
            if leftover:
                d["key_columns"] = leftover     # unmatched keys keep the explicit list
            else:
                d.pop("key_columns", None)       # all keys landed on column hints — clean
    return d


class ScreensBody(BaseModel):
    screens: dict[str, dict[str, dict[str, Any]]]


@router.put("/config/screens/parsed")
async def put_screens_parsed(body: ScreensBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate the submitted dict against :class:`ScreensFile` (every screen's ``id`` injected from
    its dict key, dialog/tabs/fields/param-binds round-trip cleanly), then rewrite ``screens.toml``
    via ``tomlkit`` (replacing the top-level ``[screens]`` table wholesale — screens are nested
    array-of-table-like under each app and re-rendering them in one shot is cleanest). Re-parses
    the resulting file before writing. Does not reload — call ``POST /admin/reload`` afterwards."""
    try:
        # parse_screens injects each screen's `id` from its dict key — most operators won't repeat
        # it in TOML, and the wire payload follows the same convention. Then the model_validator
        # enforces that an explicit `id` (if present) matches its key.
        parse_screens({"screens": body.screens})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid screens: {exc}") from exc
    except ValueError as exc:                                # id != key, etc.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid screens: {exc}") from exc

    path = Path(request.app.state.settings.screens.config_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    if body.screens:
        doc["screens"] = body.screens
    elif "screens" in doc:
        del doc["screens"]

    new_text = tomlkit.dumps(doc)
    try:
        parse_screens(tomllib.loads(new_text))   # belt-and-braces re-validation
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting screens are invalid: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


@router.get("/config/charts/parsed")
async def get_charts_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``charts.toml`` parsed and normalised — ``{path, charts: {<id>: ChartConfig
    dict}}``. A missing file → an empty chart set. Default-valued keys are dropped (so a saved
    chart with all spec defaults round-trips cleanly without empty noise)."""
    path = Path(request.app.state.settings.charts.config_path)
    cfg = load_charts(path)
    return {
        "path": str(path),
        "charts": {cid: c.model_dump(exclude_defaults=True, exclude_none=True) for cid, c in cfg.charts.items()},
    }


class ChartsBody(BaseModel):
    charts: dict[str, dict[str, Any]]


@router.put("/config/charts/parsed")
async def put_charts_parsed(body: ChartsBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate the submitted dict against :class:`ChartsFile` (each chart's ``id`` injected from
    its dict key), then rewrite ``charts.toml`` via ``tomlkit`` (replacing the top-level ``[charts]``
    table wholesale). Re-parses the resulting file before writing. Does not reload — call ``POST
    /admin/reload`` afterwards. Same shape as the other ``/admin/config/<name>/parsed`` endpoints."""
    try:
        parse_charts({"charts": body.charts})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid charts: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid charts: {exc}") from exc

    path = Path(request.app.state.settings.charts.config_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    if body.charts:
        doc["charts"] = body.charts
    elif "charts" in doc:
        del doc["charts"]

    new_text = tomlkit.dumps(doc)
    try:
        parse_charts(tomllib.loads(new_text))   # belt-and-braces re-validation
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting charts are invalid: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


@router.get("/config/dashboards/parsed")
async def get_dashboards_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``dashboards.toml`` parsed and normalised — ``{path, dashboards: {<id>: dict}}``.
    A missing file → an empty dict. Defaults are dropped so the wire payload stays terse."""
    path = Path(request.app.state.settings.dashboards.config_path)
    cfg = load_dashboards(path)
    return {
        "path": str(path),
        "dashboards": {did: d.model_dump(exclude_defaults=True, exclude_none=True) for did, d in cfg.dashboards.items()},
    }


class DashboardsBody(BaseModel):
    dashboards: dict[str, dict[str, Any]]


@router.put("/config/dashboards/parsed")
async def put_dashboards_parsed(body: DashboardsBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate against :class:`DashboardsFile` (each dashboard's ``id`` injected from its key,
    each widget's discriminator + grid bounds enforced), then rewrite ``dashboards.toml`` via
    ``tomlkit`` — same pattern as ``charts/parsed``. Does not reload — call ``POST /admin/reload``."""
    try:
        parse_dashboards({"dashboards": body.dashboards})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid dashboards: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid dashboards: {exc}") from exc

    path = Path(request.app.state.settings.dashboards.config_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    if body.dashboards:
        doc["dashboards"] = body.dashboards
    elif "dashboards" in doc:
        del doc["dashboards"]

    new_text = tomlkit.dumps(doc)
    try:
        parse_dashboards(tomllib.loads(new_text))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting dashboards are invalid: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


# ── rename top-level keys (Phase-7 loose ends) ─────────────────────────────────────────────


class RenameBody(BaseModel):
    """Payload for ``POST /admin/config/rename``. ``kind`` switches over the supported rename
    flavours — currently ``"connector"`` (the highest-value case); ``"sequence"`` /
    ``"lookup"`` / ``"screen_app"`` are pending follow-ups."""

    kind: str
    old_name: str
    new_name: str


@router.post("/config/rename")
async def rename_top_level_key(body: RenameBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Rename a top-level config key + every cross-file reference in one atomic pass.

    The structured builders edit each file's body wholesale via tomlkit, but a connector name
    (or future: a sequence / lookup / screen-app key) is referenced from several files.
    Renaming by hand means hunting every ``connector = "<old>"`` in screens / menus / dictionary
    / dashboards / charts — error-prone and easy to miss a deeply-nested step in an action
    chain. This endpoint walks every affected file, rewrites the references via
    :mod:`liberty.web.rename`, validates each rewritten doc against its Pydantic schema, then
    writes them all in one batch. On any validation failure nothing is written.

    Does **not** reload — the caller calls ``POST /admin/reload`` after to apply changes
    everywhere (in-flight requests still see the old registry until they finish)."""
    settings = request.app.state.settings
    if body.kind == "connector":
        try:
            result = rename_connector(
                body.old_name, body.new_name,
                connectors_path=Path(settings.connectors.config_path),
                screens_path=Path(settings.screens.config_path),
                menus_path=Path(settings.menus.config_path),
                dictionary_path=_dictionary_path(settings),
                dashboards_path=Path(settings.dashboards.config_path),
                charts_path=Path(settings.charts.config_path),
            )
        except RenameError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        return result.to_dict()
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"rename kind {body.kind!r} not supported yet — only 'connector' for now",
    )
