# Liberty v2 — Rebuild Plan

> Living document. Update the **Status** markers as phases complete.

## 1. Why a rebuild

Liberty v1 (`../liberty-framework/`) is a metadata-driven low-code framework:
~50 `ly_*` tables (`ly_query`, `ly_qry_sql`, `ly_dlg_frm`, `ly_dlg_tab`,
`ly_dlg_col`, `ly_dlg_filters`, `ly_tbl_col`, `ly_actions`, `ly_act_tasks`,
`ly_cdn_params`, `ly_lookup`, `ly_menus`, …) hold raw SQL and form layouts that
the backend re-traverses on every request to generate screens.

Concrete problems:

- **Adding a screen = coordinated inserts across 5+ tables** (`ly_dlg_frm` +
  `ly_dlg_tab` + `ly_dlg_col` + `ly_dlg_filters` + `ly_qry_sql` …). High friction.
- **No caching.** Every request walks `ly_qry_fmw → ly_tbl_col → dictionary
  tables`. Lookups are the main perf bottleneck.
- **All logic lives in SQL strings in the DB** — hard to read, debug, version.
- **Limited story for complex forms** — field rules, custom actions are bolted on
  via `ly_actions`/`ly_act_tasks`/`ly_cdn_params`/`ly_act_branch` (a mini workflow
  DSL inside the DB). Awkward.
- AI is hardcoded to OpenAI; user wants Anthropic.
- The React frontend depends on shared libraries that are painful to maintain;
  user wants it back inside the project.

## 2. The model we're moving to

Borrowed from **nomaubl** (`../../JavaProjects/nomaubl`, Java e-invoicing app):
**configuration drives discovery, not code drives configuration.**

Instead of storing form/column metadata in tables, store **executable queries and
endpoint definitions** in config files, and discover the result schema at runtime
(`cursor.description`). Add a column to a SQL query → it appears in the API
response and the UI, with zero schema-table edits.

Three connector types:

