"""The licence usage report — what a licensed installation tells the licence management tool
(nomalms) once a day: which environment it is, its version and how many enabled accounts it
has. Nothing else: no figure of the applications, no name, no data.

The same report NomaUBL sends (``custom.ubl.license.UsageReport``), to the same address, in the
same shape — which is built into NomaUBL and must not change: ``POST <url>`` with the licence
key as bearer and ``{environment, fingerprint, version, users, full, months}``, plus ``product``
(an optional field the tool files the installation by). Liberty is two products there, so two
reports go, the same but for those two fields: ``liberty-next`` with the framework's version,
always, and ``liberty-apps`` with the installed applications' version, when they are installed.
Liberty has no monthly figures, so ``months`` is empty and ``full`` is true.

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
from liberty.config_merge import apps_version

_log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20.0
# The licence tool's product names — exact: it files each report under the product of that name.
FRAMEWORK_PRODUCT = "liberty-next"
APPS_PRODUCT = "liberty-apps"


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


async def build_reports(settings: Settings, connectors: Any) -> list[dict[str, Any]]:
    """What is sent: one report per product of the installation, the same but for ``product``
    and ``version`` — the framework always, the applications when they are installed. The names
    are the licence tool's exact product names (a plain "liberty" would match neither)."""
    from liberty import __version__

    products = [(FRAMEWORK_PRODUCT, __version__)]
    installed = apps_version(settings.connectors.config_path.parent)
    if installed:
        products.append((APPS_PRODUCT, installed))
    environment = (settings.app.environment or "").strip()
    print_ = fingerprint(settings, connectors)
    users = await enabled_users(settings, connectors)
    reports: list[dict[str, Any]] = []
    for product, version in products:
        report: dict[str, Any] = {"product": product, "environment": environment, "fingerprint": print_, "version": version}
        if users is not None:
            report["users"] = users
        report["full"] = True       # no monthly figures: this is the whole report
        report["months"] = []
        reports.append(report)
    return reports


async def send_usage_report(*, settings: Settings, connectors: Any, **_: object) -> dict[str, Any]:
    """Send the reports; the job run's result says what was sent and what the tool answered.
    Anything that prevents a send is raised, so the run fails with the reason."""
    url = (settings.license.usage_url or "").strip()
    if not url:
        raise UsageReportError("no address configured ([license] usage_url is blank), nothing sent")
    key = licence_key(settings)
    if not key:
        raise UsageReportError("no licence key, nothing sent")
    reports = await build_reports(settings, connectors)
    sent: list[str] = []
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        for report in reports:
            try:
                res = await client.post(url, json=report, headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as exc:
                raise UsageReportError(f"{report['product']}: {type(exc).__name__} from {url}: {exc}") from exc
            answer = res.text.strip()
            if not res.is_success:
                raise UsageReportError(f"{report['product']}: HTTP {res.status_code} from {url}" + (f" : {answer[:300]}" if answer else ""))
            sent.append(f"{report['product']} {report['version']} -> HTTP {res.status_code}")
    first = reports[0]
    summary = (
        f"environment {first['environment'] or '(unnamed)'}"
        + (f", {first['users']} enabled account(s)" if "users" in first else "")
        + " — " + "; ".join(sent)
    )
    _log.info("usage report sent to %s: %s", url, summary)
    return {"sent": summary, "to": url, "reports": reports}
