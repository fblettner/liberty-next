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

from pathlib import Path

from liberty.connectors.registry import ConnectorRegistry
from liberty.connectors.sql import Column
from liberty.explorer.config import Aggregate, ExplorerEntity, ExplorerModel, ExplorerRelation
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
    decimals: int | None = None     # the entry's places (a jdenumber's implied ones: scaled in SQL)
    rule: dict[str, Any] | None = None   # enum / lookup / boolean, the wire shape the grid uses
    dd: str | None = None
    hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"key": self.key, "entity": self.entity, "name": self.name, "label": self.label, "type": self.type, "kind": self.kind}
        if self.aggregate:
            d["aggregate"] = self.aggregate
        if self.format:
            d["format"] = self.format
        if self.decimals is not None:
            d["decimals"] = self.decimals
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
    model: ExplorerModel            # the effective model: the preset applied, relations resolved
    app: str
    entities: list[EntityView]
    warnings: list[str] = field(default_factory=list)
    built_at: float = 0.0
    source: ExplorerModel | None = None   # the configured model (the cache's identity)

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
            "app": self.app, "model": m.id, "label": m.label or m.id, "description": m.description, "hub": m.hub, "preset": m.preset,
            "derived": m.derived,
            "entities": [ev.to_dict() for ev in self.entities],
            "relations": [{"from": r.from_, "to": r.to, "on": [list(p) for p in r.on]} for r in m.relations],
            "warnings": list(self.warnings),
            # visible columns no dictionary entry documents (label = the column name): the page
            # offers a superuser to create them from the source's data dictionary
            "undocumented": sum(1 for ev in self.entities for f in ev.fields if not f.hidden and not f.dd),
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
        # No label anywhere → the upper-cased name (the dialect may report a lower-cased key for a
        # case-insensitive Oracle column; the field's name is upper-cased too, so the pane shows one).
        out.append(ExplorerField(
            key=f"{entity.id}.{up}", entity=entity.id, name=up, label=col.label or up, type=tc,
            kind=kind, aggregate=agg, format=col.format, decimals=col.decimals, rule=col.rule, dd=col.dd, hidden=(up in hidden or col.hidden),
        ))
    if entity.default_date and entity.default_date.upper() not in names:
        warnings.append(f"{entity.id}: default_date {entity.default_date!r} is not a column of {entity.query!r}")
    for k in entity.key:
        if k.upper() not in names:
            warnings.append(f"{entity.id}: key column {k!r} is not a column of {entity.query!r}")
    return out, warnings


def resolve_dictionary(
    columns: list[Column], dictionary: Any, connector: str | None, prefix: int, language: str | None, *, fallback: str | None = None,
) -> None:
    """Label, format, rule and item of the columns a screen did not hint, from the dictionary —
    the entry named like the column, else (on a ``dd_prefix`` connector) like the column minus
    the table prefix (``ABALPH`` → ``ALPH``); *connector*'s section first (the APP's: a JDE
    item is filed under ``nomajde``, the app, the way screens look it up), then *fallback*'s
    (the data connector's own section), then the shared one. What the screen set is kept."""
    if dictionary is None:
        return
    scopes = [s for s in (connector, fallback) if s]
    for col in columns:
        if col.label is not None and col.dd:
            continue
        keys = [col.name, col.name.upper()]
        if prefix and len(col.name) > prefix:
            keys += [col.name[prefix:], col.name[prefix:].upper()]
        entry = key = None
        for k in keys:
            for scope in scopes or [None]:
                entry = dictionary.find_entry(k, connector=scope)
                if entry is not None:
                    break
            if entry is not None:
                key = k
                break
        if entry is None:
            continue
        if col.label is None:
            col.label = entry.label_for(language) or col.label
        if col.format is None:
            col.format = entry.format
        if col.decimals is None:
            col.decimals = entry.decimals
        if col.rule is None:
            try:
                col.rule = dictionary.resolve_rule(entry, connector=connector, language=language)
            except Exception:  # noqa: BLE001 — a broken enum / lookup reference is not the explorer's problem
                col.rule = None
        col.dd = col.dd or key


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
        query = entity.query
        if entity.table_sql is not None:
            query = f"_explorer_{entity.id}"
            conn = conn.with_query(query, entity.table_sql)
        result = await conn.execute(
            query, dict(entity.params), language=language, max_rows=1,
            column_hints=list(screen.columns) if screen is not None and screen.columns else None,
            dictionary_scope=app,
        )
    except Exception as exc:  # noqa: BLE001 — reported on the entity
        view.error = f"{connector}.{entity.query or entity.table}: {exc}"
        return view
    # The dictionary scope is the APP (where the screens file the items), the data connector's
    # own section next — ``dictionary_scope=app`` above did the same for the hinted columns.
    resolve_dictionary(result.columns, getattr(connectors, "dictionary", None), app, int(getattr(conn.config, "dd_prefix", 0) or 0), language, fallback=conn.name)
    view.fields, view.warnings = fields_from_columns(entity, result.columns, result.rows[0] if result.rows else None)
    return view


