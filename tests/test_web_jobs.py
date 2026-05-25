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
"""


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
        assert set(ids) == {"ping", "manual-only", "disabled"}
        # Only the enabled+scheduled job is registered with the scheduler
        assert ids["ping"]["registered_with_scheduler"] is True
        assert ids["manual-only"]["registered_with_scheduler"] is False
        assert ids["disabled"]["registered_with_scheduler"] is False
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
        assert ids == {"ping", "manual-only", "disabled"}


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
        assert set(nf["jobs"]) == {"ping", "manual-only", "disabled"}
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
