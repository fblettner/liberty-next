"""Response models of the main read routes — documentation only (C12).

Each route declares its model under ``responses={200: {"model": …}}``, never ``response_model=``: the
schema reaches OpenAPI (``/docs``, the Developer reference's API view) while the route keeps returning
its dict untouched — nothing is filtered or re-validated at run time. ``tests/test_response_models.py``
calls the routes and validates their JSON against these models, so the documentation can't drift: a
top-level key a route adds without its model fails there.

Top-level shapes are closed (``extra="forbid"``); the items nested in them (a query, a column, a
screen's dialog…) are open — their optional keys depend on the configuration (a column's ``format``,
``rule``, ``lookup``…), the main ones are typed here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


# ── connectors ─────────────────────────────────────────────────────────────────────────────────

class QueryInfo(_Open):
    """A named query the caller may run (never its SQL)."""
    name: str
    label: str | None = None
    description: str | None = None
    writable: bool = False
    statement_type: str | None = Field(default=None, description="SELECT / INSERT / UPDATE / DELETE / MERGE / CALL…")
    dialects: list[str] = Field(default_factory=list, description="The dialects it has a variant for (``default``, ``oracle``…).")
    params: list[dict[str, Any]] = Field(default_factory=list, description="The declared parameters (label, default, type).")
    bind_params: list[str] = Field(default_factory=list, description="The ``:NAME`` binds found in its SQL.")
    crud: str | None = Field(default=None, description="On a table slot: get / put / post / delete.")


class TableInfo(_Open):
    """A declared table with the CRUD queries the caller may run on it."""
    name: str
    label: str | None = None
    description: str | None = None
    slots: list[QueryInfo] = Field(default_factory=list)


class ConnectorInfo(_Open):
    """A connector as the caller sees it: a SQL connector's queries (flat, then by section) or an
    API connector's endpoints — permission-filtered, without SQL, credentials or URLs of pools."""
    name: str
    type: Literal["sql", "api"]
    queries: list[QueryInfo] = Field(default_factory=list, description="SQL: every permitted query, name-sorted (table slots included).")
    tables: list[TableInfo] = Field(default_factory=list)
    customs: list[QueryInfo] = Field(default_factory=list, description="SQL: the custom queries (not a table slot).")
    sequences: list[QueryInfo] = Field(default_factory=list)
    lookups: list[QueryInfo] = Field(default_factory=list)
    pools: list[str] = Field(default_factory=list, description="SQL: the pool names it may run against (multi-environment picker).")
    base_url: str | None = Field(default=None, description="API: the base URL.")
    auth_type: str | None = Field(default=None, description="API: none / basic / bearer / api_key / oauth2.")
    endpoints: list[dict[str, Any]] = Field(default_factory=list, description="API: the permitted endpoints.")
    show_in_switcher: bool = True


class ConnectorList(_Closed):
    connectors: list[ConnectorInfo]


class ResultColumn(_Open):
    """A result column with its resolved display hints (label, format, rule, key, filter… as the
    screen and the dictionary set them; the keys present depend on them)."""
    name: str
    type: str | None = Field(default=None, description="The driver's type, when it reports one (absent on a screen's column hints).")
    label: str | None = None
    key: bool = False
    hidden: bool = False
    filter: bool = False
    dd: str | None = None
    format: str | None = None
    decimals: int | None = None
    rule: dict[str, Any] | None = Field(default=None, description="BOOLEAN / ENUM / LOOKUP / SEQUENCE … with its resolved values.")


class QueryResultOut(_Closed):
    """A query's result: the rows of a SELECT, the affected count of a write."""
    connector: str
    query: str
    statement_type: str
    columns: list[ResultColumn]
    rows: list[dict[str, Any]]
    row_count: int = Field(description="Rows returned (SELECT) or affected (write).")
    rowcount: int = Field(description="The driver's affected-row count; -1 for a SELECT.")
    truncated: bool = Field(description="The row cap (``_limit`` / the screen's max_rows) cut the result.")
    duration_ms: float
    resolved_binds: dict[str, Any] = Field(description="Values the server filled in for empty binds (a sequence-assigned key on an insert).")


