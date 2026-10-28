"""``/admin/promotion`` — promote to another instance (C11). Superuser only.

* ``GET / PUT /targets`` — the target instances (``[promotion.targets.<name>]`` in app.toml; the
  password encrypted on save, round-tripped as ``ENC:`` like the App editor's secrets);
  ``POST /targets/{name}/test`` signs in there and returns its name and version.
* ``POST /push`` — build the payload here (a config package from seeds, or an approved change
  package), sign in on the target, and return the target's preview (``apply: false``) or the
  result of its import (``apply: true``). An applied push is recorded in the history; an applied
  change package is marked exported here.
* ``GET /history`` / ``GET /history/{id}`` — the applied pushes, newest first; one with its report.

See :mod:`liberty.promotion`.
"""

from __future__ import annotations

import logging
import re
import tomllib
from typing import Annotated, Any, Literal

import tomlkit
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError

from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.config import PromotionSettings, PromotionTarget, resolve_app_config_path, substitute_env
from liberty.crypto import encrypt
from liberty.promotion import PromotionError, client_for, history_for, push_changes, push_config, sign_in, target_info

router = APIRouter(prefix="/admin/promotion", tags=["admin"])
Superuser = Annotated[Principal, Depends(require_superuser)]
_log = logging.getLogger(__name__)
_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_INSTANCE_KINDS = frozenset({"connector", "pool", "api_endpoint"})   # per instance unless picked


def _settings(request: Request) -> Any:
    return request.app.state.settings


def _target(request: Request, name: str) -> PromotionTarget:
    t = _settings(request).promotion.targets.get(name)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown target {name!r} — add it under Promotion targets")
    return t


def _master_key(request: Request) -> str:
    return getattr(_settings(request).crypto, "master_key", "") or ""


def _transport(request: Request) -> Any:
    return getattr(request.app.state, "promotion_transport", None)   # the tests' in-process target


# ── targets ─────────────────────────────────────────────────────────────────────────────


@router.get("/targets", summary="The promotion targets")
async def get_targets(request: Request, _: Superuser) -> dict[str, Any]:
    """As written in app.toml (the password as its ``ENC:`` ciphertext or ``${VAR}``)."""
    path = resolve_app_config_path()
    raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    targets = ((raw.get("promotion") or {}).get("targets")) or {}
    return {"path": str(path), "targets": targets}


class TargetsBody(BaseModel):
    targets: dict[str, dict[str, Any]] = Field(default_factory=dict)


