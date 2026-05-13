"""TOML schema for ``config/connectors.toml`` — pools and connector definitions.

This is the v2 replacement for v1's ``ly_qry_sql`` / ``ly_api`` / ``ly_api_conn``
metadata tables: executable definitions live in a hot-reloadable file on disk,
not in the database. The result *schema* is still discovered at query time
(``cursor.description``), never stored here.

Example::

    [pools.default]
    url = "postgresql+asyncpg://liberty:liberty@localhost/liberty"

    [connectors.liberty]
    type = "sql"
    pool = "default"

    [[connectors.liberty.queries]]
    name = "users_list"
    sql = "SELECT usr_id, usr_name FROM ly_users WHERE usr_status = :status"
    writable = false
    params = [{ name = "status", default = "ENABLED" }]

    [connectors.github]
    type = "api"
    base_url = "https://api.github.com"
    auth_type = "bearer"
    auth_token = "${GITHUB_TOKEN}"
    default_headers = { Accept = "application/vnd.github+json" }

    [[connectors.github.endpoints]]
    name = "get_repo"
    method = "GET"
    path = "/repos/{{owner}}/{{repo}}"
    params = [{ name = "owner" }, { name = "repo" }]
    response_field = "full_name"
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from liberty.config import substitute_env

# Connector auth configs must reference secrets, never inline them — an
# unresolved ``${NAME}`` becomes the empty string (see :func:`substitute_env`)
# so a missing secret fails loudly at call time rather than using literal text.

# --------------------------------------------------------------------------- #
# Pools
# --------------------------------------------------------------------------- #


class PoolConfig(BaseModel):
    """A named database pool — one SQLAlchemy async engine per entry. (Field docs are in
    ``description=`` so the config-builder UI shows them as form hints; see ``GET /admin/config/schema``.)"""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(description=(
        "SQLAlchemy *async* URL — e.g. postgresql+asyncpg://user@host:5432/db, "
        "oracle+oracledb://user@host:1521/?service_name=ORCL, sqlite+aiosqlite:///./file.db. "
        "Supports ${ENV} / ${ENV:-default} refs. A URL-special password (@ / : / …) belongs in the "
        "separate `password` field, not here."
    ))
    password: str | None = Field(
        default=None,
        description=(
            "DB password kept *out of the URL* — substituted in (escaped) when the engine is built. May be "
            "an ENC: value (decrypted at runtime via the crypto master key, like v1's apps_password), plain "
            "text, or a ${ENV} ref. Leave empty if it's already in the URL (an ENC: password in the URL is "
            "still decrypted)."
        ),
        json_schema_extra={"format": "password"},
    )
    dialect: str = Field(
        default="",
        description=(
            "SQLAlchemy backend name (postgresql / oracle / sqlite / mysql / mssql / …). Empty → derived from "
            "the URL. Used to pick a query's per-dialect SQL variant."
        ),
        json_schema_extra={"x_enum_ref": "DATASOURCE_TYPE"},
    )
    schemas: dict[str, str] = Field(default_factory=dict, json_schema_extra={"x_group": "Schemas"}, description=(
        "Schema-name map for `#SCHEMA.<NAME>#` placeholders in this pool's queries → the real schema name "
        "(v1's ly_db_schema). Values must be plain identifiers (SY920, db.schema). Lets the same query "
        "target dev vs prod schemas — or several schemas under one DB user — without editing the SQL. "
        "Name lookup is case-insensitive."
    ))
    pool_size: int = Field(default=5, description="Persistent connections kept in the pool.", json_schema_extra={"x_group": "Pool"})
    max_overflow: int = Field(default=10, description="Extra connections allowed beyond `pool_size` under load.", json_schema_extra={"x_group": "Pool"})
    pool_pre_ping: bool = Field(default=True, description="Test a connection's liveness before handing it out (recovers from dropped connections).", json_schema_extra={"x_group": "Pool"})
    pool_recycle: int = Field(default=-1, description="Recycle a connection after this many seconds; -1 = never.", json_schema_extra={"x_group": "Pool"})
    echo: bool = Field(default=False, description="Log every SQL statement (debug only — very noisy).", json_schema_extra={"x_group": "Pool"})
    max_rows: int | None = Field(default=None, json_schema_extra={"x_group": "Pool"}, description=(
        "Default cap on rows a SELECT returns on this pool (v1's per-app apps_limit). Empty → no pool-level "
        "cap. A connector's max_rows, a query's max_rows, or a per-request override each take precedence in "
        "that order; the absolute fallback is 1000."
    ))


# --------------------------------------------------------------------------- #
# Shared bits
# --------------------------------------------------------------------------- #


class ParamDef(BaseModel):
    """A declared parameter — gives a `:name` placeholder a UI label and a default value."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The `:name` placeholder in the SQL / `{{name}}` in the endpoint.")
    label: str | None = Field(default=None, description="Form label for the parameter input (defaults to the name).")
    default: str | None = Field(default=None, description="Pre-filled value; blank means the caller omits it (→ SQL NULL for a query).")


