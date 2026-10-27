"""``explorer.toml`` — the Data Explorer model, one per app.

::

    [explorer.nomajde.sales]                # one model (a data mart) per [explorer.<app>.<model>]
    label = "Sales"
    hub = "sales"

    [explorer.nomajde.sales.entities.sales]
    label = "Sales"
    query = "fact_sales_get"            # a parameterless SELECT of the connector (default: the app)
    screen = "fact_sales"               # optional: this screen's column hints (labels, dd, hidden)
    key = ["SALE_ID"]
    cardinality = "many"                # rows per hub row — the hub itself is always "one"
    default_date = "SALE_DATE"
    measures = { QTY = "sum", REVENUE = "sum" }
    hidden = ["SALE_ID"]

    [explorer.nomajde.sales.entities.company]
    query = "dim_company_get"
    key = ["COMPANY"]

    [[explorer.nomajde.sales.relations]]
    from = "sales"
    to = "company"
    on = [["COMPANY", "COMPANY"]]       # pairs: a column of `from`, a column of `to`

No SQL lives here: an entity is a connector **query** (its ``read_query`` as a screen would
use it) and its fields are the query's result columns, labelled and typed through the
dictionary and the screen's column hints (:mod:`liberty.explorer.model`). Relations join
entities on column pairs; every entity must reach the hub through them (one or more hops).
An app may hold several models — tables that do not relate to each other are different data
marts; the permission is ``explorer:<app>:<model>`` (``explorer:<app>:*`` for all of them).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Cardinality = Literal["one", "many"]
Aggregate = Literal["sum", "avg", "min", "max", "count", "count_distinct"]

_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _check_id(value: str, what: str) -> str:
    if not value or not set(value) <= _ID_CHARS:
        raise ValueError(f"{what} id {value!r}: letters, digits, '_' and '-' only")
    return value


class ExplorerEntity(BaseModel):
    """One entity of the model — a query of a connector, seen as a table of fields."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""   # injected from the table key
    label: str | None = Field(default=None, description="Display label; defaults to the id.")
    description: str | None = None
    connector: str | None = Field(default=None, description="The connector of the query; blank = the app.")
    query: str = Field(description="A parameterless SELECT of the connector — the entity's rows.")
    screen: str | None = Field(default=None, description="A screen of the app whose column hints label and type the fields.")
    params: dict[str, Any] = Field(default_factory=dict, description="Fixed binds for the query, when it has params.")
    key: list[str] = Field(default_factory=list, description="The columns that identify one row (detail mode, drill).")
    cardinality: Cardinality = Field(default="one", description="Rows of this entity per hub row: one (a dimension) or many (a fact / lines).")
    default_date: str | None = Field(default=None, description="The date column a period applies to by default.")
    measures: dict[str, Aggregate] = Field(default_factory=dict, description="Numeric columns offered as measures, with their default aggregate.")
    hidden: list[str] = Field(default_factory=list, description="Columns left out of the fields pane (still queryable).")
    # Upgrade-safety flag (see Screen.override).
    override: bool = False

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return _check_id(v, "entity") if v else v