PendingRelation = tuple[str, str, list[tuple[str, str]]]   # (from entity, to entity, [(from item, to item)])
PendingKeys = dict[str, list[str]]                           # entity → the preset's key data items


def apply_preset(model: ExplorerModel, preset: dict[str, Any]) -> tuple[ExplorerModel, list[PendingRelation], PendingKeys]:
    """The model completed by a Screen Assistant preset: every preset table becomes an entity
    (its id the table name, lower-cased; ``SELECT *`` on the table, or the preset's fast-columns
    ``query``), the base table the hub unless the model names one, the joins **pending**
    relations on data items — resolved against the discovered columns by
    :func:`resolve_relations` — and a table's ``key`` (data items too) the entity key, resolved
    the same way. An entity the model declares with the same id, or the same ``table``, replaces
    the derived one (that is where measures and a cardinality go; a key it leaves empty comes
    from the preset)."""
    tables = [t for t in preset.get("tables") or [] if isinstance(t, dict) and t.get("name")]
    by_table = {str(e.table).upper(): eid for eid, e in model.entities.items() if e.table}
    entities: dict[str, ExplorerEntity] = {}
    ids: dict[str, str] = {}      # preset table name (upper) → entity id
    keys: PendingKeys = {}
    for i, t in enumerate(tables):
        name = str(t["name"])
        eid = by_table.get(name.upper()) or name.lower()
        ids[name.upper()] = eid
        # a preset table may carry its own ``label`` and ``cardinality`` (a lookup joined to a
        # transaction hub is "one": a plain LEFT JOIN, not a correlated sub-query)
        derived = ExplorerEntity(
            id=eid, label=t.get("label") or name, table=None if t.get("query") else name, schema=None if t.get("query") else t.get("schema"),
            query=t.get("query"), connector=t.get("query_connector") or preset.get("connector"),
            cardinality="one" if i == 0 else (t.get("cardinality") or "many"),
        )
        declared = model.entities.get(eid)
        if t.get("key") and not (declared and declared.key):
            keys[eid] = [str(k) for k in t["key"]]
        if declared is None:
            entities[eid] = derived
            continue
        # the declared entity completes the derived one: what it leaves unset comes from the preset
        # (the connector above all — a preset is declared on a connector, the model on an app)
        fill = {k: getattr(derived, k) for k in ("connector", "query", "table", "schema_", "label") if getattr(declared, k) in (None, "")}
        if declared.query:
            fill.pop("table", None); fill.pop("schema_", None)
        entities[eid] = declared.model_copy(update=fill) if fill else declared
    for eid, e in model.entities.items():
        entities.setdefault(eid, e)
    hub = model.hub or (ids[str(tables[0]["name"]).upper()] if tables else "")
    pending: list[PendingRelation] = []
    for t in tables[1:]:
        pairs: list[tuple[str, str]] = []
        for on in t.get("on") or []:
            left, right = str(on.get("left", "")), str(on.get("right", ""))
            lt, _, li = left.rpartition(".")
            rt, _, ri = right.rpartition(".")
            if not (lt and rt and li and ri):
                continue
            a, b = ids.get(lt.upper()), ids.get(rt.upper())
            if a is None or b is None:
                continue
            # orient the pair as (this table, the other)
            this = ids[str(t["name"]).upper()]
            pairs.append((li, ri) if a == this else (ri, li))
            other = b if a == this else a
        if pairs:
            pending.append((ids[str(t["name"]).upper()], other, pairs))  # noqa: F821 — set in the loop above
    effective = model.model_copy(update={"hub": hub, "entities": entities, "relations": list(model.relations)})
    return effective, pending, keys


