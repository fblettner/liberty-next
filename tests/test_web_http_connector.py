from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AISettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import ApiConnectorConfig, ConnectorsFile, EndpointDef, PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.connectors.registry import ConnectorRegistry
from liberty.main import create_app

JWT_SECRET = "web-http-test-secret"


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("nobody", password="nobodypw")
        await pools.dispose()

    asyncio.run(go())


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/boom":
        return httpx.Response(500, json={"err": "upstream"})
    payload = json.loads(request.content)
    return httpx.Response(200, json={"echo": payload["msg"]})


def _mock_registry() -> ConnectorRegistry:
    cfg = ConnectorsFile(
        connectors={
            "svc": ApiConnectorConfig(
                type="api",
                base_url="https://svc.test",
                endpoints=[
                    EndpointDef(name="echo", method="POST", path="/echo", body='{"msg": "{{msg}}"}'),
                    EndpointDef(name="boom", method="GET", path="/boom"),
                ],
            )
        }
    )
    return ConnectorRegistry(cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(_handler)))


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(f'[pools.default]\nurl = "{db_url}"\n')
    _seed(db_url)
    settings = Settings(
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings)


def _h(client: TestClient, username: str) -> dict[str, str]:
    token = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_http_call_success(app) -> None:
    with TestClient(app) as client:
        app.state.connectors = _mock_registry()
        r = client.post("/api/http/svc/echo", json={"params": {"msg": "hi"}}, headers=_h(client, "admin"))
        assert r.status_code == 200
        body = r.json()
        assert body["connector"] == "svc" and body["endpoint"] == "echo"
        assert body["success"] is True and body["status_code"] == 200
        assert body["json"] == {"echo": "hi"}


def test_http_call_upstream_failure_is_structured_200(app) -> None:
    with TestClient(app) as client:
        app.state.connectors = _mock_registry()
        r = client.post("/api/http/svc/boom", headers=_h(client, "admin"))
        assert r.status_code == 200  # the framework call succeeded; the *upstream* didn't
        body = r.json()
        assert body["success"] is False and body["status_code"] == 500 and body["error"] == "HTTP 500"


@pytest.mark.parametrize(("user", "code"), [("admin", 200), ("nobody", 403)])
def test_http_call_permission(app, user, code) -> None:
    with TestClient(app) as client:
        app.state.connectors = _mock_registry()
        assert client.post("/api/http/svc/echo", json={"params": {"msg": "x"}}, headers=_h(client, user)).status_code == code


def test_http_call_requires_auth(app) -> None:
    with TestClient(app) as client:
        app.state.connectors = _mock_registry()
        assert client.post("/api/http/svc/echo", json={"params": {"msg": "x"}}).status_code == 401


def test_http_unknown_endpoint_and_connector(app) -> None:
    with TestClient(app) as client:
        app.state.connectors = _mock_registry()
        assert client.post("/api/http/svc/ghost", headers=_h(client, "admin")).status_code == 404
        assert client.post("/api/http/ghost/echo", headers=_h(client, "admin")).status_code == 404
        # permission first → no enumeration
        assert client.post("/api/http/ghost/echo", headers=_h(client, "nobody")).status_code == 403


def test_http_connector_in_listing(app) -> None:
    with TestClient(app) as client:
        app.state.connectors = _mock_registry()
        svc = next(c for c in client.get("/api/connectors", headers=_h(client, "admin")).json()["connectors"] if c["name"] == "svc")
        assert svc["type"] == "api" and svc["base_url"] == "https://svc.test"
        assert {e["name"] for e in svc["endpoints"]} == {"echo", "boom"}
