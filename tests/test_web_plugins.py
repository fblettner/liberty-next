"""Tests for ``/api/plugins`` — list + invoke plugin callables from a ``call_plugin`` action.

Same TestClient + DB-auth pattern as test_web_jobs.py. A tiny plugin package is written under a
tmp ``plugins/`` dir (pointed at via ``LIBERTY_APPS_DIR``) so discovery + real invocation run
end-to-end against the actual wiring (connector injection, kwarg coercion, permission gate)."""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
from liberty.jobs import JobDatabase
from liberty.main import create_app

JWT_SECRET = "web-plugins-test-secret"

_CONNECTORS_TOML = """
[pools.default]
url = "{db_url}"

[connectors.db]
type = "sql"
pool = "default"

[[connectors.db.queries]]
name = "answer"
sql = "SELECT 42 AS answer"
"""

# A tiny plugin module: one ``j_*`` callable that exercises connector injection, str→int
# coercion (role_id), and a dict return (→ extras / first_row).
_PLUGIN_MODULE = '''
async def j_echo_merge(*, connectors, role_id: int, note: str = "") -> dict:
    return {"role_id": role_id, "note": note, "got_connectors": connectors is not None}
'''


def _seed_db(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        auth = AuthDatabase(pools, "default")
        await auth.create_schema()
        jobs = JobDatabase(pools, "default")
        await jobs.create_schema()
        async with auth.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            # A non-superuser who may run any plugin, and one who may not.
            await svc.get_or_create_role("plugins", permissions=["plugin:*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("merger", password="mergerpw", roles=["plugins"])
            await svc.create_user("reader", password="readerpw")
        await pools.dispose()
    asyncio.run(go())


@pytest.fixture
def env(tmp_path, monkeypatch):
    # plugins/ lives at ${LIBERTY_APPS_DIR}/../plugins — point the env var at tmp/config so the
    # plugins root resolves to tmp/plugins (the same resolution the endpoint + sys-path hook use).
    (tmp_path / "config").mkdir()
    pkg = tmp_path / "plugins" / "plg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "merge.py").write_text(textwrap.dedent(_PLUGIN_MODULE))
    monkeypatch.setenv("LIBERTY_APPS_DIR", str(tmp_path / "config"))

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(_CONNECTORS_TOML.format(db_url=db_url))
    jobs_toml = tmp_path / "jobs.toml"
    jobs_toml.write_text("")
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
    return create_app(settings)


def _h(client: TestClient, username: str) -> dict[str, str]:
    r = client.post("/auth/login", json={"username": username, "password": f"{username}pw"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


CALLABLE = "plg.merge:j_echo_merge"


# ── GET /api/plugins/callables ────────────────────────────────────────────────────────────────
def test_callables_listed_for_authenticated_user(env) -> None:
    with TestClient(env) as client:
        assert client.get("/api/plugins/callables").status_code == 401   # unauthenticated
        r = client.get("/api/plugins/callables", headers=_h(client, "reader"))
        assert r.status_code == 200   # the picker is readable by any authed user
        names = {c["callable"] for c in r.json()["callables"]}
        assert CALLABLE in names


# ── POST /api/plugins/run ─────────────────────────────────────────────────────────────────────
def test_run_invokes_callable_with_injection_and_coercion(env) -> None:
    with TestClient(env) as client:
        r = client.post(
            "/api/plugins/run",
            headers=_h(client, "merger"),
            json={"callable": CALLABLE, "params": {"role_id": "7", "note": "hi"}},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        # role_id coerced str→int, connectors injected, dict return surfaced as extras/first_row.
        assert body["extras"] == {"role_id": 7, "note": "hi", "got_connectors": True}
        assert body["first_row"]["role_id"] == 7
        assert body["rows"] == [body["extras"]]


def test_run_requires_authentication(env) -> None:
    with TestClient(env) as client:
        assert client.post("/api/plugins/run", json={"callable": CALLABLE}).status_code == 401


def test_run_requires_plugin_permission(env) -> None:
    with TestClient(env) as client:
        r = client.post(
            "/api/plugins/run",
            headers=_h(client, "reader"),   # no plugin:* permission
            json={"callable": CALLABLE, "params": {"role_id": "1"}},
        )
        assert r.status_code == 403
        assert "plugin:" in r.json()["detail"]


def test_run_unknown_callable_is_404(env) -> None:
    with TestClient(env) as client:
        r = client.post(
            "/api/plugins/run",
            headers=_h(client, "merger"),
            json={"callable": "plg.merge:j_not_real", "params": {}},
        )
        assert r.status_code == 404
        assert "Unknown plugin callable" in r.json()["detail"]


def test_run_rejects_malformed_callable(env) -> None:
    with TestClient(env) as client:
        r = client.post(
            "/api/plugins/run",
            headers=_h(client, "merger"),
            json={"callable": "no_colon_here"},
        )
        assert r.status_code == 422


def test_run_bad_kwarg_coercion_is_400(env) -> None:
    with TestClient(env) as client:
        r = client.post(
            "/api/plugins/run",
            headers=_h(client, "merger"),
            json={"callable": CALLABLE, "params": {"role_id": "not-an-int"}},
        )
        assert r.status_code == 400
        assert "coerced" in r.json()["detail"]