class ApiResultOut(_Closed):
    """An API endpoint's call."""
    connector: str
    endpoint: str
    success: bool
    status_code: int
    url: str
    json_: Any = Field(default=None, alias="json", description="The parsed JSON body, when the response was JSON.")
    body: str | None = Field(default=None, description="The raw body, when it was not JSON.")
    extracted: Any = Field(default=None, description="The endpoint's ``extract`` path applied to the JSON.")
    mapped: dict[str, Any] = Field(default_factory=dict, description="The endpoint's ``map`` of fields.")
    error: str | None = None
    duration_ms: float


class PoolSchemas(_Closed):
    pool: str
    dialect: str
    schemas: list[str]
    schema_map: dict[str, str] = Field(description="The pool's ``#SCHEMA.<KEY>#`` tokens → real schema names.")


class IntrospectedTable(_Open):
    name: str
    kind: str = Field(description="table / view")
    schema_: str | None = Field(default=None, alias="schema", description="Spelled out when the pool has several schemas.")
    columns: list[dict[str, Any]] = Field(default_factory=list, description="``{name, type, nullable}``.")


class PoolSchema(_Closed):
    pool: str
    dialect: str
    tables: list[IntrospectedTable]
    truncated: bool


class ImportRowResult(_Closed):
    index: int
    action: str
    ok: bool
    rowcount: int | None = None
    error: Any = None


class ImportResult(_Closed):
    committed: bool
    mode: str
    valid: int
    invalid: int
    results: list[ImportRowResult]


class ExternalUrls(_Closed):
    urls: dict[str, str] = Field(description="The install's named addresses (``[app] external_urls``), used as ``#URL.NAME#``.")


# ── menus ──────────────────────────────────────────────────────────────────────────────────────

class MenuNode(_Open):
    """A menu folder (``items``) or leaf (``type`` / ``connector`` / ``target`` / ``params``), its label
    in the request's language."""
    id: str
    label: str
    icon: str | None = None
    items: list[MenuNode] | None = None
    type: str | None = Field(default=None, description="screen / query / endpoint / dashboard / page / job / url")
    connector: str | None = None
    target: str | None = None
    params: dict[str, Any] | None = None


class AppMenu(_Closed):
    app: str
    label: str
    items: list[MenuNode]
    show_in_switcher: bool
    home_path: str | None = Field(default=None, description="The route of the app's home item, when the caller may see it.")


class MenusOut(_Closed):
    menus: dict[str, AppMenu]


# ── screens ────────────────────────────────────────────────────────────────────────────────────

class _ScreenSummaryFields(BaseModel):
    id: str
    app: str
    label: str
    description: str | None = None
    connector: str = Field(description="The effective connector (the screen's, else the app's).")
    read_query: str
    update_query: str | None = None
    insert_query: str | None = None
    delete_query: str | None = None
    auto_load: bool
    audit: bool
    audit_table: str | None = None
    max_rows: int | None = None
    page_size: int | None = None
    key_columns: list[str]
    editable: bool
    editable_when: list[dict[str, Any]]
    uploadable: bool
    initial_group_by: list[str]
    treeview: dict[str, Any] | None = None
    chart_id: str | None = None
    has_dialog: bool
    has_row_menu: bool
    has_actions: bool
    has_columns: bool
    has_views: bool
    has_summary: bool
    summary: dict[str, Any] | None = None
    dd_map: dict[str, str] = Field(description="Dictionary item → this screen's column (dashboard filters).")
    row_click_screen: str | None = None
    row_click_connector: str | None = None
    row_click_binds: list[dict[str, Any]]
    row_click_route: str | None = None


class ScreenSummary(_ScreenSummaryFields):
    """The compact screen descriptor; a ``has_*`` flag tells the grid to fetch the full body."""
    model_config = ConfigDict(extra="forbid")


class ScreenDetail(_ScreenSummaryFields):
    """The full screen: the summary plus its configuration (``screens.toml``'s Screen) — ``columns``
    resolved through the dictionary, ``dialog`` (tabs, fields, hooks), ``actions``, ``row_menu``,
    ``on_insert`` / ``on_update`` / ``on_delete`` / ``on_duplicate``, ``views``, ``column_groups``…"""
    model_config = ConfigDict(extra="allow")
    columns: list[ResultColumn] = Field(default_factory=list)
    dialog: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    row_menu: list[dict[str, Any]] = Field(default_factory=list)


class ScreensOut(_Closed):
    screens: dict[str, list[ScreenSummary]] = Field(description="Per app, the screens the caller may read.")


class AppScreensOut(_Closed):
    app: str
    screens: list[ScreenSummary]
