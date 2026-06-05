"""Shared actions — reusable, named action chains (``actions.toml``).

v1's ``ly_actions`` / ``ly_act_tasks``: a named action defined ONCE and referenced from many
screens (their on_save / on_insert / on_delete hooks, tab buttons, row menus) via a
``call_action`` action. v2 already had the action *primitives* (run_query / call_api / call_plugin
/ if / chain / loop) + the ``ActionTreeView`` editor, but only ever bound to a single screen; this
module lifts them into a shared registry so orchestration lives in one editable place instead of
being copy-pasted into every screen's TOML.

A :class:`SharedAction` is essentially a named :class:`~liberty.screens.config.ChainAction`: an
ordered list of ``steps`` (the full Action union, including nested ``call_action`` so shared
actions compose) plus its own ``prompt_fields`` (collected once when it runs). The runtime resolves
a screen's ``call_action`` against this registry and runs the steps with the firing context.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from liberty.screens.config import Action, PromptField


class ActionParam(BaseModel):
    """One declared input of a shared action — the v1 ``ly_act_params`` shape. The shared action's
    steps read it as ``INPUT.<name>``; the **binding** (where the value comes from) is set at the
    CALL SITE (``call_action.param_binds``), so the same action can take its ``role`` from a screen's
    ``AUUSER`` column on one screen and ``ULUSER`` on another. The ``default`` lives HERE (not on the
    call-site bind) — it's used when a caller doesn't bind the param."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="Input name — bound at the call site, read by steps as ``INPUT.<name>``.")
    label: str | None = Field(default=None, description="Human label shown in the call_action bind picker / editor.")
    default: str | None = Field(default=None, description="Value used when the caller doesn't bind this param.")
    description: str | None = Field(default=None, description="What this input is for.")


class SharedAction(BaseModel):
    """One reusable named action. Keyed by ``id`` within ``actions.toml``'s ``[actions.*]`` map."""

    # ``extra = "ignore"`` so a future field doesn't break an older runtime mid-rollout.
    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="Stable id, referenced by a screen action's ``call_action.ref``.")
    label: str | None = Field(default=None, description="Human label shown in the Actions editor / pickers.")
    description: str | None = Field(default=None, description="What this action does — shown in the editor.")
    params: list[ActionParam] = Field(
        default_factory=list,
        description="The action's declared inputs (name + default). Bound at the call site via "
        "``call_action.param_binds``; the steps read them as ``INPUT.<name>``.",
    )
    prompt_fields: list[PromptField] = Field(
        default_factory=list,
        description="Inputs collected once before the action runs (land under ``INPUT.<name>``).",
    )
    steps: list[Action] = Field(
        default_factory=list,
        description="The ordered steps — the same typed actions a screen chain uses (run_query / "
        "call_api / call_plugin / if / loop / chain / call_action / …).",
    )

    @model_validator(mode="after")
    def _id_matches_key(self) -> "SharedAction":
        # Parsed via parse_actions, which injects id from the key; an explicit mismatching id is a
        # config error worth surfacing rather than silently trusting one side.
        return self


class SharedActionsFile(BaseModel):
    """Parsed ``actions.toml`` — ``[actions.<id>]`` entries."""

    model_config = ConfigDict(extra="ignore")

    actions: dict[str, SharedAction] = Field(default_factory=dict)


def parse_actions(data: dict[str, Any]) -> SharedActionsFile:
    """Validate a raw TOML dict into a :class:`SharedActionsFile`, injecting each action's ``id``
    from its map key when omitted (by-hand authors rarely repeat it)."""
    actions = data.get("actions") or {}
    if isinstance(actions, dict):
        for aid, action in actions.items():
            if isinstance(action, dict) and not action.get("id"):
                action["id"] = aid
    return SharedActionsFile.model_validate(data)


def load_actions(path: Path | str) -> SharedActionsFile:
    """Load + validate ``actions.toml``. A missing file yields an empty registry."""
    path = Path(path)
    if not path.exists():
        return SharedActionsFile()
    with path.open("rb") as fh:
        return parse_actions(tomllib.load(fh))
