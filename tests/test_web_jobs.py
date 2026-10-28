"""Tests for the ``/admin/jobs`` endpoints (chunk 3).

Uses the same TestClient + DB-backed auth pattern as test_web_admin.py.
Each test bootstraps a fresh FastAPI app via :func:`create_app` so the full
lifespan (nomaflow registry + runner + scheduler + recovery sweep + APScheduler)
runs, and the endpoints hit the actual wiring rather than a mock.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import (
    AISettings,
    AppSettings,
    AuthSettings,
    ChartSettings,
    ConnectorSettings,
    DashboardSettings,
    JobsSettings,
    MenuSettings,
    ScreenSettings,
    Settings,
)
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.jobs import JobDatabase, JobRun, RunState
from liberty.main import create_app

JWT_SECRET = "web-jobs-test-secret"


# --------------------------------------------------------------------------- #
# fixtures: TOML + auth seed (mirrors test_web_admin.py)
# --------------------------------------------------------------------------- #


_CONNECTORS_TOML = """
[pools.default]
url = "{db_url}"

[connectors.db]
type = "sql"
pool = "default"

[[connectors.db.queries]]
name = "answer"
sql = "SELECT 42 AS answer"

[[connectors.db.queries]]
name = "wait_then_one"
sql = "SELECT 1 AS x"
"""


_JOBS_TOML = """
[[jobs]]
id = "ping"
description = "Trivial cron job"
schedule = "*/30 * * * *"

[[jobs.steps]]
type = "sql_query"
name = "answer"
connector = "db"
query = "answer"

[[jobs]]
id = "manual-only"
description = "No schedule; manual-only"

[[jobs.steps]]
type = "sql_query"
name = "answer"
connector = "db"
query = "answer"

[[jobs]]
id = "disabled"
schedule = "0 0 * * *"
enabled = false

[[jobs.steps]]
type = "sql_query"
name = "answer"
connector = "db"
query = "answer"

# Used by the op_kwargs-override tests — a python step that records what it was
# called with, so the tests can assert overrides flow through.
[[jobs]]
id = "echo-kwargs"
description = "Records its op_kwargs to a module global; used by the run-with-parameters tests."

[[jobs.steps]]
type = "python"
name = "echo"
callable = "tests.test_web_jobs:_record_kwargs"
op_kwargs = { target_connector = "default", apps_id = 10 }

# Job-level params (set once, inherited by every step) — used by the
# test_job_level_params_* tests. Two steps to verify the merge applies to each
# independently and that step.op_kwargs win on conflict.
[[jobs]]
id = "load-file"
description = "A user-run import: the file the user uploads feeds the load step."
[[jobs.steps]]
type = "file_load"
name = "load"
target = { connector = "db", table = "imported" }
sheet = "Items"
[[jobs.steps]]
type = "file_load"
name = "bundled"
path = "/nowhere/bundled.csv"
target = { connector = "db", table = "imported" }
[[jobs.steps]]
type = "python"
name = "after"
callable = "tests.test_web_jobs:_record_kwargs"
[[jobs.user_params]]
name = "mode"
label = "Mode"
step = "after"
default = "full"
choices = ["full", "delta"]
[[jobs.user_params]]
name = "note"
step = "after"

[[jobs]]
id = "pick-app"
description = "A job-level parameter picked from a read query (the applications of an app)."
params = { apps_id = 1 }
[[jobs.steps]]
type = "python"
name = "echo"
callable = "tests.test_web_jobs:_record_kwargs"
[[jobs.user_params]]
name = "apps_id"
label = "Application"
choices_from = "query:db/answer:ANSWER:ANSWER"
[[jobs.steps]]
type = "python"
name = "extra"
callable = "tests.test_web_jobs:_record_kwargs"
op_kwargs = { part = "extra" }
[[jobs.presets]]
name = "Echo only"
params = { apps_id = 42 }
step_enabled = { extra = false }

[[jobs]]
id = "per-app"
description = "One run per application picked (for_each)."
for_each = "apps_id"
params = { apps_id = 1 }
[[jobs.steps]]
type = "python"
name = "echo"
callable = "tests.test_web_jobs:_record_kwargs"
[[jobs.user_params]]
name = "apps_id"
label = "Applications"
multiple = true
choices = ["1", "2", "3"]

[[jobs]]
id = "shared-params"
description = "Job-level params merged under every step's op_kwargs (step wins on conflict)."
params = { apps_id = 99, source_connector = "src_default" }

[[jobs.steps]]
type = "python"
name = "first"
callable = "tests.test_web_jobs:_record_kwargs"
# No op_kwargs — should inherit BOTH job params verbatim.

[[jobs.steps]]
type = "python"
name = "second"
callable = "tests.test_web_jobs:_record_kwargs"
# Overrides source_connector + adds a step-only key; apps_id inherits from job.
op_kwargs = { source_connector = "src_step", local = "yes" }
"""


# Captures the kwargs the most-recent ``echo`` python step was invoked with. The
# python-step test cases assert against this; reset before each call.
RECORDED_KWARGS: dict[str, object] = {}


def _record_kwargs(**kw) -> int:
    """Callable target for the ``echo`` python step in the test jobs.toml.
    Stores ``op_kwargs`` (minus the executor's standard injections —
    ``connectors`` / ``ctx`` / ``settings``) into the module global so the
    test can assert what the operator-supplied kwargs were. Returns 0 — int
    return → StepResult(rows_affected=0).

    Two record stores: the single-snapshot dict (legacy, used by the single-step
    echo-kwargs tests) AND a per-step dict keyed by ``ctx.run_id + step name``
    (filled when ``ctx`` is injected) — multi-step jobs use this one so each
    step's kwargs are captured separately."""
    ctx = kw.get("ctx")
    filtered = {k: v for k, v in kw.items() if k not in ("connectors", "ctx", "settings", "changesets", "app_state")}
    RECORDED_KWARGS.clear()
    RECORDED_KWARGS.update(filtered)
    if ctx is not None:
        # The runner doesn't pass step.name into the callable, so we tag by a counter
        # — tests reset the list before firing and assert in step order.
        RECORDED_PER_STEP.append(filtered)
    return 0


# Per-step capture used by the multi-step job tests (shared-params).
RECORDED_PER_STEP: list[dict[str, object]] = []


def _seed_db(db_url: str) -> None:
    """Bootstrap auth tables + nomaflow tables on the test DB — same shape as
    ``liberty-admin init-db`` does in production. The lifespan reads from these
    on app startup; missing nomaflow_* tables would crash the recovery sweep."""
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        auth = AuthDatabase(pools, "default")
        await auth.create_schema()
        jobs = JobDatabase(pools, "default")
        await jobs.create_schema()
        async with auth.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("reader", password="readerpw")
            await svc.get_or_create_role("loader", permissions=["job:load-file", "job:manual-only", "job:pick-app", "job:per-app"])
            await svc.create_user("loader", password="loaderpw", roles=["loader"])
        await pools.dispose()
    asyncio.run(go())


