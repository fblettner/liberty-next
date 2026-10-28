"""Promote to another instance (C11): sign in on a target Liberty and have it preview or apply a
payload this instance built — a config package (Package & Deploy) or an approved change package
(Liberty Next → Deploy → Changes). The target does the work through its own routes (``/admin/import-package``,
``/admin/changesets/apply``), so a promotion is exactly what an operator importing the file there
would get; the source only carries it and keeps a history of what it applied where.

The targets are ``[promotion.targets.<name>]`` in app.toml (:class:`liberty.config.PromotionTarget`).
The history is one JSON file per applied push under ``<config dir>/.promotion/`` (next to
``.versions/``, so a backup carries it).
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from liberty.config import PromotionTarget
from liberty.crypto import decrypt_if_needed


class PromotionError(Exception):
    """The target couldn't be reached, refused the sign-in or refused the payload — the message
    says which, in words an operator can act on."""


def _url(target: PromotionTarget, path: str) -> str:
    return target.url.rstrip("/") + path


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        d = body.get("detail") if isinstance(body, dict) else None
        if isinstance(d, str):
            return d
        if d is not None:
            return json.dumps(d)[:300]
    except ValueError:
        pass
    return f"HTTP {resp.status_code}"


def client_for(target: PromotionTarget, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """The HTTP client of one push (*transport* lets the tests run the target in-process)."""
    return httpx.AsyncClient(verify=target.verify_ssl, timeout=target.timeout_seconds, transport=transport)


async def sign_in(client: httpx.AsyncClient, target: PromotionTarget, master_key: str) -> str:
    """Sign in on *target* with its account; returns the access token."""
    password = decrypt_if_needed(target.password, master_key) if target.password else ""
    try:
        resp = await client.post(_url(target, "/auth/login"), json={"username": target.username, "password": password})
    except httpx.HTTPError as exc:
        raise PromotionError(f"cannot reach {target.url}: {exc}") from exc
    if resp.status_code != 200:
        raise PromotionError(f"sign-in on {target.url} failed: {_detail(resp)}")
    token = (resp.json() or {}).get("access_token")
    if not token:
        raise PromotionError(f"sign-in on {target.url} returned no token")
    return str(token)


async def target_info(client: httpx.AsyncClient, target: PromotionTarget) -> dict[str, Any]:
    """The target's public ``/info`` (its name and framework version)."""
    try:
        resp = await client.get(_url(target, "/info"))
    except httpx.HTTPError as exc:
        raise PromotionError(f"cannot reach {target.url}: {exc}") from exc
    if resp.status_code != 200:
        raise PromotionError(f"{target.url}/info answered {_detail(resp)}")
    return resp.json() or {}


async def push_config(
    client: httpx.AsyncClient, target: PromotionTarget, token: str, zip_bytes: bytes, *, strategy: str, apply: bool,
) -> dict[str, Any]:
    """The target's import of a config package: its plan (a preview unless *apply*)."""
    params = {"strategy": strategy, "dry_run": "false" if apply else "true"}
    try:
        resp = await client.post(
            _url(target, "/admin/import-package"), params=params, headers={"Authorization": f"Bearer {token}"},
            files={"package": ("package.zip", zip_bytes, "application/zip")},
        )
    except httpx.HTTPError as exc:
        raise PromotionError(f"cannot reach {target.url}: {exc}") from exc
    if resp.status_code != 200:
        raise PromotionError(f"the target refused the package: {_detail(resp)}")
    return (resp.json() or {}).get("report") or {}


async def push_changes(
    client: httpx.AsyncClient, target: PromotionTarget, token: str, bundle: dict[str, Any], *, apply: bool, force: list[int],
) -> dict[str, Any]:
    """The target's replay of a change bundle: its drift report (a dry run unless *apply*)."""
    try:
        resp = await client.post(
            _url(target, "/admin/changesets/apply"), headers={"Authorization": f"Bearer {token}"},
            json={"bundle": bundle, "dry_run": not apply, "force": force},
        )
    except httpx.HTTPError as exc:
        raise PromotionError(f"cannot reach {target.url}: {exc}") from exc
    if resp.status_code != 200:
        raise PromotionError(f"the target refused the change package: {_detail(resp)}")
    return resp.json() or {}


# ── history ─────────────────────────────────────────────────────────────────────────────


class PromotionHistory:
    """One JSON file per applied push, newest first on listing."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def record(self, entry: dict[str, Any]) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        hid = f"{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        body = {"id": hid, "at": now.isoformat(timespec="seconds"), **entry}
        (self.directory / f"{hid}.json").write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
        return hid

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        """The entries without their report, newest first."""
        if not self.directory.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for p in sorted(self.directory.glob("*.json"), reverse=True)[:limit]:
            try:
                e = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            out.append({k: v for k, v in e.items() if k != "report"})
        return out

    def get(self, hid: str) -> dict[str, Any] | None:
        if not hid or "/" in hid or "\\" in hid or hid.startswith("."):
            return None
        p = self.directory / f"{hid}.json"
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None


def history_for(settings: Any) -> PromotionHistory:
    return PromotionHistory(Path(settings.connectors.config_path).parent / ".promotion")
