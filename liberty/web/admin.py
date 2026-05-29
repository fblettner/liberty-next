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
from liberty.crypto import CryptoError, encrypt, is_encrypted
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
from liberty.theme import font_choices, load_theme, parse_theme, preset_choices, resolve_theme
from liberty.web.clone import CloneError, clone_app, delete_app
from liberty.web.rename import (
    RenameError,
    rename_connector,
    rename_dictionary_entry,
    rename_lookup,
    rename_query,
    rename_screen_app,
    rename_sequence,
    rename_table,
)

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

    # nomaflow (Phase 13a chunk 3): also reload jobs.toml and reconcile the
    # live scheduler (add/remove/reschedule cron triggers per the diff). The
    # JobDatabase + JobRunner keep using the new connectors.pools — they don't
    # need rebuilding because the wiring.NomaflowComponents.db points at the
    # pool by name, not by engine reference. New jobs requesting executors for
    # step types that don't have one (python/ldap/http until those land) will
    # fail at run time, not here, with the clear "no executor registered"
    # message — same behaviour as before this reload.
    jobs_components = getattr(request.app.state, "jobs", None)
    nomaflow_reloaded: dict[str, Any] = {"enabled": False}
    if jobs_components is not None:
        from liberty.jobs.wiring import hot_reload_registry
        new_registry = await hot_reload_registry(jobs_components, settings)
        nomaflow_reloaded = {
            "enabled": True,
            "jobs": [j.id for j in new_registry.jobs()],
            "scheduled_jobs": jobs_components.scheduler.scheduled_job_ids,
        }

    await old.aclose()
    return {
        "reloaded": True,
        "connectors": new.names(),
        "pools": new.pools.names(),
        "dictionary_entries": len(new.dictionary.entries),
        "menu_apps": list(request.app.state.menus.menus),
        "screen_apps": list(request.app.state.screens.screens),
        "charts": [f"{s}.{c}" for s, c, _ in request.app.state.charts.iter_charts()],
        "dashboards": [d.qualified_id for _, _, d in request.app.state.dashboards.iter_dashboards()],
        "license_mode": license_result.mode,
        "nomaflow": nomaflow_reloaded,
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


# Password-shaped fields on each config kind that must be encrypted before they land on disk
# (the UI fetches the ENC: value, edits whatever, sends back; the user typing a new plaintext
# password is the case that matters). ``encrypt`` is idempotent so re-encrypting an already
# ENC: value is a no-op — safe to apply on every save.
_POOL_PASSWORD_FIELDS = ("password",)
_API_CONNECTOR_PASSWORD_FIELDS = ("auth_password", "auth_token")


def _encrypt_secret_fields(
    data: dict[str, Any], fields: tuple[str, ...], master_key: str, *, context: str,
) -> dict[str, Any]:
    """Wrap any non-empty, non-ENC: value at ``fields[*]`` with :func:`encrypt`.

    Raises HTTPException(422) if a plaintext is present but no master key is configured —
    silently storing plaintext would be the *exact* bug this helper exists to prevent.
    ``context`` flows into the error message so the operator knows which entry tripped it.
    """
    out = dict(data)
    for f in fields:
        v = out.get(f)
        if not isinstance(v, str) or not v or is_encrypted(v):
            continue
        try:
            out[f] = encrypt(v, master_key)
        except CryptoError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{context}: cannot encrypt {f!r} — {exc}",
            ) from exc
    return out


