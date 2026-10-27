"""describe_instance / list_playbooks / load_playbook read app.state at call time; the tool
result cap keeps a huge result from flooding the context."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from liberty.ai.appconfig import parse_ai_config
from liberty.ai.assistant import TOOL_RESULT_MAX_CHARS, AiAssistant, build_assistant
from liberty.ai.instance_tools import build_instance_tools
from liberty.ai.tools import ToolRegistry, tool
from liberty.config import AISettings
from liberty.connectors.config import ConnectorsFile
from liberty.connectors.registry import ConnectorRegistry
from liberty.dashboards.config import parse_dashboards
from liberty.jobs.schema import Job, Step, StepType
from liberty.menus.config import AppMenu, MenuItem, MenusFile
from liberty.screens.config import Screen, ScreensFile

AI = parse_ai_config({"ai": {
    "nomajde": {"starters": ["x"], "playbooks": {
        "create_user": {"title": "Create a JDE user", "description": "User + role + environments", "body": "1. Open Users.\n2. Add…"},
        "dup_role": {"title": "Duplicate a role", "body": "1. …"},
    }},
    "nomasx1": {"playbooks": {"sox": {"title": "Run the SoD audit", "body": "…"}}},
}})


def _state(ai=AI):
    reg = ConnectorRegistry(ConnectorsFile.model_validate({"pools": {}, "connectors": {
        "nomajde": {"type": "sql", "pool": "p"}, "nomasx1": {"type": "sql", "pool": "p"}}}))
    screens = ScreensFile(screens={"nomajde": {"f0092": Screen(id="f0092", app="nomajde", label="Users", read_query="f0092_get")}})
    menus = MenusFile(menus={"nomajde": AppMenu(label="JDE administration", items=[MenuItem(id="users", label="Users", type="screen", target="f0092"), MenuItem(id="sec", label="Security")])})
    dashboards = parse_dashboards({"dashboards": {"nomasx1": {"overview": {"label": "Security Overview", "widgets": []}}}})
    registry = SimpleNamespace(jobs=lambda: [Job(id="nightly", description="Collect", schedule="0 2 * * *", steps=[Step(type=StepType.SQL_QUERY, name="collect", connector="nomasx1", query="q")])])
    return SimpleNamespace(connectors=reg, screens=screens, menus=menus, dashboards=dashboards, explorer=None, jobs=SimpleNamespace(registry=registry), ai_config=ai)


def _tools(**kw):
    st = _state(**kw)
    return {t.name: t for t in build_instance_tools(lambda: st)}


@pytest.mark.asyncio
async def test_describe_instance_summarises_the_state() -> None:
    out = await _tools()["describe_instance"].run({})
    nomajde = next(a for a in out["apps"] if a["id"] == "nomajde")
    assert nomajde["label"] == "JDE administration" and nomajde["screens"] == [{"id": "f0092", "label": "Users"}]
    assert nomajde["menu"] == [{"id": "users", "label": "Users", "type": "screen", "target": "f0092"}, {"id": "sec", "label": "Security"}]
    assert out["dashboards"] == [{"id": "nomasx1.overview", "label": "Security Overview"}]
    assert out["jobs"] == [{"id": "nightly", "description": "Collect", "schedule": "0 2 * * *", "enabled": True, "steps": ["collect"]}]
    assert [p["id"] for p in out["playbooks"]] == ["create_user", "dup_role", "sox"]
    assert "explorer_models" not in out


@pytest.mark.asyncio
async def test_playbooks_are_listed_and_loaded() -> None:
    tools = _tools()
    listed = await tools["list_playbooks"].run({})
    assert listed[0] == {"app": "nomajde", "id": "create_user", "title": "Create a JDE user", "description": "User + role + environments"}
    assert await tools["load_playbook"].run({"app": "nomajde", "id": "create_user"}) == {"app": "nomajde", "id": "create_user", "title": "Create a JDE user", "body": "1. Open Users.\n2. Add…"}
    with pytest.raises(KeyError, match="list_playbooks"):
        await tools["load_playbook"].run({"app": "nomajde", "id": "ghost"})
    assert tools["load_playbook"].summarize({"app": "nomajde", "id": "create_user"}) == "app=nomajde id=create_user"
    # the allowed-connectors restriction applies
    st = _state()
    only = {t.name: t for t in build_instance_tools(lambda: st, allowed=["nomasx1"])}
    assert [p["app"] for p in await only["list_playbooks"].run({})] == ["nomasx1"]
    assert [a["id"] for a in (await only["describe_instance"].run({}))["apps"]] == ["nomasx1"]
    # no ai.toml at all
    none = {t.name: t for t in build_instance_tools(lambda: SimpleNamespace())}
    assert await none["list_playbooks"].run({}) == [] and await none["describe_instance"].run({}) == {"apps": []}


def test_build_assistant_adds_the_instance_tools_when_given_the_state() -> None:
    reg = ConnectorRegistry(ConnectorsFile())
    a = build_assistant(AISettings(api_key=""), reg, master_key="")
    assert "describe_instance" not in a.tools
    a = build_assistant(AISettings(api_key=""), reg, master_key="", state=lambda: SimpleNamespace())
    assert {"describe_instance", "list_playbooks", "load_playbook"} <= set(a.tools.names())


@pytest.mark.asyncio
async def test_tool_results_are_capped_with_a_narrow_it_note() -> None:
    from tests.test_ai_loop import FakeAnthropic, _msg, _text_block, _text_delta, _tool_block

    @tool
    def huge() -> str:
        return "x" * (TOOL_RESULT_MAX_CHARS * 2)

    turns = [([], _msg([_tool_block("t1", "huge", {})], "tool_use")), ([_text_delta("ok")], _msg([_text_block("ok")], "end_turn"))]
    client = FakeAnthropic(turns)
    a = AiAssistant(AISettings(api_key="x"), client=client, tools=ToolRegistry().add(huge))
    events = [e async for e in a.chat([{"role": "user", "content": "go"}])]
    assert events[-1].type == "done"
    sent = client.messages.calls[1]["messages"][2]["content"][0]["content"]
    assert len(sent) < TOOL_RESULT_MAX_CHARS + 300 and sent.startswith("x" * 100) and "Narrow" in sent and str(TOOL_RESULT_MAX_CHARS * 2) in sent
    assert json.loads("1") == 1
