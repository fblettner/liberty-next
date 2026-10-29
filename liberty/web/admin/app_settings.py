"""``/admin`` — app.toml (master settings) (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import tomlkit
from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, _snapshot_config, router
from pydantic import BaseModel, ValidationError

from liberty.ai.catalog import KNOWN_MODELS
from liberty.config import (
    AISettings,
    AppSettings,
    AuthSettings,
    ChangeSetsSettings,
    JobsSettings,
    LicenseSettings,
    NotifySettings,
    OIDCSettings,
    ReportsBrandingSettings,
    load_settings,
    resolve_app_config_path,
)
from liberty.crypto import CryptoError, encrypt
from liberty.licensing import verify_license

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


# Keys safe to expose: secrets, path fields, and backend toggles that require a coordinated
# DB/auth migration stay out. Everything else round-trips through the editor.
_APP_SAFE_KEYS = {
    "name", "host", "port", "log_level", "hot_reload", "default_language", "oracle_thick",
    "default_period", "default_page_size", "dashboard_cache_seconds",
    "tabs_multi", "tabs_close_on_app_switch", "tabs_max", "drill_inline",
    "log_format", "log_file", "log_file_max_mb", "log_file_backups", "base_path", "external_urls",
}
_AI_SAFE_KEYS = {
    "enabled", "model", "max_tokens", "max_iterations", "system_prompt", "thinking",
    "effort", "request_timeout", "connector_tools", "api_tool", "scaffold_tools", "ops_tools", "explorer_tools",
    "allowed_connectors", "web_fetch_domains", "web_fetch_max_uses", "allowed_models", "starters",
    "api_key",   # sensitive — encrypted on save, masked on GET
}
_LICENSE_SAFE_KEYS = {"key"}   # sensitive — encrypted on save, masked on GET
_OIDC_SAFE_KEYS = {
    "enabled", "discovery_url", "client_id", "scopes",
    "username_claim", "email_claim", "name_claim",
    "redirect_url", "frontend_redirect", "attribute_claims",
    "client_secret", "session_secret",   # sensitive — encrypted on save
}

# Fields that get encrypt-on-save + mask-on-get. Identified by (section, field) tuple
# so encryption is targeted (other strings in the same section stay as-is).
_NOTIFY_SAFE_KEYS = {
    "enabled", "pool", "smtp_host", "smtp_port", "smtp_user", "smtp_from", "smtp_tls", "smtp_ssl", "smtp_timeout", "retention_days",
    "smtp_password",   # sensitive — encrypted on save
}
# Sign-in, nomaflow and change packages (C10 — every setting editable in the UI). The secret, the
# algorithm and the file paths stay in the file (liberty.devref.FILE_ONLY says why).
_AUTH_SAFE_KEYS = {
    "backend", "pool", "jwt_issuer", "access_token_ttl", "refresh_token_ttl", "refresh_reuse_grace",
    "login_max_failures", "login_window_seconds",
}
_JOBS_SAFE_KEYS = {"enabled", "pool", "run_log_max_lines", "files_dir", "max_upload_mb"}
_CHANGESETS_SAFE_KEYS = {"enabled", "pool", "retention_days"}
_APP_TOML_SENSITIVE_FIELDS: set[tuple[str, str]] = {
    ("ai", "api_key"),
    ("license", "key"),
    ("oidc", "client_secret"),
    ("oidc", "session_secret"),
    ("notify", "smtp_password"),
}

# The model catalogue the Model dropdown offers (shared with the chat's picker).
_AI_MODELS = KNOWN_MODELS


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
        notify_model = NotifySettings.model_validate(raw.get("notify") or {})
        auth_model = AuthSettings.model_validate(raw.get("auth") or {})
        jobs_model = JobsSettings.model_validate(raw.get("jobs") or {})
        changesets_model = ChangeSetsSettings.model_validate(raw.get("changesets") or {})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"existing app.toml is invalid: {exc}") from exc

    connectors = sorted(request.app.state.connectors.names()) if hasattr(request.app.state, "connectors") else []
    return {
        "path": str(path),
        "app": _pick(app_model.model_dump(), _APP_SAFE_KEYS),
        "ai": _pick(ai_model.model_dump(), _AI_SAFE_KEYS),
        "license": _pick(license_model.model_dump(), _LICENSE_SAFE_KEYS),
        "oidc": _pick(oidc_model.model_dump(), _OIDC_SAFE_KEYS),
        "notify": _pick(notify_model.model_dump(), _NOTIFY_SAFE_KEYS),
        "auth": _pick(auth_model.model_dump(mode="json"), _AUTH_SAFE_KEYS),
        "jobs": _pick(jobs_model.model_dump(mode="json"), _JOBS_SAFE_KEYS),
        "changesets": _pick(changesets_model.model_dump(mode="json"), _CHANGESETS_SAFE_KEYS),
        # What stays in the file (paths, the signing algorithm, the env-only secrets): shown, not
        # edited — the value in force and why (liberty.devref.FILE_ONLY).
        "readonly": _readonly_settings(request),
        "choices": {
            "log_levels": ["debug", "info", "warning", "error", "critical"],
            "effort_levels": ["", "low", "medium", "high", "xhigh", "max"],
            "connectors": connectors,
            "models": _AI_MODELS,
            "pools": sorted(request.app.state.connectors.pools.names()) if hasattr(request.app.state, "connectors") else [],
        },
    }


def _readonly_settings(request: Request) -> list[dict[str, Any]]:
    from liberty.devref import FILE_ONLY
    live = request.app.state.settings
    out = []
    for key, reason in FILE_ONLY.items():
        section, _, name = key.partition(".")
        value = getattr(getattr(live, section, None), name, None)
        if key in ("crypto.master_key", "auth.jwt_secret"):
            value = "set" if value else "not set"   # a secret: whether it is there, never the value
        out.append({"key": key, "value": "" if value is None else str(value), "reason": reason})
    return out


class AppConfigBody(BaseModel):
    app: dict[str, Any] = {}
    ai: dict[str, Any] = {}
    license: dict[str, Any] = {}
    oidc: dict[str, Any] = {}
    notify: dict[str, Any] | None = None   # None: the section is left as it is on disk
    auth: dict[str, Any] | None = None
    jobs: dict[str, Any] | None = None
    changesets: dict[str, Any] | None = None


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
    notify_in = _pick(body.notify, _NOTIFY_SAFE_KEYS) if body.notify is not None else None
    extra_in: dict[str, tuple[dict[str, Any], Any]] = {}
    for section, given, keys, model in (("auth", body.auth, _AUTH_SAFE_KEYS, AuthSettings),
                                        ("jobs", body.jobs, _JOBS_SAFE_KEYS, JobsSettings),
                                        ("changesets", body.changesets, _CHANGESETS_SAFE_KEYS, ChangeSetsSettings)):
        if given is not None:
            picked = _pick(given, keys)
            if section == "jobs" and not picked.get("files_dir"):
                picked.pop("files_dir", None)   # blank = the default work area (next to the config)
            extra_in[section] = (picked, model)
    try:
        for _sec, (picked, model) in extra_in.items():
            model.model_validate(picked)
        AppSettings.model_validate(app_in)
        AISettings.model_validate(ai_in)
        LicenseSettings.model_validate(license_in)
        OIDCSettings.model_validate(oidc_in)
        if notify_in is not None:
            NotifySettings.model_validate(notify_in)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid app settings: {exc}") from exc

    # Encrypt sensitive fields before writing. Master key comes from the LIVE settings
    # (operator can rotate it via env without restart for this purpose — though full
    # rotation needs re-saving every secret).
    master_key = request.app.state.settings.crypto.master_key if hasattr(request.app.state, "settings") else ""
    ai_in = _encrypt_sensitives("ai", ai_in, master_key)
    license_in = _encrypt_sensitives("license", license_in, master_key)
    oidc_in = _encrypt_sensitives("oidc", oidc_in, master_key)
    if notify_in is not None:
        notify_in = _encrypt_sensitives("notify", notify_in, master_key)

    path = _app_config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()

    def _upsert(section: str, values: dict[str, Any]) -> None:
        if section not in doc:
            doc[section] = tomlkit.table()
        for k, v in values.items():
            if v is None:   # no value (app.log_file left blank…): TOML has no null — drop the key
                if k in doc[section]:
                    del doc[section][k]
            else:
                doc[section][k] = v

    _upsert("app", app_in)
    _upsert("ai", ai_in)
    _upsert("license", license_in)
    _upsert("oidc", oidc_in)
    if notify_in is not None:
        _upsert("notify", notify_in)
    for sec, (picked, _model) in extra_in.items():
        _upsert(sec, picked)
        if sec == "jobs" and "files_dir" not in picked and "jobs" in doc and "files_dir" in doc["jobs"]:
            del doc["jobs"]["files_dir"]

    new_text = tomlkit.dumps(doc)
    try:
        reparsed = tomllib.loads(new_text)
        AppSettings.model_validate(reparsed.get("app") or {})
        AISettings.model_validate(reparsed.get("ai") or {})
        LicenseSettings.model_validate(reparsed.get("license") or {})
        OIDCSettings.model_validate(reparsed.get("oidc") or {})
        NotifySettings.model_validate(reparsed.get("notify") or {})
        AuthSettings.model_validate(reparsed.get("auth") or {})
        JobsSettings.model_validate(reparsed.get("jobs") or {})
        ChangeSetsSettings.model_validate(reparsed.get("changesets") or {})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting app.toml is invalid: {exc}") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
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
        # thick mode is process-global, set once at the first Oracle connection
        or getattr(new_settings.app, "oracle_thick", None) != getattr(old_settings.app, "oracle_thick", None)
        # read once at startup: the log file, the sub-path, sign-in, nomaflow, change packages
        or any(getattr(new_settings.app, k) != getattr(old_settings.app, k)
               for k in ("log_format", "log_file", "log_file_max_mb", "log_file_backups", "base_path"))
        or new_settings.auth != old_settings.auth or new_settings.jobs != old_settings.jobs
        or new_settings.changesets.model_dump(exclude={"post_apply"}) != old_settings.changesets.model_dump(exclude={"post_apply"})
        or new_settings.oidc.session_secret != old_settings.oidc.session_secret
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
        from liberty.runtime import rebuild_runtime
        try:
            # Rebuilds the registry AND rebinds everything holding its pools (auth backend,
            # change packages, saved views, nomaflow, the AI tools, reports) in one place.
            await rebuild_runtime(request.app, sections={"connectors"}, license_result=new_license)
        except Exception as exc:  # noqa: BLE001 — surface as 500 so the UI shows the cause
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Saved {path}, but rebuilding connectors with the new license failed: {exc}",
            ) from exc
    else:
        # No connector swap — still rebuild the AI assistant, its [ai] settings may have changed.
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
    _snapshot_config(request, path)
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
    _snapshot_config(request, path)
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
    markdown. Used by the Liberty Next → Build → Report templates editor's "Test render" button.

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

    Used by the Liberty Next → Build → Report templates editor for two things:

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
