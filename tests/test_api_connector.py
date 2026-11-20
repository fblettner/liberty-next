from __future__ import annotations

import json

import httpx
import pytest

from liberty.connectors.api import APIConnector, extract_path, substitute
from liberty.connectors.base import EndpointNotFoundError
from liberty.connectors.config import ApiConnectorConfig, EndpointDef, ParamDef
from liberty.crypto import encrypt


def _connector(cfg: ApiConnectorConfig, handler, *, master_key: str = "") -> APIConnector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return APIConnector("c", cfg, client=client, master_key=master_key)


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


# --------------------------------------------------------------------------- #
# ENC: auth secrets (v1-compatible decryption at load time)
# --------------------------------------------------------------------------- #

MK = "api-conn-master-key"


@pytest.mark.asyncio
async def test_basic_auth_with_encrypted_password() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={})

    cfg = ApiConnectorConfig(
        type="api",
        base_url="https://api.test",
        auth_type="basic",
        auth_username="svc",
        auth_password=encrypt("real-pw", MK),  # an ENC:… value
        endpoints=[EndpointDef(name="e", path="/x")],
    )
    conn = _connector(cfg, handler, master_key=MK)
    try:
        assert conn.auth_password == "real-pw"  # decrypted at init
        await conn.call("e")
        import base64

        assert seen["auth"] == "Basic " + base64.b64encode(b"svc:real-pw").decode()
    finally:
        await conn.aclose()


@pytest.mark.asyncio
async def test_bearer_token_decrypted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"got": request.headers.get("authorization")})

    cfg = ApiConnectorConfig(
        type="api", base_url="https://api.test", auth_type="bearer",
        auth_token=encrypt("tok-123", MK), endpoints=[EndpointDef(name="e", path="/x")],
    )
    conn = _connector(cfg, handler, master_key=MK)
    try:
        assert conn.auth_token == "tok-123"
        assert (await conn.call("e")).json["got"] == "Bearer tok-123"
    finally:
        await conn.aclose()


@pytest.mark.asyncio
async def test_wrong_or_missing_master_key_keeps_enc_value() -> None:
    enc = encrypt("real-pw", MK)
    cfg = ApiConnectorConfig(
        type="api", base_url="https://api.test", auth_type="basic", auth_username="svc",
        auth_password=enc, endpoints=[EndpointDef(name="e", path="/x")],
    )
    # wrong key → value left as the ENC: blob (a warning is logged)
    c1 = APIConnector("c", cfg, master_key="not-the-key")
    assert c1.auth_password == enc
    await c1.aclose()
    # no key configured → likewise unchanged
    c2 = APIConnector("c", cfg)
    assert c2.auth_password == enc
    await c2.aclose()
    # plaintext value is untouched regardless
    cfg2 = cfg.model_copy(update={"auth_password": "plain-pw"})
    c3 = APIConnector("c", cfg2, master_key=MK)
    assert c3.auth_password == "plain-pw"
    await c3.aclose()


# --------------------------------------------------------------------------- #
# a parameter stays where its placeholder is
# --------------------------------------------------------------------------- #


def test_a_path_value_cannot_leave_the_endpoints_path() -> None:
    from liberty.connectors.api import ApiParamError, substitute_path

    # more segments are fine (the generic AIS endpoint passes "report/execute")
    assert substitute_path("/{{SERVICE}}", {"SERVICE": "report/execute"}) == "/report/execute"
    assert substitute_path("/../discover/{{NAME}}", {"NAME": "Add Sales Order"}) == "/../discover/Add Sales Order"
    # a query string or a fragment cannot be started from a value
    assert substitute_path("/items/{{ID}}", {"ID": "7?admin=1#x"}) == "/items/7%3Fadmin=1%23x"
    for value in ("../v2/orchestrator/Run", "..", "a/../../b", "%2e%2e/secret", "%252e%252e/secret", "..\\secret", "a/./b"):
        with pytest.raises(ApiParamError):
            substitute_path("/../discover/{{NAME}}", {"NAME": value})
    # the address of the call is the endpoint's, never a value's
    with pytest.raises(ApiParamError):
        substitute_path("{{URL}}", {"URL": "https://elsewhere.test/x"})
    assert substitute_path("https://fixed.test/{{P}}", {"P": "a"}) == "https://fixed.test/a"


