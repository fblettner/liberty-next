"""``/admin`` — structured config: the configuration builders' parsed GET / PUT (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

import tomlkit
from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, _drop_none, _dump_screen, _snapshot_config, router
from liberty.web.admin.bundles import _capture_screen_bundles
from pydantic import BaseModel, ValidationError

from liberty.actions.config import SharedActionsFile
from liberty.ai.appconfig import AiFile
from liberty.charts.config import ChartsFile
from liberty.config import (
    substitute_env,
)
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
from liberty.crypto import CryptoError, encrypt, is_encrypted
from liberty.dashboards.config import DashboardsFile
from liberty.explorer import ExplorerFile
from liberty.framework_enums import FRAMEWORK_ENUMS
from liberty.licensing import ALWAYS_LICENSED_CONNECTORS
from liberty.menus import load_menus
from liberty.menus.config import MenusFile, parse_menus
from liberty.screens import Screen, load_screens
from liberty.screens.config import ScreensFile, parse_screens


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
        "explorer": ExplorerFile.model_json_schema(),
        "ai": AiFile.model_json_schema(),
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
    _snapshot_config(request, path)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return {"saved": True, "path": str(path)}


@router.get("/config/integrity", summary="Config integrity check")
async def config_integrity(request: Request, _: Superuser) -> dict[str, Any]:
    """Walk every cross-reference in the loaded config and report broken references (errors) +
    smells (unused connectors / orphan screens — warnings). Runs against the in-memory registries
    (``app.state``), so it reflects the last ``/admin/reload``. Feeds Liberty Next → Build → Integrity."""
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
            # validated as the runtime reads it: ${ENV} references resolved (a boolean such as
            # verify_ssl may be one), the raw value is what gets written
            ConnectorsFile.model_validate(substitute_env({"connectors": {name: raw}}))
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
    _snapshot_config(request, path)
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
    _snapshot_config(request, path)
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
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}


@router.get("/config/screens/parsed", summary="Get screens config")
async def get_screens_parsed(request: Request, _: Superuser) -> dict[str, Any]:
    """The current ``screens.toml`` parsed and normalised — ``{path, screens: {<app>: {<screen_id>:
    Screen dict}}}``. A missing file → an empty screen set. Default-valued keys are dropped (terser
    JSON), **except** the ``type`` discriminator on each tab / action variant which is re-injected
    after the dump — Pydantic's ``exclude_defaults=True`` would otherwise strip it (the Literal
    discriminator's only-allowed value equals its default), leaving the frontend unable to tell
    nested_table tabs from plain form ones AND the PUT round-trip unable to validate
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






class ScreensBody(BaseModel):
    screens: dict[str, dict[str, dict[str, Any]]]


def _order_like(new: Any, old: Any) -> Any:
    """*new* with its keys in *old*'s order (new keys after), at every level — tables of a list paired
    by position when both lists have the same length — so a review diff shows only what changed."""
    if isinstance(new, dict) and isinstance(old, dict):
        keys = [k for k in old if k in new] + [k for k in new if k not in old]
        return {k: _order_like(new[k], old.get(k)) for k in keys}
    if isinstance(new, list) and isinstance(old, list) and len(new) == len(old):
        return [_order_like(n, o) for n, o in zip(new, old, strict=True)]
    return new


def _merge_screens(path: Path, validated: Any) -> dict[str, Any]:
    """The ``screens`` table to write. A submitted screen identical to what the file loads to (both
    seen as the Visual Designer sees them — :func:`_dump_screen`) keeps its table as it is on disk:
    the keys the model ignores (a screen-level ``l``), an explicit ``key_columns``, no defaults. A
    changed or new screen is written as the designer loads it (no defaults, the ``type``
    discriminators kept) plus the unknown keys its table had. Apps, screens and their order follow
    the submission — a screen left out is deleted."""
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8")).get("screens") or {}
        except tomllib.TOMLDecodeError:
            raw = {}
    try:
        # a copy: parse_screens injects each screen's ``id`` into the dicts it is given
        on_disk = parse_screens({"screens": copy.deepcopy(raw)}).screens if raw else {}
    except (ValidationError, ValueError):
        on_disk = {}                                 # an invalid file on disk: every screen is written anew
    known = set(Screen.model_fields)
    out: dict[str, Any] = {}
    for app, screens in validated.screens.items():
        out[app] = {}
        for sid, screen in screens.items():
            old = (on_disk.get(app) or {}).get(sid)
            old_raw = (raw.get(app) or {}).get(sid)
            if old is not None and isinstance(old_raw, dict) and _dump_screen(old) == _dump_screen(screen):
                out[app][sid] = old_raw
                continue
            d = _dump_screen(screen)
            if isinstance(old_raw, dict):
                if "id" not in old_raw:
                    d.pop("id", None)                # the key is the id (parse_screens injects it)
                d.update({k: v for k, v in old_raw.items() if k not in known})
                d = _order_like(d, old_raw)
            else:
                d.pop("id", None)
            out[app][sid] = d
    return out


@router.put("/config/screens/parsed", summary="Update screens config")
async def put_screens_parsed(body: ScreensBody, request: Request, _: Superuser) -> dict[str, object]:
    """Validate the submitted dict against :class:`ScreensFile` (every screen's ``id`` injected from
    its dict key, dialog/tabs/fields/param-binds round-trip cleanly), then write ``screens.toml`` via
    ``tomli-w`` — the screens left unchanged as they are on disk, a changed one without its defaults
    (:func:`_merge_screens`).

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

    # Only the screens the designer changed are rewritten (_merge_screens): the others keep their
    # table as on disk; a changed one is written without its defaults, the ``type`` discriminators
    # kept (the full dump used to write every default of every screen — +70 % of file, keys the
    # model ignores lost). tomli_w as before: milliseconds on the 600 kB file (tomlkit takes a minute).
    import tomli_w
    path = Path(request.app.state.settings.screens.config_path)
    new_text = tomli_w.dumps(_drop_none({"screens": _merge_screens(path, validated)}))
    try:
        parse_screens(tomllib.loads(new_text))   # belt-and-braces re-validation
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting screens are invalid: {exc}") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    # Per-screen dependency bundles — captured from the PRE-save app.state (still live here), so a
    # later restore reverts the screen *with* the queries / lookups / DD entries it referenced.
    _capture_screen_bundles(request, body.screens)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}
