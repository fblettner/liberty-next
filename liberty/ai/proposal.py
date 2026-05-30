"""Proposal envelope — the structured shape every write-capable AI tool emits.

The assistant never writes to disk on its own. Tools that *could* mutate configuration
(scaffold a screen, propose dictionary entries, …) return a ``Proposal`` instead — the
chat UI renders it as a card with an Apply button. Applying POSTs to
``/admin/config/apply-proposal`` which dispatches to the existing parsed-config PUT
endpoints (the single, validated write path).

The envelope is deliberately minimal: a kind, a one-line summary, the target config
path (for the UI hint), the payload the apply endpoint will merge, plus any extra
context the assistant may need to reference in subsequent turns ("you already proposed
12 dictionary entries — don't redo them"). Keep this stable across kinds: the chat UI
renders a generic card and doesn't special-case shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ProposalKind = Literal[
    "dictionary_entries",  # add/update entries in dictionary.toml (shared or per-connector)
    "connector_queries",   # add/update queries on a SQL connector in connectors.toml
    "screen",              # add/replace a screen in screens.toml
    "menu_item",           # append a menu item to a [menus.<app>] tree
]


@dataclass(slots=True)
class Proposal:
    """One write the assistant is asking the operator to apply."""

    kind: ProposalKind
    summary: str
    target_path: str                          # human-facing config file the apply will rewrite
    payload: dict[str, Any]                   # what the apply endpoint will merge — shape depends on `kind`
    # Optional extras — none strictly required by the apply endpoint, but useful for the UI:
    app: str | None = None                    # nomajde / nomasx1 / nomaflow / … when scoped to an app
    connector: str | None = None              # the source/target connector if relevant
    notes: list[str] = field(default_factory=list)   # caveats the operator should read before applying

    def to_dict(self) -> dict[str, Any]:
        """Wire shape — sent to the chat UI as the ``proposal`` event payload, AND included
        verbatim in the tool_result returned to the model so it can reference what it proposed."""
        out: dict[str, Any] = {
            "kind": self.kind,
            "summary": self.summary,
            "target_path": self.target_path,
            "payload": self.payload,
        }
        if self.app is not None:
            out["app"] = self.app
        if self.connector is not None:
            out["connector"] = self.connector
        if self.notes:
            out["notes"] = list(self.notes)
        return out


def wrap(proposal: Proposal, *, message: str | None = None) -> dict[str, Any]:
    """The standard tool-return shape: the proposal plus an optional model-facing message.

    The assistant extracts ``["proposal"]`` from this dict to emit a ``proposal`` ChatEvent
    for the UI; the model sees the whole dict in its ``tool_result``."""
    out: dict[str, Any] = {"proposal": proposal.to_dict()}
    if message:
        out["message"] = message
    return out
