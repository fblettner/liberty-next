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
        # … said with the server it could not reach and where its address is set
        assert (result.error or "").startswith("cannot connect to ") and "check the connector's base URL" in result.error
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


@pytest.mark.asyncio
async def test_a_parameter_says_what_it_expects_and_is_blank_when_left_out() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    cfg = ApiConnectorConfig(
        type="api",
        base_url="https://api.test",
        endpoints=[EndpointDef(
            name="find", method="POST", path="/find", body='{"name": "{{NAME}}", "object": "{{OBJECT}}"}',
            params=[ParamDef(name="NAME", label="Function", help="The function's name", example="RetrieveCompanyFromBusUnit"),
                    ParamDef(name="OBJECT")],
        )],
    )
    conn = _connector(cfg, handler)
    try:
        described = conn.describe()["endpoints"][0]["params"]
        assert described[0]["help"] == "The function's name" and described[0]["example"] == "RetrieveCompanyFromBusUnit"
        assert described[1]["help"] is None and described[1]["example"] is None
        await conn.call("find", {"NAME": "X"})
        assert seen["body"] == {"name": "X", "object": ""}        # not the text "{{OBJECT}}"
    finally:
        await conn.aclose()


@pytest.mark.asyncio
async def test_a_parameter_with_choices_is_checked_and_a_blank_one_takes_its_default() -> None:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    cfg = ApiConnectorConfig(
        type="api",
        base_url="https://api.test",
        endpoints=[EndpointDef(
            name="query", method="POST", path="/data", body='{"target": "{{TABLE}}", "type": "{{TYPE}}", "where": {{CONDITIONS}}}',
            params=[ParamDef(name="TABLE"), ParamDef(name="TYPE", default="table", choices=["table", "view"]),
                    ParamDef(name="CONDITIONS", default="[]")],
        )],
    )
    conn = _connector(cfg, handler)
    try:
        assert conn.describe()["endpoints"][0]["params"][1]["choices"] == ["table", "view"]
        await conn.call("query", {"TABLE": "V0010D", "TYPE": "VIEW"})                     # typed in capitals
        await conn.call("query", {"TABLE": "F0010", "TYPE": "", "CONDITIONS": ""})          # cleared fields
        assert sent == [{"target": "V0010D", "type": "view", "where": []}, {"target": "F0010", "type": "table", "where": []}]
        refused = await conn.call("query", {"TABLE": "F0010", "TYPE": "bsvw"})
        assert refused.success is False and "is not one of table, view" in (refused.error or "") and len(sent) == 2
    finally:
        await conn.aclose()


