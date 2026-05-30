"""``POST /admin/dictionary/scan`` — propose dictionary items for a table's columns.

The v1 "reverse a table" reflex: after scaffolding a table's CRUD queries, most of its columns
need a dictionary entry (label + format) and typing them by hand is busywork. This scans the
table's columns, flags the ones with no dictionary entry yet, and fills a proposed definition:

* **JDE source** (Oracle / ``apps_type=JDE``): the column is a JDE alias — ``<2-char table
  prefix> + <data item>`` (e.g. ``ABAN8`` → data item ``AN8``). We strip the prefix and look the
  data item up in the JDE data dictionary (F9200/F9202/F9210 via the ``nomajde`` connector's
  ``dictionary_data_items_get`` query) to get its description as the label.
* **Non-JDE**: the label is left blank for the operator to fill; the format is inferred from the
  column's SQL type (number / date / boolean / text).

Superuser only (it leaks a pool's schema, like the ``_schema`` introspection route). It does NOT
write anything — it returns proposals; the frontend lets the operator edit + then PUTs the chosen
entries through the normal ``/admin/config/dictionary/parsed`` save.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.connectors.base import ConnectorError
from liberty.connectors.dictionary import load_dictionary
from liberty.connectors.introspect import introspect_pool

router = APIRouter(prefix="/admin", tags=["dictionary"])

Superuser = Annotated[Principal, Depends(require_superuser)]


class ScanBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    connector: str = Field(description="Source connector being scanned.")
    # Exactly one of `table` / `query`:
    #  * table — introspect the DB table directly (the reverse wizard: the queries aren't saved yet).
    #  * query — describe an *already-reversed* read query's result columns (standalone: scan a
    #    screen/query you've already built, without re-listing every table in the schema).
    table: str | None = Field(default=None, description="DB table (or view) to introspect.")
    query: str | None = Field(default=None, description="A saved read query whose result columns are scanned.")
    db_schema: str | None = Field(default=None, alias="schema", description="Optional schema to scope table introspection.")
    scope: str | None = Field(default=None, description="Dictionary scope (connectors.<scope>.entries). Defaults to `connector`.")
    jde: bool | None = Field(default=None, description="Treat the source as JDE. Default: inferred from the pool dialect (oracle).")
    jde_dd_connector: str = Field(default="nomajde", description="Connector hosting the JDE data dictionary.")
    jde_dd_query: str = Field(default="dictionary_data_items_get", description="Named query returning DTAI/DSCR rows.")


def _dictionary_path(settings: Any) -> Path:
    explicit = settings.connectors.dictionary_path
    return Path(explicit) if explicit else Path(settings.connectors.config_path).with_name("dictionary.toml")


def _infer_format(type_str: str | None) -> str | None:
    """Map a SQL column type string to a Liberty display format (best-effort; None = plain text)."""
    t = (type_str or "").upper()
    if any(k in t for k in ("INT", "NUMERIC", "NUMBER", "DECIMAL", "FLOAT", "DOUBLE", "REAL", "MONEY", "DEC")):
        return "number"
    if "BOOL" in t:
        return "boolean"
    if any(k in t for k in ("TIMESTAMP", "DATETIME", "DATE", "TIME")):
        return "date"
    return None


async def _fetch_jde_dd(registry: Any, conn_name: str, query_name: str) -> dict[str, str]:
    """``{DATA_ITEM (upper) → description}`` from the JDE DD. Best-effort: a missing connector /
    query / unreachable source just yields an empty map (JDE labels then come back blank)."""
    try:
        conn = registry.sql(conn_name)
    except ConnectorError:
        return {}
    try:
        res = await conn.execute(query_name)
    except (ConnectorError, SQLAlchemyError, KeyError, ValueError):
        return {}
    out: dict[str, str] = {}
    for row in res.rows:
        m = {(k.upper() if isinstance(k, str) else k): v for k, v in row.items()}
        dtai, dscr = m.get("DTAI"), m.get("DSCR")
        if dtai is not None:
            key = str(dtai).strip().upper()
            if key and key not in out:
                out[key] = (str(dscr).strip() if dscr is not None else "")
    return out


@router.post("/dictionary/scan", summary="Scan dictionary")
async def scan_dictionary(body: ScanBody, request: Request, _: Superuser) -> dict[str, Any]:
    registry = request.app.state.connectors
    if bool(body.table) == bool(body.query):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Provide exactly one of `table` or `query`.")
    try:
        conn = registry.sql(body.connector)
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    # Resolve (name, type) column pairs + the pool dialect, from whichever source was given.
    cols: list[tuple[str, str | None]]
    if body.table:
        try:
            info = await introspect_pool(registry.pools, conn.pool_name, only_schema=body.db_schema, name_like=body.table)
        except SQLAlchemyError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"schema introspection failed: {exc}") from exc
        table = next((t for t in info.get("tables", []) if str(t.get("name", "")).lower() == body.table.lower()), None)
        if table is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"table {body.table!r} not found on {body.connector!r}")
        dialect = info.get("dialect")
        cols = [(str(c.get("name", "")), c.get("type")) for c in table.get("columns", [])]
    else:
        # Describe an already-reversed read query — max_rows=0 is the cheap column-only fetch the
        # frontend's ``?_limit=0`` introspection uses. A query that needs params will raise; surface
        # it as a 400 so the operator picks a parameter-free read query.
        try:
            res = await conn.execute(body.query, max_rows=0)  # type: ignore[arg-type]
        except ConnectorError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (SQLAlchemyError, ValueError, KeyError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"could not describe query {body.query!r}: {exc}") from exc
        try:
            dialect = registry.pools.dialect(conn.pool_name)
        except Exception:  # noqa: BLE001 — dialect lookup is advisory (drives JDE auto-detect)
            dialect = None
        cols = [(c.name, c.type) for c in res.columns]

    is_jde = body.jde if body.jde is not None else (dialect == "oracle")

    # Existing dictionary ids in scope (scoped + shared), case-insensitive.
    scope = body.scope or body.connector
    dcfg = load_dictionary(_dictionary_path(request.app.state.settings))
    existing = {k.upper() for k in dcfg.entries}
    sc = dcfg.connectors.get(scope)
    if sc is not None:
        existing |= {k.upper() for k in sc.entries}

    dd_map = await _fetch_jde_dd(registry, body.jde_dd_connector, body.jde_dd_query) if is_jde else {}

    items: list[dict[str, Any]] = []
    for name, ctype in cols:
        if not name:
            continue
        # Dictionary ids are UPPERCASE by convention (the editors uppercase column ``name`` / ``dd``
        # via x_case, and ``find_entry`` is case-sensitive) — so a screen column ``CLA_ACTION`` only
        # resolves an entry keyed ``CLA_ACTION``. Postgres/SQLite hand back lowercase column names,
        # so we uppercase the dd id here; otherwise the scan would mint entries that never resolve.
        dd_id = name.upper()
        data_item = dd_id[2:] if (is_jde and len(dd_id) > 2) else None
        label = dd_map.get(data_item) if data_item else None
        items.append({
            "column": name,
            "dd_id": dd_id,
            "exists": dd_id in existing,
            "type": ctype,
            "data_item": data_item,
            "source": "jde" if label else "inferred",
            "label": label or None,
            "format": _infer_format(ctype),
        })

    return {"scope": scope, "dialect": dialect, "jde": is_jde, "items": items}