class ExplorerRelation(BaseModel):
    """A join: columns of ``from`` equal columns of ``to``, pair by pair."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    on: list[list[str]] = Field(description="Pairs [from_column, to_column].")

    @field_validator("on")
    @classmethod
    def _pairs(cls, v: list[list[str]]) -> list[list[str]]:
        if not v:
            raise ValueError("a relation needs at least one column pair")
        for pair in v:
            if len(pair) != 2 or not all(isinstance(c, str) and c for c in pair):
                raise ValueError(f"relation pair {pair!r}: expected [from_column, to_column]")
        return v


class BundledObject(BaseModel):
    """A shipped analysis or dashboard: installed once into the store, refreshed while untouched
    (see :func:`liberty.explorer.store.sync_bundled`). ``definition`` is the frontend's
    AnalysisDefinition (or the dashboard's tiles), kept opaque here."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    description: str | None = None
    audience: str | None = Field(default=None, description="Groups the picker: finance, sales, it…")
    version: int = Field(default=1, ge=1, description="Bump to refresh installed, untouched copies.")
    definition: dict[str, Any] = Field(default_factory=dict)


class ExplorerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""   # injected from the table key (the model id)
    app: str = ""  # injected from the outer table key
    label: str | None = None
    description: str | None = None
    hub: str = Field(description="The entity every analysis starts from.")
    entities: dict[str, ExplorerEntity] = Field(default_factory=dict)
    relations: list[ExplorerRelation] = Field(default_factory=list)
    analyses: dict[str, BundledObject] = Field(default_factory=dict, description="The bundled analyses, by id.")
    dashboards: dict[str, BundledObject] = Field(default_factory=dict, description="The bundled dashboards of analyses, by id.")
    override: bool = False

    @model_validator(mode="after")
    def _inject_and_check(self) -> ExplorerModel:
        for eid, e in self.entities.items():
            _check_id(eid, "entity")
            if not e.id:
                e.id = eid
            elif e.id != eid:
                raise ValueError(f"entity {eid!r}: id {e.id!r} does not match its key")
        if self.hub not in self.entities:
            raise ValueError(f"hub {self.hub!r} is not an entity of the model")
        self.entities[self.hub].cardinality = "one"
        for r in self.relations:
            for side in (r.from_, r.to):
                if side not in self.entities:
                    raise ValueError(f"relation {r.from_!r} → {r.to!r}: unknown entity {side!r}")
            if r.from_ == r.to:
                raise ValueError(f"relation {r.from_!r} → {r.to!r}: an entity cannot relate to itself")
        unreachable = set(self.entities) - self.reachable(self.hub)
        if unreachable:
            raise ValueError(f"entities not related to the hub {self.hub!r}: {sorted(unreachable)}")
        return self

    def reachable(self, start: str) -> set[str]:
        """The entities joined to *start* through the relations, in either direction."""
        seen = {start}
        frontier = [start]
        while frontier:
            cur = frontier.pop()
            for r in self.relations:
                nxt = r.to if r.from_ == cur else r.from_ if r.to == cur else None
                if nxt is not None and nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        return seen

    def path_to_hub(self, entity: str) -> list[ExplorerRelation]:
        """The relations from *entity* to the hub, shortest first hop by hop (breadth-first)."""
        if entity == self.hub:
            return []
        prev: dict[str, tuple[str, ExplorerRelation]] = {}
        frontier = [self.hub]
        seen = {self.hub}
        while frontier:
            cur = frontier.pop(0)
            for r in self.relations:
                nxt = r.to if r.from_ == cur else r.from_ if r.to == cur else None
                if nxt is None or nxt in seen:
                    continue
                seen.add(nxt)
                prev[nxt] = (cur, r)
                if nxt == entity:
                    out: list[ExplorerRelation] = []
                    node = entity
                    while node != self.hub:
                        node, rel = prev[node]
                        out.append(rel)
                    return out
                frontier.append(nxt)
        raise KeyError(entity)


class ExplorerFile(BaseModel):
    """``explorer: {app: {model_id: ExplorerModel}}``."""

    model_config = ConfigDict(extra="forbid")

    explorer: dict[str, dict[str, ExplorerModel]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _inject_ids(self) -> ExplorerFile:
        for app, models in self.explorer.items():
            for mid, m in models.items():
                _check_id(mid, "model")
                m.id = mid
                m.app = app
        return self

    def get(self, app: str, model: str) -> ExplorerModel | None:
        return (self.explorer.get(app) or {}).get(model)

    def items(self) -> list[tuple[str, str, ExplorerModel]]:
        """``(app, model_id, model)`` for every model."""
        return [(app, mid, m) for app, models in self.explorer.items() for mid, m in models.items()]


def parse_explorer(data: dict[str, Any]) -> ExplorerFile:
    return ExplorerFile.model_validate(data)


def load_explorer(path: Path | str) -> ExplorerFile:
    """Load and validate ``explorer.toml``; a missing file is an empty model set."""
    path = Path(path)
    if not path.exists():
        return ExplorerFile()
    with path.open("rb") as fh:
        return parse_explorer(tomllib.load(fh))
