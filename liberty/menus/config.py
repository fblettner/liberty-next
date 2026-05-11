"""``config/menus.toml`` — app navigation menus (the v2 form of v1's ``ly_menus``).

One ``[menus.<app>]`` per app, where ``<app>`` is a connector name. Each is a *flat* list
of items linked by ``parent`` (the backend assembles the tree — flat is far friendlier to
hand-edit and round-trips cleanly through TOML). An item is either:

* a **folder** — no ``type``; groups children, shows iff ≥1 descendant is visible;
* a **leaf** — ``type = "query"`` (→ a SELECT screen / TableView) or ``"endpoint"`` (→ an
  API call / HttpRunner), pointing at ``connector`` (defaults to the app) ``.`` ``target``
  (the query / endpoint name). It may carry fixed ``params`` and a ``roles`` visibility filter.

Example::

    [menus.nomasx1]
    label = "Rights, licenses and SOD"

    [[menus.nomasx1.items]]
    id = "security"
    label = "Security"
    l.fr = "Sécurité"
    icon = "shield"

    [[menus.nomasx1.items]]
    id = "security.users"
    parent = "security"
    label = "Users"
    l.fr = "Utilisateurs"
    type = "query"
    target = "security_users_get"

``GET /api/menus`` resolves the labels in the request's language and prunes anything the
caller can't run (the leaf's ``sql:<connector>:<target>`` / ``api:<connector>:<target>``
permission, plus any ``roles`` filter), then drops folders left empty.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ItemType = Literal["query", "endpoint"]  # a leaf's kind; ``None`` = a folder/group node


class MenuItem(BaseModel):
    """One menu node — a folder (no ``type``) or a leaf pointing at a connector target."""

    model_config = ConfigDict(extra="forbid")

    id: str
    parent: str | None = None  # the id of the parent item; None = a top-level item
    label: str  # the default-language display label (v1's menu_label)
    l: dict[str, str] = Field(default_factory=dict)  # language code → translated label (v1's ly_menus_l)
    icon: str | None = None  # a lucide icon name — a UI hint, not interpreted here
    type: ItemType | None = None  # None = folder
    connector: str | None = None  # which connector the target lives in (defaults to the app)
    target: str | None = None  # the query / endpoint name
    params: dict[str, Any] = Field(default_factory=dict)  # fixed params handed to the target
    roles: list[str] = Field(default_factory=list)  # if non-empty, visible only to these roles

    def label_for(self, language: str | None) -> str:
        """The label in *language* if a translation exists, else the default label."""
        if language and self.l:
            return self.l.get(language) or self.label
        return self.label

    @model_validator(mode="after")
    def _check(self) -> MenuItem:
        if self.type is None:  # folder
            if self.target is not None or self.connector is not None or self.params:
                raise ValueError(f"menu item {self.id!r}: a folder (no `type`) cannot carry target/connector/params")
        elif not self.target:  # leaf
            raise ValueError(f"menu item {self.id!r}: a {self.type!r} item needs a `target`")
        return self


class AppMenu(BaseModel):
    """One app's menu — its display name plus a flat list of items linked by ``parent``."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None  # the app's display name (defaults to the app / connector name)
    items: list[MenuItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> AppMenu:
        by_id: dict[str, MenuItem] = {}
        for it in self.items:
            if it.id in by_id:
                raise ValueError(f"duplicate menu item id {it.id!r}")
            by_id[it.id] = it
        for it in self.items:  # every parent must exist, and following parents must terminate
            seen: set[str] = set()
            cur = it
            while cur.parent is not None:
                if cur.parent not in by_id:
                    raise ValueError(f"menu item {cur.id!r}: unknown parent {cur.parent!r}")
                if cur.parent in seen:
                    raise ValueError(f"menu item {it.id!r}: parent cycle through {cur.parent!r}")
                seen.add(cur.id)
                cur = by_id[cur.parent]
        return self


class MenusFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    menus: dict[str, AppMenu] = Field(default_factory=dict)


def parse_menus(data: dict[str, Any]) -> MenusFile:
    """Validate a raw TOML dict into a :class:`MenusFile`."""
    return MenusFile.model_validate(data)


def load_menus(path: Path | str) -> MenusFile:
    """Load and validate ``menus.toml``. A missing file yields an empty menu set."""
    path = Path(path)
    if not path.exists():
        return MenusFile()
    with path.open("rb") as fh:
        return parse_menus(tomllib.load(fh))


def build_menu_tree(
    app_menu: AppMenu,
    *,
    app: str,
    language: str | None = None,
    keep: Callable[[MenuItem, str], bool] | None = None,
) -> list[dict[str, Any]]:
    """The app's menu as a nested list of dicts (file order preserved), labels resolved in *language*.

    *keep* is consulted for each **leaf** as ``keep(item, resolved_connector)`` (where the connector
    is ``item.connector`` or *app*); a folder survives iff ≥1 descendant does. When *keep* is ``None``
    nothing is pruned. Node dicts: ``id``, ``label``, ``icon`` (if set); a folder adds ``items``;
    a leaf adds ``type``/``connector``/``target`` (and ``params`` when non-empty)."""
    children: dict[str | None, list[MenuItem]] = {}
    for it in app_menu.items:
        children.setdefault(it.parent, []).append(it)

    def node(it: MenuItem) -> dict[str, Any] | None:
        d: dict[str, Any] = {"id": it.id, "label": it.label_for(language)}
        if it.icon:
            d["icon"] = it.icon
        if it.type is None:  # folder
            kids = [n for c in children.get(it.id, []) if (n := node(c)) is not None]
            if not kids:
                return None  # an empty folder collapses away
            d["items"] = kids
            return d
        connector = it.connector or app  # leaf
        if keep is not None and not keep(it, connector):
            return None
        d["type"] = it.type
        d["connector"] = connector
        d["target"] = it.target
        if it.params:
            d["params"] = it.params
        return d

    return [n for it in children.get(None, []) if (n := node(it)) is not None]
