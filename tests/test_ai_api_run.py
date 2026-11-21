"""An API endpoint that changes data and the assistant: ``api_call`` refuses it, ``api_run``
proposes a card in the chat and calls it over MCP, with the endpoint's own permission."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from liberty.ai.connector_tools import build_connector_tools
from liberty.ai.tools import ToolRegistry, current_channel
from liberty.ai.write_tools import build_write_tools, perform_api_run
from liberty.auth.principal import Principal, current_principal
from liberty.connectors.api import APIConnector
from liberty.connectors.config import ApiConnectorConfig, EndpointDef, ParamDef
from liberty.screens.save import ScreenWriteError

CFG = ApiConnectorConfig(
    type="api",
    base_url="https://erp.test/rest",
    endpoints=[
        EndpointDef(name="status", method="POST", path="/status", body='{"job": "{{JOB}}"}', params=[ParamDef(name="JOB")]),
        EndpointDef(name="submit", method="POST", path="/submit", writable=True, label="Submit a report",
                    body='{"report": "{{REPORT}}", "kind": "{{KIND}}"}',
                    params=[ParamDef(name="REPORT"), ParamDef(name="KIND", default="full", choices=["full", "short"])]),
    ],
)


class Registry:
    """Just what the tools need of a ConnectorRegistry: one API connector named ``erp``."""

    def __init__(self, sent: list) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            sent.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"jobNumber": 7})

        self.conn = APIConnector("erp", CFG, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    def api(self, name: str) -> APIConnector:
        from liberty.connectors.base import UnknownConnectorError
        if name != "erp":
            raise UnknownConnectorError(name)
        return self.conn

    def get(self, name: str) -> APIConnector:
        return self.api(name)

    def describe(self) -> list[dict]:
        return [self.conn.describe()]

    def names(self) -> list[str]:
        return ["erp"]


def _state(sent: list, allowed: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(connectors=Registry(sent), screens=None, changesets_db=None,
                           settings=SimpleNamespace(ai=SimpleNamespace(allowed_connectors=allowed or [])))


def _user(*permissions: str) -> Principal:
    return Principal(id="1", username="ann", permissions=tuple(permissions))


@pytest.mark.asyncio
async def test_the_read_tool_refuses_an_endpoint_that_changes_data() -> None:
    sent: list = []
    state = _state(sent)
    token = current_principal.set(_user("api:erp:*"))
    try:
        tools = ToolRegistry().add(*build_connector_tools(state.connectors, include_api=True))
        listing = json.loads((await tools.execute("list_connectors", {"connector": "erp"}))[0])
        by_name = {e["name"]: e for e in listing["connectors"][0]["endpoints"]}
        assert by_name["submit"]["changes_data"] is True and "changes_data" not in by_name["status"]
        assert by_name["submit"]["params"][1] == {"name": "KIND", "default": "full", "choices": ["full", "short"]}
        content, is_error = await tools.execute("api_call", {"connector": "erp", "endpoint": "submit", "params": {"REPORT": "R1"}})
        assert is_error and "api_run" in content and sent == []
        content, is_error = await tools.execute("api_call", {"connector": "erp", "endpoint": "status", "params": {"JOB": "7"}})
        assert not is_error and sent == [("/rest/status", {"job": "7"})]
    finally:
        current_principal.reset(token)


@pytest.mark.asyncio
async def test_api_run_checks_first_and_calls_as_the_endpoints_route_does() -> None:
    sent: list = []
    state = _state(sent)
    ann = _user("api:erp:submit")
    checked = await perform_api_run(state, ann, connector="erp", endpoint="submit", params={"REPORT": "R1", "KIND": "SHORT"}, dry_run=True)
    assert checked["called"] is False and checked["params"] == {"REPORT": "R1", "KIND": "short"} and sent == []
    done = await perform_api_run(state, ann, connector="erp", endpoint="submit", params={"REPORT": "R1"})
    assert done["called"] is True and done["success"] is True and done["result"] == {"jobNumber": 7}
    assert sent == [("/rest/submit", {"report": "R1", "kind": "full"})]
    for kwargs, reason in (
        ({"endpoint": "status"}, "only reads"),
        ({"endpoint": "submit", "params": {"REPORT": "R1", "KIND": "huge"}}, "is not one of full, short"),
        ({"endpoint": "submit", "params": {"REPORT": "R1", "EXTRA": "x"}}, "not a parameter"),
    ):
        with pytest.raises(ScreenWriteError, match=reason):
            await perform_api_run(state, _user("api:erp:*"), connector="erp", **kwargs)
    # no permission, a connector the assistant may not use, a name that does not exist: one answer
    for who, st, connector, endpoint in ((_user("api:erp:status"), state, "erp", "submit"), (_user("*"), _state(sent, ["other"]), "erp", "submit"),
                                         (_user("api:erp:status"), state, "erp", "nope")):
        with pytest.raises(ScreenWriteError, match="permission denied"):
            await perform_api_run(st, who, connector=connector, endpoint=endpoint)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_in_the_chat_it_is_a_card_and_over_mcp_it_is_the_call() -> None:
    sent: list = []
    state = _state(sent)
    assert [t.name for t in build_write_tools(lambda: state)].count("api_run") == 0        # only with the API tool
    tools = ToolRegistry().add(*build_write_tools(lambda: state, include_api=True))
    who = current_principal.set(_user("ai:write", "api:erp:submit"))
    try:
        content, is_error = await tools.execute("api_run", {"connector": "erp", "endpoint": "submit", "params": {"REPORT": "R1"}})
        card = json.loads(content)
        assert not is_error and sent == [] and card["proposal"]["kind"] == "api_run"
        assert card["proposal"]["summary"] == "Call erp.submit (Submit a report) — REPORT=R1, KIND=full"
        assert card["proposal"]["payload"]["params"] == {"REPORT": "R1"}
        channel = current_channel.set("mcp")
        try:
            content, is_error = await tools.execute("api_run", {"connector": "erp", "endpoint": "submit", "params": {"REPORT": "R1"}})
        finally:
            current_channel.reset(channel)
        assert not is_error and json.loads(content)["called"] is True and len(sent) == 1
    finally:
        current_principal.reset(who)
    # without ai:write the tool is not theirs
    who = current_principal.set(_user("api:erp:submit"))
    try:
        content, is_error = await tools.execute("api_run", {"connector": "erp", "endpoint": "submit", "params": {"REPORT": "R1"}})
        assert is_error and "permission denied" in content and len(sent) == 1
    finally:
        current_principal.reset(who)
