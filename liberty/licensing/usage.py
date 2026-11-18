"""The licence usage report — what a licensed installation tells the licence management tool
(nomalms) once a day: which environment it is, its version and how many enabled accounts it
has. Nothing else: no figure of the applications, no name, no data.

The same report NomaUBL sends (``custom.ubl.license.UsageReport``), to the same address, in the
same shape — which is built into NomaUBL and must not change: ``POST <url>`` with the licence
key as bearer and ``{environment, fingerprint, version, users, full, months}``. Liberty has no
monthly figures, so ``months`` is empty and ``full`` is true (the report is the whole of it).

It runs as the nomaflow job ``liberty-usage-report`` (:mod:`liberty.jobs.builtin`), visible and
pausable like any job, on purpose: the customer can see what is sent, when, and what the tool
answered. Without a licence key or with ``[license] usage_url`` blank, nothing is sent.
"""

from __future__ import annotations

import hashlib
import logging
import socket
from typing import Any

import httpx

from liberty.config import Settings

_log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20.0


class UsageReportError(RuntimeError):
    """The report could not be sent — the job run shows the reason."""


def licence_key(settings: Settings) -> str:
    """The licence key as issued (decrypted when stored ``ENC:``); "" when there is none."""
    key = (settings.license.key or "").strip()
    if key.startswith("ENC:"):
        from liberty.crypto import decrypt

        if not settings.crypto.master_key:
            raise UsageReportError("the licence key is encrypted but no master key is configured, nothing sent")
        try:
            key = decrypt(key, settings.crypto.master_key).strip()
        except Exception as exc:  # noqa: BLE001
            raise UsageReportError(f"the licence key could not be decrypted, nothing sent ({exc})") from exc
    return key


def fingerprint(settings: Settings, connectors: Any) -> str:
    """Tells one installation from another without naming it: a short hash of where its accounts
    live — the auth pool's address (two servers on one database are one installation), or, for
    the file backend, this host and the file's path."""
    auth = settings.auth
    if auth.backend == "db":
        from sqlalchemy.engine import make_url

        cfg = connectors.pools._config(auth.pool)
        url = make_url(cfg.url)
        where = f"{url.host or ''}:{url.port or ''}/{url.database or ''}"
    else:
        where = f"{socket.gethostname()}|{auth.toml_path.resolve()}"
    return hashlib.sha256(where.lower().encode("utf-8")).hexdigest()[:16]


async def enabled_users(settings: Settings, connectors: Any) -> int | None:
    """The enabled accounts, or None when the store cannot be read (the report goes without)."""
    from liberty.auth.authstore import build_auth_backend

    try:
        backend = build_auth_backend(settings, connectors.pools)
        return sum(1 for u in await backend.list_users() if u.is_active)
    except Exception as exc:  # noqa: BLE001
        _log.warning("usage report: the accounts could not be counted (%s)", exc)
        return None


async def build_report(settings: Settings, connectors: Any) -> dict[str, Any]:
    """What is sent — also what ``GET /api/license/usage`` shows."""
    from liberty import __version__

    report: dict[str, Any] = {
        "environment": (settings.app.environment or "").strip(),
        "fingerprint": fingerprint(settings, connectors),
        "version": __version__,
    }
    users = await enabled_users(settings, connectors)
    if users is not None:
        report["users"] = users
    report["full"] = True       # no monthly figures: this is the whole report
    report["months"] = []
    return report


async def send_usage_report(*, settings: Settings, connectors: Any, **_: object) -> dict[str, Any]:
    """Send the report; the job run's result says what was sent and what the tool answered.
    Anything that prevents the send is raised, so the run fails with the reason."""
    url = (settings.license.usage_url or "").strip()
    if not url:
        raise UsageReportError("no address configured ([license] usage_url is blank), nothing sent")
    key = licence_key(settings)
    if not key:
        raise UsageReportError("no licence key, nothing sent")
    report = await build_report(settings, connectors)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            res = await client.post(url, json=report, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        raise UsageReportError(f"{type(exc).__name__} from {url}: {exc}") from exc
    answer = res.text.strip()
    if not res.is_success:
        raise UsageReportError(f"HTTP {res.status_code} from {url}" + (f" : {answer[:300]}" if answer else ""))
    sent = (
        f"environment {report['environment'] or '(unnamed)'}, version {report['version']}"
        + (f", {report['users']} enabled account(s)" if "users" in report else "")
    )
    _log.info("usage report sent to %s: %s -> HTTP %s", url, sent, res.status_code)
    return {"sent": sent, "to": url, "status": res.status_code, "report": report}
