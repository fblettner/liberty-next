"""job_form / job_run (liberty.ai.write_tools): the assistant runs a nomaflow job for the user,
through the functions of the job page's own routes (`/api/jobs`) — the declared parameters, the
presets, `job:<id>` — proposed on a card in the chat, fired over MCP."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from liberty.ai.tools import current_channel
from liberty.auth.db import AuthDatabase
from liberty.auth.principal import current_principal
from liberty.auth.routes import principal_for
from liberty.auth.service import AuthService
from liberty.config import (
    AISettings, AppSettings, AuthSettings, ChartSettings, ConnectorSettings, DashboardSettings, JobsSettings, MenuSettings,
    ScreenSettings, Settings,
)
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app
from tests import test_web_jobs as jobs_fixture


def _app(tmp_path, *, write_tools: bool = True, mcp_changes: bool = True):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(jobs_fixture._CONNECTORS_TOML.format(db_url=db_url))
    (tmp_path / "jobs.toml").write_text(jobs_fixture._JOBS_TOML)
    jobs_fixture._seed_db(db_url)

    async def more() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        async with AuthDatabase(pools, "default").session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("runner", permissions=["ai:chat", "ai:write", "mcp:use", "job:pick-app", "job:load-file"])
            await svc.get_or_create_role("writer_only", permissions=["ai:chat", "ai:write", "mcp:use"])
            await svc.create_user("runner", password="runnerpw", roles=["runner"])
            await svc.create_user("writer_only", password="writer_onlypw", roles=["writer_only"])
        await pools.dispose()

    asyncio.run(more())
    return create_app(Settings(
        app=AppSettings(static_dir=""), connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"), screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"), dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        auth=AuthSettings(backend="db", jwt_secret="job-tools-test-secret-0123456789abcdefgh", pool="default"),
        ai=AISettings(enabled=True, api_key="", config_path=tmp_path / "ai.toml", write_tools=write_tools, mcp_enabled=True, mcp_changes=mcp_changes),
        jobs=JobsSettings(config_path=Path(tmp_path / "jobs.toml"), pool="default"),
    ))


def _h(client: TestClient, user: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + client.post("/auth/login", json={"username": user, "password": f"{user}pw"}).json()["access_token"]}


def _wait(client: TestClient, user: str, job: str, run_id: str) -> dict:
    for _ in range(100):
        d = client.get(f"/api/jobs/{job}/runs/{run_id}", headers=_h(client, user)).json()
        state = (d.get("run") or d).get("state")
        if state in ("SUCCEEDED", "FAILED", "CANCELED"):
            return d
        time.sleep(0.05)
    raise AssertionError("the run did not finish")


def _tool(client: TestClient, user: str, name: str, arguments: dict, channel: str = "chat"):
    async def who():
        return principal_for(await client.app.state.auth_backend.get_by_username(user))
    principal = client.portal.call(who)

    async def go():
        current_principal.set(principal)
        current_channel.set(channel)
        content, is_error = await client.app.state.ai.tools.execute(name, arguments)
        return json.loads(content), is_error
    return client.portal.call(go)


def test_a_job_is_run_as_its_page_runs_it(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        run = lambda user, **body: client.post("/ai/job-run", json=body, headers=_h(client, user))   # noqa: E731
        # a check runs nothing
        out = run("runner", job="pick-app", params={"apps_id": "42"}, dry_run=True).json()
        assert out == {"job": "pick-app", "description": "A job-level parameter picked from a read query (the applications of an app).",
                       "params": {"apps_id": "42"}, "ran": False}
        # the run: queued, then done with the user's parameter
        jobs_fixture.RECORDED_PER_STEP.clear()
        r = run("runner", job="pick-app", params={"apps_id": "42"})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["ran"] is True and out["status"] == "queued" and out["run_id"]
        _wait(client, "runner", "pick-app", out["run_id"])
        assert jobs_fixture.RECORDED_PER_STEP and jobs_fixture.RECORDED_PER_STEP[0]["apps_id"] == 42
        # what the page refuses is refused, in words
        refused = lambda r, *words: r.status_code == 409 and all(w in r.json()["detail"] for w in words)   # noqa: E731
        assert refused(run("runner", job="pick-app", params={"apps_id": "7"}), "not one of the choices")
        assert refused(run("runner", job="pick-app", params={"nope": 1}), "not a parameter", "apps_id")
        assert refused(run("runner", job="load-file"), "needs a file for: load")
        assert refused(run("runner", job="pick-app", files={"echo": "/tmp/x.csv"}), "not a step", "takes a file")
        assert refused(run("runner", job="pick-app", preset="ghost"), "not a preset")
        # the locks: ai:write, then the job's own permission — and no enumeration of job ids
        assert run("loader", job="pick-app").status_code == 403                               # job:pick-app without ai:write
        r = run("writer_only", job="pick-app")
        assert r.status_code == 403 and "job:pick-app" in r.json()["detail"]
        assert run("writer_only", job="ghost").status_code == 403
        assert run("admin", job="ghost").status_code == 409
        assert client.post("/ai/job-run", json={"job": "pick-app"}).status_code == 401


def test_job_form_and_the_card(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        form, err = _tool(client, "runner", "job_form", {"job": "pick-app"})
        assert not err, form
        assert form["job"] == "pick-app" and form["running"] is False and form["last_runs"] == [] and form["presets"] == ["Echo only"]
        assert form["params"] == [{"name": "apps_id", "label": "Application", "choices": [{"value": "42", "label": "42"}]}]
        assert _tool(client, "runner", "job_form", {"job": "load-file"})[0]["needs_uploaded_file"] == ["load"]
        out, err = _tool(client, "writer_only", "job_form", {"job": "pick-app"})
        assert err and "permission denied" in out["error"]

        # in the chat: a card, nothing started
        out, err = _tool(client, "runner", "job_run", {"job": "pick-app", "params": {"apps_id": "42"}})
        assert not err, out
        p = out["proposal"]
        assert p["kind"] == "job_run" and p["summary"] == "Run the job pick-app — apps_id=42" and p["payload"]["preview"]["ran"] is False
        assert _tool(client, "runner", "job_form", {"job": "pick-app"})[0]["last_runs"] == []
        # the card applied
        applied = client.post("/ai/job-run", json={k: p["payload"][k] for k in ("job", "params", "preset")}, headers=_h(client, "runner"))
        assert applied.status_code == 200 and applied.json()["run_id"]
        _wait(client, "runner", "pick-app", applied.json()["run_id"])
        runs = _tool(client, "runner", "job_form", {"job": "pick-app"})[0]["last_runs"]
        assert len(runs) == 1 and runs[0]["state"] == "SUCCEEDED" and runs[0]["triggered_by"] == "runner"
        # over MCP the client asked its user: the tool fires
        out, err = _tool(client, "runner", "job_run", {"job": "pick-app", "preset": "Echo only"}, channel="mcp")
        assert not err and out["ran"] is True and out["run_id"]
        _wait(client, "runner", "pick-app", out["run_id"])
        out, err = _tool(client, "runner", "job_run", {"job": "load-file"}, channel="mcp")
        assert err and "needs a file for: load" in out["error"]


def test_a_job_that_loads_a_file_takes_it_from_the_card(tmp_path) -> None:
    """The assistant proposes the run; the card asks the user for the file, uploads it through the
    job's own upload and runs the job with it — the file never goes through the model."""
    with TestClient(_app(tmp_path)) as client:
        h = _h(client, "runner")
        out, err = _tool(client, "runner", "job_run", {"job": "load-file", "params": {"mode": "delta"}})
        assert not err, out
        p = out["proposal"]
        assert p["payload"]["preview"]["needs_files"] == [{"step": "load", "sheet": "Items"}]
        assert p["payload"]["preview"]["accept"] == [".csv", ".tsv", ".txt", ".xlsm", ".xlsx"]
        assert "needs a file for the step load: chosen on this card" in p["notes"] and "chooses the file there" in out["message"]
        # what the card does on Apply: the job's upload, then the run with the path it returned
        up = client.post("/api/jobs/load-file/upload", files={"file": ("items.csv", b"code,name\nA,Alpha\n", "text/csv")}, headers=h).json()
        jobs_fixture.RECORDED_PER_STEP.clear()
        r = client.post("/ai/job-run", json={"job": "load-file", "params": {"mode": "delta"}, "files": {"load": up["path"]}}, headers=h)
        assert r.status_code == 200, r.text
        d = _wait(client, "runner", "load-file", r.json()["run_id"])
        # the run took the user's file for its step (whatever the load then made of it in this fixture)
        assert d["run"]["triggered_by"] == "runner" and [x["step_name"] for x in d["steps"]][:1] == ["load"]
        assert d["inputs"] == [{"step": "load", "name": "items.csv", "size": len(b"code,name\nA,Alpha\n")}]
        # only a file uploaded through this job is taken
        bad = client.post("/ai/job-run", json={"job": "load-file", "files": {"load": "/etc/passwd"}}, headers=h)
        assert bad.status_code == 409 and "upload the file first" in bad.json()["detail"]