| Connector | Role | nomaubl analogue |
|---|---|---|
| **SQLConnector** | Named SQL queries, `:param` binding, parameterised, `writable` gate for mutations, runtime schema discovery | `SqlConnectorClient.java` + `DynamicResultMapper.java` |
| **APIConnector** | Generic HTTP client: pluggable auth (NONE / BASIC / BEARER / API_KEY / OAUTH2), `{{placeholder}}` substitution, JSON-path response extraction, token cache w/ auto-refresh on 401 | `ApiConnectorClient.java` |
| **DBConnector** | Pool registry — multiple databases (v1's `apps_pool` multi-tenant concept) | nomaubl `DatabaseDialect` |

Connector configs live in `config/connectors.toml` (and friends), hot-reloadable.

## 3. Decisions taken (with the user)

| Question | Decision |
|---|---|
| Rewrite vs in-place refactor | **Greenfield v2.** The metadata-table model *is* the disease; refactoring in place keeps it. |
| Existing apps (nomasx1, NOMAJDE, AIRFLOW) | **Must keep running on v1.** v2 ships migration tools (Phase 5); apps move one at a time. v1 source untouched. |
| Language | **Python** — keep the Liberty stack (FastAPI + SQLAlchemy async). Port nomaubl's *patterns*, not its Java. |
| Frontend | **Fresh React 19 + Vite + TS** in `frontend/`, built `dist/` served as static by FastAPI (`SPAStaticFiles` mounted at `/` last). No shared libraries with v1/nomaubl, but the *look* is ported from nomaubl: `@emotion/styled`, a dark default + light theme (CSS-var swap), `react-i18next` (EN/FR), `lucide-react` icons, DM Sans; shared primitives in `src/ui.tsx`. `react-router-dom` v7; `fetch` (no axios); Context for auth/state. Still post-MVP: Monaco config editor, `@tanstack/react-table`, `react-markdown`. |
| Location | **Sibling directory** `../liberty-v2/`. |
| Config format | **TOML** files on disk (nomaubl uses flat XML properties — the flat-config insight stands, TOML is the Python-idiomatic form). |
| AI | **Anthropic SDK** (`AsyncAnthropic`), drop OpenAI. Default model `claude-opus-4-7` (operator-overridable). Own `@tool` decorator + manual streaming loop — the SDK's `@beta_tool`/`tool_runner` returns complete messages, can't per-token stream (the SSE endpoint needs it). The `claude-api` skill is the source of SDK truth. |
| Auth | Internal users (**argon2id**) + **OIDC via authlib** (Keycloak-ready); own JWTs (HS256). |
| Users storage | **Dedicated ORM table** (`ly2_users`/`ly2_roles`) created via `liberty-admin init-db`, *not* SQLConnector queries — auth is infra, not a configurable screen; users are app data so the "no metadata tables" rule still holds. |
| Multi-DB queries | Queries must run on **Postgres and Oracle (and more later)** — like v1's per-`dbtype` SQL. v2: a `QueryDef.sql` can be a `{ default, oracle, postgresql, … }` map keyed by SQLAlchemy backend name; the connector picks the variant matching its pool's database (→ `default`). The v1 `query_dbtype` variants migrate to that shape; portable SQL stays a plain string. |
| Pool topology | A **`default`/framework pool** holds v2's own users/roles (`ly2_*`), shared across every app. Per-*app* pools (`nomasx1`, `nomajde`, …) carry that app's migrated queries against its business DB — mirrors v1's split between an app's "definition DB" (queries/users → now TOML + `ly2_*`) and its "data DB". |
| Field encryption | **Reuse v1's scheme and key, byte for byte** — `liberty/crypto.py` is AES-256-GCM + PBKDF2-HMAC-SHA512(2145 iters, 32 bytes) with the `"ENC:" + base64(salt[64]‖iv[16]‖tag[16]‖ct)` layout, so v2 reads/writes the same encrypted columns the user's *other* scripts touch (`SETTINGS_APPLICATIONS.password`, …) without re-encrypting the DB. Key = `[crypto] master_key` (`${LIBERTY_MASTER_KEY}` = v1's `MASTER_KEY`). `APIConnector` decrypts `ENC:` auth secrets at runtime; `liberty-crypto` is the CLI. v1's Fernet/`secrets.json` plumbing is **not** ported — the key comes straight from an env var. |
| Column display | The result **schema stays query-discovered** (no metadata tables) — but a query may carry an optional `columns` *overlay* (`ColumnHint`: `dd` ref / label / hidden / order / width / align / a UI `format`). `label`/`format` usually come from a **shared field dictionary** — `config/dictionary.toml`, the v2 form of v1's `ly_dictionary` (define a field once: label, type, per-language translations) — keyed by the hint's `dd`, or the column `name` when `dd` is unset; an inline `label`/`format` overrides. v1's dictionaries were **per-app**; v2 mirrors that with **per-connector sections** — `[connectors.<conn>.entries.*]` is consulted first, then the top-level `[entries.*]` (a shared/common pool), so two migrated apps don't clash on a `dd_id`. The SQL connector resolves it at result time in the request's language (`X-Liberty-Lang` header → `Accept-Language` → `default_language`); TableView honours it. v1's `ly_tbl_col`/`ly_dlg_col` → the hints, `ly_dictionary`/`ly_dictionary_l` → the dictionary (`liberty-migrate dictionary --connector <app>` nests under that connector). A hint matches its result column **case-insensitively** (the DB folds unquoted identifiers — Postgres→lower / Oracle→upper — while v1's hints are upper); the emitted column keeps the discovered case so it still lines up with the row keys. A hint for a column the query doesn't return is ignored. The form-side workflow/rules in v1's `ly_dlg_*` (and v1's `dd_rules`) are Phase 6, not this overlay. |

## 4. Phased plan

### Phase 0 — Foundation — ✅ DONE
- `pyproject.toml` with the full dep set pinned; pip + venv (no uv/poetry on this machine).
- `liberty/config.py` — TOML loader, Pydantic-validated `Settings`.
- `liberty/main.py` — FastAPI app, `/health`, `/info`, `liberty-v2` CLI entry.
- `config/app.toml`, `config/connectors.toml` (placeholder).
- `tests/test_health.py` — 3 passing tests.

### Phase 1 — Connector core — ✅ DONE
The heart of v2. Delivered in `liberty/connectors/`:
1. **SQLConnector** (`sql.py`) — named queries from TOML; `:name` params bound via
   SQLAlchemy `text()` (never string-substituted); `writable` gates
   INSERT/UPDATE/DELETE/MERGE; statement-type allow-list rejects DROP/ALTER/… up
   front; any `:name` the caller omits is bound to SQL NULL (optional filters);
   result schema discovered at runtime from `result.keys()` + best-effort
   `cursor.description` types; `max_rows` cap with `truncated` flag.
   *JDE Julian date/time conversion (nomaubl `DynamicResultMapper`) deferred to
   Phase 5 — only if the NOMAJDE migration needs to read JDE data directly.*
2. **APIConnector** (`api.py`) — `httpx.AsyncClient`; auth `none`/`basic`/
   `bearer`/`api_key`/`oauth2` (token endpoint POST + dot-path token extraction +
   TTL cache + one refresh on 401); `{{placeholder}}` substitution in
   path/query/headers/body (built-ins `{{username}}`/`{{password}}`/`{{token}}`);
   dot-path response extraction (`data.0.id` indexes lists) via `response_field`
   and `response_map`; `multipart/form-data` bodies (`name=value` /
   `name=@path;filename=…;contentType=…`).
3. **PoolRegistry** (`db.py`) — one async engine per named `[pools.*]` entry,
   created lazily (unreachable DB never blocks startup; tests inject engines).
   v1's `apps_pool`/`apps_dbtype` is the model.
4. **ConnectorRegistry** (`registry.py`) owns pools + connectors and tears them
   down; **`liberty/cli.py`** (`liberty-connectors`) exposes `list` / `describe` /
   `run`; wired into `main.py` lifespan; `/info` reports loaded connectors/pools.
5. Config validated by Pydantic in `connectors/config.py`; `${ENV_VAR}` secret
   substitution at load time. 42 tests (config/SQL via sqlite/API via httpx mock/
   CLI). `aiosqlite` added as a dev dep for the SQL tests.

Config shape (as shipped — see `config/connectors.toml`):
```toml
[pools.default]
url = "${LIBERTY_DB_URL}"   # secrets referenced, never inlined

[connectors.liberty]
type = "sql"
pool = "default"
max_rows = 1000

[[connectors.liberty.queries]]
name = "users_list"
sql = "SELECT usr_id, usr_name FROM ly_users WHERE (:status IS NULL OR usr_status = :status)"
writable = false
params = [{ name = "status", default = "ENABLED" }]

[connectors.svc]
type = "api"
base_url = "https://api.example.test"
auth_type = "bearer"
auth_token = "${SVC_TOKEN}"
default_headers = { Accept = "application/json" }

[[connectors.svc.endpoints]]
name = "get_thing"
method = "GET"
path = "/things/{{id}}"
params = [{ name = "id" }]
response_field = "data.0.name"
```

### Phase 2 — Auth + AI — ✅ DONE

**2a — Auth (internal users + OIDC) — ✅ DONE.** `liberty/auth/`:
- `models.py` — SQLAlchemy 2.0 ORM `User` / `Role` / `user_roles` (`ly2_*` tables).
  Decided **with the user**: a dedicated ORM table (not SQLConnector queries) —
  auth is foundational infra, not a user-configurable screen; users are app data,
  so the "no metadata tables" rule still holds. `Role.permissions` = JSON list of
  colon-segmented strings (`"sql:liberty:read"`, `"*"` wildcard); superuser bypasses.
- `password.py` — Argon2id (`argon2-cffi`), with `needs_rehash` so logins upgrade
  stale hashes transparently.
- `tokens.py` — `TokenService`: HS256 JWTs, `access` (carries roles/perms/superuser
  so handlers need no DB hit) + `refresh` (re-reads the user → role changes
  propagate within a TTL). Secret from `[auth] jwt_secret` / `LIBERTY_JWT_SECRET`;
  empty → ephemeral key + a warning.
- `db.py` — `AuthDatabase`: lazy `async_sessionmaker` over a `PoolRegistry` pool
  (`[auth] pool`, default `default`); `create_schema()` for `liberty-admin init-db`.
- `principal.py` — `Principal` from JWT claims; `has_permission` glob match.
- `service.py` — `AuthService`: `authenticate` (+rehash), CRUD, role ops,
  `provision_oidc_user` (find-or-create by `(provider="oidc", sub)`).
- `oidc.py` — Authlib Starlette `OAuth` client from a discovery URL (Keycloak-ready);
  ID-token validation is Authlib's job; returns `None` when disabled.
- `dependencies.py` — `get_current_principal` / `optional_principal`,
  `require_permission` / `require_role` / `require_superuser`, `get_auth_service`,
  `get_oidc`.
- `routes.py` — `POST /auth/login`, `POST /auth/refresh`, `GET /auth/me`,
  `GET /auth/oidc/login` + `GET /auth/oidc/callback` (both 404 when OIDC off). Both
  login paths mint *our* JWTs; the IdP's tokens aren't propagated downstream.
- `liberty/admin_cli.py` (`liberty-admin`) — `init-db` (tables + bootstrap a
  superuser `admin`/role `admin`/perm `*`; password explicit or generated-and-
  printed), `create-user`, `set-password`, `set-active`, `list-users`, `create-role`.
- `liberty/main.py` — `create_app(settings=None)`; lifespan builds `auth_db` /
  `token_service` / `oidc` on `app.state`; `SessionMiddleware` only when OIDC on;
  includes the auth router; `/info` reports `auth.pool` + `oidc_enabled`.
- `liberty/config.py` — `[auth]` + `[oidc]` settings; `${ENV_VAR}` substitution now
  also applied to `app.toml` (`substitute_env` lives here, reused by the connectors loader).
- Deps added: `itsdangerous` (for `SessionMiddleware`). 68 new tests (110 total).
- *Not done:* refresh-token rotation/denylist (no revocation yet — short TTLs only),
  a full OIDC flow integration test (needs a fake IdP), an HTTP "admin users"
  endpoint (CLI only for now).

**2b — AI (Anthropic tool-use loop) — ✅ DONE.** `liberty/ai/`:
- `tools.py` — `@tool` decorator: a (sync/async) Python function → a `Tool` whose
  Anthropic `input_schema` is derived from the type hints, with the description +
  per-param descriptions read from a Google-style docstring `Args:` block. `ToolRegistry`
  dispatches by name, JSON-encodes results, and returns `(content, is_error)`.
  (Built our own rather than the SDK's `@beta_tool`/`tool_runner` — that runner
  returns complete messages and can't per-token stream, which the SSE endpoint needs.)
- `connector_tools.py` — the Phase 1 connectors as tools: `list_connectors`
  (discovery — descriptions stay byte-stable for prompt caching, the catalog is in
  the result), `sql_query` (**read-only** — refuses `writable` queries), `api_call`
  (off unless `[ai] api_tool` — API endpoints can mutate). An `allowed_connectors`
  list can restrict which connectors the assistant sees.
- `assistant.py` — `AiAssistant.chat(messages)` is an async generator of `ChatEvent`
  (`token`/`thinking`/`tool_call`/`tool_result`/`error`/`done`): stream via
  `AsyncAnthropic.messages.stream`, surface text deltas, execute local tools, feed
  `tool_result` back, loop until a non-`tool_use` stop reason or the `max_iterations`
  cap; `pause_turn` → re-send to resume. `system` block carries `cache_control`
  (caches the stable tool list too). Optional server-side `web_fetch_20260209`,
  restricted to `web_fetch_domains` (off unless set). Model default `claude-opus-4-7`
  (operator-overridable in config / per request); no `temperature`/`top_p` (removed
  on Opus 4.7); `thinking` (adaptive) and `effort` are config opt-ins.
  `build_assistant(settings.ai, connectors)` → `AiAssistant | None`.
- `routes.py` — `POST /ai/chat` → `StreamingResponse` of SSE; `GET /ai/tools` →
  the tool catalog + availability. Both behind `require_permission("ai:chat")`.
  AI disabled → 404; enabled but no API key → the stream's first event is `error`.
- `liberty/config.py` — `[ai]` settings; API key from `${ANTHROPIC_API_KEY}`.
- `liberty/main.py` — lifespan builds `app.state.ai`; `/info` reports `ai.{enabled,available,model}`.
- 38 new tests (148 total). The `claude-api` skill holds the live Anthropic-SDK
  guidance — re-consult it before changing this module or the model.
- *Not done:* prompt caching only covers the system+tools prefix (message-history
  caching: TODO); finer-grained per-connector permission gating beyond the chat
  endpoint's `ai:chat`; the Anthropic `web_fetch_20260209` tool version is assumed
  GA (no beta header) — surfaces as an API error if a beta header turns out to be needed.

### Phase 3 — Web layer — ✅ DONE
`liberty/web/`:
- `connectors.py` — `GET /api/connectors` (+ `/{connector}`): list connectors filtered
  to what the caller may use — **metadata only** (names/labels/params/`bind_params`/
  `writable`/`statement_type` for SQL; method/path/params for API) — never the SQL text,
  the pool, or any credential. `GET /api/sql/{c}/{q}` (SELECT-only — non-SELECT → 405; params
  from the query string) and `POST /api/sql/{c}/{q}` (any allowed statement; body
  `{"params": {…}}` or a flat `{name: value}`) → `QueryResult.to_dict()`. `POST /api/http/{c}/{e}`
  → `ApiResult.to_dict()` (HTTP 200 even on upstream failure — caller inspects `success`).
  Permission strings settled: `sql:{connector}:{query}` / `api:{connector}:{endpoint}`,
  glob-aware (`sql:liberty:*`, `sql:*`, `*`). Permission is checked **before** the connector
  lookup → no enumeration of names you lack access to. A mutating query needs *both* its
  TOML `writable = true` and the caller's permission (two orthogonal gates).
- `admin.py` — `POST /admin/reload` (superuser): rebuild `ConnectorRegistry` from
  `connectors.toml`, swap `app.state.connectors`, re-point `app.state.auth_db`, dispose the
  old registry. (The AI assistant's connector tools refresh on app restart, not on reload;
  in-flight requests keep whichever registry they started with.)
- `deps.py` — `get_connectors`, `require_permission(principal, perm)` (imperative — the perm
  string is built from path params), `public_connector` (the SQL/credential-stripped view).
- `errors.py` — `ConnectorError` → HTTP: not-found→404, statement/writable→422, other→400;
  SQLAlchemy errors during execute → 502.
- OpenAPI auto-doc at `/docs` (`/openapi.json`) replaces v1's hand-rolled "get screen
  metadata" endpoint. SSE: AI streaming already done (`/ai/chat`); "SSE for long-running
  queries" deferred (queries are fast — revisit with a job runner if a slow query appears).
  WebSocket: not needed yet — SSE covers the live-update use case for now.
- 24 new tests (172 total).
- *Not done:* `?accessible=` filter is implicit (always filtered); a `/api/sql/{c}/{q}/schema`
  shortcut (just run it and read `.columns`); a `POST /admin/reload` that also rebuilds the
  AI assistant; `/admin/reload` is fire-and-forget — no in-flight-request draining.
  *(Phase 4 added `GET/PUT /admin/config/connectors` for the settings UI.)*

### Phase 4 — Frontend — ✅ DONE
`frontend/` — React 19 + Vite + TS, built `dist/` served as static by the backend.
- `liberty/main.py` mounts a `SPAStaticFiles` (StaticFiles + index.html fallback for client
  routes) at `/` **last** — so it never shadows `/api`, `/auth`, `/ai`, `/admin`, `/health`,
  `/info`, `/docs`; only mounted if `[app] static_dir` (default `frontend/dist`) exists, so a
  fresh checkout with no frontend build runs API-only.
- New backend bits: `[app] static_dir`, `[oidc] frontend_redirect` (when set,
  `/auth/oidc/callback` redirects there with `#access_token=…&refresh_token=…` for SPAs;
  empty → returns JSON as before), `GET/PUT /admin/config/connectors` (superuser — read /
  validate-and-write `connectors.toml`; PUT does not auto-reload). `/info` reports `frontend`.
- **Look & feel** — adopted from **nomaubl's React app** (`../../JavaProjects/nomaubl/src/web-react/`):
  `@emotion/styled`, a "liquid-glass" palette, a dark default + light theme (CSS-var swap via a
  `.theme-light` class on `<html>`, preference persisted), `react-i18next` (EN/FR, persisted),
  `lucide-react` icons, DM Sans (Google Fonts), `@tanstack/react-table` (the SELECT grid),
  `react-markdown` + `remark-gfm` (assistant replies), `@monaco-editor/react` (the connector-
  config editor — Monaco is *bundled*, not CDN-loaded: `src/services/monaco.ts` imports the
  editor API + the `ini` language only, wires the worker via Vite's `?worker`, `loader.config({
  monaco })`, and is side-effect-imported from the Settings page so it stays in that lazy chunk;
  the app works offline — only the DM Sans webfont is still CDN, with a system-font fallback).
- **Source layout** — also borrowed from nomaubl, to keep the project from sprawling into giant
  files: `src/theme.ts` (tokens), `src/index.css` (the `:root`/`.theme-light` var sets + ambient
  gradient bg + thin scrollbar), `src/i18n.ts` + `src/locales/{en,fr}.ts`; `src/api/client.ts`
  (fetch wrapper + `streamSSE`); `src/auth/AuthContext.tsx`; `src/types/{connectors,auth,ai}.ts`
  (backend response shapes — no React); `src/services/` (plain-TS helpers, e.g. `cells.ts`);
  `src/common/` (shared theme-driven primitives, one file each — `Button`, `Card`, `Input`/`Field`,
  `Tag`/`Mono`, `Banner`/`Pre`, `Spinner`/`Centered`, `PageLayout`, `Modal`/`ConfirmModal`,
  `layout` `Stack`/`Row`, `useIsLight`, `Markdown`; `common/index.ts` barrels all but `Markdown`,
  which stays out so react-markdown doesn't leak into every page chunk); `src/pages/<Screen>/index.tsx`
  (one dir per page; sub-components and styled bits live alongside — e.g. `TableView/ResultTable.tsx`
  + `TableView/styled.ts`); `src/components/` (app chrome — `Layout`, `Sidebar`, `ProfileModal`,
  `WorkspaceSelect`); `src/workspace/WorkspaceContext.tsx` (`useWorkspace()` — the picked connector,
  persisted + made to follow `/sql/<c>/…` & `/http/<c>/…` routes, plus the shared `GET /api/connectors` fetch).
  *Rule for future work:* keep pages small (split helpers into `pages/<X>/`), reusable bits go in
  `common/`, plain logic/shapes go in `services/`/`types/` (no React there), and every styled
  component pulls colours/sizes/radii/shadows from `theme.ts` — no hard-coded hex/rgba/font-px.
- `src/App.tsx` — `react-router-dom` v7: `/login`, `/oidc/callback`, `RequireAuth` `Layout`
  with `/` (Connectors), `/sql/:c/:q` (TableView), `/http/:c/:e` (HttpRunner), `/chat`,
  `/settings` (superuser). The page components are `React.lazy`-split (the heavy libs ride
  along — TableView/Chat/Settings each become their own chunk; entry chunk ~112 kB gz);
  `Layout` renders `<Outlet/>` inside a `<Suspense fallback={<Centered/>}>`.
- The pages: **Layout** (shell — `Sidebar` + workspace-title header + a fixed top-right
  utility pill: app-picker (`WorkspaceSelect`, shown when ≥2 connectors — v2 auth is centralized,
  so v1's "pick an app at login" becomes "which connector's screens am I scoped to", persisted,
  pure-frontend soft filter) · EN/FR · dark/light · username→profile · sign-out), **Sidebar** (collapsible nav
  rail, lucide icons, react-router `NavLink`s + an external "API docs" link), **ProfileModal**
  (read-only — username/email/provider/roles/permissions from the Principal; no self-service
  password change yet — the backend has no endpoint), **Connectors** (the accessible connectors
  from `useWorkspace()`, scoped to the picked app — drills to queries/endpoints), **TableView** (param form from `params`/`bind_params`; SELECT →
  `GET` + a `@tanstack/react-table` grid — sortable columns, client-side paging, sticky header —
  whose columns come from `result.columns`, honouring their display hints (label/hidden/width/align);
  writable → confirm + `POST`), **HttpRunner**
  (`POST /api/http/...` + pretty `ApiResult` + JSON `Pre`), **Chat** (consumes the `/ai/chat`
  SSE — user bubbles plain, assistant bubbles via `<Markdown>`, + tool_call/tool_result lines +
  new-conversation), **Settings** (a Monaco editor — `ini` highlighting, theme follows
  dark/light — over `GET/PUT /admin/config/connectors` + Save + Reload), **Login** + **OidcCallback**.
- Dev: `cd frontend && npm i && npm run dev` (Vite :5173, proxies the API to :8000); prod:
  `npm run build` → `dist/` → served by the backend. `frontend/.gitignore` excludes
  `node_modules/` + `dist/`; `package-lock.json` is committed.
- 6 tests on the backend side (SPA static serving — index-fallback for client routes; API not
  shadowed; no-dir → not mounted —, `GET/PUT /admin/config/connectors` validate-then-write
  superuser-only, the `[oidc] frontend_redirect` setting). Frontend itself is tsc-checked at
  build time; no Vitest/RTL yet.
- *Still TODO toward full nomaubl parity:* a self-service change-password flow (needs a
  backend endpoint — ProfileModal is read-only for now); `@tanstack/react-virtual` to
  virtualise very large result grids; reusable `<FormView>`/`<Lookup>` (TableView covers
  reads + writable "runs"; HttpRunner covers API endpoints); Vitest/RTL frontend tests;
  frontend build in CI.

### Phase 5 — Migration tools — 🚧 IN PROGRESS  (~4–6 wks)
**Done so far** — `liberty/migrations/` + the `liberty-migrate` CLI, plus dialect-aware queries
in the connector model (so a migrated query that v1 had per-`dbtype` variants of works on
Postgres *and* Oracle, etc.):
- **Dialect-aware queries** (in `liberty/connectors/`): `QueryDef.sql` is a string *or* a
  `{ default = …, oracle = …, postgresql = … }` map keyed by SQLAlchemy backend name (`default`
  required). `[pools.*]` may set an explicit `dialect`; else it's derived from the URL.
  `SQLConnector` picks `qdef.sql_for(pool_dialect)` per call (falling back to `default`);
  `describe()` reports `dialects`. An empty/undefined pool URL surfaces as `503` (`UnknownPoolError`).
- `v1.py` — pure transforms over plain row dicts. `migrate_sql_queries(ly_query rows,
  ly_qry_sql rows, dbtype=…, connector_prefix=…, column_hints=…)`: groups by `query_pool` →
  **one SQL connector per pool** (+ a `[pools.<name>] url = "${LIBERTY_DB_URL_<NAME>}"` stub);
  **one `[[connectors.<pool>.queries]]` per `(query_id, query_crud)`** named `<query_label>_<crud>`
  — the per-`query_dbtype` SQL rows become a dialect map (`generic` → `default`; `postgres` →
  `postgresql`; …; identical variants collapse to a plain string; `dbtype=` keeps just one). v1's
  `query_crud` is a **REST verb**: `GET`/`SELECT` = read (gets `ORDER BY <query_orderby>` and the
  `column_hints` for its `query_id`), `POST`/`PUT`/`PATCH`/`DELETE` = write (`writable = true`).
  `migrate_api(ly_api_conn, ly_api, ly_api_header, ly_api_params, …)`:
  **one API connector per `ly_api_conn`** (`base_url = conn_url`, `auth_type=basic` + `auth_username`
  from `conn_user` + the v1 `conn_password` carried over **verbatim** — it's an `ENC:…` blob and v2
  decrypts it at runtime with `[crypto] master_key` = v1's `MASTER_KEY`; the `# migrated:` header
  flags that) with endpoints (method/path/body/headers/params) from the `ly_api` rows that point
  at it; connectionless `ly_api` rows → a single `legacy_api` connector (`base_url = ""`,
  absolute-URL paths). `migrate_pools(ly_applications rows, connector_prefix=…)`: real
  **`[pools.*]` from `ly_applications`** — one per `apps_pool`, `url` = a SQLAlchemy async URL
  built from `apps_dbtype`/`apps_host`/`apps_port`/`apps_database` (`postgresql+asyncpg://…` /
  `oracle+oracledb://…/?service_name=…`) or a parseable `apps_jdbc`, else the `${LIBERTY_DB_URL_<NAME>}`
  stub; `dialect` from `apps_dbtype`, `pool_size` from `apps_pool_max`; the DB **password is never
  inlined** — it's a `${MIGRATED_PW_<NAME>}` placeholder (v1 keeps it `ENC:`-encrypted in
  `apps_password` — set the env var, or `liberty-crypto decrypt` it). v1's reserved `default` pool
  is **skipped** — v2's `[pools.default]` is v2's own framework DB (the `ly2_*` tables).
  `migrate_column_hints(ly_tbl_col rows, ly_dlg_col rows)`: `{query_id: [ColumnHint dict]}` —
  each `col_target` → `{name, dd?` (= `col_dd_id`, only when ≠ `name`)`, label?` (only when an
  explicit `col_label` overrides the dictionary)`, hidden?` (when `col_visible` reads false)`,
  format?` (only when an explicit `col_type` overrides the dictionary)`}`; table-widget columns
  beat form-field columns, first `(query, col)` wins so the per-query list keeps `col_seq` order —
  passed to `migrate_sql_queries(column_hints=…)`, attached to each *read* query's `columns` (the
  result *schema* is still discovered at run time — these hints only augment it).
  `migrate_dictionary(ly_dictionary rows, ly_dictionary_l rows, *, default_language="en", connector_name=None)`
  → the `dictionary.toml` dict (one `[entries.<dd_id>]` per row — `label`=`dd_label`, `format`=a non-trivial
  `dd_type`, `rules`/`rules_values`/`default` verbatim, `[entries.<dd_id>.l]` = `{lng_id: lng_label}`
  from `ly_dictionary_l`); `connector_name` nests them under `[connectors.<name>.entries.*]` (v1's
  dictionaries were per-app — keeps two apps from clashing on a `dd_id`). `merge_connectors(*)` — pools
  merged with `migrate_pools` *last*, so its real URLs override the `migrate_sql_queries` stubs. `render_toml(d)`.
