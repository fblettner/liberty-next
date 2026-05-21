# Liberty Next — Claude Code context

This file is auto-loaded by Claude Code. Read it first.

## What this is

Greenfield rewrite of the **Liberty low-code framework**. v1 lives at
`../liberty-framework/` (FastAPI + SQLAlchemy, ~50 `ly_*` metadata tables driving
forms/queries/UI at runtime). v1 is **slow on lookups, hard to add screens, and
buries all logic in SQL stored in tables**. v2 replaces the metadata-table model
with a **connector pattern** lifted from `../../JavaProjects/nomaubl` (a Java
e-invoicing app the user built afterwards and considers much better designed):
configuration drives discovery, not code drives configuration.

## Hard rules

- **Never touch `../liberty-framework/` source.** v1 stays alive in production
  (apps: nomasx1, NOMAJDE, AIRFLOW) until v2's migration tools (Phase 5) exist.
- **No `ly_*` metadata tables in v2.** Form/column schema is discovered at query
  time from `cursor.description`, not stored. Connector definitions live in TOML
  files under `config/`, hot-reloadable.
- **Anthropic, not OpenAI.** v1 hardcodes ChatGPT; v2 uses the `anthropic` SDK.
- Match the surrounding code's style. Type hints everywhere (`from __future__
  import annotations`). Tests for new modules.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 async · asyncpg (PostgreSQL) + oracledb (Oracle, thin) · Anthropic SDK ·
authlib (OIDC — any provider) · argon2-cffi (passwords) · React 19 + Vite + TS
(frontend, Phase 4, embedded as static — no shared libraries).

## Current status

**Phase 0 (Foundation) — DONE.** Project skeleton, TOML config loader
(`liberty/config.py`), FastAPI app (`liberty/main.py` with `/health`, `/info`).
Full dep set pinned in `pyproject.toml`.

**Phase 1 (Connector core) — DONE.** Lives in `liberty/connectors/`:
- `config.py` — Pydantic schema for `config/connectors.toml` (`[pools.*]` plus
  discriminated `[connectors.*]` of type `sql`/`api`); `${ENV_VAR}` secret
  substitution at load time. A query's `sql` is a string *or* a per-dialect map
  (`sql = { default = "…", oracle = "…" }`, keyed by SQLAlchemy backend name; `default`
  required) — `QueryDef.sql_for(dialect)` / `.default_sql` / `.dialects` resolve it.
  A query may also carry optional `columns` display hints (`ColumnHint`: `name`, `dd?`,
  `label?`, `hidden?`, `filter?`, `filter_from?`, `visible_when?`, `width?`, `align?`, `format?`) — these only *augment* the still-discovered
  schema (display title / visibility / column order / a `filter` flag — v1's `col_filter` — / `filter_from` —
  v1's `ly_tbl_filters` — a list of `{source, column}` cascading-filter deps for the TableView panel: when the
  `source` filter has a value this column's LOOKUP options narrow to the lookup rows whose `column` matches it /
  `visible_when` — v1's `cdn_*` — a `{field, value}` rule (or a list of them, all AND-ed): a rule passes
  when its `field` server-filter is unset or its value matches `value` (or is in `value` when a list), and
  a column whose rules don't all pass is dropped from the grid entirely / a UI-interpreted `format`);
  `label`/`format` may be omitted and pulled from the shared dictionary (the entry key is `dd`, or
  `name` when `dd` is unset; `dd = ""` opts out); a hint for a column the query doesn't return is ignored.
  A query may also carry `label`/`description` (display names — the frontend titles the TableView with
  `description`, else `label`, else the menu label; the menu label rides on the tab), `auto_load = true`
  (v1's per-table auto-load — the TableView runs a SELECT immediately on open instead of waiting for a Run click),
  `max_rows` (the SELECT row cap for this query — overrides the connector's, then the pool's, then 1000;
  a per-request override beats it), and `key_columns` (the result columns that identify a row — v1's `col_key` —
  surfaced in `describe()` for the TableView's Excel-import update-vs-insert match). `[pools.*]` may carry an
  explicit `dialect` (else derived from the URL), a `max_rows` (the pool's default row cap — v1's per-app
  `apps_limit`), a `password` (kept out of the URL — substituted in, escaped, when the engine is built;
  may be an `ENC:` value decrypted via the crypto master key — how v1's `apps_password` reads — or plaintext /
  a `${ENV}` ref; an `ENC:` password embedded in the URL is also decrypted), and a `schemas` map (`{NAME =
  "actual_schema"}` — `#SCHEMA.<NAME>#` in a query's SQL is replaced with it at execution time; v1's
  `ly_db_schema`, for dev/prod schema swaps or several schemas under one DB user); `[connectors.*]` (sql) a `max_rows` too.
- `dictionary.py` — `config/dictionary.toml`: the **shared field dictionary** (v1's `ly_dictionary`
  + `ly_dictionary_l`, plus `ly_enum`/`ly_enum_val`/`ly_lookup`). `[entries.<key>]` (or
  `[connectors.<conn>.entries.<key>]` — per-connector, since v1 dictionaries were per-app) =
  `{ label?, format?, rules?/rules_values?/default?, [..l] { fr = "…", … }` (per-language labels) `}`,
  plus `[enums.<id>]` (`{ label?, values: [{ value, label?, l? }, …] }` — v1's ly_enum + ly_enum_val
  with translations) and `[lookups.<id>]` (`{ description?, connector?, query, value, label, group? }` —
  a reference to a v2 query whose `value`/`label` columns resolve the cell; v1's ly_lookup), plus
  `default_language`. A query's `columns` hints reference an entry; the SQL connector resolves
  the label/format at result time in the request's language *and* the entry's display **rule**
  (BOOLEAN / ENUM / LOOKUP — the v2 form of v1's `dd_rules`) — its own `[connectors.<conn>.…]`
  section first, then the shared top-level. `DictionaryFile.find_entry(key, *, connector)` returns
  the entry; `.resolve_rule(entry, *, connector, language)` returns a wire-ready
  `{kind:"boolean", true_value}` / `{kind:"enum", values:[…]}` / `{kind:"lookup", connector, query, value, label}` /
  `{kind:"auto_fill", source:"current_date"|"login_user"}` dict (None for PASSWORD — masking is driven by
  `format = "password"`, not the rule — and for SEQUENCE / NN — server-side via `SQLConnector._resolve_sequences`).
  `Column.rule` rides on the result so the frontend renders ✓/✗ for booleans, the enum label, or — via
  `services/lookups.useLookupBatch` — the lookup label after a one-shot per-session fetch. A missing
  file = an empty dictionary. `/info` reports `dictionary.{entries, default_language}`.
- `base.py` — connector exceptions; `detect_statement_type` (resolves `WITH` CTE
  queries to their main statement keyword — `WITH … SELECT` → `SELECT`, `WITH … DELETE`
  → `DELETE` so the writable gate still applies; an unparseable CTE list → `"WITH"` →
  rejected) + `find_bind_params` (SQL text scanner skipping literals/comments/`::` casts);
  `ALLOWED_STATEMENTS` / `WRITE_STATEMENTS`.
- `db.py` — `PoolRegistry`: one SQLAlchemy async engine per named pool, created
  lazily (unreachable DB never blocks startup; tests inject their own engine); `dialect(name)`
  → the pool's backend name (a live engine's own dialect / the explicit setting / the URL); `schemas(name)`
  → the pool's `#SCHEMA.<NAME>#` map; when building the engine it resolves the pool's `password` (or an
  `ENC:` password in the URL) — decrypts an `ENC:` value via the `master_key` it's given (wrong/missing key →
  kept as-is + a logged warning, like the API connector) and re-sets it on the URL object so URL-special chars are escaped.
