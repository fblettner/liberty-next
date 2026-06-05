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
from liberty.licensing import ALWAYS_LICENSED_CONNECTORS, verify_license
from liberty.menus import load_menus
from liberty.menus.config import MenusFile, parse_menus
from liberty.screens import Screen, load_screens
from liberty.screens.config import ScreensFile, parse_screens
from liberty.actions import load_actions
from liberty.actions.config import SharedActionsFile, parse_actions
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
    rename_screen,
    rename_screen_app,
    rename_sequence,
    rename_table,
)

router = APIRouter(prefix="/admin", tags=["admin"])

Superuser = Annotated[Principal, Depends(require_superuser)]


@router.post("/reload", summary="Reload config")
async def reload_connectors(request: Request, _: Superuser) -> dict[str, object]:
    settings = request.app.state.settings
    old = request.app.state.connectors
    license_result = verify_license(settings.license.key, master_key=settings.crypto.master_key)
    new = load_connectors(
        settings.connectors.config_path,
        dictionary_path=settings.connectors.dictionary_path,
        master_key=settings.crypto.master_key,
        license=license_result,
        default_language=settings.app.default_language,
    )
    request.app.state.license = license_result
    request.app.state.connectors = new
    request.app.state.menus = load_menus(settings.menus.config_path)
    request.app.state.screens = load_screens(settings.screens.config_path)
    request.app.state.charts = load_charts(settings.charts.config_path)
    request.app.state.actions = load_actions(settings.actions.config_path)
    request.app.state.dashboards = load_dashboards(settings.dashboards.config_path)
    request.app.state.auth_backend = build_auth_backend(settings, new.pools)

    # nomaflow (Phase 13a chunk 3): also reload jobs.toml and reconcile the
    # live scheduler (add/remove/reschedule cron triggers per the diff). The
    # JobDatabase looks up pools by name through the new registry on every fire,
    # so it doesn't need rebuilding — but the per-step-type executors capture
    # ``connectors`` at construction time, so they DO need rebuilding to see the
    # new registry. Without that, UI queries pick up the new pool credentials
    # while jobs that touch the same pool fail with stale ones.
    jobs_components = getattr(request.app.state, "jobs", None)
    nomaflow_reloaded: dict[str, Any] = {"enabled": False}
    if jobs_components is not None:
        from liberty.jobs.wiring import hot_reload_registry, refresh_executors
        new_registry = await hot_reload_registry(jobs_components, settings)
        refresh_executors(jobs_components, connectors=new, settings=settings)
        nomaflow_reloaded = {
            "enabled": True,
            "jobs": [j.id for j in new_registry.jobs()],
            "scheduled_jobs": jobs_components.scheduler.scheduled_job_ids,
        }

    # Reports — license rotation that newly covers a plugin (or stops covering
    # one) immediately changes the registry; rebuild here so /api/reports
    # reflects the new state without a process restart.
    from liberty.reports.wiring import refresh_reports
    new_reports = refresh_reports(request.app, settings, license_result)

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
        "reports": new_reports.names(),
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


@router.post("/config/connectors/{connector}/test-sql", summary="Test SQL query")
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
@router.get("/config/schema", summary="Get config JSON Schemas")
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
        "actions": SharedActionsFile.model_json_schema(),
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


@router.get("/config/pools", summary="Get pools")
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


@router.put("/config/pools", summary="Update pools")
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


@router.get("/config/integrity", summary="Config integrity check")
async def config_integrity(request: Request, _: Superuser) -> dict[str, Any]:
    """Walk every cross-reference in the loaded config and report broken references (errors) +
    smells (unused connectors / orphan screens — warnings). Runs against the in-memory registries
    (``app.state``), so it reflects the last ``/admin/reload``. Feeds Settings → Integrity."""
    from liberty.web.integrity import check_integrity
    issues = check_integrity(request.app.state)
    return {
        "issues": [i.to_dict() for i in issues],
        "errors": sum(1 for i in issues if i.severity == "error"),
        "warnings": sum(1 for i in issues if i.severity == "warning"),
    }


@router.get("/config/connectors/parsed", summary="Get connectors")
async def get_connectors_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``[connectors.*]`` as ``{name: connector dict}`` (default-valued keys dropped).
    ``always_licensed`` lists connector names that the framework gates regardless of their
    on-disk ``licensed`` flag — the editor uses this to lock the corresponding checkbox."""
    path = Path(request.app.state.settings.connectors.config_path)
    cfg = load_connectors_file(path)
    return {
        "path": str(path),
        "connectors": {name: c.model_dump(exclude_defaults=True) for name, c in cfg.connectors.items()},
        "always_licensed": sorted(ALWAYS_LICENSED_CONNECTORS),
    }


class ConnectorsParsedBody(BaseModel):
    connectors: dict[str, dict[str, Any]]


@router.put("/config/connectors/parsed", summary="Update connectors")
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
    # Migrate legacy-shape connector bodies in place before validation / write. A stale UI
    # client could still POST flat-``queries``-with-``type`` payloads; the loader's same
    # migration normalises them so the disk always lands in the new sectioned shape.
    from liberty.connectors.config import _migrate_legacy_shape
    migrated = _migrate_legacy_shape({"connectors": body.connectors})
    body.connectors = migrated.get("connectors") or {}
    for name, raw in body.connectors.items():
        # Enforce always-licensed names: the UI may have stripped or unset the flag, but the
        # framework gates these regardless of what's on disk — keep the file accurate by
        # forcing ``licensed = true`` here so reads through other paths (raw TOML, package
        # import, …) don't mislead.
        if name in ALWAYS_LICENSED_CONNECTORS:
            raw["licensed"] = True
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


@router.get("/config/dictionary/parsed", summary="Get dictionary")
async def get_dictionary_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``dictionary.toml`` parsed and normalised — ``{path, dictionary: {default_language,
    entries, enums, lookups, connectors: {<name>: {entries, enums, lookups}}}}``. A missing file →
    an empty dictionary. Default-valued keys are dropped so the wire payload stays terse."""
    path = _dictionary_path(request.app.state.settings)
    cfg = load_dictionary(path)
    return {"path": str(path), "dictionary": cfg.model_dump(exclude_defaults=True)}


class DictionaryBody(BaseModel):
    dictionary: dict[str, Any]


@router.put("/config/dictionary/parsed", summary="Update dictionary")
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


@router.get("/config/menus/parsed", summary="Get menus")
async def get_menus_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``menus.toml`` parsed and normalised — ``{path, menus: {<app>: AppMenu dict}}``.
    A missing file → an empty menu set. Default-valued keys are dropped."""
    path = Path(request.app.state.settings.menus.config_path)
    cfg = load_menus(path)
    return {"path": str(path), "menus": {name: app.model_dump(exclude_defaults=True) for name, app in cfg.menus.items()}}


class MenusBody(BaseModel):
    menus: dict[str, dict[str, Any]]


@router.put("/config/menus/parsed", summary="Update menus")
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


@router.get("/config/screens/parsed", summary="Get screens config")
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


@router.put("/config/screens/parsed", summary="Update screens config")
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
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


@router.get("/config/actions/parsed", summary="Get shared actions config")
async def get_actions_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``actions.toml`` parsed — ``{path, actions: {<id>: SharedAction dict}}``. The
    ``id`` is the map key, so it's dropped from each body (re-injected by parse_actions on save)."""
    path = Path(request.app.state.settings.actions.config_path)
    cfg = load_actions(path)
    return {
        "path": str(path),
        "actions": {
            aid: a.model_dump(exclude_defaults=True, exclude_none=True, exclude={"id"})
            for aid, a in cfg.actions.items()
        },
    }


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
    path.write_text(new_text, encoding="utf-8")
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
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


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
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path), "resolved": resolve_theme(validated.theme)}


# ── nomaflow jobs.toml (Phase 13 — see docs/NOMAFLOW-UI.md §5) ────────────────────────────────
# Unlike pools/connectors/charts/dashboards (dict-keyed sections), jobs.toml's top level is a
# ``[[jobs]]`` *array of tables*. So the wire shape is a list, not a {key: value} map: GET
# returns ``{path, jobs: [...]}``, PUT accepts ``{jobs: [...]}``. The GET returns *merged* jobs
# (``[jobs.step_defaults]`` expanded into each step) — the editor works on fully-expanded steps;
# see NOMAFLOW-UI.md §4.