@router.put("/targets", summary="Save the promotion targets")
async def put_targets(body: TargetsBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Replace ``[promotion.targets]`` in app.toml (tomlkit: the rest of the file untouched); a
    plaintext password is encrypted with the master key. Live at once (no restart)."""
    master_key = _master_key(request)
    clean: dict[str, dict[str, Any]] = {}
    for name, t in body.targets.items():
        if not _NAME.match(name):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"target name {name!r}: lowercase letters, digits, - and _")
        t = {k: v for k, v in t.items() if v is not None}
        pw = str(t.get("password") or "")
        if pw and not pw.startswith(("ENC:", "${")):
            if not master_key:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Cannot store a password: no master_key configured (set LIBERTY_MASTER_KEY).")
            t["password"] = encrypt(pw, master_key)
        clean[name] = t
    try:
        live = PromotionSettings.model_validate(substitute_env({"targets": clean}))
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid targets: {exc}") from exc

    path = resolve_app_config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    if "promotion" not in doc:
        doc["promotion"] = tomlkit.table(is_super_table=True)
    targets = tomlkit.table(is_super_table=True)
    for name, t in clean.items():
        targets[name] = t
    doc["promotion"]["targets"] = targets
    from liberty.web.admin import _snapshot_config
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    _settings(request).promotion = live
    return {"targets": clean}


@router.post("/targets/{name}/test", summary="Sign in on a target")
async def test_target(name: str, request: Request, _: Superuser) -> dict[str, Any]:
    """Reach the target, sign in with its account and read its ``/info``: its name and version."""
    target = _target(request, name)
    try:
        async with client_for(target, _transport(request)) as client:
            await sign_in(client, target, _master_key(request))
            info = await target_info(client, target)
    except PromotionError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return {"ok": True, "name": info.get("name"), "version": info.get("version")}


# ── push ────────────────────────────────────────────────────────────────────────────────


class SeedBody(BaseModel):
    kind: str
    name: str
    scope: str | None = None


class PushBody(BaseModel):
    target: str
    payload: Literal["config", "changes"] = "config"
    # config: the Package selection (as /admin/build-package takes it)
    seeds: list[SeedBody] = Field(default_factory=list)
    include: list[SeedBody] | None = None
    strategy: Literal["merge", "overwrite", "replace_all"] = "overwrite"
    # changes: an approved change package of this instance
    package_id: str | None = None
    force: list[int] = Field(default_factory=list)
    apply: bool = False
    comment: str = ""


async def _config_payload(request: Request, body: PushBody) -> tuple[bytes, str]:
    from liberty import __version__
    from liberty.web.dependencies import Seed, collect_dependencies
    from liberty.web.package import build_package_zip
    if not body.seeds:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="nothing selected to promote")
    manifest = collect_dependencies(request.app.state, [Seed(kind=s.kind, name=s.name, scope=s.scope) for s in body.seeds])
    # No explicit selection = the Package screen's default: connectors, pools and API endpoints stay
    # the target's own (a connector's queries still travel, merged into the target's connector).
    include = ({(i.kind, i.scope, i.name) for i in body.include} if body.include is not None
               else {d.key() for d in manifest.deps if d.kind not in _INSTANCE_KINDS})
    src = getattr(_settings(request).app, "name", "liberty-next")
    blob = build_package_zip(manifest, source_install=src, include=include, settings=_settings(request), source_version=__version__)
    names = ", ".join(f"{s.kind} {s.scope + '.' if s.scope else ''}{s.name}" for s in body.seeds[:3])
    more = f" (+{len(body.seeds) - 3})" if len(body.seeds) > 3 else ""
    return blob, names + more


async def _changes_payload(request: Request, body: PushBody) -> tuple[dict[str, Any], str]:
    from liberty.changesets import store
    from liberty.changesets.compaction import compact
    from liberty.web.changesets import _bundle, _post_apply_ops
    db = getattr(request.app.state, "changesets_db", None)
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="change packages are not enabled on this instance")
    if not body.package_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="package_id is required")
    async with db.session() as session:
        pkg = await store.get_package(session, body.package_id)
        if pkg is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"change package {body.package_id!r} not found")
        if pkg.status not in ("approved", "exported"):
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"only an approved package can be promoted (this one is {pkg.status!r})")
        ops = compact(pkg.entries) + _post_apply_ops(_settings(request), pkg.post_apply_ids, pkg.application)
        return _bundle(pkg, ops), pkg.name


@router.post("/push", summary="Preview or apply on a target")
async def push(body: PushBody, request: Request, principal: Superuser) -> dict[str, Any]:
    target = _target(request, body.target)
    payload: Any
    if body.payload == "config":
        payload, label = await _config_payload(request, body)
    else:
        payload, label = await _changes_payload(request, body)
    try:
        async with client_for(target, _transport(request)) as client:
            token = await sign_in(client, target, _master_key(request))
            info = await target_info(client, target)
            if body.payload == "config":
                report = await push_config(client, target, token, payload, strategy=body.strategy, apply=body.apply)
            else:
                report = await push_changes(client, target, token, payload, apply=body.apply, force=body.force)
    except PromotionError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    out: dict[str, Any] = {
        "target": {"name": body.target, "label": target.label or body.target, "url": target.url,
                   "instance": info.get("name"), "version": info.get("version")},
        "payload": body.payload, "label": label, "applied": body.apply, "report": report,
    }
    if body.apply:
        summary = report.get("summary") or {}
        failed = bool(summary.get("errors") or summary.get("conflict") or summary.get("error"))
        if body.payload == "changes" and not failed:
            from liberty.changesets import store
            try:
                async with request.app.state.changesets_db.session() as session:
                    await store.mark_exported(session, body.package_id or "")
            except Exception:  # noqa: BLE001 — the target applied; the source's status is secondary
                _log.warning("could not mark change package %s exported after its promotion", body.package_id, exc_info=True)
        out["history_id"] = history_for(_settings(request)).record({
            "user": principal.username, "target": body.target, "target_url": target.url,
            "target_version": info.get("version"), "payload": body.payload, "label": label,
            "strategy": body.strategy if body.payload == "config" else None, "comment": body.comment,
            "status": "partial" if failed else "applied", "summary": summary, "report": report,
        })
    return out


# ── history ─────────────────────────────────────────────────────────────────────────────


@router.get("/history", summary="The applied promotions")
async def get_history(request: Request, _: Superuser) -> dict[str, Any]:
    return {"entries": history_for(_settings(request)).list()}


@router.get("/history/{hid}", summary="One applied promotion with its report")
async def get_history_entry(hid: str, request: Request, _: Superuser) -> dict[str, Any]:
    entry = history_for(_settings(request)).get(hid)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no promotion {hid!r}")
    return entry
