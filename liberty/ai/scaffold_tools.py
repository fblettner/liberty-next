"""Tier 1 scaffolding tools — connector-agnostic, propose-only.

Each tool that would *write* configuration returns a :class:`Proposal` envelope wrapped
via :func:`proposal.wrap`. The chat UI renders the envelope as a card with Apply / Skip;
the Apply button POSTs to ``/admin/config/apply-proposal`` which dispatches to the
existing parsed-config PUT endpoints. The assistant never writes to disk directly.

Tools:
    introspect_table       — read-only; returns one table's columns + types
    scaffold_crud_queries  — proposes 4 query objects (select/insert/update/delete) on a SQL connector
    scaffold_dict_entries  — proposes dictionary entries (shared or per-connector overlay)
    scaffold_screen        — proposes a screens.toml entry for an app
    scaffold_menu_item     — proposes a menu addition for an app

Gated by ``[ai] scaffold_tools`` (default off) — see :func:`liberty.ai.assistant.build_assistant`.
"""

from __future__ import annotations

from typing import Any

from liberty.ai.proposal import Proposal, wrap
from liberty.ai.tools import Tool, tool
from liberty.connectors import ConnectorRegistry, SQLConnector
from liberty.connectors.base import ConnectorError
from liberty.connectors.introspect import introspect_pool