@router.put("/config/pools")
async def put_pools_config(body: PoolsBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate each pool against :class:`PoolConfig`, then rewrite *only* the ``[pools.*]`` tables
    of ``connectors.toml`` (everything else — comments, the ``[connectors.*]`` tables, formatting —
    is left byte-for-byte intact via ``tomlkit``). Does not reload — call ``POST /admin/reload``.

    Pool passwords are encrypted with the configured master key before write — a UI edit that
    typed a plaintext password lands on disk as ``ENC:…``. Already-ENC: values pass through
    unchanged (the operator probably didn't touch the field; ``encrypt`` is idempotent)."""
    settings = request.app.state.settings
    master_key = settings.crypto.master_key
    # validate + normalise (drop default-valued keys so the file stays terse)
    new_pools: dict[str, dict[str, Any]] = {}
    for name, raw in body.pools.items():
        try:
            normalised = PoolConfig.model_validate(raw).model_dump(exclude_defaults=True)
        except ValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"pool {name!r}: {exc}") from exc
        new_pools[name] = _encrypt_secret_fields(
            normalised, _POOL_PASSWORD_FIELDS, master_key, context=f"pool {name!r}",
        )

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
    identical; review in git). Re-validates the whole resulting file before writing. Does not reload.

    API connector secrets (``auth_password``, ``auth_token``) are encrypted before write — same
    contract as pools (idempotent on already-ENC: values; raises 422 if a plaintext secret was
    typed but no master key is configured, since silently storing plaintext is the worst outcome)."""
    master_key = request.app.state.settings.crypto.master_key
    for name, raw in body.connectors.items():
        try:
            ConnectorsFile.model_validate({"connectors": {name: raw}})
        except ValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"connector {name!r}: {exc}") from exc
        if raw.get("type") == "api":
            # Mutate in place so the tomlkit write below picks up the encrypted values.
            body.connectors[name] = _encrypt_secret_fields(
                raw, _API_CONNECTOR_PASSWORD_FIELDS, master_key, context=f"connector {name!r}",
            )

    # Build the new doc by tomllib-parsing the existing file (FAST — built-in C parser, O(n)),
    # taking its ``[pools.*]`` subtree verbatim, and pairing it with the validated connectors.
    # This avoids the slow ``tomlkit.parse`` + ``dumps`` round-trip (~tens of seconds on a
    # 3 k-line connectors.toml after a namespace clone). Same perf fix as
    # :func:`put_screens_parsed` / :func:`put_dictionary_parsed`, with the extra wrinkle that
    # pools must round-trip even though they're not in the PUT body — they live in the same
    # file but are managed via the Pools editor, not this one.
    #
    # Cost: TOML-level comments + ordering on the [pools.*] subtree may shift (pool VALUES
    # round-trip byte-identically via tomllib → tomli_w; just the trailing comments / blank
    # lines / formatting can change). Connectors lose their inter-section comments too. Both
    # are auto-generated by the builders; operators don't hand-edit comments in practice.
    path = Path(request.app.state.settings.connectors.config_path)
    existing_doc: dict[str, Any] = {}
    if path.exists():
        try:
            existing_doc = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"existing connectors.toml is unparseable, refusing to overwrite: {exc}",
            ) from exc
    new_doc: dict[str, Any] = {}
    if "pools" in existing_doc:
        new_doc["pools"] = existing_doc["pools"]
    if body.connectors:
        new_doc["connectors"] = body.connectors

    import tomli_w
    new_text = tomli_w.dumps(new_doc)
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
    unique ids, parents exist, no cycles, folder-vs-leaf shape), then rewrite ``menus.toml``
    via ``tomli-w``. Same perf rationale as :func:`put_screens_parsed`: tomlkit round-trip is
    O(n²) on big nested files, painful at 1.5 k+ lines after a namespace clone. Re-parses the
    resulting text via :func:`parse_menus` before writing. Does not reload."""
    try:
        validated = MenusFile.model_validate({"menus": body.menus})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid menus: {exc}") from exc

    # ``exclude_defaults=False`` for the same reason as screens — menu items are not
    # discriminated unions today, but folder-vs-leaf detection in the validator relies on
    # ``type`` being explicitly present/absent. Safer to write everything than to chase
    # one-off field omissions.
    normalized = validated.model_dump(exclude_defaults=False, exclude_none=True)
    import tomli_w
    new_text = tomli_w.dumps(normalized)
    try:
        parse_menus(tomllib.loads(new_text))   # belt-and-braces re-validation
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting menus are invalid: {exc}") from exc

    path = Path(request.app.state.settings.menus.config_path)
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
    from scratch via ``tomli-w``.

    **Why not tomlkit here.** Same reason as
    :func:`put_dictionary_parsed`: ``tomlkit.parse`` + ``dumps`` is O(n²)-ish on big nested-table
    files (~minute-scale for a 16 k-line / 600 kB ``screens.toml`` once an operator clones a
    namespace — nomasx1 + nomasx1b each carry ~70 screens × dialog tabs × columns). The Screen
    Designer POSTs the *full* screens dict on every Save and waits on the response — at minutes
    per call the UI looks like an infinite loop, the browser tab gives up, and the operator can't
    recover. ``tomli-w`` rewrites the same file in milliseconds.

    Cost: comments / formatting between sections are not preserved. The screen file is
    **generated** content (Screen Designer + ``clone_app``); the few hand-written comments at
    the top of the file (a section divider above [screens.nomasx1.last_refresh] etc.) are nice
    but operators don't expect them to survive a UI save anyway — the round-trip through the
    visual builder has always normalised formatting.

    Re-parses the resulting text via :func:`parse_screens` before writing as a belt-and-braces
    guard. Does not reload — call ``POST /admin/reload`` afterwards."""
    try:
        # parse_screens injects each screen's `id` from its dict key — most operators won't repeat
        # it in TOML, and the wire payload follows the same convention. Then the model_validator
        # enforces that an explicit `id` (if present) matches its key.
        validated = parse_screens({"screens": body.screens})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid screens: {exc}") from exc
    except ValueError as exc:                                # id != key, etc.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid screens: {exc}") from exc

    # Round-trip the validated model. ``exclude_defaults=False`` is intentional — Action's
    # discriminator (``type``) carries default values on each variant (``navigate`` /
    # ``run_query`` / ``refresh`` / …); dropping defaults would strip those discriminators
    # and the re-parse below would fail with "Unable to extract tag using discriminator
    # 'type'". The file grows ~70% as a result (370 kB → 640 kB on a 208-screen nomasx1 +
    # nomasx1b doc) but the save speed gain — 33 s → 125 ms on that file in benchmarks —
    # makes the trade-off obviously right. ``exclude_none=True`` still trims explicit nulls.
    # The model wraps its data under the ``screens`` key (:class:`ScreensFile.screens`), so
    # the dump is shaped ``{"screens": {<app>: {<id>: …}}}`` and goes straight to tomli_w.
    normalized = validated.model_dump(exclude_defaults=False, exclude_none=True)

    import tomli_w
    new_text = tomli_w.dumps(normalized)
    try:
        parse_screens(tomllib.loads(new_text))   # belt-and-braces re-validation
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting screens are invalid: {exc}") from exc

    path = Path(request.app.state.settings.screens.config_path)
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
    # Nested ``{scope: {id: chart}}``; ``id`` + ``connector`` are the path keys, so they're dropped
    # from each body (re-injected by parse_charts on the way back in).
    return {
        "path": str(path),
        "charts": {
            scope: {
                cid: c.model_dump(exclude_defaults=True, exclude_none=True, exclude={"id", "connector"})
                for cid, c in by_id.items()
            }
            for scope, by_id in cfg.charts.items()
        },
    }


class ChartsBody(BaseModel):
    # Nested ``{scope: {id: chart-dict}}``.
    charts: dict[str, dict[str, Any]]


@router.put("/config/charts/parsed")
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
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


@router.get("/config/dashboards/parsed")
async def get_dashboards_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``dashboards.toml`` parsed and normalised — ``{path, dashboards: {<id>: dict}}``.
    A missing file → an empty dict. Defaults are dropped so the wire payload stays terse."""
    path = Path(request.app.state.settings.dashboards.config_path)
    cfg = load_dashboards(path)
    # Nested ``{scope: {id: dashboard}}``; ``id`` + ``connector`` are the path keys, dropped here.
    return {
        "path": str(path),
        "dashboards": {
            scope: {
                did: d.model_dump(exclude_defaults=True, exclude_none=True, exclude={"id", "connector"})
                for did, d in by_id.items()
            }
            for scope, by_id in cfg.dashboards.items()
        },
    }