@pytest.fixture
def env(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(_CONNECTORS_TOML.format(db_url=db_url))
    jobs_toml = tmp_path / "jobs.toml"
    jobs_toml.write_text(_JOBS_TOML)
    _seed_db(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
        dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
        jobs=JobsSettings(config_path=Path(jobs_toml), pool="default"),
    )
    return create_app(settings), jobs_toml


def _h(client: TestClient, username: str) -> dict[str, str]:
    r = client.post("/auth/login", json={"username": username, "password": f"{username}pw"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# --------------------------------------------------------------------------- #
# GET /admin/jobs
# --------------------------------------------------------------------------- #


def test_list_jobs_requires_superuser(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        assert client.get("/admin/jobs").status_code == 401
        assert client.get("/admin/jobs", headers=_h(client, "reader")).status_code == 403


def test_list_jobs_returns_catalogue_with_operational_flags(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.get("/admin/jobs", headers=_h(client, "admin"))
        assert r.status_code == 200
        body = r.json()
        ids = {j["id"]: j for j in body["jobs"]}
        assert set(ids) == {"ping", "manual-only", "disabled", "echo-kwargs", "load-file", "pick-app", "per-app", "shared-params"}
        # Only the enabled+scheduled job is registered with the scheduler
        assert ids["ping"]["registered_with_scheduler"] is True
        assert ids["manual-only"]["registered_with_scheduler"] is False
        assert ids["disabled"]["registered_with_scheduler"] is False
        assert ids["echo-kwargs"]["registered_with_scheduler"] is False  # no schedule
        # None are in flight at this point
        assert all(j["in_flight"] is False for j in body["jobs"])
        assert body["active_run_ids"] == []


# --------------------------------------------------------------------------- #
# POST /admin/jobs/<id>/run
# --------------------------------------------------------------------------- #


_TERMINAL_STATES = (
    RunState.SUCCEEDED.value, RunState.FAILED.value, RunState.CANCELED.value,
)


def _wait_for_terminal(app, run_id: str, *, timeout_s: float = 5.0) -> JobRun:
    """Poll the JobRun row until it reaches a terminal state. The /run endpoint
    is fire-and-return now; tests that want to assert on the finished state
    need to wait for the background task to finalize."""
    import time
    deadline = time.monotonic() + timeout_s
    async def fetch():
        async with app.state.jobs.db.session() as s:
            return (await s.execute(select(JobRun).where(JobRun.id == run_id))).scalar_one_or_none()
    while time.monotonic() < deadline:
        row = asyncio.run(fetch())
        if row is not None and row.state in _TERMINAL_STATES:
            return row
        time.sleep(0.05)
    raise AssertionError(f"run {run_id!r} did not reach a terminal state within {timeout_s}s")


def test_run_now_fires_and_returns_run_id(env) -> None:
    """The /run endpoint is fire-and-return: it allocates the JobRun row,
    spawns execute_run as a background task, returns the new run id with
    status=queued. The row reaches SUCCEEDED on its own time — operators
    watch via /admin/jobs polling, not by waiting on the POST response."""
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/jobs/ping/run", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["job_id"] == "ping"
        assert body["triggered_by"] == "admin"
        assert body["status"] == "queued"
        run_id = body["run_id"]
        assert run_id  # a real id, not None

        # The background task finishes shortly after — verify the row reaches
        # SUCCEEDED and the trigger metadata is preserved.
        row = _wait_for_terminal(app, run_id)
        assert row.job_id == "ping"
        assert row.state == RunState.SUCCEEDED.value
        assert row.triggered_by == "admin"


def test_run_now_for_unknown_job_returns_404(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.post("/admin/jobs/no-such-job/run", headers=_h(client, "admin"))
        assert r.status_code == 404
        assert "no-such-job" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# GET /admin/jobs — last-run + next-fire extension (increment 1)
# --------------------------------------------------------------------------- #


def test_list_jobs_carries_last_run_and_next_run(env) -> None:
    """After a manual run of `ping`, GET /admin/jobs carries that run as `last_run`;
    a scheduled job carries a `next_run` ISO timestamp."""
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Before any run: last_run is None for every job
        before = {j["id"]: j for j in client.get("/admin/jobs", headers=h).json()["jobs"]}
        assert before["ping"]["last_run"] is None
        # `ping` is scheduled → it has a next_run timestamp; manual-only / disabled don't
        assert before["ping"]["next_run"] is not None
        assert before["manual-only"]["next_run"] is None
        assert before["disabled"]["next_run"] is None

        # Fire ping (fire-and-return), then wait for the background task to
        # finalize, then last_run reflects it.
        run_id = client.post("/admin/jobs/ping/run", headers=h).json()["run_id"]
        _wait_for_terminal(app, run_id)
        after = {j["id"]: j for j in client.get("/admin/jobs", headers=h).json()["jobs"]}
        lr = after["ping"]["last_run"]
        assert lr is not None
        assert lr["state"] == "SUCCEEDED"
        assert lr["run_id"] == run_id
        assert lr["started_at"] and lr["finished_at"]


def test_list_jobs_reports_in_flight_for_a_running_manual_run(env) -> None:
    """A manual 'Run now' goes fire_now → runner.run directly — it never enters
    the scheduler's _in_flight set. in_flight must still be true (the latest run
    is RUNNING), else the Jobs-list Cancel button never shows for a manual run."""
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        jobs = app.state.jobs

        async def seed_running() -> None:
            from liberty.jobs import TriggerKind
            async with jobs.db.session() as s:
                s.add(JobRun(
                    job_id="ping", trigger_kind=TriggerKind.MANUAL.value,
                    triggered_by="admin", state=RunState.RUNNING.value,
                ))
        asyncio.run(seed_running())

        body = {j["id"]: j for j in client.get("/admin/jobs", headers=h).json()["jobs"]}
        assert body["ping"]["in_flight"] is True
        assert body["ping"]["last_run"]["state"] == "RUNNING"
        # The other jobs, with no run, stay not-in-flight.
        assert body["manual-only"]["in_flight"] is False


# --------------------------------------------------------------------------- #
# GET / PUT /admin/config/jobs/parsed (increment 1)
# --------------------------------------------------------------------------- #


def test_get_jobs_parsed_returns_catalogue(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.get("/admin/config/jobs/parsed", headers=_h(client, "admin"))
        assert r.status_code == 200
        body = r.json()
        assert body["path"].endswith("jobs.toml")
        ids = {j["id"] for j in body["jobs"]}
        assert ids == {"ping", "manual-only", "disabled", "echo-kwargs", "load-file", "pick-app", "per-app", "shared-params"}


def test_get_jobs_parsed_requires_superuser(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        assert client.get("/admin/config/jobs/parsed").status_code == 401
        assert client.get("/admin/config/jobs/parsed", headers=_h(client, "reader")).status_code == 403


def test_put_jobs_parsed_round_trip(env) -> None:
    """PUT a new job list, GET it back — the edit persisted to jobs.toml."""
    app, jobs_toml = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        new_jobs = [
            {
                "id": "added-via-put",
                "schedule": "0 5 * * *",
                "steps": [
                    {"type": "sql_query", "name": "s", "connector": "db", "query": "answer"},
                ],
            },
        ]
        r = client.put("/admin/config/jobs/parsed", json={"jobs": new_jobs}, headers=h)
        assert r.status_code == 200 and r.json()["saved"] is True

        back = client.get("/admin/config/jobs/parsed", headers=h).json()
        assert [j["id"] for j in back["jobs"]] == ["added-via-put"]
        # The file on disk really changed
        assert "added-via-put" in jobs_toml.read_text()


def test_put_jobs_parsed_drops_null_fields(env) -> None:
    """A builder clearing an optional field sends null; TOML has no null, so the key is dropped
    and the model default applies on reload (tomlkit used to refuse the whole save)."""
    app, jobs_toml = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        jobs = [{
            "id": "loader", "description": None,
            "steps": [{"type": "file_load", "name": "load", "path": None, "watch_dir": None, "sheet": None,
                       "target": {"connector": "db", "table": "t"}, "constants": {"period": "2026-03", "nothing": None}}],
        }]
        r = client.put("/admin/config/jobs/parsed", json={"jobs": jobs}, headers=h)
        assert r.status_code == 200, r.text
        text = jobs_toml.read_text()
        assert "loader" in text and "null" not in text.lower().replace("nullable", "")
        back = client.get("/admin/config/jobs/parsed", headers=h).json()["jobs"][0]
        assert back["steps"][0]["constants"] == {"period": "2026-03"} and "watch_dir" not in back["steps"][0] or back["steps"][0].get("watch_dir") is None


def test_put_jobs_parsed_rejects_invalid(env) -> None:
    """A job with an unknown step type → 422, file untouched."""
    app, jobs_toml = env
    original = jobs_toml.read_text()
    with TestClient(app) as client:
        h = _h(client, "admin")
        bad = [{"id": "broken", "steps": [{"type": "spark_submit", "name": "x"}]}]
        r = client.put("/admin/config/jobs/parsed", json={"jobs": bad}, headers=h)
        assert r.status_code == 422
        assert jobs_toml.read_text() == original  # not written


def test_put_jobs_parsed_requires_superuser(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        body = {"jobs": []}
        assert client.put("/admin/config/jobs/parsed", json=body).status_code == 401
        assert client.put("/admin/config/jobs/parsed", json=body, headers=_h(client, "reader")).status_code == 403


# --------------------------------------------------------------------------- #
# meta round-trip — the [meta] block (retention policy etc.) must survive
# GET → edit → PUT → GET. Regression guard for the bug where the RetentionPanel
# changed days kept to 5, saved, and the value reverted to the schema default
# because the GET response and JobsBody model both ignored ``meta`` entirely.
# --------------------------------------------------------------------------- #


def test_get_jobs_parsed_includes_meta_with_defaults(env) -> None:
    """GET returns the effective meta (defaults included) so the RetentionPanel
    can initialise its inputs to 30 / 100 / 60 instead of empty fields."""
    app, _ = env
    with TestClient(app) as client:
        body = client.get("/admin/config/jobs/parsed", headers=_h(client, "admin")).json()
        assert "meta" in body
        assert body["meta"]["version"] == 1
        # The retention defaults — without this the panel can't tell the operator
        # what's actually in effect.
        ret = body["meta"]["retention"]
        assert ret == {"enabled": True, "days": 30, "keep_last_per_job": 100, "sweep_interval_minutes": 60, "files_days": None}
        assert body["meta"]["missed"] == {"enabled": True, "grace_minutes": 10, "check_interval_minutes": 5, "lookback_hours": 24}


def test_missed_status_and_check(env) -> None:
    """GET /admin/jobs/missed shows the policy (+ no report yet); POST …/check runs one pass —
    nothing is missed on a fresh install (the window opens at the scheduler's start)."""
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = client.get("/admin/jobs/missed", headers=h).json()
        assert body["policy"]["grace_minutes"] == 10 and body["last_check"] is None
        report = client.post("/admin/jobs/missed/check", headers=h).json()
        assert report["missed"] == [] and report["triggers"] == 1
        assert client.get("/admin/jobs/missed", headers=h).json()["last_check"]["triggers"] == 1
        assert client.get("/admin/jobs/missed", headers=_h(client, "reader")).status_code == 403


def test_put_jobs_parsed_round_trips_meta(env) -> None:
    """The RetentionPanel save flow: PUT a body with both ``jobs`` and ``meta``,
    GET it back and the new retention policy is what we sent."""
    app, jobs_toml = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Mirror the panel's payload — fetched parsed, mutated meta, sent both.
        parsed = client.get("/admin/config/jobs/parsed", headers=h).json()
        new_meta = {
            **parsed["meta"],
            "retention": {
                "enabled": True, "days": 5, "keep_last_per_job": 25, "sweep_interval_minutes": 15,
            },
        }
        r = client.put(
            "/admin/config/jobs/parsed",
            json={"jobs": parsed["jobs"], "meta": new_meta},
            headers=h,
        )
        assert r.status_code == 200 and r.json()["saved"] is True

        back = client.get("/admin/config/jobs/parsed", headers=h).json()
        assert back["meta"]["retention"]["days"] == 5
        assert back["meta"]["retention"]["keep_last_per_job"] == 25
        assert back["meta"]["retention"]["sweep_interval_minutes"] == 15
        # The on-disk file actually carries the new values
        text = jobs_toml.read_text()
        assert "days = 5" in text
        assert "keep_last_per_job = 25" in text


def test_put_jobs_parsed_without_meta_preserves_existing_block(env) -> None:
    """JobEditor's save flow PUTs only ``jobs``; the existing [meta] in jobs.toml
    must survive untouched (otherwise editing a single job would silently reset
    the retention policy)."""
    app, jobs_toml = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # First, set a non-default meta so we can tell "preserved" from "default".
        parsed = client.get("/admin/config/jobs/parsed", headers=h).json()
        parsed["meta"]["retention"]["days"] = 7
        client.put("/admin/config/jobs/parsed", json={"jobs": parsed["jobs"], "meta": parsed["meta"]}, headers=h)
        assert "days = 7" in jobs_toml.read_text()

        # Now a meta-less PUT (JobEditor-style) — the days=7 must STILL be there.
        client.put("/admin/config/jobs/parsed", json={"jobs": parsed["jobs"]}, headers=h)
        back = client.get("/admin/config/jobs/parsed", headers=h).json()
        assert back["meta"]["retention"]["days"] == 7


# --------------------------------------------------------------------------- #
# GET /admin/jobs/cron-preview (increment 6)
# --------------------------------------------------------------------------- #


def test_cron_preview_returns_next_fires(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.get(
            "/admin/jobs/cron-preview",
            params={"schedule": "30 2 * * *", "timezone": "Europe/Paris", "count": 3},
            headers=_h(client, "admin"),
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["next"]) == 3
        # All three are valid ascending ISO timestamps
        assert body["next"] == sorted(body["next"])


def test_cron_preview_rejects_bad_expression(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.get(
            "/admin/jobs/cron-preview",
            params={"schedule": "not a cron"},
            headers=_h(client, "admin"),
        )
        assert r.status_code == 422


def test_cron_preview_requires_superuser(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        params = {"schedule": "0 * * * *"}
        assert client.get("/admin/jobs/cron-preview", params=params).status_code == 401
        assert client.get("/admin/jobs/cron-preview", params=params, headers=_h(client, "reader")).status_code == 403


# --------------------------------------------------------------------------- #
# GET /admin/jobs/runs/<id> — run detail + captured log (live-logs increment)
# --------------------------------------------------------------------------- #


def test_get_run_detail_returns_run_steps_and_log(env) -> None:
    """After ping runs, its run-detail carries the run summary, the step rows,
    and the captured log — the runner's progress lines, persisted to
    nomaflow_run_logs at finalize."""
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        run_id = client.post("/admin/jobs/ping/run", headers=h).json()["run_id"]
        _wait_for_terminal(app, run_id)

        r = client.get(f"/admin/jobs/runs/{run_id}", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["run"]["job_id"] == "ping"
        assert body["run"]["state"] == "SUCCEEDED"
        assert isinstance(body["steps"], list) and len(body["steps"]) >= 1
        # The per-run log capture ran — the runner's start line is in there.
        assert "nomaflow.runner started" in body["logs"]


def test_get_run_detail_unknown_run_404(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.get("/admin/jobs/runs/no-such-run", headers=_h(client, "admin"))
        assert r.status_code == 404


def test_get_run_detail_requires_superuser(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        assert client.get("/admin/jobs/runs/x").status_code == 401
        assert client.get("/admin/jobs/runs/x", headers=_h(client, "reader")).status_code == 403


def test_run_now_for_manual_only_job_works(env) -> None:
    """``manual-only`` has no cron schedule; it's not registered with APScheduler
    but the manual fire endpoint still triggers it (that's the whole point).
    Same fire-and-return contract as the scheduled case."""
    app, _ = env
    with TestClient(app) as client:
        r = client.post("/admin/jobs/manual-only/run", headers=_h(client, "admin"))
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "queued"
        row = _wait_for_terminal(app, body["run_id"])
        assert row.job_id == "manual-only"
        assert row.state == RunState.SUCCEEDED.value


def test_run_now_requires_superuser(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        assert client.post("/admin/jobs/ping/run").status_code == 401
        assert client.post("/admin/jobs/ping/run", headers=_h(client, "reader")).status_code == 403


# --------------------------------------------------------------------------- #
# POST /admin/jobs/<id>/run — op_kwargs overrides ("Run with parameters")
# --------------------------------------------------------------------------- #


def test_run_now_uses_saved_op_kwargs_by_default(env) -> None:
    """No body / empty body → the step's saved op_kwargs reach the callable
    verbatim. Baseline before the override tests below."""
    app, _ = env
    RECORDED_KWARGS.clear()
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/jobs/echo-kwargs/run", headers=h)
        assert r.status_code == 200
        _wait_for_terminal(app, r.json()["run_id"])
    # The saved op_kwargs from jobs.toml: target_connector="default", apps_id=10.
    assert RECORDED_KWARGS == {"target_connector": "default", "apps_id": 10}


def test_run_now_applies_op_kwargs_override(env) -> None:
    """Per-fire override merges INTO the saved op_kwargs — operator-typed values
    win over defaults; un-overridden keys keep their saved values. The on-disk
    jobs.toml is NOT modified (regression coverage for that comes via
    test_run_now_uses_saved_op_kwargs_by_default — running the override test
    after it would fail if it leaked)."""
    app, _ = env
    RECORDED_KWARGS.clear()
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/jobs/echo-kwargs/run",
            json={"op_kwargs": {"echo": {"target_connector": "nomasx1b"}}},  # override one key
            headers=h,
        )
        assert r.status_code == 200
        _wait_for_terminal(app, r.json()["run_id"])
    # target_connector overridden; apps_id kept its saved value.
    assert RECORDED_KWARGS == {"target_connector": "nomasx1b", "apps_id": 10}


def test_run_now_override_for_unknown_step_is_no_op(env) -> None:
    """Override keyed by a step name that doesn't exist in the job is a silent
    no-op — same shape as ignoring an unknown kwarg. (Failing loudly would be
    just as defensible; the choice here is to keep the modal forgiving.)"""
    app, _ = env
    RECORDED_KWARGS.clear()
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/jobs/echo-kwargs/run",
            json={"op_kwargs": {"no-such-step": {"x": 1}}},
            headers=h,
        )
        assert r.status_code == 200
        _wait_for_terminal(app, r.json()["run_id"])
    # Saved kwargs reached the callable unmodified.
    assert RECORDED_KWARGS == {"target_connector": "default", "apps_id": 10}


def test_job_level_params_inherited_by_every_step(env) -> None:
    """job.params provide defaults; a step with no op_kwargs inherits them verbatim.
    First step in shared-params declares NO op_kwargs → both job params appear in
    its call."""
    app, _ = env
    RECORDED_PER_STEP.clear()
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/jobs/shared-params/run", headers=h)
        assert r.status_code == 200
        _wait_for_terminal(app, r.json()["run_id"])
    # Two steps fired; first got pure inherited params.
    assert len(RECORDED_PER_STEP) == 2
    assert RECORDED_PER_STEP[0] == {"apps_id": 99, "source_connector": "src_default"}


def test_job_level_params_overridden_by_step(env) -> None:
    """The shared-params job's second step sets source_connector + local; apps_id
    inherits from the job. Verifies step.op_kwargs wins on conflict + step-only
    keys land alongside inherited ones."""
    app, _ = env
    RECORDED_PER_STEP.clear()
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/jobs/shared-params/run", headers=h)
        _wait_for_terminal(app, r.json()["run_id"])
    # The second step's recorded kwargs: source_connector overridden, apps_id
    # inherited, local added.
    assert len(RECORDED_PER_STEP) == 2
    assert RECORDED_PER_STEP[1] == {
        "apps_id": 99,                    # inherited from job
        "source_connector": "src_step",   # overridden by step
        "local": "yes",                   # step-only
    }


def test_per_fire_params_override_wins_over_job_params(env) -> None:
    """The body's ``params`` field overrides job.params for the run — applies to
    every step (no step name required). Per-step ``op_kwargs[step]`` still wins
    over ``params`` though; this test isolates the params-vs-job-params layer."""
    app, _ = env
    RECORDED_PER_STEP.clear()
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/jobs/shared-params/run",
            json={"params": {"apps_id": 42, "source_connector": "src_run"}},
            headers=h,
        )
        _wait_for_terminal(app, r.json()["run_id"])
    # First step: had no op_kwargs → params_override wins entirely.
    assert RECORDED_PER_STEP[0] == {"apps_id": 42, "source_connector": "src_run"}
    # Second step: step.op_kwargs has source_connector=src_step + local=yes.
    # params_override (apps_id=42, source_connector=src_run) merges UNDER step.
    # So source_connector=src_step (step wins) but apps_id=42 (params_override
    # wins over job.params).
    # Wait — let me reread the order: job.params → step.op_kwargs → params_override
    # → op_kwargs_overrides[step]. So params_override WINS over step.op_kwargs.
    # That's intentional: "I want to run this whole job with apps_id=X" should
    # apply everywhere even if some step accidentally has apps_id baked in.
    assert RECORDED_PER_STEP[1] == {
        "apps_id": 42,                    # params_override wins over job.params
        "source_connector": "src_run",    # params_override wins over step.op_kwargs
        "local": "yes",                   # step-only, kept
    }


def test_job_level_params_overridden_by_run_time_override(env) -> None:
    """The full layer order is: job.params → step.op_kwargs → run-time override.
    Override apps_id at run time on the first step and assert it wins over both."""
    app, _ = env
    RECORDED_PER_STEP.clear()
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/jobs/shared-params/run",
            json={"op_kwargs": {"first": {"apps_id": 12345}}},
            headers=h,
        )
        _wait_for_terminal(app, r.json()["run_id"])
    # First step: apps_id overridden to 12345; source_connector kept from job.
    assert RECORDED_PER_STEP[0] == {"apps_id": 12345, "source_connector": "src_default"}
    # Second step: unaffected by the per-step override (it targeted "first" only).
    assert RECORDED_PER_STEP[1] == {
        "apps_id": 99, "source_connector": "src_step", "local": "yes",
    }


def test_run_now_rejects_malformed_op_kwargs(env) -> None:
    """The body's ``op_kwargs`` must be a ``{step_name: {key: value}}`` map.
    Anything else is 422 — silent-dropping malformed payloads would have
    operators wondering why their override didn't apply."""
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # not a dict at all
        assert client.post(
            "/admin/jobs/echo-kwargs/run", json={"op_kwargs": "broken"}, headers=h,
        ).status_code == 422
        # outer is a dict but inner value is not
        assert client.post(
            "/admin/jobs/echo-kwargs/run",
            json={"op_kwargs": {"echo": "also-broken"}}, headers=h,
        ).status_code == 422


# --------------------------------------------------------------------------- #
# POST /admin/jobs/<id>/run — step_enabled override (per-fire disable toggle)
# --------------------------------------------------------------------------- #


def test_run_now_step_enabled_false_skips_the_step(env) -> None:
    """Per-fire ``step_enabled = {step_name: false}`` makes the runner skip
    that step (CANCELED with ``skipped: disabled``). The recorded kwargs are
    empty because ``echo`` never ran."""
    app, _ = env
    RECORDED_KWARGS.clear()
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/jobs/echo-kwargs/run",
            json={"step_enabled": {"echo": False}},
            headers=h,
        )
        assert r.status_code == 200
        _wait_for_terminal(app, r.json()["run_id"])
    # Empty — the step was skipped before _record_kwargs could fire.
    assert RECORDED_KWARGS == {}


def test_run_now_step_enabled_true_is_default_and_still_runs(env) -> None:
    """``step_enabled = {step_name: true}`` is a no-op when the step is
    already enabled (sanity check that the toggle doesn't break the happy
    path)."""
    app, _ = env
    RECORDED_KWARGS.clear()
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(
            "/admin/jobs/echo-kwargs/run",
            json={"step_enabled": {"echo": True}},
            headers=h,
        )
        assert r.status_code == 200
        _wait_for_terminal(app, r.json()["run_id"])
    assert RECORDED_KWARGS == {"target_connector": "default", "apps_id": 10}


def test_run_now_rejects_malformed_step_enabled(env) -> None:
    """``step_enabled`` must be ``{step_name: bool}``. Reject:
       (a) non-dict, (b) non-bool value, (c) unknown step name (would silently
       no-op otherwise → operator wonders why their toggle didn't work)."""
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # (a) not a dict
        assert client.post(
            "/admin/jobs/echo-kwargs/run",
            json={"step_enabled": "broken"}, headers=h,
        ).status_code == 422
        # (b) non-bool value (string "false" is a footgun — reject explicitly)
        assert client.post(
            "/admin/jobs/echo-kwargs/run",
            json={"step_enabled": {"echo": "false"}}, headers=h,
        ).status_code == 422
        # (c) unknown step name
        r = client.post(
            "/admin/jobs/echo-kwargs/run",
            json={"step_enabled": {"no-such-step": False}}, headers=h,
        )
        assert r.status_code == 422
        assert "unknown step" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# GET /admin/jobs/runs/<id> — overrides snapshot in the run detail payload
# --------------------------------------------------------------------------- #


def test_run_detail_includes_overrides_snapshot(env) -> None:
    """When a run was fired with per-fire overrides (log_level / op_kwargs /
    step_enabled / params), the snapshot persists on the new RunOverrides
    table and surfaces in GET /admin/jobs/runs/<id> so the RunDetail page can
    show "this run used these overrides". Scheduled fires (and pre-feature
    runs) just see ``overrides: {}``."""
    app, _ = env
    RECORDED_KWARGS.clear()
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Fire with a couple of overrides — the snapshot should capture them.
        r = client.post(
            "/admin/jobs/echo-kwargs/run",
            json={
                "log_level": "DEBUG",
                "op_kwargs": {"echo": {"target_connector": "nomasx1b"}},
            },
            headers=h,
        )
        assert r.status_code == 200
        run_id = r.json()["run_id"]
        _wait_for_terminal(app, run_id)
        # GET the run detail; overrides should reflect what we sent.
        detail = client.get(f"/admin/jobs/runs/{run_id}", headers=h).json()
        assert detail["overrides"]["log_level"] == "DEBUG"
        assert detail["overrides"]["op_kwargs"] == {"echo": {"target_connector": "nomasx1b"}}
        # No params + no step_enabled in the request → not present in the snapshot
        # (only the keys the operator actually used land in the snapshot).
        assert "params" not in detail["overrides"]
        assert "step_enabled" not in detail["overrides"]


def test_run_detail_overrides_empty_for_scheduled_or_no_override_run(env) -> None:
    """A run fired with no body (the manual-without-overrides path) writes
    an empty overrides snapshot — and the GET surfaces ``overrides: {}``,
    which the frontend treats as "no overrides section to render"."""
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/jobs/echo-kwargs/run", headers=h)
        run_id = r.json()["run_id"]
        _wait_for_terminal(app, run_id)
        detail = client.get(f"/admin/jobs/runs/{run_id}", headers=h).json()
        assert detail["overrides"] == {}


# --------------------------------------------------------------------------- #
# POST /admin/jobs/runs/<id>/cancel
# --------------------------------------------------------------------------- #


def test_cancel_for_unknown_run_returns_404(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.post("/admin/jobs/runs/no-such-run/cancel", headers=_h(client, "admin"))
        assert r.status_code == 404


def test_cancel_requires_superuser(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        assert client.post("/admin/jobs/runs/x/cancel").status_code == 401
        assert client.post("/admin/jobs/runs/x/cancel", headers=_h(client, "reader")).status_code == 403


# --------------------------------------------------------------------------- #
# /admin/reload hot-reloads the JobRegistry into the live scheduler
# --------------------------------------------------------------------------- #


def test_admin_reload_includes_nomaflow_state(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.post("/admin/reload", headers=_h(client, "admin"))
        assert r.status_code == 200
        body = r.json()
        assert "nomaflow" in body
        nf = body["nomaflow"]
        assert nf["enabled"] is True
        assert set(nf["jobs"]) == {"ping", "manual-only", "disabled", "echo-kwargs", "load-file", "pick-app", "per-app", "shared-params"}
        assert nf["scheduled_jobs"] == ["ping"]


def test_admin_reload_picks_up_new_job_into_scheduler(env, tmp_path) -> None:
    """Add a new scheduled job to jobs.toml, reload, verify it landed in the
    live scheduler without restarting the process."""
    app, jobs_toml = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Initial state — only "ping" is scheduled
        before = client.post("/admin/reload", headers=h).json()
        assert before["nomaflow"]["scheduled_jobs"] == ["ping"]

        # Append a new job to jobs.toml
        jobs_toml.write_text(jobs_toml.read_text() + textwrap.dedent("""
            [[jobs]]
            id = "new-cron-job"
            schedule = "0 4 * * *"

            [[jobs.steps]]
            type = "sql_query"
            name = "answer"
            connector = "db"
            query = "answer"
        """))

        after = client.post("/admin/reload", headers=h).json()
        scheduled = set(after["nomaflow"]["scheduled_jobs"])
        assert scheduled == {"ping", "new-cron-job"}


def test_admin_reload_removes_dropped_jobs_from_scheduler(env, tmp_path) -> None:
    """Drop "ping" from jobs.toml and reload — APScheduler no longer carries it."""
    app, jobs_toml = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # Rewrite jobs.toml without ping (keep only manual-only + disabled)
        jobs_toml.write_text(textwrap.dedent("""
            [[jobs]]
            id = "manual-only"
            [[jobs.steps]]
            type = "sql_query"
            name = "answer"
            connector = "db"
            query = "answer"

            [[jobs]]
            id = "disabled"
            schedule = "0 0 * * *"
            enabled = false
            [[jobs.steps]]
            type = "sql_query"
            name = "answer"
            connector = "db"
            query = "answer"
        """))

        after = client.post("/admin/reload", headers=h).json()
        assert after["nomaflow"]["scheduled_jobs"] == []  # nothing scheduled now
        assert "ping" not in after["nomaflow"]["jobs"]


# --------------------------------------------------------------------------- #
# POST /admin/jobs/<id>/upload + GET /admin/jobs/runs/<id>/files/<name>
# --------------------------------------------------------------------------- #


def test_upload_and_download_run_file(env, tmp_path) -> None:
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert client.post("/admin/jobs/ping/upload", headers=_h(client, "reader"), files={"file": ("a.csv", b"x\n")}).status_code == 403
        assert client.post("/admin/jobs/no-such-job/upload", headers=h, files={"file": ("a.csv", b"x\n")}).status_code == 404
        r = client.post("/admin/jobs/ping/upload", headers=h, files={"file": ("../evil name.CSV", b"period;qty\n2026-03;1\n")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["job_id"] == "ping" and body["name"].endswith("-evil_name.CSV") and body["size"] == 21
        saved = Path(body["path"])
        assert saved.is_file() and saved.parent.name == "uploads" and saved.parent.parent.name == "ping"
        assert saved.parent.parent.parent == tmp_path / ".nomaflow" / "files"   # next to connectors.toml
        assert client.post("/admin/jobs/ping/upload", headers=h, files={"file": ("a.json", b"{}")}).status_code == 400
        assert client.post("/admin/jobs/ping/upload", headers=h, data={"x": "1"}).status_code == 400
        # a run's file: only a plain name starting with the run id, under the run's job folder
        run_id = client.post("/admin/jobs/ping/run", headers=h).json()["run_id"]
        _wait_for_terminal(app, run_id)
        report = tmp_path / ".nomaflow" / "files" / "ping" / f"{run_id}-answer-rejects.csv"
        report.write_text("file,row,reason\na.csv,2,qty: bad\n")
        r = client.get(f"/admin/jobs/runs/{run_id}/files/{report.name}", headers=h)
        assert r.status_code == 200 and r.text.startswith("file,row,reason") and "text/csv" in r.headers["content-type"]
        assert client.get(f"/admin/jobs/runs/{run_id}/files/other-{report.name}", headers=h).status_code == 404
        assert client.get(f"/admin/jobs/runs/{run_id}/files/..%2F{report.name}", headers=h).status_code == 404
        assert client.get(f"/admin/jobs/runs/no-such-run/files/{report.name}", headers=h).status_code == 404
        assert client.get(f"/admin/jobs/runs/{run_id}/files/{report.name}", headers=_h(client, "reader")).status_code == 403


def test_user_job_routes(env) -> None:
    """``/api/jobs``: the job for its ``job:<id>`` holder (uploads = the file_load steps, the run
    history), the upload, the run with the uploaded file, the run detail with its log; a user
    without the permission gets 403 before any lookup, an unknown job 404, a foreign path 422."""
    import time
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "loader")
        assert client.get("/api/jobs/load-file", headers=_h(client, "reader")).status_code == 403
        assert client.get("/api/jobs/ghost", headers=h).status_code == 403          # no permission → no enumeration
        assert client.get("/api/jobs/ping", headers=h).status_code == 403
        assert client.get("/api/jobs/ghost", headers=_h(client, "admin")).status_code == 404
        r = client.get("/api/jobs/load-file", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["job"]["id"] == "load-file" and body["job"]["uploads"] == [{"step": "load", "sheet": "Items"}]
        assert body["runs"] == [] and body["job"]["in_flight"] is False
        assert body["job"]["params"] == [
            {"name": "mode", "label": "Mode", "step": "after", "default": "full", "required": False, "multiple": False, "choices": [{"value": "full", "label": "full"}, {"value": "delta", "label": "delta"}]},
            {"name": "note", "label": "note", "step": "after", "default": None, "required": False, "multiple": False, "choices": []},
        ]
        assert client.post("/api/jobs/load-file/run", json={"params": {"mode": "nope"}}, headers=h).status_code == 422
        # the file must be uploaded through this job (a path elsewhere is refused)
        assert client.post("/api/jobs/load-file/run", json={"files": {"load": "/etc/passwd"}}, headers=h).status_code == 422
        assert client.post("/api/jobs/load-file/run", json={"files": {"nope": "x"}}, headers=h).status_code == 422
        up = client.post("/api/jobs/load-file/upload", files={"file": ("items.csv", b"code,name\nA,Alpha\n", "text/csv")}, headers=h)
        assert up.status_code == 200, up.text
        assert up.json()["name"].endswith("-items.csv")
        assert client.post("/api/jobs/load-file/upload", files={"file": ("x.exe", b"MZ", "application/octet-stream")}, headers=h).status_code == 400
        r = client.post("/api/jobs/load-file/run", json={"files": {"load": up.json()["path"]}, "params": {"mode": "delta", "note": " hello "}}, headers=h)
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        d: dict = {}
        for _ in range(100):
            d = client.get(f"/api/jobs/load-file/runs/{run_id}", headers=h).json()
            if d["run"]["state"] not in ("QUEUED", "RUNNING"):
                break
            time.sleep(0.05)
        assert d["run"]["id"] == run_id and d["run"]["triggered_by"] == "loader" and "overrides" not in d and isinstance(d["logs"], str)
        assert [s["step_name"] for s in d["steps"]][:1] == ["load"]
        # the user's parameters reached the step (recorded by the callable, when the load let it run)
        if d["run"]["state"] == "SUCCEEDED":
            assert RECORDED_KWARGS.get("mode") == "delta" and RECORDED_KWARGS.get("note") == "hello"
        # the history lists it; another job's run is not reachable through this job
        runs = client.get("/api/jobs/load-file", headers=h).json()["runs"]
        assert [x["id"] for x in runs] == [run_id]
        assert client.get(f"/api/jobs/manual-only/runs/{run_id}", headers=h).status_code == 404
        assert client.get(f"/api/jobs/load-file/runs/{run_id}/files/{run_id}-nope.csv", headers=h).status_code == 404
        # the uploaded file: listed on the run (the name as sent), downloadable by the user and the admin
        assert d["inputs"] == [{"step": "load", "name": "items.csv", "size": len(b"code,name\nA,Alpha\n")}]
        got = client.get(f"/api/jobs/load-file/runs/{run_id}/inputs/load", headers=h)
        assert got.status_code == 200 and got.content == b"code,name\nA,Alpha\n" and "items.csv" in got.headers["content-disposition"]
        assert client.get(f"/admin/jobs/runs/{run_id}/inputs/load", headers=_h(client, "admin")).content == b"code,name\nA,Alpha\n"
        assert client.get(f"/api/jobs/load-file/runs/{run_id}/inputs/nope", headers=h).status_code == 404
        assert client.get(f"/api/jobs/manual-only/runs/{run_id}/inputs/load", headers=h).status_code == 404
        assert client.get(f"/admin/jobs/runs/{run_id}/inputs/load", headers=h).status_code == 403
        # a plain manual job runs without files
        r = client.post("/api/jobs/manual-only/run", json={}, headers=h)
        assert r.status_code == 200, r.text


def test_job_level_param_with_query_choices(env) -> None:
    """A ``user_params`` entry without a step is a job-level parameter: its choices come from a
    read query (``query:<connector>/<query>:<value>:<label>``), the admin listing carries them for
    the Run-with-parameters modal, and a user's pick reaches every step typed like the default."""
    import time
    app, _ = env
    with TestClient(app) as client:
        admin = client.get("/admin/jobs", headers=_h(client, "admin")).json()["jobs"]
        pick = next(j for j in admin if j["id"] == "pick-app")
        assert pick["user_params"] == [{"name": "apps_id", "label": "Application", "step": None, "default": None, "required": False, "multiple": False, "choices": [{"value": "42", "label": "42"}]}]
        assert "user_params" not in next(j for j in admin if j["id"] == "ping")
        h = _h(client, "loader")
        assert client.post("/api/jobs/pick-app/run", json={"params": {"apps_id": "7"}}, headers=h).status_code == 422
        RECORDED_KWARGS.clear()
        r = client.post("/api/jobs/pick-app/run", json={"params": {"apps_id": "42"}}, headers=h)
        assert r.status_code == 200, r.text
        for _ in range(100):
            d = client.get(f"/api/jobs/pick-app/runs/{r.json()['run_id']}", headers=h).json()
            if d["run"]["state"] not in ("QUEUED", "RUNNING"):
                break
            time.sleep(0.05)
        assert d["run"]["state"] == "SUCCEEDED" and RECORDED_KWARGS.get("apps_id") == 42


def test_user_run_with_a_preset(env) -> None:
    """A menu item may pin one of the job's presets: the user's run applies its params and step
    toggles (the skipped step does not run), the user's own picks still win, and an unknown preset
    is refused."""
    import time
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "loader")
        assert client.post("/api/jobs/pick-app/run", json={"preset": "nope"}, headers=h).status_code == 422

        def fire(body: dict) -> list[dict]:
            RECORDED_PER_STEP.clear()
            r = client.post("/api/jobs/pick-app/run", json=body, headers=h)
            assert r.status_code == 200, r.text
            for _ in range(100):
                d = client.get(f"/api/jobs/pick-app/runs/{r.json()['run_id']}", headers=h).json()
                if d["run"]["state"] not in ("QUEUED", "RUNNING"):
                    break
                time.sleep(0.05)
            assert d["run"]["state"] == "SUCCEEDED", d
            return list(RECORDED_PER_STEP)

        assert [k.get("part") for k in fire({})] == [None, "extra"]          # no preset: both steps
        ran = fire({"preset": "Echo only"})
        assert len(ran) == 1 and ran[0]["apps_id"] == 42                     # extra skipped, preset's apps_id


def test_for_each_runs_once_per_value(env) -> None:
    """A ``for_each`` job with a ``multiple`` parameter: the picked values make one run each, one
    after another, each seeing a single typed value; "all" means every choice; a value outside
    the choices is refused; a single default value stays one plain run."""
    import time
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "loader")
        assert client.post("/api/jobs/per-app/run", json={"params": {"apps_id": ["1", "9"]}}, headers=h).status_code == 422

        def fire(body: dict, expect: int) -> list[int]:
            RECORDED_PER_STEP.clear()
            r = client.post("/api/jobs/per-app/run", json=body, headers=h)
            assert r.status_code == 200, r.text
            for _ in range(200):
                runs = client.get("/api/jobs/per-app", headers=h).json()["runs"]
                if len(RECORDED_PER_STEP) >= expect and all(x["state"] not in ("QUEUED", "RUNNING") for x in runs):
                    break
                time.sleep(0.05)
            return [k["apps_id"] for k in RECORDED_PER_STEP]

        assert fire({"params": {"apps_id": ["1", "3"]}}, 2) == [1, 3]
        assert fire({"params": {"apps_id": "all"}}, 3) == [1, 2, 3]
        assert fire({}, 1) == [1]
        assert len(client.get("/api/jobs/per-app", headers=h).json()["runs"]) == 6
        # the Job Runs listing: filtered by job, each run with its value, counts per state
        ah = _h(client, "admin")
        body = client.get("/admin/jobs/runs", params={"job": "per-app"}, headers=ah).json()
        assert body["total"] == 6 and body["counts"] == {"SUCCEEDED": 6}
        assert sorted(r["params"].get("apps_id") for r in body["runs"] if r["params"]) == [1, 1, 2, 3, 3]   # the plain default run has no override
        assert client.get("/admin/jobs/runs", params={"job": "per-app", "state": "failed"}, headers=ah).json()["runs"] == []
        page = client.get("/admin/jobs/runs", params={"q": "per-a", "limit": 2, "hours": 1}, headers=ah).json()
        assert len(page["runs"]) == 2 and page["total"] == 6
        assert client.get("/admin/jobs/runs", headers=_h(client, "loader")).status_code == 403


async def test_fan_out_values_shapes() -> None:
    from liberty.jobs.fanout import fan_out_values
    from liberty.jobs.schema import Job
    job = Job.model_validate({"id": "j", "for_each": "apps_id", "params": {"apps_id": 1},
                              "steps": [{"type": "python", "name": "s", "callable": "m:f"}],
                              "user_params": [{"name": "apps_id", "multiple": True, "choices": ["1", "2"]}]})
    assert await fan_out_values(job, None, connectors=None) is None                    # the plain default
    assert await fan_out_values(job, {"apps_id": "2,1,2"}, connectors=None) == [2, 1]
    assert await fan_out_values(job, {"apps_id": ["all"]}, connectors=None) == [1, 2]
    assert await fan_out_values(job, {"apps_id": "00A"}, connectors=None) is None
    import pytest
    with pytest.raises(ValueError):
        Job.model_validate({"id": "k", "for_each": "nope", "steps": [{"type": "python", "name": "s", "callable": "m:f"}]})


def test_for_each_from_the_admin_run_dialog(env) -> None:
    """The admin "Run with parameters" dialog sends the picked values as a list (or "all") in
    ``params``, with step toggles: one run per value as well."""
    import time
    app, _ = env
    with TestClient(app) as client:
        ah = _h(client, "admin")
        RECORDED_PER_STEP.clear()
        r = client.post("/admin/jobs/per-app/run", json={"params": {"apps_id": ["2", "3"]}, "step_enabled": {"echo": True}}, headers=ah)
        assert r.status_code == 200, r.text
        for _ in range(200):
            if len(RECORDED_PER_STEP) >= 2:
                break
            time.sleep(0.05)
        assert [k["apps_id"] for k in RECORDED_PER_STEP] == [2, 3]


def test_pause_and_resume_a_job(env) -> None:
    """``POST /admin/jobs/{id}/enabled``: the one job's ``enabled`` in jobs.toml (the rest of
    the file kept), the live registry and its trigger follow without a reload; the listing shows
    it; a bad body / an unknown job / a non-superuser are refused."""
    app, jobs_toml = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        before = jobs_toml.read_text()
        r = client.post("/admin/jobs/ping/enabled", json={"enabled": False}, headers=h)
        assert r.status_code == 200 and r.json() == {"job_id": "ping", "enabled": False, "next_run": None}, r.text
        after = jobs_toml.read_text()
        assert "enabled = false" in after and len(after.splitlines()) == len(before.splitlines()) + 1   # one line added
        assert 'description = "Trivial cron job"' in after                     # the rest of the file kept
        ping = next(j for j in client.get("/admin/jobs", headers=h).json()["jobs"] if j["id"] == "ping")
        assert ping["enabled"] is False and not ping.get("next_run")
        r = client.post("/admin/jobs/ping/enabled", json={"enabled": True}, headers=h)
        assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["next_run"]
        assert client.post("/admin/jobs/ping/enabled", json={"enabled": "no"}, headers=h).status_code == 422
        assert client.post("/admin/jobs/ghost/enabled", json={"enabled": True}, headers=h).status_code == 404
        assert client.post("/admin/jobs/ping/enabled", json={"enabled": True}, headers=_h(client, "loader")).status_code == 403


def test_retention_policy_saves_with_a_blank_files_days(env) -> None:
    """The Retention panel sends the whole policy back, ``files_days: null`` when left blank: the
    save drops the null (TOML has none) and the status shows the policy as saved."""
    app, jobs_toml = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        parsed = client.get("/admin/config/jobs/parsed", headers=h).json()
        meta = {**(parsed.get("meta") or {}), "retention": {"enabled": True, "days": 45, "keep_last_per_job": 50, "sweep_interval_minutes": 60, "files_days": None}}
        r = client.put("/admin/config/jobs/parsed", json={**parsed, "meta": meta}, headers=h)
        assert r.status_code == 200, r.text
        assert "files_days" not in jobs_toml.read_text() and "days = 45" in jobs_toml.read_text()
        meta["retention"]["files_days"] = 7
        assert client.put("/admin/config/jobs/parsed", json={**parsed, "meta": meta}, headers=h).status_code == 200
        assert "files_days = 7" in jobs_toml.read_text()


def test_server_log_page_api(env, tmp_path, monkeypatch) -> None:
    """``/admin/logs``: the startup is in the file; search by level / module / text, the export
    as text, the zip of the files, the levels changed at run time and saved to app.toml."""
    import io
    import zipfile
    app_toml = tmp_path / "app.toml"
    app_toml.write_text('[app]\nname = "Test"  # kept\n')
    monkeypatch.setenv("LIBERTY_APP_CONFIG", str(app_toml))
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        logging.getLogger("liberty.jobs.test").warning("disk almost full on %s", "/data")
        body = client.get("/admin/logs", params={"level": "WARNING", "q": "disk almost"}, headers=h).json()
        assert [e["msg"] for e in body["entries"]] == ["disk almost full on /data"]
        assert body["entries"][0]["logger"] == "liberty.jobs.test" and "liberty.jobs" in body["modules"]
        assert body["files"] and body["files"][0]["name"] == "liberty.log"
        assert client.get("/admin/logs", params={"module": "liberty.jobs", "q": "disk"}, headers=h).json()["entries"]
        assert not client.get("/admin/logs", params={"module": "liberty.auth", "q": "disk"}, headers=h).json()["entries"]
        txt = client.get("/admin/logs/export", params={"q": "disk almost"}, headers=h)
        assert txt.status_code == 200 and "disk almost full on /data" in txt.text and "attachment" in txt.headers["content-disposition"]
        z = zipfile.ZipFile(io.BytesIO(client.get("/admin/logs/download", headers=h).content))
        assert "liberty.log" in z.namelist()
        lv = client.post("/admin/logs/levels", json={"levels": {"liberty.jobs": "DEBUG"}, "persist": True}, headers=h)
        assert lv.status_code == 200 and lv.json()["levels"]["liberty.jobs"] == "DEBUG"
        assert logging.getLogger("liberty.jobs").level == logging.DEBUG
        saved = app_toml.read_text()
        assert "log_levels" in saved and "liberty.jobs" in saved and "# kept" in saved
        assert client.post("/admin/logs/levels", json={"levels": {"liberty.jobs": "LOUD"}}, headers=h).status_code == 422
        client.post("/admin/logs/levels", json={"levels": {"liberty.jobs": "NOTSET"}, "persist": True}, headers=h)
        assert logging.getLogger("liberty.jobs").level == logging.NOTSET and "log_levels" not in app_toml.read_text()
        assert client.get("/admin/logs", headers=_h(client, "loader")).status_code == 403


def test_retention_center(env, tmp_path) -> None:
    """``/admin/retention``: every store with its policy and size; a dry run counts what would
    go and leaves it; the real run removes it; stores without a purge (saved views, the server
    log) or without a policy (change packages at 0 days) refuse; superuser only."""
    import os
    import sqlite3
    from datetime import datetime, timedelta, timezone
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        old = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(tmp_path / "app.db") as c:
            c.execute("INSERT INTO nomaflow_job_runs (id, job_id, trigger_kind, state, started_at, finished_at) VALUES ('old-run', 'ping', 'manual', 'SUCCEEDED', ?, ?)", (old, old))
        upload = tmp_path / ".nomaflow" / "files" / "load-file" / "uploads" / "20260101-000000-old.csv"
        upload.parent.mkdir(parents=True, exist_ok=True)
        upload.write_text("a,b\n")
        t = (datetime.now(timezone.utc) - timedelta(days=90)).timestamp()
        os.utime(upload, (t, t))

        stores = {s["id"]: s for s in client.get("/admin/retention", headers=h).json()["stores"]}
        assert {"runs", "work_area", "config_versions", "server_log"} <= set(stores)
        assert stores["runs"]["policy"]["days"] == 30 and stores["runs"]["size"]["rows"] >= 1
        assert stores["work_area"]["size"]["files"] >= 1 and stores["server_log"]["can_run"] is False

        dry = client.post("/admin/retention/runs/run", headers=h).json()
        assert dry["dry_run"] is True and dry["deleted"] == 1
        with sqlite3.connect(tmp_path / "app.db") as c:
            assert c.execute("SELECT COUNT(*) FROM nomaflow_job_runs WHERE id = 'old-run'").fetchone()[0] == 1
        assert client.post("/admin/retention/runs/run", params={"dry_run": "false"}, headers=h).json()["deleted"] == 1
        with sqlite3.connect(tmp_path / "app.db") as c:
            assert c.execute("SELECT COUNT(*) FROM nomaflow_job_runs WHERE id = 'old-run'").fetchone()[0] == 0

        assert client.post("/admin/retention/work_area/run", headers=h).json()["deleted"] == 1 and upload.exists()
        assert client.post("/admin/retention/work_area/run", params={"dry_run": "false"}, headers=h).json()["deleted"] == 1
        assert not upload.exists()

        assert client.post("/admin/retention/config_versions/run", headers=h).status_code == 200
        assert client.post("/admin/retention/server_log/run", headers=h).status_code == 409
        if "change_packages" in stores:
            assert client.post("/admin/retention/change_packages/run", headers=h).status_code == 409   # 0 days = keep
        assert client.post("/admin/retention/nope/run", headers=h).status_code == 404
        assert client.get("/admin/retention", headers=_h(client, "loader")).status_code == 403


def test_web_manifest_is_public_and_named_after_the_instance(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.get("/manifest.webmanifest")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
        m = r.json()
        assert m["name"] == "Liberty Next" and m["start_url"] == "./" and m["icons"][0]["src"] == "favicon.svg"