def build_scaffold_tools(
    connectors: ConnectorRegistry,
    *,
    allowed: list[str] | None = None,
) -> list[Tool]:
    """Build the scaffold tool set. ``allowed`` mirrors the connector-tools setting —
    if non-empty, restrict the connectors the assistant can introspect / scaffold against."""
    allow = set(allowed or [])

    def _visible(name: str) -> bool:
        return not allow or name in allow

    def _sql(name: str) -> SQLConnector:
        conn = connectors.get(name)  # raises UnknownConnectorError
        if not isinstance(conn, SQLConnector):
            raise ConnectorError(f"{name!r} is not a SQL connector")
        if not _visible(name):
            raise ConnectorError(f"connector {name!r} is not available to the assistant")
        return conn

    # -- introspect (read-only, no Proposal) ---------------------------------- #

    async def introspect_table(connector: str, table: str, schema: str | None = None) -> dict[str, Any]:
        """Inspect a database table on a SQL connector — list its columns, types, and
        primary-key columns. Read-only; no proposal emitted. Call before
        scaffold_crud_queries / scaffold_dict_entries so the next call has real
        column names + types.

        Args:
            connector: SQL connector name (from list_connectors).
            table: table name (case may matter — Oracle is uppercase by convention, Postgres lowercase).
            schema: optional schema scope (Oracle owner / Postgres schema). Omit for the pool default.
        """
        conn = _sql(connector)
        # Use the connector's pool + introspect with a LIKE filter to narrow the walk
        # to the requested table only — same path the CRUD wizard uses.
        pool = connectors.pools
        # ``name_like`` is a SQL-LIKE pattern; using the exact name without wildcards filters to one.
        info = await introspect_pool(pool, conn.pool_name, only_schema=schema, name_like=table)
        for t in info.get("tables", []):
            if t.get("name", "").lower() == table.lower() and (schema is None or (t.get("schema") or "").lower() == (schema or "").lower()):
                return {
                    "connector": connector,
                    "pool": conn.pool_name,
                    "dialect": info["dialect"],
                    "schema": t.get("schema"),
                    "table": t["name"],
                    "kind": t.get("kind", "table"),
                    "columns": t.get("columns", []),
                    "primary_key": t.get("primary_key", []),
                }
        raise ConnectorError(
            f"table {schema + '.' if schema else ''}{table} not found on connector {connector!r} "
            f"(searched {len(info.get('tables', []))} tables)"
        )

    # -- helpers --------------------------------------------------------------- #

    def _fq(schema: str | None, table: str) -> str:
        return f"{schema}.{table}" if schema else table

    def _slug(s: str) -> str:
        # Mirrors the CrudWizardModal slugify so generated query names match what an
        # operator would have gotten from the UI wizard.
        out = "".join(c.lower() if c.isalnum() else "_" for c in s)
        return out.strip("_")

    def _select_sql(columns: list[str], fq: str) -> str:
        cols = ",\n  ".join(columns)
        return f"SELECT\n  {cols}\nFROM {fq}"

    def _insert_sql(columns: list[str], fq: str) -> str:
        cols = ",\n  ".join(columns)
        placeholders = ",\n  ".join(f":{c}" for c in columns)
        return f"INSERT INTO {fq} (\n  {cols}\n) VALUES (\n  {placeholders}\n)"

    def _update_sql(columns: list[str], keys: list[str], fq: str) -> str:
        non_key = [c for c in columns if c not in keys]
        sets = ",\n  ".join(f"{c} = :{c}" for c in non_key)
        where = "\n  AND ".join(f"{c} = :{c}_ORIGINAL" for c in keys)
        return f"UPDATE {fq}\nSET\n  {sets}\nWHERE\n  {where}"

    def _delete_sql(keys: list[str], fq: str) -> str:
        where = "\n  AND ".join(f"{c} = :{c}" for c in keys)
        return f"DELETE FROM {fq}\nWHERE\n  {where}"

    # -- propose CRUD queries ------------------------------------------------- #

    def scaffold_crud_queries(
        connector: str,
        table: str,
        columns: list[str],
        key_columns: list[str] | None = None,
        schema: str | None = None,
        prefix: str | None = None,
        kinds: list[str] | None = None,
    ) -> dict[str, Any]:
        """Propose SQL queries (any subset of select / insert / update / delete) for a table
        on a SQL connector. **Defaults to SELECT-only** — opt into mutation slots explicitly
        so a report-only screen doesn't accidentally scaffold write paths the operator never
        asked for. Returns a Proposal envelope; nothing is written until the operator clicks
        Apply. Run introspect_table first to get the column + key lists.

        Args:
            connector: SQL connector name.
            table: table name.
            columns: columns to include in SELECT and INSERT.
            key_columns: primary-key columns — REQUIRED if ``kinds`` includes update / delete
                (drives the WHERE clause). Optional when ``kinds`` is select-only.
            schema: optional schema for the FROM / INTO / UPDATE / DELETE prefix.
            prefix: optional query-name prefix (defaults to the table name slug).
            kinds: subset of ["select", "insert", "update", "delete"] to generate. Defaults
                to ["select"] — for editable tables pass the full list; for a report / drill-
                down only ask for ["select"].
        """
        if not _visible(connector):
            raise ConnectorError(f"connector {connector!r} is not available to the assistant")
        if not columns:
            raise ConnectorError("columns is required and must be non-empty")
        chosen = [k.lower() for k in (kinds or ["select"])]
        unknown = [k for k in chosen if k not in {"select", "insert", "update", "delete"}]
        if unknown:
            raise ConnectorError(f"unknown kind(s): {unknown} — pick from select / insert / update / delete")
        needs_keys = any(k in ("update", "delete") for k in chosen)
        if needs_keys and not key_columns:
            raise ConnectorError("key_columns is required when generating update / delete (drives the WHERE clause)")
        fq = _fq(schema, table)
        slug = _slug(prefix or table)
        builders: dict[str, dict[str, Any]] = {
            "select": {"name": f"{slug}_get",    "type": "select", "sql": _select_sql(columns, fq)},
            "insert": {"name": f"{slug}_post",   "type": "insert", "writable": True, "sql": _insert_sql(columns, fq)},
            "update": {"name": f"{slug}_put",    "type": "update", "writable": True, "sql": _update_sql(columns, key_columns or [], fq)},
            "delete": {"name": f"{slug}_delete", "type": "delete", "writable": True, "sql": _delete_sql(key_columns or [], fq)},
        }
        queries = [builders[k] for k in chosen]
        kinds_label = "SELECT" if chosen == ["select"] else "/".join(k.upper() for k in chosen)
        prop = Proposal(
            kind="connector_queries",
            summary=f"Add {len(queries)} {kinds_label} quer{'y' if len(queries) == 1 else 'ies'} for {fq} on {connector}",
            target_path="connectors.toml",
            connector=connector,
            payload={"connector": connector, "queries": queries},
            notes=[
                "Apply merges these queries into the connector's existing query list. "
                "Names that already exist will be REPLACED — review before applying.",
            ],
        )
        return wrap(prop, message=f"Proposed {len(queries)} {kinds_label} quer{'y' if len(queries) == 1 else 'ies'}; review and apply when ready.")

    # -- propose dictionary entries ------------------------------------------- #

    def scaffold_dict_entries(
        scope: str,
        entries: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Propose dictionary entries — labels + display formats for column names so the
        screen renders them nicely. ``scope`` is either ``"shared"`` (top-level dictionary,
        available to every connector) or a connector name (per-connector overlay, wins on
        collision).

        Each entry is keyed by the UPPERCASE column id (CUST_ID, AGING_BUCKET_30, …). Common
        fields:
          - label: human-facing column title (e.g. "Customer Number")
          - format: how to render the cell — one of "boolean", "date", "datetime", "timestamp",
            "jdedate", "number", "integer", "decimal", "currency", "text", "textarea",
            "password", "email", "url". (NOT "type" — the field is "format".)
          - rules: optional display rule (BOOLEAN / ENUM / LOOKUP / SEQUENCE / NN / PASSWORD)
          - rules_values: the rule's argument when rules is set
          - default: initial value when a new row is added

        See liberty/connectors/dictionary.py::DictionaryEntry for the full schema.

        Args:
            scope: "shared" or a connector name.
            entries: {ENTRY_ID: {label, format, ...}} — see fields above.
        """
        if not entries:
            raise ConnectorError("entries is required and must be non-empty")
        # Normalise keys to UPPERCASE (the runtime's case-sensitive find_entry expects this).
        normalised = {k.upper(): v for k, v in entries.items()}
        scope_label = "shared dictionary" if scope == "shared" else f"{scope!r} overlay"
        prop = Proposal(
            kind="dictionary_entries",
            summary=f"Add {len(normalised)} entries to the {scope_label}",
            target_path="dictionary.toml",
            connector=None if scope == "shared" else scope,
            payload={"scope": scope, "entries": normalised},
            notes=[
                "Apply merges into the existing dictionary; entries with the same id will be REPLACED.",
            ],
        )
        return wrap(prop, message=f"Proposed {len(normalised)} dictionary entries under {scope!r}.")

    # -- propose a screen ----------------------------------------------------- #

    def scaffold_screen(
        app: str,
        screen_id: str,
        connector: str,
        read_query: str,
        label: str | None = None,
        update_query: str | None = None,
        insert_query: str | None = None,
        delete_query: str | None = None,
        key_columns: list[str] | None = None,
        column_hints: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Propose a screen entry for screens.toml. Wires the screen to the named queries
        on the given connector; ``key_columns`` drives row identity (Excel import + edit
        lock); ``column_hints`` overrides per-column display (label / format / hidden /
        dd reference). Most fields are optional — a read-only screen just needs ``read_query``.

        Args:
            app: which app the screen belongs to (= the [screens.<app>] table; typically the connector name).
            screen_id: stable id within the app (will become the TOML key).
            connector: which connector owns the queries this screen references.
            read_query: name of the read (SELECT) query on the connector.
            label: human-facing screen title (falls back to ``screen_id`` titlecased).
            update_query / insert_query / delete_query: optional CRUD slot names on the connector.
            key_columns: row primary-key columns (must match the read query's result columns).
            column_hints: optional [{name, label?, hidden?, dd?, format?, key?}] overrides per column.
        """
        screen: dict[str, Any] = {
            "label": label or screen_id.replace("_", " ").title(),
            "connector": connector,
            "read_query": read_query,
        }
        for k, v in (("update_query", update_query), ("insert_query", insert_query), ("delete_query", delete_query)):
            if v:
                screen[k] = v
        if column_hints:
            screen["columns"] = [
                {**({k: v for k, v in c.items() if v not in (None, "")})}
                for c in column_hints
                if c.get("name")
            ]
        if key_columns:
            # When column_hints is empty, materialise hint stubs that only carry the key flag —
            # otherwise the runtime has no place to record row identity. Hints already provided
            # carry their own key flags through.
            existing = {(h.get("name") or "").upper() for h in screen.get("columns", [])}
            for k in key_columns:
                if k.upper() not in existing:
                    screen.setdefault("columns", []).append({"name": k, "key": True})
                else:
                    for h in screen.get("columns", []):
                        if (h.get("name") or "").upper() == k.upper():
                            h["key"] = True
        prop = Proposal(
            kind="screen",
            summary=f"Add screen {app}.{screen_id} ({label or screen_id})",
            target_path="screens.toml",
            app=app,
            connector=connector,
            payload={"app": app, "screen_id": screen_id, "screen": screen},
            notes=[
                "Apply replaces any existing screen with the same id under this app.",
            ],
        )
        return wrap(prop, message=f"Proposed screen {app}.{screen_id}.")

    # -- propose a menu item -------------------------------------------------- #

    def scaffold_menu_item(
        app: str,
        screen_id: str,
        label: str | None = None,
        parent_folder: str | None = None,
        type: str = "table",
    ) -> dict[str, Any]:
        """Propose a menu item that opens a screen. Appended to the app's menu under
        ``parent_folder`` (omit to append to the root). Use ``type='table'`` for a
        standard data-grid screen, ``'dashboard'`` for a dashboard target.

        Args:
            app: the app whose menu to edit ([menus.<app>]).
            screen_id: target screen id (must already exist or be in another pending proposal).
            label: menu label (defaults to the screen_id titlecased).
            parent_folder: parent folder id; omit for top-level.
            type: target kind — 'table' (default), 'dashboard', or 'http'.
        """
        item: dict[str, Any] = {
            "id": screen_id,
            "label": label or screen_id.replace("_", " ").title(),
            "type": type,
            "target": screen_id,
        }
        if parent_folder:
            item["parent"] = parent_folder
        prop = Proposal(
            kind="menu_item",
            summary=f"Add menu item {label or screen_id} under {app}" + (f" / {parent_folder}" if parent_folder else ""),
            target_path="menus.toml",
            app=app,
            payload={"app": app, "item": item},
            notes=[
                "Apply appends the item to the app's menu. If an item with the same id already exists, it will be REPLACED.",
            ],
        )
        return wrap(prop, message=f"Proposed menu item for {screen_id}.")

    return [
        tool(introspect_table, summary_keys=("connector", "table")),
        tool(scaffold_crud_queries, summary_keys=("connector", "table")),
        tool(scaffold_dict_entries, summary_keys=("scope",)),
        tool(scaffold_screen, summary_keys=("app", "screen_id")),
        tool(scaffold_menu_item, summary_keys=("app", "screen_id")),
    ]
