"""The OAuth sign-in of the MCP endpoint (liberty.auth.oauth): discovery, registration, the
consent of a signed-in user, the code against its PKCE verifier, the rotating refresh — and
what ends the access. Nothing is stored: every piece is a signed token."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.routes import principal_from_personal_token
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


def _app(tmp_path, *, mcp_enabled: bool = True, public_url: str = "", max_days: int = 30):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(f'[pools.default]\nurl = "{db_url}"\n')

    async def seed() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("mcp", permissions=["mcp:use", "sql:shop:orders"])
            await svc.get_or_create_role("plain", permissions=["sql:shop:orders"])
            await svc.create_user("bob", password="bobpw-123", roles=["mcp"])
            await svc.create_user("carol", password="carolpw-123", roles=["plain"])
        await pools.dispose()

    asyncio.run(seed())
    return create_app(Settings(
        app=AppSettings(static_dir="", public_url=public_url), connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(backend="db", jwt_secret="oauth-sign-in-test-secret-0123456789abcdef", pool="default"),
        ai=AISettings(enabled=True, api_key="", config_path=tmp_path / "ai.toml", mcp_enabled=mcp_enabled, mcp_token_max_days=max_days),
    ))


@pytest.fixture
def app(tmp_path):
    return _app(tmp_path)


def _login(client: TestClient, user: str, password: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + client.post("/auth/login", json={"username": user, "password": password}).json()["access_token"]}


def _register(client: TestClient, uris: list[str] | None = None, name: str = "Claude") -> str:
    r = client.post("/oauth/register", json={"client_name": name, "redirect_uris": uris or [CALLBACK]})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _code(client: TestClient, who: dict[str, str], client_id: str, **over) -> str:
    body = {"client_id": client_id, "redirect_uri": CALLBACK, "code_challenge": CHALLENGE, "state": "s-1", **over}
    r = client.post("/oauth/approve", json=body, headers=who)
    assert r.status_code == 200, r.text
    target = urlsplit(r.json()["redirect_to"])
    query = parse_qs(target.query)
    assert f"{target.scheme}://{target.netloc}{target.path}" == body["redirect_uri"] and query["state"] == ["s-1"]
    return query["code"][0]


def _exchange(client: TestClient, client_id: str, code: str, **over):
    return client.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": client_id, "code": code,
                                             "redirect_uri": CALLBACK, "code_verifier": VERIFIER, **over})


def _refresh(client: TestClient, client_id: str, refresh_token: str):
    return client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token})


def test_discovery_names_this_server(tmp_path) -> None:
    with TestClient(_app(tmp_path, public_url="https://liberty.example.com/app/")) as client:
        base = "https://liberty.example.com/app"
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            doc = client.get(path).json()
            assert doc["resource"] == f"{base}/mcp" and doc["authorization_servers"] == [base], path
        # the three addresses a client tries — the last is the one reachable under a base path
        for path in ("/.well-known/oauth-authorization-server", "/.well-known/oauth-authorization-server/app", "/.well-known/openid-configuration"):
            doc = client.get(path).json()
            assert doc["issuer"] == base, path
            assert doc["authorization_endpoint"] == f"{base}/oauth/authorize" and doc["token_endpoint"] == f"{base}/oauth/token"
            assert doc["registration_endpoint"] == f"{base}/oauth/register"
            assert doc["code_challenge_methods_supported"] == ["S256"] and doc["token_endpoint_auth_methods_supported"] == ["none"]


def test_without_a_public_url_the_request_address_is_used(app) -> None:
    with TestClient(app, base_url="http://liberty.local:8000") as client:
        assert client.get("/.well-known/oauth-protected-resource").json()["resource"] == "http://liberty.local:8000/mcp"


def test_nothing_answers_while_the_endpoint_is_off(tmp_path) -> None:
    with TestClient(_app(tmp_path, mcp_enabled=False)) as client:
        assert client.get("/.well-known/oauth-protected-resource").status_code == 404
        assert client.get("/.well-known/oauth-authorization-server").status_code == 404
        assert client.post("/oauth/register", json={"redirect_uris": [CALLBACK]}).status_code == 404
        assert client.post("/oauth/token", data={"grant_type": "refresh_token"}).status_code == 404


def test_registration_takes_https_or_local_addresses_only(app) -> None:
    with TestClient(app) as client:
        out = client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [CALLBACK, "http://localhost:6274/cb", "http://127.0.0.1/cb"]})
        assert out.status_code == 201 and out.json()["token_endpoint_auth_method"] == "none" and out.json()["client_name"] == "Claude"
        for bad in (["http://evil.example.com/cb"], ["javascript:alert(1)"], [CALLBACK + "#frag"], [], "nope", [42]):
            r = client.post("/oauth/register", json={"redirect_uris": bad})
            assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri", bad
        assert client.post("/oauth/register", content=b"not json").json()["error"] == "invalid_client_metadata"


def test_the_whole_sign_in(app) -> None:
    with TestClient(app) as client:
        client_id = _register(client)
        bob = _login(client, "bob", "bobpw-123")

        info = client.get("/oauth/client", params={"client_id": client_id, "redirect_uri": CALLBACK}, headers=bob).json()
        # what the consent page says: who asks, where the user returns, whether changes are possible, for how long
        assert info == {"client_name": "Claude", "redirect_host": "claude.ai", "local": False, "allowed": True, "changes": False, "days": 30}
        app.state.settings.ai.mcp_changes = True
        assert client.get("/oauth/client", params={"client_id": client_id, "redirect_uri": CALLBACK}, headers=bob).json()["changes"] is True

        r = _exchange(client, client_id, _code(client, bob, client_id))
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        out = r.json()
        assert out["token_type"] == "Bearer" and out["expires_in"] == 3600 and out["scope"] == "mcp"
        # the access token is a personal access token: what /mcp reads, the user as they are now
        who = asyncio.run(principal_from_personal_token(out["access_token"], app.state.token_service, app.state.auth_backend))
        assert who.username == "bob" and set(who.permissions) == {"mcp:use", "sql:shop:orders"}
        # … and nothing else: the API refuses it, as it refuses the refresh token
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {out['access_token']}"}).status_code == 401
        assert client.post("/auth/refresh", json={"refresh_token": out["refresh_token"]}).status_code == 401

        again = _refresh(client, client_id, out["refresh_token"])
        assert again.status_code == 200, again.text
        assert again.json()["refresh_token"] != out["refresh_token"]
        asyncio.run(principal_from_personal_token(again.json()["access_token"], app.state.token_service, app.state.auth_backend))


def test_the_consent_needs_a_signed_in_user_holding_mcp_use(app) -> None:
    with TestClient(app) as client:
        client_id = _register(client)
        body = {"client_id": client_id, "redirect_uri": CALLBACK, "code_challenge": CHALLENGE}
        assert client.post("/oauth/approve", json=body).status_code == 401
        carol = _login(client, "carol", "carolpw-123")
        assert client.post("/oauth/approve", json=body, headers=carol).status_code == 403
        assert client.get("/oauth/client", params={"client_id": client_id, "redirect_uri": CALLBACK}, headers=carol).json()["allowed"] is False


def test_the_consent_refuses_what_the_client_did_not_register(app) -> None:
    with TestClient(app) as client:
        client_id = _register(client)
        bob = _login(client, "bob", "bobpw-123")
        ok = {"client_id": client_id, "redirect_uri": CALLBACK, "code_challenge": CHALLENGE}
        for over in ({"redirect_uri": "https://evil.example.com/cb"}, {"redirect_uri": CALLBACK + "/x"}, {"client_id": "forged"},
                     {"client_id": bob["Authorization"][7:]}, {"code_challenge_method": "plain"}):
            assert client.post("/oauth/approve", json={**ok, **over}, headers=bob).status_code == 400, over
        assert client.post("/oauth/approve", json={**ok, "code_challenge": "short"}, headers=bob).status_code == 422
        assert client.get("/oauth/client", params={"client_id": client_id, "redirect_uri": "https://evil.example.com/cb"}, headers=bob).status_code == 400


def test_a_code_is_bound_and_works_once(app) -> None:
    with TestClient(app) as client:
        client_id = _register(client)
        other = _register(client, name="Another")
        bob = _login(client, "bob", "bobpw-123")

        def refused(r) -> bool:
            return r.status_code == 400 and r.json()["error"] == "invalid_grant"

        assert refused(_exchange(client, client_id, _code(client, bob, client_id), code_verifier="w" * 64))      # PKCE
        assert refused(_exchange(client, client_id, _code(client, bob, client_id), code_verifier=""))
        assert refused(_exchange(client, other, _code(client, bob, client_id)))                                 # another client
        assert refused(_exchange(client, client_id, _code(client, bob, client_id), redirect_uri=CALLBACK + "/x"))
        assert refused(_exchange(client, client_id, bob["Authorization"][7:]))                                  # not a code
        code = _code(client, bob, client_id)
        assert _exchange(client, client_id, code).status_code == 200
        assert refused(_exchange(client, client_id, code))                                                      # once

        bad = _exchange(client, "forged", _code(client, bob, client_id))
        assert bad.status_code == 401 and bad.json()["error"] == "invalid_client"
        r = client.post("/oauth/token", data={"grant_type": "password", "client_id": client_id})
        assert r.status_code == 400 and r.json()["error"] == "unsupported_grant_type"


def test_a_refresh_token_rotates(app) -> None:
    with TestClient(app) as client:
        app.state.token_service.config = app.state.token_service.config.__class__(
            **{**{f: getattr(app.state.token_service.config, f) for f in app.state.token_service.config.__slots__}, "refresh_reuse_grace": 0})
        client_id = _register(client)
        other = _register(client, name="Another")
        bob = _login(client, "bob", "bobpw-123")
        first = _exchange(client, client_id, _code(client, bob, client_id)).json()["refresh_token"]
        assert _refresh(client, other, first).json()["error"] == "invalid_grant"          # another client's
        assert _refresh(client, client_id, first).status_code == 200
        assert _refresh(client, client_id, first).json()["error"] == "invalid_grant"      # used up


def test_the_account_ends_the_access(app) -> None:
    with TestClient(app) as client:
        client_id = _register(client)
        bob = _login(client, "bob", "bobpw-123")
        code = _code(client, bob, client_id)
        out = _exchange(client, client_id, _code(client, bob, client_id)).json()
        assert client.post("/auth/change-password", json={"current_password": "bobpw-123", "new_password": "bobpw-456789"}, headers=bob).status_code in (200, 204)
        assert _refresh(client, client_id, out["refresh_token"]).json()["error"] == "invalid_grant"
        assert _exchange(client, client_id, code).json()["error"] == "invalid_grant"      # a code allowed before the change


def test_the_consent_lasts_mcp_token_max_days(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, max_days=2)
    with TestClient(app) as client:
        client_id = _register(client)
        bob = _login(client, "bob", "bobpw-123")
        out = _exchange(client, client_id, _code(client, bob, client_id)).json()
        claims = app.state.token_service.decode(out["refresh_token"])
        assert 2 * 86400 - 5 <= claims["dl"] - claims["iat"] <= 2 * 86400 and claims["exp"] - claims["iat"] <= 2 * 86400
        # a rotation keeps the day the consent ends
        nxt = _refresh(client, client_id, out["refresh_token"]).json()
        assert app.state.token_service.decode(nxt["refresh_token"])["dl"] == claims["dl"]


def test_the_mcp_endpoint_points_at_the_sign_in(app) -> None:
    pytest.importorskip("mcp")
    with TestClient(app, base_url="https://liberty.example.com") as client:
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == 'Bearer resource_metadata="https://liberty.example.com/.well-known/oauth-protected-resource", scope="mcp"'
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith('Bearer error="invalid_token", resource_metadata=')


def test_the_authorize_address_is_the_applications_page(tmp_path) -> None:
    """`/oauth/authorize` is a page of the application (the consent), not an API route: next to
    the `/oauth/*` routes it must still fall to the page, with its query."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><head></head><body>the page</body></html>")
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    (tmp_path / "connectors.toml").write_text(f'[pools.default]\nurl = "{db_url}"\n')
    served = create_app(Settings(
        app=AppSettings(static_dir=str(dist)), connectors=ConnectorSettings(config_path=tmp_path / "connectors.toml"),
        auth=AuthSettings(backend="db", jwt_secret="oauth-sign-in-test-secret-0123456789abcdef", pool="default"),
        ai=AISettings(enabled=True, api_key="", config_path=tmp_path / "ai.toml", mcp_enabled=True),
    ))
    with TestClient(served, follow_redirects=False) as client:
        r = client.get("/oauth/authorize", params={"response_type": "code", "client_id": "x"})
        assert r.status_code == 200 and "the page" in r.text
