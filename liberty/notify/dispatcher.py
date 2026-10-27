"""The notification dispatcher — one message, three channels, recipients by user, role or address.

* ``portal``: an inbox row per recipient user (:class:`~liberty.notify.store.NotificationStore`)
  and a ``notification`` Socket.IO event to that user's room, so the bell updates at once.
* ``email``: SMTP from ``[notify]`` in ``app.toml`` (stdlib ``smtplib`` in a worker thread), to the
  users' addresses from the auth store and to the ``email:`` recipients.
* ``action``: a connector query or endpoint run with the message's payload as its parameters —
  a webhook, a Teams connector, a table that logs events.

Recipients: ``user:<name>``, ``role:<name>`` (every active user holding the role), ``email:<addr>``.
Each channel is isolated: a failure is logged and reported, never raised to the caller, and never
blocks the other channels. Subject and body take ``{placeholders}`` from the payload.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import smtplib
import string
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Awaitable, Callable

from liberty.notify.store import NotificationStore

_log = logging.getLogger(__name__)

CHANNELS = ("portal", "email", "action")


class _Keep(dict):
    """``str.format_map`` helper: an unknown placeholder stays as written instead of raising."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(text: str | None, payload: dict[str, Any] | None) -> str:
    """``{placeholder}`` substitution from *payload* (keys matched as given and upper-cased, so a
    row's ``PERIOD`` serves ``{period}`` too). Unknown names are left as they are."""
    if not text:
        return ""
    values: dict[str, Any] = {}
    for k, v in (payload or {}).items():
        values[str(k)] = "" if v is None else v
        values.setdefault(str(k).lower(), "" if v is None else v)
        values.setdefault(str(k).upper(), "" if v is None else v)
    try:
        return string.Formatter().vformat(text, (), _Keep(values))
    except (ValueError, IndexError):
        return text


@dataclass(slots=True)
class Message:
    subject: str
    body: str | None = None
    link: str | None = None
    rule: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ActionTarget:
    """What the ``action`` channel runs: a SQL query or an API endpoint of a connector."""

    connector: str
    query: str | None = None
    endpoint: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DispatchReport:
    portal_users: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    action_ran: bool = False
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"portal_users": self.portal_users, "emails": self.emails, "action_ran": self.action_ran, "errors": dict(self.errors)}


@dataclass(slots=True)
class Recipient:
    username: str | None
    email: str | None


