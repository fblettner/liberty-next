"""The licence usage report (environment, version, enabled accounts) sent once a day to the
licence management tool — the report NomaUBL sends, in the shape built into it — as a nomaflow
job the framework ships itself: visible, run now, paused like any job."""

from __future__ import annotations

import asyncio
import json
import time
import tomllib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from liberty import __version__
from liberty.config import (
    AISettings, AppSettings, AuthSettings, ChartSettings, ConnectorSettings, DashboardSettings, JobsSettings,
    LicenseSettings, MenuSettings, ScreenSettings, Settings,
)
from liberty.jobs.builtin import USAGE_JOB_ID, builtin_jobs
from liberty.jobs.registry import load_jobs
from liberty.licensing import usage
from liberty.main import create_app
from tests.test_web_jobs import _CONNECTORS_TOML, _JOBS_TOML, JWT_SECRET, _h, _seed_db

URL = "https://lms.test/api/usage"


def _settings(tmp_path, *, key: str = "the.licence.key", url: str = URL, environment: str = "Production", seed: bool = True) -> Settings:
    """Settings on a database under *tmp_path*; ``seed=False`` for a second look at the same one."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(_CONNECTORS_TOML.format(db_url=db_url))
    jobs_toml = tmp_path / "jobs.toml"
    if seed:
        jobs_toml.write_text(_JOBS_TOML)
        _seed_db(db_url)
    return Settings(
        app=AppSettings(static_dir="", environment=environment),
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"), screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"), dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
        jobs=JobsSettings(config_path=Path(jobs_toml), pool="default"),
        license=LicenseSettings(key=key, usage_url=url),
    )


@pytest.fixture
def lms(monkeypatch):
    """A stand-in for the licence tool: records what it receives, answers as it does."""
    seen: list[dict] = []
    answer = {"status": 200, "body": {"ok": True, "full": False}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": json.loads(request.content)})
        return httpx.Response(answer["status"], json=answer["body"])

    real = httpx.AsyncClient
    monkeypatch.setattr(usage.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return seen, answer


# -- the built-in job --------------------------------------------------------------------


def test_the_job_exists_only_where_there_is_something_to_report(tmp_path) -> None:
    (job,) = builtin_jobs(_settings(tmp_path))
    assert job.id == USAGE_JOB_ID and job.enabled and job.schedule == "17 4 * * *"
    assert job.steps[0].callable == "liberty.licensing.usage:send_usage_report"
    assert builtin_jobs(_settings(tmp_path, key="", seed=False)) == []      # the open framework: no key, no report, no job
    assert builtin_jobs(_settings(tmp_path, key="   ", seed=False)) == []
    assert builtin_jobs(_settings(tmp_path, url="", seed=False)) == []      # the address blanked: nothing is sent


def test_load_jobs_adds_a_built_in_unless_the_file_has_it(tmp_path) -> None:
    s = _settings(tmp_path)
    builtins = builtin_jobs(s)
    ids = [j.id for j in load_jobs(s.jobs.config_path, builtins=builtins).jobs()]
    assert ids[-1] == USAGE_JOB_ID and len(ids) == len(load_jobs(s.jobs.config_path).jobs()) + 1    # after the file's jobs
    # written into the file (paused, another hour): the file's version is the job
    path = Path(s.jobs.config_path)
    path.write_text(path.read_text() + f'\n[[jobs]]\nid = "{USAGE_JOB_ID}"\nenabled = false\nschedule = "0 6 * * 1"\n'
                    '[[jobs.steps]]\ntype = "python"\nname = "send"\ncallable = "liberty.licensing.usage:send_usage_report"\n')
    mine = load_jobs(path, builtins=builtins).get(USAGE_JOB_ID)
    assert mine.enabled is False and mine.schedule == "0 6 * * 1"
    assert [j.id for j in load_jobs(path, builtins=builtins).jobs()].count(USAGE_JOB_ID) == 1
    # no jobs.toml at all: the built-in alone
    assert [j.id for j in load_jobs(tmp_path / "none.toml", builtins=builtins).jobs()] == [USAGE_JOB_ID]


# -- the report --------------------------------------------------------------------------


def test_the_report_is_nomaubls_shape_without_monthly_figures(tmp_path) -> None:
    from liberty.runtime import load_registry

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    s, other = _settings(tmp_path / "a"), _settings(tmp_path / "b")

    async def go():
        reg = load_registry(s, None)
        try:
            return await usage.build_report(s, reg), usage.fingerprint(s, reg), usage.fingerprint(other, load_registry(other, None))
        finally:
            await reg.aclose()

    report, again, elsewhere = asyncio.run(go())
    assert report == {"environment": "Production", "fingerprint": again, "version": __version__,
                      "users": 5, "full": True, "months": []}        # the five enabled accounts of the seed
    assert len(again) == 16 and elsewhere != again                   # another database, another installation


def test_the_job_sends_the_report_and_shows_it(tmp_path, lms) -> None:
    seen, answer = lms
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        h = _h(client, "admin")
        listed = {j["id"]: j for j in client.get("/admin/jobs", headers=h).json()["jobs"]}
        assert USAGE_JOB_ID in listed and listed[USAGE_JOB_ID]["enabled"] is True                     # visible like any job

        run_id = client.post(f"/admin/jobs/{USAGE_JOB_ID}/run", headers=h, json={}).json()["run_id"]
        detail = _finished(client, h, run_id)
        assert (detail.get("run") or detail)["state"] == "SUCCEEDED", detail
        assert len(seen) == 1 and seen[0]["url"] == URL and seen[0]["auth"] == "Bearer the.licence.key"
        assert seen[0]["body"] == {"environment": "Production", "fingerprint": seen[0]["body"]["fingerprint"], "version": __version__,
                                   "users": 5, "full": True, "months": []}

        # refused by the tool: the run fails with the reason
        answer.update(status=401, body={"detail": "Invalid licence key"})
        run_id = client.post(f"/admin/jobs/{USAGE_JOB_ID}/run", headers=h, json={}).json()["run_id"]
        detail = _finished(client, h, run_id)
        assert (detail.get("run") or detail)["state"] == "FAILED" and "HTTP 401" in json.dumps(detail) and "Invalid licence key" in json.dumps(detail)


def _finished(client: TestClient, h: dict, run_id: str) -> dict:
    for _ in range(100):
        d = client.get(f"/admin/jobs/runs/{run_id}", headers=h).json()
        if (d.get("run") or d).get("state") not in ("QUEUED", "RUNNING", "PENDING"):
            return d
        time.sleep(0.05)
    raise AssertionError("the run did not finish")


def test_pausing_the_built_in_job_writes_it_into_jobs_toml(tmp_path, lms) -> None:
    s = _settings(tmp_path)
    app = create_app(s)
    path = Path(s.jobs.config_path)
    assert USAGE_JOB_ID not in path.read_text()
    with TestClient(app) as client:
        h = _h(client, "admin")
        r = client.post(f"/admin/jobs/{USAGE_JOB_ID}/enabled", headers=h, json={"enabled": False})
        assert r.status_code == 200 and r.json() == {"job_id": USAGE_JOB_ID, "enabled": False, "next_run": None}
        written = {j["id"]: j for j in tomllib.loads(path.read_text())["jobs"]}
        assert written[USAGE_JOB_ID]["enabled"] is False and written[USAGE_JOB_ID]["steps"][0]["callable"] == "liberty.licensing.usage:send_usage_report"
        assert len(written) == len(tomllib.loads(_JOBS_TOML)["jobs"]) + 1                              # the other jobs untouched
        # a reload reads the file's version: still paused, still listed once
        assert client.post("/admin/jobs/reload", headers=h).status_code == 200
        listed = [j for j in client.get("/admin/jobs", headers=h).json()["jobs"] if j["id"] == USAGE_JOB_ID]
        assert len(listed) == 1 and listed[0]["enabled"] is False
        assert client.post(f"/admin/jobs/{USAGE_JOB_ID}/enabled", headers=h, json={"enabled": True}).json()["enabled"] is True
        # a job that is neither in the file nor built in is still refused
        assert client.post("/admin/jobs/ghost/enabled", headers=h, json={"enabled": False}).status_code == 404


def test_the_editor_lists_the_built_in_job(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        jobs = client.get("/admin/config/jobs/parsed", headers=_h(client, "admin")).json()["jobs"]
        assert jobs[-1]["id"] == USAGE_JOB_ID


# -- what prevents a send ---------------------------------------------------------------


def test_nothing_is_sent_without_a_key_or_an_address(tmp_path, lms) -> None:
    seen, _ = lms
    _settings(tmp_path)
    for kw, reason in (({"key": ""}, "no licence key"), ({"url": ""}, "usage_url is blank"), ({"key": "ENC:abc"}, "no master key")):
        with pytest.raises(usage.UsageReportError, match=reason):
            asyncio.run(usage.send_usage_report(settings=_settings(tmp_path, seed=False, **kw), connectors=None))
    assert seen == []