@pytest.mark.asyncio
async def test_errors_inside_a_200_answer_make_the_call_a_failure() -> None:
    answers = [
        {"fs_F": {"errors": [{"TITLE": "Error: Alpha Name Cannot Be Blank", "CONTROLTITLE": "Alpha Name", "DESC": "long text"},
                             {"TITLE": "Error: Blanks Invalid", "CONTROLTITLE": "Alpha Name"}], "warnings": []}, "sysErrors": []},
        {"fs_F": {"errors": [], "warnings": [{"TITLE": "only a warning"}]}, "sysErrors": []},
        {"fs_F": {"errors": []}, "sysErrors": ["session lost"]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=answers.pop(0))

    cfg = ApiConnectorConfig(type="api", base_url="https://api.test", endpoints=[
        EndpointDef(name="save", method="POST", path="/form", body="{}", error_fields=["fs_F.errors", "sysErrors"], response_field="fs_F.errors"),
    ])
    conn = _connector(cfg, handler)
    try:
        refused = await conn.call("save")
        assert refused.success is False and refused.status_code == 200
        # the field is named once: not repeated when the message already says it
        assert refused.error == "Error: Alpha Name Cannot Be Blank; Error: Blanks Invalid (Alpha Name)"
        assert refused.json["fs_F"]["warnings"] == []                 # the whole answer is still there
        assert (await conn.call("save")).success is True               # a warning is not an error field here
        lost = await conn.call("save")
        assert lost.success is False and lost.error == "session lost"
        assert conn.describe()["endpoints"][0]["error_fields"] == ["fs_F.errors", "sysErrors"]
        # a heading in the editor: carried as it is, nothing else reads it
        assert EndpointDef(name="x", group="P01012 — Address Book").group == "P01012 — Address Book"
        assert conn.describe()["endpoints"][0]["group"] is None
    finally:
        await conn.aclose()


@pytest.mark.asyncio
async def test_a_warning_is_reported_and_does_not_fail_the_call() -> None:
    """A server that puts errors and warnings in one list, told apart by a field (JD Edwards'
    business functions: errorLevel 1 = an error, 2 = a warning)."""
    answers = [
        {"result": {"output": [], "errors": [{"alphaDescription": "Warning: Duplicate Address Book record exists", "errorLevel": 2}]}},
        {"result": {"errors": [{"alphaDescription": "Error: Address Number - Invalid", "errorLevel": 1},
                               {"alphaDescription": "Warning: Duplicate Address Book record exists", "errorLevel": 2}]}},
        {"result": {"output": [{"id": 1, "value": "A"}], "errors": []}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=answers.pop(0))

    cfg = ApiConnectorConfig(type="api", base_url="https://api.test", endpoints=[
        EndpointDef(name="run", method="POST", path="/bsfn", body="{}",
                    error_fields=["result.errors[errorLevel=1]", "sysErrors"], warning_fields=["result.errors[errorLevel=2]"]),
    ])
    conn = _connector(cfg, handler)
    try:
        warned = await conn.call("run")
        assert warned.success is True and warned.error is None and warned.warnings == "Warning: Duplicate Address Book record exists"
        assert warned.to_dict()["warnings"] == warned.warnings
        failed = await conn.call("run")
        assert failed.success is False and failed.error == "Error: Address Number - Invalid"
        assert failed.warnings == "Warning: Duplicate Address Book record exists"       # both are said
        clean = await conn.call("run")
        assert clean.success is True and clean.warnings is None
    finally:
        await conn.aclose()


@pytest.mark.asyncio
async def test_a_blank_query_parameter_is_left_out_when_the_endpoint_says_so() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.query, "ascii") if isinstance(request.url.query, bytes) else str(request.url.query))
        return httpx.Response(200, json={})

    cfg = ApiConnectorConfig(type="api", base_url="https://sm.test", endpoints=[
        EndpointDef(name="state", path="/instancestate", query_params={"instanceName": "{{NAME}}", "jdeHome": "{{HOME}}"},
                    params=[ParamDef(name="NAME"), ParamDef(name="HOME")], skip_blank_query=True),
        EndpointDef(name="as_before", path="/x", query_params={"a": "{{NAME}}", "b": "{{HOME}}"}, params=[ParamDef(name="NAME"), ParamDef(name="HOME")])])
    conn = APIConnector("sm", cfg, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await conn.call("state", {"NAME": "AIS1"})
    await conn.call("as_before", {"NAME": "AIS1"})
    assert seen == ["instanceName=AIS1", "a=AIS1&b="]


@pytest.mark.asyncio
async def test_a_declared_parameter_is_not_the_connectors_credential() -> None:
    """``{{username}}`` / ``{{password}}`` fill a token request with the connector's own login — but an
    endpoint that DECLARES a ``username`` parameter (a user filter) owns the name: left blank it is
    blank. Seen on Server Manager: "who is connected" was asked for the console's admin, and answered
    "no active users"."""
    sent: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.path, dict(request.url.params), request.content.decode()))
        return httpx.Response(200, json={})

    cfg = ApiConnectorConfig(type="api", base_url="https://api.test", auth_type="basic", auth_username="svc_admin", auth_password="s3cret", endpoints=[
        EndpointDef(name="users", path="/users", skip_blank_query=True, query_params={"username": "{{username}}"}, params=[ParamDef(name="username")]),
        EndpointDef(name="create", method="POST", path="/create", body='{"name": "{{NAME}}", "password": "{{password}}"}',
                    params=[ParamDef(name="NAME"), ParamDef(name="password")]),
        EndpointDef(name="token", method="POST", path="/token", body='{"username": "{{username}}", "password": "{{password}}"}'),
    ])
    conn = _connector(cfg, handler)
    try:
        await conn.call("users")
        assert sent[-1][:2] == ("/users", {})                                    # the filter left blank: not the connector's login
        await conn.call("users", {"username": "JDE"})
        assert sent[-1][1] == {"username": "JDE"}
        await conn.call("create", {"NAME": "x"})
        assert json.loads(sent[-1][2]) == {"name": "x", "password": ""}          # never the connector's password
        await conn.call("token")
        assert json.loads(sent[-1][2]) == {"username": "svc_admin", "password": "s3cret"}   # undeclared: the built-ins, as before
    finally:
        await conn.aclose()