class Dispatcher:
    """Built once per app (and rebuilt on reload — see :func:`liberty.runtime`)."""

    def __init__(
        self, store: NotificationStore | None, settings: Any, *,
        auth_backend: Callable[[], Any], connectors: Callable[[], Any], sio: Callable[[], Any] | None = None,
    ) -> None:
        self._store = store
        # liberty.config.Settings, or a callable returning it: the App editor swaps
        # ``app.state.settings`` on save, so the SMTP account is read at send time.
        self._settings = settings
        self._auth_backend = auth_backend   # callables: the app's holders are rebuilt on reload
        self._connectors = connectors
        self._sio = sio or (lambda: None)

    # ── recipients ──────────────────────────────────────────────────────────────
    async def resolve(self, recipients: list[str]) -> list[Recipient]:
        """``user:`` / ``role:`` / ``email:`` → (username, email) pairs, without duplicates."""
        out: list[Recipient] = []
        seen: set[tuple[str | None, str | None]] = set()
        users: list[Any] | None = None
        backend = self._auth_backend()

        async def all_users() -> list[Any]:
            nonlocal users
            if users is None:
                users = list(await backend.list_users()) if backend is not None else []
            return users

        def push(username: str | None, email: str | None) -> None:
            key = (username, (email or "").lower() or None)
            if key not in seen and (username or email):
                seen.add(key)
                out.append(Recipient(username=username, email=email or None))

        for raw in recipients:
            kind, _, name = str(raw).partition(":")
            kind, name = kind.strip().lower(), name.strip()
            if not name and "@" in kind:
                # no prefix, an address: the same reading as a job's alerts.recipients
                kind, name = "email", kind
            if not name:
                _log.warning("notify: recipient %r ignored (use user:<name> / role:<name> / email:<address>)", raw)
                continue
            if kind == "email":
                push(None, name)
            elif kind == "user":
                rec = await backend.get_by_username(name) if backend is not None else None
                push(name, getattr(rec, "email", None))
            elif kind == "role":
                for u in await all_users():
                    if name in (getattr(u, "roles", None) or []) and getattr(u, "is_active", True):
                        push(u.username, getattr(u, "email", None))
            else:
                _log.warning("notify: unknown recipient kind %r (use user: / role: / email:)", raw)
        return out

    # ── dispatch ────────────────────────────────────────────────────────────────
    async def dispatch(
        self, message: Message, *, channels: list[str], recipients: list[str], action: ActionTarget | None = None,
        attachments: list[tuple[str, bytes]] | None = None,
    ) -> DispatchReport:
        report = DispatchReport()
        subject = render(message.subject, message.payload)
        body = render(message.body, message.payload)
        link = render(message.link, message.payload) or None
        wanted = [c for c in channels if c in CHANNELS] or ["portal"]
        targets = await self.resolve(recipients) if ("portal" in wanted or "email" in wanted) else []

        if "portal" in wanted:
            try:
                usernames = [r.username for r in targets if r.username]
                if self._store is None:
                    raise RuntimeError("the notification store is unavailable")
                rows = await self._store.add(usernames, subject=subject, body=body or None, link=link, rule=message.rule, payload=message.payload)
                report.portal_users = [r["username"] for r in rows]
                sio = self._sio()
                if sio is not None:
                    for row in rows:
                        try:
                            await sio.notify_user(row["username"], row)
                        except Exception as exc:  # noqa: BLE001 — a push is best-effort
                            _log.debug("notify: push to %s failed: %s", row["username"], exc)
            except Exception as exc:  # noqa: BLE001 — channel isolation
                _log.warning("notify: portal channel failed: %s", exc)
                report.errors["portal"] = str(exc)

        if "email" in wanted:
            addresses = [r.email for r in targets if r.email]
            if addresses:
                try:
                    await asyncio.to_thread(self._send_email, addresses, subject, body, link, attachments or [])
                    report.emails = addresses
                except Exception as exc:  # noqa: BLE001
                    _log.warning("notify: email channel failed: %s", exc)
                    report.errors["email"] = str(exc)

        if "action" in wanted and action is not None:
            try:
                await self._run_action(action, message, subject=subject, body=body)
                report.action_ran = True
            except Exception as exc:  # noqa: BLE001
                _log.warning("notify: action channel failed: %s", exc)
                report.errors["action"] = str(exc)
        return report

    # ── email ───────────────────────────────────────────────────────────────────
    def _cfg(self) -> Any:
        return self._settings() if callable(self._settings) else self._settings

    def _send_email(
        self, addresses: list[str], subject: str, body: str, link: str | None = None, attachments: list[tuple[str, bytes]] | None = None,
    ) -> None:
        n = self._cfg().notify
        if not n.smtp_host:
            raise RuntimeError("[notify] smtp_host is not set")
        password = n.smtp_password or ""
        if password.startswith("ENC:"):
            from liberty.crypto import decrypt
            password = decrypt(password, self._cfg().crypto.master_key)
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = n.smtp_from or n.smtp_user or "liberty@localhost"
        msg["To"] = ", ".join(addresses)
        text = body or subject
        if link:
            text = f"{text}\n\n{link}"
        msg.set_content(text)
        for name, data in attachments or []:
            ctype, _enc = mimetypes.guess_type(name)
            maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
        smtp_cls = smtplib.SMTP_SSL if n.smtp_ssl else smtplib.SMTP
        try:
            with smtp_cls(n.smtp_host, n.smtp_port, timeout=n.smtp_timeout) as smtp:
                if n.smtp_tls and not n.smtp_ssl:
                    smtp.starttls()
                if n.smtp_user:
                    try:
                        smtp.login(n.smtp_user, password)
                    except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected) as exc:
                        # Gmail and others drop the connection on a refused login rather than
                        # answering 535 — name the likely cause instead of "connection closed".
                        raise RuntimeError(
                            f"login refused by {n.smtp_host} for user {n.smtp_user!r} ({exc}) — the user is usually the "
                            "full address, and Gmail / Microsoft want an app password, not the account password"
                        ) from exc
                smtp.send_message(msg)
        except smtplib.SMTPServerDisconnected as exc:
            raise RuntimeError(
                f"{n.smtp_host}:{n.smtp_port} closed the connection ({exc}) — check the port matches the encryption "
                "(STARTTLS on 587, SSL on 465)"
            ) from exc

    # ── action ──────────────────────────────────────────────────────────────────
    async def _run_action(self, action: ActionTarget, message: Message, *, subject: str, body: str) -> None:
        registry = self._connectors()
        if registry is None:
            raise RuntimeError("the connector registry is unavailable")
        params: dict[str, Any] = {**message.payload, **{k: render(str(v), message.payload) if isinstance(v, str) else v for k, v in action.params.items()}}
        params.setdefault("SUBJECT", subject)
        params.setdefault("BODY", body)
        params.setdefault("RULE", message.rule or "")
        conn = registry.get(action.connector)
        if action.query:
            await conn.execute(action.query, params)
        elif action.endpoint:
            await conn.call(action.endpoint, params)
        else:
            raise RuntimeError("an action needs a query or an endpoint")


# Something awaitable that resolves recipients and dispatches — what the routes and hooks hold.
DispatchFn = Callable[..., Awaitable[DispatchReport]]
