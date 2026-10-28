"""``notifications.toml`` — the rules that turn an event into a message.

::

    [notifications.period_closed]
    label = "Period closed"
    trigger = { kind = "screen", app = "nomajde", screen = "period_status", events = ["update"],
                when = [{ field = "STATUS", value = "closed" }] }
    channels = ["portal", "email"]
    recipients = ["role:group", "email:cfo@corp.test"]
    subject = "Period {PERIOD} closed by {USER}"
    body = "Period {PERIOD} of company {COMPANY} is now closed."
    link = "/screen/nomajde/period_status"

    [notifications.job_failed]
    trigger = { kind = "job", events = ["failure"] }          # every job; or jobs = ["sales-load"]
    channels = ["portal"]
    recipients = ["role:admin"]
    subject = "Job {JOB} failed"
    body = "{ERROR}"

    [notifications.on_demand]
    trigger = { kind = "action" }                            # fired by a screen action: send_notification
    …

Three trigger kinds: a **screen** event (an insert / update / delete through a screen, with an
optional ``when`` on the written row — the same ``field`` / ``value`` shape as ``disabled_when``),
a **job** event (``failure``, ``succeeded``, ``canceled``, ``missed`` — a scheduled fire that did
not run —, ``late`` — no successful run by the job's ``alerts.finish_by`` — or ``long_run`` with
``long_run_minutes``),
or an **action** (a screen's ``send_notification`` step names the rule). Placeholders in subject,
body and link come from the event: the row's columns and ``USER`` / ``APP`` / ``SCREEN`` / ``EVENT``
for a screen, ``JOB`` / ``RUN_ID`` / ``STATE`` / ``ERROR`` / ``DURATION_MINUTES`` / ``ROWS`` / ``EXPECTED_AT`` for a job.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

ScreenEvent = Literal["insert", "update", "delete"]
JobEvent = Literal["failure", "succeeded", "canceled", "missed", "long_run", "late"]
Channel = Literal["portal", "email", "action"]


class When(BaseModel):
    """A condition on the event's row: ``field`` equals ``value`` (or one of the values)."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="A column of the written row.")
    value: str | list[str] = Field(description="The value, or list of values, that fires the rule.")


def when_holds(rules: list[When], values: Mapping[str, Any]) -> bool:
    """Every rule holds (AND); keys match without case, values compare trimmed."""
    lower = {str(k).lower(): v for k, v in values.items()}
    for r in rules:
        if r.field.lower() not in lower:
            return False
        live = lower[r.field.lower()]
        live_s = "" if live is None else str(live).strip()
        allowed = r.value if isinstance(r.value, list) else [r.value]
        if live_s not in {str(v).strip() for v in allowed}:
            return False
    return True


class ScreenTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["screen"] = "screen"
    app: str = Field(description="The screen's app (the scope key in screens.toml).")
    screen: str = Field(description="The screen id.")
    events: list[ScreenEvent] = Field(default_factory=lambda: ["insert", "update", "delete"])
    when: list[When] = Field(default_factory=list, description="Fire only when the written row matches (AND).")


class JobTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["job"] = "job"
    events: list[JobEvent] = Field(default_factory=lambda: ["failure"])
    jobs: list[str] = Field(default_factory=list, description="Job ids; empty = every job.")
    long_run_minutes: int | None = Field(default=None, ge=1, description="For ``long_run``: fire when a run lasted at least this long.")


class ActionTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["action"] = "action"


Trigger = Annotated[Union[ScreenTrigger, JobTrigger, ActionTrigger], Field(discriminator="kind")]


class RuleAction(BaseModel):
    """What the ``action`` channel runs: a connector query or endpoint, with extra params
    (templated) on top of the event's values."""

    model_config = ConfigDict(extra="forbid")

    connector: str
    query: str | None = None
    endpoint: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class NotificationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    enabled: bool = True
    trigger: Trigger
    channels: list[Channel] = Field(default_factory=lambda: ["portal"])
    recipients: list[str] = Field(default_factory=list, description="user:<name> / role:<name> / email:<address>")
    subject: str = Field(description="With {placeholders} from the event.")
    body: str | None = None
    link: str | None = Field(default=None, description="A frontend route the inbox row opens, e.g. /screen/<app>/<screen>.")
    action: RuleAction | None = None
    # Upgrade-safety flag (see Screen.override).
    override: bool = False


class NotificationsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notifications: dict[str, NotificationRule] = Field(default_factory=dict)

    def for_screen(self, app: str, screen: str, event: str, row: Mapping[str, Any]) -> list[tuple[str, NotificationRule]]:
        out = []
        for rid, r in self.notifications.items():
            t = r.trigger
            if not r.enabled or not isinstance(t, ScreenTrigger):
                continue
            if t.app != app or t.screen != screen or event not in t.events:
                continue
            if t.when and not when_holds(t.when, row):
                continue
            out.append((rid, r))
        return out

    def for_job(self, job_id: str, event: str, *, duration_minutes: float | None = None) -> list[tuple[str, NotificationRule]]:
        out = []
        for rid, r in self.notifications.items():
            t = r.trigger
            if not r.enabled or not isinstance(t, JobTrigger):
                continue
            if t.jobs and job_id not in t.jobs:
                continue
            if event == "long_run":
                if "long_run" not in t.events or t.long_run_minutes is None or duration_minutes is None or duration_minutes < t.long_run_minutes:
                    continue
            elif event not in t.events:
                continue
            out.append((rid, r))
        return out

    def for_action(self, rule_id: str) -> NotificationRule | None:
        r = self.notifications.get(rule_id)
        return r if r is not None and r.enabled and isinstance(r.trigger, ActionTrigger) else None


def parse_notification_rules(data: dict[str, Any]) -> NotificationsFile:
    return NotificationsFile.model_validate(data)


def load_notification_rules(path: Path | str, *, strict: bool = True) -> NotificationsFile:
    """Load and validate ``notifications.toml``; a missing file means no rules. With
    ``strict=False`` (the app's boot and reloads) a file that does not parse or validate is
    logged and treated as empty — a broken rule file must not keep the server from starting."""
    import logging

    path = Path(path)
    if not path.exists():
        return NotificationsFile()
    try:
        with path.open("rb") as fh:
            return parse_notification_rules(tomllib.load(fh))
    except Exception as exc:  # noqa: BLE001 — tomllib / pydantic errors alike
        if strict:
            raise
        logging.getLogger(__name__).error("notifications.toml at %s is invalid — no rule is active until it is fixed: %s", path, exc)
        return NotificationsFile()