@pytest.mark.asyncio
async def test_a_delete_sends_the_body_its_endpoint_declares() -> None:
    seen: list[tuple[str, str, bytes, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.content, request.headers.get("content-type")))
        return httpx.Response(200, json={"ok": True})

    cfg = ApiConnectorConfig.model_validate({"type": "api", "base_url": "https://sm.example.com/manage", "endpoints": [
        {"name": "clear", "method": "DELETE", "path": "/clearcache", "writable": True,
         "body": '{"instanceName": "{{instanceName}}", "cache": "{{cache}}"}', "params": [{"name": "instanceName"}, {"name": "cache"}]},
        {"name": "drop", "method": "DELETE", "path": "/things/{{id}}", "writable": True, "params": [{"name": "id"}]},
        {"name": "read", "method": "GET", "path": "/things", "body": '{"ignored": true}'},
    ]})
    conn = _connector(cfg, handler)
    assert (await conn.call("clear", {"instanceName": "html_1", "cache": "ALL"})).success
    assert seen[0][0] == "DELETE" and json.loads(seen[0][2]) == {"instanceName": "html_1", "cache": "ALL"} and seen[0][3] == "application/json"
    # a DELETE without a declared body stays without one; a GET never carries one
    await conn.call("drop", {"id": "7"})
    await conn.call("read", {})
    assert seen[1][2] == b"" and seen[2][2] == b""


# --------------------------------------------------------------------------- #
# a call made as the signed-in user: a signed token per user (JWT)
# --------------------------------------------------------------------------- #


def _rsa_pem() -> tuple[str, object]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return pem, key.public_key()


@pytest.mark.asyncio
async def test_a_call_is_made_as_the_signed_in_user(tmp_path) -> None:
    import base64

    import jwt as pyjwt

    from liberty.auth.principal import Principal, current_principal
    pem, public = _rsa_pem()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={})

    def cfg(**over) -> ApiConnectorConfig:
        base = dict(type="api", base_url="https://ais.test", auth_type="user_jwt", auth_jwt_key=encrypt(pem.replace("\n", "\\n"), MK),
                    auth_jwt_issuer="liberty", endpoints=[EndpointDef(name="e", path="/x")])
        return ApiConnectorConfig(**{**base, **over})

    def claims(header: str) -> dict:
        assert header.startswith("Bearer ")
        return pyjwt.decode(header[7:], public, algorithms=["RS256"], options={"verify_aud": False})

    ann = Principal(id="1", username="ann", attributes={"jde_user": "ab12345"})
    bob = Principal(id="2", username="bob")                       # no JDE user on his account
    conn = _connector(cfg(), handler, master_key=MK)
    token = current_principal.set(ann)
    try:
        # the signed-in user's attribute, upper-cased, in the claim the server reads; short-lived; signed by the key
        assert (await conn.call("e")).success
        c = claims(seen[-1])
        assert c["sub"] == "AB12345" and c["iss"] == "liberty" and 0 < c["exp"] - c["iat"] <= 300
        await conn.call("e")
        assert seen[-1] == seen[-2]                                # kept until it is about to expire
        # a user without the attribute: refused, nothing sent
        current_principal.set(bob)
        sent = len(seen)
        out = await conn.call("e")
        assert not out.success and out.status_code == 0 and "no 'jde_user' attribute" in out.error and len(seen) == sent
        # nobody signed in (a job): refused too, and said so
        current_principal.set(None)
        out = await conn.call("e")
        assert not out.success and "nobody is signed in" in out.error and len(seen) == sent
    finally:
        current_principal.reset(token)
        await conn.aclose()

    # the service account when no user can be named — and still the user's own token when one can
    conn = _connector(cfg(auth_jwt_fallback="service", auth_username="svc", auth_password="pw", auth_jwt_claim="prn", auth_jwt_audience="jde",
                          auth_jwt_uppercase=False, auth_jwt_user_attribute="", auth_jwt_ttl=60), handler, master_key=MK)
    token = current_principal.set(None)
    try:
        await conn.call("e")
        assert seen[-1] == "Basic " + base64.b64encode(b"svc:pw").decode()
        current_principal.set(bob)                                 # a blank attribute setting: the Liberty name itself, as written
        await conn.call("e")
        c = claims(seen[-1])
        assert c["prn"] == "bob" and c["aud"] == "jde" and c["exp"] - c["iat"] == 60 and "sub" not in c
    finally:
        current_principal.reset(token)
        await conn.aclose()

    # the key as a file; a missing or unreadable key is said, nothing sent
    key_file = tmp_path / "liberty-jde.key"
    key_file.write_text(pem)
    for key, expected in ((str(key_file), None), ("", "no key to sign"), (str(tmp_path / "absent.key"), "cannot be read"), ("-----BEGIN PRIVATE KEY-----\\nnope", "not a private key")):
        conn = _connector(cfg(auth_jwt_key=key or None), handler, master_key=MK)
        token = current_principal.set(ann)
        try:
            out = await conn.call("e")
            assert (out.success, expected is None) in ((True, True), (False, False)) and (expected is None or expected in out.error)
        finally:
            current_principal.reset(token)
            await conn.aclose()
    assert claims(seen[-1])["sub"] == "AB12345"

    # each token has its own id; a single-use one says so and is never kept; the certificate's fingerprint goes in the header
    import datetime as dt
    import hashlib

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import NameOID
    private = serialization.load_pem_private_key(pem.encode(), password=None)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "liberty")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(public).serial_number(1)
            .not_valid_before(dt.datetime(2026, 1, 1)).not_valid_after(dt.datetime(2036, 1, 1)).sign(private, hashes.SHA256()))
    cer = tmp_path / "liberty-jde.cer"
    cer.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    conn = _connector(cfg(auth_jwt_certificate=str(cer)), handler, master_key=MK)
    try:
        one, again, once, twice = conn.user_token("AB12345"), conn.user_token("AB12345"), conn.user_token("AB12345", single_use=True), conn.user_token("AB12345", single_use=True)
        assert one == again and once != twice != one
        assert claims("Bearer " + once)["at_use_nbr"] == 1 and "at_use_nbr" not in claims("Bearer " + one)
        assert len({claims("Bearer " + x)["jti"] for x in (one, once, twice)}) == 3
        expected = base64.urlsafe_b64encode(hashlib.sha1(cert.public_bytes(serialization.Encoding.DER)).digest()).rstrip(b"=").decode()
        assert pyjwt.get_unverified_header(one)["x5t"] == expected
    finally:
        await conn.aclose()
    bad = _connector(cfg(auth_jwt_certificate=str(tmp_path / "absent.cer")), handler, master_key=MK)
    token = current_principal.set(ann)
    try:
        out = await bad.call("e")
        assert not out.success and "auth_jwt_certificate cannot be read" in out.error
    finally:
        current_principal.reset(token)
        await bad.aclose()