@router.get("/config/jobs/parsed", summary="Get jobs config")
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


@router.put("/config/jobs/parsed", summary="Update jobs config")
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


# ── app.toml (master settings — host, port, log level, AI exposure) ─────────────────────────
# The UI exposes a curated subset of AppSettings + AISettings (see liberty.config). Secrets
# bound through ``${ENV_VAR}`` references — jwt_secret, master_key, license_key, oidc
# client_secret, ai.api_key — are NEVER read or written by these endpoints; they stay env-only.
# Path fields (config_path, static_dir, dictionary_path) are also out of scope (operators don't
# move them from the UI). Other sections of app.toml — [auth], [oidc], [crypto], [license],
# [pools.*] root, [connectors] paths, etc. — are preserved verbatim on disk (tomlkit round-trip,
# so comments + ``${VAR}`` references on untouched keys survive a save). Changes do NOT take
# effect live: app.toml is loaded once at startup. The PUT returns ``requires_restart: true`` to
# nudge the UI.
from liberty.config import AISettings, AppSettings, LicenseSettings, OIDCSettings, ReportsBrandingSettings, load_settings, resolve_app_config_path


# Keys safe to expose: secrets, path fields, and backend toggles that require a coordinated
# DB/auth migration stay out. Everything else round-trips through the editor.
_APP_SAFE_KEYS = {"name", "host", "port", "log_level", "hot_reload", "default_language"}
_AI_SAFE_KEYS = {
    "enabled", "model", "max_tokens", "max_iterations", "system_prompt", "thinking",
    "effort", "request_timeout", "connector_tools", "api_tool", "scaffold_tools",
    "allowed_connectors", "web_fetch_domains", "web_fetch_max_uses",
    "api_key",   # sensitive — encrypted on save, masked on GET
}
_LICENSE_SAFE_KEYS = {"key"}   # sensitive — encrypted on save, masked on GET
_OIDC_SAFE_KEYS = {
    "enabled", "discovery_url", "client_id", "scopes",
    "username_claim", "email_claim", "name_claim",
    "redirect_url", "frontend_redirect",
    "client_secret",   # sensitive — encrypted on save, masked on GET
}

# Fields that get encrypt-on-save + mask-on-get. Identified by (section, field) tuple
# so encryption is targeted (other strings in the same section stay as-is).
_APP_TOML_SENSITIVE_FIELDS: set[tuple[str, str]] = {
    ("ai", "api_key"),
    ("license", "key"),
    ("oidc", "client_secret"),
}

# Canonical Anthropic model list the AppBuilder's Model dropdown offers. Keep ordered
# best-first within each family so the recommended pick (Opus 4.8) sits at the top. The
# editor accepts any string saved on disk — if app.toml carries a model that's not in this
# list (an experimental id, a legacy one), the UI extends the dropdown with that value so
# the operator's current pick stays visible.
_AI_MODELS: list[dict[str, str]] = [
    {"id": "claude-opus-4-8",            "label": "Opus 4.8 (latest, recommended)"},
    {"id": "claude-opus-4-7",            "label": "Opus 4.7"},
    {"id": "claude-opus-4-6",            "label": "Opus 4.6"},
    {"id": "claude-sonnet-4-6",          "label": "Sonnet 4.6"},
    {"id": "claude-sonnet-4-5",          "label": "Sonnet 4.5"},
    {"id": "claude-haiku-4-5-20251001",  "label": "Haiku 4.5 (fast, cheap)"},
]


def _app_config_path() -> Path:
    """Where app.toml lives. Honours ``LIBERTY_APP_CONFIG`` (env override; useful for a
    gitignored ``config/app-dev.toml`` in dev), else the historical ``config/app.toml``.
    Reads and writes go through the same resolver so the editor never targets a different
    file than the running server is reading."""
    return resolve_app_config_path()


def _pick(d: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k in keys}


@router.get("/config/app/parsed", summary="Get app config")
async def get_app_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The curated ``[app]`` + ``[ai]`` + ``[license]`` + ``[oidc]`` view from app.toml.

    Sensitive fields (ai.api_key, license.key, oidc.client_secret) come back as their
    on-disk value — encrypted (``ENC:…`` ciphertext) when configured via the UI. The
    UI renders them in a ``<PasswordInput>`` (masked + reveal-eye) the same way pools
    and API connectors handle their secrets. Leaking ciphertext to the browser is
    fine: it can only be decrypted with the server-side master key. Round-tripping
    the same ciphertext on save is a no-op because :func:`encrypt` is idempotent on
    already-``ENC:`` values."""
    path = _app_config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    raw = tomllib.loads(text) if text.strip() else {}
    try:
        app_model = AppSettings.model_validate(raw.get("app") or {})
        ai_model = AISettings.model_validate(raw.get("ai") or {})
        license_model = LicenseSettings.model_validate(raw.get("license") or {})
        oidc_model = OIDCSettings.model_validate(raw.get("oidc") or {})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"existing app.toml is invalid: {exc}") from exc

    connectors = sorted(request.app.state.connectors.names()) if hasattr(request.app.state, "connectors") else []
    return {
        "path": str(path),
        "app": _pick(app_model.model_dump(), _APP_SAFE_KEYS),
        "ai": _pick(ai_model.model_dump(), _AI_SAFE_KEYS),
        "license": _pick(license_model.model_dump(), _LICENSE_SAFE_KEYS),
        "oidc": _pick(oidc_model.model_dump(), _OIDC_SAFE_KEYS),
        "choices": {
            "log_levels": ["debug", "info", "warning", "error", "critical"],
            "effort_levels": ["", "low", "medium", "high", "xhigh", "max"],
            "connectors": connectors,
            "models": _AI_MODELS,
        },
    }


class AppConfigBody(BaseModel):
    app: dict[str, Any] = {}
    ai: dict[str, Any] = {}
    license: dict[str, Any] = {}
    oidc: dict[str, Any] = {}


def _encrypt_sensitives(section: str, fields: dict[str, Any], master_key: str) -> dict[str, Any]:
    """Encrypt any sensitive fields in *fields*. Plaintext non-empty → ENC:<value>. Already
    ENC:-prefixed → unchanged (the UI round-trips ciphertext when the operator hasn't touched
    the field — encrypt() is idempotent on its own output). Empty → unchanged (the operator
    cleared the field; _upsert() will write the empty string to disk, clearing the on-disk
    value). Returns a new dict."""
    out = dict(fields)
    for sec, fld in _APP_TOML_SENSITIVE_FIELDS:
        if sec != section or fld not in out:
            continue
        v = str(out.get(fld) or "")
        if not v:
            continue              # operator cleared the field
        if v.startswith("ENC:"):
            continue              # already encrypted (shouldn't happen via UI, but harmless)
        if not master_key:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Cannot save {section}.{fld}: no master_key configured (set LIBERTY_MASTER_KEY).",
            )
        try:
            out[fld] = encrypt(v, master_key)
        except CryptoError as exc:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"encrypt({section}.{fld}) failed: {exc}",
            ) from exc
    return out


@router.put("/config/app/parsed", summary="Update app config")
async def put_app_parsed(body: AppConfigBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate the submitted ``[app]`` + ``[ai]`` + ``[license]`` + ``[oidc]`` against
    their Pydantic models, encrypt any sensitive fields (ai.api_key, license.key,
    oidc.client_secret) with the install's master key, then surgically rewrite ONLY those
    keys in app.toml via ``tomlkit`` — comments, formatting, and ``${VAR}`` references on
    every other section stay untouched. Changes require a server restart to take effect
    (app.toml is loaded once at boot).

    Sensitive fields submitted as empty string are written as empty (clear). Already-ENC
    values pass through unchanged (UI never round-trips ciphertext, but defensive)."""
    app_in = _pick(body.app or {}, _APP_SAFE_KEYS)
    ai_in = _pick(body.ai or {}, _AI_SAFE_KEYS)
    license_in = _pick(body.license or {}, _LICENSE_SAFE_KEYS)
    oidc_in = _pick(body.oidc or {}, _OIDC_SAFE_KEYS)
    try:
        AppSettings.model_validate(app_in)
        AISettings.model_validate(ai_in)
        LicenseSettings.model_validate(license_in)
        OIDCSettings.model_validate(oidc_in)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid app settings: {exc}") from exc

    # Encrypt sensitive fields before writing. Master key comes from the LIVE settings
    # (operator can rotate it via env without restart for this purpose — though full
    # rotation needs re-saving every secret).
    master_key = request.app.state.settings.crypto.master_key if hasattr(request.app.state, "settings") else ""
    ai_in = _encrypt_sensitives("ai", ai_in, master_key)
    license_in = _encrypt_sensitives("license", license_in, master_key)
    oidc_in = _encrypt_sensitives("oidc", oidc_in, master_key)

    path = _app_config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()

    def _upsert(section: str, values: dict[str, Any]) -> None:
        if section not in doc:
            doc[section] = tomlkit.table()
        for k, v in values.items():
            doc[section][k] = v

    _upsert("app", app_in)
    _upsert("ai", ai_in)
    _upsert("license", license_in)
    _upsert("oidc", oidc_in)

    new_text = tomlkit.dumps(doc)
    try:
        reparsed = tomllib.loads(new_text)
        AppSettings.model_validate(reparsed.get("app") or {})
        AISettings.model_validate(reparsed.get("ai") or {})
        LicenseSettings.model_validate(reparsed.get("license") or {})
        OIDCSettings.model_validate(reparsed.get("oidc") or {})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting app.toml is invalid: {exc}") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")

    # Live-apply: reload settings from disk and rebuild the runtime objects the new values
    # feed into — license, connector registry (only if the license key changed), AI
    # assistant, OIDC client. Their next request sees the new key without a server restart.
    # Uvicorn-bound [app] fields (host / port / log_level) are the only thing that still
    # needs a hard restart; everything else (app.name / default_language / hot_reload) is
    # re-read off the live settings on demand, so it picks up the new value naturally.
    from liberty.ai.assistant import build_assistant
    from liberty.auth.oidc import build_oidc

    old_settings = request.app.state.settings
    new_settings = load_settings()
    restart_needed = (
        new_settings.app.host != old_settings.app.host
        or new_settings.app.port != old_settings.app.port
        or new_settings.app.log_level != old_settings.app.log_level
    )
    license_changed = new_settings.license.key != old_settings.license.key
    request.app.state.settings = new_settings
    new_license = verify_license(
        new_settings.license.key, master_key=new_settings.crypto.master_key,
    )
    request.app.state.license = new_license

    # When the license key changed, rebuild the connector registry — licensed-connector
    # filtering happens INSIDE load_connectors (see liberty/connectors/registry.py), so
    # just reassigning app.state.license isn't enough: licensed connectors that were
    # filtered out at startup wouldn't reappear, and ones the new key no longer covers
    # would still be loaded. The auth backend rebinds too (DB-backend auth uses
    # connectors.pools). Menus/screens/charts/dashboards/jobs don't depend on the
    # license — leave them be (POST /admin/reload is still the path for a full reload).
    if license_changed:
        old_conns = request.app.state.connectors
        try:
            new_conns = load_connectors(
                new_settings.connectors.config_path,
                dictionary_path=new_settings.connectors.dictionary_path,
                master_key=new_settings.crypto.master_key,
                license=new_license,
                default_language=new_settings.app.default_language,
            )
        except Exception as exc:  # noqa: BLE001 — surface as 500 so the UI shows the cause
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Saved {path}, but rebuilding connectors with the new license failed: {exc}",
            ) from exc
        request.app.state.connectors = new_conns
        request.app.state.auth_backend = build_auth_backend(new_settings, new_conns.pools)
        await old_conns.aclose()

    # Rebuild the AI assistant AFTER any connector swap — its tools bind to the registry.
    old_ai = getattr(request.app.state, "ai", None)
    new_ai = build_assistant(
        new_settings.ai, request.app.state.connectors,
        master_key=new_settings.crypto.master_key,
    )
    request.app.state.ai = new_ai
    if old_ai is not None and old_ai is not new_ai:
        await old_ai.aclose()
    request.app.state.oidc = build_oidc(
        new_settings.oidc, master_key=new_settings.crypto.master_key,
    )

    return {"saved": True, "path": str(path), "requires_restart": restart_needed}


