"""Where events meet rules: the write route calls :func:`fire_screen_event` after a successful
write through a screen, the job runner calls :func:`fire_job_event` when a run ends, and a
screen's ``send_notification`` action calls :func:`fire_action_rule`. Each matching rule is
dispatched on its own task so the caller never waits on a mail server; failures are logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from liberty.notify.dispatcher import ActionTarget, Dispatcher, DispatchReport, Message
from liberty.notify.rules import NotificationRule, NotificationsFile

_log = logging.getLogger(__name__)

# Tasks in flight — kept so they are not garbage-collected mid-way and tests can await them.
_pending: set[asyncio.Task] = set()


def _action_of(rule: NotificationRule) -> ActionTarget | None:
    a = rule.action
    if a is None:
        return None
    return ActionTarget(connector=a.connector, query=a.query, endpoint=a.endpoint, params=dict(a.params))


async def dispatch_rule(dispatcher: Dispatcher, rule_id: str, rule: NotificationRule, payload: dict[str, Any]) -> DispatchReport:
    msg = Message(subject=rule.subject, body=rule.body, link=rule.link, rule=rule_id, payload=payload)
    report = await dispatcher.dispatch(msg, channels=list(rule.channels), recipients=list(rule.recipients), action=_action_of(rule))
    if report.errors:
        _log.warning("notify: rule %s delivered with errors: %s", rule_id, report.errors)
    else:
        _log.info("notify: rule %s → portal %s, email %s, action %s", rule_id, report.portal_users, report.emails, report.action_ran)
    return report


def _spawn(coro: Any) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return task


async def drain() -> None:
    """Wait for every in-flight dispatch (tests, shutdown)."""
    if _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


def fire_screen_event(
    rules: NotificationsFile | None, dispatcher: Dispatcher | None, *, app: str, screen: str, event: str,
    row: Mapping[str, Any], user: str | None,
) -> list[asyncio.Task]:
    """Match the screen rules against the written row and dispatch each on its own task.
    The payload is the row plus ``USER`` / ``APP`` / ``SCREEN`` / ``EVENT``."""
    if rules is None or dispatcher is None:
        return []
    matches = rules.for_screen(app, screen, event, row)
    if not matches:
        return []
    payload = {**{str(k): v for k, v in row.items()}, "USER": user or "", "APP": app, "SCREEN": screen, "EVENT": event}
    return [_spawn(dispatch_rule(dispatcher, rid, r, payload)) for rid, r in matches]


def fire_job_event(
    rules: NotificationsFile | None, dispatcher: Dispatcher | None, *, job_id: str, run_id: str, state: str,
    error: str | None, duration_minutes: float | None, rows: int | None, extra_recipients: list[str] | None = None,
) -> list[asyncio.Task]:
    """A finished run: ``failure`` / ``succeeded`` / ``canceled`` from the state, plus ``long_run``
    when a rule's threshold is met. *extra_recipients* (the job's own ``alerts.recipients``)
    get a plain failure message even without a rule."""
    if dispatcher is None:
        return []
    event = {"FAILED": "failure", "SUCCEEDED": "succeeded", "CANCELED": "canceled"}.get(state, state.lower())
    payload = {
        "JOB": job_id, "RUN_ID": run_id, "STATE": state, "ERROR": error or "",
        "DURATION_MINUTES": round(duration_minutes, 1) if duration_minutes is not None else "", "ROWS": rows if rows is not None else "",
    }
    tasks: list[asyncio.Task] = []
    if rules is not None:
        for rid, r in rules.for_job(job_id, event):
            tasks.append(_spawn(dispatch_rule(dispatcher, rid, r, payload)))
        for rid, r in rules.for_job(job_id, "long_run", duration_minutes=duration_minutes):
            tasks.append(_spawn(dispatch_rule(dispatcher, rid, r, payload)))
    if event == "failure" and extra_recipients:
        recipients = [x if ":" in x else f"email:{x}" for x in extra_recipients]
        msg = Message(subject=f"Job {job_id} failed", body=error or "", link=f"/nomaflow/runs/{run_id}", rule=f"job:{job_id}", payload=payload)
        tasks.append(_spawn(dispatcher.dispatch(msg, channels=["portal", "email"], recipients=recipients)))
    return tasks


async def fire_action_rule(
    rules: NotificationsFile | None, dispatcher: Dispatcher | None, *, rule_id: str, payload: Mapping[str, Any], user: str | None,
) -> DispatchReport | None:
    """A ``send_notification`` action: the rule must exist, be enabled and be of kind ``action``
    (the operator opted it in for screens); ``None`` otherwise. Awaited, so the action sees the
    report."""
    if rules is None or dispatcher is None:
        return None
    rule = rules.for_action(rule_id)
    if rule is None:
        return None
    data = {**{str(k): v for k, v in payload.items()}, "USER": user or ""}
    return await dispatch_rule(dispatcher, rule_id, rule, data)
