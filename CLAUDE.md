# Liberty v2 — Claude Code context

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
  `{kind:"boolean", true_value}` / `{kind:"enum", values:[…]}` / `{kind:"lookup", connector, query, value, label}`
  dict (None for form-layer rules — SEQUENCE/SYSDATE/LOGIN/PASSWORD/CURRENT_DATE — those wait for Phase 6).
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
  `GET /admin/config/schema` → `{pool, sql, api}` = the `PoolConfig` / `SqlConnectorConfig` /
  `ApiConnectorConfig` `model_json_schema()`s, each with its own `$defs` (`QueryDef`/`ColumnHint`/
  `ParamDef`/`EndpointDef`/…) — the UI renders its forms from this ; `GET /admin/config/pools` →
  `{path, pools: {name: PoolConfig dict}}` + `PUT /admin/config/pools` (`{pools: {name: dict}}`) —
  validates each against `PoolConfig`, drops default-valued keys, then **surgically rewrites only the
  `[pools.*]` tables** in `connectors.toml` via `tomlkit` (comments + the `[connectors.*]` tables +
  formatting left intact) ; `GET /admin/config/connectors/parsed` → `{path, connectors: {name: dict}}`
  (default-valued keys dropped) + `PUT /admin/config/connectors/parsed` (`{connectors: {name: dict}}`)
  — validates each against the discriminated connector schema, rewrites only the `[connectors.*]`
  tables (a *changed* connector's own subtree is re-rendered by `tomlkit`, so its inline `columns =
  [{…}]` arrays may become `[[…]]` tables — functionally identical), re-parses the whole result before
  writing. PUT endpoints don't reload — call `POST /admin/reload` after. (Dep: `tomlkit` —
  comment/format-preserving TOML edits.) The field docs on the config models are in `Field(description=)`
  so the builder forms show them as hints.
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
  union (a textarea, or per-dialect textareas); anything else → a "edit in the raw editor" note. With
  `onNavigate` it renders `list[Model]` / nested-object props as drill-in rows, without it as inline
  accordions) + `SchemaNavigator` (a master-detail wrapper around `SchemaForm` — shows one level at a time
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
  utility pill: app-picker (`WorkspaceSelect` — lists the *apps* (menu-having connectors), shown when ≥2) · EN/FR · dark/light ·
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
  list of sql/api connectors + a `SchemaNavigator` over the matching schema — drill connector → query →
  column → … via a breadcrumb, no nested accordions — → `PUT /admin/config/connectors/parsed` + Reload),
  and `RawEditor` = the Monaco `connectors.toml` editor (`language="ini"`, theme-aware, over
  `GET/PUT /admin/config/connectors` + Reload — the escape hatch); the structured editors don't support
  rename yet — delete + re-add — the Phase-7 builder slices), `Login` + `OidcCallback`.
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
  a self-service change-password flow (needs a backend endpoint), `@tanstack/react-virtual`
  for huge result grids, Vitest/RTL frontend tests, frontend build in CI. Reference app:
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
  ly_dlg_col rows)` → `{query_id: [ColumnHint dict]}` (each `col_target` → `{name, dd?` (= v1's
  `col_dd_id` — only when ≠ `name`; the connector looks the entry up under `name` otherwise),
  `label?` (only when an explicit `col_label` overrides the dictionary), `hidden?` (`col_visible`
  reads false), `filter?` (`col_filter` reads true — table widgets only), `format?` (only when an explicit
  `col_type` overrides the dictionary)`}`; table-widget
  columns beat form-field columns; first `(query, col)` wins → per-query list keeps `col_seq` order)
  — passed to `migrate_sql_queries(column_hints=…)`, attached to each *read* query's `columns`;
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
- `liberty/migrate_cli.py` (`liberty-migrate` script) — `sql | api | all | dictionary | menu`,
  `--source-url <v1-db-url>`, `--dbtype`, `--prefix`, `-o out.toml` (else stdout); `sql`/`all`
  also scaffold the `ly_applications` pools + carry over the `ly_tbl_col`/`ly_dlg_col` column
  hints + `ly_tbl_filters`/`ly_dlg_filters` cascading-filter deps + `ly_cdn_params` conditional-render
  rules (`visible_when`) + the `ly_tables`/`ly_dlg_frm`
  screen labels & auto-load flags (the hints reference the
  dictionary — also run `liberty-migrate dictionary -o config/dictionary.toml`);
  `dictionary [--default-language en] [--connector <app>]` migrates `ly_dictionary` (+ `ly_dictionary_l`)
  — `--connector` nests the entries under `[connectors.<app>.entries.*]` so several migrated apps don't
  clash on a `dd_id`; `menu --connector <app>` migrates `ly_menus` (+ `ly_menus_l`) → `config/menus.toml`
  (one `[menus.<app>]` per app — run `sql`/`all` first so the menu's query targets exist). Prepends a
  `# migrated: …` summary + the `${…}` placeholders the operator must fill in (incl. each
  `${MIGRATED_PW_*}` — recover from `ly_applications.apps_password` with `liberty-crypto decrypt`).
v1 (`../liberty-framework/`) is **read-only** — the readers only SELECT. The output is a
fragment to review + merge into `config/connectors.toml` (the `dictionary` output → `config/dictionary.toml`).
*Not yet done:* validate-by-diff against nomasx1's read paths; migrate the real apps
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

306 tests pass.

**Roadmap (planned, see `docs/PLAN.md`):** finish Phase 5 (validate-by-diff + the real
nomasx1→NOMAJDE cutover; AIRFLOW is *not* migrated; migrate v1's `AUD_<table>` audit) → **Phase 6**
the form/screen engine (dialogs + conditions + actions/events + `call_api` from actions + table
contextual menus — the `visible_when`/`filter_from` work is its table-side first slice; design it
against real migrated screens) → **Phase 7** the config builders (a *schema-driven* UI shell — `SchemaForm`
over the Pydantic config — not raw TOML — **done so far**: the `[pools.*]` and `[connectors.*]` builders
(sql + api), `SchemaForm` + the `SchemaNavigator` (breadcrumb drill-down master-detail — no nested accordions),
the `GET /admin/config/schema` + `GET/PUT /admin/config/pools` + `GET/PUT /admin/config/connectors/parsed`
endpoints, and the field docs moved to `Field(description=)`; next: a dictionary builder, a menus tree builder,
a SQL editor + "test run" preview for queries, plus rename support; + git-backed config-file versioning +
frontend tests/CI) → **Phase 8** charts & dashboards →
**Phase 9** notifications / reporting / backports → **Phase 10** the Airflow replacement (in-project
Python/local-Spark jobs & scheduling).

## Run it

```bash
.venv/bin/pytest -v               # tests   ·   pytest --html=test-report.html --self-contained-html → a browsable HTML report (pytest-html, like Playwright's)
./start.sh init-db                # FIRST RUN: bootstrap the auth store + an `admin` user (prints the password) — default backend = "toml" → writes config/auth.toml; backend = "db" → creates the ly2_* tables
./start.sh                        # builds frontend/dist if stale, then runs FastAPI serving the SPA + API on :8000
./start.sh dev                    # same, with --reload   ·   ./start.sh frontend → Vite :5173 (HMR)   ·   ./start.sh help
# by hand: .venv/bin/fastapi dev liberty/main.py   |   .venv/bin/uvicorn liberty.main:app --reload   |   .venv/bin/liberty-v2
.venv/bin/liberty-connectors list # poke at config/connectors.toml without the web layer
.venv/bin/liberty-migrate all --source-url postgresql+asyncpg://…/libnsx1 -o migrated.toml   # v1 ly_* → connectors.toml fragment
.venv/bin/liberty-migrate dictionary --source-url postgresql+asyncpg://…/libnsx1 -o config/dictionary.toml   # v1 ly_dictionary → shared field labels
.venv/bin/liberty-migrate menu --source-url postgresql+asyncpg://…/libnsx1 --connector nomasx1 -o config/menus.toml   # v1 ly_menus → app nav tree
.venv/bin/liberty-crypto encrypt 'secret' --master-key "$LIBERTY_MASTER_KEY"   # v1-compatible ENC:… (decrypt / is-encrypted too)
.venv/bin/liberty-license verify "$LIBERTY_LICENSE_KEY"   # inspect a license key → JSON status (exit 0=full, 1=restricted); `status` checks the configured one
(cd frontend && npm install && npm run build)   # → frontend/dist (the backend serves it at /; no copy step)
# HTTP: GET /api/connectors  ·  GET/POST /api/sql/{c}/{q}  ·  POST /api/http/{c}/{e}  ·  GET /api/menus  ·  GET /api/license  ·  /docs (OpenAPI)
# AI: set ANTHROPIC_API_KEY, then POST /ai/chat (SSE) with an `ai:chat`-permitted token
./start.sh init-config            # copy config/{connectors,dictionary,menus}.toml.example → the real (uncommitted) files (serve/dev do this too)
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
config/         app.toml (committed — framework config) · connectors.toml / dictionary.toml / menus.toml (NOT committed —
                per-deployment / licensed-app config; only *.toml.example templates are committed; `./start.sh init-config` copies them) ·
                auth.toml (the TOML auth store — users/roles, password hashes; gitignored, created by `liberty-admin init-db`)
liberty/        main.py, config.py, crypto.py, cli.py, admin_cli.py, migrate_cli.py, crypto_cli.py, license_cli.py
                · connectors/{config,base,db,sql,api,registry,dictionary}.py
                · licensing/{__init__.py (verify_license), public.pem}   (RS256 license-key verification — the embedded public key)
                · menus/config.py · auth/{authstore,password,tokens,principal,oidc,dependencies,routes, models,db,service}.py
                  (authstore = the TOML/DB backend abstraction + config/auth.toml schema; models/db/service = the DB backend's internals)
                · ai/{tools,connector_tools,assistant,routes}.py
                · web/{deps,errors,connectors,menus,license,admin}.py
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
