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
authlib (OIDC/Keycloak) · argon2-cffi (passwords) · React 19 + Vite + TS
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
  `label?`, `hidden?`, `filter?`, `width?`, `align?`, `format?`) — these only *augment* the still-discovered
  schema (display title / visibility / column order / a `filter` flag — v1's `col_filter` — / a UI-interpreted
  `format`); `label`/`format` may be omitted and pulled from the shared dictionary (the entry key is `dd`, or
  `name` when `dd` is unset; `dd = ""` opts out); a hint for a column the query doesn't return is ignored.
  A query may also carry `label`/`description` (display names — the frontend titles the TableView with
  `description`, else `label`, else the menu label; the menu label rides on the tab), `auto_load = true`
  (v1's per-table auto-load — the TableView runs a SELECT immediately on open instead of waiting for a Run click),
  and `max_rows` (the SELECT row cap for this query — overrides the connector's, then the pool's, then 1000;
  a per-request override beats it). `[pools.*]` may carry an explicit `dialect` (else derived from the URL) and a
  `max_rows` (the pool's default row cap — v1's per-app `apps_limit`); `[connectors.*]` (sql) a `max_rows` too.
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
  → the pool's backend name (a live engine's own dialect / the explicit setting / the URL).
- `sql.py` — `SQLConnector`: named queries, `:param` binding via SQLAlchemy
  `text()` (never string-substituted), the SQL variant matching the pool's dialect is
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

*Auth* lives in `liberty/auth/`:
- `models.py` — SQLAlchemy 2.0 ORM: `User`, `Role`, `user_roles` M2M (tables
  `ly2_users` / `ly2_roles` / `ly2_user_roles`). These are *app data*, not v1-style
  metadata tables. `Role.permissions` is a JSON list of strings (`"sql:liberty:read"`,
  `"*"`); superuser bypasses checks. Own `Base` → `create_all` scopes to auth tables.
- `password.py` — Argon2id via `argon2-cffi` (`hash_password` / `verify_password` /
  `needs_rehash`).
- `tokens.py` — `TokenService` mints/verifies HS256 JWTs: `access` (carries
  `roles`/`perms`/`sup` → no per-request DB hit) and `refresh` (re-reads the user).
- `db.py` — `AuthDatabase`: lazy `async_sessionmaker` over a `PoolRegistry` pool
  (`[auth] pool`, default `default`); `create_schema()` for `liberty-admin init-db`.
  ⚠ async-ORM gotcha: assign `user.roles = [...]` explicitly (even `[]`) on new
  rows — a freshly-flushed object's unloaded relationship lazy-loads on access,
  which raises `MissingGreenlet` under async.
- `principal.py` — `Principal` (built from JWT claims, no DB): `has_permission`
  with colon-segment globs (`sql:*` ⊇ `sql:liberty:read`), `has_role`, superuser.
- `service.py` — `AuthService` over an `AsyncSession`: `authenticate` (+ rehash),
  `create_user`, `set_password`/`set_active`, role ops, `provision_oidc_user`
  (find-or-create by `(provider="oidc", sub)`, username-collision suffixing).
- `oidc.py` — `build_oidc(settings)` → Authlib Starlette `OAuth` client (Keycloak
  discovery URL); `None` when disabled. ID-token validation is Authlib's job.
- `dependencies.py` — `get_current_principal` / `optional_principal`,
  `require_permission(perm)` / `require_role(role)` / `require_superuser`,
  `get_auth_service` (session per request), `get_oidc` (404 if off). All read
  `request.app.state`.
- `routes.py` — `POST /auth/login`, `POST /auth/refresh`, `GET /auth/me`,
  `GET /auth/oidc/login`, `GET /auth/oidc/callback` (both 404 when OIDC is off).
  Both login paths mint *our* JWTs; the IdP's tokens aren't propagated.
- `liberty/admin_cli.py` (`liberty-admin` script) — `init-db` (create tables +
  bootstrap a superuser `admin` with role `admin`/perm `*`; password from
  `--password` / `--password-env` / generated-and-printed), `create-user`,
  `set-password`, `set-active`, `list-users`, `create-role`.
