"""The tools the applications ship (PLAN G2): `[ai.<app>.tools.<id>]` of ai.toml — a plugin
function each, offered as `<app>_<id>`, bounded by the plugins allow-list and the caller's
permission, the framework's objects injected and never the model's to give."""

from __future__ import annotations

import sys
import textwrap
from types import SimpleNamespace

import pytest

from liberty.ai.app_tools import app_tools_provider, build_app_tools
from liberty.ai.appconfig import parse_ai_config
from liberty.ai.assistant import build_assistant
from liberty.ai.tools import ToolRegistry
from liberty.auth.principal import Principal, current_principal
from liberty.config import AISettings

PLUGIN = '''
from liberty.jobs.steps.base import StepResult


async def j_object_access(*, connectors, object_name: str, role: str = "*ALL", limit: int = 20, **_: object) -> dict:
    """Who can run a JDE object.

    Args:
        object_name: the object, e.g. P4210.
        role: one role only.
        limit: how many lines at most.
    """
    return {"object": object_name, "role": role, "limit": limit, "apps": sorted(connectors.names()), "given": type(connectors).__name__}


def j_count(*, settings, ctx, table: str) -> StepResult:
    """Count a table (a job-style result)."""
    return StepResult(rows_affected=42, extras={"table": table, "by": ctx.trigger.triggered_by, "run": ctx.run_id})


def helper(x: str) -> str:
    return x


def j_broken(*, table: str) -> dict:
    raise RuntimeError("the database said no")
'''


@pytest.fixture
def plugins(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "__init__.py").write_text("")
    (root / "shop" / "lookup.py").write_text(textwrap.dedent(PLUGIN))
    monkeypatch.syspath_prepend(str(root))
    for mod in [m for m in sys.modules if m == "shop" or m.startswith("shop.")]:
        monkeypatch.delitem(sys.modules, mod)
    yield root
    for mod in [m for m in sys.modules if m == "shop" or m.startswith("shop.")]:
        sys.modules.pop(mod, None)


@pytest.fixture(autouse=True)
def _no_caller():
    token = current_principal.set(None)
    yield
    current_principal.reset(token)


def _cfg(tools: dict, app: str = "shop"):
    return parse_ai_config({"ai": {app: {"tools": tools}}})


def _state(cfg, apps=("shop", "other")):
    connectors = SimpleNamespace(names=lambda: list(apps))
    return SimpleNamespace(ai_config=cfg, connectors=connectors, settings=SimpleNamespace(name="settings"))


ACCESS = {"callable": "shop.lookup:j_object_access", "description": "Who can run a JDE object, or what a role can do."}


def test_the_model_reads_what_the_app_wrote(plugins) -> None:
    st = _state(_cfg({"object_access": ACCESS}))
    (t,) = build_app_tools(st.ai_config, state=lambda: st, root=plugins)
    assert t.name == "shop_object_access" and t.description == "Who can run a JDE object, or what a role can do."
    assert t.permission == "plugin:shop.lookup:j_object_access"
    # the function's own parameters — never what the framework injects, nor **kwargs
    assert t.input_schema == {"type": "object", "required": ["object_name"], "properties": {
        "object_name": {"type": "string", "description": "the object, e.g. P4210."},
        "role": {"type": "string", "description": "one role only."},
        "limit": {"type": "integer", "description": "how many lines at most."},
    }}
    assert t.summary_keys == ("object_name", "role")


@pytest.mark.asyncio
async def test_the_call_injects_the_framework_objects(plugins) -> None:
    st = _state(_cfg({"object_access": ACCESS, "count": {"callable": "shop.lookup:j_count", "description": "Count a table."}}))
    tools = {t.name: t for t in build_app_tools(st.ai_config, state=lambda: st, root=plugins)}
    out = await tools["shop_object_access"].run({"object_name": "P4210", "limit": "5", "connectors": "mine!", "settings": "x"})
    assert out == {"object": "P4210", "role": "*ALL", "limit": 5, "apps": ["other", "shop"], "given": "SimpleNamespace"}   # coerced; the injected names ignored
    current_principal.set(Principal(id="1", username="bob", permissions=("plugin:*",)))
    assert await tools["shop_count"].run({"table": "F0101"}) == {"rows_affected": 42, "table": "F0101", "by": "bob", "run": "ai:bob"}


