"""An API connector's per-pool ``targets`` — one server per environment (AIS per JDE pool).

The call follows the pool the request (``X-Liberty-Pool``) or the job step runs for: the
connector's own pool or none → its own server, a pool in ``targets`` → that server, a pool of
its environments without a target → refused (never the default server)."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.api import APIConnector, current_pool
from liberty.connectors.base import ApiTargetMissingError
from liberty.connectors.config import ApiConnectorConfig, ApiTargetConfig, ConnectorsFile, EndpointDef, PoolConfig, SqlConnectorConfig
from liberty.connectors.db import PoolRegistry
from liberty.connectors.registry import ConnectorRegistry
from liberty.crypto import encrypt
from liberty.jobs import JobRunner, ManualTrigger, RunContext, RunState, Step, StepResult, StepType, load_jobs
from liberty.main import create_app

MK = "targets-test-master-key"
JWT_SECRET = "api-targets-test-secret"


def _ais(**kw) -> ApiConnectorConfig:
    return ApiConnectorConfig(
        type="api", base_url="https://ais-prod.test/jderest", auth_type="basic",
        auth_username="JDE", auth_password="prodpw", pool="jde_prod",
        targets={
            "jde_dev": ApiTargetConfig(base_url="https://ais-dev.test/jderest", auth_password=encrypt("devpw", MK)),
            "jde_uat": ApiTargetConfig(base_url="https://ais-uat.test/jderest", auth_username="UATUSER", verify_ssl=False),
        },
        endpoints=[EndpointDef(name="reset", method="POST", path="/v2/reset", body='{"user": "{{user}}"}')],
        **kw,
    )


def _recorder() -> tuple[list[tuple[str, str, str]], httpx.AsyncClient]:
    """(url, basic user, basic password) per request."""
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        user, _, pw = base64.b64decode(request.headers["authorization"].split()[1]).decode().partition(":")
        seen.append((str(request.url), user, pw))
        return httpx.Response(200, json={"ok": json.loads(request.content)["user"]})

    return seen, httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _registry(client: httpx.AsyncClient) -> ConnectorRegistry:
    """jde (SQL) runs on jde_prod + jde_dev + jde_uat + jde_cv (no AIS target: refused);
    other (SQL) on its own pool, outside the JDE environments."""
    cfg = ConnectorsFile(
        pools={p: PoolConfig(url="sqlite+aiosqlite://") for p in ("jde_prod", "jde_dev", "jde_uat", "jde_cv", "other")},
        connectors={
            "ais": _ais(),
            "jde": SqlConnectorConfig(type="sql", pool="jde_prod", pools=["jde_dev", "jde_uat", "jde_cv"]),
            "other": SqlConnectorConfig(type="sql", pool="other"),
        },
    )
    return ConnectorRegistry(cfg, http_client=client, master_key=MK)


# --------------------------------------------------------------------------- #
# config + resolution
# --------------------------------------------------------------------------- #


def test_targets_need_the_connectors_own_pool() -> None:
    with pytest.raises(ValueError, match="targets need `pool`"):
        ApiConnectorConfig(type="api", base_url="x", targets={"a": ApiTargetConfig()})
    with pytest.raises(ValueError, match="can't also be a target"):
        ApiConnectorConfig(type="api", base_url="x", pool="a", targets={"a": ApiTargetConfig()})


@pytest.mark.asyncio
async def test_for_pool_picks_the_environments_server() -> None:
    conn = APIConnector("ais", _ais(), master_key=MK)
    try:
        assert conn.for_pool(None) is conn and conn.for_pool("jde_prod") is conn
        dev, uat = conn.for_pool("jde_dev"), conn.for_pool("jde_uat")
        assert dev.config.base_url == "https://ais-dev.test/jderest" and dev.target_pool == "jde_dev"
        assert (dev.auth_username, dev.auth_password) == ("JDE", "devpw")      # user inherited, password decrypted
        assert (uat.auth_username, uat.auth_password) == ("UATUSER", "prodpw")  # password inherited
        assert uat.config.verify_ssl is False and conn.config.verify_ssl is True
        assert dev.endpoint_names == conn.endpoint_names == ["reset"]          # the endpoints are shared
        assert dev._client is not conn._client                                  # own client, own token
        assert conn.for_pool("elsewhere") is conn                               # not one of its environments
    finally:
        await conn.aclose()


@pytest.mark.asyncio
async def test_an_environment_without_target_is_refused() -> None:
    seen, client = _recorder()
    reg = _registry(client)
    try:
        assert reg.get("ais").governed_pools == {"jde_prod", "jde_dev", "jde_uat", "jde_cv"}
        with pytest.raises(ApiTargetMissingError, match="no target for pool 'jde_cv'"):
            reg.api("ais", pool="jde_cv")
        assert reg.api("ais", pool="other") is reg.get("ais")   # another app's pool → the connector's own
        assert seen == []
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_the_registry_follows_the_current_pool() -> None:
    seen, client = _recorder()
    reg = _registry(client)
    try:
        for pool in (None, "jde_dev", "jde_uat", "jde_prod"):
            token = current_pool.set(pool)
            try:
                await reg.api("ais").call("reset", {"user": "LYTEST01"})
            finally:
                current_pool.reset(token)
        assert seen == [
            ("https://ais-prod.test/jderest/v2/reset", "JDE", "prodpw"),
            ("https://ais-dev.test/jderest/v2/reset", "JDE", "devpw"),
            ("https://ais-uat.test/jderest/v2/reset", "UATUSER", "prodpw"),
            ("https://ais-prod.test/jderest/v2/reset", "JDE", "prodpw"),
        ]
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_a_connector_without_targets_ignores_the_pool() -> None:
    conn = APIConnector("plain", ApiConnectorConfig(type="api", base_url="https://x.test"))
    try:
        assert conn.for_pool("anything") is conn and conn.governed_pools == frozenset()
    finally:
        await conn.aclose()


# --------------------------------------------------------------------------- #
# the HTTP route: X-Liberty-Pool picks the server
# --------------------------------------------------------------------------- #


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(f'[pools.default]\nurl = "{db_url}"\n')
    _seed(db_url)
    return create_app(Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    ))


def test_http_call_goes_to_the_pools_server(app) -> None:
    seen, client = _recorder()
    with TestClient(app) as tc:
        app.state.connectors = _registry(client)
        token = tc.post("/auth/login", json={"username": "admin", "password": "adminpw"}).json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        for pool in ("jde_dev", "", "jde_uat"):
            r = tc.post("/api/http/ais/reset", json={"params": {"user": "LYTEST01"}}, headers={**auth, "X-Liberty-Pool": pool})
            assert r.status_code == 200 and r.json()["success"] is True
        r = tc.post("/api/http/ais/reset", json={"params": {"user": "LYTEST01"}}, headers={**auth, "X-Liberty-Pool": "jde_cv"})
        assert r.status_code == 503 and "no target for pool 'jde_cv'" in r.json()["detail"]
    assert [u for u, _, _ in seen] == [
        "https://ais-dev.test/jderest/v2/reset", "https://ais-prod.test/jderest/v2/reset", "https://ais-uat.test/jderest/v2/reset",
    ]


# --------------------------------------------------------------------------- #
# jobs: a step's pool
# --------------------------------------------------------------------------- #


class _PoolRecorder:
    def __init__(self) -> None:
        self.pools: list[str | None] = []

    async def execute(self, step: Step, ctx: RunContext) -> StepResult:
        self.pools.append(current_pool.get())
        return StepResult()


@pytest.mark.asyncio
async def test_a_job_step_runs_for_its_pool(tmp_path) -> None:
    from liberty.jobs import JobDatabase

    path = tmp_path / "jobs.toml"
    path.write_text("""