# --------------------------------------------------------------------------- #
# Reports — branding defaults (Phase 3b)
# --------------------------------------------------------------------------- #
# Read / write JUST the ``[reports.branding]`` section of app.toml. The render
# pipeline (liberty.reports.render.render_content) folds these in as
# BuildOptions defaults between the framework's title/author values and the
# plugin's per-report pdf_options overrides — so the operator can theme every
# report's PDF cover from one place without touching plugin code.
@router.get("/config/reports/branding", summary="Get reports branding defaults")
async def get_reports_branding(request: Request, _: Superuser) -> dict[str, Any]:
    """Return the install-wide PDF branding defaults from
    ``[reports.branding]`` (author, primary_color, cover_eyebrow, …) plus the
    canonical defaults so the UI can show "Reset" against them."""
    path = _app_config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    raw = tomllib.loads(text) if text.strip() else {}
    section = (raw.get("reports") or {}).get("branding") or {}
    try:
        model = ReportsBrandingSettings.model_validate(section)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"existing [reports.branding] is invalid: {exc}",
        ) from exc
    return {
        "path": str(path),
        "branding": model.model_dump(),
        "defaults": ReportsBrandingSettings().model_dump(),
    }


class ReportsBrandingBody(BaseModel):
    branding: dict[str, Any] = {}


