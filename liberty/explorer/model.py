"""The model as the UI sees it: entities with their **fields**, discovered from each entity
query's result columns. Nothing is hard-coded per field — the column's label, format, rule
(enum / lookup / boolean) and ``dd`` come from the dictionary and the entity's screen hints,
exactly as the data screens get them; the model adds the kind (dimension / measure), the
default aggregate and the type class.

Field keys are ``<entity>.<COLUMN>`` with the column name **upper-cased**: the framework's
convention (Oracle folds unquoted identifiers to uppercase, Postgres to lowercase; config files
name columns uppercase and match without case), so an analysis saved on one dialect runs on the
other. Types collapse to five classes the engine and the UI
reason about: ``string`` · ``number`` · ``date`` · ``datetime`` · ``boolean``.

Discovery runs each entity query with ``max_rows = 1`` (the row-level scope applies, so a
caller only ever sees what they may read). The result is cached per (app, language) on
``app.state`` until the next reload or an explicit refresh — a model is small, its queries
may not be.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from liberty.connectors.registry import ConnectorRegistry
from liberty.connectors.sql import Column
from liberty.explorer.config import Aggregate, ExplorerEntity, ExplorerModel
from liberty.screens.config import ScreensFile

_log = logging.getLogger(__name__)

CACHE_SECONDS = 600

_NUMBER_FORMATS = {"number", "integer", "decimal", "currency", "percent"}
_DATE_FORMATS = {"date", "jdedate"}
_DATETIME_FORMATS = {"datetime", "timestamp"}
_NUMBER_TYPES = ("int", "num", "dec", "float", "double", "real", "money", "serial", "bigint", "smallint")
_DATETIME_TYPES = ("timestamp", "datetime")
_DATE_TYPES = ("date",)


def type_class(col: Column, sample: Any = None) -> str:
    """``string`` / ``number`` / ``date`` / ``datetime`` / ``boolean`` from the column's rule,
    format and driver type, in that order of trust; when the driver reports no type (SQLite,
    computed columns) the sampled value's Python type decides."""
    rule = col.rule or {}
    if rule.get("kind") == "boolean":
        return "boolean"
    fmt = (col.format or "").strip().lower()
    if fmt in _NUMBER_FORMATS:
        return "number"
    if fmt in _DATE_FORMATS:
        return "date"
    if fmt in _DATETIME_FORMATS:
        return "datetime"
    if fmt == "jdetime":
        return "string"
    t = (col.type or "").strip().lower()
    if any(x in t for x in _DATETIME_TYPES):
        return "datetime"
    if any(x in t for x in _DATE_TYPES):
        return "date"
    if t == "bool" or t == "boolean":
        return "boolean"
    if any(x in t for x in _NUMBER_TYPES):
        return "number"
    if not t and sample is not None:
        if isinstance(sample, bool):
            return "boolean"
        if isinstance(sample, (int, float, Decimal)):
            return "number"
        if isinstance(sample, datetime):
            return "datetime"
        if isinstance(sample, date):
            return "date"
    return "string"


@dataclass(slots=True)
class ExplorerField:
    key: str
    entity: str
    name: str
    label: str
    type: str
    kind: str                       # dimension | measure
    aggregate: Aggregate | None = None
    format: str | None = None
    rule: dict[str, Any] | None = None   # enum / lookup / boolean, the wire shape the grid uses
    dd: str | None = None
    hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"key": self.key, "entity": self.entity, "name": self.name, "label": self.label, "type": self.type, "kind": self.kind}
        if self.aggregate:
            d["aggregate"] = self.aggregate
        if self.format:
            d["format"] = self.format
        if self.rule:
            d["rule"] = self.rule
        if self.dd:
            d["dd"] = self.dd
        if self.hidden:
            d["hidden"] = True
        return d


@dataclass(slots=True)
class EntityView:
    entity: ExplorerEntity
    fields: list[ExplorerField] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        e = self.entity
        d: dict[str, Any] = {
            "id": e.id, "label": e.label or e.id, "description": e.description, "connector": e.connector,
            "query": e.query, "screen": e.screen, "key": list(e.key), "cardinality": e.cardinality,
            "default_date": e.default_date,
            "fields": [f.to_dict() for f in self.fields if not f.hidden],
        }
        if self.error:
            d["error"] = self.error
        return d


@dataclass(slots=True)
class ModelView:
    model: ExplorerModel
    app: str
    entities: list[EntityView]
    warnings: list[str] = field(default_factory=list)
    built_at: float = 0.0

    def field(self, key: str) -> ExplorerField | None:
        ent, _, name = key.partition(".")
        for ev in self.entities:
            if ev.entity.id == ent:
                for f in ev.fields:
                    if f.name == name.upper():
                        return f
        return None

    def to_dict(self) -> dict[str, Any]:
        m = self.model
        return {
            "app": self.app, "model": m.id, "label": m.label or m.id, "description": m.description, "hub": m.hub,
            "entities": [ev.to_dict() for ev in self.entities],
            "relations": [{"from": r.from_, "to": r.to, "on": [list(p) for p in r.on]} for r in m.relations],
            "warnings": list(self.warnings),
        }