- `source.py` — async `read_sql_queries(engine)` / `read_api(engine)` / `read_applications(engine)` /
  `read_dictionary(engine)` (→ `ly_dictionary` + `ly_dictionary_l` rows) /
  `read_column_hints(engine)` (`ly_tbl_col`←`ly_tables`←`ly_query`, `ly_dlg_col`←`ly_dlg_frm`←`ly_query`
  — `col_target`/`col_dd_id`/`col_label`/`col_seq`/`col_visible`/`col_type`) — SELECT-only; a missing
  table on an old v1 schema → `[]` *with a logged warning* (not silently swallowed); `make_engine(url)`
  takes any async URL (`postgresql+asyncpg://…/liberty`).
- **`liberty/crypto.py`** — field-level encryption, byte-compatible with v1's `Encryption`
  (AES-256-GCM, PBKDF2-HMAC-SHA512 2145 iters / 32 bytes, `"ENC:" + base64(salt[64]‖iv[16]‖tag[16]‖ct)`).
  So migrated `ENC:` secrets work as-is, *and* the user's other scripts that read/write the same
  encrypted columns stay interoperable — nothing in the DB gets re-encrypted. Key from
  `[crypto] master_key` (`= "${LIBERTY_MASTER_KEY}"`, v1's `MASTER_KEY`); `Settings.crypto.master_key`;
  `/info` → `crypto.configured`. `APIConnector` decrypts `ENC:` `auth_username`/`auth_password`/`auth_token`
  at init (key threaded via `load_connectors(master_key=…)`; wrong/missing key → keep the blob + warn).
  `liberty-crypto` CLI (`encrypt`/`decrypt`/`is-encrypted`, stdin-friendly). v1's Fernet/`secrets.json`
  layer is *not* ported. Dep: `cryptography`.
- `liberty/migrate_cli.py` (`liberty-migrate` script) — `sql | api | all | dictionary`, `--source-url`,
  `--dbtype`, `--prefix`, `--default-language`/`--connector` (dictionary), `-o out.toml` (else stdout);
  `sql`/`all` also scaffold the `ly_applications` pools and carry over the `ly_tbl_col`/`ly_dlg_col` column
  hints (which reference the dictionary — so also run `liberty-migrate dictionary --connector <app> -o config/dictionary.toml`,
  `--connector` nesting the entries under `[connectors.<app>.entries.*]`); prepends a `# migrated: …` summary
  + the `${…}` placeholders the operator must fill in (incl. each `${MIGRATED_PW_*}`). Output is a fragment to
  review + merge into `config/connectors.toml` (the `dictionary` output → `config/dictionary.toml`). v1
  (`../liberty-framework/`) stays untouched (read-only SELECTs).
- Tests: the transforms over hand-crafted v1 rows (incl. the dialect-map cases), the DB readers
  against a minimal v1 schema in SQLite, the CLI, dialect resolution in `SQLConnector` /
  `QueryDef` / `PoolRegistry.dialect`, and round-trips (emitted TOML re-parses cleanly via
  `parse_connectors`). Deps added: `tomli-w`. **`config/connectors.toml` is now the real
  deployment config** — the migrated **nomasx1** app (208 queries) on `[pools.nomasx1]`, with
  `[pools.default]` as the shared framework/users pool.

**Still to do:**
- Validate by running nomasx1's read paths against v2 and diffing results.
- Migrate nomasx1 first (read-heavy, lower risk), then NOMAJDE, then AIRFLOW.
- (Possible later) richer column hints — lookups / format strings / per-column filters — and the
  form side (v1's `ly_dlg_*` field rules/conditions) once a v2 form concept exists; `ly_tbl_col`/
  `ly_dlg_col`'s *display* metadata is migrated, the workflow/rules part isn't (that's Phase 6).

### Phase 6 — Custom form logic — (deferred, decide after Phases 1–3)
v1 solves field rules + custom actions via `ly_actions` / `ly_act_tasks` /
`ly_cdn_params` / `ly_act_branch` — a workflow DSL stored in the DB. nomaubl has
no strong equivalent. Options to compare once real screens exist:
- **Python plugins** — hook functions registered per form/field event. Most power, needs deploys.
- **Declarative rules** (YAML/TOML) — `when field X = Y, require Z` / `disable on edit` — covers 80%, no deploy.
- **Embedded JS hooks** — like v1's `ly_function`, evaluated client-side. Flexible, harder to test.
Likely a mix: declarative rules for common cases + a Python plugin escape hatch.
**Do not design this in the abstract** — wait for Phases 1–3 to produce screens
to validate against.

## 5. Open questions / parking lot

- ~~Multipart/file upload story in APIConnector~~ — done in Phase 1 (line-based
  parts list, files read into memory; revisit streaming if a large-file PA needs it).
- WebSocket vs SSE — SSE covers live updates for now (`/ai/chat`). Add WebSocket only
  if a real bidirectional/low-latency need appears (v1 = Socket.IO).
- Secrets / config — settled on the **env-var** path: `${NAME}` and `${NAME:-default}`
  references in `connectors.toml` *and* `app.toml`, substituted at load time (`:-` =
  shell semantics: unset *or* empty → default; bare `${NAME}` unset → ""). The shipped
  `[pools.default]` is `${LIBERTY_DB_URL:-sqlite+aiosqlite:///./liberty.db}` so the app
  runs out of the box. v1's Fernet + `secrets.json` plumbing not ported (the `MASTER_KEY` is just
  an env var now); revisit a vault only if ops asks. **Field-level** `ENC:…` secrets *are* still
  honoured — `liberty/crypto.py` is byte-compatible with v1's `Encryption`, keyed by `[crypto]
  master_key` (`= "${LIBERTY_MASTER_KEY}"`); see the decisions table + Phase 5.
- Token revocation — refresh tokens are stateless (no denylist / rotation), so a
  leaked refresh token is good until expiry. Add a `jti` denylist (or per-user
  token version) if/when that matters; for now keep TTLs short.
- Hot-reload — `POST /admin/reload` rebuilds the `ConnectorRegistry` from disk and
  re-points `app.state.auth_db` (Phase 3); the frontend Settings page edits `connectors.toml`
  via `GET/PUT /admin/config/connectors` and then calls reload (Phase 4). Still missing: a
  file watcher (auto-reload on change), rebuilding the AI assistant on reload, draining
  in-flight requests before disposing the old registry, and config validation feedback in
  the editor beyond the PUT 422.
- DB migrations — auth tables are created via `create_all` (`liberty-admin init-db`),
  no Alembic. Fine while the schema is small; add Alembic before the schema churns.
- AI prompt caching — only the system+tools prefix is `cache_control`-ed; growing
  message history (incl. tool-loop turns) isn't cached. Add a moving message
  breakpoint (or top-level auto-cache) if cost on long conversations matters.
- AI permission granularity — the chat endpoint is gated by `ai:chat`; there's no
  per-connector ACL beyond `[ai] allowed_connectors` / read-only SQL. Tighten if a
  tenant needs the AI to see only a subset of connectors.
- `web_fetch_20260209` beta header — assumed GA. If Anthropic requires a beta
  header for that tool version, switch the AI loop to `client.beta.messages.stream(betas=[...])`.
- Reporting/PDF — v1 has Excel export (`tbl_workbook`/`tbl_sheet`), nomaubl has
  XSLT→PDF via BI Publisher. Out of scope until a user asks.
- JDE Julian date conversion — only if v2 needs to talk to JD Edwards data
  directly (NOMAJDE migration). Port from nomaubl `DynamicResultMapper`.

## 6. How to pick up the work

1. Read `CLAUDE.md` (project root) — it has the current status + run commands.
2. Read this file for the full picture.
3. Done: Phases 0–4; Phase 5 in progress — `liberty/migrations/` + `liberty-migrate` does the
   `ly_query`/`ly_qry_sql` → SQL-connector and `ly_api*` → API-connector TOML emission. Still
   to do in Phase 5: the `ly_tbl_col`/`ly_dlg_col` → UI-hints mapping (blocked on a v2 column-
   hints concept), validate-by-diff against nomasx1's read paths, and the actual app migrations
   (nomasx1 → NOMAJDE → AIRFLOW, one at a time — v1 stays read-only). Then **Phase 6** (custom
   form logic — defer designing until real screens exist). Alongside: polish the frontend to
   nomaubl's UI stack (see the Phase 4 *Not done* note + the `feedback_frontend_nomaubl_style`
   memory) — emotion theming/dark mode, react-i18next, Monaco, @tanstack/react-table, lucide.
