from __future__ import annotations

import json

import httpx
import pytest

from liberty.connectors.api import APIConnector, extract_path, substitute
from liberty.connectors.base import EndpointNotFoundError
from liberty.connectors.config import ApiConnectorConfig, EndpointDef, ParamDef


def _connector(cfg: ApiConnectorConfig, handler) -> APIConnector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return APIConnector("c", cfg, client=client)


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def test_substitute() -> None:
    assert substitute("/repos/{{owner}}/{{repo}}", {"owner": "a", "repo": "b"}) == "/repos/a/b"
    assert substitute("{{a}}", {"a": None}) == ""
    # Unknown placeholders are left verbatim (matches nomaubl).
    assert substitute("{{missing}}", {"other": "x"}) == "{{missing}}"
    assert substitute(None, {}) == ""


def test_extract_path() -> None:
    data = {"data": [{"id": 7}, {"id": 8}], "meta": {"count": 2}}
    assert extract_path(data, "data.0.id") == 7
    assert extract_path(data, "meta.count") == 2
    assert extract_path(data, "data.9.id") is None
    assert extract_path(data, "nope.deep") is None
    assert extract_path(data, "") is None


# --------------------------------------------------------------------------- #
# call() behaviour
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_with_placeholders_and_extraction() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"args": {"hello": "liberty"}})

    cfg = ApiConnectorConfig(
        type="api",
        base_url="https://api.test",
        endpoints=[
            EndpointDef(
                name="get",
                path="/get",
                query_params={"hello": "{{who}}"},
                params=[ParamDef(name="who", default="liberty")],
                response_field="args.hello",
            )
        ],
    )
    conn = _connector(cfg, handler)
    try:
        result = await conn.call("get")
        assert result.success is True
        assert result.status_code == 200
        assert result.extracted == "liberty"
        assert "hello=liberty" in str(seen["url"])
    finally:
        await conn.aclose()


@pytest.mark.asyncio
async def test_post_json_body_and_response_map() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(201, json={"json": body, "id": "abc"})

    cfg = ApiConnectorConfig(
        type="api",
        base_url="https://api.test",
        endpoints=[
            EndpointDef(
                name="create",
                method="POST",
                path="/items",
                body='{"message": "{{msg}}"}',
                response_map={"echo": "json.message", "newId": "id"},
            )
        ],
    )
    conn = _connector(cfg, handler)
    try:
        result = await conn.call("create", {"msg": "hi"})
        assert result.status_code == 201
        assert result.mapped == {"echo": "hi", "newId": "abc"}
    finally:
        await conn.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cfg_kwargs", "endpoint_kwargs", "header", "check"),
    [
        ({"auth_type": "bearer", "auth_token": "T"}, {}, "authorization", "Bearer T"),
        ({"auth_type": "basic", "auth_username": "u", "auth_password": "p"}, {}, "authorization", "Basic dTpw"),
        ({"auth_type": "api_key", "auth_token": "K"}, {}, "x-api-key", "K"),
        ({"auth_type": "api_key", "auth_token": "K", "auth_api_key_header": "X-Key"}, {}, "x-key", "K"),
    ],
)
async def test_auth_headers(cfg_kwargs, endpoint_kwargs, header, check) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(200, json={})

    cfg = ApiConnectorConfig(
        type="api",
        base_url="https://api.test",
        endpoints=[EndpointDef(name="e", path="/x", **endpoint_kwargs)],
        **cfg_kwargs,
    )
    conn = _connector(cfg, handler)
    try:
        await conn.call("e")
        assert captured[header] == check
    finally:
        await conn.aclose()


@pytest.mark.asyncio
async def test_oauth2_fetch_cache_and_401_refresh() -> None:
    state = {"token_calls": 0, "api_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            state["token_calls"] += 1
            return httpx.Response(200, json={"access_token": f"tok{state['token_calls']}"})
        # /data
        state["api_calls"] += 1
        auth = request.headers.get("authorization")
        if auth == "Bearer tok1" and state["api_calls"] == 1:
            return httpx.Response(401, json={"error": "expired"})
        if auth == "Bearer tok2":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(403, json={"error": "bad token", "got": auth})

    cfg = ApiConnectorConfig(
        type="api",
        base_url="https://api.test",
        auth_type="oauth2",
        auth_token_endpoint="/oauth/token",
        auth_token_field="access_token",
        endpoints=[EndpointDef(name="data", path="/data")],
    )
    conn = _connector(cfg, handler)
    try:
        # First call: fetch tok1 → 401 → refresh tok2 → 200.
        r1 = await conn.call("data")
        assert r1.success is True
        assert state["token_calls"] == 2

        # Second call: tok2 is cached and still valid → no new token fetch.
        r2 = await conn.call("data")
        assert r2.success is True
        assert state["token_calls"] == 2
    finally:
        await conn.aclose()


@pytest.mark.asyncio
async def test_non_2xx_marks_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "missing"})

    cfg = ApiConnectorConfig(
        type="api", base_url="https://api.test", endpoints=[EndpointDef(name="e", path="/x")]
    )
    conn = _connector(cfg, handler)
    try:
        result = await conn.call("e")
        assert result.success is False
        assert result.status_code == 404
        assert result.error == "HTTP 404"
        assert result.extracted is None
    finally:
        await conn.aclose()


@pytest.mark.asyncio
async def test_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    cfg = ApiConnectorConfig(
        type="api", base_url="https://api.test", endpoints=[EndpointDef(name="e", path="/x")]
    )
    conn = _connector(cfg, handler)
    try:
        result = await conn.call("e")
        assert result.success is False
        assert result.status_code == 0
        assert "ConnectError" in (result.error or "")
    finally:
        await conn.aclose()


@pytest.mark.asyncio
async def test_unknown_endpoint() -> None:
    cfg = ApiConnectorConfig(type="api", base_url="https://api.test", endpoints=[])
    conn = APIConnector("c", cfg)
    try:
        with pytest.raises(EndpointNotFoundError):
            await conn.call("nope")
    finally:
        await conn.aclose()


def test_describe_excludes_credentials() -> None:
    cfg = ApiConnectorConfig(
        type="api",
        base_url="https://api.test",
        auth_type="bearer",
        auth_token="super-secret",
        endpoints=[EndpointDef(name="e", path="/x")],
    )
    conn = APIConnector("c", cfg)
    desc = conn.describe()
    assert "super-secret" not in json.dumps(desc)
    assert desc["auth_type"] == "bearer"
    assert desc["endpoints"][0]["name"] == "e"