[[jobs]]
id = "j"

[[jobs.steps]]
type = "sql_query"
name = "dev"
connector = "c"
query = "q"
pool = "jde_dev"

[[jobs.steps]]
type = "sql_query"
name = "default"
connector = "c"
query = "q"
""", encoding="utf-8")
    job = load_jobs(path).config.jobs[0]
    pools = PoolRegistry({"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runner.db'}")})
    db = JobDatabase(pools, "default")
    await db.create_schema()
    try:
        rec = _PoolRecorder()
        run = await JobRunner(db, {StepType.SQL_QUERY: rec}).run(job, ManualTrigger(triggered_by="t"))
        assert run.state == RunState.SUCCEEDED.value
        assert rec.pools == ["jde_dev", None]
        assert current_pool.get() is None   # the runner's own context is untouched
    finally:
        await pools.dispose()


# --------------------------------------------------------------------------- #
# integrity: an environment without its server
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_integrity_flags_an_environment_without_target() -> None:
    from types import SimpleNamespace

    from liberty.web.integrity import check_integrity

    _, client = _recorder()
    reg = _registry(client)
    try:
        issues = [i for i in check_integrity(SimpleNamespace(connectors=reg)) if i.category.startswith("Missing")]
        assert [(i.severity, i.category, i.message) for i in issues] == [
            ("error", "Missing environment target", "connector 'ais' has no target for pool 'jde_cv' — a call for that environment is refused"),
        ]
    finally:
        await reg.aclose()