class FilterDep(BaseModel):
    """One cascading-filter dependency for a column's TableView filter dropdown (v1's
    ``ly_tbl_filters`` / ``ly_dlg_filters`` row). When the ``source`` filter (another
    ``filter``-flagged column on the same query) has a value, this column's LOOKUP options
    are narrowed to the rows whose ``column`` (a column of *that lookup query's* result)
    equals the source's value — i.e. pick an App in one filter, the Role dropdown shows only
    that App's roles. (v1's ``flt_source`` / ``flt_target``.) Purely a frontend behaviour:
    the lookup query itself is unchanged. Ignored for non-LOOKUP columns."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        description="Another filter-flagged column on the same query — its current value is the input.",
        json_schema_extra={"x_enum_ref": "DD_ENTRIES"},
    )
    column: str = Field(
        description="A result column of *this* column's lookup query to match the source's value against.",
        json_schema_extra={"x_enum_ref": "DD_ENTRIES"},
    )


class VisibleWhen(BaseModel):
    """One conditional-visibility rule for a TableView grid column (v1's ``ly_tbl_col.cdn_*``).
    A column may carry a single rule or a list of them (``ColumnHint.visible_when``) — *all* must
    hold for the column to appear, and a rule holds when the named ``field`` server-filter is unset
    or its value is ``value`` (or one of ``value`` when it's a list). So setting ``field`` to a value
    outside the allowed set drops the *whole column* from the grid. (A table has no per-row "current
    value", so this is the table form of v1's conditional rendering; forms/dialogs get the per-record
    version in the form phase.) ``field`` should be a ``filter``-flagged column on the same query."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="A filter-flagged column on the same query whose value gates this column's visibility.")
    value: str | list[str] = Field(description="The allowed value (or list of values); the column hides when `field` is set to something else.")

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field, "value": self.value}


class ColumnHint(BaseModel):
    """Optional *display* metadata for one result column. The column **schema**
    (names + types) is still discovered from the query at run time — these hints only
    augment it (a display title, visibility, column order, a width/alignment, a
    free-text ``format`` the UI may interpret, a ``filter`` flag — v1's ``col_filter`` —
    that surfaces the column in the TableView filter panel, ``filter_from`` — v1's ``ly_tbl_filters`` —
    cascading-filter dependencies for that panel, and ``visible_when`` — v1's ``ly_tbl_col.cdn_*`` —
    a condition that drops the whole column from the grid unless a server-filter has a given value).
    ``label``/``format``
    may be left out and pulled from the shared dictionary (``config/dictionary.toml``) instead:
    the entry key is ``dd`` if set, else the column ``name``; an inline ``label``/``format`` here
    still overrides the dictionary. v1's ``ly_tbl_col`` / ``ly_dlg_col`` rows migrate to
    this shape (``dd`` = v1's ``col_dd_id``). A hint for a column the query doesn't return
    is ignored. The order of the ``columns`` list is the display order; columns with no
    hint keep their discovery order and follow the hinted ones.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The result column this hint applies to (matched case-insensitively; a hint for a column the query doesn't return is ignored).")
    dd: str | None = Field(
        default=None,
        description="Dictionary-entry key for the label/format/rule (config/dictionary.toml). Blank → looked up under `name`; set to \"\" to opt this column out of the dictionary.",
        json_schema_extra={"x_enum_ref": "DD_ENTRIES"},
    )
    label: str | None = Field(default=None, description="Display title — overrides the dictionary's label.")
    hidden: bool = Field(default=False, description="Hide this column in the grid by default (the user can still un-hide it via the Columns menu).")
    filter: bool = Field(default=False, json_schema_extra={"x_group": "Filtering"}, description="Surface this column as a server-filter field in the TableView filter panel (v1's col_filter).")
    filter_from: list[FilterDep] = Field(default_factory=list, json_schema_extra={"x_group": "Filtering"}, description="Cascading-filter dependencies (v1's ly_tbl_filters) — narrow this column's LOOKUP options when a source filter is set.")
    visible_when: VisibleWhen | list[VisibleWhen] | None = Field(default=None, json_schema_extra={"x_group": "Filtering"}, description="Conditional visibility (v1's cdn_*): a rule (or list of rules, all AND-ed) — the column drops from the grid unless a server-filter has the allowed value.")
    width: int | None = Field(default=None, description="Fixed column width in pixels (blank → auto-size to content).")
    align: Literal["left", "right", "center"] | None = Field(
        default=None,
        description='"left" / "right" / "center" — blank auto-aligns (numbers right, booleans centred).',
        json_schema_extra={"x_enum_ref": "COLUMN_ALIGN"},
    )
    format: str | None = Field(
        default=None,
        description='UI-interpreted format hint — "date" / "datetime" / "number" / "boolean" / "currency" / … — overrides the dictionary\'s. Free-text — the frontend tolerates v1\'s various aliases.',
        json_schema_extra={"x_enum_ref": "DICTIONARY_TYPE"},
    )

    @property
    def visible_when_rules(self) -> list[VisibleWhen]:
        """``visible_when`` normalised to a list (a single rule → ``[rule]``; unset → ``[]``)."""
        if self.visible_when is None:
            return []
        return [self.visible_when] if isinstance(self.visible_when, VisibleWhen) else list(self.visible_when)

    @property
    def dictionary_key(self) -> str:
        """The dictionary entry to consult for an un-set ``label``/``format`` (``dd`` or, if
        ``dd`` is the empty string, none — set ``dd = ""`` to opt a column out of the dictionary)."""
        return self.name if self.dd is None else self.dd


# --------------------------------------------------------------------------- #
# SQL connector
# --------------------------------------------------------------------------- #


class QueryDef(BaseModel):
    """A named SQL query with ``:name`` placeholders.

    ``sql`` is either a single statement (the common case) or a per-dialect map —
    e.g. ``sql = { default = "…", oracle = "…" }`` — keyed by SQLAlchemy backend
    name; the connector picks the variant matching its pool's database, falling
    back to ``default`` (which is required when ``sql`` is a map). v1's per-``dbtype``
    SQL variants migrate to this shape.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Unique name within the connector; the permission string is sql:<connector>:<name>.")
    sql: str | dict[str, str] = Field(description="The SQL statement (`:name` placeholders). Either one string, or a per-dialect map { default = \"…\", oracle = \"…\" } keyed by SQLAlchemy backend name (the connector picks the variant matching its pool, falling back to `default`, which is then required).")
    writable: bool = Field(default=False, description="Allow non-SELECT statements (INSERT/UPDATE/DELETE/…). Required for any mutating query — plus the caller must hold the permission.")
    params: list[ParamDef] = Field(default_factory=list, json_schema_extra={"x_group": "Params"}, description="Declared parameters — give a `:name` placeholder a form label and a default.")
    columns: list[ColumnHint] = Field(default_factory=list, json_schema_extra={"x_group": "Columns"}, description="Display hints for the result columns (label/visibility/order/filter/…). The column *schema* is still discovered from the query at run time — these only augment it; order here = display order.")
    label: str | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="Friendly name for the query (shown in listings).")
    description: str | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="Screen title in the TableView (falls back to `label`, then the menu label).")
    auto_load: bool = Field(default=False, json_schema_extra={"x_group": "Advanced"}, description="Run this query immediately when the TableView opens — no \"Run\" click (v1's per-table auto-load). Safe even with params: an omitted `:name` becomes SQL NULL.")
    max_rows: int | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="Per-query SELECT row cap (else the connector's, then the pool's, then 1000). A per-request `?_limit` override beats this.")
    key_columns: list[str] = Field(default_factory=list, json_schema_extra={"x_group": "Advanced"}, description="Result columns that identify a row (v1's col_key) — the TableView's Excel import matches imported rows against loaded ones on these to decide update-vs-insert.")
    update_query: str | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="A `writable` query on this connector that UPDATEs a row of this result (the TableView's batch-edit). Blank → auto-derived from the `<base>_get` → `<base>_put` naming convention.")
    insert_query: str | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="A `writable` query that INSERTs a new row. Blank → auto-derived (`<base>_post`).")
    delete_query: str | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="A `writable` query that DELETEs a row. Blank → auto-derived (`<base>_delete`).")

    @field_validator("sql")
    @classmethod
    def _require_default(cls, v: str | dict[str, str]) -> str | dict[str, str]:
        if isinstance(v, dict):
            if "default" not in v:
                raise ValueError("a per-dialect sql map must include a 'default' key")
            if not v.get("default", "").strip():
                raise ValueError("the 'default' sql variant must not be empty")
        return v

    def sql_for(self, dialect: str | None) -> str:
        """The SQL to run on a pool of *dialect* (falls back to ``default``)."""
        if isinstance(self.sql, str):
            return self.sql
        return self.sql.get(dialect or "", self.sql["default"])

    @property
    def default_sql(self) -> str:
        """The dialect-independent variant — used for statement-type / bind-param introspection."""
        return self.sql if isinstance(self.sql, str) else self.sql["default"]

    @property
    def dialects(self) -> list[str]:
        return ["default"] if isinstance(self.sql, str) else list(self.sql)


class SqlConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["sql"]
    pool: str = Field(default="default", description="Which [pools.*] this connector's queries run on.")
    licensed: bool = Field(default=False, description="Gate this connector behind a valid [license] key — without one (the open framework) it isn't loaded.")
    max_rows: int | None = Field(default=None, description="Default SELECT row cap for this connector's queries (else the pool's, then 1000). A query's max_rows / a per-request override take precedence.")
    queries: list[QueryDef] = Field(default_factory=list, json_schema_extra={"x_group": "Queries"}, description="The named SQL queries this connector exposes.")


# --------------------------------------------------------------------------- #
# API connector
# --------------------------------------------------------------------------- #

AuthType = Literal["none", "basic", "bearer", "api_key", "oauth2"]


class EndpointDef(BaseModel):
    """A named HTTP endpoint relative to the connector's ``base_url``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Unique name within the connector; the permission string is api:<connector>:<name>.")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] = Field(
        default="GET",
        description="HTTP method.",
        json_schema_extra={"x_enum_ref": "HTTP_METHOD"},
    )
    path: str = Field(default="", description="Path appended to the connector's base_url. Supports {{placeholder}} substitution (params + built-ins {{username}}/{{password}}/{{token}}). An absolute http(s):// path overrides base_url.")
    headers: dict[str, str] = Field(default_factory=dict, json_schema_extra={"x_group": "Headers"}, description="Per-endpoint request headers (merged over the connector's default_headers); values support {{placeholders}}.")
    query_params: dict[str, str] = Field(default_factory=dict, json_schema_extra={"x_group": "Headers"}, description="Query-string parameters; values support {{placeholders}}.")
    body: str | None = Field(default=None, description="Request body template ({{placeholders}}). For multipart/form-data: lines of `name=value` (text) or `name=@path;filename=X;contentType=Y` (file).")
    content_type: str = Field(default="application/json", description="Content-Type of the request body.")
    response_field: str | None = Field(default=None, json_schema_extra={"x_group": "Response"}, description='Dot-path into the JSON response to extract (e.g. "data.0.id" indexes lists). Blank → return the whole response.')
    response_map: dict[str, str] = Field(default_factory=dict, json_schema_extra={"x_group": "Response"}, description="Pick several fields out of the response: {output_name = dot.path}.")
    params: list[ParamDef] = Field(default_factory=list, json_schema_extra={"x_group": "Params"}, description="Declared parameters for the {{placeholders}}.")
    label: str | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="Friendly name (shown in listings).")
    description: str | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="Longer description of what the endpoint does.")


class ApiConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["api"]
    licensed: bool = Field(default=False, description="Gate this connector behind a valid [license] key — without one (the open framework) it isn't loaded.")
    base_url: str = Field(description="Base URL endpoints are relative to, e.g. https://api.example.com. Supports ${ENV} refs. (Leave blank only if every endpoint uses an absolute path.)")
    auth_type: AuthType = Field(
        default="none",
        description="none / basic / bearer / api_key / oauth2.",
        json_schema_extra={"x_enum_ref": "AUTH_TYPE"},
    )
    auth_username: str | None = Field(default=None, json_schema_extra={"x_group": "Auth"}, description="Username — for basic auth, and the {{username}} placeholder. May be an ENC: value.")
    auth_password: str | None = Field(default=None, json_schema_extra={"x_group": "Auth", "format": "password"}, description="Password — for basic auth, and {{password}}. May be an ENC: value (decrypted at runtime via the crypto master key).")
    auth_token: str | None = Field(default=None, json_schema_extra={"x_group": "Auth", "format": "password"}, description="Static bearer token / API key (for bearer & api_key auth), and the {{token}} placeholder. May be an ENC: value.")
    auth_api_key_header: str = Field(default="X-Api-Key", json_schema_extra={"x_group": "Auth"}, description="Header name for api_key auth.")
    auth_token_endpoint: str | None = Field(default=None, json_schema_extra={"x_group": "OAuth2"}, description="OAuth2: token-endpoint URL (POSTed to fetch a token).")
    auth_token_field: str | None = Field(default=None, json_schema_extra={"x_group": "OAuth2"}, description="OAuth2: dot-path to the token in the token-endpoint response (e.g. \"access_token\").")
    auth_token_body: str | None = Field(default=None, json_schema_extra={"x_group": "OAuth2"}, description="OAuth2: request body sent to the token endpoint.")
    auth_token_content_type: str = Field(default="application/json", json_schema_extra={"x_group": "OAuth2"}, description="OAuth2: Content-Type of that request body.")
    auth_token_headers: dict[str, str] = Field(default_factory=dict, json_schema_extra={"x_group": "OAuth2"}, description="OAuth2: extra headers for the token request.")
    auth_token_ttl: int = Field(default=3300, json_schema_extra={"x_group": "OAuth2"}, description="OAuth2: how long (seconds) to cache a fetched token before refreshing (default 3300 = 55 min).")
    default_headers: dict[str, str] = Field(default_factory=dict, json_schema_extra={"x_group": "Transport"}, description="Headers sent on every request from this connector.")
    timeout: float = Field(default=30.0, json_schema_extra={"x_group": "Transport"}, description="Per-request timeout in seconds.")
    verify_ssl: bool = Field(default=True, json_schema_extra={"x_group": "Transport"}, description="Verify the server's TLS certificate (disable only for dev / self-signed).")
    endpoints: list[EndpointDef] = Field(default_factory=list, json_schema_extra={"x_group": "Endpoints"}, description="The named HTTP endpoints this connector exposes.")


ConnectorConfig = Annotated[
    Union[SqlConnectorConfig, ApiConnectorConfig],
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------- #
# Top-level file
# --------------------------------------------------------------------------- #


class ConnectorsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pools: dict[str, PoolConfig] = Field(default_factory=dict)
    connectors: dict[str, ConnectorConfig] = Field(default_factory=dict)


def parse_connectors(data: dict[str, Any], *, env: dict[str, str] | None = None) -> ConnectorsFile:
    """Validate a raw TOML dict into a :class:`ConnectorsFile` (after env substitution)."""
    return ConnectorsFile.model_validate(substitute_env(data, env=env))


def load_connectors_file(
    path: Path | str, *, env: dict[str, str] | None = None
) -> ConnectorsFile:
    """Load and validate ``connectors.toml``. A missing file yields an empty config."""
    path = Path(path)
    if not path.exists():
        return ConnectorsFile()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return parse_connectors(data, env=env)