class DashboardsBody(BaseModel):
    # Nested ``{scope: {id: dashboard-dict}}``.
    dashboards: dict[str, dict[str, Any]]


@router.put("/config/dashboards/parsed")
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
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


# ── theme.toml (per-deployment branding — see liberty/theme.py) ───────────────────────────────


@router.get("/config/theme/parsed")
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


@router.put("/config/theme/parsed")
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
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path), "resolved": resolve_theme(validated.theme)}


# ── nomaflow jobs.toml (Phase 13 — see docs/NOMAFLOW-UI.md §5) ────────────────────────────────
# Unlike pools/connectors/charts/dashboards (dict-keyed sections), jobs.toml's top level is a
# ``[[jobs]]`` *array of tables*. So the wire shape is a list, not a {key: value} map: GET
# returns ``{path, jobs: [...]}``, PUT accepts ``{jobs: [...]}``. The GET returns *merged* jobs
# (``[jobs.step_defaults]`` expanded into each step) — the editor works on fully-expanded steps;
# see NOMAFLOW-UI.md §4.


@router.get("/config/jobs/parsed")
async def get_jobs_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``jobs.toml`` parsed and normalised — ``{path, meta, jobs: [<Job dict>...]}``.

    A missing file → an empty job list (+ default meta). ``step_defaults`` is merged into
    each step (the loader does this); default-valued keys are dropped so the wire payload
    stays terse. ``by_alias=True`` so the ``schema`` field on SQL endpoints round-trips as
    ``schema`` (the model's Python attribute is ``schema_`` — see
    :class:`liberty.jobs.schema.SqlEndpoint`).

    ``meta`` carries the file-level settings — today the version + the retention policy.
    Returned with defaults included (``exclude_defaults=False``) so the UI sees the
    EFFECTIVE policy (30 / 100 / 60) rather than ``{}`` for a meta block that's still
    using the schema defaults — without this, the RetentionPanel can't initialise its
    inputs to the values the scheduler is actually using."""
    from liberty.jobs import load_jobs

    path = Path(request.app.state.settings.jobs.config_path)
    registry = load_jobs(path)
    return {
        "path": str(path),
        # Defaults included on purpose — see docstring. Without this the panel inputs
        # would be blank for a fresh install where the operator hasn't customised meta.
        "meta": registry.config.meta.model_dump(),
        "jobs": [
            j.model_dump(by_alias=True, exclude_defaults=True, exclude_none=True)
            for j in registry.jobs()
        ],
    }


class JobsBody(BaseModel):
    jobs: list[dict[str, Any]]
    # Optional so existing callers that only PUT ``jobs`` (the JobEditor save flow)
    # keep working unchanged — when omitted, the file's existing [meta] block is
    # preserved. The RetentionPanel always sends meta so its edits land.
    meta: dict[str, Any] | None = None


@router.put("/config/jobs/parsed")
async def put_jobs_parsed(body: JobsBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate the submitted jobs (+ optional meta) against :class:`liberty.jobs.JobsFile`,
    then rewrite ``jobs.toml`` via ``tomlkit`` — preserving comments + formatting on
    untouched sections. Re-parses the result before writing. Does not reload — call
    ``POST /admin/reload`` afterwards to apply.

    The body carries already-expanded steps (no ``step_defaults``); ``Job``'s ``extra="forbid"``
    rejects a stray ``step_defaults`` key. Saving therefore normalises a hand-authored
    ``step_defaults`` block away — the documented behaviour (NOMAFLOW-UI.md §4).

    ``meta`` is optional: when present it REPLACES the file's existing ``[meta]`` block
    (so retention edits land); when absent the existing block is preserved. The JobEditor
    flow omits meta + the RetentionPanel sends it — neither steps on the other."""
    from liberty.jobs import JobsFile

    try:
        validate_payload: dict[str, Any] = {"jobs": body.jobs}
        if body.meta is not None:
            validate_payload["meta"] = body.meta
        JobsFile.model_validate(validate_payload)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid jobs: {exc}") from exc

    path = Path(request.app.state.settings.jobs.config_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    if body.jobs:
        doc["jobs"] = body.jobs
    elif "jobs" in doc:
        del doc["jobs"]
    # Replace the [meta] block when one was submitted. Assigning a plain dict lets
    # tomlkit lay it out as nested tables (``[meta]`` + ``[meta.retention]``) the
    # same way a hand-authored file would; the round-trip parse below catches any
    # malformed shape before it touches disk.
    if body.meta is not None:
        doc["meta"] = body.meta

    new_text = tomlkit.dumps(doc)
    try:
        # Validate the FULL re-parsed document, not just the jobs list — a meta-only
        # change wouldn't be caught by the jobs-only validate above.
        JobsFile.model_validate(tomllib.loads(new_text))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting jobs are invalid: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


# ── API connector test ──────────────────────────────────────────────────────────────────────


class ApiTestBody(BaseModel):
    """Submitted by the ConnectorsBuilder's Test button — the operator's *in-progress* API
    connector definition (may not be saved yet). The endpoint builds a temporary
    :class:`APIConnector` from it and runs the smallest meaningful test:

    * ``oauth2`` — POST the token endpoint, return the resolved token (or the error).
    * ``basic`` / ``bearer`` / ``api_key`` — no separate verifier; if the operator gave
      ``test_endpoint``, run that. Otherwise just confirm the auth headers build cleanly.
    * ``none`` — nothing to test; succeed immediately.

    The connector is built in-memory, used once, then disposed; nothing touches disk.
    """

    config: dict[str, Any]                                       # one ApiConnectorConfig dict
    test_endpoint: str | None = None                             # optional: name of an endpoint to fire as a smoke call
    params: dict[str, Any] | None = None                         # placeholder values forwarded to the smoke call


@router.post("/config/api/test")
async def test_api_connector(body: ApiTestBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Smoke-test the operator's API connector definition without saving. Two modes:

    * **OAuth2 token fetch** — the most useful test, since it confirms the token endpoint +
      auth_username/password + auth_token_body all work in concert. Returns the resolved
      token (truncated for display) or the error from the token endpoint.
    * **Endpoint smoke call** — if ``test_endpoint`` is provided AND it exists on the
      connector, fire it with no params and return the result. Useful for basic / bearer /
      api_key auth where the token fetch isn't a separate step.
    """
    from liberty.connectors.api import APIConnector
    from liberty.connectors.config import ApiConnectorConfig

    try:
        cfg = ApiConnectorConfig.model_validate(body.config)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid api connector config: {exc}") from exc

    settings = request.app.state.settings
    conn = APIConnector(
        "__test__", cfg,
        master_key=settings.crypto.master_key,
    )
    try:
        if cfg.auth_type == "oauth2":
            # Force a fresh fetch — the cache is per-connector-instance + we just made it,
            # but force=True keeps the behaviour explicit (no surprise on re-runs).
            token = await conn._get_token({}, force=True)
            if token is None:
                return {
                    "kind": "oauth2_token",
                    "success": False,
                    "error": "token endpoint did not return a token (check auth_token_endpoint / auth_token_body / auth_token_field).",
                }
            # Truncate for display — the full token isn't useful in the UI and may be sensitive.
            return {
                "kind": "oauth2_token",
                "success": True,
                "token_preview": (token[:12] + "…") if len(token) > 16 else token,
                "ttl_seconds": cfg.auth_token_ttl,
            }
        # No separate OAuth2 step. If the operator named a test endpoint, fire it — forwarding
        # any ``params`` they supplied at the Test tab.
        if body.test_endpoint:
            try:
                result = await conn.call(body.test_endpoint, body.params or {})
            except Exception as exc:                              # noqa: BLE001 — surface any error
                return {"kind": "smoke_call", "success": False, "error": f"call failed: {exc}"}
            return {
                "kind": "smoke_call",
                "success": result.success,
                "status_code": result.status_code,
                "url": result.url,
                "duration_ms": result.duration_ms,
                "error": result.error,
                "extracted": result.extracted,
                "mapped": result.mapped,
                "body": result.body[:4000] if isinstance(result.body, str) else result.body,
            }
        # Nothing to fire — auth headers build cleanly + the config validated; report so.
        return {
            "kind": "config_only",
            "success": True,
            "message": f"config is valid; pick an endpoint to fire a smoke call (auth_type={cfg.auth_type}).",
        }
    finally:
        await conn.aclose()


# ── rename top-level keys (Phase-7 loose ends) ─────────────────────────────────────────────


class RenameBody(BaseModel):
    """Payload for ``POST /admin/config/rename``. ``kind`` selects the rename flavour:

    * ``"connector"`` — top-level ``[connectors.<old>]`` in :file:`connectors.toml` + every
      cross-file ``connector = "<old>"`` reference (screens / menus / dictionary / dashboards
      / charts).
    * ``"sequence"`` / ``"lookup"`` — ``[sequences.<old>]`` / ``[lookups.<old>]`` in
      :file:`dictionary.toml` + the matching ``DictionaryEntry.rules_values`` references.
      ``scope`` optional: connector name to target ``[connectors.<scope>.sequences.<old>]`` etc.
    * ``"screen_app"`` — ``[screens.<old>]`` in :file:`screens.toml` + the matching
      ``[menus.<old>]`` in :file:`menus.toml` (if it exists). ``scope`` ignored.
    * ``"dictionary_entry"`` — ``[entries.<old>]`` in :file:`dictionary.toml` + every
      ``ColumnHint.dd`` / ``PromptField.dd`` in :file:`screens.toml` + ``SequenceDef.dd_id``
      and ``LookupDef.return_params`` references in the same scope. ``scope`` optional.
    * ``"query"`` — a query name in ``[[connectors.<scope>.queries]]`` + every reference scoped
      to that connector (screens' read/update/insert/delete_query + run_query/navigate/export,
      dictionary sequence/lookup ``query``, chart/widget ``query``, sql menu leaves' ``target``).
      ``scope`` is **required** (the connector).
    * ``"table"`` — a CRUD base: renames every existing ``<old>_<get|put|post|delete>`` slot +
      the screen bindings, in one pass. ``scope`` is **required** (the connector).
    """

    kind: str
    old_name: str
    new_name: str
    scope: str | None = None


@router.post("/config/rename")
async def rename_top_level_key(body: RenameBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Rename a top-level config key + every cross-file reference in one atomic pass.

    The structured builders edit each file's body wholesale via tomlkit, but a top-level key
    is referenced from several files. Renaming by hand means hunting every reference —
    error-prone and easy to miss a deeply-nested step in an action chain. This endpoint walks
    every affected file, rewrites the references via :mod:`liberty.web.rename`, validates each
    rewritten doc against its Pydantic schema, then writes them all in one batch. On any
    validation failure nothing is written.

    Does **not** reload — the caller calls ``POST /admin/reload`` after to apply changes
    everywhere (in-flight requests still see the old registry until they finish).
    """
    settings = request.app.state.settings
    try:
        if body.kind == "connector":
            result = rename_connector(
                body.old_name, body.new_name,
                connectors_path=Path(settings.connectors.config_path),
                screens_path=Path(settings.screens.config_path),
                menus_path=Path(settings.menus.config_path),
                dictionary_path=_dictionary_path(settings),
                dashboards_path=Path(settings.dashboards.config_path),
                charts_path=Path(settings.charts.config_path),
            )
        elif body.kind == "sequence":
            result = rename_sequence(
                body.old_name, body.new_name,
                dictionary_path=_dictionary_path(settings),
                scope=body.scope,
            )
        elif body.kind == "lookup":
            result = rename_lookup(
                body.old_name, body.new_name,
                dictionary_path=_dictionary_path(settings),
                scope=body.scope,
            )
        elif body.kind == "screen_app":
            result = rename_screen_app(
                body.old_name, body.new_name,
                screens_path=Path(settings.screens.config_path),
                menus_path=Path(settings.menus.config_path),
            )
        elif body.kind == "dictionary_entry":
            result = rename_dictionary_entry(
                body.old_name, body.new_name,
                dictionary_path=_dictionary_path(settings),
                screens_path=Path(settings.screens.config_path),
                scope=body.scope,
            )
        elif body.kind in ("query", "table"):
            if not body.scope:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"a {body.kind} rename requires `scope` (the connector name)")
            fn = rename_query if body.kind == "query" else rename_table
            result = fn(
                body.old_name, body.new_name,
                connector=body.scope,
                connectors_path=Path(settings.connectors.config_path),
                screens_path=Path(settings.screens.config_path),
                menus_path=Path(settings.menus.config_path),
                dictionary_path=_dictionary_path(settings),
                charts_path=Path(settings.charts.config_path),
                dashboards_path=Path(settings.dashboards.config_path),
            )
        else:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"rename kind {body.kind!r} not supported — one of: connector, sequence, lookup, screen_app, dictionary_entry, query, table",
            )
    except RenameError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return result.to_dict()


# ── clone an app namespace (cross-file duplication) ────────────────────────────────────────


class CloneAppBody(BaseModel):
    """Payload for ``POST /admin/config/clone-app``.

    Duplicates every per-app entry (connector / dictionary overlay / menu / screens) under
    ``new_app`` with ``connector`` field values inside the clone rewritten to point at
    ``new_app``. The cloned connector's ``pool`` is set to ``new_pool`` — which must
    already exist in :file:`connectors.toml` (create the database + the pool entry first;
    we refuse to clone pointing at a non-existent pool).

    The regression-testing path: create ``nomasx1b`` (CREATE DATABASE + add the pool +
    set a password via the UI), then clone ``nomasx1`` → ``nomasx1b`` to get a parallel
    app namespace pointing at the new DB. Run agent jobs against it, diff vs the existing
    ``nomasx1``.
    """

    source_app: str
    new_app: str
    new_pool: str


class DeleteAppBody(BaseModel):
    """Payload for ``POST /admin/config/delete-app``.

    Removes every per-app entry (connector / dictionary overlay / menu / screens) for
    *app*. The cross-file inverse of clone-app; same files touched, opposite direction.
    Pool deletion is NOT included — pools are managed separately via Settings → Pools
    and may be shared between apps.
    """

    app: str


@router.post("/config/delete-app")
async def delete_app_endpoint(body: DeleteAppBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Remove a whole app namespace across the 4 config files. Surgical text-edit on
    each file — fast even on big dictionary.toml (preserves comments + formatting in
    every section that isn't being deleted). Atomic: validates the rewritten text via
    Pydantic before any file is touched; if any fails, nothing changes on disk.

    Does **not** reload — the caller calls ``POST /admin/reload`` after to apply changes
    everywhere (in-flight requests still see the old registry until they finish)."""
    settings = request.app.state.settings
    try:
        result = delete_app(
            body.app,
            connectors_path=Path(settings.connectors.config_path),
            dictionary_path=_dictionary_path(settings),
            menus_path=Path(settings.menus.config_path),
            screens_path=Path(settings.screens.config_path),
            charts_path=Path(settings.charts.config_path),
            dashboards_path=Path(settings.dashboards.config_path),
        )
    except CloneError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return result.to_dict()


@router.post("/config/clone-app")
async def clone_app_endpoint(body: CloneAppBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Clone a whole app namespace across :file:`connectors.toml`, :file:`dictionary.toml`,
    :file:`menus.toml`, and :file:`screens.toml`. Atomic: validates every rewritten doc
    against its Pydantic schema before writing; if any fails, nothing changes on disk.

    Does **not** reload — the caller calls ``POST /admin/reload`` after to apply changes
    everywhere (in-flight requests still see the old registry until they finish)."""
    settings = request.app.state.settings
    try:
        result = clone_app(
            body.source_app, body.new_app, new_pool=body.new_pool,
            connectors_path=Path(settings.connectors.config_path),
            dictionary_path=_dictionary_path(settings),
            menus_path=Path(settings.menus.config_path),
            screens_path=Path(settings.screens.config_path),
            charts_path=Path(settings.charts.config_path),
            dashboards_path=Path(settings.dashboards.config_path),
        )
    except CloneError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return result.to_dict()