def test_what_is_left_out(plugins, caplog) -> None:
    cfg = parse_ai_config({"ai": {
        "shop": {"tools": {
            "ok": ACCESS,
            "not_an_entry_point": {"callable": "shop.lookup:helper", "description": "x"},        # not a j_ function
            "elsewhere": {"callable": "os:system", "description": "x"},                          # not under plugins/
            "ghost": {"callable": "shop.nowhere:j_x", "description": "x"},
        }},
        "licensed": {"tools": {"t": ACCESS}},                                                    # its connector is not loaded
        "other": {"tools": {"t": ACCESS}},
    }})
    st = _state(cfg)
    assert [t.name for t in build_app_tools(cfg, state=lambda: st, root=plugins)] == ["shop_ok", "other_t"]
    assert "os:system is not a plugin entry point" in caplog.text and "shop.lookup:helper is not a plugin entry point" in caplog.text
    assert [t.name for t in build_app_tools(cfg, state=lambda: st, allowed=["other"], root=plugins)] == ["other_t"]
    assert build_app_tools(None, state=lambda: st, root=plugins) == []


def test_the_config_refuses_a_bad_tool() -> None:
    for bad in ({"Bad-Id": ACCESS}, {"t": {"callable": "no_colon", "description": "x"}}, {"t": {"callable": "a:b"}},
                {"t": {**ACCESS, "kind": "action"}}):
        with pytest.raises(ValueError):
            _cfg(bad)
    assert _cfg({"t": {**ACCESS, "permission": "sql:shop:*"}}).ai["shop"].tools["t"].required_permission() == "sql:shop:*"


@pytest.mark.asyncio
async def test_offered_and_run_by_permission(plugins, monkeypatch) -> None:
    monkeypatch.setattr("liberty.ai.app_tools.plugins_root", lambda: plugins)
    st = _state(_cfg({"object_access": ACCESS, "open": {**ACCESS, "permission": "sql:shop:orders"}}))
    reg = ToolRegistry(provider=app_tools_provider(lambda: st))
    assert reg.names() == ["shop_object_access", "shop_open"] and "shop_open" in reg and len(reg) == 2
    reader = Principal(id="1", username="bob", permissions=("sql:shop:orders",))
    assert [t["name"] for t in reg.definitions(reader)] == ["shop_open"]
    assert [t["name"] for t in reg.definitions(Principal(id="1", username="a", is_superuser=True))] == ["shop_object_access", "shop_open"]
    current_principal.set(reader)
    content, is_error = await reg.execute("shop_object_access", {"object_name": "P4210"})
    assert is_error and "missing plugin:shop.lookup:j_object_access" in content
    content, is_error = await reg.execute("shop_open", {"object_name": "P4210"})
    assert not is_error and '"object": "P4210"' in content


@pytest.mark.asyncio
async def test_a_failing_function_is_an_error_result(plugins, monkeypatch) -> None:
    monkeypatch.setattr("liberty.ai.app_tools.plugins_root", lambda: plugins)
    st = _state(_cfg({"broken": {"callable": "shop.lookup:j_broken", "description": "x"}}))
    content, is_error = await ToolRegistry(provider=app_tools_provider(lambda: st)).execute("shop_broken", {"table": "T"})
    assert is_error and "the database said no" in content


def test_a_reload_is_seen_without_rebuilding_the_assistant(plugins, monkeypatch) -> None:
    monkeypatch.setattr("liberty.ai.app_tools.plugins_root", lambda: plugins)
    st = _state(None)
    a = build_assistant(AISettings(enabled=True, api_key="", connector_tools=False, explorer_tools=False), SimpleNamespace(), state=lambda: st)
    builtin = a.tools.names()
    assert "describe_instance" in builtin and not any(n.startswith("shop_") for n in builtin)
    st.ai_config = _cfg({"object_access": ACCESS})                         # ai.toml reloaded
    assert a.tools.names() == [*builtin, "shop_object_access"]             # after the built-in ones
    assert [t["name"] for t in a._request_kwargs(model=None)["tools"]][-1] == "shop_object_access"
    st.ai_config = _cfg({})
    assert a.tools.names() == builtin
    # the operator can switch the applications' tools off
    off = build_assistant(AISettings(enabled=True, api_key="", app_tools=False), SimpleNamespace(), state=lambda: _state(_cfg({"object_access": ACCESS})))
    assert not any(n.startswith("shop_") for n in off.tools.names())


def test_a_built_in_name_wins(plugins, monkeypatch) -> None:
    from liberty.ai.tools import tool

    monkeypatch.setattr("liberty.ai.app_tools.plugins_root", lambda: plugins)
    st = _state(_cfg({"object_access": ACCESS}))
    reg = ToolRegistry(provider=app_tools_provider(lambda: st)).add(tool(lambda: "built-in", name="shop_object_access"))
    assert reg.names() == ["shop_object_access"] and reg.get("shop_object_access").permission is None
