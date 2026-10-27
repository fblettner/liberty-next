"""notifications.toml rules: parsing, matching screen / job / action triggers, and the event
functions that turn an event into dispatches."""

from __future__ import annotations

import textwrap
import tomllib

import pytest

from liberty.notify import NotificationsFile, parse_notification_rules
from liberty.notify.events import drain, fire_action_rule, fire_job_event, fire_screen_event
from liberty.notify.rules import ScreenTrigger, when_holds


def _rules(toml: str) -> NotificationsFile:
    return parse_notification_rules(tomllib.loads(textwrap.dedent(toml)))


RULES = """
[notifications.period_closed]
label = "Period closed"
trigger = { kind = "screen", app = "nomajde", screen = "period_status", events = ["update"], when = [{ field = "STATUS", value = "closed" }] }
channels = ["portal", "email"]
recipients = ["role:group"]
subject = "Period {PERIOD} closed by {USER}"
link = "/screen/nomajde/period_status"

[notifications.any_delete]
trigger = { kind = "screen", app = "nomajde", screen = "period_status", events = ["delete"] }
recipients = ["user:ann"]
subject = "deleted {PERIOD}"

[notifications.job_failed]
trigger = { kind = "job", events = ["failure"] }
recipients = ["role:admin"]
subject = "Job {JOB} failed"
body = "{ERROR}"

[notifications.slow_sales]
trigger = { kind = "job", events = ["long_run"], jobs = ["sales-load"], long_run_minutes = 30 }
recipients = ["user:ann"]
subject = "{JOB} took {DURATION_MINUTES} min"

[notifications.on_demand]
trigger = { kind = "action" }
recipients = ["user:bob"]
subject = "Please look at {PERIOD}"

[notifications.off]
enabled = false
trigger = { kind = "action" }
subject = "never"
"""


def test_parse_and_match() -> None:
    rules = _rules(RULES)
    assert set(rules.notifications) == {"period_closed", "any_delete", "job_failed", "slow_sales", "on_demand", "off"}
    t = rules.notifications["period_closed"].trigger
    assert isinstance(t, ScreenTrigger) and t.events == ["update"] and t.when[0].field == "STATUS"
    assert when_holds(t.when, {"status": " closed "}) and not when_holds(t.when, {"STATUS": "open"}) and not when_holds(t.when, {})
    # screen matching: app / screen / event / when
    assert [r for r, _ in rules.for_screen("nomajde", "period_status", "update", {"STATUS": "closed"})] == ["period_closed"]
    assert rules.for_screen("nomajde", "period_status", "update", {"STATUS": "open"}) == []
    assert [r for r, _ in rules.for_screen("nomajde", "period_status", "delete", {})] == ["any_delete"]
    assert rules.for_screen("nomasx1", "period_status", "update", {"STATUS": "closed"}) == []
    # job matching
    assert [r for r, _ in rules.for_job("sales-load", "failure")] == ["job_failed"]
    assert rules.for_job("sales-load", "succeeded") == []
    assert [r for r, _ in rules.for_job("sales-load", "long_run", duration_minutes=45)] == ["slow_sales"]
    assert rules.for_job("sales-load", "long_run", duration_minutes=10) == []
    assert rules.for_job("other", "long_run", duration_minutes=45) == []
    # action rules: enabled + kind = action only
    assert rules.for_action("on_demand") is not None and rules.for_action("off") is None and rules.for_action("period_closed") is None
    with pytest.raises(ValueError):
        _rules('[notifications.bad]\ntrigger = { kind = "comet" }\nsubject = "x"\n')


class _Dispatcher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def dispatch(self, message, *, channels, recipients, action=None):
        from liberty.notify import DispatchReport
        self.calls.append({"subject": message.subject, "rule": message.rule, "payload": message.payload, "channels": channels, "recipients": recipients, "action": action})
        return DispatchReport(portal_users=["x"])


@pytest.mark.asyncio
async def test_events_dispatch_matching_rules() -> None:
    rules = _rules(RULES)
    d = _Dispatcher()
    tasks = fire_screen_event(rules, d, app="nomajde", screen="period_status", event="update", row={"PERIOD": "2026-03", "STATUS": "closed", "STATUS_ORIGINAL": "open"}, user="ann")
    assert len(tasks) == 1
    await drain()
    c = d.calls[-1]
    assert c["rule"] == "period_closed" and c["channels"] == ["portal", "email"] and c["recipients"] == ["role:group"]
    assert c["payload"]["PERIOD"] == "2026-03" and c["payload"]["USER"] == "ann" and c["payload"]["EVENT"] == "update" and c["payload"]["SCREEN"] == "period_status"
    assert fire_screen_event(rules, d, app="nomajde", screen="period_status", event="update", row={"STATUS": "open"}, user="ann") == []
    assert fire_screen_event(None, d, app="a", screen="b", event="update", row={}, user=None) == []
    # a failed run: the rule + the job's own recipients (addresses get the email: prefix)
    tasks = fire_job_event(rules, d, job_id="sales-load", run_id="r1", state="FAILED", error="boom", duration_minutes=2.5, rows=0, extra_recipients=["ops@corp.test", "user:ann"])
    assert len(tasks) == 2
    await drain()
    by_rule = {c["rule"]: c for c in d.calls[-2:]}
    assert by_rule["job_failed"]["payload"]["ERROR"] == "boom" and by_rule["job_failed"]["payload"]["DURATION_MINUTES"] == 2.5
    assert by_rule["job:sales-load"]["recipients"] == ["email:ops@corp.test", "user:ann"] and by_rule["job:sales-load"]["channels"] == ["portal", "email"]
    # a long run that succeeded fires the long_run rule only
    tasks = fire_job_event(rules, d, job_id="sales-load", run_id="r2", state="SUCCEEDED", error=None, duration_minutes=40, rows=10)
    assert len(tasks) == 1
    await drain()
    assert d.calls[-1]["rule"] == "slow_sales" and d.calls[-1]["payload"]["STATE"] == "SUCCEEDED"
    # an action rule, awaited; a non-action rule is refused
    report = await fire_action_rule(rules, d, rule_id="on_demand", payload={"PERIOD": "2026-03"}, user="ann")
    assert report is not None and d.calls[-1]["rule"] == "on_demand" and d.calls[-1]["payload"]["USER"] == "ann"
    assert await fire_action_rule(rules, d, rule_id="period_closed", payload={}, user="ann") is None
    assert await fire_action_rule(rules, d, rule_id="off", payload={}, user="ann") is None


def test_load_tolerates_a_broken_file_when_asked(tmp_path, caplog) -> None:
    from liberty.notify import load_notification_rules
    bad = tmp_path / "notifications.toml"
    bad.write_text('[notifications.x]\ntrigger = { kind = "screen",\n  app = "a" }\nsubject = "s"\n')   # an inline table over two lines
    with pytest.raises(Exception):
        load_notification_rules(bad)
    assert load_notification_rules(bad, strict=False).notifications == {}
    assert "is invalid" in caplog.text
    assert load_notification_rules(tmp_path / "missing.toml").notifications == {}