@router.put("/config/reports/branding", summary="Update reports branding defaults")
async def put_reports_branding(
    body: ReportsBrandingBody, request: Request, _: Superuser,
) -> dict[str, object]:
    """Validate the submitted branding fields, then surgically rewrite ONLY
    the ``[reports.branding]`` section of app.toml via ``tomlkit`` — every
    other table stays byte-identical (comments, formatting, ``${VAR}``
    references on other sections all preserved). Live-applies via
    ``load_settings()``; no restart needed because the next report render
    reads ``app.state.settings.reports.branding`` fresh."""
    try:
        model = ReportsBrandingSettings.model_validate(body.branding or {})
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"invalid branding: {exc}",
        ) from exc

    path = _app_config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()

    # Ensure [reports] super-table exists, then [reports.branding] under it.
    if "reports" not in doc:
        doc["reports"] = tomlkit.table(is_super_table=True)
    reports_tbl = doc["reports"]
    if "branding" not in reports_tbl:
        reports_tbl["branding"] = tomlkit.table()
    branding_tbl = reports_tbl["branding"]

    # Persist only non-default values to keep the file terse — defaults are
    # reapplied automatically by the Pydantic model on next read.
    normalised = model.model_dump(exclude_defaults=True)
    for stale in [k for k in list(branding_tbl.keys()) if k not in normalised]:
        del branding_tbl[stale]
    for k, v in normalised.items():
        branding_tbl[k] = v

    new_text = tomlkit.dumps(doc)
    try:
        reparsed = tomllib.loads(new_text)
        ReportsBrandingSettings.model_validate(
            ((reparsed.get("reports") or {}).get("branding") or {})
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"resulting app.toml is invalid: {exc}",
        ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")

    # Live-apply — next render picks the new branding up without a restart.
    request.app.state.settings = load_settings()
    return {"saved": True, "path": str(path)}


# --------------------------------------------------------------------------- #
# Reports — operator-authored custom templates (Phase 4a)
# --------------------------------------------------------------------------- #
# Read / write the entire ``reports.toml`` (one [reports.<id>] table per
# template) as a structured list. Editing is rebuild-the-file rather than
# in-place because each entry is independently validated and the whole shape
# (id + title + params + data + template_inline + pdf_options) ships through
# the UI in one round-trip.
@router.get("/config/reports/parsed", summary="Get custom reports")
async def get_reports_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """Return every ``[reports.<id>]`` block from reports.toml as a list of
    :class:`liberty.reports.custom.CustomReportTemplate` dicts. Includes the
    list of available connectors + their queries so the UI can populate the
    data-binding dropdowns without a second round-trip."""
    from liberty.reports.custom import load_custom_reports

    path = request.app.state.settings.reports.templates_config_path
    try:
        templates = load_custom_reports(path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"existing reports.toml is invalid: {exc}",
        ) from exc

    # Connectors → their named queries — feeds the UI's data-source dropdowns.
    connectors = getattr(request.app.state, "connectors", None)
    connector_choices: dict[str, list[str]] = {}
    if connectors is not None:
        for name in sorted(connectors.names()):
            c = connectors.get(name)
            try:
                # SQLConnector.query_names is a @property → not callable
                connector_choices[name] = sorted(c.query_names)
            except (AttributeError, TypeError):
                # Non-SQL connector (e.g. HTTP API) — no SELECT-by-name surface
                connector_choices[name] = []
    return {
        "path": str(path),
        "templates": [t.model_dump() for t in templates],
        "connectors": connector_choices,
    }


class ReportsTemplatesBody(BaseModel):
    templates: list[dict[str, Any]]


@router.put("/config/reports/parsed", summary="Update custom reports")
async def put_reports_parsed(
    body: ReportsTemplatesBody, request: Request, _: Superuser,
) -> dict[str, object]:
    """Replace every ``[reports.<id>]`` table in reports.toml with the
    submitted list. Each entry is validated against
    :class:`CustomReportTemplate` (so a typo'd field or a duplicate id surfaces
    as 422 before disk is touched). Rebuilds the in-memory report registry on
    success so the next ``GET /api/reports`` reflects the change without a
    restart."""
    from liberty.reports.custom import CustomReportTemplate
    from liberty.reports.wiring import refresh_reports

    # Validate every entry up-front — fail fast with a useful per-id message.
    parsed: list[CustomReportTemplate] = []
    seen_ids: set[str] = set()
    for raw in body.templates:
        if not isinstance(raw, dict):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"each templates[] entry must be an object, got {type(raw).__name__}",
            )
        rid = raw.get("id")
        if not rid:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="each templates[] entry must carry an 'id' field",
            )
        if rid in seen_ids:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"duplicate template id {rid!r}",
            )
        seen_ids.add(rid)
        try:
            parsed.append(CustomReportTemplate.model_validate(raw))
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"template {rid!r}: {exc}",
            ) from exc

    # Serialise — fresh tomlkit document keyed by id. We don't preserve
    # existing comments in reports.toml because the UI is the source of truth
    # (operators don't edit this file by hand the way they might edit
    # connectors.toml). A first write creates the file; subsequent writes
    # overwrite the [reports.*] section verbatim.
    path = Path(request.app.state.settings.reports.templates_config_path)
    doc = tomlkit.document()
    reports_tbl = tomlkit.table(is_super_table=True)
    for t in parsed:
        body_table = tomlkit.table()
        body_table["title"] = t.title
        if t.description:
            body_table["description"] = t.description
        body_table["formats"] = list(t.formats)
        if t.params:
            params_array = tomlkit.array()
            for p in t.params:
                params_array.append(p.model_dump(exclude_defaults=False))
            body_table["params"] = params_array
        data_tbl = tomlkit.table()
        data_tbl["connector"] = t.data.connector
        data_tbl["query"] = t.data.query
        body_table["data"] = data_tbl
        body_table["template_inline"] = tomlkit.string(
            t.template_inline, multiline=True,
        )
        if t.pdf_options:
            pdf_tbl = tomlkit.table()
            for k, v in t.pdf_options.items():
                pdf_tbl[k] = v
            body_table["pdf_options"] = pdf_tbl
        reports_tbl[t.id] = body_table
    doc["reports"] = reports_tbl

    new_text = tomlkit.dumps(doc)
    # Round-trip validate the just-rendered TOML — catches a tomlkit serialisation
    # quirk that survives validation above but is unreadable on next boot.
    try:
        reparsed = tomllib.loads(new_text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"resulting reports.toml is invalid: {exc}",
        ) from exc
    # Sanity: every saved id reappears post-roundtrip.
    saved_ids = set((reparsed.get("reports") or {}).keys())
    if saved_ids != seen_ids:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"round-trip mismatch — saved {sorted(seen_ids)}, on disk {sorted(saved_ids)}",
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")

    # Rebuild the report registry so the new templates surface immediately
    # in ``GET /api/reports``. The license result on app.state stays — custom
    # reports aren't license-gated (scope='custom' is non-licensed by design).
    license = getattr(request.app.state, "license", None)
    refresh_reports(request.app, request.app.state.settings, license)
    return {"saved": True, "path": str(path), "count": len(parsed)}


# Render-on-the-fly preview — operators hit this from the template editor to
# see what their Jinja markdown produces against real data BEFORE committing
# the template to ``reports.toml``. The body matches what the PUT endpoint
# accepts for one entry, plus an optional ``params`` dict for the test run.
# Returns the rendered markdown (truncated when data is large) so the UI can
# show a side-by-side preview without rendering a PDF.
class ReportsTemplatePreviewBody(BaseModel):
    template_inline: str
    data: dict[str, Any]
    params: dict[str, Any] = {}


# Preview rows are capped so a multi-thousand-row query doesn't render a
# multi-megabyte markdown payload back to the browser. Operators only need a
# representative sample to validate the template shape.
_PREVIEW_ROW_CAP = 200


@router.post("/config/reports/preview", summary="Preview custom report")
async def preview_report_template(
    body: ReportsTemplatePreviewBody, request: Request, _: Superuser,
) -> dict[str, Any]:
    """Render *body.template_inline* against the live connector data (or an
    empty dataset when no data binding is supplied) and return the rendered
    markdown. Used by the Settings → Templates editor's "Test render" button.

    Errors during data fetch / template parse / template render are returned as
    422 with the underlying message — the UI shows them inline next to the
    preview pane so operators can iterate without leaving the editor."""
    from liberty.reports.custom import (
        CustomReportDataBinding,
        _make_sandbox_env,
    )

    connectors = getattr(request.app.state, "connectors", None)
    rows: list[dict[str, Any]] = []
    columns: list[dict[str, Any]] = []
    truncated = False
    fetched_count = 0

    binding_payload = body.data or {}
    connector_name = (binding_payload.get("connector") or "").strip()
    query_name = (binding_payload.get("query") or "").strip()

    if connector_name and query_name:
        # Validate the binding via the same Pydantic shape the saved form uses
        # so a malformed connector/query name surfaces before any I/O.
        try:
            binding = CustomReportDataBinding.model_validate(
                {"connector": connector_name, "query": query_name},
            )
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"data binding: {exc}",
            ) from exc
        if connectors is None or binding.connector not in connectors.names():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"unknown connector: {binding.connector!r}",
            )
        connector = connectors.get(binding.connector)
        try:
            result = await connector.execute(binding.query, dict(body.params))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"query {binding.query!r} failed: {exc}",
            ) from exc
        all_rows = list(getattr(result, "rows", []) or [])
        fetched_count = len(all_rows)
        if fetched_count > _PREVIEW_ROW_CAP:
            rows = all_rows[:_PREVIEW_ROW_CAP]
            truncated = True
        else:
            rows = all_rows
        columns = [
            {"name": getattr(c, "name", None), "label": getattr(c, "label", None),
             "type": getattr(c, "type", None)}
            for c in (getattr(result, "columns", []) or [])
        ]

    env = _make_sandbox_env()
    try:
        tpl = env.from_string(body.template_inline)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"template parse error: {exc}",
        ) from exc
    ctx = {
        "data": {"rows": rows, "columns": columns},
        "params": dict(body.params),
    }
    try:
        rendered = tpl.render(ctx=ctx)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"template render error: {exc}",
        ) from exc
    return {
        "markdown": rendered,
        "row_count": fetched_count,
        "rendered_row_count": len(rows),
        "column_count": len(columns),
        "truncated": truncated,
    }


# Column introspection for a (connector, query) pair — feeds the template
# editor's Monaco autocomplete (so ``row.<TAB>`` suggests real column names)
# and the "Scaffold from query" button (which emits a markdown table with the
# columns pre-filled). Runs the query with ``max_rows=1`` to keep DB cost
# trivial; the row payload is discarded, only ``columns`` rides back.
class ReportsTemplateColumnsBody(BaseModel):
    connector: str
    query: str
    params: dict[str, Any] = {}


