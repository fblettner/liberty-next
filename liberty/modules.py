"""Module catalogues (catalogue v2) — a whole application module described once, beside the table
presets in ``<config dir>/presets/**.toml``: its screens (one per table, with the key, the grid
columns, the dialog layout and the detail tabs to the related tables) and the menu folder they go
under. "Apply module" (the Screen Assistant) turns a module into configuration: a read query per
table, the screens, the menu, the dictionary entries.

Columns are named by **data item** (``AN8``), never by physical column: JD Edwards prefixes them
per table (``ABAN8`` on F0101, ``WWAN8`` on F0111), and the apply resolves them against the live
schema — ``prefix`` on a screen forces it. ``schema`` is the portable ``#SCHEMA.<KEY>#`` token.

::

    [[modules]]
    id = "address_book"
    label = "Address Book"
    connector = "jdedwards"
    app = "nomajde"
    schema = "#SCHEMA.DTA#"
    menu = { label = "Address Book", icon = "users" }

    [[modules.screens]]
    id = "f0101"
    table = "F0101"
    label = "Address Book"
    key = ["AN8"]
    columns = ["AN8", "ALPH", "AT1"]           # the grid; the dialog shows them too unless tabs say otherwise
    [[modules.screens.tabs]]
    label = "General"
    fields = ["AN8", "ALKY", "ALPH"]
    [[modules.screens.details]]                # a nested table tab: the child screen, filtered on the parent's key
    screen = "f0111"
    label = "Who's Who"
    on = ["AN8"]                               # same data item on both sides, or { parent = "AN8", child = "AN8" }

A screen named in a ``details`` entry is a detail: it gets no menu item of its own unless it says
``menu = true``. Display first: the module declares no write — the generated screens are read-only.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_DATA_ITEM = r"^[A-Z0-9#$@]{1,10}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DetailLink(_Strict):
    """One column pair a detail is filtered on: the parent's data item and the child's."""

    parent: str = Field(pattern=_DATA_ITEM)
    child: str = Field(pattern=_DATA_ITEM)


class ModuleDetail(_Strict):
    """A tab on the parent screen's dialog showing *screen* (another screen of the module) for the
    rows whose *on* columns equal the parent's: a grid (``many``, a nested table) or, for a record
    the parent has at most one of (``one`` — the link covers the child's whole key), its form."""

    screen: str
    label: str
    l: dict[str, str] = Field(default_factory=dict, description="Translations of the label per language.")
    on: list[DetailLink] = Field(min_length=1)
    cardinality: Literal["many", "one"] = "many"

    @field_validator("on", mode="before")
    @classmethod
    def _same_item(cls, v: Any) -> Any:
        # ``"AN8"`` is the same data item on both sides
        return [{"parent": x, "child": x} if isinstance(x, str) else x for x in (v or [])]


class ModuleJoin(_Strict):
    """A lookup table joined to the screen's table (LEFT JOIN) for a few of its columns — the name
    of an address on a customer record. *on*: data items present on both tables."""

    table: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    schema_: str | None = Field(default=None, alias="schema", description="Overrides the module's schema token.")
    prefix: str | None = Field(default=None, pattern=r"^[A-Z0-9]{1,4}$")
    on: list[str] = Field(min_length=1)
    columns: list[str] = Field(min_length=1, description="The joined table's columns brought in (data items).")

    @field_validator("on", "columns")
    @classmethod
    def _items(cls, v: list[str]) -> list[str]:
        import re
        bad = [x for x in v if not re.match(_DATA_ITEM, x)]
        if bad:
            raise ValueError(f"not data items: {bad}")
        return v


class ModuleTab(_Strict):
    """A form tab of the dialog — its fields as data items."""

    label: str
    l: dict[str, str] = Field(default_factory=dict)
    cols: int = Field(default=2, ge=1, le=4)
    fields: list[str] = Field(min_length=1)


class ModuleScreen(_Strict):
    """One table of the module as a screen."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", description="The screen id (and the read query's stem: ``<id>_get``).")
    table: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    label: str
    l: dict[str, str] = Field(default_factory=dict)
    description: str | None = None
    schema_: str | None = Field(default=None, alias="schema", description="Overrides the module's schema token.")
    prefix: str | None = Field(default=None, pattern=r"^[A-Z0-9]{1,4}$",
                               description="The column prefix (JDE: AB on F0101); resolved from the live schema when absent.")
    key: list[str] = Field(min_length=1)
    columns: list[str] = Field(default_factory=list, description="The grid's columns (data items); empty = every column of the table.")
    filters: list[str] = Field(default_factory=list, description="The columns of the advanced filter panel (data items, joined ones too) — filtered in the database, before the rows are loaded.")
    tabs: list[ModuleTab] = Field(default_factory=list, description="The dialog's form tabs; none = one tab with the grid columns.")
    details: list[ModuleDetail] = Field(default_factory=list)
    joins: list[ModuleJoin] = Field(default_factory=list, description="Lookup tables joined for a few columns (listed in ``columns`` / tabs like the table's own).")
    menu: bool | None = Field(default=None, description="A menu item for this screen; default: yes unless it is another screen's detail.")
    row_menu: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "The screen's row-menu actions, as in screens.toml (an ``open_url`` to the source application…); "
            "a ``{ITEM}`` placeholder of an ``open_url`` names a data item of the screen — the apply puts the "
            "physical column in its place."
        ),
    )
    icon: str | None = None

    @field_validator("key", "columns", "filters")
    @classmethod
    def _items(cls, v: list[str]) -> list[str]:
        import re
        bad = [x for x in v if not re.match(_DATA_ITEM, x)]
        if bad:
            raise ValueError(f"not data items: {bad}")
        if len(set(v)) != len(v):
            raise ValueError("listed twice")
        return v

    def items(self) -> list[str]:
        """Every data item the screen names (key, grid, dialog, detail links, joined columns), in
        first-seen order."""
        out: list[str] = []
        for x in [*self.key, *self.columns, *(f for t in self.tabs for f in t.fields), *(p.parent for d in self.details for p in d.on),
                  *(c for j in self.joins for c in j.columns)]:
            if x not in out:
                out.append(x)
        return out


class ModuleMenu(_Strict):
    """The menu folder the module's screens go under."""

    label: str
    l: dict[str, str] = Field(default_factory=dict)
    icon: str | None = None
    parent: str | None = Field(default=None, description="An existing folder of the app's menu; none = top level.")


class Module(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    l: dict[str, str] = Field(default_factory=dict)
    description: str | None = None
    group: str | None = None
    connector: str
    app: str
    schema_: str | None = Field(default=None, alias="schema")
    menu: ModuleMenu
    screens: list[ModuleScreen] = Field(min_length=1)

    @model_validator(mode="after")
    def _graph(self) -> Module:
        ids = [s.id for s in self.screens]
        dup = sorted({x for x in ids if ids.count(x) > 1})
        if dup:
            raise ValueError(f"screen ids listed twice: {dup}")
        by_id = {s.id: s for s in self.screens}
        for s in self.screens:
            for d in s.details:
                child = by_id.get(d.screen)
                if child is None:
                    raise ValueError(f"screen {s.id!r}: detail {d.screen!r} is not a screen of the module")
                if child.id == s.id:
                    raise ValueError(f"screen {s.id!r} lists itself as a detail")
                # the child is filtered on its link columns: they must be part of its key (a detail row
                # belongs to one parent) — else a new detail row could not be tied to the parent
                missing = [p.child for p in d.on if p.child not in child.key]
                if missing:
                    raise ValueError(f"screen {s.id!r}: detail {d.screen!r} links on {missing}, not in its key {child.key}")
                if d.cardinality == "one" and {p.child for p in d.on} != set(child.key):
                    raise ValueError(f"screen {s.id!r}: detail {d.screen!r} is one-to-one but links on part of its key {child.key}")
        # no cycle: a screen cannot (transitively) be its own detail
        def walk(sid: str, path: tuple[str, ...]) -> None:
            for d in by_id[sid].details:
                if d.screen in path:
                    raise ValueError(f"detail cycle: {' → '.join((*path, d.screen))}")
                walk(d.screen, (*path, d.screen))
        for sid in ids:
            walk(sid, (sid,))
        return self

    @model_validator(mode="after")
    def _row_menu_actions(self) -> Module:
        from pydantic import TypeAdapter

        from liberty.screens.config import Action
        adapter = TypeAdapter(Action)
        for s in self.screens:
            for a in s.row_menu:
                adapter.validate_python(a)       # a malformed action fails at load, naming the module
        return self

    @model_validator(mode="after")
    def _filters_shown(self) -> Module:
        for s in self.screens:
            unknown = [f for f in s.filters if f not in s.items()]
            if unknown:
                raise ValueError(f"screen {s.id!r}: filters {unknown} are not columns of the screen (key, columns, tabs or joins)")
        return self

    def detail_ids(self) -> set[str]:
        return {d.screen for s in self.screens for d in s.details}

    def menu_screens(self) -> list[ModuleScreen]:
        """The screens that get a menu item: the explicit ones, else every screen that is no detail."""
        details = self.detail_ids()
        return [s for s in self.screens if (s.menu if s.menu is not None else s.id not in details)]

    def screen_schema(self, screen: ModuleScreen) -> str | None:
        return screen.schema_ or self.schema_


class ModuleError(ValueError):
    """A catalogue file whose ``[[modules]]`` do not validate — the message names the file and module."""


def load_modules(base: Path | str, connector: str | None = None) -> list[Module]:
    """Every ``[[modules]]`` of the catalogue files under *base* (for *connector* when given), sorted
    by group then label. A module that does not validate raises :class:`ModuleError` naming it; two
    modules with the same id too."""
    base = Path(base)
    if not base.is_dir():
        return []
    out: list[Module] = []
    seen: dict[str, Path] = {}
    for path in sorted(base.rglob("*.toml")):
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8")).get("modules") or []
        except tomllib.TOMLDecodeError as exc:
            raise ModuleError(f"{path.name}: {exc}") from exc
        for i, entry in enumerate(raw):
            try:
                module = Module.model_validate(entry)
            except ValidationError as exc:
                name = entry.get("id") if isinstance(entry, dict) else None
                raise ModuleError(f"{path.name}: module {name or f'#{i + 1}'}: {exc}") from exc
            if module.id in seen:
                raise ModuleError(f"{path.name}: module {module.id!r} also in {seen[module.id].name}")
            seen[module.id] = path
            if connector is None or module.connector == connector:
                out.append(module)
    return sorted(out, key=lambda m: (m.group or "", m.label))


def find_module(base: Path | str, module_id: str) -> Module | None:
    return next((m for m in load_modules(base) if m.id == module_id), None)
