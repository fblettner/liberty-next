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
            "DB password kept out of the URL — substituted in (URL-escaped) when the engine connects. "
            "May be an ENC: value (decrypted at runtime via the master key), plain text, or a ${ENV} "
            "reference. Leave blank if the password is already in the URL."
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
        "Maps #SCHEMA.<NAME># placeholders in this pool's queries to the real schema name. Lets the "
        "same query target dev vs prod schemas — or several schemas under one DB user — without "
        "editing the SQL. Values must be plain identifiers (SY920, db.schema). Case-insensitive."
    ))
    pool_size: int = Field(default=5, description="Persistent connections kept in the pool.", json_schema_extra={"x_group": "Pool"})
    max_overflow: int = Field(default=10, description="Extra connections allowed beyond `pool_size` under load.", json_schema_extra={"x_group": "Pool"})
    pool_pre_ping: bool = Field(default=True, description="Test a connection's liveness before handing it out (recovers from dropped connections).", json_schema_extra={"x_group": "Pool"})
    pool_recycle: int = Field(default=-1, description="Recycle a connection after this many seconds; -1 = never.", json_schema_extra={"x_group": "Pool"})
    echo: bool = Field(default=False, description="Log every SQL statement (debug only — very noisy).", json_schema_extra={"x_group": "Pool"})
    max_rows: int | None = Field(default=None, json_schema_extra={"x_group": "Pool"}, description=(
        "Default cap on rows a SELECT returns on this pool. Each screen and each request can override; "
        "the absolute fallback is 1000. Blank = no pool-level cap."
    ))
    trim_strings: bool = Field(default=False, json_schema_extra={"x_group": "Pool"}, description=(
        "Strip trailing whitespace from every string cell on SELECT. Turn on for Oracle pools "
        "that use space-padded CHAR / NCHAR columns (JD Edwards is the canonical case). Off by "
        "default — not all Oracle deployments use CHAR, so the choice is left explicit."
    ))
    coalesce_nulls: bool = Field(default=False, json_schema_extra={"x_group": "Pool"}, description=(
        "On INSERT / UPDATE, replace empty bind values (None or empty string) with type-appropriate "
        "defaults — a single space for CHAR / VARCHAR / NCHAR / NVARCHAR2 / CLOB / LONG, 0 for "
        "NUMBER / FLOAT / INTEGER. Turn on for Oracle pools with NOT-NULL string columns (Oracle "
        "treats '' as NULL on every string type, so only a real character satisfies the constraint). "
        "Off by default — not every Oracle deployment hits this, so the choice is left explicit."
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
    """A cascading filter — narrow this column's lookup options based on another filter's value.
    Example: pick an Application in one filter, the Role dropdown then shows only that
    application's roles. Only meaningful when this column's rule is LOOKUP."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        description="Another filter-flagged column on the same screen — its current value is the input.",
        json_schema_extra={"x_enum_ref": "DD_ENTRIES"},
    )
    column: str = Field(
        description="Column of the lookup query's result to match the source's value against.",
        json_schema_extra={"x_enum_ref": "DD_ENTRIES"},
    )


class VisibleWhen(BaseModel):
    """Show this column only when a server-filter has a given value. Useful when the same grid
    serves several modes — e.g. show the "From / To date" column only when "Status" is filtered
    to "Active". The column disappears from the grid entirely when the rule doesn't hold.
    Set multiple rules to AND them. ``field`` must be another filter-flagged column on the
    same screen."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="The filter column whose value gates this column's visibility.")
    value: str | list[str] = Field(description="The allowed value, or list of allowed values.")

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field, "value": self.value}


class ParamBind(BaseModel):
    """Bind a parameter of a target query — for a lookup combo, an action argument, a row
    context-menu trigger, or a nested-tab filter. Two modes: bind a literal value, or bind
    another column's / field's current value (resolved at call time). Set exactly one of
    ``value`` or ``source``. ``source = "#LOGIN_USER#"`` and ``#SYSDATE#`` are reserved
    built-ins.

    ``default`` is the fallback bound when *source mode* resolves to NULL / empty at call
    time — v2's port of v1's ``ly_act_tasks_params.map_default``. Useful when a workflow
    step needs a sane fallback for an optional input (e.g. JDE F0092 inserts where a blank
    ``UPMJ`` should default to today). Ignored in value mode."""

    model_config = ConfigDict(extra="forbid")

    param: str = Field(description="The :placeholder parameter to bind on the target query.")
    value: str | None = Field(default=None, description="Literal value to bind.")
    source: str | None = Field(
        default=None,
        description="Column / field whose current value to bind. Read at call time from the firing context (row, dialog form, …).",
    )
    default: str | None = Field(
        default=None,
        description=(
            "Fallback value bound when *source mode* resolves to NULL / empty at call time. "
            "v2's port of v1's ``ly_act_tasks_params.map_default``. Ignored in value mode."
        ),
    )


class ColumnHint(BaseModel):
    """Display + edit metadata for one column on a screen. Drives both the grid (the table view)
    and the dialog form — set it once, both surfaces use it.

    ``label`` and ``format`` fall back to the shared field dictionary when not set here. The
    dictionary entry is looked up under ``dd`` if set, else the column ``name``; set ``dd = ""``
    to opt out of dictionary lookup entirely.

    The order of the ``columns`` list is the display order in the grid. Columns the read query
    returns but that aren't hinted here keep their discovery order and follow the hinted ones.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The result column this entry applies to (matched case-insensitively).")
    dd: str | None = Field(
        default=None,
        description="Dictionary entry to inherit label / format / rule from. Blank = use ``name`` as the key. Empty string = no dictionary lookup.",
        json_schema_extra={"x_enum_ref": "DD_ENTRIES"},
    )
    label: str | None = Field(default=None, description="Display title in the grid header and the dialog field label.")
    hidden: bool = Field(default=False, description="Hide this column in the grid (operator can still un-hide it via the Columns menu).")
    key: bool = Field(
        default=False,
        description=(
            "Mark this column as part of the row's primary key — drives the Excel import's "
            "update-vs-insert match (a row whose key columns match a loaded row updates that row; "
            "otherwise inserts) and locks the column on edit-mode dialogs so the operator can't "
            "type over an identifying value. v1's ``col_key = 'Y'``."
        ),
    )
    filter: bool = Field(default=False, json_schema_extra={"x_group": "Filtering"}, description="Show this column in the grid's filter panel for pre-narrowing the query.")
    filter_from: list[FilterDep] = Field(default_factory=list, json_schema_extra={"x_group": "Filtering"}, description="Cascading filters — narrow this column's lookup options when another filter is set.")
    visible_when: VisibleWhen | list[VisibleWhen] | None = Field(default=None, json_schema_extra={"x_group": "Filtering"}, description="Show this column only when another filter has a given value. Multiple rules are AND-ed.")
    width: int | None = Field(default=None, description="Fixed grid column width in pixels. Blank = auto-size to content.")
    align: Literal["left", "right", "center"] | None = Field(
        default=None,
        description="Grid cell alignment. Blank auto-aligns (numbers right, booleans centred, everything else left).",
        json_schema_extra={"x_enum_ref": "COLUMN_ALIGN"},
    )
    format: str | None = Field(
        default=None,
        description="How to render the value — date / datetime / number / boolean / currency / password / … Overrides the dictionary's format.",
        json_schema_extra={"x_enum_ref": "DICTIONARY_TYPE"},
    )
    # ── form-side metadata (drives both the grid cell renderer and the dialog form) ──────
    rules: str | None = Field(
        default=None,
        json_schema_extra={"x_group": "Rule", "x_enum_ref": "DICTIONARY_RULES"},
        description=(
            "How to render and validate the column's value. BOOLEAN renders a checkbox, ENUM a "
            "dropdown, LOOKUP a searchable picker. Leave blank to inherit from the dictionary "
            "entry. Set here when one screen needs a different widget than the global default."
        ),
    )
    rules_values: str | None = Field(
        default=None,
        json_schema_extra={
            "x_group": "Rule",
            "x_enum_ref_when": {
                "field": "rules",
                "map": {
                    "BOOLEAN": "BOOLEAN_TRUE_VALUES",
                    "ENUM": "ENUM_IDS",
                    "LOOKUP": "LOOKUP_IDS",
                    "SEQUENCE": "SEQUENCE_IDS",
                    "NN": "SEQUENCE_IDS",
                },
            },
        },
        description=(
            "The rule's value: BOOLEAN → the true marker (Y / 1 / true); ENUM → the enum id; "
            "LOOKUP → the lookup id; SEQUENCE / NN → the sequence id."
        ),
    )
    default: str | None = Field(
        default=None,
        json_schema_extra={"x_group": "Rule"},
        description="Pre-fill value when adding a new row.",
    )
    required: bool = Field(
        default=False,
        json_schema_extra={"x_group": "Rule"},
        description=(
            "Operator must fill this column before saving. Dialog field can override "
            "per-dialog if needed."
        ),
    )
    disabled: bool = Field(
        default=False,
        json_schema_extra={"x_group": "Rule"},
        description=(
            "Read-only — the operator sees the value but can't change it. Dialog field can "
            "override per-dialog if needed."
        ),
    )
    lookup_param_binds: list[ParamBind] = Field(
        default_factory=list,
        json_schema_extra={"x_group": "Rule"},
        description=(
            "Narrow this column's lookup query by binding extra parameters. Used when the "
            "lookup depends on another field's value — e.g. picking a role narrows by the "
            "row's current application id."
        ),
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
    """A named SQL query with ``:name`` placeholders. **Phase 3 — pure SQL definition.**

    Carries only what the SQL connector needs to *run* the query: the statement, declared
    parameters, the writable gate, and friendly metadata. Per-screen display + behaviour
    (``columns`` hints, ``auto_load``, ``audit_table``, ``key_columns``, ``max_rows``,
    ``update_query`` / ``insert_query`` / ``delete_query`` companion refs) lives on the
    matching :class:`liberty.screens.config.Screen` and is threaded into the SQL connector
    by the route layer at execute time.

    ``sql`` is either a single statement (the common case) or a per-dialect map —
    e.g. ``sql = { default = "…", oracle = "…" }`` — keyed by SQLAlchemy backend
    name; the connector picks the variant matching its pool's database, falling
    back to ``default`` (which is required when ``sql`` is a map). v1's per-``dbtype``
    SQL variants migrate to this shape.
    """

    # ``extra = "ignore"`` (not "forbid") so a connectors.toml written before Phase 3 keeps
    # loading. Removed keys (``columns`` / ``auto_load`` / ``audit`` / ``max_rows`` /
    # ``key_columns`` / ``update_query`` / ``insert_query`` / ``delete_query``) are silently
    # dropped on parse — operators re-migrate at their pace via ``liberty-migrate sql`` to
    # repopulate the matching ``Screen`` fields.
    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="Unique name within the connector; the permission string is sql:<connector>:<name>.")
    sql: str | dict[str, str] = Field(description="The SQL statement (`:name` placeholders). Either one string, or a per-dialect map { default = \"…\", oracle = \"…\" } keyed by SQLAlchemy backend name (the connector picks the variant matching its pool, falling back to `default`, which is then required).")
    writable: bool = Field(default=False, description="Allow non-SELECT statements (INSERT/UPDATE/DELETE/…). Required for any mutating query — plus the caller must hold the permission.")
    params: list[ParamDef] = Field(default_factory=list, json_schema_extra={"x_group": "Params"}, description="Declared parameters — give a `:name` placeholder a form label and a default.")
    label: str | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="Friendly name for the query (shown in listings).")
    description: str | None = Field(default=None, json_schema_extra={"x_group": "Advanced"}, description="Longer description of what the query returns.")

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