- `liberty/config.py` — added `[auth]` + `[oidc]` settings; `${ENV_VAR}`
  substitution now applies to `app.toml` too (`substitute_env` moved here).
- `liberty/main.py` — `create_app(settings=None)`; lifespan builds `auth_db`,
  `token_service`, `oidc` (and `ai` — below) on `app.state`; `SessionMiddleware`
  added iff OIDC enabled; includes the auth router; `/info` reports `auth.pool` +
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
- Also (added in Phase 4): `GET /admin/config/connectors` (raw `connectors.toml` text) and
  `PUT /admin/config/connectors` (validates the TOML against the schema, then writes — does
  *not* reload; call `POST /admin/reload` after). Both superuser.
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
  (`WorkspaceContext.tsx` — `WorkspaceProvider`/`useWorkspace()`: owns the one `GET /api/connectors`
  + `GET /api/menus` fetch; exposes `connectors` (all accessible) and `apps` (the subset that have a
  menu — one v1 app can map to several v2 connectors, so "app" ≠ "connector"; with no menus defined,
  every connector is an app); holds `currentApp` — the picked app, persisted, dropped if it's no
  longer an app, made to follow the route when you open a `/sql/<c>/…` or `/http/<c>/…` screen *only*
  if `<c>` is an app (so opening a data-source connector's screen via an app's menu doesn't yank the
  picker over) — plus `currentMenu` (the picked app's nav tree, or — with a single app — that one's,
  for the Sidebar). A pure-frontend soft filter; v2 auth is centralized so the v1 "pick an app at
  login" idea becomes "which app's screens am I looking at"), `src/types/`
  (`connectors.ts`/`auth.ts`/`ai.ts`/`menus.ts` — backend response shapes, no React), `src/services/`
  (plain-TS helpers/side-effect modules — `cells.ts`'s `cellText`/`ruleCell` (the latter applies the
  dictionary's BOOLEAN/ENUM/LOOKUP display rules), `lookups.ts` (`useLookupBatch` — fetches each
  LOOKUP-target query once, module-level session cache), `monaco.ts` (bundles
  Monaco + its worker, no CDN), `lookups.ts`'s `useLookupBatch` listed above)), `src/common/` (shared
  theme-driven primitives, one file each — `Button`, `Card`, `Input`/`Select`/`Textarea`/`Field`, `Tag`/`Mono`,
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
  with an ENUM rule renders a value `<select>`, a LOOKUP rule a `<select>` of resolved labels (`useLookupBatch`,
  the user picks the label not the code), both implicitly `equals`. A "Clear" button in the panel header resets
  all server filters. On Run it sends `:<col>` + `:<col>_op` for
  each filled field; the migration has wrapped such queries in
  `SELECT * FROM (<orig>) _flt WHERE …` so this actually pre-filters server-side before the grid loads (the
  in-grid TanStack filters then refine the loaded page; those `:<col>`/`:<col>_op` binds are kept out of the
  param form). SELECT → `GET` + the `DataTable`
  grid built from `result.columns`, honouring their display hints (label/hidden/width/align — `hidden` takes
  effect on first load and survives a stale saved grid state) and `rule`
  — BOOLEAN → ✓ green / ✗ red, ENUM → the value's label, LOOKUP → split into a "(ID)" column (raw code)
  + a resolved-label column (fetched once, raw value tooltipped, italic-muted while fetching); sorts/filters
  run on the displayed value, rule rendering is visual-only. When the query has writable companions, an
  **Edit** toggle puts the *whole grid* into edit mode (v1's FormsTable batch model): every cell editable,
  "+ Add row" / per-row "duplicate" / **Import** (.xlsx/.csv → headers matched to *result columns by header
  text* — name / label / "(ID)"-suffixed, case-insensitive — so the sheet's column order doesn't matter) /
  multi-row copy-paste (a selection checkbox column → Copy → Paste) → new rows (added at the *top*); a per-row
  × marks an existing row for deletion (a status column shows +/●/− marks); **Save**
  fires the lot — edited rows → `update_query` (the merged new values, plus the row's original values under
  `:<NAME>_ORIGINAL` so a key-aware WHERE can use them — the verbatim-migrated `_put`s don't yet), new rows →
  `insert_query`, deleted → `delete_query` (params sent both as-is + UPPERCASE — PG lowercases the read columns,
  v1's `_put`/`_post`/`_delete` use uppercase; `text()` binds only what it references) — then refetches;
  **Cancel** discards. (Modal-form edit
  = the form layer, Phase 6.) A non-SELECT query → `confirm` + `POST` + affected-rows banner),
  `HttpRunner` (`POST /api/http/...` + pretty `ApiResult` + JSON `Pre`),
  `Chat` (consumes the `/ai/chat` SSE — user bubbles plain, assistant bubbles rendered via
  `<Markdown>`, + `tool_call`/`tool_result` lines), `Settings` (a Monaco editor — `language="ini"`,
  theme follows dark/light — over `GET/PUT /admin/config/connectors` + Save + Reload),
  `Login` + `OidcCallback`.
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
  also wrapped — `SELECT * FROM (<orig>) _flt WHERE …` with a `:<col>` value bind + `:<col>_op` operator
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
  `pool_size` from `apps_pool_max`, `max_rows` from `apps_limit`; the DB **password is never inlined** — `${MIGRATED_PW_<NAME>}`,
  v1 keeps it `ENC:`-encrypted in `apps_password`; v1's reserved `default` pool is **skipped**
  — v2's `[pools.default]` is v2's own framework DB); `migrate_table_meta(ly_tables rows, ly_dlg_frm rows)` →
  `{query_id: {description?, auto_load?}}` (the table/form friendly label `tbl_label`/`frm_label` → the read
  query's `description`, `tbl_auto_load = 'Y'` → `auto_load = true`; a table widget beats a form) — passed to
  `migrate_sql_queries(table_meta=…)`; `migrate_key_columns(ly_tbl_col rows, ly_dlg_col rows)` → `{query_id:
  [col, …]}` (the `col_key = 'Y'` columns) — passed as `key_columns=…`; for an UPDATE-crud write query
  `migrate_sql_queries` rebinds those columns in the `_put`'s **WHERE** from `:<col>` to `:<col>_ORIGINAL`
  (the SET clause keeps `:<col>` — the new value), so editing a key column still updates the right row (the
  TableView sends the row's pre-edit values under `:<col>_ORIGINAL`); `migrate_column_hints(ly_tbl_col rows,
  ly_dlg_col rows)` → `{query_id: [ColumnHint dict]}` (each `col_target` → `{name, dd?` (= v1's
  `col_dd_id` — only when ≠ `name`; the connector looks the entry up under `name` otherwise),
  `label?` (only when an explicit `col_label` overrides the dictionary), `hidden?` (`col_visible`
  reads false), `filter?` (`col_filter` reads true — table widgets only), `format?` (only when an explicit
  `col_type` overrides the dictionary)`}`; table-widget
  columns beat form-field columns; first `(query, col)` wins → per-query list keeps `col_seq` order)
  — passed to `migrate_sql_queries(column_hints=…)`, attached to each *read* query's `columns`;
  `migrate_dictionary(ly_dictionary rows, ly_dictionary_l rows, enum_rows=(), enum_val_rows=(),
  enum_val_l_rows=(), lookup_rows=(), sql_rows=(), *, default_language="en", connector_name=None)`
  → the `dictionary.toml` dict — one `[entries.<dd_id>]` per `ly_dictionary` row (`label`=`dd_label`,
  `format`=a non-trivial `dd_type`, `rules`/`rules_values`/`default` verbatim, `[entries.<dd_id>.l]`
  = `{lng_id: lng_label}` from `ly_dictionary_l`); plus `[enums.<enum_id>]` from `ly_enum` (+
  `ly_enum_val` + `ly_enum_val_l` translations) and `[lookups.<lkp_id>]` from `ly_lookup` (its
  `lkp_query_id` resolved via *sql_rows* to the matching read query's v2 name, same logic as
  `migrate_menus`). `connector_name` nests all three sections under `[connectors.<name>.…]`. `migrate_menus(ly_menus rows, ly_menus_l rows, ly_tables rows,
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
  `read_column_hints(engine)` → (`ly_tbl_col`←`ly_tables`←`ly_query`, `ly_dlg_col`←`ly_dlg_frm`←`ly_query`
  — `col_target`/`col_dd_id`/`col_label`/`col_seq`/`col_visible`/`col_type`/`col_filter`/`col_key` — `ly_dlg_col` has
  no `col_filter`, so it's aliased NULL; these rows feed both `migrate_column_hints` and `migrate_key_columns`) (SELECT-only; a missing
  table on an old v1 schema → `[]` *with a logged warning* — not silently swallowed; `make_engine(url)`
  accepts any async URL — `postgresql+asyncpg://…`).
- `liberty/menus/` — `config.py`: the `config/menus.toml` schema (`MenuItem`/`AppMenu`/`MenusFile`,
  `extra="forbid"`; validates unique ids, parents exist & don't cycle, folder-vs-leaf shape),
  `load_menus`/`parse_menus`, `build_menu_tree(app_menu, *, app, language, keep)` → nested dicts
  (labels resolved in *language*, `keep(item, connector)` prunes leaves, empty folders collapse).
- `liberty/migrate_cli.py` (`liberty-migrate` script) — `sql | api | all | dictionary | menu`,
  `--source-url <v1-db-url>`, `--dbtype`, `--prefix`, `-o out.toml` (else stdout); `sql`/`all`
  also scaffold the `ly_applications` pools + carry over the `ly_tbl_col`/`ly_dlg_col` column
  hints + the `ly_tables`/`ly_dlg_frm` screen labels & auto-load flags (the hints reference the
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
- `APIConnector` decrypts `ENC:` `auth_username`/`auth_password`/`auth_token` at init via
  the `master_key` threaded through `load_connectors(master_key=…)` /
  `ConnectorRegistry(master_key=…)` (from `settings.crypto.master_key` in `main.py`'s
  lifespan and `POST /admin/reload`). Best-effort: a wrong/missing key → the value is left
  as the `ENC:` blob and a warning is logged (the connector still loads). Plaintext values
  pass through untouched. `describe()` still never exposes credentials.
- `liberty/crypto_cli.py` (`liberty-crypto` script) — `encrypt <v>` / `decrypt <ENC:…>` /
  `is-encrypted <v>` (exit 0/1); `--master-key` / `--config` overrides; reads stdin when no
  value arg; key comes from `[crypto] master_key` otherwise. For poking at values / scripting.
- v1's *other* crypto (the Fernet wrapper around `secrets.json` → `secrets.json.enc`) is
  **not** ported — v2 takes the `MASTER_KEY` straight from an env var. Only the field-level
  `ENC:` scheme above is shared.
- Operator runbook (when you need the key, how to set it, `liberty-crypto` recipes):
  `docs/crypto.md`. (The `admin` user from `liberty-admin init-db` is Argon2id, *not* `ENC:` —
  unaffected by the master key.)

267 tests pass.

## Run it

```bash
.venv/bin/pytest -v               # tests
./start.sh init-db                # FIRST RUN: create the auth tables + an `admin` user (prints the password)
./start.sh                        # builds frontend/dist if stale, then runs FastAPI serving the SPA + API on :8000
./start.sh dev                    # same, with --reload   ·   ./start.sh frontend → Vite :5173 (HMR)   ·   ./start.sh help
# by hand: .venv/bin/fastapi dev liberty/main.py   |   .venv/bin/uvicorn liberty.main:app --reload   |   .venv/bin/liberty-v2
.venv/bin/liberty-connectors list # poke at config/connectors.toml without the web layer
.venv/bin/liberty-migrate all --source-url postgresql+asyncpg://…/libnsx1 -o migrated.toml   # v1 ly_* → connectors.toml fragment
.venv/bin/liberty-migrate dictionary --source-url postgresql+asyncpg://…/libnsx1 -o config/dictionary.toml   # v1 ly_dictionary → shared field labels
.venv/bin/liberty-migrate menu --source-url postgresql+asyncpg://…/libnsx1 --connector nomasx1 -o config/menus.toml   # v1 ly_menus → app nav tree
.venv/bin/liberty-crypto encrypt 'secret' --master-key "$LIBERTY_MASTER_KEY"   # v1-compatible ENC:… (decrypt / is-encrypted too)
(cd frontend && npm install && npm run build)   # → frontend/dist (the backend serves it at /; no copy step)
# HTTP: GET /api/connectors  ·  GET/POST /api/sql/{c}/{q}  ·  POST /api/http/{c}/{e}  ·  GET /api/menus  ·  /docs (OpenAPI)
# AI: set ANTHROPIC_API_KEY, then POST /ai/chat (SSE) with an `ai:chat`-permitted token
# fresh checkout: python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

`start.sh` (repo root): `serve` (default) | `dev` | `api [dev]` | `build` | `frontend` |
`init-db` | `help`. `fastapi-cli` is a dependency, so `fastapi dev liberty/main.py` works too.

**Pools / DB / secrets:** `config/connectors.toml` is the *deployment* config (it ships with real
examples — the migrated **nomasx1** app (Postgres) plus the **NOMAJDE** app, whose v1 DB spans
three v2 connectors: `jdedwards` (the Oracle JDE business DB), `nomajde` (its Postgres app DB),
`session` (a stub) — and the `ais_connection` API connector. `config/dictionary.toml` carries
nomasx1's fields nested under `[connectors.nomasx1.*]` and NOMAJDE's at the top level (shared);
`config/menus.toml` has `[menus.nomasx1]` + `[menus.nomajde]` — the latter's leaves spell out
`connector = "jdedwards"` where the screen runs against that connector). Convention: `[pools.default]`
is the **framework pool** — it holds v2's own `ly2_users`/`ly2_roles`/`ly2_user_roles`
(created by `liberty-admin init-db`), shared across every app; `[auth] pool` (in
`config/app.toml`) points here. Per-*app* pools (`[pools.nomasx1]`, `[pools.jdedwards]`, `[pools.nomajde]`,
…) carry that app's migrated queries against its business DB; mirrors the v1 split between an
app's "definition DB" (queries/users/roles → now TOML + `ly2_*`) and its "data DB" (`pg_dump`
that into the target). `[pools.default]` defaults to `${LIBERTY_DB_URL:-sqlite+aiosqlite:///./liberty.db}`
(set `LIBERTY_DB_URL` for Postgres; SQLite `liberty.db` is gitignored). `substitute_env`
supports `${NAME}` and `${NAME:-default}` (shell `:-` = unset *or* empty → default), in both
`connectors.toml` and `app.toml`. An empty pool URL raises `UnknownPoolError`, and any
`ConnectorError` not caught per-route (e.g. an unconfigured DB on `/auth/login`) becomes a clean
**503** via a global handler in `liberty/main.py`. `LIBERTY_JWT_SECRET` empty → ephemeral key + a warning.
**Encrypted fields** (`ENC:…` values from v1 — e.g. a migrated API connector's `auth_password`,
or columns the user's other scripts touch) are decrypted at runtime with `[crypto] master_key`
(`= "${LIBERTY_MASTER_KEY}"` in `app.toml`) — same AES-256-GCM scheme and key as v1; set
`LIBERTY_MASTER_KEY` to your v1 `MASTER_KEY`. See *Crypto* above; `liberty-crypto` is the CLI.

## Layout

```
config/         app.toml, connectors.toml, dictionary.toml (shared field labels/types), menus.toml (app nav)
liberty/        main.py, config.py, crypto.py, cli.py, admin_cli.py, migrate_cli.py, crypto_cli.py
                · connectors/{config,base,db,sql,api,registry,dictionary}.py
                · menus/config.py · auth/{models,password,tokens,db,principal,service,oidc,dependencies,routes}.py
                · ai/{tools,connector_tools,assistant,routes}.py
                · web/{deps,errors,connectors,menus,admin}.py
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