def fields_from_columns(
    entity: ExplorerEntity, columns: list[Column], sample: dict[str, Any] | None = None,
) -> tuple[list[ExplorerField], list[str]]:
    """Classify an entity's result columns. A column named in ``measures`` becomes a measure
    when numeric (a warning otherwise); ``hidden`` columns, and hint-hidden ones, stay in the
    list flagged hidden so a saved analysis keeps working after an operator hides a field."""
    out: list[ExplorerField] = []
    warnings: list[str] = []
    hidden = {h.upper() for h in entity.hidden}
    measures = {k.upper(): v for k, v in entity.measures.items()}
    names = {c.name.upper() for c in columns}
    for name in measures:
        if name not in names:
            warnings.append(f"{entity.id}: measure column {name!r} is not a column of {entity.query!r}")
    for col in columns:
        tc = type_class(col, (sample or {}).get(col.name))
        up = col.name.upper()
        agg = measures.get(up)
        kind = "dimension"
        if agg is not None:
            if tc == "number":
                kind = "measure"
            else:
                warnings.append(f"{entity.id}.{col.name}: declared as a measure but typed {tc}")
                agg = None
        out.append(ExplorerField(
            key=f"{entity.id}.{up}", entity=entity.id, name=up, label=col.label or col.name, type=tc,
            kind=kind, aggregate=agg, format=col.format, rule=col.rule, dd=col.dd, hidden=(up in hidden or col.hidden),
        ))
    if entity.default_date and entity.default_date.upper() not in names:
        warnings.append(f"{entity.id}: default_date {entity.default_date!r} is not a column of {entity.query!r}")
    for k in entity.key:
        if k.upper() not in names:
            warnings.append(f"{entity.id}: key column {k!r} is not a column of {entity.query!r}")
    return out, warnings


async def describe_entity(
    connectors: ConnectorRegistry, screens: ScreensFile | None, app: str, entity: ExplorerEntity, *, language: str | None,
) -> EntityView:
    """Run the entity query for one row and classify its columns. A failure (a bad query, a
    missing permission on the pool…) is carried on the view, not raised: the rest of the model
    stays usable and the operator sees which entity is broken."""
    view = EntityView(entity=entity)
    connector = entity.connector or app
    screen = None
    if screens is not None and entity.screen:
        screen = (screens.screens.get(app) or {}).get(entity.screen)
        if screen is None:
            view.error = f"screen {app}.{entity.screen!r} not found"
    try:
        conn = connectors.sql(connector)
        result = await conn.execute(
            entity.query, dict(entity.params), language=language, max_rows=1,
            column_hints=list(screen.columns) if screen is not None and screen.columns else None,
            dictionary_scope=app,
        )
    except Exception as exc:  # noqa: BLE001 — reported on the entity
        view.error = f"{connector}.{entity.query}: {exc}"
        return view
    view.fields, view.warnings = fields_from_columns(entity, result.columns, result.rows[0] if result.rows else None)
    return view


async def build_model_view(
    connectors: ConnectorRegistry, screens: ScreensFile | None, app: str, model: ExplorerModel, *, language: str | None,
) -> ModelView:
    """Every entity described concurrently (they are independent queries)."""
    order = [model.hub] + [eid for eid in model.entities if eid != model.hub]
    views = await asyncio.gather(*(describe_entity(connectors, screens, app, model.entities[eid], language=language) for eid in order))
    warnings: list[str] = []
    for ev in views:
        warnings.extend([ev.error] if ev.error else ev.warnings)
    return ModelView(model=model, app=app, entities=list(views), warnings=warnings, built_at=time.monotonic())


class ModelCache:
    """Per-app, per-language cache of :class:`ModelView` with a TTL; cleared on reload."""

    def __init__(self, ttl: float = CACHE_SECONDS) -> None:
        self._ttl = ttl
        self._views: dict[tuple[str, str, str], ModelView] = {}
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        self._views.clear()

    async def get(
        self, connectors: ConnectorRegistry, screens: ScreensFile | None, app: str, model: ExplorerModel, *,
        language: str | None, refresh: bool = False,
    ) -> ModelView:
        key = (app, model.id, language or "")
        now = time.monotonic()
        cached = self._views.get(key)
        if cached is not None and not refresh and cached.model is model and now - cached.built_at < self._ttl:
            return cached
        async with self._lock:
            cached = self._views.get(key)
            if cached is not None and not refresh and cached.model is model and now - cached.built_at < self._ttl:
                return cached
            view = await build_model_view(connectors, screens, app, model, language=language)
            self._views[key] = view
            return view