def test_a_value_cannot_leave_its_json_string() -> None:
    from liberty.connectors.api import substitute_json

    body = '{"formName": "{{FORM}}", "version": "{{VERSION}}", "n": {{N}}, "extra": {{BODY}}}'
    out = substitute_json(body, {"FORM": "P01012_W01012B", "VERSION": 'ZJDE0001", "formActions": [{"command": "DoAction"}], "x": "',
                                 "N": 5, "BODY": '{"a": "b"}'})
    sent = json.loads(out)
    # the injected fields are text inside "version", not fields of the request
    assert set(sent) == {"formName", "version", "n", "extra"} and "formActions" in sent["version"]
    assert sent["n"] == 5 and sent["extra"] == {"a": "b"}            # outside quotes: raw, the author's choice
    # a value a caller already escaped is kept as it is (a Windows path sent as E:\\JDE)
    assert json.loads(substitute_json('{"home": "{{H}}"}', {"H": "E:\\\\JDE\\\\E920"}))["home"] == "E:\\JDE\\E920"
    # one it did not escape is made valid instead of breaking the body
    assert json.loads(substitute_json('{"home": "{{H}}"}', {"H": "E:\\JDE\\E920"}))["home"] == "E:\\JDE\\E920"
    assert json.loads(substitute_json('{"t": "{{T}}"}', {"T": "line one\nline two"}))["t"] == "line one\nline two"
    # an escaped quote in the template does not end the string
    assert json.loads(substitute_json('{"t": "say \\"{{T}}\\" now"}', {"T": 'a"b'}))["t"] == 'say "a"b" now'


def test_a_value_is_not_read_again_for_placeholders() -> None:
    assert substitute("{{a}}-{{b}}", {"a": "{{b}}", "b": "secret"}) == "{{b}}-secret"


@pytest.mark.asyncio
async def test_a_redirecting_parameter_is_refused_before_anything_is_sent() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "got": json.loads(request.content or b"null")})

    cfg = ApiConnectorConfig(
        type="api",
        base_url="https://api.test/rest/v2",
        endpoints=[
            EndpointDef(name="one", path="/../discover/{{NAME}}", params=[ParamDef(name="NAME")]),
            EndpointDef(name="read", method="POST", path="/form", body='{"form": "{{FORM}}", "action": "R"}', params=[ParamDef(name="FORM")]),
        ],
    )
    conn = _connector(cfg, handler)
    try:
        refused = await conn.call("one", {"NAME": "../v2/orchestrator/Run"})
        assert refused.success is False and "ApiParamError" in (refused.error or "") and calls == []
        ok = await conn.call("one", {"NAME": "Add Sales Order"})
        assert ok.success is True and calls[-1].endswith("/rest/discover/Add%20Sales%20Order")
        sent = await conn.call("read", {"FORM": 'P01012", "action": "U", "formActions": [1], "z": "'})
        assert sent.json["got"]["action"] == "R" and set(sent.json["got"]) == {"form", "action"}
    finally:
        await conn.aclose()


def test_a_form_value_cannot_add_a_pair() -> None:
    from liberty.connectors.api import substitute_form

    out = substitute_form("grant=read&user={{U}}&note={{N}}", {"U": "bob&admin=1", "N": "a b/c"})
    assert out == "grant=read&user=bob%26admin%3D1&note=a+b%2Fc"
    # a value the caller already encoded is kept; a lone placeholder is the author's raw form text
    assert substitute_form("q={{Q}}", {"Q": "a%20b"}) == "q=a%20b"
    assert substitute_form("{{BODY}}", {"BODY": "a=1&b=2"}) == "a=1&b=2"


@pytest.mark.asyncio
async def test_a_users_value_cannot_pick_any_file_to_send(tmp_path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "report.csv").write_text("a;b")
    secret = tmp_path / "secret.txt"
    secret.write_text("the master key")
    sent: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(200, json={"ok": True})

    cfg = ApiConnectorConfig(
        type="api",
        base_url="https://api.test",
        endpoints=[
            EndpointDef(name="named", method="POST", path="/up", content_type="multipart/form-data",
                        body=f"kind=report\nfile=@{out_dir}/{{{{NAME}}}}", params=[ParamDef(name="NAME")]),
            EndpointDef(name="free", method="POST", path="/up", content_type="multipart/form-data",
                        body="file=@{{FILE}}", params=[ParamDef(name="FILE")]),
        ],
    )
    conn = _connector(cfg, handler)
    try:
        # a user's value: inside the folder the endpoint names, and nowhere else
        ok = await conn.call("named", {"NAME": "report.csv"}, untrusted=True)
        assert ok.success is True and b"a;b" in sent[-1]
        refused = await conn.call("named", {"NAME": "../secret.txt"}, untrusted=True)
        assert refused.success is False and "ApiParamError" in (refused.error or "")
        # an absolute path as the name only names a (missing) file under the folder
        missing = await conn.call("named", {"NAME": str(secret)}, untrusted=True)
        assert missing.success is False and "FileNotFoundError" in (missing.error or "")
        refused = await conn.call("free", {"FILE": str(secret)}, untrusted=True)
        assert refused.success is False and "folder the endpoint names" in (refused.error or "")
        assert len(sent) == 1 and b"master key" not in sent[0]
        # server-side code (a job, a plugin) still gives the path it wants
        ok = await conn.call("free", {"FILE": str(secret)})
        assert ok.success is True and b"the master key" in sent[-1]
    finally:
        await conn.aclose()