def resolve_column(fields: list[ExplorerField], item: str, prefix: int) -> str | None:
    """The physical column carrying data item *item*: the field named so, the field whose
    dictionary item is it, or — on a ``dd_prefix`` connector — the field whose name minus the
    prefix is it (``ABAN8`` ↔ ``AN8``)."""
    up = item.upper()
    for f in fields:
        if f.name.upper() == up:
            return f.name
    for f in fields:
        if (f.dd or "").upper() == up:
            return f.name
    if prefix:
        for f in fields:
            if len(f.name) > prefix and f.name[prefix:].upper() == up:
                return f.name
    return None


def resolve_relations(views: list[EntityView], pending: list[PendingRelation], prefix_of: dict[str, int]) -> tuple[list[ExplorerRelation], list[str]]:
    fields = {ev.entity.id: ev.fields for ev in views}
    out: list[ExplorerRelation] = []
    warnings: list[str] = []
    for a, b, pairs in pending:
        on: list[list[str]] = []
        for ia, ib in pairs:
            ca = resolve_column(fields.get(a, []), ia, prefix_of.get(a, 0))
            cb = resolve_column(fields.get(b, []), ib, prefix_of.get(b, 0))
            if ca is None or cb is None:
                warnings.append(f"preset join {a}.{ia} = {b}.{ib}: column not found on {'both' if ca is None and cb is None else a if ca is None else b}")
                continue
            on.append([ca, cb])
        if on:
            out.append(ExplorerRelation(**{"from": a, "to": b, "on": on}))
    return out, warnings


async def materialize_preset(
    connectors: ConnectorRegistry, screens: ScreensFile | None, app: str, model: ExplorerModel, preset: dict[str, Any], *,
    language: str | None,
) -> tuple[ExplorerModel, list[str]]:
    """The model filled from *preset* — the builder's "apply a preset": the derived entities, the
    hub, and the joins resolved on the discovered columns (JDE prefixes handled). The result is
    what gets saved. A model that only names its preset (one a preset ships by default) is
    filled the same way at first use — see :func:`build_model_view`."""
    model, _views, warnings = await _materialize(connectors, screens, app, model, preset, language=language)
    return model, warnings