@router.post("/config/reports/columns", summary="Introspect query columns")
async def introspect_report_query_columns(
    body: ReportsTemplateColumnsBody, request: Request, _: Superuser,
) -> dict[str, Any]:
    """Return the column metadata for *body.query* on *body.connector*.

    Used by the Settings → Templates editor for two things:

    * Monaco autocomplete — typing ``row.`` suggests the actual column names
      the query returns.
    * The "Scaffold from query" button — fills the template body with a
      markdown table whose header row uses real column labels.

    Runs the query with ``max_rows=1`` so the DB only pays for the metadata
    fetch, not a full result set. Errors (unknown connector, query failure)
    surface as 422 so the UI can show a clear "couldn't introspect" message."""
    from liberty.reports.custom import CustomReportDataBinding

    connectors = getattr(request.app.state, "connectors", None)
    if connectors is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="connector registry not initialised",
        )
    try:
        binding = CustomReportDataBinding.model_validate(
            {"connector": body.connector, "query": body.query},
        )
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"binding: {exc}",
        ) from exc
    if binding.connector not in connectors.names():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown connector: {binding.connector!r}",
        )
    connector = connectors.get(binding.connector)
    try:
        result = await connector.execute(
            binding.query, dict(body.params), max_rows=1,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"query {binding.query!r} failed: {exc}",
        ) from exc
    columns = [
        {"name": getattr(c, "name", None), "label": getattr(c, "label", None),
         "type": getattr(c, "type", None)}
        for c in (getattr(result, "columns", []) or [])
    ]
    return {"columns": columns}


# ── Find usages ─────────────────────────────────────────────────────────────────────────────
# Every Settings editor surfaces a "Find usages" button that calls this. The reference graph
# lives in liberty.web.usages — walks the in-memory registries (no disk reads) and returns a
# grouped list of every place the named entity is referenced. Used to safely rename / delete
# and to spot orphans. See usages.py for the full kind list.
from liberty.web.usages import find_usages
from liberty.web.dependencies import Seed, collect_dependencies
from liberty.web.package import build_package_zip


class FindUsagesBody(BaseModel):
    kind: str
    name: str
    scope: str | None = None


class SeedItem(BaseModel):
    kind: str
    name: str
    scope: str | None = None


class FindDependenciesBody(BaseModel):
    seeds: list[SeedItem]


@router.post("/find-dependencies", summary="Find dependencies")
async def find_dependencies_endpoint(body: FindDependenciesBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Walk the dependency closure for *seeds* — every connector / query / pool / DD entry
    / lookup / sequence / chart / screen / menu item the seeds transitively reach. Returns
    the manifest (counts, per-kind groups, missing refs, warnings) for the inspect UI to
    render as a tree. Same walker the package builder uses; this endpoint just doesn't
    serialise to a ZIP."""
    manifest = collect_dependencies(
        request.app.state,
        [Seed(kind=s.kind, name=s.name, scope=s.scope) for s in body.seeds],
    )
    return manifest.to_dict()


class IncludeItem(BaseModel):
    kind: str
    name: str
    scope: str | None = None


class BuildPackageBody(BaseModel):
    seeds: list[SeedItem]
    # Optional filter — only deps whose (kind, scope, name) is in this list go into the ZIP.
    # When absent or None, every dep ships (older behaviour). The frontend sends this with
    # the operator's per-dep checkbox state so connectors / pools / api_endpoints (which are
    # default-excluded in the UI) don't land on the target unless the operator opted in.
    include: list[IncludeItem] | None = None


@router.post("/build-package", summary="Build deployment package")
async def build_package_endpoint(body: BuildPackageBody, request: Request, _: Superuser):
    """Build a ZIP package of the dependency closure — returns ``application/zip`` ready to
    download. Optional ``include`` list filters the closure to the operator's per-dep
    selection (the frontend default-excludes connector / pool / api_endpoint).

    Apply on the target by unzipping into its ``config/`` and running ``POST /admin/reload``
    — or call ``POST /admin/import-package`` to merge / overwrite per a strategy. Secrets
    (``ENC:...`` / ``${VAR}``) pass through verbatim; the target must carry the same
    master_key + env vars (MANIFEST.md lists what's needed)."""
    from fastapi.responses import Response
    manifest = collect_dependencies(
        request.app.state,
        [Seed(kind=s.kind, name=s.name, scope=s.scope) for s in body.seeds],
    )
    include_set: set[tuple[str, str | None, str]] | None = None
    if body.include is not None:
        include_set = {(i.kind, i.scope, i.name) for i in body.include}
    src = getattr(request.app.state.settings.app, "name", "liberty-next")
    blob = build_package_zip(manifest, source_install=src, include=include_set)
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="liberty-package.zip"'},
    )


@router.post("/import-package", summary="Import deployment package")
async def import_package_endpoint(
    request: Request, _: Superuser,
    strategy: str = "overwrite",
):
    """Apply a package ZIP (produced by /admin/build-package) to this install's config files.

    Multipart upload — the ZIP is the request body's ``package`` file part; the strategy
    is a query param: ``merge`` / ``overwrite`` / ``replace_all`` (see
    liberty/web/package_import.py for semantics).

    Returns ``{report: {files: [...], warnings: [...], reloaded: bool}}`` with per-file
    counts of added / replaced / skipped entities. Reloads the framework after a
    successful apply so the new config is live for the next request."""
    from fastapi import UploadFile, File
    from liberty.web.package_import import apply_package_zip
    if strategy not in ("merge", "overwrite", "replace_all"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"invalid strategy: {strategy!r} — pick merge / overwrite / replace_all")
    form = await request.form()
    upload = form.get("package")
    if not isinstance(upload, UploadFile) or not upload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="missing 'package' file part")
    blob = await upload.read()
    try:
        report = apply_package_zip(zip_bytes=blob, strategy=strategy, settings=request.app.state.settings)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    # Reload — same path the structured PUT endpoints expect operators to call afterwards.
    try:
        await reload_connectors(request, _)
        report.reloaded = True
    except Exception as exc:  # noqa: BLE001 — surface reload failures but keep the import report
        report.warnings.append(f"reload failed after apply: {type(exc).__name__}: {exc}")
    return {"report": report.to_dict()}


class ExportJobBody(BaseModel):
    id: str


@router.post("/export-job", summary="Export job package")
async def export_job_endpoint(body: ExportJobBody, request: Request, _: Superuser):
    """Export ONE nomaflow job as a ZIP — minimal ``jobs.toml`` carrying just this job, plus
    a MANIFEST.md. Job deployment is intentionally separate from the screen / dashboard
    package: jobs are operational and the operator decides per-environment whether to ship
    them. Apply on the target by unzipping into the target's nomaflow plugin dir (the same
    ``LIBERTY_APPS_DIR/../plugins/nomaflow/jobs.toml`` path the loader watches) and reloading."""
    from fastapi.responses import Response
    jobs_registry = getattr(request.app.state, "jobs", None)
    if jobs_registry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="nomaflow registry not loaded")
    registry = getattr(jobs_registry, "registry", None) or jobs_registry
    try:
        job = registry.get(body.id)
    except Exception as exc:  # UnknownJobError
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"job {body.id!r} not found: {exc}") from exc
    # Minimal jobs.toml: only the single job, no meta / step_defaults — the target preserves
    # its own meta (retention policy is per-install).
    import tomli_w
    job_dict = job.model_dump(by_alias=True, exclude_defaults=True, exclude_none=True)
    jobs_toml = tomli_w.dumps({"jobs": [job_dict]})
    manifest_md = (
        f"# Nomaflow job export — `{body.id}`\n\n"
        f"**Source install:** `{request.app.state.settings.app.name}`\n\n"
        f"## Apply on the target\n\n"
        "1. Unzip into the target install's nomaflow jobs.toml location\n"
        "   (typically `${LIBERTY_APPS_DIR}/../plugins/nomaflow/jobs.toml` — see PHASE13).\n"
        "2. Either replace the file (single-job target) or merge the `[[jobs]]` entry into\n"
        "   your target's existing jobs.toml.\n"
        "3. Reload nomaflow:\n"
        "   ```\n"
        "   curl -X POST -H 'Authorization: Bearer <superuser-token>' https://<target>/admin/reload\n"
        "   ```\n\n"
        "## Notes\n\n"
        "- Queries / connectors / pools referenced by this job are NOT bundled — make sure\n"
        "  the target install already has them. Use `Settings → Package` to ship the\n"
        "  underlying config slice separately if needed.\n"
        "- ENC: secrets and ${VAR} references pass through verbatim. The target needs the\n"
        "  same `[crypto] master_key` and any referenced env vars set.\n"
    )
    import io as _io
    import zipfile as _zipfile
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("jobs.toml", jobs_toml)
        zf.writestr("MANIFEST.md", manifest_md)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="nomaflow-job-{body.id}.zip"'},
    )


