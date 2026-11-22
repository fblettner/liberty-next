"""``notify`` step — send a message through the notification dispatcher (portal / e-mail),
with the files this run produced attached (C5: the digest's delivery). Placeholders
in the subject and body: ``{JOB}``, ``{RUN_ID}``, ``{FILES}`` (the attached names), ``{DATE}``,
``{USER}`` (who started the run).

A recipient may name the person who started the run: ``user:{USER}`` — "send me what I asked
for". A run nobody started (a schedule, a hook) has no such person: that recipient is left out,
and a step left with no recipient at all sends nothing and says so, without failing the run.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable

from liberty.jobs.schema import Step, StepType
from liberty.jobs.steps.base import RunContext, StepFailed, StepResult

_log = logging.getLogger(__name__)

RUNNER = "{USER}"


def run_user(ctx: RunContext) -> str:
    """Who started the run — ``""`` for a run no user started (a schedule, another job, a hook)."""
    return str(getattr(ctx.trigger, "triggered_by", None) or "").strip()


def recipients_for(recipients: list[str], user: str) -> list[str]:
    """The step's recipients with ``{USER}`` filled in; one that names the runner is left out when
    nobody started the run."""
    out: list[str] = []
    for raw in recipients:
        if RUNNER in raw:
            if not user:
                continue
            raw = raw.replace(RUNNER, user)
        if raw not in out:
            out.append(raw)
    return out


class NotifyStepExecutor:
    def __init__(self, state: Callable[[], Any]) -> None:
        self._state = state

    async def execute(self, step: Step, ctx: RunContext) -> StepResult:
        if step.type is not StepType.NOTIFY:
            raise StepFailed(f"NotifyStepExecutor cannot run a {step.type.value} step")
        st = self._state()
        dispatcher = getattr(st, "notify", None)
        if dispatcher is None:
            raise StepFailed("notify: the notification dispatcher is unavailable ([notify] enabled?)")
        from liberty.jobs.files import job_dir
        from liberty.notify.dispatcher import Message
        settings = getattr(st, "settings", None)
        attachments: list[tuple[str, bytes]] = []
        if settings is not None and step.attach:
            d = job_dir(settings, ctx.job_id)
            if d.is_dir():
                wanted = [a for a in step.attach if a != "*"]
                for p in sorted(d.iterdir()):
                    if not p.is_file() or not p.name.startswith(f"{ctx.run_id}-"):
                        continue
                    short = p.name[len(ctx.run_id) + 1:]
                    if "*" in step.attach or short in wanted or p.name in wanted:
                        attachments.append((short, p.read_bytes()))
            missing = [a for a in step.attach if a != "*" and a not in {n for n, _ in attachments} and a not in {f"{ctx.run_id}-{n}" for n, _ in attachments}]
            if missing:
                raise StepFailed(f"notify: file(s) not produced by this run: {', '.join(missing)}")
        user = run_user(ctx)
        recipients = recipients_for(list(step.recipients), user)
        if step.recipients and not recipients:                  # only "who started the run", and nobody did
            return StepResult(rows_affected=0, log_excerpt="nobody started this run: nothing sent", extras={"attached": [n for n, _ in attachments]})
        payload = {"JOB": ctx.job_id, "RUN_ID": ctx.run_id, "FILES": ", ".join(n for n, _ in attachments), "DATE": date.today().isoformat(), "USER": user}
        msg = Message(subject=step.subject or "", body=step.body if isinstance(step.body, str) else None, link=f"/nomaflow/runs/{ctx.run_id}", rule=f"job:{ctx.job_id}:notify", payload=payload)
        report = await dispatcher.dispatch(msg, channels=list(step.channels), recipients=recipients, attachments=attachments)
        if report.errors:
            raise StepFailed("notify: " + "; ".join(f"{k}: {v}" for k, v in report.errors.items()))
        excerpt = f"portal {len(report.portal_users)}, email {len(report.emails)}, {len(attachments)} attachment(s)"
        return StepResult(rows_affected=len(report.portal_users) + len(report.emails), log_excerpt=excerpt, extras={**report.to_dict(), "attached": [n for n, _ in attachments]})
