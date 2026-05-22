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


def test_run_now_executes_job_synchronously(env) -> None:
    """Fire ping manually; the response blocks until the run terminates, and
    a JobRun row has landed in the DB (recorded as triggered_by the admin)."""
    app, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post("/admin/jobs/ping/run", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body == {"job_id": "ping", "triggered_by": "admin", "status": "completed"}

        # Verify a JobRun landed and reached SUCCEEDED
        jobs = app.state.jobs
        async def fetch():
            async with jobs.db.session() as s:
                return (await s.execute(select(JobRun).where(JobRun.job_id == "ping"))).scalars().all()
        runs = asyncio.run(fetch())
        assert len(runs) == 1
        assert runs[0].state == RunState.SUCCEEDED.value
        assert runs[0].triggered_by == "admin"


def test_run_now_for_unknown_job_returns_404(env) -> None:
    app, _ = env
    with TestClient(app) as client:
        r = client.post("/admin/jobs/no-such-job/run", headers=_h(client, "admin"))
        assert r.status_code == 404
        assert "no-such-job" in r.json()["detail"]


def test_run_now_for_manual_only_job_works(env) -> None:
    """``manual-only`` has no cron schedule; it's not registered with APScheduler
    but the manual fire endpoint still triggers it (that's the whole point)."""
    app, _ = env
    with TestClient(app) as client:
        r = client.post("/admin/jobs/manual-only/run", headers=_h(client, "admin"))
        assert r.status_code == 200
        assert r.json()["status"] == "completed"


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