def test_a_parameter_names_a_dictionary_lookup() -> None:
    """A declared parameter that names a lookup is described with it — the reference, how its list is shown, what
    narrows it — for the browser to offer the list where the parameter is filled."""
    from types import SimpleNamespace

    from liberty.connectors.config import ParamDef
    from liberty.connectors.dictionary import DictionaryFile

    dictionary = DictionaryFile.model_validate({
        "lookups": {"shared": {"connector": "jde", "query": "roles_get", "value": "ROLE", "label": "NAME"}},
        "connectors": {"jde": {"lookups": {"udos": {"query": "udos_get", "value": "UDO", "label": "NAME", "display_fields": ["KIND"], "filter_fields": ["KIND"]}}}},
    })
    plain = ParamDef(name="WHO", lookup="shared")
    assert dictionary.param_lookup(plain, "api_x") == {"kind": "lookup", "connector": "jde", "query": "roles_get", "value": "ROLE", "label": "NAME"}
    scoped = ParamDef(name="UDO", lookup="udos", lookup_connector="jde", lookup_binds={"KIND": "CATEGORY"})
    assert dictionary.param_lookup(scoped, "api_x") == {"kind": "lookup", "connector": "jde", "query": "udos_get", "value": "UDO", "label": "NAME",
                                                        "display_fields": ["KIND"], "filter_fields": ["KIND"], "binds": {"KIND": "CATEGORY"}}
    assert dictionary.param_lookup(ParamDef(name="X"), "api_x") is None
    assert dictionary.param_lookup(ParamDef(name="X", lookup="unknown"), "api_x") is None      # a name the dictionary does not hold: typed, as before
    # …and an API connector says it with each parameter
    connector = APIConnector("api_x", ApiConnectorConfig.model_validate({"type": "api", "base_url": "http://x", "endpoints": [
        {"name": "e", "path": "/e", "params": [{"name": "WHO", "lookup": "shared"}, {"name": "N"}]}]}))
    connector.registry = SimpleNamespace(dictionary=dictionary)
    params = connector.describe()["endpoints"][0]["params"]
    assert params[0]["lookup"]["query"] == "roles_get" and params[1]["lookup"] is None
