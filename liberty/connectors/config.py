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
    """A named database pool — one SQLAlchemy async engine per entry."""

    model_config = ConfigDict(extra="forbid")

    url: str
    # SQLAlchemy backend name (postgresql / oracle / sqlite / mysql / mssql / …). Empty →
    # derived from the URL. Used to pick a query's per-dialect SQL variant (see QueryDef.sql).
    dialect: str = ""
    pool_size: int = 5
    max_overflow: int = 10
    pool_pre_ping: bool = True
    pool_recycle: int = -1
    echo: bool = False
    # Default cap on rows a SELECT returns on this pool (v1's per-app `apps_limit`). `None` → no
    # pool-level default. A connector's `max_rows`, a query's `max_rows`, or a per-request override
    # each take precedence in that order; the absolute fallback is 1000. See SQLConnector.execute.
    max_rows: int | None = None


# --------------------------------------------------------------------------- #
# Shared bits
# --------------------------------------------------------------------------- #


class ParamDef(BaseModel):
    """A declared parameter — surfaces a default and (eventually) a UI label."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str | None = None
    default: str | None = None


class ColumnHint(BaseModel):
    """Optional *display* metadata for one result column. The column **schema**
    (names + types) is still discovered from the query at run time — these hints only
    augment it (a display title, visibility, column order, a width/alignment, a
    free-text ``format`` the UI may interpret, and a ``filter`` flag — v1's ``col_filter`` —
    that surfaces the column in the TableView filter panel). ``label``/``format`` may be left out and
    pulled from the shared dictionary (``config/dictionary.toml``) instead: the entry
    key is ``dd`` if set, else the column ``name``; an inline ``label``/``format`` here
    still overrides the dictionary. v1's ``ly_tbl_col`` / ``ly_dlg_col`` rows migrate to
    this shape (``dd`` = v1's ``col_dd_id``). A hint for a column the query doesn't return
    is ignored. The order of the ``columns`` list is the display order; columns with no
    hint keep their discovery order and follow the hinted ones.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    dd: str | None = None      # dictionary-entry key for label/format; None → look it up under `name`
    label: str | None = None
    hidden: bool = False
    filter: bool = False       # surface this column in the TableView filter panel (v1's col_filter)
    width: int | None = None
    align: str | None = None   # "left" | "right" | "center" — a UI hint, not strictly validated
    format: str | None = None  # e.g. "date" / "datetime" / "number" / "boolean" / "currency" — UI-interpreted

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

    name: str
    sql: str | dict[str, str]
    writable: bool = False
    params: list[ParamDef] = Field(default_factory=list)
    columns: list[ColumnHint] = Field(default_factory=list)  # display hints; the schema is still from the query
    label: str | None = None
    description: str | None = None
    # When true the TableView runs this query immediately on open (no "Run" click) — v1's
    # per-table "auto load" flag. A query with required-feeling params is still safe: every
    # `:name` the caller omits becomes SQL NULL.
    auto_load: bool = False
    # Per-query override of the row cap (else the connector's, then the pool's, then 1000); a
    # per-request override beats this. See SQLConnector.execute.
    max_rows: int | None = None
    # Result columns that identify a row (v1's `col_key`) — surfaced in `describe()`; the TableView's
    # Excel import matches imported rows against the loaded ones on these to decide update vs insert.
    key_columns: list[str] = Field(default_factory=list)
    # Names of `writable` queries on the same connector that update / insert / delete a row of this
    # query's result — set explicitly, or (when unset) auto-derived from the `<base>_get` → `<base>_put`
    # / `<base>_post` / `<base>_delete` naming convention the migration uses. Drive the TableView's
    # batch edit mode (edit a row → its `_put`; "Add row" → `_post`; delete a row → `_delete`).
    update_query: str | None = None
    insert_query: str | None = None
    delete_query: str | None = None

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
    pool: str = "default"
    # Default row cap for this connector's SELECTs. `None` → fall back to the pool's `max_rows`, then 1000.
    # A query's `max_rows`, or a per-request override, take precedence. See SQLConnector.execute.
    max_rows: int | None = None
    queries: list[QueryDef] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# API connector
# --------------------------------------------------------------------------- #

AuthType = Literal["none", "basic", "bearer", "api_key", "oauth2"]


class EndpointDef(BaseModel):
    """A named HTTP endpoint relative to the connector's ``base_url``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    method: str = "GET"
    path: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    content_type: str = "application/json"
    response_field: str | None = None
    response_map: dict[str, str] = Field(default_factory=dict)
    params: list[ParamDef] = Field(default_factory=list)
    label: str | None = None
    description: str | None = None


class ApiConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["api"]
    base_url: str
    auth_type: AuthType = "none"
    auth_username: str | None = None
    auth_password: str | None = None
    auth_token: str | None = None
    auth_api_key_header: str = "X-Api-Key"
    # OAuth2 token endpoint
    auth_token_endpoint: str | None = None
    auth_token_field: str | None = None
    auth_token_body: str | None = None
    auth_token_content_type: str = "application/json"
    auth_token_headers: dict[str, str] = Field(default_factory=dict)
    auth_token_ttl: int = 3300  # seconds (55 min — matches nomaubl's TokenManager)
    # Transport
    default_headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = 30.0
    verify_ssl: bool = True
    endpoints: list[EndpointDef] = Field(default_factory=list)


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