async def _materialize(
    connectors: ConnectorRegistry, screens: ScreensFile | None, app: str, model: ExplorerModel, preset: dict[str, Any], *,
    language: str | None,
) -> tuple[ExplorerModel, list[EntityView], list[str]]:
    model, pending, keys = apply_preset(model, preset)
    order = ([model.hub] if model.hub in model.entities else []) + [eid for eid in model.entities if eid != model.hub]
    views = await asyncio.gather(*(describe_entity(connectors, screens, app, model.entities[eid], language=language) for eid in order))
    warnings: list[str] = [ev.error for ev in views if ev.error]
    prefix_of: dict[str, int] = {}
    for ev in views:
        try:
            prefix_of[ev.entity.id] = int(getattr(connectors.sql(ev.entity.connector or app).config, "dd_prefix", 0) or 0)
        except Exception:  # noqa: BLE001
            prefix_of[ev.entity.id] = 0
    fields_of = {ev.entity.id: ev.fields for ev in views}
    entities = dict(model.entities)
    for eid, items in keys.items():
        cols = [resolve_column(fields_of.get(eid, []), item, prefix_of.get(eid, 0)) for item in items]
        missing = [item for item, c in zip(items, cols, strict=True) if c is None]
        if missing:
            warnings.append(f"preset key of {eid}: column not found for {', '.join(missing)}")
        found = [c for c in cols if c is not None]
        if found and eid in entities:
            entities[eid] = entities[eid].model_copy(update={"key": found})
    model = model.model_copy(update={"entities": entities})
    resolved, w = resolve_relations(list(views), pending, prefix_of)
    warnings.extend(w)
    known = {(r.from_, r.to) for r in model.relations}
    model = model.model_copy(update={"relations": [*model.relations, *[r for r in resolved if (r.from_, r.to) not in known]]})
    try:
        model.check_complete()
    except ValueError as exc:
        warnings.append(str(exc))
    # the views describe the entities as derived; the key / relations resolved above are on the
    # model, the entity objects inside the views are re-pointed so a view's entity is the final one
    for ev in views:
        ev.entity = model.entities.get(ev.entity.id, ev.entity)
    return model, list(views), warnings


async def build_model_view(
    connectors: ConnectorRegistry, screens: ScreensFile | None, app: str, model: ExplorerModel, *, language: str | None,
    presets_dir: Path | str | None = None,
) -> ModelView:
    """Every entity described concurrently (they are independent queries). A model that names a
    preset but has no entity (one a preset ships by default, or one saved before the builder
    filled it) is materialised here from the catalogue under *presets_dir* — the result is
    cached like any view, the configured model stays untouched."""
    source = model
    warnings: list[str] = []
    if model.preset and not model.entities:
        from liberty.presets import find_preset
        preset = find_preset(presets_dir, model.preset) if presets_dir is not None else None
        if preset is None:
            warnings.append(f"the preset {model.preset!r} is named but not applied: the model has no entity")
        else:
            model, views, w = await _materialize(connectors, screens, app, model, preset, language=language)
            warnings.extend(w)
            for ev in views:
                warnings.extend(ev.warnings)
            return ModelView(model=model, app=app, entities=views, warnings=warnings, built_at=time.monotonic(), source=source)
    order = ([model.hub] if model.hub in model.entities else []) + [eid for eid in model.entities if eid != model.hub]
    views = await asyncio.gather(*(describe_entity(connectors, screens, app, model.entities[eid], language=language) for eid in order))
    for ev in views:
        warnings.extend([ev.error] if ev.error else ev.warnings)
    if model.preset:
        try:
            model.check_complete()
        except ValueError as exc:
            warnings.append(str(exc))
    return ModelView(model=model, app=app, entities=list(views), warnings=warnings, built_at=time.monotonic(), source=source)


class ModelCache:
    """Per-app, per-language cache of :class:`ModelView` with a TTL; cleared on reload."""

    def __init__(self, ttl: float = CACHE_SECONDS, presets_dir: Path | str | None = None) -> None:
        self._ttl = ttl
        self._views: dict[tuple[str, str, str], ModelView] = {}
        self._lock = asyncio.Lock()
        self.presets_dir = presets_dir   # the catalogue a preset-shipped model is built from

    def clear(self) -> None:
        self._views.clear()

    async def get(
        self, connectors: ConnectorRegistry, screens: ScreensFile | None, app: str, model: ExplorerModel, *,
        language: str | None, refresh: bool = False, presets_dir: Path | str | None = None,
    ) -> ModelView:
        key = (app, model.id, language or "")
        now = time.monotonic()
        cached = self._views.get(key)
        if cached is not None and not refresh and cached.source is model and now - cached.built_at < self._ttl:
            return cached
        async with self._lock:
            cached = self._views.get(key)
            if cached is not None and not refresh and cached.source is model and now - cached.built_at < self._ttl:
                return cached
            view = await build_model_view(connectors, screens, app, model, language=language, presets_dir=presets_dir or self.presets_dir)
            self._views[key] = view
            return view