def test_the_job_tools_follow_the_write_switch(tmp_path) -> None:
    with TestClient(_app(tmp_path, write_tools=False)) as client:
        assert client.post("/ai/job-run", json={"job": "pick-app"}, headers=_h(client, "runner")).status_code == 404
        assert client.app.state.ai.tools.get("job_run") is None


def test_a_file_sent_by_the_claude_desktop_extension(tmp_path) -> None:
    """`POST /mcp/files`: the extension sends a file of the folder its user chose, with the user's
    personal token; it lands in the job's upload area and `job_run` then names it. Nothing runs
    on the upload; the caller needs what running the job needs; changes over MCP must be allowed."""
    import pytest
    pytest.importorskip("mcp")
    content = b"code,name\nA,Alpha\n"
    with TestClient(_app(tmp_path)) as client:
        def token(user: str) -> dict[str, str]:
            t = client.post("/auth/tokens", json={"days": 1}, headers=_h(client, user)).json()["token"]
            return {"Authorization": f"Bearer {t}"}

        send = lambda user, **q: client.post("/mcp/files", params=q, content=content, headers=token(user))   # noqa: E731
        r = send("runner", job="load-file", filename="items.csv")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["job"] == "load-file" and out["file"].endswith("-items.csv") and out["size"] == len(content) and out["steps_that_take_a_file"] == ["load"]
        assert _tool(client, "runner", "job_form", {"job": "load-file"})[0]["last_runs"] == []            # nothing ran
        # the run names the file by the name the upload returned
        ran, err = _tool(client, "runner", "job_run", {"job": "load-file", "files": {"load": out["file"]}}, channel="mcp")
        assert not err and ran["ran"] is True, ran
        d = _wait(client, "runner", "load-file", ran["run_id"])
        assert d["inputs"] == [{"step": "load", "name": "items.csv", "size": len(content)}]
        # a name that is not a file of this job's uploads is refused, as on the job page
        bad, err = _tool(client, "runner", "job_run", {"job": "load-file", "files": {"load": "nope.csv"}}, channel="mcp")
        assert err and "upload the file first" in bad["error"]
        # the refusals of the upload itself
        assert send("runner", job="load-file", filename="x.exe").status_code == 400
        assert send("runner", job="pick-app", filename="items.csv").status_code == 404                     # the job takes no file
        assert send("writer_only", job="load-file", filename="items.csv").status_code == 403               # not allowed to run it
        assert client.post("/mcp/files", params={"job": "load-file", "filename": "items.csv"}, content=content).status_code == 401
    (tmp_path / "off").mkdir()
    with TestClient(_app(tmp_path / "off", mcp_changes=False)) as client:
        t = client.post("/auth/tokens", json={"days": 1}, headers=_h(client, "runner")).json()["token"]
        r = client.post("/mcp/files", params={"job": "load-file", "filename": "items.csv"}, content=content, headers={"Authorization": f"Bearer {t}"})
        assert r.status_code == 403 and "Changes over MCP are off" in r.json()["detail"]
