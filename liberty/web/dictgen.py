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
from liberty.connectors.introspect import introspect_pool, resolve_schema_token

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
    jde_dd_table_query: str = Field(
        default="dictionary_data_items_for_table_get",
        description=(
            "Optional per-table JDE DD query that enriches the scan with format / rule / default / "
            "justify. Bound with ``:TBL_DB_NAME`` + ``:TBL_SCHEMA`` (the scanned table + its owner) and "
            "read for the columns ``DD_ID, DD_LABEL, DD_TYPE, DD_RULES, DD_RULES_VALUES, DD_DEFAULT, "
            "DD_JUSTIFY`` (the last from F9210.FRDRUL — *RAB/*RABN/*RAZ). Use a ``#SCHEMA.<KEY>#`` token "
            "(not a bind) for the F92xx schema. Missing / erroring → ignored."
        ),
    )


def _dictionary_path(settings: Any) -> Path:
    explicit = settings.connectors.dictionary_path
    return Path(explicit) if explicit else Path(settings.connectors.config_path).with_name("dictionary.toml")


def _map_jde_justify(frdrul: str | None) -> str | None:
    """JDE F9210.FRDRUL → the dictionary ``justify`` setting. ``*RAB`` / ``*RABN`` (right-adjust, blank
    fill) → ``right_blank``; ``*RAZ`` (right-adjust, zero fill) → ``right_zero``; anything else (incl.
    left-justified / unset) → None."""
    v = (frdrul or "").strip().upper()
    if v in ("*RAB", "*RABN"):
        return "right_blank"
    if v == "*RAZ":
        return "right_zero"
    return None


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


async def _fetch_jde_dd_table(
    registry: Any, conn_name: str, query_name: str, *, table: str, owner: str | None,
) -> dict[str, dict[str, Any]]:
    """Per-table JDE DD enrichment: ``{DATA_ITEM (upper) → {label, format, rules, rules_values,
    default}}`` from *query_name*, bound with the scanned table + owner. The query is expected to
    return ``DD_ID`` (the stripped data item) + ``DD_LABEL`` / ``DD_TYPE`` / ``DD_RULES`` /
    ``DD_RULES_VALUES`` / ``DD_DEFAULT``. Best-effort — a missing query / unreachable source / wrong
    shape yields an empty map (the scan then falls back to the DTAI/DSCR labels + inferred format)."""
    try:
        conn = registry.sql(conn_name)
    except ConnectorError:
        return {}
    try:
        res = await conn.execute(query_name, params={"TBL_DB_NAME": table, "TBL_SCHEMA": owner})
    except (ConnectorError, SQLAlchemyError, KeyError, ValueError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in res.rows:
        m = {(k.upper() if isinstance(k, str) else k): v for k, v in row.items()}
        ddid = m.get("DD_ID")
        if ddid is None:
            continue
        key = str(ddid).strip().upper()
        if not key or key in out:
            continue
        def _s(col: str) -> str | None:
            v = m.get(col)
            return str(v).strip() if v is not None and str(v).strip() != "" else None
        def _i(col: str) -> int | None:
            s = _s(col)
            try:
                return int(s) if s is not None else None
            except ValueError:
                return None
        out[key] = {
            "label": _s("DD_LABEL"), "format": _s("DD_TYPE"), "rules": _s("DD_RULES"),
            "rules_values": _s("DD_RULES_VALUES"), "default": _s("DD_DEFAULT"),
            "justify": _map_jde_justify(_s("DD_JUSTIFY")),  # F9210.FRDRUL → right_blank / right_zero
            "size": _i("DD_SIZE"),                          # F9210.FRDTAS — data item width (MCU = 12)
        }
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
    real_schema: str | None = None   # the table's resolved owner — also bound into the JDE DD query
    if body.table:
        # Resolve a portable ``#SCHEMA.<KEY>#`` token to the pool's real owner before introspecting
        # (the table picker hands back the token when the pool maps one).
        real_schema = resolve_schema_token(body.db_schema, registry.pools.schemas(conn.pool_name)) if body.db_schema else None
        try:
            info = await introspect_pool(registry.pools, conn.pool_name, only_schema=real_schema, name_like=body.table)
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
    # Per-table enrichment (format / rule / default straight from the JDE DD), keyed by data item.
    # Runs on the SCANNED connector (the F92xx DD tables live in the same Oracle DB as the table +
    # ALL_TAB_COLUMNS). Only in table mode (bound by table + owner); best-effort, empty when missing.
    rich_map = (
        await _fetch_jde_dd_table(
            registry, body.connector, body.jde_dd_table_query, table=body.table, owner=real_schema,
        )
        if (is_jde and body.table and body.jde_dd_table_query)
        else {}
    )

    items: list[dict[str, Any]] = []
    for name, ctype in cols:
        if not name:
            continue
        # Dictionary ids are UPPERCASE by convention (the editors uppercase column ``name`` / ``dd``
        # via x_case, and ``find_entry`` is case-sensitive) — so a screen column ``CLA_ACTION`` only
        # resolves an entry keyed ``CLA_ACTION``. Postgres/SQLite hand back lowercase column names,
        # so we uppercase here; otherwise the scan would mint entries that never resolve.
        physical = name.upper()
        # JDE physical columns are ``<2-char table prefix> + <data item>`` (``FRDTAI`` → ``DTAI``).
        # The data item is SHARED across tables, so the dictionary entry is keyed by the stripped item
        # (and queries alias the physical column back to it). Non-JDE: the id is the column itself.
        data_item = physical[2:] if (is_jde and len(physical) > 2) else None
        dd_id = data_item if data_item else physical
        rich = rich_map.get(dd_id) or {}
        # Prefer the per-table JDE DD enrichment (label / format / rule / default); fall back to the
        # DTAI/DSCR label + the SQL-type-inferred format when the rich query isn't available.
        label = rich.get("label") or (dd_map.get(data_item) if data_item else None)
        items.append({
            "column": name,
            "dd_id": dd_id,
            "exists": dd_id in existing,
            "type": ctype,
            "data_item": data_item,
            "source": "jde" if (rich or label) else "inferred",
            "label": label or None,
            "format": rich.get("format") or _infer_format(ctype),
            "rules": rich.get("rules"),
            "rules_values": rich.get("rules_values"),
            "default": rich.get("default"),
            "justify": rich.get("justify"),
            "size": rich.get("size"),
        })

    return {"scope": scope, "dialect": dialect, "jde": is_jde, "items": items}
