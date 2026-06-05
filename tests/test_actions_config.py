"""Shared actions (``actions.toml``) — the reusable named-action registry (v1's ly_actions).

A SharedAction is a named chain of the same typed steps a screen uses; a screen references it via
a ``call_action`` action. These pin the model + parse/load round-trip, including a shared action
that composes other actions (nested ``call_action``) and calls a plugin (``call_plugin``)."""

from __future__ import annotations

import textwrap

from liberty.actions import SharedActionsFile, load_actions, parse_actions
from liberty.screens.config import Action
from pydantic import TypeAdapter


def test_call_action_is_a_first_class_action() -> None:
    """``call_action`` resolves through the screen Action union with a PLUGIN-like enum hint, and
    carries optional param_binds to seed the shared action's INPUT."""
    a = TypeAdapter(Action).validate_python({
        "type": "call_action", "id": "mk", "label": "Create role",
        "ref": "create_role", "param_binds": [{"param": "AUUSER", "source": "AUUSER"}],
    })
    assert type(a).__name__ == "CallActionAction"
    assert a.ref == "create_role" and a.param_binds[0].param == "AUUSER"
    schema = TypeAdapter(Action).json_schema()
    ca = next(s for s in schema["$defs"].values() if s.get("title") == "CallActionAction")
    assert ca["properties"]["ref"].get("x_enum_ref") == "SHARED_ACTIONS"


def test_shared_action_parses_and_injects_id() -> None:
    """parse_actions injects each action's id from its map key; steps validate as the full union
    (here: a call_plugin + a nested call_action) and prompt_fields parse."""
    f = parse_actions({"actions": {
        "create_role": {
            "label": "Create role in all tables",
            "prompt_fields": [{"name": "AUUSER", "hidden": True}],
            "steps": [
                {"id": "mk", "type": "call_plugin", "callable": "nomajde.security:j_create_role",
                 "param_binds": [{"param": "role", "source": "AUUSER"}]},
                {"id": "rem", "type": "call_action", "ref": "remerge",
                 "param_binds": [{"param": "child_role", "source": "AUUSER"}]},
            ],
        },
    }})
    a = f.actions["create_role"]
    assert a.id == "create_role"           # injected from key
    assert a.label == "Create role in all tables"
    assert [type(s).__name__ for s in a.steps] == ["CallPluginAction", "CallActionAction"]
    assert a.steps[1].ref == "remerge"
    assert a.prompt_fields[0].name == "AUUSER"


def test_load_actions_roundtrip(tmp_path) -> None:
    f = tmp_path / "actions.toml"
    f.write_text(textwrap.dedent("""
        [actions.import_security]
        label = "Import security from a source role"

        [[actions.import_security.steps]]
        id = "imp"
        type = "call_plugin"
        callable = "nomajde.security:j_import_security"

        [[actions.import_security.steps.param_binds]]
        param = "role"
        source = "ROLE"
    """))
    cfg = load_actions(f)
    act = cfg.actions["import_security"]
    assert act.id == "import_security"
    assert act.steps[0].callable == "nomajde.security:j_import_security"
    assert act.steps[0].param_binds[0].source == "ROLE"


def test_shared_action_params_carry_defaults() -> None:
    """A shared action declares its inputs under ``params`` (name + optional default). The binding
    (source) is set at the call site; the default lives on the param. Steps read ``INPUT.<name>``."""
    f = parse_actions({"actions": {"create_role": {
        "params": [
            {"name": "role", "description": "Role id"},
            {"name": "import_workbench", "default": "Y"},
        ],
        "steps": [
            {"id": "ins", "type": "run_query", "connector": "jdedwards", "query": "f0092_post",
             "param_binds": [{"param": "ULUSER", "source": "INPUT.role"}, {"param": "ULOUTQ", "value": "QPRINT"}]},
        ],
    }}})
    a = f.actions["create_role"]
    assert [p.name for p in a.params] == ["role", "import_workbench"]
    assert a.params[0].default is None and a.params[1].default == "Y"
    # the step binds the query placeholder to the action's input (INPUT.role), not a fixed source
    assert a.steps[0].param_binds[0].source == "INPUT.role"
    assert a.steps[0].param_binds[1].value == "QPRINT"


def test_load_actions_missing_file_is_empty(tmp_path) -> None:
    assert load_actions(tmp_path / "nope.toml") == SharedActionsFile()
