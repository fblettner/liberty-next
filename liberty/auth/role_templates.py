"""The predefined roles the applications ship (``roles.toml``, next to the other config files).

    [roles.shop_sales_manager]
    app = "shop"
    label = "Sales manager"
    description = "Orders and customers: read and change. The rest of the application: read."
    l = { fr = { label = "Responsable des ventes", description = "…" } }
    permissions = ["menu:shop:*", "edit:shop:sales", "explorer:shop:*", "ai:chat"]

A predefined role is a suggestion: *Access → Roles → Add a predefined role* opens it as a new
role, which the customer saves — and may then change — in their own store. Nothing here is a
user's role by itself.

What a role may open is said the way the role editor says it: a menu folder or entry in read
(``menu:<app>:<id>``) or in read and change (``edit:<app>:<id>`` on top) — the server works out
what the screens under it need (:mod:`liberty.auth.implied`), so a predefined role follows its
folders when an upgrade adds screens to them.

**A predefined role only gives.** It holds no rule that takes away (``!…``): a person may hold
several roles at once — a security officer who is also the licence manager — and their rights
add up; one role's "hidden" would take away what the other gives.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from liberty.auth.scope import ScopeRule


class RoleTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="The role's name as a person reads it.")
    description: str | None = Field(default=None, description="Who this role is for, in one sentence.")
    l: dict[str, dict[str, str]] = Field(
        default_factory=dict, title="In the other languages",
        description="Per language: ``label`` and ``description``.",
    )
    app: str | None = Field(default=None, description="The application the role belongs to (where it is listed).")
    permissions: list[str] = Field(default_factory=list, description="Menu folders and entries (``menu:`` / ``edit:``) and the rules beyond the menus.")
    scope: list[ScopeRule] = Field(default_factory=list, description="Row-level data scope shipped with the role (rare).")

    @field_validator("permissions")
    @classmethod
    def _only_gives(cls, v: list[str]) -> list[str]:
        cleaned = [p.strip() for p in v if p and p.strip()]
        taking = [p for p in cleaned if p.startswith("!")]
        if taking:
            raise ValueError(
                f"a predefined role only gives: {', '.join(taking)} takes away, and would do so from every other "
                "role the same person holds"
            )
        return cleaned

    def text(self, field: str, language: str | None) -> str | None:
        """``label`` / ``description`` in *language*, else as written."""
        lang = (language or "").split("-")[0].lower()
        said = (self.l.get(lang) or {}).get(field) if lang else None
        return said or getattr(self, field)


class RoleTemplatesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: dict[str, RoleTemplate] = Field(default_factory=dict)


def parse_role_templates(data: dict[str, Any]) -> RoleTemplatesFile:
    return RoleTemplatesFile.model_validate(data or {})


def load_role_templates(path: Path | str) -> RoleTemplatesFile:
    """The catalogue at *path*; an application that ships none has no file: an empty catalogue."""
    p = Path(path)
    if not p.is_file():
        return RoleTemplatesFile()
    return parse_role_templates(tomllib.loads(p.read_text(encoding="utf-8")))