@router.post("/find-usages", summary="Find usages")
async def find_usages_endpoint(body: FindUsagesBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Return every reference to ``(kind, name, scope)`` across the loaded configs. Walks the
    in-memory state — call ``POST /admin/reload`` first if you've edited a file on disk and
    need fresh results. Empty ``usages`` means safe to rename / delete (no references)."""
    try:
        results = find_usages(request.app.state, kind=body.kind, name=body.name, scope=body.scope)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "kind": body.kind, "name": body.name, "scope": body.scope,
        "usages": [u.to_dict() for u in results],
        "count": len(results),
    }


# ── Apply an AI-assistant proposal ──────────────────────────────────────────────────────────
# The AI assistant's scaffold tools never write to disk — they return a Proposal envelope
# (see liberty.ai.proposal) which the chat UI surfaces as an Apply card. Clicking Apply POSTs
# the envelope here; this endpoint merges the payload into the right config file via the same
# validation + write paths the dedicated PUT endpoints use, then runs /admin/reload so the new
# config is live. Returns ``{applied: true}`` plus a short summary so the chat can confirm.
#
# Dispatching by ``kind`` keeps the chat UI from needing to know 4+ endpoint paths — one knob.


class ProposalApplyBody(BaseModel):
    """A Proposal envelope as emitted by ``liberty.ai.proposal.Proposal.to_dict()``."""

    kind: str  # dictionary_entries | connector_queries | screen | menu_item
    summary: str | None = None
    target_path: str | None = None
    payload: dict[str, Any]
    app: str | None = None
    connector: str | None = None
    notes: list[str] | None = None


def _apply_dictionary_entries(request: Request, payload: dict[str, Any]) -> str:
    """Merge dictionary entries into the shared scope or a connector overlay."""
    scope = payload.get("scope")
    entries = payload.get("entries") or {}
    if not scope or not isinstance(entries, dict) or not entries:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="proposal payload must carry {scope, entries}")
    # Normalise common synonyms — the assistant occasionally uses ``type`` instead of the
    # DictionaryEntry's actual ``format`` field. Quietly rename so a sloppy proposal still
    # validates. Same kind of forgiveness the parsed-config PUTs already offer (extra=ignore
    # on ScreenField, etc.) but here Pydantic's extra='forbid' would reject the key outright.
    normalised: dict[str, dict[str, Any]] = {}
    for eid, rec in entries.items():
        if not isinstance(rec, dict):
            continue
        rec = dict(rec)
        if "type" in rec and "format" not in rec:
            rec["format"] = rec.pop("type")
        normalised[eid] = rec
    entries = normalised
    path = _dictionary_path(request.app.state.settings)
    cfg = load_dictionary(path)
    doc = cfg.model_dump(exclude_defaults=True)
    if scope == "shared":
        merged = {**(doc.get("entries") or {}), **entries}
        doc["entries"] = merged
    else:
        connectors = doc.get("connectors") or {}
        section = connectors.get(scope) or {}
        section_entries = {**(section.get("entries") or {}), **entries}
        section["entries"] = section_entries
        connectors[scope] = section
        doc["connectors"] = connectors
    # Re-validate + write — same pattern as put_dictionary_parsed
    try:
        validated = DictionaryFile.model_validate(doc)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"merged dictionary invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps(validated.model_dump(exclude_defaults=True))
    parse_dictionary(tomllib.loads(new_text))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return f"merged {len(entries)} entries into {scope!r} scope"


def _apply_connector_queries(request: Request, payload: dict[str, Any]) -> str:
    """Append (or replace by name) queries on a SQL connector. Uses tomlkit so other
    connectors / comments in connectors.toml round-trip untouched."""
    connector = payload.get("connector")
    queries = payload.get("queries") or []
    if not connector or not isinstance(queries, list) or not queries:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="proposal payload must carry {connector, queries}")
    path = Path(request.app.state.settings.connectors.config_path)
    cfg = load_connectors_file(path)
    conn = cfg.connectors.get(connector)
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"connector {connector!r} not found")
    if conn.type != "sql":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"connector {connector!r} is not a SQL connector")
    existing = conn.model_dump(exclude_defaults=True)
    # Bucket the incoming proposals into the four sections with the SAME classifier the loader
    # uses (``type`` field wins, else CRUD-suffix guess → table). It drops the stray ``type``
    # field, so an earlier scaffold revision's bad per-slot kind (select / insert / …) falls
    # through to the name-pattern fallback cleanly. Then merge per section:
    #   * queries / sequences / lookups — replace-by-name, append new.
    #   * tables — merge by table name: incoming slots + non-null label/description win, new
    #     tables are appended. (A clone of a CRUD screen re-forms its ``<base>_<suffix>_<crud>``
    #     slots back into one table here.)
    from liberty.connectors.config import _split_legacy_queries
    incoming = _split_legacy_queries(connector, [dict(q) for q in queries if q.get("name")])
    for section in ("queries", "sequences", "lookups"):
        by_name = {q.get("name"): q for q in (existing.get(section) or [])}
        for q in incoming.get(section) or []:
            by_name[q.get("name")] = q
        merged = list(by_name.values())
        if merged:
            existing[section] = merged
    tbl_by_name: dict[Any, dict[str, Any]] = {t.get("name"): t for t in (existing.get("tables") or [])}
    for t in incoming.get("tables") or []:
        cur = tbl_by_name.get(t.get("name"))
        if cur is None:
            tbl_by_name[t.get("name")] = t
            continue
        for meta_key in ("label", "description"):
            if t.get(meta_key) is not None:
                cur[meta_key] = t[meta_key]
        for crud in ("get", "put", "post", "delete"):
            if crud in t:
                cur[crud] = t[crud]
    if tbl_by_name:
        existing["tables"] = list(tbl_by_name.values())
    try:
        SqlConnectorConfig.model_validate(existing)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"merged connector invalid: {exc}") from exc

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    connectors_tbl = doc.get("connectors")
    if connectors_tbl is None:
        connectors_tbl = tomlkit.table(is_super_table=True)
        doc["connectors"] = connectors_tbl
    # Replace the connector's subtree wholesale — preserves [pools.*] + other connectors + comments
    # outside this connector. The connector's own subtree gets re-rendered (any inline columns become
    # nested tables — same trade-off as put_connectors_parsed).
    connectors_tbl[connector] = existing
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return f"added/updated {len(queries)} queries on connector {connector!r}"


def _apply_screen(request: Request, payload: dict[str, Any]) -> str:
    """Add or replace a screen under [screens.<app>.<screen_id>]."""
    app = payload.get("app")
    screen_id = payload.get("screen_id")
    screen = payload.get("screen") or {}
    if not app or not screen_id or not isinstance(screen, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="proposal payload must carry {app, screen_id, screen}")
    path = Path(request.app.state.settings.screens.config_path)
    registry = load_screens(path)
    # ``registry`` stores Screen instances directly per (app, id); rebuild the wire-shape doc
    doc = {a: {sid: s.model_dump(exclude_defaults=False, exclude_none=True) for sid, s in screens.items()}
           for a, screens in registry.screens.items()}
    doc.setdefault(app, {})[screen_id] = screen
    try:
        ScreensFile.model_validate({"screens": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"merged screens invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"screens": doc})
    parse_screens(tomllib.loads(new_text))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return f"saved screen {app}.{screen_id}"


def _apply_menu_item(request: Request, payload: dict[str, Any]) -> str:
    """Append a menu item to [menus.<app>]. If an item with the same id already exists, replace it."""
    app = payload.get("app")
    item = payload.get("item") or {}
    if not app or not isinstance(item, dict) or not item.get("id"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="proposal payload must carry {app, item: {id, ...}}")
    path = Path(request.app.state.settings.menus.config_path)
    cfg = load_menus(path)
    doc = {name: app_menu.model_dump(exclude_defaults=False, exclude_none=True) for name, app_menu in cfg.menus.items()}
    app_doc = doc.get(app) or {"items": []}
    items = list(app_doc.get("items") or [])
    items = [it for it in items if it.get("id") != item["id"]]
    items.append(item)
    app_doc["items"] = items
    doc[app] = app_doc
    try:
        MenusFile.model_validate({"menus": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"merged menus invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"menus": doc})
    parse_menus(tomllib.loads(new_text))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return f"saved menu item {item['id']} under {app}"


_APPLY_DISPATCH: dict[str, Any] = {
    "dictionary_entries": _apply_dictionary_entries,
    "connector_queries": _apply_connector_queries,
    "screen": _apply_screen,
    "menu_item": _apply_menu_item,
}


@router.post("/config/apply-proposal", summary="Apply AI proposal")
async def apply_proposal(body: ProposalApplyBody, request: Request, _: Superuser) -> dict[str, object]:
    """Apply a proposal emitted by the AI assistant's scaffold tools. Dispatches to the
    matching merge/write path based on ``kind``, then runs /admin/reload so the new config is
    live. Returns ``{applied, kind, summary}``.

    All write paths are the same Pydantic-validated + tomli_w / tomlkit serialisers the
    dedicated PUT endpoints use — no fast paths, no skipped validation. A bad proposal lands
    as a 422 with the validation error, same as a hand-edited PUT."""
    fn = _APPLY_DISPATCH.get(body.kind)
    if fn is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unknown proposal kind: {body.kind!r}")
    summary = fn(request, body.payload)
    # Reload the live registries so the new config is in effect for the next chat turn (the
    # "applied; continue" follow-up the assistant gets needs to see the writes it just made).
    await reload_connectors(request, _)  # type: ignore[arg-type]
    return {"applied": True, "kind": body.kind, "summary": summary}


# ── Clone with dependencies ─────────────────────────────────────────────────────────────────
# Clone a screen / chart / dashboard PLUS optionally duplicate the referenced queries
# under suffixed names, with the clone's own refs rewritten to point at the new query
# names. See liberty/web/clone_with_deps.py for the walker — this endpoint is the thin
# write wrapper that applies the result through the existing parsed-config write paths.

from liberty.web.clone_with_deps import Seed as CloneSeed, clone_with_deps
from liberty.web.delete_with_deps import Seed as DeleteSeed, plan_delete_with_deps


class CloneWithDepsBody(BaseModel):
    kind: str                                  # 'screen' | 'chart' | 'dashboard'
    name: str                                  # source entity id
    scope: str | None = None                   # app for screen / chart / dashboard scope
    new_name: str                              # the cloned entity's id
    suffix: str | None = None                  # appended to each cloned query name; defaults to the diff between new_name and name
    options: dict[str, bool] = {}              # {clone_queries: bool}


@router.post("/clone-with-deps", summary="Clone entity with dependencies")
async def clone_with_deps_endpoint(body: CloneWithDepsBody, request: Request, _: Superuser) -> dict[str, object]:
    """Clone *body.name* under *body.new_name* + (per options) duplicate every referenced
    query under ``<original>_<suffix>``. Rewrites the cloned entity's refs so it's a
    self-contained fork.

    Atomic per-file: dependency queries are written via _apply_connector_queries (grouped
    by connector) and the seed via the matching _apply_<kind>. Hits /admin/reload at the
    end so the new entries are live."""
    if body.kind not in ("screen", "chart", "dashboard"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"clone-with-deps doesn't support kind {body.kind!r}")
    seed = CloneSeed(kind=body.kind, name=body.name, scope=body.scope)
    result = clone_with_deps(
        request.app.state,
        seed=seed, new_name=body.new_name, suffix=body.suffix, options=body.options,
    )
    # Apply order matters: write QUERIES first, then the seed (screen / chart / dashboard)
    # last. Hot-reload watches the files individually; if the screen lands first while its
    # newly-renamed read_query / update_query / etc. doesn't exist yet on the connector,
    # the screen-only reload sees broken refs (the operator then has to restart to recover).
    # Writing queries first means: when the seed's reload fires, the connector already has
    # the cloned queries. The final explicit /admin/reload below stitches everything.
    queries_by_conn: dict[str, list[dict[str, Any]]] = {}
    seed_items: list[Any] = []
    summaries: list[str] = []
    for item in result.items:
        if item.kind == "query":
            cfg = dict(item.config)
            cfg["name"] = item.new_name
            queries_by_conn.setdefault(item.scope or "", []).append(cfg)
        else:
            seed_items.append(item)
    # Pass 1 — queries.
    for conn, queries in queries_by_conn.items():
        if conn:
            summaries.append(_apply_connector_queries(request, {"connector": conn, "queries": queries}))
    # Pass 2 — the seed entity (screen / chart / dashboard) referencing the now-written queries.
    for item in seed_items:
        if item.kind == "screen":
            payload = {"app": item.scope, "screen_id": item.new_name, "screen": {**item.config, "id": item.new_name}}
            summaries.append(_apply_screen(request, payload))
        elif item.kind == "chart":
            # Charts live at [charts.<scope>.<id>] — there's no dedicated apply helper
            # for one-off chart inserts, so we inline the write here (a dedicated
            # _apply_chart helper would be cleaner; follow-up).
            await _apply_chart_clone(request, item.scope, item.new_name, dict(item.config))
            summaries.append(f"cloned chart {item.scope}.{item.new_name}")
        elif item.kind == "dashboard":
            await _apply_dashboard_clone(request, item.scope, item.new_name, dict(item.config))
            summaries.append(f"cloned dashboard {item.scope or '_'}.{item.new_name}")
    await reload_connectors(request, _)  # type: ignore[arg-type]
    return {
        "cloned": True,
        "items": result.to_dict()["items"],
        "warnings": result.warnings,
        "summary": "; ".join(summaries),
    }


async def _apply_chart_clone(request: Request, scope: str | None, new_id: str, config: dict[str, Any]) -> None:
    """Insert ONE chart at ``[charts.<scope>.<new_id>]`` — same shape an apply-proposal
    for charts would use; minimal helper since clone-with-deps is the only caller today."""
    if not scope:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="chart clone needs a scope (= connector)")
    path = Path(request.app.state.settings.charts.config_path)
    cfg = load_charts(path)
    doc: dict[str, Any] = {s: {cid: c.model_dump(exclude_defaults=True, exclude_none=True) for cid, c in by_id.items()}
                            for s, by_id in cfg.charts.items()}
    doc.setdefault(scope, {})[new_id] = {**config, "id": new_id}
    try:
        ChartsFile.model_validate({"charts": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"chart clone invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"charts": doc})
    parse_charts(tomllib.loads(new_text))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")


async def _apply_dashboard_clone(request: Request, scope: str | None, new_id: str, config: dict[str, Any]) -> None:
    """Insert ONE dashboard at ``[dashboards.<scope>.<new_id>]`` — minimal helper, same
    pattern as the chart clone."""
    path = Path(request.app.state.settings.dashboards.config_path)
    cfg = load_dashboards(path)
    doc: dict[str, Any] = {s: {did: d.model_dump(exclude_defaults=True, exclude_none=True) for did, d in by_id.items()}
                            for s, by_id in cfg.dashboards.items()}
    bucket = scope or "_"
    doc.setdefault(bucket, {})[new_id] = {**config, "id": new_id}
    try:
        DashboardsFile.model_validate({"dashboards": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"dashboard clone invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"dashboards": doc})
    parse_dashboards(tomllib.loads(new_text))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")


# ── Delete with dependencies ────────────────────────────────────────────────────────────────
# Mirror of clone-with-deps: destructive. Two endpoints — a PREVIEW one (returns the plan
# so the operator sees what'll happen before confirming) + an APPLY one (executes). The
# planner is in liberty/web/delete_with_deps.py; it only marks queries as deletable when
# every reference to them comes back from the entity being deleted.


class DeleteWithDepsBody(BaseModel):
    kind: str                               # 'screen' | 'chart' | 'dashboard'
    name: str
    scope: str | None = None
    options: dict[str, bool] = {}           # {delete_queries: bool}


@router.post("/delete-with-deps/preview", summary="Preview delete-with-deps")
async def delete_with_deps_preview(body: DeleteWithDepsBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Compute (without applying) the deletion plan: which queries are safely orphaned by
    deleting *body.name* and which would be preserved (still referenced by other entities).
    The frontend uses this to show the operator a preview before they confirm."""
    if body.kind not in ("screen", "chart", "dashboard"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"delete-with-deps doesn't support kind {body.kind!r}")
    plan = plan_delete_with_deps(
        request.app.state,
        seed=DeleteSeed(kind=body.kind, name=body.name, scope=body.scope),
        options=body.options,
    )
    return plan.to_dict()


@router.post("/delete-with-deps", summary="Delete entity with dependencies")
async def delete_with_deps_endpoint(body: DeleteWithDepsBody, request: Request, _: Superuser) -> dict[str, object]:
    """Delete *body.name* (a screen / chart / dashboard) and — when
    ``options.delete_queries`` is set — every query exclusively used by it. Queries
    referenced by other entities are PRESERVED + listed in the response.

    Order matters: the seed entity is removed FIRST (so its refs to the soon-to-be-
    deleted queries are gone), then the queries themselves. Hot-reload sees consistent
    state at each step. The final ``/admin/reload`` stitches the live registries."""
    if body.kind not in ("screen", "chart", "dashboard"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"delete-with-deps doesn't support kind {body.kind!r}")
    seed = DeleteSeed(kind=body.kind, name=body.name, scope=body.scope)
    plan = plan_delete_with_deps(request.app.state, seed=seed, options=body.options)
    summaries: list[str] = []
    # Pass 1 — delete the seed entity. References from the seed to the soon-to-be-
    # deleted queries vanish at this step, so the connector reload (pass 2) doesn't
    # see broken refs even if hot-reload fires mid-flight.
    if body.kind == "screen":
        await _delete_screen(request, body.scope, body.name)
        summaries.append(f"deleted screen {body.scope}.{body.name}")
    elif body.kind == "chart":
        await _delete_chart(request, body.scope, body.name)
        summaries.append(f"deleted chart {body.scope}.{body.name}")
    elif body.kind == "dashboard":
        await _delete_dashboard(request, body.scope, body.name)
        summaries.append(f"deleted dashboard {body.scope or '_'}.{body.name}")
    # Pass 2 — delete each safe query (grouped by connector for one PUT per connector).
    by_conn: dict[str, list[str]] = {}
    for conn, qname in plan.delete_queries:
        by_conn.setdefault(conn, []).append(qname)
    for conn, qnames in by_conn.items():
        await _delete_connector_queries(request, conn, qnames)
        summaries.append(f"deleted {len(qnames)} quer{'y' if len(qnames) == 1 else 'ies'} on {conn}")
    await reload_connectors(request, _)  # type: ignore[arg-type]
    return {
        "deleted": True,
        "summary": "; ".join(summaries),
        "plan": plan.to_dict(),
    }


async def _delete_screen(request: Request, app: str | None, screen_id: str) -> None:
    """Remove ``[screens.<app>.<screen_id>]`` from screens.toml. Validates the resulting
    file before writing — bails with 422 if the delete would leave dangling refs the
    Pydantic validator catches."""
    if not app:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="screen delete needs an app scope")
    path = Path(request.app.state.settings.screens.config_path)
    registry = load_screens(path)
    doc = {a: {sid: s.model_dump(exclude_defaults=False, exclude_none=True) for sid, s in screens.items()}
           for a, screens in registry.screens.items()}
    app_doc = doc.get(app) or {}
    if screen_id not in app_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"screen {app}.{screen_id} not found")
    del app_doc[screen_id]
    if not app_doc:
        del doc[app]
    else:
        doc[app] = app_doc
    try:
        ScreensFile.model_validate({"screens": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"delete leaves screens invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"screens": doc})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")


async def _delete_chart(request: Request, scope: str | None, chart_id: str) -> None:
    """Remove ``[charts.<scope>.<chart_id>]`` from charts.toml."""
    if not scope:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="chart delete needs a scope")
    path = Path(request.app.state.settings.charts.config_path)
    cfg = load_charts(path)
    doc = {s: {cid: c.model_dump(exclude_defaults=True, exclude_none=True) for cid, c in by_id.items()}
           for s, by_id in cfg.charts.items()}
    scope_doc = doc.get(scope) or {}
    if chart_id not in scope_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"chart {scope}.{chart_id} not found")
    del scope_doc[chart_id]
    if not scope_doc:
        del doc[scope]
    else:
        doc[scope] = scope_doc
    try:
        ChartsFile.model_validate({"charts": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"delete leaves charts invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"charts": doc})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")


async def _delete_dashboard(request: Request, scope: str | None, dashboard_id: str) -> None:
    """Remove ``[dashboards.<scope>.<dashboard_id>]`` from dashboards.toml."""
    path = Path(request.app.state.settings.dashboards.config_path)
    cfg = load_dashboards(path)
    doc = {s: {did: d.model_dump(exclude_defaults=True, exclude_none=True) for did, d in by_id.items()}
           for s, by_id in cfg.dashboards.items()}
    bucket = scope or "_"
    scope_doc = doc.get(bucket) or {}
    if dashboard_id not in scope_doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"dashboard {bucket}.{dashboard_id} not found")
    del scope_doc[dashboard_id]
    if not scope_doc:
        del doc[bucket]
    else:
        doc[bucket] = scope_doc
    try:
        DashboardsFile.model_validate({"dashboards": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"delete leaves dashboards invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"dashboards": doc})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")


async def _delete_connector_queries(request: Request, connector: str, query_names: list[str]) -> None:
    """Remove *query_names* from ``[connectors.<connector>]`` via tomlkit so other connectors /
    comments in connectors.toml round-trip untouched.

    *query_names* are flat synthesised names, so a name is matched against all four sections:
    a custom ``queries`` / ``sequences`` / ``lookups`` entry is dropped by name, and a table
    slot (``<base>_<crud>``) is removed from its :class:`TableDef` — a table left with no
    slots is dropped entirely (an operator deleting every CRUD op of a table means to remove
    the table)."""
    path = Path(request.app.state.settings.connectors.config_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    connectors_tbl = doc.get("connectors")
    if connectors_tbl is None or connector not in connectors_tbl:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"connector {connector!r} not found")
    conn_tbl = connectors_tbl[connector]
    to_drop = set(query_names)
    # Flat sections — drop standalone entries by name.
    for section in ("queries", "sequences", "lookups"):
        lst = conn_tbl.get(section)
        if isinstance(lst, list):
            conn_tbl[section] = [q for q in lst if not (isinstance(q, dict) and q.get("name") in to_drop)]
    # Tables — drop the matching ``<base>_<crud>`` slot off each table; remove a table that's
    # left with no slots at all.
    tables = conn_tbl.get("tables")
    if isinstance(tables, list):
        kept_tables = []
        for t in tables:
            if not isinstance(t, dict):
                kept_tables.append(t)
                continue
            base = t.get("name")
            for crud in ("get", "put", "post", "delete"):
                if f"{base}_{crud}" in to_drop:
                    t.pop(crud, None)
            if any(crud in t for crud in ("get", "put", "post", "delete")):
                kept_tables.append(t)
        conn_tbl["tables"] = kept_tables
    # Re-validate the connector after the drops — refuses to write a connector that's now
    # invalid (e.g. a sequence config that pointed at one of the deleted queries).
    try:
        # Re-load the file with the dropped queries and validate the connector.
        normalised = tomllib.loads(tomlkit.dumps(doc))
        cfg = ConnectorsFile.model_validate(normalised)
        if connector not in cfg.connectors:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail=f"connector {connector!r} disappeared from validation")
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"delete leaves connector {connector!r} invalid: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")


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


@router.post("/config/api/test", summary="Test API connector")
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


@router.post("/config/rename", summary="Rename entity (cross-file)")
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
        elif body.kind == "screen":
            if not body.scope:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="a screen rename requires `scope` (the app name)")
            result = rename_screen(
                body.old_name, body.new_name,
                app=body.scope,
                screens_path=Path(settings.screens.config_path),
                menus_path=Path(settings.menus.config_path),
            )
        else:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"rename kind {body.kind!r} not supported — one of: connector, sequence, lookup, screen, screen_app, dictionary_entry, query, table",
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


@router.post("/config/delete-app", summary="Delete app")
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


@router.post("/config/clone-app", summary="Clone app")
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