- `sql.py` — `SQLConnector`: named queries, `:param` binding via SQLAlchemy
  `text()` (never string-substituted), `#SCHEMA.<NAME>#` placeholders in the SQL replaced at execute time
  with the pool's `schemas` map (`_apply_schema_placeholders` — a `#SCHEMA.X#` with no mapping, or a mapping
  that isn't a plain identifier, raises `ConnectorError`), the SQL variant matching the pool's dialect is
  selected per call, statement-type allow-list, `writable` gate for mutations, any `:name`
  the caller omits → SQL NULL, runtime schema from `result.keys()` + best-effort
  `cursor.description` types (then the query's `columns` hints overlaid — reorder + attach
  label/hidden/width/align/format, label & format resolved against the registry's shared
  dictionary in `execute(query, params, *, language=…)`'s language — default: the dictionary's
  `default_language`; a hint matches a result column **case-insensitively** — the DB folds
  unquoted identifiers, Postgres→lower / Oracle→upper, v1's hints are upper — and the emitted
  column keeps the *discovered* case so it lines up with the row dict's keys), `max_rows` cap;
  `QueryResult.to_dict()` carries the resolved per-column
  hints, `describe()` exposes the `columns` resolved for the default language plus `update_query` /
  `insert_query` / `delete_query` per query (the explicit `QueryDef.{update,insert,delete}_query`, else
  the `<base>_get` → `<base>_put` / `_post` / `_delete` companion if it exists & is writable — the
  frontend's batch-edit hook). (JDE Julian date/time
  conversion from nomaubl `DynamicResultMapper`: deferred to Phase 5, if NOMAJDE needs it.)
- `api.py` — `APIConnector`: `httpx.AsyncClient`; auth `none`/`basic`/`bearer`/
  `api_key`/`oauth2` (OAuth2 = token-endpoint POST + dot-path token extraction +
  TTL cache + one refresh on 401); `{{placeholder}}` substitution in
  path/query/headers/body (built-ins `{{username}}`/`{{password}}`/`{{token}}`);
  dot-path response extraction (`data.0.id` indexes lists) via `response_field`
  and/or `response_map`; `multipart/form-data` bodies (`name=value` text parts,
  `name=@path;filename=X;contentType=Y` file parts).
- `registry.py` — `ConnectorRegistry`: builds connectors from `ConnectorsFile`,
  owns the pool registry + the shared `DictionaryFile` (passed to each SQL connector),
  `aclose()` disposes engines + HTTP clients. `load_connectors(path, *, dictionary_path=…)`
  also loads `dictionary.toml` (or one next to `connectors.toml`). Rebuildable → hot-reload.
- `liberty/cli.py` (`liberty-connectors` script) — `list` / `describe <c>` /
  `run <c> <query-or-endpoint> -p k=v`.
Wired into `main.py` lifespan (`app.state.connectors`); `/info` reports loaded
connector + pool names.

**Phase 2 (Auth + AI) — DONE.**

*Auth* lives in `liberty/auth/`. Users/roles have **two interchangeable backends** behind one async
interface (`AuthBackend`), both returning `UserRecord`s — `[auth] backend = "toml" | "db"` (default
`"toml"`, in the shipped `config/app.toml`):
- `authstore.py` — the abstraction + both backends + `config/auth.toml` schema:
  - `AuthRole` (`permissions: [str]`, `description?`) / `AuthUser` (`password_hash?` — Argon2, `None`
    for OIDC-only; `roles: [str]`, `active`, `superuser`, `provider`, `sub?`, `email?`, `full_name?`)
    / `AuthFile` (`roles`, `users` dicts); `load_auth(path)` / `save_auth(path, file)` (atomic temp+rename,
    `chmod 600`, TOML has no null so `None`s are stripped). `UserRecord` (the common view: `id` = `str(db id)`
    for the DB backend / the *username* for TOML; `username`, `email`, `roles`, `permissions`, `is_active`,
    `is_superuser`, `provider`, `provider_subject`, `public_dict()`).
  - `TomlAuthBackend(path)` — users/roles in `config/auth.toml`, **no database**. Reloaded on every
    call (it's small) so a hand edit / hot-reload takes effect at once; mutations rewrite the file.
    `ready()` creates an empty file. For lots of users, point customers at OIDC/LDAP instead.
  - `DbAuthBackend(AuthDatabase)` — the `ly2_*` tables; a thin adapter (one session per call) over
    `AuthService` (below), mapping its ORM `User`s to `UserRecord`s. `ready()` = `create_schema()`.
  - `build_auth_backend(settings, pools)` picks one from `[auth] backend`.
  Both: `authenticate`, `get_by_id` (= the JWT subject), `provision_oidc_user`, `list_users`,
  `list_roles`, `count_users`, `create_user`, `set_password`, `set_active`, `assign_roles`, `get_or_create_role`.
- `password.py` — Argon2id via `argon2-cffi` (`hash_password` / `verify_password` / `needs_rehash`) —
  login passwords are one-way hashed (not reversibly encrypted) either way.
- `tokens.py` — `TokenService` mints/verifies HS256 JWTs: `access` (carries `roles`/`perms`/`sup`
  + `sub` (the user id) → no per-request DB hit) and `refresh` (re-reads the user by `sub`).
- `models.py` / `db.py` / `service.py` — the **DB backend's** internals: SQLAlchemy 2.0 ORM (`User`,
  `Role`, `user_roles` M2M; tables `ly2_users`/`ly2_roles`/`ly2_user_roles`; `Role.permissions` a JSON
  list, `User.is_superuser` a bool — *app data*, not v1-style metadata), `AuthDatabase` (lazy
  `async_sessionmaker` over a `PoolRegistry` pool — `[auth] pool` — `create_schema()` for init-db),
  `AuthService` (the ORM operations: `authenticate` + rehash, `create_user`, `set_password`/`set_active`,
  role ops, `provision_oidc_user` — find-or-create by `(provider="oidc", sub)`, username-collision suffixing).
  ⚠ async-ORM gotcha: assign `user.roles = [...]` explicitly (even `[]`) on new rows — a freshly-flushed
  object's unloaded relationship lazy-loads on access, which raises `MissingGreenlet` under async.
- `principal.py` — `Principal` (built from JWT claims, no DB): `has_permission`
  with colon-segment globs (`sql:*` ⊇ `sql:liberty:read`), `has_role`, superuser.
- `oidc.py` — `build_oidc(settings)` → Authlib Starlette `OAuth` client, configured purely from
  `[oidc] discovery_url` (`…/.well-known/openid-configuration`) — provider-agnostic (Keycloak,
  OneLogin, Auth0, Okta, Azure AD, Google, … directly, no broker); `None` when disabled. ID-token
  validation is Authlib's job. (`username_claim` defaults to `preferred_username` — set it to
  `email`/`sub` for providers that don't emit that, e.g. OneLogin.)
- `dependencies.py` — `get_current_principal` / `optional_principal`,
  `require_permission(perm)` / `require_role(role)` / `require_superuser`,
  `get_auth_backend` (the configured `AuthBackend`), `get_oidc` (404 if off). All read `request.app.state`.
- `routes.py` — `POST /auth/login`, `POST /auth/refresh`, `GET /auth/me`,
  `GET /auth/oidc/login`, `GET /auth/oidc/callback` (both 404 when OIDC is off);
  go through `AuthBackend`. Both login paths mint *our* JWTs; the IdP's tokens aren't propagated.
- `liberty/admin_cli.py` (`liberty-admin` script) — operates on the active backend: `init-db`
  (`ready()` — create the DB tables *or* an empty `auth.toml` — + a `admin` role (perm `*`) and, if
  there are no users yet, a superuser `admin`; password from `--password` / `--password-env` /
  generated-and-printed), `create-user`, `set-password`, `set-active`, `list-users`, `create-role`.
- `liberty/config.py` — `[auth]` (`backend`, `toml_path`, `pool`, `jwt_*`) + `[oidc]` settings;
  `${ENV_VAR}` substitution applies to `app.toml` too (`substitute_env` moved here).
- `liberty/main.py` — `create_app(settings=None)`; lifespan builds `auth_backend`
  (`build_auth_backend`), `token_service`, `oidc` (and `ai` — below) on `app.state`; `SessionMiddleware`
  added iff OIDC enabled; includes the auth router; `/info` reports `auth.backend` (+ `pool`/`toml`) +
  `oidc_enabled` (and `ai`).
OIDC full-flow integration test: deferred (needs a fake IdP).

*AI* lives in `liberty/ai/` — an async Anthropic Messages-API agentic loop
(ported from nomaubl `AiAssistant.java`):
- `tools.py` — `@tool` decorator turns a (sync/async) function into a `Tool`:
  JSON `input_schema` derived from type hints, description + per-param descriptions
  from a Google-style docstring `Args:` block; `ToolRegistry` dispatches by name
  and returns `(content_str, is_error)`.
- `connector_tools.py` — `build_connector_tools(registry, allowed, include_api)`:
  `list_connectors` (discovery — tool *descriptions* stay byte-stable, the catalog
  is in the *result*, prompt-cache friendly), `sql_query` (**read-only** — refuses
  `writable` queries), `api_call` (off by default; API endpoints can mutate).
- `assistant.py` — `AiAssistant.chat(messages)` is an async generator of
  `ChatEvent` (`token` / `thinking` / `tool_call` / `tool_result` / `error` /
  `done`): `AsyncAnthropic.messages.stream(...)`, surface text deltas, run local
  tools, feed `tool_result` back, loop until non-`tool_use` stop reason or the
  `max_iterations` cap; `pause_turn` → re-send; `system` block is `cache_control`-ed
  (caches the stable tool list too); optional server-side `web_fetch_20260209`
  restricted to `web_fetch_domains`. `build_assistant(settings.ai, connectors)` →
  `AiAssistant | None` (None when disabled; client is None — calls fail fast — when
  no API key). Model default `claude-opus-4-7` (operator-overridable); no
  `temperature`/`top_p` (removed on 4.7); `thinking`/`effort` opt-in via config.
- `routes.py` — `POST /ai/chat` streams SSE (`StreamingResponse`), `GET /ai/tools`
  lists the catalog; both behind `require_permission("ai:chat")`. AI disabled → 404.
- `liberty/config.py` — `[ai]` settings (`model`, `max_tokens`, `max_iterations`,
  `thinking`, `effort`, `connector_tools`, `api_tool`, `allowed_connectors`,
  `web_fetch_domains`, …). API key from `${ANTHROPIC_API_KEY}`.

Deps used: `anthropic` (already pinned), `itsdangerous` (auth's `SessionMiddleware`),
`aiosqlite` (dev). Note: the `claude-api` skill (`/claude-api`) holds the live
Anthropic-SDK guidance — re-consult it before changing the AI module or bumping
the model. **Use `claude-opus-4-7` unless the user names another model.**

**Phase 3 (Web layer) — DONE.** Lives in `liberty/web/`:
- `connectors.py` — `GET /api/connectors` (+ `/{connector}`) lists connectors filtered
  to what the caller may use — **metadata only: no SQL text, no credentials, no pool** (the
  `columns` hints it shows are *resolved* for the dictionary's default language); `GET /api/sql/{c}/{q}`
  (SELECT-only, params from the query string; `?_limit=N` overrides the row cap) and `POST /api/sql/{c}/{q}`
  (any allowed statement; body `{"params": {…}, "max_rows": N}` or a flat `{name: value}`) execute a query → `QueryResult.to_dict()`
  (its `columns` carry the display hints, labels/formats resolved in the request's language —
  the `X-Liberty-Lang` header, else the first `Accept-Language` tag, else `default_language`);
  `POST /api/http/{c}/{e}` calls an API endpoint → `ApiResult.to_dict()` (returned as HTTP 200
  even on upstream failure — inspect `success`/`status_code`/`error`). Permission strings:
  `sql:{c}:{q}` / `api:{c}:{e}` (glob-aware — `sql:liberty:*`, `sql:*`, `*`). The permission is
  checked *before* the connector is looked up, so callers can't enumerate names they lack access
  to. A mutating query needs *both* its TOML `writable = true` and the caller's perm.
- `menus.py` — `GET /api/menus` (every accessible app's nav tree) and `GET /api/menus/{app}`;
  the tree is the v2 form of v1's `ly_menus` (config: `liberty/menus/`, file: `config/menus.toml`),
  resolved in the request's language and **pruned to what the caller may run** (a `query`/`endpoint`
  leaf needs `sql:{c}:{target}` / `api:{c}:{target}` and to pass any `roles` filter; folders left
  empty collapse away). Hot-reloaded with `connectors.toml`. The frontend renders it in the Sidebar.
- `admin.py` — `POST /admin/reload` (superuser): rebuild `ConnectorRegistry` from
  `connectors.toml` + `dictionary.toml`, re-read `menus.toml`, swap `app.state.connectors`/`.menus`,
  re-point `app.state.auth_db`, dispose the old registry. (The AI assistant's connector tools
  refresh on restart, not on reload; in-flight requests keep the registry they started with.)
- `deps.py` — `get_connectors` / `get_menus`, `require_permission(principal, perm)` (imperative — the
  perm string depends on path params), `public_connector` (the SQL/credential-stripped view),
  `request_language` (the `X-Liberty-Lang` header → first `Accept-Language` tag → `None`).
- `errors.py` — `ConnectorError` → HTTP: not-found→404, statement/writable→422, other→400;
  SQLAlchemy errors during execute → 502.
- Also (config editing — superuser): `GET /admin/config/connectors` (raw `connectors.toml` text) +
  `PUT /admin/config/connectors` (validates the TOML against the schema, then writes — does *not*
  reload; call `POST /admin/reload` after). And the **structured config builders** (Phase 7):
  `GET /admin/config/schema` → `{pool, sql, api, dictionary, menus, framework_enums}` = the
  `PoolConfig` / `SqlConnectorConfig` / `ApiConnectorConfig` / `DictionaryFile` / `MenusFile`
  `model_json_schema()`s, each with its own `$defs` (`QueryDef`/`ColumnHint`/`ParamDef`/`EndpointDef`
  / `DictionaryEntry`/`EnumDef`/`LookupDef` / `AppMenu`/`MenuItem` / …) — the UI renders its forms
  from this ; `GET /admin/config/pools` →
  `{path, pools: {name: PoolConfig dict}}` + `PUT /admin/config/pools` (`{pools: {name: dict}}`) —
  validates each against `PoolConfig`, drops default-valued keys, then **surgically rewrites only the
  `[pools.*]` tables** in `connectors.toml` via `tomlkit` (comments + the `[connectors.*]` tables +
  formatting left intact) ; `GET /admin/config/connectors/parsed` → `{path, connectors: {name: dict}}`
  (default-valued keys dropped) + `PUT /admin/config/connectors/parsed` (`{connectors: {name: dict}}`)
  — validates each against the discriminated connector schema, rewrites only the `[connectors.*]`
  tables (a *changed* connector's own subtree is re-rendered by `tomlkit`, so its inline `columns =
  [{…}]` arrays may become `[[…]]` tables — functionally identical), re-parses the whole result before
  writing ; `GET /admin/config/dictionary/parsed` → `{path, dictionary: {default_language, entries,
  enums, lookups, connectors: {<name>: {entries, enums, lookups}}}}` (default-valued keys dropped, a
  missing `dictionary.toml` → an empty dict) + `PUT /admin/config/dictionary/parsed` (`{dictionary:
  {…}}`) — validates the whole payload against `DictionaryFile`, then replaces each top-level section
  (`default_language`/`entries`/`enums`/`lookups`/`connectors`) wholesale via `tomlkit` (comments
  outside those sections survive), re-parses the file before writing ; `GET /admin/config/menus/parsed`
  → `{path, menus: {<app>: AppMenu dict}}` (defaults dropped) + `PUT /admin/config/menus/parsed`
  (validates the whole `MenusFile` — unique ids, parents exist, no cycles, folder-vs-leaf shape — then
  replaces the top-level `[menus]` table wholesale via `tomlkit`; re-parses before writing). PUT endpoints don't reload —
  call `POST /admin/reload` after. (Dep: `tomlkit` — comment/format-preserving TOML edits.) The
  config models carry per-field metadata for the builder forms: `Field(description=…)` → form hints,
  `Field(json_schema_extra={"x_group": "…"})` → which tab the field goes in (e.g. a query's
  `params`/`columns` are their own tabs, the optional bits are an "Advanced" tab; a dictionary
  entry's `rules`/`rules_values`/`default` form a "Rule" tab, the `l` map is "Translations";
  ungrouped → "General"). **Sensitive fields** (`PoolConfig.password`,
  `ApiConnectorConfig.auth_password` / `.auth_token`) carry
  `Field(json_schema_extra={"format": "password"})` and render through `common/Input` `PasswordInput`
  (masked, with a reveal-eye toggle) so an `ENC:` ciphertext doesn't sit in plain text in the
  builder — the stored value is the raw string, purely a visual mask. Widget selection is driven by
  **framework enums** — v2's port of v1's
  `ly_enum`-for-the-framework table — defined in `liberty/framework_enums.py` (`DICTIONARY_TYPE`,
  `DICTIONARY_RULES`, `DATASOURCE_TYPE`, `HTTP_METHOD`, `COLUMN_ALIGN`, `AUTH_TYPE`, …) and shipped
  via the same `GET /admin/config/schema` response under `framework_enums` (the operator can
  override a bundled entry by adding a `[framework_enums.<id>]` section to `dictionary.toml` —
  full replace, merged on the fly at the schema endpoint, surfaced in the DictionaryBuilder's
  *Framework* sub-tab). Two extensions on top: `x_enum_ref_when={"field": "rules", "map":
  {"ENUM": "ENUM_IDS", "LOOKUP": "LOOKUP_IDS", "BOOLEAN": "BOOLEAN_TRUE_VALUES"}}` picks the ref
  from a *sibling* field's current value (DictionaryEntry's `rules_values` swaps source when
  `rules` changes); `x_key_enum_ref` on a `dict[str, T]` field renders the row's *key* as a
  themed SearchSelect (translations `l` use `SUPPORTED_LANGUAGES` so the user picks "fr /
  Français" rather than typing a code, and already-used languages drop from per-row options so
  the same locale can't appear twice). And each builder *augments* the bundled set with its own
  dynamic enums before threading the context — DictionaryBuilder materialises `ENUM_IDS` /
  `LOOKUP_IDS` from the current scope's enums + lookups; ConnectorsBuilder fetches
  `dictionary.toml` and materialises `DD_ENTRIES` from the selected connector's entries + the
  shared bucket, which feeds `ColumnHint.dd` and `FilterDep.source` / `.column`. A field referencing one
  (`json_schema_extra={"x_enum_ref": "DICTIONARY_TYPE"}` on `DictionaryEntry.format`,
  `DictionaryEntry.rules`, `ColumnHint.format`, `ColumnHint.align`, `PoolConfig.dialect`,
  `EndpointDef.method`, `ApiConnectorConfig.auth_type`, …) renders as a themed two-column
  `SearchSelect` (mono `value` + sans `label` — so a "Dictionary Type" entry reads `number  Number`
  not just `Number`). A `Literal[…]`-typed + `x_enum_ref` field becomes a **strict** SearchSelect
  (option set narrowed to the Literal's enum); a free-text + `x_enum_ref` field is a **combobox**
  (`allowCustom` — typing a value the registry doesn't know commits, so v1's `numeric`/`decimal`/…
  aliases survive). Other strict `enum` fields without an `x_enum_ref` still render as a basic
  SearchSelect. The values are threaded through `FrameworkEnumsContext` (in `common/SchemaForm.tsx`)
  — each builder fetches once and wraps its render; deeply-nested SchemaForms (drill-in pages,
  list-item editors) read the same context.
OpenAPI auto-doc at `/docs` (`/openapi.json`) covers everything — replaces v1's
hand-rolled "get screen metadata" endpoint. WebSocket: not needed yet (SSE covers AI).

**Phase 4 (Frontend) — DONE.** `frontend/` — React 19 + Vite + TS, built `dist/` served
as static by the backend. UI adopts **nomaubl's "liquid-glass" look**: `@emotion/styled`,
a dark default + light theme (CSS-var swap via `.theme-light` on `<html>`, persisted),
`react-i18next` EN/FR (persisted), `lucide-react` icons, DM Sans (Google Fonts),
`@tanstack/react-table` (the SELECT grid), `react-markdown` + `remark-gfm` (assistant
replies), `@monaco-editor/react` (the connector-config editor).
- Layout (nomaubl-style): `src/api/client.ts` (the fetch wrapper + `streamSSE`; every request
  carries `X-Liberty-Lang` = the current i18n language, so query-result column labels come back
  localized from the shared dictionary), `src/auth/`
  (`AuthContext.tsx` — `AuthProvider`/`useAuth()`: login → `POST /auth/login`, token in
  `localStorage`, validate on mount via `/auth/me`, OIDC fragment hand-off), `src/workspace/`
  (`WorkspaceContext.tsx` — `WorkspaceProvider`/`useWorkspace()`: owns the post-login `GET /api/connectors`
  + `GET /api/menus` + `GET /api/license` fetch; exposes `license` (the `LicenseInfo` — mode + claims;
  shown in the ProfileModal, and a Layout banner when a *configured* key is broken), `connectors` (all accessible) and `apps` (the subset that have a
  menu — one v1 app can map to several v2 connectors, so "app" ≠ "connector"; with no menus defined,
  every connector is an app); holds `currentApp` — the picked app, persisted, dropped if it's no
  longer an app, made to follow the route when you open a `/sql/<c>/…` or `/http/<c>/…` screen *only*
  if `<c>` is an app (so opening a data-source connector's screen via an app's menu doesn't yank the
  picker over) — plus `currentMenu` (the picked app's nav tree, or — with a single app — that one's,
  for the Sidebar). A pure-frontend soft filter; v2 auth is centralized so the v1 "pick an app at
  login" idea becomes "which app's screens am I looking at"), `src/types/`
  (`connectors.ts`/`auth.ts`/`ai.ts`/`menus.ts`/`license.ts` — backend response shapes, no React), `src/services/`
  (plain-TS helpers/side-effect modules — `cells.ts`'s `cellText`/`ruleCell` (the latter applies the
  dictionary's BOOLEAN/ENUM/LOOKUP display rules), `lookups.ts` (`useLookupBatch` → value→label maps for the
  grid; `useLookupTables` → the richer `LookupData` (raw rows too) for the FilterPanel's cascading dropdowns;
  `lookupOptions` narrows a table to `{value,label}[]`; fetches each LOOKUP-target query once, module-level
  session cache), `monaco.ts` (bundles Monaco + its worker, no CDN))), `src/common/` (shared
  theme-driven primitives, one file each — `Button`, `Card`, `Input`/`Select`/`Textarea`/`Field`, `SearchSelect`
  (a searchable single-select pop-over — themed replacement for a long native `<select>`), `SchemaForm`
  (renders an editing form from a JSON Schema — string / number / bool / `dict[str,str]` map / `list[str]` /
  `list[Model]` / `$ref`-to-a-`$defs`-model (resolved) / enum / `X|None` / the `sql` `str|{dialect:str}`
  union (a textarea, or per-dialect textareas); anything else → a "edit in the raw editor" note. Fields are
  tabbed by their `x_group`; with `onNavigate` it renders `list[Model]` / nested-object props as drill-in
  rows, without it as inline accordions — a `list[Model]` of more than ~6 items also gets a **search box**
  that filters by each row's summary so a connector's dozens of queries are findable) + `SchemaNavigator` (a master-detail wrapper around `SchemaForm` — shows one level at a time
  with a breadcrumb of the path `nomasx1 / users_get / USR_ID`; the path is *segments*, the current
  schema/value derived from the root each render so edits keep it valid). The Phase-7 config-builder
  shell, used by `Settings/PoolsBuilder` (flat → plain `SchemaForm`) & `ConnectorsBuilder` (nested → `SchemaNavigator`)), `Tag`/`Mono`,
  `Banner`/`Pre`, `Spinner`/`Centered`, `PageLayout`, `Modal`/`ConfirmModal`, `layout` `Stack`/`Row`,
  `useIsLight`, plus `DataTable` + `DataTableFilter` (the generic TanStack grid — uppercase themed headers,
  global search (over *every* column — `getColumnCanGlobalFilter` is overridden so a column whose first row
  is `null` isn't excluded), a type-aware per-column filter row (text/number/date with an operator picked
  from a small labelled popover — `OpPicker`; boolean/enum as a select) + clear-all, sort (shift-click =
  multi), column drag-reorder/hide (the Columns menu has All/None) — columns are *not* user-resizable
  (`table-layout: auto`, so the browser content-sizes them; a `width` display hint or the narrow internal
  columns still pin a width), row grouping, CSV/Excel export via `xlsx`, paging, localStorage
  persistence per `tableId` (column visibility + order); ported from nomaubl — *not* barrelled, it pulls in `xlsx`) and `Markdown`
  (react-markdown — also *not* re-exported by `common/index.ts`, so each rides only its lazy page chunk);
  `common/index.ts` barrels the rest, pages import
  `{ Button, ... } from '../../common'`), `src/pages/<Screen>/index.tsx` (one dir per page,
  splitting helpers alongside — e.g. `TableView/ResultTable.tsx` + `TableView/styled.ts`),
  `src/components/` (app chrome: `Layout`, `Sidebar`, `SidebarMenu`, `ProfileModal`, `WorkspaceSelect`, `TabStrip`, `TabHost`),
  `src/tabs/TabsContext.tsx` (`useTabs()` — the open `/sql`+`/http` tabs + the active one, persisted to sessionStorage),
  `src/theme.ts` (tokens — colours/fonts/`fontSize`/`radius`/`shadow`/`glass`, all via CSS vars), `src/index.css`
  (the `:root`/`.theme-light` var sets + ambient gradient bg + thin scrollbar), `src/i18n.ts` +
  `src/locales/{en,fr}.ts`. **Rule: keep pages small (split helpers into `pages/<X>/`), reusable
  bits go in `common/`, plain logic/shapes go in `services/`/`types/` (no React), and styled
  components pull every colour/size/radius/shadow from `theme.ts` — no hard-coded hex/rgba.**
- `src/App.tsx` — `react-router-dom` v7; `/login`, `/oidc/callback`, and a `RequireAuth`
  `Layout` with children `/` (Connectors), `/chat` (Chat), `/settings` (Settings, superuser-only),
  and `/sql/:connector/:target` / `/http/:connector/:target` — the latter two are **thin `<TabRoute>`
  markers** that open/activate the matching tab; the actual screens render inside `<TabHost>` (in `Layout`),
  which keeps every open tab mounted (only the active one shown) so each keeps its state. The framework
  pages (`Connectors`/`Chat`/`Settings`) render via `<Outlet/>` (not tabs — AI gets a drawer later, the
  others a later phase); `TableView`/`HttpRunner` take `connector`/`query`(or `endpoint`) as props. All
  `React.lazy`-split; `Layout` shows `<TabStrip/>` (the tab bar, or the "Liberty" title when no tabs) and
  renders `<Outlet/>` + `<TabHost/>` inside a `<Suspense fallback={<Centered/>}>`.
- The pages: `Layout` (the shell — `Sidebar` + a `<TabStrip/>` bar + a fixed top-right
  utility pill: app-picker (`WorkspaceSelect` — a themed `SearchSelect` over the *apps* (menu-having connectors), shown when ≥2) · EN/FR · dark/light ·
  username→profile · sign-out), `Sidebar` (collapsible nav
  rail — when an app is active it leads with that app's menu tree (`SidebarMenu` — collapsible
  folders, leaf `NavLink`s to `/sql|/http`, from `GET /api/menus`) above a divider, then the
  framework links (Connectors / Assistant / Settings) + an external "API docs" link), `ProfileModal`
  (read-only "who am I" — username/email/provider/roles/permissions from the Principal; no
  self-service password change yet — the backend has no endpoint for it), `Connectors` (lists
  the accessible connectors from `useWorkspace()` — scoped to the picked app — drills to queries/endpoints),
  `TableView` (titled with the query's `description` (v1's `tbl_label`), else `label`, else the menu label —
  `services/menuLabels.findMenuLabel` walks the `GET /api/menus` trees; the technical `connector.query` is the
  mono subtitle; `auto_load` queries run on open. Param form from the query's `params`/`bind_params`, a
  "Max rows" input (blank = the configured cap; else sent as `?_limit=N`, DbVisualizer-style), plus a
  collapsible **`FilterPanel`** — one field per `filter`-flagged column (v1's `col_filter`, from `meta.columns`),
  each with an operator picker (contains / equals / notEquals / startsWith / endsWith — like the grid's); a column
  with an ENUM rule renders a value `SearchSelect`, a LOOKUP rule a `SearchSelect` whose options are `<code> — <description>`
  (`useLookupTables`; the user picks the label not the code), both implicitly `equals`. A column with `filter_from`
  (v1's `ly_tbl_filters`) **cascades** — when its source filter has a value, its LOOKUP options narrow to the
  lookup rows whose `column` matches it (client-side over the once-fetched rows; changing a source clears its
  dependents). A "Clear" button in the panel header resets
  all server filters. On Run it sends `:<col>` + `:<col>_op` for
  each filled field; the migration has wrapped such queries in
  `SELECT * FROM (<orig>) lib_flt WHERE …` so this actually pre-filters server-side before the grid loads (the
  in-grid TanStack filters then refine the loaded page; those `:<col>`/`:<col>_op` binds are kept out of the
  param form). SELECT → `GET` + the `DataTable`
  grid built from `result.columns`, honouring their display hints (label/hidden/width/align — `hidden` takes
  effect on first load and survives a stale saved grid state; `align` defaults from the type when not set —
  numbers right, booleans/checkboxes centred, everything else left — and is carried in the column's TanStack
  `meta` so the header lines up with the cells; a `visible_when` column is dropped from the
  grid entirely when a `field` server-filter is set to a value outside its allowed set — recomputed live as you change the FilterPanel) and `rule`
  — BOOLEAN → ✓ green / ✗ red, ENUM → the value's label, LOOKUP → split into a "(ID)" column (raw code)
  + a resolved-label column (fetched once, raw value tooltipped, italic-muted while fetching); sorts/filters
  run on the displayed value, rule rendering is visual-only. When the query has writable companions, an
  **Edit** toggle puts the *whole grid* into edit mode (v1's FormsTable batch model): every cell editable,
  "+ Add row" / per-row "duplicate" / **Import** (.xlsx/.csv → headers matched to *result columns by header
  text* — name / label / "(ID)"-suffixed, case-insensitive — so the sheet's column order doesn't matter; an
  imported row whose `key_columns` match a *loaded* row becomes an **edit** of that row (→ `update_query`),
  the rest are **new** rows (→ `insert_query`) — that's why the migration can collapse v1's upsert queries
  (`INSERT … ON CONFLICT` / `MERGE`) to plain `INSERT`/`UPDATE`) /
  multi-row copy-paste (a selection checkbox column → Copy → Paste) → new rows (added at the *top*); a per-row
  × marks an existing row for deletion (a status column shows +/●/− marks); **Save**
  fires the lot — edited rows → `update_query` (the merged new values, plus the row's pre-edit values under
  `:<NAME>_ORIGINAL` — the migration rewrites each `_put`'s WHERE to bind those, so editing a key column still
  updates the right row), new rows → `insert_query`, deleted → `delete_query` (params sent both as-is + UPPERCASE
  — PG lowercases the read columns, v1's `_put`/`_post`/`_delete` use uppercase; `text()` binds only what it
  references) — then refetches; **Cancel** discards. (Modal-form edit
  = the form layer, Phase 6.) A non-SELECT query → `confirm` + `POST` + affected-rows banner),
  `HttpRunner` (`POST /api/http/...` + pretty `ApiResult` + JSON `Pre`),
  `Chat` (consumes the `/ai/chat` SSE — user bubbles plain, assistant bubbles rendered via
  `<Markdown>`, + `tool_call`/`tool_result` lines), `Settings` (a tab switcher over the config editors —
  `PoolsBuilder` = the structured `[pools.*]` editor (a left list + a `SchemaForm` over the `PoolConfig`
  schema → `PUT /admin/config/pools` + Reload), `ConnectorsBuilder` = the `[connectors.*]` editor (a left
  list of sql/api connectors; for a SQL connector the right pane has two views — **Tables** (default:
  queries grouped by `<base>_<get|put|post|delete>` suffix — v1's "table/view/business object" concept —
  each table opens a unified `ConnectorsTableEditor` with tabs *General · Columns · Read · Update ·
  Insert · Delete*; General/Columns write to `<base>_get` since `columns`/`label`/`auto_load`/etc. only
  live on the read query; missing CRUD slots show a "+ Create" button; a **Duplicate** action (per row
  + in the editor header) deep-clones every CRUD slot under a new base name so a table can be forked +
  customized; loose non-CRUD queries are listed as a footnote that points to the Form view) and
  **Form** (the full connector `SchemaNavigator` — General/Pool/Queries, the escape hatch for the flat
  queries list and connector-level settings); API connectors only show Form. Saves go through
  `PUT /admin/config/connectors/parsed` + Reload),
  `DictionaryBuilder` = the structured `dictionary.toml` editor — sub-tabs for *Entries* / *Enums* /
  *Lookups* / *Framework* (the last overrides the bundled `framework_enums` registry — Shared scope
  only, hides the scope chips), a scope chip strip (*Shared* + one chip per connector overlay;
  "+ Add connector scope" for new) on the first three, per-record `SchemaNavigator` over the
  matching schema (`DictionaryEntry`/`EnumDef`/`LookupDef`; both `enums` and the framework overrides
  drill into `values: [{value, label, l}]`), search past ~6 records, each list row shows the
  record's `label` (or `description` for lookups) under the key so a numeric `[lookups.1]` is
  findable, top-level `default_language` input — → `PUT /admin/config/dictionary/parsed` + Reload),
  `MenusBuilder` = the structured `menus.toml` editor (a left list of apps + a right pane with an
  indented tree of `[[menus.<app>.items]]` built from the flat `items[]` linked by `parent`, plus
  a per-item inspector — SchemaForm over `MenuItem`, so `type` gets the `MENU_ITEM_TYPE` framework
  dropdown, translations land in their tab, advanced bits like `parent`/`params`/`roles` are their
  own tab). Per-row hover actions handle move ↑/↓, indent (reparent under the previous sibling),
  outdent (move to the grand-parent), add-child and recursive delete; the inspector lets you
  rename `id` and every child's `parent` ref follows. Filter past 6 items, search hits keep the
  filtered tree expanded down to each hit. Save → `PUT /admin/config/menus/parsed` + Reload), and
  `RawEditor` = the Monaco `connectors.toml` editor (`language="ini"`, theme-aware, over `GET/PUT
  /admin/config/connectors` + Reload — the escape hatch); the structured editors don't support
  rename of the *top-level key* yet — delete + re-add — the Phase-7 builder slices), `Login` +
  `OidcCallback`.
- Backend wiring: `liberty/main.py` mounts a `SPAStaticFiles` (StaticFiles with index.html
  fallback for client routes) at `/` **last** (so it never shadows `/api`, `/auth`, `/ai`,
  `/admin`, `/health`, `/info`, `/docs`); only mounts if `[app] static_dir` exists (default
  `frontend/dist` — absent on a fresh checkout → API-only, which is fine). New settings:
  `[app] static_dir`, `[oidc] frontend_redirect` (when set, `/auth/oidc/callback` redirects
  there with `#access_token=…&refresh_token=…` instead of returning JSON — for SPAs), `[menus] config_path`
  (default `config/menus.toml`). `/info` reports `frontend` and `menus.{apps}`.
- `frontend/.gitignore` excludes `node_modules/` and `dist/`; `package-lock.json` is committed.
  Dev: `cd frontend && npm install && npm run dev` (proxies the API paths to `:8000`);
  prod build: `npm run build` → `dist/` → served automatically by the backend (entry chunk
  ~112 kB gz; TableView/Chat/Settings split off into their own route chunks; Monaco is
  bundled — see below — so the Settings chunk is heavy but lazy, ~600 kB gz).
  Monaco is **bundled, not CDN-loaded** — `src/services/monaco.ts` imports the editor API +
  the `ini` basic language only, wires the editor worker via Vite's `?worker`, and calls
  `loader.config({ monaco })`; it's `import`-ed (side-effect) from the Settings page so it
  rides in that lazy chunk. So the app works offline (the only remaining CDN dep is the DM
  Sans webfont, which just falls back to system fonts). Still TODO toward full nomaubl parity:
  a self-service change-password flow (needs a backend endpoint), Vitest/RTL frontend tests,
  frontend build in CI. (`@tanstack/react-virtual` is wired — see the *Big-grid scaling*
  section below.) Reference app:
  `../../JavaProjects/nomaubl/src/web-react/`.

**Phase 5 (Migration tools) — IN PROGRESS.** `liberty/migrations/` + the
`liberty-migrate` CLI — turn a v1 Liberty DB's `ly_*` metadata into v2 `connectors.toml`:
- `v1.py` — pure transforms over row dicts: `slugify`; `migrate_sql_queries(ly_query rows,
  ly_qry_sql rows, dbtype=…, connector_prefix=…, column_hints=…)` → one **SQL connector per
  `query_pool`**, one query per `(query_id, query_crud)` named `<label>_<crud>` — the
  per-`query_dbtype` SQL variants become a `sql = { default = …, oracle = …, … }` dialect map
  (a single distinct statement collapses to a plain string; `--dbtype` keeps just one variant).
  v1's `query_crud` is a **REST verb** — `GET`/`SELECT` = read (gets `ORDER BY <query_orderby>`
  and the `column_hints` for its `query_id`; if any of those hints is `filter`-flagged the query is
  also wrapped — `SELECT * FROM (<orig>) lib_flt WHERE …` with a `:<col>` value bind + `:<col>_op` operator
  bind per such column (both, and the column, `CAST(… AS VARCHAR(4000))` — `VARCHAR2(…)` on Oracle variants — pins the bind's type so an
  *unset* filter's NULL bind doesn't trip asyncpg's "could not determine data type", and compares
  uniformly regardless of the column's real type; an empty/NULL value matches everything = "no filter"),
  the ORDER BY moving onto the outer query — so the TableView's `FilterPanel` actually pre-filters
  server-side), `POST`/`PUT`/`PATCH`/`DELETE` = write (`writable =
  true`). Pool stubs `[pools.<name>] url = "${LIBERTY_DB_URL_<NAME>}"` (overridden by
  `migrate_pools`). `migrate_api(ly_api_conn, ly_api,
  ly_api_header, ly_api_params, …)` → an **API connector per `ly_api_conn`** (`base_url=conn_url`,
  basic auth from `conn_user` + the v1 `conn_password` carried over **verbatim** — it's an
  `ENC:…` blob, and v2 decrypts it at runtime with the same key, see *Crypto* below) with
  endpoints from the `ly_api` rows; connectionless `ly_api` → a single
  `legacy_api` connector (`base_url=""`, absolute-URL paths); `migrate_pools(ly_applications
  rows, connector_prefix=…)` → real **`[pools.*]` from `ly_applications`** (one per `apps_pool`,
  URL = SQLAlchemy async URL built from `apps_dbtype`/`apps_host`/`apps_port`/`apps_database`
  — `postgresql+asyncpg://…` / `oracle+oracledb://…/?service_name=…` — or a parseable
  `apps_jdbc`, else the `${LIBERTY_DB_URL_<NAME>}` stub; `dialect` from `apps_dbtype`,
  `pool_size`/`max_overflow` from `apps_pool_min`/`apps_pool_max`, `max_rows` from `apps_limit`,
  `schemas` from `ly_db_schema` (`db_schemas=…` — `{sch_name: sch_target}` per `sch_pool`; a `sch_pool`
  with no `ly_applications` row gets a stub pool carrying just the `schemas`); the DB password is the pool's separate
  `password` field — **never inlined into the URL** (so URL-special chars don't break parsing): v1's
  `apps_password` `ENC:` value carried over **verbatim** (v2 decrypts it at runtime via the crypto master key,
  exactly as v1 reads it from the table), else a `${MIGRATED_PW_<NAME>}` env-var stub; v1's reserved `default`
  pool is **skipped** — v2's `[pools.default]` is v2's own framework DB); `migrate_table_meta(ly_tables rows, ly_dlg_frm rows)` →
  `{query_id: {description?, auto_load?}}` (the table/form friendly label `tbl_label`/`frm_label` → the read
  query's `description`, `tbl_auto_load = 'Y'` → `auto_load = true`; a table widget beats a form) — passed to
  `migrate_sql_queries(table_meta=…)`; `migrate_key_columns(ly_tbl_col rows, ly_dlg_col rows)` → `{query_id:
  [col, …]}` (the `col_key = 'Y'` columns) — passed as `key_columns=…`, attached to the **read** query as
  `key_columns` (the TableView's Excel import matches imported rows against the loaded ones on these to decide
  update vs insert). Separately, `migrate_sql_queries` normalises the write queries (v2's TableView splits
  update/insert, so v1's upserts are split apart): a `_post` upsert (`INSERT … ON CONFLICT` / Oracle `MERGE`)
  collapses to a plain `INSERT` (`_simplify_upsert`); a `_put` upsert collapses to a plain `UPDATE`
  (`_upsert_to_update` — its WHERE = the conflict / `ON` columns); and every `_put`'s WHERE is then rebound —
  every `:<col>` → `:<col>_ORIGINAL` (`_rewrite_put_where`; the SET clause untouched — the new value) — so
  editing a key column still updates the right row (the TableView sends the row's pre-edit values under
  `:<col>_ORIGINAL`); `migrate_column_hints(ly_tbl_col rows,
  ly_dlg_col rows, *, extra_filter_cols=…)` → `{query_id: [ColumnHint dict]}` (each `col_target` → `{name, dd?` (= v1's
  `col_dd_id` — only when ≠ `name`; the connector looks the entry up under `name` otherwise),
  `label?` (only when an explicit `col_label` overrides the dictionary), `hidden?` (`col_visible`
  reads false), `filter?` (`col_filter` reads true — table widgets only — *or* the column is in
  `extra_filter_cols[qid]` from `migrate_drill_filter_columns` below — without it the URL drill
  emitted by `migrate_context_menus` would land in a destination with no filter slot and the
  value would be silently dropped), `format?` (only when an explicit
  `col_type` overrides the dictionary)`}`; a column referenced by `extra_filter_cols` but with no
  v1 hint of its own gets a minimal `{name, filter}` row appended (case-insensitive match on
  column name); table-widget
  columns beat form-field columns; first `(query, col)` wins → per-query list keeps `col_seq` order)
  — passed to `migrate_sql_queries(column_hints=…)`, attached to each *read* query's `columns`;
  `migrate_drill_filter_columns(ly_ctx_val rows, ly_ctx_filters rows, tables_rows, dlg_frm_rows)`
  → `{query_id: [col_target, …]}` — for every v1 row-context-menu drill, the columns on the
  *destination* read query that the drill binds (the `ly_ctx_filters.flt_target` for `flt_type` ∈
  `{DD, VALUE}`); fed into `migrate_column_hints(extra_filter_cols=…)` so each becomes filter-flagged
  on the destination — `migrate_sql_queries`' `_wrap_with_filters` then binds `:COL` server-side
  and the URL drill actually narrows the destination;
  `migrate_table_filters(ly_tbl_filters rows, ly_dlg_filters rows)` → `{query_id: {col_target: [{source, column}]}}`
  (v1's `flt_source` → `source`, `flt_target` → `column`; table-widget rows beat form rows per `(query, col)`,
  dup `(source, column)` dropped) — passed to `migrate_sql_queries(column_filters=…)`, merged onto the matching
  column hint as `filter_from`;
  `migrate_column_visibility(ly_tbl_col rows, ly_dlg_col rows, ly_cdn_params rows)` → `{query_id: {col_target:
  [{field, value}]}}` — best-effort distillation of v1's conditional rendering: each column's `col_cdn_id` →
  the `ly_cdn_params` predicates for that condition; `<field> EQUAL <v>` predicates collapse to a `{field, value:[…]}`
  rule (the field name resolved via the column whose `col_dd_id` = it), `EMPTY` predicates are dropped ("or unset"
  is v2's default), rules AND-ed; a condition using any other operator → the column is left always-visible (a wrong
  hide is worse). v1's `ly_tbl_col_cdn`/`ly_dlg_col_cdn` link tables (extra OR branches v2 can't represent) aren't
  read. Passed to `migrate_sql_queries(column_visibility=…)`, merged onto the matching column hint as `visible_when`;
  `migrate_dictionary(ly_dictionary rows, ly_dictionary_l rows, enum_rows=(), enum_val_rows=(),
  enum_val_l_rows=(), lookup_rows=(), sql_rows=(), *, default_language="en", connector_name=None)`
  → the `dictionary.toml` dict — one `[entries.<dd_id>]` per `ly_dictionary` row (`label`=`dd_label`,
  `format`=a non-trivial `dd_type`, `rules`/`rules_values`/`default` verbatim, `[entries.<dd_id>.l]`
  = `{lng_id: lng_label}` from `ly_dictionary_l`); plus `[enums.<enum_id>]` from `ly_enum` (+
  `ly_enum_val` + `ly_enum_val_l` translations) and `[lookups.<lkp_id>]` from `ly_lookup` (its
  `lkp_query_id` resolved via *sql_rows* to the matching read query's v2 name + the slug of that
  query's `query_pool` as the lookup's `connector` — so a lookup pointing at a query on another
  connector resolves there, not the asking one; same logic as `migrate_menus`). `connector_name`
  nests all three sections under `[connectors.<name>.…]`. `migrate_menus(ly_menus rows, ly_menus_l rows, ly_tables rows,
  ly_dlg_frm rows, ly_qry_sql⋈ly_query rows, *, app_name, app_label=None)` → the `menus.toml` dict
  (`{"menus": {<app_name>: {label?, items}}}` — flat items in `menu_seq_ukid` order, linked by `parent`;
  a query-backed `menu_component` → a `type="query"` leaf whose `target` is `menu_component_id` →
  `ly_tables.tbl_id`/`ly_dlg_frm.frm_id` → `ly_query` → the exact name `migrate_sql_queries` gives that
  query's read variant, plus `connector` = the slug of that query's `query_pool` when it differs from
  `app_name` (so a `[menus.<app>]` leaf can point at a screen on another connector); an unresolvable
  component → a folder placeholder; `ly_menus_l` → `l`; v1's
  `ly_menus_filters` not migrated yet). `merge_connectors(*)` (pools merged last → real
  `migrate_pools` URLs override `migrate_sql_queries`'s stubs); `render_toml(d)` (via `tomli-w`).
  The `# migrated: …` header notes the counts + the `${…}` placeholders + any `ENC:` secrets +
  a reminder to run `liberty-migrate dictionary` when there are column hints.
- `source.py` — async `read_sql_queries(engine)` / `read_api(engine)` / `read_applications(engine)` /
  `read_dictionary(engine)` (→ `ly_dictionary` + `ly_dictionary_l` rows) /
  `read_dictionary_rules(engine)` (→ `ly_enum`, `ly_enum_val`, `ly_enum_val_l`, `ly_lookup`,
  `ly_qry_sql⋈ly_query` — the data the dictionary's display rules reference) /
  `read_menus(engine)` (→ `ly_menus`, `ly_menus_l`, `ly_tables`, `ly_dlg_frm`, `ly_qry_sql⋈ly_query`) /
  `read_table_meta(engine)` (→ `ly_tables` `tbl_query_id`/`tbl_label`/`tbl_auto_load` + `ly_dlg_frm`
  `frm_query_id`/`frm_label`) /
  `read_db_schemas(engine)` (→ `ly_db_schema` `sch_pool`/`sch_name`/`sch_target` — the `#SCHEMA.<NAME>#` maps) /
  `read_column_hints(engine)` → (`ly_tbl_col`←`ly_tables`←`ly_query`, `ly_dlg_col`←`ly_dlg_frm`←`ly_query`
  — `col_target`/`col_dd_id`/`col_label`/`col_seq`/`col_visible`/`col_type`/`col_filter`/`col_key`/`col_cdn_id` — `ly_dlg_col` has
  no `col_filter`, so it's aliased NULL; these rows feed `migrate_column_hints`, `migrate_key_columns` and `migrate_column_visibility`) /
  `read_table_filters(engine)` (→ `ly_tbl_filters`←`ly_tbl_col`←`ly_tables`, `ly_dlg_filters`←`ly_dlg_col`←`ly_dlg_frm`
  — `query_id`/`col_target`/`flt_source`/`flt_target` per cascading-filter rule; feeds `migrate_table_filters`) /
  `read_column_conditions(engine)` (→ `ly_cdn_params` rows — `cdn_id`/`cdn_dd_id`/`cdn_operator`/`cdn_value`, the
  predicates a column's `col_cdn_id` points at; feeds `migrate_column_visibility`)
  (SELECT-only; a missing table on an old v1 schema → `[]` *with a logged warning* — not silently swallowed; `make_engine(url)`
  accepts any async URL — `postgresql+asyncpg://…`).
- `liberty/menus/` — `config.py`: the `config/menus.toml` schema (`MenuItem`/`AppMenu`/`MenusFile`,
  `extra="forbid"`; validates unique ids, parents exist & don't cycle, folder-vs-leaf shape),
  `load_menus`/`parse_menus`, `build_menu_tree(app_menu, *, app, language, keep)` → nested dicts
  (labels resolved in *language*, `keep(item, connector)` prunes leaves, empty folders collapse).
- `liberty/migrate_cli.py` (`liberty-migrate` script) — `sql | api | all | dictionary | menu | screen`,
  `--source-url <v1-db-url>`, `--dbtype`, `--prefix`, `-o out.toml` (else stdout); `sql`/`all`
  also scaffold the `ly_applications` pools + carry over the `ly_tbl_col`/`ly_dlg_col` column
  hints + `ly_tbl_filters`/`ly_dlg_filters` cascading-filter deps + `ly_cdn_params` conditional-render
  rules (`visible_when`) + the `ly_tables`/`ly_dlg_frm`
  screen labels & auto-load flags (the hints reference the
  dictionary — also run `liberty-migrate dictionary -o config/dictionary.toml`);
  `dictionary [--default-language en] [--connector <app>]` migrates `ly_dictionary` (+ `ly_dictionary_l`)
  — `--connector` nests the entries under `[connectors.<app>.entries.*]` so several migrated apps don't
  clash on a `dd_id`; `menu --connector <app>` migrates `ly_menus` (+ `ly_menus_l`) → `config/menus.toml`
  (one `[menus.<app>]` per app — run `sql`/`all` first so the menu's query targets exist);
  `screen --connector <app>` migrates `ly_tables` (+ `ly_dialogs`/`ly_dlg_frm`/`ly_dlg_tab`/`ly_dlg_col`/
  `ly_dlg_filters`) → `config/screens.toml` — one ``[screens.<app>.<id>]`` per ``ly_tables`` row,
  ``connector`` only set when the query's pool differs from ``<app>``, ``read_query`` /
  ``update_query`` / ``insert_query`` / ``delete_query`` resolved against ``ly_qry_sql`` (the
  v2 name keeps the **raw** v1 ``query_crud`` verbatim — matches what ``migrate_sql_queries``
  emits in ``connectors.toml``), and an inline ``dialog`` built from the form's tabs/fields when
  ``tbl_frm_id`` resolves. (Phase 6 slice 1 — runtime + builder wiring still pending.) Prepends a
  `# migrated: …` summary + the `${…}` placeholders the operator must fill in (incl. each
  `${MIGRATED_PW_*}` — recover from `ly_applications.apps_password` with `liberty-crypto decrypt`).
v1 (`../liberty-framework/`) is **read-only** — the readers only SELECT. The output is a
fragment to review + merge into `config/connectors.toml` (the `dictionary` output → `config/dictionary.toml`).
**`liberty-migrate diff`** (shipped) — validate-by-diff harness: walks every v1 row
(pools / SQL queries / dictionary entries + enums + lookups + sequences / screens /
menu items / API connectors + endpoints) and verifies each has a matching v2 entity.
Surfaces what's missing (a sequence not in `dictionary.toml`, a screen not in
`screens.toml`, a query whose v2 name doesn't exist), what diverges (a screen whose
column count is far from v1's `ly_tbl_col`/`ly_dlg_col` total — usually means
re-running `liberty-migrate screen` would pick up new hints), and what's a stub
(`${LIBERTY_DB_URL_X}` placeholder pools the operator must fill in). Text or JSON
output (`--format json` feeds CI / tooling). Exit code `1` when missing /
mismatched entries land, `0` otherwise — drives pre-deploy checks. Real-data
smoke against libnjde found 11 missing + 6 mismatched entries on the first run,
guiding what to re-migrate next.

Lives in `liberty/migrations/diff.py` (the engine — pure-ish, async readers from
`source.py` + sync v2 loaders, returns a `DiffReport` of `DiffEntry` rows with
`kind` / `severity` / `entity_id` / `message` / `details`) and is wired as the
`diff` subcommand in `liberty/migrate_cli.py`. Tests in
`tests/test_migration_diff.py` (12 cases against a SQLite-backed minimal v1
schema, covers each entity kind + the text / JSON output shapes).

*Not yet done:* migrate v1's `AUD_<table>` audit data (Slice 5 wired the audit
*interceptor*, but the historic rows aren't carried over); migrate the real apps
(nomasx1 → NOMAJDE → AIRFLOW). Deps: `tomli-w`.

**Crypto (field-level secrets, v1-byte-compatible).** v1 stores some DB columns
encrypted (e.g. `SETTINGS_APPLICATIONS.password`, `ly_api_conn.conn_password`) and the
user's other scripts read those — so v2 reuses **the exact same scheme and key**, it does
*not* re-encrypt anything:
- `liberty/crypto.py` — AES-256-GCM, key = PBKDF2-HMAC-SHA512(master_key, salt, 2145 iters,
  32 bytes), wire format `"ENC:" + base64(salt[64] ‖ iv[16] ‖ tag[16] ‖ ciphertext)` — bit
  for bit what v1's `Encryption` writes (a test round-trips against an independent v1
  reimplementation, both directions). `encrypt`/`decrypt` (raise `CryptoError`),
  `is_encrypted`, `encrypt`/`decrypt` are **idempotent on an `ENC:` value**,
  `decrypt_if_needed`, `decrypt_or_keep` (never raises → `(value_or_plain, err_or_None)`).
- The master key lives in `[crypto] master_key` in `config/app.toml` (`= "${LIBERTY_MASTER_KEY}"` —
  always supplied via the env var, never hard-coded; it's the `MASTER_KEY` from v1's `secrets.json`).
  `liberty/config.py` → `Settings.crypto.master_key`; `/info` reports `crypto.configured` (bool, never the key).
- `APIConnector` decrypts `ENC:` `auth_username`/`auth_password`/`auth_token` at init, and
  `PoolRegistry` decrypts a pool's `password` (or an `ENC:` password embedded in its URL) when it
  builds the engine — both via the `master_key` threaded through `load_connectors(master_key=…)` /
  `ConnectorRegistry(master_key=…)` → `PoolRegistry(master_key=…)` (from `settings.crypto.master_key`
  in `main.py`'s lifespan and `POST /admin/reload`). Best-effort: a wrong/missing key → the value is
  left as the `ENC:` blob and a warning is logged (the connector / pool still loads — the connection
  then fails loudly with bad credentials, like v1 with the wrong key). Plaintext values pass through
  untouched. `describe()` still never exposes credentials.
- `liberty/crypto_cli.py` (`liberty-crypto` script) — `encrypt <v>` / `decrypt <ENC:…>` /
  `is-encrypted <v>` (exit 0/1); `--master-key` / `--config` overrides; reads stdin when no
  value arg; key comes from `[crypto] master_key` otherwise. For poking at values / scripting.
- v1's *other* crypto (the Fernet wrapper around `secrets.json` → `secrets.json.enc`) is
  **not** ported — v2 takes the `MASTER_KEY` straight from an env var. Only the field-level
  `ENC:` scheme above is shared.
- Operator runbook (when you need the key, how to set it, `liberty-crypto` recipes):
  `docs/crypto.md`. (The `admin` user from `liberty-admin init-db` is Argon2id, *not* `ENC:` —
  unaffected by the master key.)

**License (gates the licensed apps).** The open framework is free; connectors marked `licensed = true`
in `connectors.toml` (nomasx1 / nomajde do — they're sold together, one key) are unlocked by a license
key — an RS256-signed JWT the vendor signs with a private key (a separate key-gen tool, ported from
nomaubl's `LicenseVerifier`; **same JWT shape and key-pair as nomaubl**). v2 only *verifies*:
- `liberty/licensing/__init__.py` — `verify_license(key, *, public_key_pem=None)` → a frozen `LicenseResult`
  (`mode` "full"|"restricted", `customer`/`email`/`plan`/`apps`/`expires_at`/`error`, `.valid`,
  `.covers(connector_name)`, `.public_dict()`). RS256 verified with the embedded `liberty/licensing/public.pem`
  (a public key — safe to commit; replace it if you regenerate the key-pair) via `cryptography` (already a dep —
  no JWT library); checks the signature + `exp`; never raises. Claims: `customer`/`email`/`plan` (informational),
  `apps` (optional list of connector names this key covers — absent ⇒ covers every `licensed` connector), `exp`
  (epoch seconds — absent ⇒ no expiry). An empty key → restricted (the normal open-framework state).
- `[license] key` in `app.toml` (`= "${LIBERTY_LICENSE_KEY}"` — always via env var). `Settings.license.key`.
- `main.py` lifespan verifies it once → `app.state.license`; `load_connectors(…, license=…)` drops any
  `licensed = true` connector the key doesn't cover (logged) — so a fresh open checkout simply doesn't load
  nomasx1/nomajde. `POST /admin/reload` re-verifies. `/info` reports `license.{mode}`; `GET /api/license`
  (auth required) returns the full `public_dict()`; the frontend (`WorkspaceContext.license`) shows it in the
  ProfileModal and a banner when a *configured* key is broken (expired / bad signature — not for "no key").
- `liberty/license_cli.py` (`liberty-license` script) — `verify [<key>]` / `status` → JSON status (exit 0 if
  full, 1 if restricted); reads the key from the arg / stdin / `[license] key`; `--public-key PATH` overrides.

**Phase 6 (Form/screen engine) — slice 1 (Screen + ParamBind + migration) DONE.** Lives in
`liberty/screens/` — the v2 collapse of v1's ``ly_tables`` → ``ly_dialogs`` → ``ly_dlg_frm`` →
``ly_dlg_tab`` → ``ly_dlg_col`` → ``ly_dlg_filters`` chain into one ``Screen`` entity per
business object:
- `liberty/screens/config.py` — Pydantic shape for ``config/screens.toml`` (``[screens.<app>.<id>]``
  per screen): ``Screen`` (``id``, ``label``, ``description``, ``connector?``, ``read_query``,
  ``update_query?``/``insert_query?``/``delete_query?``, ``auto_load``, ``audit``, ``editable``,
  ``uploadable``, ``dialog?``, ``actions`` & ``row_menu`` — placeholders for slices 4 & 6), inline
  ``ScreenDialog`` (``title?``, ``tabs[]``) → ``ScreenTab`` (``id``, ``label?``, ``l``, ``cols?``,
  ``hide_on_add``/``hide_on_edit``, ``fields[]``) → ``ScreenField`` (``name``, ``dd?``, ``label?``,
  ``hidden``/``disabled``/``required``, ``colspan?``, ``default?``, ``lookup_param_binds[]``), and
  the unified ``ParamBind`` (``{param, value | source, default?}``) — v2's port of v1's ``ly_dlg_filters``
  (and ``ly_tbl_filters`` / ``ly_dictionary_filters``: one mechanism for every kind of parameter
  passing — dialog lookups, action arguments, row menu triggers, etc.). ``default`` is the
  fallback bound when *source mode* resolves to NULL / empty at call time — v2's port of v1's
  ``ly_act_tasks_params.map_default``; accepts a literal (``"0"``), a built-in (``"#SYSDATE#"``),
  or a chain-context path. ``actionRunner.resolveBinds`` dispatches by default's shape so a
  workflow step's missing input lands a sane fallback instead of being dropped. ``parse_screens`` injects
  each screen's ``id`` from its dict key; a mismatched explicit ``id`` is rejected. ``load_screens``
  → an empty ``ScreensFile`` when no file.
- `liberty/migrations/source.py::read_screens(engine)` returns 8 row-sets (the seven dialog tables
  + ``ly_qry_sql ⋈ ly_query`` for v2-name resolution); missing tables → ``[]`` with a logged warning.
- `liberty/migrations/v1.py::migrate_screens(*, app_name=…)` builds the ``screens.toml`` dict:
  one Screen per ``ly_tables`` row, ``id`` from ``slugify(tbl_db_name | tbl_label | screen_<id>)``
  (deduped via ``_2``/``_3``), CRUD slot map (REST + SQL keywords) routes the resolved v2 name
  into ``read_query`` / ``update_query`` / ``insert_query`` / ``delete_query``; the **v2 name itself
  keeps the raw v1 ``query_crud`` verbatim** so it lines up with what ``migrate_sql_queries`` emits
  in ``connectors.toml`` (e.g. v1 ``GET`` → ``users_get``, v1 ``SELECT`` → ``users_select``).
  ``connector`` is set only when the query's pool ≠ ``app_name``. With ``tbl_frm_id`` resolvable,
  the screen also gets a ``dialog`` — tabs from ``ly_dlg_tab`` (in ``tab_seq`` order, translations
  from ``ly_dlg_tab_l``), fields from ``ly_dlg_col`` (placeholder rows with empty ``col_target``
  dropped), per-field ``lookup_param_binds`` from ``ly_dlg_filters`` (``flt_type='VALUE'`` →
  ``{param, value}``, ``flt_type='DD'`` → ``{param, source}``).
- `liberty/migrate_cli.py` — `screen --connector <app>` subcommand; summary line reports
  ``N screen(s) for [screens.<app>] — X with dialog, Y with audit, Z cross-connector, F dialog
  field(s), B param-bind(s)``.
Real-data check (the user's live DBs): 96 screens for nomasx1 (14 with dialog, 6 with audit, 122
fields, 2 param-binds) and 12 for nomajde (6 dialogs, 10 cross-connector, 199 fields, 2 param-binds)
migrate cleanly.

**Phase 6 slice 1f (runtime + builder wiring) — DONE.**
- `liberty/config.py::ScreenSettings` adds ``[screens] config_path`` (default ``config/screens.toml``);
  ``./start.sh init-config`` now also seeds ``screens.toml`` from the shipped ``.example`` template.
- `liberty/main.py` lifespan loads it into ``app.state.screens``; ``/info`` reports
  ``screens.{apps, total}``.
- `liberty/web/screens.py` — three permission-pruned routes:
  ``GET /api/screens`` (every accessible screen per app, list view — no dialog body, no actions),
  ``GET /api/screens/{app}`` (one app's accessible screens; 404 when nothing survives), and
  ``GET /api/screens/{app}/{id}`` (the full screen including ``dialog``/``actions``/``row_menu`` —
  same shape ``Screen.model_dump`` produces). Permission gate: a screen is shown iff the caller
  holds ``sql:{effective_connector}:{read_query}`` (``effective_connector`` = the explicit ``connector``
  field, else the app name — same convention :func:`migrate_screens` uses). Single-screen 403s
  surface as 404s so we don't leak existence — matches the connectors-route convention.
- `liberty/web/admin.py` — ``POST /admin/reload`` now re-reads ``screens.toml`` (reply carries
  ``screen_apps``); ``GET /admin/config/schema`` now includes ``screens`` (the ``ScreensFile``
  shape with its ``$defs`` — ``Screen``, ``ScreenDialog``, ``ScreenTab``, ``ScreenField``,
  ``ParamBind``, ``ScreenAction``); ``GET/PUT /admin/config/screens/parsed`` for the builder
  (the PUT runs the payload through :func:`parse_screens` so each screen's ``id`` is injected
  from its dict key, then surgically replaces the ``[screens]`` table via ``tomlkit`` — comments
  outside it survive).
- Frontend: ``src/types/config.ts`` adds ``ScreensDoc`` / ``Screen`` / ``ScreenDialog`` /
  ``ScreenTab`` / ``ScreenField`` / ``ParamBind``; ``src/pages/Settings/ScreensBuilder.tsx`` (a
  new lazy-loaded tab) — app chips on the left + per-app screen list (search past ~6); right
  pane drills into the selected screen via ``SchemaNavigator`` over the ``Screen`` ``$def``
  (dialog → tabs → fields → ``lookup_param_binds`` all become drill-in rows). Renaming a screen
  via the inspector ``id`` field moves the dict key. Save → ``PUT /admin/config/screens/parsed``
  + Reload. (No frontend *consumer* for ``GET /api/screens`` yet — that's slice 2's job, the
  TableView opens the dialog from it.)

**Phase 6 slice 2 (Dialog runtime) — DONE.** Lives in `frontend/src/pages/TableView/`:
- `ScreenDialog.tsx` — the modal form. Built from a Screen's `dialog`: tabs (filtered by
  `hide_on_add` / `hide_on_edit` for the current mode), each tab a CSS grid `cols` wide, each
  field's widget picked from the matching read-result `column.rule`: BOOLEAN → checkbox, ENUM →
  `SearchSelect`, LOOKUP → `SearchSelect` whose options come from `useLookupTables` (with
  `lookup_param_binds` resolved at call time — `value` literal + `source` reading the live form
  state, fed into the lookup spec's `params`, so a UDC-style WHERE narrows correctly), date /
  number / text from the column's `format`/`type`. `hidden` skips the field; `disabled` renders
  a read-only echo; `required` flags the label; `colspan` widens; `default` seeds on `add`. Save
  POSTs to `/api/sql/{connector}/{update_query|insert_query}` with the row's values (uppercased
  + `:<COL>_ORIGINAL` keys on edit — same convention the inline grid editor uses). Form state is
  keyed by `ScreenField.name` (whatever case the screen uses), and seeding reads from the DB row
  case-insensitively (Postgres folds unquoted identifiers to lowercase; v1 migration emits
  uppercase) so the form picks the right initial value either way.
- `ResultTable.tsx` — when the workspace's `findScreen(connector, query)` returns a hit *and*
  the screen has a dialog, the toolbar gains a primary "Add row" (opens the dialog in `add`
  mode) and clicking a non-grouped row opens the dialog in `edit` mode for that row. The
  existing inline "Edit" (now "Bulk edit") batch flow stays as the fallback / power-user path.
  `onSaved` refetches the query so the grid reflects the new state.
- `frontend/src/common/DataTable.tsx` — adds an `onRowClick` prop; the click handler bails on
  interactive children (`input/button/a/select/textarea/label`) so cell widgets and the column-
  header menus keep working without firing the dialog underneath.
- `frontend/src/workspace/WorkspaceContext.tsx` — fetches `GET /api/screens` after login and
  exposes `screens` (list-view, no dialog body) plus a `findScreen(connector, read_query)`
  helper. A connector + read_query appearing in two screens (a config bug) → `null` (safer than
  silently picking one).
- `frontend/src/types/screens.ts` — the runtime shapes (`ScreenListItem` / `ScreenDetail` /
  `ScreenTab` / `ScreenField` / `ParamBind` / `ScreensByApp`); see also the Settings-builder
  shapes in `types/config.ts`.
- EN/FR i18n strings added (`table.bulkEdit`, `common.no`/`common.pick`, `dialog.*`).
- Password fields (a column with ``format = "password"``, v1's PASSWORD rule) are *never* seeded
  with the stored value (the column holds a hash / ``ENC:`` blob — leaking that in the dialog is
  a security issue). They render as ``<input type="password">`` with a "leave blank to keep"
  placeholder; submit drops blank password fields *and* strips them from the ``:<COL>_ORIGINAL``
  binds (the migrated ``_put``'s SET only binds ``:PASSWORD`` if the user typed a new one — the
  DB column keeps its current value when blank).

**Multi-table writes from one dialog (v1's ``FormsDialog``).** v1's NOMASX1
``settings_applications`` screen wrote to 3-4 tables on save (the apps row + its JDE settings
+ its LDAP settings, all on one PK), orchestrated by ``liberty-core``'s ``FormsDialog``. v2's
solution is now in place — see slice 4 below for the ``Action`` union and slice 2b for
``NestedFormTab`` / ``NestedTableTab``. Two equally idiomatic routes depending on the layout:
either (a) the dialog's ``on_save`` runs a sequence of ``run_query`` actions, each binding
the PK via ``ParamBind`` to write its own table; or (b) the dialog has nested-form tabs (one
per related table), each with its own ``read_query`` + ``update_query`` + ``insert_query``,
all bound by the parent's PK — the parent dialog's Save walks the saver registry sequentially.
No special ``compound_dialog`` shape; same ParamBind mechanism as field lookups, actions,
and row menus.

**Phase 6 slice 2b (Nested tabs + dialog UX polish) — DONE.**
- **Nested tab kinds** (`liberty/screens/config.py`): ``ScreenTab`` is now a discriminated union
  over three variants — ``FormTab`` (the default, plain grid of fields — pre-existing), plus
  ``NestedFormTab`` and ``NestedTableTab``. A nested-form tab embeds a child-record form inline
  (v2's port of v1's "FormsDialog inside FormsDialog" — same APPS_ID, extra columns on related
  tables; e.g. NOMASX1's ``settings_applications`` JDE / LDAP tabs). Its own ``read_query`` is
  bound by ``param_binds`` against the parent's form state — a returned row → edit mode (saves
  via the nested ``update_query``), no row → add mode (saves via ``insert_query``). A nested-table
  tab embeds an entire related-rows TableView by *referencing another v2 screen by id* — that
  screen's read query + columns + dialog + actions get re-used inside the parent dialog tab
  (e.g. a parent's "Activity Log" tab points at ``settings_activity_log``, narrowed by APPS_ID).
  ``parse_screens`` defaults a tab missing ``type`` to ``"form"`` so older screens.toml files
  keep validating. Each tab kind shares the ``_ScreenTabBase`` core (``id`` / ``label`` / ``l`` /
  ``hide_on_add`` / ``hide_on_edit`` / ``actions`` — the per-tab buttons; see slice 4).
- **Frontend runtime** (`frontend/src/pages/TableView/NestedTab.tsx`): ``NestedFormView`` mounts
  inside the parent ``ScreenDialog`` (one per nested_form tab), fetches the linked row on open
  (binds resolve from the parent's live form state), renders editable ``FieldRow``s with the
  shared widget switch, and **registers a saver** via ``NestedSaversContext`` — the parent's
  Save walks the registry sequentially after its own update/insert succeeds (a throwing saver
  surfaces on the parent's banner; the main row stays written so the operator retries the
  nested save). ``NestedTableView`` mounts a fully-interactive ``DataTable`` (with sub-dialog
  on row-click — opened as a ``ScreenDialog nested`` variant so the parent stays visible
  behind a smaller, auto-height modal on a bumped-z-index ``NestedOverlay``). Password columns
  are filtered out of nested grids too (no ENC: leak).
- **Promoted row-click dialog** (the *row_click_screen* pattern in `Screen`): when a screen has
  no own ``dialog`` but has a single FormsDialog ctx-menu entry (the conventional v1 "Display
  Properties" / "Edit details" action), the migrator promotes that entry to a **row-click
  target**. The frontend fetches the target screen's detail + binds the clicked row's columns
  into the target's read_query, then opens *that* screen's dialog as a modal on click — same
  affordance as having an inline dialog. Set via ``Screen.row_click_screen`` /
  ``row_click_connector`` / ``row_click_binds``; the matching ctx-menu entry is dropped during
  migration so the same affordance doesn't double up.
- **Dialog UX polish** (`frontend/src/pages/TableView/ScreenDialog.tsx`):
  - **Delete button** in the footer (edit mode + ``delete_query`` set). Single confirm modal,
    fires ``handleDelete`` → POST to ``delete_query`` + runs ``screen.on_delete`` chain (slice 4)
    with the deleted row's values. v1 didn't expose this on the dialog (delete lived on the
    table) — v2 surfaces it where the operator already is.
  - **Unsaved-changes guard** (Save / Discard / Stay). ``isDirty`` compares ``formValues`` vs the
    seeded original; Cancel + click-outside both gate on this. Save reuses the main submit;
    Discard runs ``on_cancel`` then closes; Stay dismisses the prompt.
  - **Tab actions live in the footer** (left side, ``marginRight: auto``) — they used to sit at
    the top of the body, which on a tab with a nested table required scrolling past the grid to
    reach Import Security / Merge Roles. The footer keeps them always visible.
  - **Password fields are never seeded** with the stored ciphertext — render as ``<input
    type="password">`` with a "leave blank to keep" placeholder; submit drops blank password
    fields *and* strips them from ``:<COL>_ORIGINAL`` binds. (The migrated ``_put``'s SET only
    binds ``:PASSWORD`` if the user typed one.)
  - **ModalBody is flex-fill** (`flex: 1 1 auto; min-height: 0`) and **DataTable's table stretches
    to fit** when its content is narrower than the viewport (``style={{ minWidth:
    table.getTotalSize() }}`` — was ``width``). Both make tall tab content scroll inside the
    body while the footer stays pinned at the bottom of a fixed-height ``ScreenDialogModal``.
  - **SearchSelect is portal-rendered** (`document.body` + position-fixed coords from
    ``getBoundingClientRect()``) so a dropdown inside a tall dialog doesn't get clipped by the
    modal's own ``overflow: auto`` body. **Checkbox** is rewritten as a native input overlaid on
    a styled box (opacity 0) so label-click reliably toggles.
- Real-data check: NOMASX1's ``settings_applications`` screen (the JDE+LDAP+activity tabs case)
  round-trips through the nested_form + nested_table tab kinds; security_users right-clicks open
  the promoted "Display Properties" dialog (no own ``dialog`` set; row_click_screen points at
  the matching screen).

**Phase 6 slice 3 (Per-field conditions) — DONE.**
- `liberty/screens/config.py` — new ``FieldCondition`` shape (``{field, value: str | list[str]}``)
  mirroring :class:`liberty.connectors.config.VisibleWhen` but for the form context: ``field``
  names another field on the *same dialog* (not a server filter), and the predicate holds when
  that field's current form value equals ``value`` (or is in ``value`` when a list). ``ScreenField``
  grows three lists — ``visible_when`` / ``required_when`` / ``disabled_when`` — each AND-ed;
  when a list is non-empty *and* every predicate holds, the rule fires (the field shows / is
  required / is locked); the static flags act as the fallback when the corresponding ``*_when``
  list is empty. (v1's `dd_rules` form-side auto-fills — SYSDATE / CURRENT_DATE / LOGIN — are now
  wired via the dictionary's `auto_fill` rule kind: the resolver emits
  `{kind:"auto_fill", source:"current_date"|"login_user"}` and ScreenDialog's add-mode seeder
  calls `actionRunner.resolveAutoFill` to set the field's initial value. Explicit
  `ScreenField.default` still wins; edit mode keeps the row's stored value untouched.)
- `liberty/migrations/v1.py` — the cdn-graph parser (``_cdn_to_field_groups`` / ``_cdn_resolve``)
  is factored out and shared between :func:`migrate_column_visibility` (grid columns) and
  :func:`migrate_screens` (dialog fields). ``migrate_screens(*, cdn_param_rows=…)`` resolves each
  field's ``col_cdn_id`` to ``visible_when`` against a *per-frm* dd→target map (predicate's
  ``cdn_dd_id`` resolves to the col_target of another field on the same form). Unsupported
  operators (NOT_EQUAL / LIKE / …) leave the field unconstrained (always-visible) with a logged
  warning — same conservative bias as the grid migration. ``liberty/migrations/source.py`` now
  reads ``col_cdn_id`` on ``ly_dlg_col``. The CLI summary reports an extra ``N conditional
  field(s)`` count.
- ``required_when`` / ``disabled_when`` have no v1 source migrated yet — operators set them in
  the Settings → Screens editor; the runtime evaluates them.
- Frontend: ``ScreenDialog`` evaluates the three lists at render time against ``formValues`` —
  fields hide / require / lock as the user types in their gating fields; a field hidden by
  ``visible_when`` is also dropped from the submit body (so an irrelevant column keeps its
  current DB value — same v1 behaviour). The condition evaluator matches field names
  case-insensitively (Postgres lowercases identifiers, v1's migration emits uppercase).
- ``ScreensBuilder``'s ``ScreenEditor`` now has a ``conditionsSchema`` SchemaForm in the
  expanded field row alongside the existing props + binds editors; the collapsed row gets a
  "conditional" orange badge when any of the three lists is non-empty.
Real-data smoke: 0 conditional fields on nomasx1 (none of the migrated screens used
``col_cdn_id``); **15** on nomajde — notably JDE F00950 (Security Workbench) where field
visibility depends on SEC_TYPE / FSSETY pickers. Migrates cleanly; round-trips through the
schema.

**Phase 6 slice 4 (Actions, events & lifecycle hooks) — DONE.**
- **Action union** (`liberty/screens/config.py`) — discriminated by ``type`` over 7 variants:
  ``RunQueryAction`` / ``CallApiAction`` / ``NavigateAction`` / ``SetFieldAction`` /
  ``ConfirmAction`` / ``NotifyAction`` / ``RefreshAction``. Each carries the common
  ``{id, label?, stop_on_error=true}``; the three ParamBind-bearing variants (run_query /
  call_api / navigate) reuse the same :class:`ParamBind` shape used for lookups + cascading
  filters + row menus. **One mechanism per attachment point**; the schema is the same
  everywhere.
- **Attachment points** — actions hang off a screen at several lifecycle moments, all built on
  the same Action union:

  | Attachment | When it fires | v1 source |
  |---|---|---|
  | ``ScreenDialog.on_load`` | After the dialog opens + row data is loaded (edit) or defaults seed (add) | — (v2 extension) |
  | ``ScreenDialog.on_save`` | After the dialog's main update/insert succeeds | ``ly_evt_cpt`` FormsDialog evt 1 |
  | ``ScreenDialog.on_cancel`` | When the user closes without saving (Cancel / click-outside / Discard) — *blocks* the close on failure | — (v2 extension) |
  | ``FormTab.actions`` (every tab kind) | Per-tab toolbar buttons (in the footer) | ``ly_dlg_col col_component='InputAction'`` |
  | ``Screen.actions`` | Toolbar buttons above the table | (none — used to be heuristic; now empty unless hand-wired) |
  | ``Screen.row_menu`` | Right-click row context menu (slice 6) | ``ly_ctxmenus`` (via slice 6b) |
  | ``Screen.on_insert`` | After a row is inserted (dialog Save in add mode *or* batch-edit grid Save) | ``ly_evt_cpt`` FormsTable evt 2 |
  | ``Screen.on_update`` | After a row is updated (dialog Save in edit mode *or* batch-edit grid Save) | — (v2 extension) |
  | ``Screen.on_delete`` | After a row is deleted (dialog Delete *or* batch-edit grid Save) | ``ly_evt_cpt`` FormsTable evt 3 |

- **Migration: event-driven via ``ly_evt_cpt``** (`liberty/migrations/v1.py::attach_actions_to_screens`).
  v1's ``ly_evt_cpt`` is *the* schema-level attachment table — each row says "event N on
  component C fires action A". The migrator walks it:

  * ``FormsDialog`` evt_id 1 (Save) → ``dialog.on_save``. The action's tasks become a sequential
    ``run_query`` chain. **The first task is skipped when its query matches the screen's
    update/insert** (the dialog Save already runs that — otherwise the row would be inserted
    twice). Falls back to ``Screen.actions`` when the screen has no dialog.
  * ``FormsTable`` evt_id 2 (row insert) → ``Screen.on_insert`` — fires after batch-edit grid
    insert + after dialog Save in add mode.
  * ``FormsTable`` evt_id 3 (row delete) → ``Screen.on_delete`` — fires after batch-edit grid
    delete + after dialog Delete. **Distinct from on_save** so the previous "everything onto
    on_save" model can't double-fire a Delete chain on Save.

  Deduped by ``(target_screen, hook, v1_act_id)`` — when both a FormsDialog and its FormsTable
  point at the same action, the chain is wired once per hook. Idempotent: re-running scrubs
  prior auto-attached entries (``id`` starting with ``migrated_``) from every hook before
  re-attaching, so hand-wired entries (without the prefix) survive untouched.

- **Migration: ``ly_dlg_col col_component='InputAction'`` → ``FormTab.actions``**
  (`migrate_screens::_input_action_to_button`). v1 placed manual workflow buttons *inside* a
  dialog tab via dlg_col rows with that special component. The migrator emits one v2 Action
  per such column on the matching tab's ``actions`` list — picking the underlying action's
  first task with a resolved v2 query as the button's ``run_query``. Multi-task workflows get a
  ``(1/N)`` hint in the label so the operator notices the full chain isn't wired (the rest
  lives in ``migrated_actions.toml`` for hand-wiring). InputAction detection is
  **cross-cutting**: works even when the same tab carries a nested ``FormsTable`` (NOMAJDE's
  "Roles" tab had Import Security + Merge Roles + a roles-of-this-user table all together).

- **Frontend runtime**:
  * `ScreenDialog.tsx` — ``runOnSaveActions(actions, ctx)`` walks any of the hook lists
    sequentially. Each action's ParamBinds resolve against the running ``ctx`` (the dialog
    form's live state for dialog hooks, the row's values for grid-save hooks). Implemented now:
    ``run_query`` (POST /api/sql with bound + uppercased params, falls back to the screen's
    effective connector), ``notify`` (collected as warnings), ``refresh`` (signals the caller
    via the returned flag). Stubbed: ``call_api`` / ``set_field`` / ``confirm`` log a
    console.warn and abort the chain when ``stop_on_error = true`` (the default). The
    ``navigate`` action is implemented in the ResultTable / row-menu / toolbar paths (URL
    push with the bound ParamBinds as query string), so the dialog runner's stub is for the
    rare on_save-side use.
  * Per-tab actions (``FormTab.actions`` / ``NestedFormTab.actions`` / ``NestedTableTab.actions``)
    fire via ``fireTabAction`` — same runner, single-action chain, surfaces the result on the
    dialog's status banner.
  * ``ResultTable.tsx`` runs ``Screen.on_insert`` / ``on_update`` / ``on_delete`` after batch-
    save success (one chain per affected row, with that row's values as context).
- **Builder** (`ScreenEditor.tsx`) — every hook attachment point has the same expandable
  action-row editor: type picker (SearchSelect over the 7 variants; switching seeds a
  minimum-viable shape via ``blankActionOfType``), then the matching ``$def``-driven SchemaForm
  beneath. ``ParamBind`` editor renders inline. The Pydantic union's ``$defs`` for every
  variant ride along on ``GET /admin/config/schema`` so the builder resolves them. (UI for
  ``on_load`` / ``on_cancel`` / ``on_insert`` / ``on_update`` / ``on_delete`` / per-tab
  ``actions``: the schema endpoint already ships every needed ``$def``; the matching editors
  in ``ScreenEditor`` are a follow-up slice.)
- **Multi-table writes** (NOMASX1 ``settings_applications`` → apps + apps_jde + apps_ldap on
  one PK) now expressible as one ``update_query`` (the apps row) + two ``run_query`` actions
  on ``dialog.on_save``, each binding the PK via ParamBind. Same mechanism as field lookups
  and row menus.
- **Action dump** for hand-wiring: ``liberty-migrate actions --connector <app>`` writes a
  ``[migrated_actions.<app>]`` reference block — every v1 ``ly_actions`` workflow captured
  faithfully (branches, params, per-task binds, IF/LOOP shape). The operator reads this to
  understand a workflow before wiring its tasks via the builder. (Auto-attached actions ride
  on top of this dump — same data, same names — so what lands on a screen via ``ly_evt_cpt``
  is consistent with what the dump shows.)

**Phase 6 slice 4b (Action input dialog — ``ly_act_params`` → ``prompt_fields``) — DONE.**
v1's named actions could declare *input arguments* (``ly_act_params``) the operator fills in
before the workflow fires (NOMAJDE "Create Role" asks for AUUSER / JOBN / MUSE / PID / UPMJ
before chaining its F0092 + F00921 + F0093 inserts). v2's port: a sub-dialog ahead of the
action fire, values merged into the chain's resolution context.

- `liberty/screens/config.py` — new ``PromptField`` shape (mirrors ``ScreenField``: name / dd /
  label / format / required / disabled / hidden / colspan / default / lookup_param_binds + the
  three ``*_when`` conditional rules — but stands on its own, no backing column). A new
  ``_PromptableMixin`` adds ``prompt_fields`` / ``prompt_title`` / ``prompt_l`` / ``prompt_cols``
  / ``prompt_submit_label`` to the three ParamBind-bearing variants (``RunQueryAction`` /
  ``CallApiAction`` / ``NavigateAction``). The other four variants (notify / refresh / confirm
  / set_field) reject the mixin via ``extra='forbid'`` — keeps stub variants clean.
- `liberty/web/screens.py` — ``GET /api/screens/{app}/{id}`` resolves each prompt field's
  ``dd`` against the shared dictionary in the request language, attaching ``label`` / ``format``
  / ``rule`` (BOOLEAN / ENUM / LOOKUP) onto the wire payload. Same shape ``Column.rule`` ships
  — so the prompt sub-dialog renders the right widget (text / number / date / SearchSelect /
  Checkbox / password) without any extra plumbing. Walks every attachment point: screen-level
  ``actions`` / ``row_menu`` / ``on_insert`` / ``on_update`` / ``on_delete`` plus the dialog's
  ``on_load`` / ``on_save`` / ``on_cancel`` and each tab's ``actions``.
- Frontend (`frontend/src/pages/TableView/ActionPromptDialog.tsx`) — small modal opened *before*
  a promptable action fires. Reuses ``FieldRow`` via a synthesized ``Column`` so the existing
  widget switch covers everything; conditional rules (``visible_when`` / ``required_when`` /
  ``disabled_when``) evaluate against the prompt's *own* local state (a JDE param shown only
  when another JDE param is "AC" stays consistent inside the sub-dialog). Cancel resolves with
  ``null`` → chain aborts soft (no error banner).
- Chain runners (`ScreenDialog.tsx`'s ``runOnSaveActions`` + `ResultTable.tsx`'s ``runRowAction``
  + ``runScreenAction``) check each action for ``prompt_fields`` before resolving its binds;
  if non-empty, ``requestPrompt`` opens the sub-dialog and awaits the resolver. **The merged
  values feed the running ctx** so this action's ParamBinds *and* every later action in the
  same chain can refer to a prompt field via ``source: "<NAME>"``. The toolbar's ``navigate``
  action also threads prompt values through to the destination's URL query string (so a
  prompted-for ``USR_ID`` ends up on ``?USR_ID=<value>``).
- Migration (`liberty/migrations/v1.py::_params_to_prompt_fields`): each v1 ``ly_act_params``
  row → a v2 ``PromptField``. ``map_dir = 'OUT'`` skipped (SP returns, not inputs);
  ``map_display = 'N'`` → ``hidden = true``; ``map_default`` → ``default``;
  ``ly_act_params_filters`` → ``lookup_param_binds`` (a future LOOKUP-typed dd makes them
  cascade-narrow correctly). ``map_rules`` / ``map_rules_values`` (the v1 inline rule decls)
  are **not** auto-carried — v2 wires them through the shared dictionary via ``dd``, and
  auto-creating dictionary entries is out of slice scope. ``_action_chain`` attaches the prompt
  fields to the **first emitted task only**, so the prompt fires once per chain and the rest
  read from the merged ctx. ``_input_action_to_button`` attaches them to the migrated
  ``RunQueryAction`` for ``col_component='InputAction'`` rows.
- Real-data check: NOMAJDE's 5-input "Create Role" workflow migrates cleanly — operator clicks
  Add Row, the prompt dialog opens (with AUUSER prefilled to "ADMIN", JOBN/PID empty, MUSE
  hidden, UPMJ skipped), confirms, and the F0092 + F00921 + F0093 inserts fire in sequence
  with the prompt values bound into each.

**Phase 6 slice 4c (API tasks → ``call_api`` actions in the migration) — DONE.** v1 actions
that fire an API call (NOMAJDE's Reset Password on F0092, Update Password, … — all-API
workflows) used to degrade to a ``notify`` placeholder on the migrated screen. The migrator
now resolves them to real ``call_api`` actions:
- `liberty/migrations/v1.py::build_api_resolver(conns, apis, *, connector_prefix="")` — exposes
  the ``{v1 api_id: (v2 connector_name, v2 endpoint_name)}`` map ``migrate_api`` would
  otherwise compute internally. Same slugify + uniquify + ``legacy_api`` fallback logic, so the
  resolver picks the *same* names as the rendered ``connectors.toml``. Lives as a separate
  helper so ``migrate_api``'s rendered output stays TOML-safe (integer keys can't sit in a
  ``tomli_w``-serialised dict).
- ``migrate_actions(..., api_resolver=...)`` reads the map. A task with ``evt_type='API'`` and
  a resolved ``v1_api_id`` gets ``connector`` + ``endpoint`` set on the dumped task dict.
- ``_action_chain`` (the on_save / on_insert / on_delete event-driven attach path) and
  ``_input_action_to_button`` (the ``col_component='InputAction'`` button path) both look at the
  task's ``connector`` + ``endpoint`` and emit a v2 ``call_api`` action instead of the
  ``notify`` placeholder. ``param_binds`` carry over verbatim (so a JDE API call's INPUT-bound
  username / password / token wire through correctly).
- CLI: the ``screen`` and ``actions`` subcommands now run ``read_api`` + ``build_api_resolver``
  ahead of ``migrate_actions`` and thread the resolver through. A v1 DB with no API schema → an
  empty resolver, behaviour unchanged (the existing notify placeholder still emits for any
  unresolved API task).
- **Operators with an existing `screens.toml` should re-run `liberty-migrate screen` to pick
  up the change** — any pre-existing notify-only stubs survive in the file otherwise.

**Phase 6 slice 4d (Multi-step workflows — ChainAction / IfAction / LoopAction / ReturnAction)
— Slices A + B + C DONE.** v1 named-action workflows are richer than v2's flat ``list[Action]``
attachment points: a v1 button runs N tasks sharing a context (``allParams.current = {INPUT,
TASK_<id>: {RESULTS: …}}``), branches via IF, iterates via LOOP, returns values to the caller
via RETURN. F00926's "Import Security" is the motivating case (9 tasks with 3 IF guards →
delete/insert pairs for Security Workbench + UDO + Menu Filtering). v2 now expresses the
same shape inline on screens.

- **Pydantic** (`liberty/screens/config.py`): new step variants in the Action union —
  ``ChainAction`` (one outer button → ``steps`` list with shared context), ``IfAction``
  (``condition`` + ``then_steps`` / ``else_steps``), ``LoopAction`` (``source`` + nested
  ``steps``, iterating over an array path), ``ReturnAction`` (``bindings`` map writing back
  to the caller's form fields), plus ``Condition`` (``source`` + ``operator`` ∈ ``equals`` /
  ``not_equals`` / ``has_rows`` / ``no_rows`` / ``truthy`` / ``falsy`` / ``greater_than`` /
  ``less_than`` + optional ``value``). ``RunQueryAction`` / ``CallApiAction`` gain
  ``bind_result: bool = False`` — when true, the action's rows land in the chain context
  under the step's ``id`` as ``{rows, first_row, success}`` so later steps reference them via
  ``ParamBind {source: '<step_id>.first_row.<col>'}``. The Action union is rebuilt
  recursively so steps can nest arbitrarily.
- **Chain context** (semantics — Slice B wires the runtime): each chain accumulates ``{INPUT:
  {…prompt values…}, <step_id>: {rows, first_row, success}, loop: {…}}`` as it runs. v1's
  ``TASK_<id>.RESULTS[N].COL`` references become ``<slugified_step_id>.first_row.COL`` (or
  ``.rows.<N>.COL`` for non-zero indices). ``INPUT.<X>`` paths stay verbatim. Plain
  form-field references (no dots) keep their existing meaning — the runtime tries chain
  context first, then falls back to the firing context.
- **Migration** (`liberty/migrations/v1.py`):
  - Module-level ``_build_chain_action(action_dict, *, skip_query=None)`` — single source of
    truth for both ``_action_chain`` (event-driven attach) and ``_input_action_to_button``
    (per-tab button rows). Slugs the workflow id from ``act_label`` (e.g.
    ``import_security_from_user_role`` — no more ``migrated_3_0`` placeholder ids); each
    step's id is the slugified ``evt_label`` (e.g. ``delete_security_workbench``).
  - v1 IF tasks + branch groups (``evt_brc_id`` / ``evt_brc_true`` / ``evt_brc_false``)
    collapse to nested :class:`IfAction` with ``then_steps`` / ``else_steps`` (no more
    label-jump model; v2's nested shape reads naturally). LOOP tasks wrap into
    :class:`LoopAction`. RETURN tasks become :class:`ReturnAction`. QUERY → ``run_query``
    (with ``bind_result = True`` when the v2 query name suffix is ``_select`` / ``_get``).
  - v1 ``ly_condition`` + ``ly_cdn_params`` are read via the new
    ``read_conditions(engine)`` source helper; ``migrate_actions(..., condition_param_rows=…)``
    attaches the predicates to each IF task's dump as ``condition_predicates``;
    ``_build_chain_action`` picks the first clause and emits a real v2
    :class:`Condition` (``source = "INPUT.<cdn_dd_id>"``, ``operator`` mapped via
    ``_V1_TO_V2_COND_OP`` — ``EQUAL`` → ``equals``, ``NOT_EMPTY`` → ``truthy``, etc.).
    Multi-clause predicates pick the first + log a warning.
  - Source-path rewrite (``map_value``): ``TASK_<v1_evt_id>.RESULTS[0].COL`` becomes
    ``<step_id>.first_row.COL``; ``TASK_<v1_evt_id>.RESULTS[N].COL`` becomes
    ``<step_id>.rows.N.COL``. ``INPUT.<X>`` and form-field references stay verbatim.
  - **Single-step chains unwrap** — when ``_build_chain_action`` produces a chain with one
    resolvable step and no control flow, ``_action_chain`` / ``_input_action_to_button``
    return the lone step directly (no ``ChainAction`` wrapper); the emitted screens.toml
    stays close to what hand-written buttons look like. Multi-step or branching workflows
    wrap (so the chain context is per-fire-site).
- **CLI** (`liberty/migrate_cli.py`): both ``screen`` and ``actions`` subcommands now run
  ``read_conditions`` ahead of ``migrate_actions`` and thread the condition_param_rows
  through. Missing schema → empty rows, falls back to a placeholder condition + log warning.
- **Real-data check** — `liberty-migrate screen --connector nomajde` on libnjde produces
  F00926's Import Security as a single ``chain`` with 9 prompt fields and 3 ``IfAction``
  guards (each with a real ``operator = "equals"`` / ``value = "Y"`` condition from
  ``ly_cdn_params``). Each ``then_steps`` branch carries the delete + insert pair with
  readable step ids (``delete_security_workbench`` / ``import_security_workbench`` / …).
- **Operators with an existing `screens.toml` should re-run `liberty-migrate screen`** to
  pick up the readable ids + IF / LOOP migration. Pre-existing flat chains keep loading
  (Pydantic accepts both shapes); they just don't get the upgrade until they're re-migrated.

**Slice B — frontend runtime** lives in ``frontend/src/pages/TableView/actionRunner.ts``:
- A ``ChainCtx`` accumulates ``{INPUT, <step_id>: {rows, first_row, success}, loop}`` as the
  chain walks. Each ``run_query`` / ``call_api`` with ``bind_result`` lands its rows under the
  step's ``id`` so a later step's ``ParamBind {source: '<id>.first_row.<col>'}`` reads them.
- ``resolveSource(path, ctx, formCtx)`` — dotted-path resolver. Plain (no dots) falls through
  to the firing context (form / row) so hand-written screens keep working. Dotted paths walk
  the chain context with case-insensitive object-key matching and numeric segments indexing
  arrays. Reserved ``#``-prefixed paths are dropped (auth built-ins land in a future slice).
- ``evalCondition`` — handles ``equals`` / ``not_equals`` / ``truthy`` / ``falsy`` /
  ``has_rows`` / ``no_rows`` / ``greater_than`` / ``less_than``. ``truthy`` uses v1's
  Y/N/0/false/null falsiness (so a migrated ``cdn_operator = NOT_EMPTY`` on a JDE Y/N flag
  works without manual fixing). The actual values come from ``resolveSource`` so any chain
  path is comparable.
- ``runChain(actions, initialInput, formCtx, deps)`` — recursive walker. ``ChainAction``
  recurses into ``steps``; ``IfAction`` evaluates its condition and walks ``then_steps`` /
  ``else_steps``; ``LoopAction`` resolves its source to an array, iterates with ``ctx.loop``
  set to each element, walks nested ``steps``; ``ReturnAction`` / ``SetFieldAction`` write
  values into a ``returnedValues`` dict the caller merges back into its form state (v1's
  ``RETURN`` semantics). ``ConfirmAction`` calls into an optional ``deps.confirm`` provider —
  no provider = a warning + carry on (don't silently auto-confirm something irreversible).
- ``ActionRunnerDeps`` — pluggable: ``defaultConnector`` (the screen's effective connector
  for ``run_query`` with no explicit connector), ``requestPrompt`` (the imperative-from-async
  prompt dialog plumbing ScreenDialog already had), optional ``confirm``. Keeps the runner
  decoupled from React + makes mocking trivial.
- **Wired into every action firing site:**
  - ``frontend/src/pages/TableView/ScreenDialog.tsx``'s ``runOnSaveActions`` — covers
    ``dialog.on_load`` / ``on_save`` / ``on_cancel`` / ``screen.on_insert`` / ``on_update`` /
    ``on_delete`` / per-tab button clicks. ``returnedValues`` (from :class:`ReturnAction` /
    :class:`SetFieldAction`) merge back into ``formValues`` (case-insensitive field name
    match — Postgres lowercases columns; v1 migration emits upper).
  - ``frontend/src/pages/TableView/ResultTable.tsx``'s ``runRowAction`` (right-click row
    menu, slice 6) — passes the clicked row as ``formCtx`` so plain ``source: 'USR_ID'``
    references keep reading from the row while ``INPUT.<X>`` / step-id-keyed dotted paths
    resolve against the chain context. ``deps.navigate`` wires the v1 "drill into another
    table" pattern (a ``navigate`` step opens ``/sql/<connector>/<to>?<qs>``).
  - ``ResultTable``'s ``runScreenAction`` (toolbar buttons) — same as row-menu but with an
    empty ``formCtx`` (no row context); operator-input flows in through prompt_fields → INPUT.
  - ``ResultTable``'s inline ``fireChain`` (batch-save ``on_insert`` / ``on_update`` /
    ``on_delete`` hooks per affected row) — single ``runChain`` call per row.
  Hand-written flat chains (no ChainAction wrapper) still work — each top-level action sees
  the same accumulating chain context, so a flat ``on_save`` chain that pins a value via
  ``ReturnAction`` will write back to the form just like a wrapped chain.
- **API response shapes** — ``run_query`` reads ``{rows, columns, …}`` from
  ``QueryResult.to_dict``; ``call_api`` reads ``{success, status_code, data, error}`` from
  ``ApiResult.to_dict``. A ``call_api`` with ``success: false`` is treated as a soft error
  (the route returns 200 even on upstream failure — see :class:`liberty.connectors.api`).
- Smoke-tested via ``resolveSource`` / ``resolveBinds`` / ``evalCondition`` with a synthetic
  F00926-shaped context (INPUT, captured ``select_workbench.rows``, an active ``loop``
  element, missing paths). All cases — including v1's Y/N truthiness convention,
  case-insensitive segment matching, numeric array indexing — pass.

**Slice C — builder UX** lives in ``frontend/src/pages/Settings/ActionListEditor.tsx``:
- ``ACTION_TYPES`` extended with the four workflow-control variants
  (``chain`` / ``if`` / ``loop`` / ``return``); the action-type SearchSelect inside an
  expanded action body now offers all 11 variants.
- ``blankActionOfType`` seeds each new variant with its required shape — ``chain`` starts
  with ``steps: []``; ``if`` with a placeholder ``condition`` + empty then/else_steps;
  ``loop`` with ``source: ''`` + empty ``steps``; ``return`` with ``bindings: {}``. So
  switching an action's type doesn't drop the operator into a wall of validation red.
- ``ACTION_OVERRIDE_KEYS`` strips ``steps`` / ``then_steps`` / ``else_steps`` / ``condition``
  from the variant schema so SchemaForm doesn't render them as cramped object-list
  accordions; dedicated UI takes over.
- ``chain`` joins ``run_query`` / ``call_api`` / ``navigate`` as a *promptable* variant — a
  chain-fired workflow can open a single pre-fire prompt for the operator's inputs (lands
  under ``INPUT.<name>`` in the chain context). The existing prompt-fields editor handles it.
- New ``renderWorkflowExtras(action, onPatch)`` emits the right inline editor per variant:
  * **chain** → recursive ``<ActionListEditor>`` for the ``steps`` list (same component
    rendering itself; nested steps get the full editing experience — type switcher,
    prompt_fields, connector / query pickers, condition editors, …).
  * **if** → a ``<SchemaForm>`` over the ``Condition`` ``$def`` (the operator literal renders
    as a SearchSelect via the enum handling; ``source`` and ``value`` are text inputs) +
    two recursive ``<ActionListEditor>`` instances for ``then_steps`` and ``else_steps``.
  * **loop** → a hint about the dotted-path source syntax + recursive
    ``<ActionListEditor>`` for the loop body's ``steps``.
  * **return** → the ``bindings: dict[str, str]`` map renders through SchemaForm's existing
    ``StringMapEditor`` (no extra UI needed). ``bind_result: bool`` on ``run_query`` /
    ``call_api`` likewise — SchemaForm's checkbox handler covers it.
- Existing F00926-style migrated chains open in the builder with every level editable: a
  ChainAction at the top shows its 3 IfAction guards as expandable rows; each IF shows the
  Condition editor + the then-branch's delete + insert run_query steps; each run_query
  step's param_binds (with dotted-path sources like ``INPUT.LYF00950``) edit as ParamBind
  rows. The whole tree is a recursive ActionListEditor at every depth.
- i18n strings added under ``settings.screens.{chain,condition,if,loop}.*`` (EN + FR).

Pending follow-ups for this slice:
- **Slice D (later)** — shared / cross-screen actions in a new ``config/actions.toml`` +
  ``action_ref`` outer variant. For now actions live inline per screen, which is fine for
  the workflow library size of nomasx1 + nomajde.
- Some UX polish that's not blocking real edits but would help:
  * Pre-fill the LoopAction ``source`` SearchSelect from the preceding steps' ids that have
    ``bind_result = true`` (today it's free-text — the hint tells you what to type).
  * Show a step-id picker for ``ReturnAction.bindings`` and ``ParamBind.source`` values
    instead of free-text (today both autocompletion is by hand).

**Phase 6 slice 5 (AUD audit trail) — DONE.**
- `liberty/connectors/config.py` — new ``QueryDef.audit: str | None``. When set on a writable
  query it names the audit table the SQL connector mirrors writes into. Lives in the
  ``Advanced`` group of the builder (the per-write tabs of the Connectors → Tables editor now
  surface it alongside ``writable``, ``sql``, ``params``).
- `liberty/connectors/sql.py::SQLConnector.execute()` — new ``user: str | None`` kwarg + a
  ``_write_audit`` helper. After a writable execute succeeds, if ``qdef.audit`` is set, the
  connector runs a generic ``INSERT INTO <audit_table> (col1, col2, …, AUD_ACTION, AUD_USER,
  AUD_DATE) VALUES (:col1, :col2, …, :_aud_action, :_aud_user, :_aud_date)`` **in the same
  transaction** — a failing audit rolls the main write back, loud rather than silent. Columns
  are taken from the bound params (uppercase keys, ``_ORIGINAL`` suffixes skipped — those are
  WHERE rebinds for ``_put``, not row data). ``AUD_ACTION`` is the statement type
  (``INSERT``/``UPDATE``/``DELETE``); ``AUD_USER`` is the caller's username (or
  ``"anonymous"`` when unauthenticated); ``AUD_DATE`` is the server's UTC timestamp.
- `liberty/web/connectors.py` — ``_run_sql`` now threads ``principal.username`` into
  ``execute(user=…)`` from both ``GET /api/sql`` and ``POST /api/sql``. The username comes
  from the JWT — never from the request body.
- `liberty/migrations/v1.py::migrate_table_meta` — emits ``audit_table = "AUD_<TBL_DB_NAME>"``
  when ``tbl_audit = 'Y'`` and ``tbl_db_name`` is set. ``migrate_sql_queries`` picks it up via
  the existing ``table_meta`` plumb and attaches ``audit = …`` to each **writable** companion
  of the audited screen (`_put` / `_post` / `_delete`); read companions never get an audit field.
  ``read_table_meta`` now reads ``tbl_audit`` + ``tbl_db_name`` so this lands without extra
  CLI flags.
- Frontend: the per-write tab of the Connectors → Tables editor now picks ``audit`` from
  ``WRITE_BODY_KEYS``, so operators see + edit it directly.
- Real-data smoke: ``liberty-migrate all`` on libnsx1 emits 13 ``audit`` fields across the
  6 audited screens (each writable companion of LICENSE_CSI / SOD_PROCESS / SOD_ACTIVITIES /
  …). The matching ``AUD_<TABLE>`` tables already exist in the v1 DB — v2 just keeps writing
  to them.

**Phase 6 slice 6 (Row context menus) — DONE.**
- `frontend/src/common/DataTable.tsx` — new ``onRowContextMenu`` callback. Right-click on a
  non-grouped row fires it (group rows skip); ``preventDefault()`` is called for you so the
  native browser menu doesn't fight the consumer's overlay. Headers and pagination keep their
  native menu.
- `frontend/src/pages/TableView/ResultTable.tsx` — when ``screen.row_menu`` is non-empty *and*
  the user isn't in batch-edit mode, right-click opens a floating ``RowMenuBox`` at the click
  coords with one item per action. ``runRowAction`` runs the picked action's task against the
  clicked row's values (the shared ``resolveRowBinds`` helper case-folds ``source`` lookups —
  Postgres lowercases identifiers, ParamBinds usually carry v1's uppercase column names).
  ``run_query`` POSTs to ``/api/sql/{c}/{q}`` with bound + uppercased params (falls back to the
  screen's effective connector); ``notify`` is logged; ``refresh`` is implied by ``onSaved()``.
  Stubbed variants (``call_api`` / ``navigate`` / ``set_field`` / ``confirm``) warn and abort
  unless ``stop_on_error = false`` — same convention as the dialog ``on_save`` runner.
  Click-outside / Escape close the menu; click *inside* doesn't bubble out.
- `frontend/src/pages/Settings/ScreenEditor.tsx` — the **Row menu** tab is now a real editor
  (was a "coming soon" placeholder). The slice refactored the dialog ``on_save`` editor into a
  shared ``renderActionList`` helper so both attachment points use the same UX: list of
  expandable action rows, type picker (one of 7), per-action SchemaForm over the matching
  ``$def``, ``param_binds`` rendered inline. Each editor keeps its own expansion state.
- `frontend/src/types/screens.ts` — ``ScreenDetail`` now carries ``actions`` and ``row_menu``
  (both ``Action[]``), so the runtime can read them off the catalog payload directly.
- v1 ``ly_actions`` *now* auto-attach via the ``ly_evt_cpt`` event junction *and* via
  ``ly_dlg_col col_component='InputAction'`` (see slice 4 above) — the previous "wired in v1
  frontend code, not migratable" assumption was wrong. v1's **row context menus** are the
  *third* attachment route: ``ly_tables.tbl_ctx_id`` → ``ly_ctxmenus`` → ``ly_ctx_val`` (items)
  → ``ly_ctx_filters`` (per-item ParamBinds). Slice 6b (see below) migrates those automatically.

**Phase 6 slice 6b (Row context menus — v1 migration) — DONE.**
- `liberty/migrations/source.py` — new reader ``read_context_menus(engine)`` returns
  ``(ly_ctxmenus, ly_ctx_val, ly_ctx_filters)`` rows. ``_SCREENS_TABLES`` now also reads
  ``tbl_ctx_id`` so :func:`migrate_screens` can attach the resolved menus by ``tbl_id``.
- `liberty/migrations/v1.py::migrate_context_menus` — collapses each ``ly_ctxmenus`` row into a
  list of ``NavigateAction`` dicts, then maps it onto every referencing ``ly_tables`` row. Each
  item's ``val_component`` decides how the target query resolves: ``FormsTable`` →
  ``val_component_id`` is a ``ly_tables.tbl_id`` (→ ``tbl_query_id``); ``FormsDialog`` →
  ``val_component_id`` is a ``ly_dlg_frm.frm_id`` (→ ``frm_query_id``). The migrated v2 name
  matches what :func:`migrate_sql_queries` emits (raw v1 ``query_crud`` verbatim). ``connector``
  is spelled out only when the target's pool differs from the app's (cross-pool drills like
  NOMAJDE → jdedwards). ``ly_ctx_filters`` shape is identical to ``ly_dlg_filters``: ``flt_type=
  'DD'`` → ``{param, source}``, ``flt_type='VALUE'`` → ``{param, value}``. v1 context menus are
  *shared* (one ``ctx_id`` can be referenced by several tables) — v2's ``Screen.row_menu`` is
  inline per-screen, so the resolved list is *copied* into each referencing screen. A future
  ``[contextual_menus.<id>]`` shared pool is an option if redundancy becomes painful.
- `liberty/migrations/v1.py::migrate_screens` — accepts a ``row_menus: Mapping[int, list[dict]]``
  arg keyed by ``tbl_id``; inlines the matching items onto each screen's ``row_menu``.
- `liberty/migrate_cli.py screen` subcommand — pulls + threads context menus through; the
  ``# migrated:`` summary line gets ``N with row-menu (M items)`` at the end.
- Real-data check: ``liberty-migrate screen`` on libnsx1 produces **15 screens with row-menu,
  39 items total** (Security - Users / Roles / Matrix / SOD Summary / License - JD Edwards /
  Oracle / etc.). libnjde has no context menus in v1; nothing emitted. Each migrated action
  round-trips through the Pydantic ``NavigateAction`` shape; the runtime built in slice 6 picks
  them up directly (right-click any row → menu of "Display Roles" / "Display Rights" / …).

**Phase 6 slice 7 (Oracle compatibility — read trim + write null coalesce) — DONE.**
v1's NOMAJDE app reads + writes JDE's Oracle DB extensively; Oracle's quirks bit two real
places. v2 wires them as **pool-level flags** that auto-enable on Oracle and stay off
elsewhere, so a deployment doesn't have to know:

- ``PoolConfig.trim_strings: bool | None`` — strip trailing whitespace from string cells on
  SELECT. Auto-on when the pool's dialect is ``oracle``. Reason: Oracle CHAR / NCHAR columns
  are space-padded to the column width; the v1 frontend used to ``rstrip()`` every cell. With
  this on, ``SQLConnector.execute()`` does the same right after fetching rows — only ``str``
  cells, only trailing whitespace, leaves everything else alone. (v1 parity: same behaviour
  for the same dialect, no opt-in needed.)
- ``PoolConfig.coalesce_nulls: bool | None`` — replace ``None`` bind values with type-
  appropriate sentinels on INSERT / UPDATE / MERGE. Auto-on for Oracle. Reason: Oracle's
  ``''`` ≡ ``NULL`` on VARCHAR2 but *not* on CHAR/NCHAR (a CHAR(N) NOT NULL with an empty
  string fails); v1's NCHAR-heavy JDE tables needed ``''`` for char columns and ``0`` for
  numeric columns. ``SQLConnector`` introspects the target table once via ``ALL_TAB_COLUMNS``
  (cached by ``(pool, owner, table)`` — invalidated on hot-reload via ``aclose``'s
  ``reset_oracle_column_cache()``), then ``_coalesce_oracle_nulls`` swaps each ``None`` bind
  to ``''`` (CHAR / NCHAR / VARCHAR / VARCHAR2 / NVARCHAR2 / CLOB / NCLOB / LONG) or ``0``
  (NUMBER / FLOAT / BINARY_FLOAT / BINARY_DOUBLE / INTEGER / INT). The regex
  ``_ORACLE_TARGET_RE`` extracts ``owner.table`` from the INSERT / UPDATE / MERGE INTO /
  DELETE FROM clause.
- `liberty/connectors/db.py::PoolRegistry` — ``trim_strings(name)`` and ``coalesce_nulls(name)``
  resolve the effective flag per pool (explicit setting wins; else auto-on for Oracle; else
  off). Builder exposes both as boolean toggles in the Pool editor's General tab.
- Real-data check: NOMAJDE's role/user screens against JDE (Oracle) no longer return cells
  with trailing spaces (v1 parity restored); writes to F0092 / F00921 / F0093 succeed with
  the NCHAR-quirk handled. Operators who explicitly want to *disable* the auto-trim (e.g. a
  table where trailing whitespace is data) can set ``trim_strings = false`` on the pool.

Phase 6 (Form/screen engine) is now feature-complete for the slices outlined in `docs/PLAN.md`.

**Phase 6 follow-up (Connector ↔ Screen ↔ ScreenField roles consolidation) — Phase 1 of 3 DONE,
Phase 2 of 3 DONE.** The original design split display metadata across three places (the
connector's `QueryDef.columns`, the screen, and each `ScreenField`), with the dialog form and
the grid editor each reading from a different layer. Phased refactor (committable per phase) to
make **Screen the single source of truth for display metadata** while Connector keeps only
queries:

* **Phase 1 (done):** Added `Screen.columns: list[ColumnHint]` as an additive mirror of
  `QueryDef.columns`. The migration emits both; the screens API ships `Screen.columns` resolved
  against the dictionary (label/format/rule/hidden/filter/filter_from/visible_when/align/width/dd
  — same shape `Column.to_dict()` emits); the TableView merges these over the SQL result's
  discovered columns (case-insensitive name match, keeps server-discovered type). When the screen
  has no `columns` list, falls back to the query's columns (back-compat).

* **Phase 2 (done):** `ScreenField` shrinks to **layout-only** — keeps `name` (reference) +
  `hidden` / `disabled` / `required` / `colspan` + the three conditional rule lists. The per-field
  metadata (`dd` / `label` / `format` / `rules` / `rules_values` / `default` / `lookup_param_binds`)
  moves onto `ColumnHint` so it lives **once** on `Screen.columns` and drives both the grid
  editor and the dialog form. The unified `ParamBind` moves to `liberty/connectors/config.py`
  (no upstream deps) and is re-exported from `liberty/screens.config`. The migration emits
  display metadata onto `Screen.columns`, including a post-pass that propagates nested-form
  field metadata onto each nested target screen's columns. The backend resolver
  (`_resolve_screen_field`) merges the matching `ColumnHint` onto each dialog field's wire
  payload so the frontend's `FieldRow` keeps reading `field.label` / `field.rule` /
  `field.lookup_param_binds` / `field.default` transparently — the wire shape is unchanged,
  the metadata moved one level up. `ScreenField` has `extra="ignore"` (not `"forbid"`) so an
  old screens.toml keeps loading — operators re-migrate at their own pace via
  `liberty-migrate screen`. The Visual Designer's field cards now read `dd` / `label` / format
  preview / lookup-bind count from the matching column hint instead of the field.

* **Phase 3 (done):** Stripped `QueryDef` of `columns` / `auto_load` / `audit` / `max_rows`
  / `key_columns` / `update_query` / `insert_query` / `delete_query`. **Final state:**
  Connector = queries only (`sql`, `params`, `writable`, `description?`, `label?`); Screen =
  source of truth for everything else (`columns`, `auto_load`, `audit_table`, `max_rows`,
  `key_columns`, `update_query`/`insert_query`/`delete_query`, `dialog`, `actions`,
  `row_menu`, lifecycle hooks). `QueryDef` uses `extra="ignore"` so a `connectors.toml`
  written before Phase 3 keeps loading (legacy keys silently dropped); `Screen` does the
  same for `audit: bool` (replaced by `audit_table: str | None`). Operators re-migrate via
  `liberty-migrate sql` + `liberty-migrate screen` to repopulate the matching `Screen`
  fields.

  **Runtime plumbing** — the SQL connector's `execute()` gained three new kwargs:
  `column_hints` (the screen's `Screen.columns` list — drives the filter wrap, result-column
  display hints, and write-side rule coercion / SEQUENCE resolution), `audit_table` (the
  audit-mirror target, was `qdef.audit` pre-Phase-3), and `screen_max_rows` (the per-screen
  SELECT cap). The web route layer (`liberty/web/connectors.py`) looks up the matching
  `Screen` for `(connector, query)` — checking `read_query` / `update_query` / `insert_query`
  / `delete_query` — and threads its `columns` / `audit_table` / `max_rows` into the
  `execute()` call. A query with no matching screen runs unadorned (no audit, no
  filter wrap, no per-screen hints) — back-compat for connector-only deployments. The
  connector's `describe()` output drops `columns` / `auto_load` / `audit` / `key_columns` /
  `update_query` / `insert_query` / `delete_query`; the frontend reads them from
  `GET /api/screens/{app}/{id}` instead. The TableView now reads `update_query` /
  `insert_query` / `delete_query` / `key_columns` / `auto_load` from `screen.*` first,
  falling back to the (now mostly-empty) `meta.*`.

457 tests pass.

**Phase 6 follow-up (Workbook export — v1's ``tbl_workbook`` / ``tbl_sheet``) — DONE.**
v1 lets a screen export to xlsx with two layout dimensions: **split** the result by a column
into one xlsx-per-distinct-value, and within each xlsx **fan out** to several **sheets**
each pulling from its own query (filtered by the split value). NOMASX1's reference case is
``ldap_apps_get`` — one .xlsx per department group, each carrying a sheet per app + a sheet
of all LDAP users in that group. v2's port lives on the ``Screen``:

- `liberty/screens/config.py` — new shapes alongside ``ScreenDialog``:
  - ``SheetSpec`` (``{name, connector?, query, split_by?, param_binds[]}``) — one sheet inside
    a workbook. ``name`` supports ``{{split_value}}`` (the workbook's group key) and
    ``{{sheet_value}}`` (the sheet partition value, only when ``split_by`` is set)
    interpolation. ``connector`` is optional (defaults to the screen's effective connector).
    ``param_binds`` is the same :class:`ParamBind` shape used for lookups / actions / row
    menus; ``source = "split_value"`` resolves to the current workbook's split value (the
    rest of the sources read from the running export ctx). ``split_by`` (optional) names a
    *result column* of the sheet's own query — when set, the query's rows are partitioned
    in-memory by that column into one worksheet per distinct value (single DB roundtrip,
    first-seen order). v2's port of v1's ``tbl_sheet`` — together with the workbook-level
    ``split_by`` this covers the full v1 ``ldap_apps_get`` layout (file per department
    group + sheet per app within each file + an optional flat "all users" sheet alongside).
  - ``WorkbookExport`` (``{split_by?, sheets[], file_name_template?, archive_name?}``):
    ``split_by`` names a *result column* from the screen's ``read_query`` whose distinct
    values drive the burst (omit → one workbook). ``sheets`` is a non-empty list of
    ``SheetSpec``. ``file_name_template`` defaults to ``{{screen}}_{{split_value}}.xlsx``
    when ``split_by`` is set, ``{{screen}}.xlsx`` otherwise; ``archive_name`` defaults to
    ``{{screen}}.zip`` for the multi-file case.
  - ``Screen.export: WorkbookExport | None`` — the screen's optional export config.
- `liberty/web/export.py` — ``POST /api/screens/{app}/{id}/export``. Builds the workbook(s)
  in-memory with **openpyxl** (already streaming-friendly), one ``Workbook`` per split value,
  one ``Worksheet`` per ``SheetSpec``. Permission gate: the caller must hold
  ``sql:{conn}:{read_query}`` (to discover split values) **and** ``sql:{conn}:{sheet.query}``
  for every sheet — missing any → 404 (existence not leaked, same convention as the
  ``GET /api/screens`` route). Helpers: ``_safe_sheet_name`` (Excel's 31-char / no
  ``:\\/?*[]`` rule + ``_2``/``_3`` dedupe), ``_split_values`` (preserves first-seen order,
  case-insensitive column match against the discovered result), ``_cell_value`` (strips
  openpyxl's ``ILLEGAL_CHARACTERS_RE`` from strings). Output: a single ``.xlsx`` when there's
  one workbook (Content-Type ``…spreadsheetml.sheet``), a ``.zip`` of ``.xlsx`` otherwise
  (Content-Type ``application/zip``). Single-workbook mode also works fine when
  ``split_by`` is set but the read query returns just one value.
- `frontend/src/pages/TableView/ResultTable.tsx` — toolbar **Export** button (next to Import)
  appears when ``screen.export`` is set; fires the endpoint, downloads the blob via a
  hidden anchor + ``URL.createObjectURL``. Per-button busy state so a slow export doesn't
  double-fire on impatient clicks.
- `frontend/src/pages/Settings/ScreenEditor.tsx` — new **Export** tab in the Visual Designer
  / Screen editor. Empty state shows a "Create export" button; configured state renders
  ``WorkbookExport`` via SchemaForm (``$def``-driven — sheet picker, split_by free-text,
  filename templates, per-sheet ParamBind editor) with a Delete-config button. The
  ``WorkbookExport`` / ``SheetSpec`` shapes ride along on ``GET /admin/config/schema`` like
  the other ``$def``\\ s, so the editor renders for free.
- `frontend/src/types/screens.ts` — ``SheetSpec`` / ``WorkbookExport`` / ``ScreenDetail.export``.
- `pyproject.toml` — adds ``openpyxl>=3.1`` (workbook export).
- `tests/test_web_export.py` — eight end-to-end tests using a SQLite fixture: single-workbook
  mode produces a raw .xlsx with the configured sheet, multi-workbook mode bursts into a
  .zip with one .xlsx per group (each carrying both sheets filtered by ``:GROUP`` via
  ``ParamBind`` ``source = "split_value"``), the v1 ``ldap_apps_get`` layout (file split +
  sheet split + flat "all users" sheet alongside the fan-out) round-trips with correct
  worksheet names from ``{{sheet_value}}``, single-xlsx + sheet-level split produces several
  worksheets in one file, auth gate 404s a user lacking perm for any sheet's query, unknown
  screen returns 404. 523 backend tests pass.

**Phase 7 (Config builders) — mostly DONE.** The Settings page (`liberty/web/admin.py` + the
`/admin/config/<section>/parsed` endpoints + `frontend/src/pages/Settings/*Builder.tsx`) is the
operator-facing way to edit every config section without touching TOML. Schema-driven through
`/admin/config/schema` (each builder pulls the matching `model_json_schema()` and renders via
`SchemaForm` / `SchemaNavigator`). Shipped this round:

* **Themed `useModals` primitive** (`frontend/src/common/Modals.tsx`) — `confirm` / `prompt` /
  `alert` / `choose` (multi-button for the unsaved-changes pattern). All four use a `TopOverlay`
  (z-index 2000) + `createPortal` to `document.body` so they paint above every other modal
  (Screen Designer at 400, SearchSelect dropdowns at 1000). Replaced 40+ browser-native dialogs
  across the builders. Mounted as `<ModalsProvider>` near the top of the tree in `main.tsx`.
* **Consolidated top toolbar** in every builder (Pools / Connectors / Dictionary / Menus /
  Screens / Dashboards) — config path + dirty/status on the left, all actions (Add / Delete /
  Save / Reload) on the right; the body flex-fills + only the inner list/form panels scroll.
  No page-level scroll, the toolbar stays pinned.
* **`ConnectorsBuilder` Tables / Sequences / Lookups switcher** — replaces the old Tables / Form
  toggle. **Tables** = CRUD-grouped (the `ConnectorsTableEditor` view, unchanged); **Sequences**
  = flat list of queries referenced by `[sequences.*]` in dictionary.toml; **Lookups** = same
  for `[lookups.*]`. Each opens a single-query SchemaForm editor with a Back button on the
  left. API connectors get a **dedicated 5-tab editor** (`ApiConnectorEditor.tsx`):
  *Connection* (base URL / timeout / SSL verify / default headers) · *Authentication* (method
  picker + only the fields that method uses — OAuth2's token endpoint section appears only
  when method = OAuth2, etc.) · *Endpoints* (collapsible list, per-endpoint Name/Label/
  Method/URL/headers/body with Format-JSON button/query params/response field/description/
  response mappings/parameters table) · *Webhooks* (placeholder for the Phase 9 inbound-
  webhook slice) · *Test* (pick an endpoint, the declared parameters surface as inputs with
  defaults, Run fires `POST /admin/config/api/test` against the in-progress config without
  saving and shows status/URL/body/extracted/mapped as a JSON result panel). Headers display
  as nomaubl's `Key:Value;Key:Value` semicolon-separated form; query params as
  `key=val&key=val`. SchemaNavigator was the previous render path; it surfaced 20+ flat
  fields including OAuth2 ones that didn't apply to a basic connector and vice versa. A small
  **Settings…** modal on each SQL connector exposes the rare connector-wide fields (`type` /
  `pool` / `licensed` / `max_rows`) — no longer a permanent pane.
* **Scaffold modals** — `ScaffoldQueryModal` (sequence + lookup; `+ Add sequence` /
  `+ Add lookup` in the Sequences/Lookups views) and `CrudWizardModal` (`+ Add table → Generate
  from DB`). Both read the connector's pool via the lazy two-step introspection (`GET /api/sql/
  <c>/_schemas` returns just schema names; `GET /api/sql/<c>/_schema?schema=<sch>&name_like=…`
  returns one schema's tables with an optional `F009%` LIKE filter applied *before* the per-
  table column walk — the wall-clock-expensive step on Oracle). Sequence scaffold emits
  `SELECT COALESCE(MAX(col), 0) + 1` + a `[sequences.<id>]` entry with `dd_id` set; lookup
  scaffold emits `SELECT value, label FROM table ORDER BY label` + a `[lookups.<id>]` entry
  with `value` / `label` set; CRUD wizard emits all four `_get` / `_put` / `_post` / `_delete`
  queries with `:NAME_ORIGINAL` rebinds on `_put`'s WHERE. The wizard's column grid is laid out
  side-by-side (340px min width) with an `Include` checkbox + a `key` checkbox per column;
  per-CRUD opt-out chips; SQL previews in `SqlEditor` cards (autocomplete on via
  `SqlConnectorContext`) — operator can edit any preview to override the generated SQL.
* **Shared `EditQueryModal`** — pencil button next to every query SearchSelect (Screen Editor's
  Queries tab + Action overrides; NestedFormTab pickers in the visual builder). Opens the
  picked query's QueryDef editor in place; Save PUTs the connectors back + reloads. Cancel with
  unsaved edits triggers the Discard / Keep-editing prompt via `modals.choose`.
* **Per-column `ColumnHint.key`** — row-identifying columns now ticked per column in the Visual
  Designer's Columns tab (replaces the old flat `Screen.key_columns` field in the General tab).
  `Screen.effective_key_columns()` derives the runtime list. The `/admin/config/screens/parsed`
  GET payload folds any legacy `screen.key_columns` list onto the matching column hints as
  `key: true` and drops the redundant list, so pre-existing screens.toml files light up the new
  "key" badge without a re-migration.
* **`AppMenu.home`** — per-app home page pointer (a menu item id). `GET /api/menus/{app}`
  resolves it to `/dashboard/<id>` / `/sql/<c>/<t>` / `/http/<c>/<t>` on the wire as
  `home_path`, emitted only when the caller can read the target. The frontend's
  `WorkspaceContext.setCurrentApp` navigates there on an explicit picker change; a one-shot
  mount effect also redirects from `/` on cold load. Settings → Menus has a `Home page`
  SearchSelect on each app's header (over a new `MENU_HOME_ITEMS` augmented framework enum).
* **Screen Designer unsaved-changes guard** — Cancel reverts every edit made inside the modal
  session to a snapshot taken on open; the three-way prompt fires when there are pending edits
  (Discard / Save / Keep editing). Delete-dialog button on the Dialog tab clears the entire
  dialog after a confirm. Native `<select>` for the action type picker → themed `SearchSelect`.
* **Bulk-edit + Oracle fixes** — text-input focus preserved across edits (was being lost by an
  over-eager `dataCols` rebuild on `editTick`); Oracle empty-bind handling replaces `None` *and*
  `""` with a single space for CHAR / NCHAR / VARCHAR2 (was just `""`, which Oracle still
  treats as NULL); `trim_strings` / `coalesce_nulls` are explicit `bool` (no more "auto-on
  for Oracle" guess — operator opts in per pool).
* **General tab loop fix** — `setProp` was called in a loop over `GENERAL_FORM_KEYS`; each call
  rebased `{...value}` from the closure's stale `value`, so only the last key in the iteration
  order survived. Replaced with a single `onChange(next)` that builds the full patched value.
* **Reset columns** — new `Reset` link in the `DataTable` Columns menu header — clears the
  `dt-<tableId>` localStorage entry + re-applies `initialColumnVisibility`. Fixes the
  `visible_when` ↔ stale-localStorage conflict (a column the rule wants shown stayed hidden
  because of a prior session's tick).
* **Raw TOML editor removed** — every section has a structured builder + a validator now; the
  raw escape hatch was a foot-gun.

Shipped this slice (continued) — **per-tab `FormTab.actions` editor** in the Visual Designer:
* `renderActionList` + `renderPromptFields` + `renderActionOverrides` + `actionVariantSchema`
  (and the action / PromptField shape constants) extracted from `ScreenEditor.tsx` into a new
  shared `frontend/src/pages/Settings/ActionListEditor.tsx`. Same UX as before in the screen-
  level attachment points (dialog `on_load` / `on_save` / `on_cancel`, screen `actions` /
  `row_menu` / `on_insert` / `on_update` / `on_delete`); the Visual Designer's Tab Settings
  panel now hosts a matching instance bound to the selected tab's `actions` array.
* Operators land on F0092 / F00926 in NOMAJDE (which carry `dialog.tabs.actions` migrated from
  v1's `col_component='InputAction'` rows — Import Security / Merge Roles / row-bound workflow
  buttons), open a tab, and edit those buttons in place. Tab Settings auto-opens when actions
  exist on the selected tab; a `Zap N` chip on its summary line surfaces the count even when
  collapsed. The read-only `TabActionsRow` badges at the bottom of the canvas remain — they
  serve as a live preview of which buttons land in the dialog footer at runtime.
* Same shared editor handles ParamBind-bearing variants (`run_query` / `call_api` /
  `navigate`) → it surfaces the connector + target dropdowns, the PromptField list below the
  variant SchemaForm, and the pencil-button into `EditQueryModal` from inside the visual
  designer's tab context (the modal itself stays in `ScreenVisualBuilder`).

Shipped earlier this slice — **`prompt_fields` editor per action** (slice-4b's `ly_act_params` editing
wired into `ScreenEditor.renderActionList`):
* `actionVariantSchema()` strips `prompt_fields` from the variant schema so SchemaForm doesn't
  inline-accordion it inside its auto-generated "Prompt" tab. The other four prompt_* fields
  (`prompt_title` / `prompt_l` / `prompt_cols` / `prompt_submit_label`) stay on that tab — they
  are simple strings/numbers and SchemaForm's auto-rendering is fine for them.
* A dedicated `renderPromptFields(parentKey, idx, action, onPatch)` helper renders, below the
  variant SchemaForm, a collapsible per-field list (same `FieldList` / `FieldHeader` /
  `FieldBody` styled components used for the surrounding action list). Each row's collapsed
  header shows the prompt field's `name` plus a `dd / format / required` summary; the expanded
  body splits the PromptField's 11+ properties into four blocks — *Basic* (`name` / `dd` /
  `label` / `format` / `required` / `default`, always-open SchemaForm), *Advanced* (`hidden`
  / `disabled` / `colspan`, closed-by-default `<details>`), *Lookup binds* (the
  `lookup_param_binds` ParamBind list — v2's port of v1's `ly_act_params_filters`), and
  *Conditions* (`visible_when` / `required_when` / `disabled_when`). Each block is its own
  SchemaForm over a `pickSchemaProperties` slice of `PromptField`'s `$def`, mirroring the
  visual-builder field inspector.
* Only rendered for the three ParamBind-bearing variants — `run_query` / `call_api` /
  `navigate` (the `_PromptableMixin` carriers). The other four action types (`set_field` /
  `confirm` / `notify` / `refresh`) don't carry prompt fields, so the editor stays hidden.
* Expansion state keyed by `<parent action-list key>:<action idx>` so each action's prompt
  list remembers its open row across re-renders (e.g. `on_save:0`, `row_menu:1`).

Shipped this slice — **top-level-key rename for connectors** lives in `liberty/web/rename.py`
+ `POST /admin/config/rename`:
* `rename_connector(old, new, *, connectors_path, screens_path, menus_path, dictionary_path,
  dashboards_path, charts_path)` walks every file via ``tomlkit`` (comments preserved), then
  rewrites:
  - `connectors.toml` — renames the top-level `[connectors.<old>]` subtree.
  - `screens.toml` — every ``Screen.connector`` / ``NestedFormTab.connector`` /
    ``NestedTableTab.connector`` / ``RunQueryAction.connector`` / ``CallApiAction.connector``
    / ``NavigateAction.connector`` / ``row_click_connector`` field value (recursive walk so
    deeply-nested ChainAction / IfAction / LoopAction steps all get visited).
  - `menus.toml` — every ``MenuItem.connector`` value. The matching `[menus.<old>]` *app* key
    is intentionally left alone (apps and connectors are distinct concepts even though they
    conventionally share a name); a warning surfaces when an app key with the same name
    exists so the operator can decide whether to follow up.
  - `dictionary.toml` — renames `[connectors.<old>]` scope; updates every ``LookupDef.connector``
    / ``SequenceDef.connector`` reference (shared + per-connector-scoped).
  - `dashboards.toml` — every ``ChartWidget.connector`` / ``KpiWidget.connector`` /
    ``DashboardFilterOptions.connector``.
  - `charts.toml` — every ``ChartDef.connector``.
* Two-pass strategy: in-memory rewrite + Pydantic validation of every rewritten doc; on any
  validation failure (collision, broken ref) nothing gets written. ``RenameResult`` reports
  ``{kind, old_name, new_name, files: {path: ref_count}, warnings, total_refs}`` so the
  operator sees what was touched.
* ConnectorsBuilder's Rename button now calls the endpoint, runs `/admin/reload` on success
  + bumps the WorkspaceContext nonce so screens / menus / dashboards reflect the new name
  app-wide. Refuses to fire with unsaved local edits (prompts the operator to save first —
  the disk-side rewrite + reload would clobber pending changes otherwise).
* 12 backend tests pin every cross-file reference + the edge cases (collision rejects, invalid
  identifier, missing connector, self-rename, endpoint auth gating).
* Rename flavours now covered by ``POST /admin/config/rename`` (``kind`` discriminator):
  - ``connector`` — top-level ``[connectors.<old>]`` in connectors.toml + every cross-file
    ``connector = "<old>"`` reference (screens / menus / dictionary / dashboards / charts).
  - ``sequence`` / ``lookup`` — ``[sequences.<old>]`` / ``[lookups.<old>]`` (shared OR per-
    connector scope via ``scope=<conn>``) + every same-scope ``DictionaryEntry.rules_values``
    reference (matched by the entry's ``rules`` kind — only SEQUENCE/NN entries cascade on
    sequence rename, only LOOKUP entries on lookup rename). Shared and scoped sequences/
    lookups can carry the same id (a v1 migration produces that shape — every app's sequence
    is numbered ``1``); scope narrows the rename to one side.
  - ``screen_app`` — ``[screens.<old>]`` top-level + the matching ``[menus.<old>]`` in
    menus.toml when one exists (apps and connectors are distinct concepts but in practice
    share a name; warning fires when no matching menu block exists).
  - ``dictionary_entry`` — ``[entries.<old>]`` (shared or scoped) + every ``ColumnHint.dd`` /
    ``PromptField.dd`` reference across screens.toml (scope-blind walk — ``dd`` fields are
    free strings without a scope mechanism) + ``SequenceDef.dd_id`` and
    ``LookupDef.return_params`` references in the same scope.
* Frontend wiring lands on each builder's existing Rename… button:
  - ``ConnectorsBuilder`` → connector rename.
  - ``DictionaryBuilder`` → routes ``entries`` / ``lookups`` / ``sequences`` rename through
    the endpoint (scope-aware via the chip strip); ``enums`` / ``framework_enums`` keep the
    local in-memory rename (no backend endpoint, narrower cascade).
  - ``ScreensBuilder`` → new Rename chip on each app row.
  All four refuse to fire with unsaved local edits + auto-reload + bump the WorkspaceContext
  nonce after success so screens / menus / dashboards everywhere reflect the new name.

Still-loose ends: **frontend vitest + CI** (the Python side has 502 tests; the frontend has
none).

**Phase 8 (Charts & Dashboards) — DONE (runtime + builder).** Lives in
`liberty/web/dashboards.py` + `liberty/dashboards/config.py` + `frontend/src/pages/DashboardView/`
+ `frontend/src/pages/Settings/DashboardsBuilder.tsx`:

* `MenuItem.type = "dashboard"` is a first-class menu kind. Sidebar resolves a dashboard leaf
  to `/dashboard/<id>` (no connector segment — the catalog is flat, keyed by id only); the
  `TabsContext` carries it like sql / http tabs. `TabHost` lazy-loads `<DashboardView>`.
* `GET /api/dashboards/{id}` resolves chart references (`chart = "users_per_app"`) into inline
  `connector` / `query` / `spec` shapes so the frontend sees one uniform widget payload; the
  per-widget permission gate (`sql:{connector}:{query}`) filters out widgets the caller can't
  read at request time. A dashboard with zero readable widgets still surfaces (placeholder for
  admin who curated it).
* Widgets — `ChartWidget` (over Recharts, same pipeline as TableView's chart mode) and
  `KpiWidget` (aggregates a single column over the query's rows). Other widget kinds (table,
  markdown, heading) flagged as later slices.
* `DashboardFilter` — the dashboard-level filter bar; each filter binds to a dictionary key
  so widgets whose query has a matching `dd` column get the value as a `:placeholder` bind.
  `useLookupTables` resolves the options once per session.
* Responsive 12-column CSS grid (12 desktop → 6 tablet → 1 mobile). Each widget carries
  `col_span` / `row_span` (default 4 / 1). Per-widget row height is 150px.
* Settings → Dashboards is the point-and-click builder over `config/dashboards.toml`. New
  dashboards are routed via `MenusBuilder` (`type = "dashboard"`, `target = <id>`); the
  `AppMenu.home` pointer above lets an operator make a dashboard the app's landing page.
* Caveat: `config/menus.toml` is gitignored — operators who re-run `liberty-migrate menu`
  after curating a dashboard menu leaf lose it on the next migration (v1's `ly_menus` has no
  dashboard concept and the migrator emits the same set of items). The shipped
  `config/menus.toml` carries a warning comment + has nomasx1's `home = "overview"` set so the
  framework restores the dashboard via the home redirect even when the menu leaf is missing.

531 backend tests pass.

**Roadmap (planned, see `docs/PLAN.md`):** **Phase 5** is effectively complete from a
framework standpoint — the NOMAJDE cutover is operator work, and the historic AUD_<table>
data carry-over is per-customer (handled manually). → **Phase 9** notifications / reporting
/ backports → **Phase 10** the Airflow replacement (in-project Python/local-Spark jobs &
scheduling).

**Big-grid scaling — slice 1 (row virtualization) DONE.** `frontend/src/common/DataTable.tsx`
now wraps the tbody with `@tanstack/react-virtual`'s `useVirtualizer`. Only the visible rows
plus a small overscan window (`overscan: 10`) mount to the DOM; off-screen rows are replaced
by top + bottom spacer `<tr>`s that hold the scroll extent. Row heights start from a 26px
estimate and re-measure via the default `measureElement` callback (each row carries
`data-index` + `ref={rowVirtualizer.measureElement}`) so a group row, an edit-mode row with
taller cell content, or a wrapped cell doesn't drift the spacer math. Always-on — no
behavioural cliff between small and large grids; the same code path renders 25 rows or 100K.

`<table>` semantics stay intact (no div-grid switch); the existing `table-layout: auto`
keeps content-fit column sizing, and every existing feature (multi-sort, grouping,
hide/reorder, filter row, hover states, edit-mode row classes, group toggles, CSV/Excel
export, row click + right-click) keeps working unchanged — virtualization only changes which
rows are *mounted*, not how a mounted row renders. The column-width-jitter risk that exists
in principle (different visible rows = different natural widths) hasn't shown up at the
content widths v2's screens have; revisit if it does.

The `PageSizeSelect` gained a sentinel **"All"** option (`Number.MAX_SAFE_INTEGER`) — picks
"no pagination, show every loaded row in one scrollable list", which is the natural shape
once virtualization is in place. The 500 / 1000 in-between options ride alongside. The
"Showing X–Y of Z" line + the pagination chrome handle the sentinel correctly (overflow
clamped to the row count). Operators with a screen whose pool sets `max_rows = 100000` now
pick "All" and get a smooth single-scroll experience.

**Big-grid scaling — slice 2 (NDJSON streaming) DONE.** Adds **progressive rendering** so
the operator sees the first rows ~100 ms after Run instead of waiting for the whole result
to land. The SQL query still runs in full — what changes is *how* it's delivered: as the DB
cursor advances, rows trickle to the browser in small NDJSON chunks; the TableView appends
them live. On a 15K-row screen that took ~6 s before the first paint, the first 100 rows
are now visible inside ~150 ms and the grid stays responsive throughout.

Backend (`liberty/connectors/sql.py` + `liberty/web/connectors.py`):
- `SQLConnector.execute_stream(query, params, *, chunk_size=100, …)` is an async generator
  that yields :class:`StreamMeta` → N × :class:`StreamRows` → :class:`StreamDone`. Uses
  SQLAlchemy's `conn.stream(stmt, …, execution_options={"stream_results": True,
  "max_row_buffer": chunk})` for a real server-side cursor. Both `OracleDialectAsync_oracledb`
  and `PGDialect_asyncpg` report `supports_server_side_cursors = True` — the same code runs
  on Postgres (asyncpg cursors via `DECLARE … CURSOR`) and Oracle (REF cursor + `arraysize`).
  SQLite/aiosqlite emits the same event shape (it effectively buffers under the hood — fine
  for tests). Streaming is **SELECT-only** — writes raise `StatementNotAllowedError` (the
  protocol has no row stream and needs a transactional commit; callers fall back to
  :meth:`execute`). Same prep as :meth:`execute` is inlined (filter wrap, form rules, schema
  placeholders, row cap, dictionary scope) so the streamed shape stays identical to the
  one-shot shape. Trim-strings + JDE-date conversion run **per chunk** as rows are yielded.
  Chunk size is clamped to `[1, MAX_CHUNK_SIZE=5000]`; the default is `DEFAULT_CHUNK_SIZE=100`.
- `POST/GET /api/sql/{c}/{q}?_stream=1` opts the response into NDJSON
  (`application/x-ndjson`). `?_chunk_size=N` overrides the default chunk size. The route
  pre-flights the permission + connector + query lookup + statement-type check *synchronously*
  so 401/403/404/405 surface as standard HTTP errors before any byte ships. Mid-stream
  failures (the cursor opens but a fetchmany raises) can't change the HTTP status — instead
  the stream terminates with a `{"kind":"error","detail":…}` line so the consumer's NDJSON
  parser sees a clean end. `X-Accel-Buffering: no` + `Cache-Control: no-cache` headers stop
  proxies from holding back the chunks.
- 8 streaming tests in `tests/test_web_connectors.py` cover: full event sequence
  (meta + rows + done), POST + params + filter narrowing, the permission short-circuit
  (403 before any NDJSON ships), non-SELECT rejection (405), unknown-query (404), mid-stream
  error → inline `error` event, chunk size clamping (0 → 100, 9999999 → 5000), and `_limit`
  truncation (`done.truncated = True`). 531 backend tests pass (was 523 + 8 new).

Frontend (`frontend/src/api/client.ts` + `frontend/src/pages/TableView/index.tsx`):
- `streamNDJSON(path, {method, body, signal}, onEvent)` — parallel to the existing `streamSSE`
  helper. Uses `fetch().body.getReader()` + `TextDecoder` to parse newline-delimited JSON,
  filtering blank lines + handling a non-`\n`-terminated trailing chunk. Throws `ApiError`
  on a non-OK initial response (auth / not-found / wrong method); a mid-stream `error` event
  is delivered through `onEvent` like any other event. The `AbortSignal` cancels the fetch +
  releases the server-side cursor when the user navigates away.
- TableView's `run()` switches the SELECT path to streaming. `meta` event seeds columns
  immediately (headers + filter row paint while the first chunk is still in flight); `rows`
  events accumulate into `allRows` and re-render on a 120 ms throttle (per-chunk re-renders
  would saturate React); `done` forces a final flush with the actual `truncated` +
  `duration_ms`. An `AbortController` stored in a ref cancels the previous stream when a new
  run starts or the tab unmounts (prevents leaked cursors on Oracle).
- Run button surfaces the live count: **"Running… 1,200 rows"** instead of an
  undifferentiated spinner. EN/FR locale: `table.runningRows`.
- Writes (non-SELECT) keep using the existing non-streaming POST — no protocol change there.
- Per-screen opt-out isn't needed; streaming is strictly better than the one-shot path for
  every SELECT we've measured. The shape of the assembled `QueryResult` is identical to what
  the non-streaming path produces, so the rest of the TableView (column merge, FilterPanel,
  ResultTable, ChartView, edit mode, dialog) works unchanged.

Slice 3 — **cursor-based server pagination** — still deferred. Streaming gets the first
rows on screen fast, but the full payload still travels for big queries. Cursor pagination
would let the frontend pull only what it scrolls past + sort/filter on the server. The
screen's `key_columns` (already collected per Phase 3) become the cursor's stable ORDER BY
tiebreaker; the FilterPanel's `filter`-flagged columns already pre-narrow server-side, so
the cursor would only need to thread through that wrapping. Pull this in whenever a real
screen needs >100K rows in a single shot, not before.

## Run it

```bash
.venv/bin/pytest -v               # tests   ·   pytest --html=test-report.html --self-contained-html → a browsable HTML report (pytest-html, like Playwright's)
./start.sh init-db                # FIRST RUN: bootstrap the auth store + an `admin` user (prints the password) — default backend = "toml" → writes config/auth.toml; backend = "db" → creates the ly2_* tables
./start.sh                        # builds frontend/dist if stale, then runs FastAPI serving the SPA + API on :8000
./start.sh dev                    # same, with --reload   ·   ./start.sh frontend → Vite :5173 (HMR)   ·   ./start.sh help
# by hand: .venv/bin/fastapi dev liberty/main.py   |   .venv/bin/uvicorn liberty.main:app --reload   |   .venv/bin/liberty-next
.venv/bin/liberty-connectors list # poke at config/connectors.toml without the web layer
.venv/bin/liberty-migrate all --source-url postgresql+asyncpg://…/libnsx1 -o migrated.toml   # v1 ly_* → connectors.toml fragment
.venv/bin/liberty-migrate dictionary --source-url postgresql+asyncpg://…/libnsx1 -o config/dictionary.toml   # v1 ly_dictionary → shared field labels
.venv/bin/liberty-migrate menu --source-url postgresql+asyncpg://…/libnsx1 --connector nomasx1 -o config/menus.toml   # v1 ly_menus → app nav tree
.venv/bin/liberty-migrate screen --source-url postgresql+asyncpg://…/libnsx1 --connector nomasx1 -o config/screens.toml   # v1 ly_tables+ly_dlg_* → screens.toml
.venv/bin/liberty-migrate diff --source-url postgresql+asyncpg://…/libnsx1 --config-dir config   # what didn't migrate (exit 1 if missing/mismatched)
.venv/bin/liberty-crypto encrypt 'secret' --master-key "$LIBERTY_MASTER_KEY"   # v1-compatible ENC:… (decrypt / is-encrypted too)
.venv/bin/liberty-license verify "$LIBERTY_LICENSE_KEY"   # inspect a license key → JSON status (exit 0=full, 1=restricted); `status` checks the configured one
(cd frontend && npm install && npm run build)   # → frontend/dist (the backend serves it at /; no copy step)
# HTTP: GET /api/connectors  ·  GET/POST /api/sql/{c}/{q}  ·  POST /api/http/{c}/{e}  ·  GET /api/menus  ·  GET /api/screens  ·  GET /api/license  ·  /docs (OpenAPI)
# AI: set ANTHROPIC_API_KEY, then POST /ai/chat (SSE) with an `ai:chat`-permitted token
./start.sh init-config            # copy config/{connectors,dictionary,menus,screens}.toml.example → the real (uncommitted) files (serve/dev do this too)
# fresh checkout: python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"  (then ./start.sh init-config, or run liberty-migrate)
```

`start.sh` (repo root): `serve` (default) | `dev` | `api [dev]` | `build` | `frontend` |
`init-db` | `init-config` | `help`. `fastapi-cli` is a dependency, so `fastapi dev liberty/main.py` works too.

**Pools / DB / secrets:** `config/connectors.toml`, `config/dictionary.toml`, `config/menus.toml` are
the *per-deployment* config and are **not committed** — the open framework ships only `*.toml.example`
templates (copy them with `./start.sh init-config`, or fill them with `liberty-migrate`); licensed
apps (**nomasx1**, **NOMAJDE**) ship these files separately. A fresh checkout with none of them runs
API-only (just `[pools.default]`). (Reference shape — what nomasx1/NOMAJDE put there: the migrated
nomasx1 app (Postgres) plus the NOMAJDE app, whose v1 DB spans three v2 connectors: `jdedwards` (the
Oracle JDE business DB), `nomajde` (its Postgres app DB), `session` (a stub) — and the `ais_connection`
API connector; nomasx1's dictionary entries nested under `[connectors.nomasx1.*]`, NOMAJDE's at the
top level (shared); `[menus.nomasx1]` + `[menus.nomajde]`, the latter's leaves spelling out
`connector = "jdedwards"` where the screen runs against that connector. The `nomasx1`/`nomajde`
connectors carry `licensed = true` — gated behind `[license]`, see *License* below.) Convention: `[pools.default]`
is the **framework pool** — historically it held v2's own users/roles, but with the default
`[auth] backend = "toml"` those live in `config/auth.toml` instead, so **no pool is needed at
startup at all** (the framework opens connections lazily, on the first query against a pool);
`[pools.default]` is now just the fallback pool for a connector that doesn't name one (and the
ly2_* tables if you switch `[auth] backend = "db"`). Every `[pools.X]`
is an *app* pool — `nomasx1`, `jdedwards`, `nomajde`, … — migrated straight from v1's `ly_applications`
(`url` + `dialect` + `pool_size`/`max_overflow` + `max_rows` + an `ENC:` `password` + a `[pools.X.schemas]`
map for `#SCHEMA.<NAME>#` query placeholders, from v1's `ly_db_schema`), opened **lazily**
on first query; mirrors the v1 split between an app's "definition DB" (queries/users/roles → now TOML)
and its "data DB". `[pools.default]` defaults to `${LIBERTY_DB_URL:-sqlite+aiosqlite:///./liberty.db}`
(set `LIBERTY_DB_URL` for Postgres; SQLite `liberty.db` is gitignored); the app pools' URLs are v1's
literal `apps_host`/`apps_port`/`apps_database` (so a docker `@pg:5432` host etc. — edit the `url` line if
your deployment's host differs), and `LIBERTY_MASTER_KEY` must equal v1's `MASTER_KEY` so v2 can decrypt
their `password`. `substitute_env` supports `${NAME}` and `${NAME:-default}` (shell `:-` = unset *or* empty
→ default), in both `connectors.toml` and `app.toml`. An empty pool URL raises `UnknownPoolError`, and any
`ConnectorError` not caught per-route (e.g. an unconfigured DB on `/auth/login`) becomes a clean
**503** via a global handler in `liberty/main.py`. `LIBERTY_JWT_SECRET` empty → ephemeral key + a warning.
**Encrypted fields** (`ENC:…` values from v1 — pool `password`s, a migrated API connector's `auth_password`,
or columns the user's other scripts touch) are decrypted at runtime with `[crypto] master_key`
(`= "${LIBERTY_MASTER_KEY}"` in `app.toml`) — same AES-256-GCM scheme and key as v1; set
`LIBERTY_MASTER_KEY` to your v1 `MASTER_KEY`. See *Crypto* above; `liberty-crypto` is the CLI.

## Layout

```
config/         app.toml (committed — framework config) · connectors.toml / dictionary.toml / menus.toml / screens.toml (NOT committed —
                per-deployment / licensed-app config; only *.toml.example templates are committed; `./start.sh init-config` copies them) ·
                auth.toml (the TOML auth store — users/roles, password hashes; gitignored, created by `liberty-admin init-db`)
liberty/        main.py, config.py, crypto.py, cli.py, admin_cli.py, migrate_cli.py, crypto_cli.py, license_cli.py
                · connectors/{config,base,db,sql,api,registry,dictionary}.py
                · licensing/{__init__.py (verify_license), public.pem}   (RS256 license-key verification — the embedded public key)
                · menus/config.py · screens/config.py (ParamBind, FieldCondition, ScreenField, PromptField,
                  FormTab/NestedFormTab/NestedTableTab → ScreenTab, ScreenDialog with on_load/on_save/on_cancel,
                  Action union {RunQueryAction, CallApiAction, NavigateAction, SetFieldAction, ConfirmAction,
                  NotifyAction, RefreshAction}, Screen with on_insert/on_update/on_delete + row_click_screen,
                  ScreensFile)
                · auth/{authstore,password,tokens,principal,oidc,dependencies,routes, models,db,service}.py
                  (authstore = the TOML/DB backend abstraction + config/auth.toml schema; models/db/service = the DB backend's internals)
                · ai/{tools,connector_tools,assistant,routes}.py
                · web/{deps,errors,connectors,menus,screens,export,license,admin}.py
                · migrations/{v1,source}.py
frontend/       Vite + React 19 + TS (emotion + react-i18next) — src/{App,main,theme,i18n}.* +
                src/{api,auth,workspace,types,services,common,pages,components,locales}/* (nomaubl layout:
                common/ = shared primitives, pages/<X>/ = screens, types/+services/ = no-React);
                built dist/ served by liberty/main.py; gitignored
start.sh        run/dev helper (serve | dev | api | build | frontend | init-db)
tests/
docs/PLAN.md    full phased plan + design decisions + rationale
```

## Reference

- Full plan & decisions: `docs/PLAN.md`
- nomaubl connector pattern: `../../JavaProjects/nomaubl` — key files:
  `src/custom/ubl/api/ApiConnectorClient.java`, `src/custom/ubl/api/SqlConnectorClient.java`,
  `src/custom/db/DynamicResultMapper.java`, `src/custom/ubl/web/AiAssistant.java`,
  `src/custom/ubl/auth/AuthManager.java`
- v1 hotspots (read-only, for migration): `../liberty-framework/liberty/framework/services/api_services.py`
  (742-line dispatch), `.../database/base_dao.py` (510-line SQL builder),
  `.../services/rest_services.py` (OpenAI + external API)
