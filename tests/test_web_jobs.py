"""Tests for the ``/admin/jobs`` endpoints (chunk 3).

Uses the same TestClient + DB-backed auth pattern as test_web_admin.py.
Each test bootstraps a fresh FastAPI app via :func:`create_app` so the full
lifespan (nomaflow registry + runner + scheduler + recovery sweep + APScheduler)
runs, and the endpoints hit the actual wiring rather than a mock.
"""

from __future__ import annotations

import asyncio
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
    filtered = {k: v for k, v in kw.items() if k not in ("connectors", "ctx", "settings")}
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
        assert set(ids) == {"ping", "manual-only", "disabled", "echo-kwargs", "shared-params"}
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
        assert ids == {"ping", "manual-only", "disabled", "echo-kwargs", "shared-params"}


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
        assert set(nf["jobs"]) == {"ping", "manual-only", "disabled", "echo-kwargs", "shared-params"}
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
