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
| Frontend | **Fresh React 19 + Vite + TS inside v2**, embedded as static. No shared libraries. |
| Location | **Sibling directory** `../liberty-v2/`. |
| Config format | **TOML** files on disk (nomaubl uses flat XML properties — the flat-config insight stands, TOML is the Python-idiomatic form). |
| AI | **Anthropic SDK**, drop OpenAI. Port nomaubl's `AiAssistant.java` tool-use loop. |
| Auth | Internal users (**argon2id**) + **OIDC via authlib** (Keycloak-ready); own JWTs (HS256). |
| Users storage | **Dedicated ORM table** (`ly2_users`/`ly2_roles`) created via `liberty-admin init-db`, *not* SQLConnector queries — auth is infra, not a configurable screen; users are app data so the "no metadata tables" rule still holds. |

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

### Phase 2 — Auth + AI — 🚧 IN PROGRESS

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

**2b — AI (Anthropic tool-use loop) — ⏳ NEXT.**
- AI module: Anthropic SDK, tool-use loop ported from nomaubl `AiAssistant.java`
  — tools = decorated Python functions w/ type hints (JSON schema derived from the
  signature); the SQL/API connectors are the first tools to expose; SSE token
  streaming; max-iteration cap; optional `web_fetch` restricted to allowlisted
  domains. Gate the chat endpoint behind `require_permission("ai:chat")` (or similar).

### Phase 3 — Web layer — (~2 wks)
- `GET/POST /api/sql/{connector}/query/{name}` — params in querystring/body.
- `POST /api/api/{connector}/call/{endpoint}`.
- SSE for AI streaming and long-running queries.
- OpenAPI auto-doc replaces v1's hand-rolled "get screen metadata" endpoint.
- WebSocket if needed for live updates (v1 uses Socket.IO; evaluate vs SSE).

### Phase 4 — Frontend — (~3–4 wks)
- Fresh `frontend/` — React 19 + Vite + TS. Built `dist/` served as static by
  FastAPI (mounted in `liberty/main.py`).
- Login screen: internal form + "Sign in with OIDC" button.
- Generic components driven by runtime-discovered schema:
  - `<TableView connector queryName params>` — fetches, renders columns from the
    response metadata, sorting/paging/filtering client-side or server-side.
  - `<FormView connector queryName>` — CRUD against a connector's GET/POST/PUT/DELETE.
  - `<Lookup connector queryName>` — dropdown backed by a SQL query.
  - Settings UI: CRUD the connector TOML configs (write-back + hot-reload).
- i18n (react-i18next), Monaco editor for SQL editing (like nomaubl).

### Phase 5 — Migration tools — (~4–6 wks)
- Read v1's `ly_qry_sql` → emit SQLConnector TOML.
- Read v1's `ly_api` + `ly_api_conn` → emit APIConnector TOML.
- Map `ly_tbl_col`/`ly_dlg_col` labels → optional UI hint config (column titles,
  visibility) — but the *schema* comes from the query, not these tables.
- Validate by running nomasx1's read paths against v2 and diffing results.
- Migrate nomasx1 first (read-heavy, lower risk), then NOMAJDE, then AIRFLOW.

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
- WebSocket vs SSE for live updates (v1 = Socket.IO).
- Secrets handling — settled on the **env-var** path: `${ENV_VAR}` references in
  `connectors.toml` *and* `app.toml`, substituted at load time (unset → empty
  string). v1's Fernet + `secrets.json` not ported; revisit a vault only if ops asks.
- Token revocation — refresh tokens are stateless (no denylist / rotation), so a
  leaked refresh token is good until expiry. Add a `jti` denylist (or per-user
  token version) if/when that matters; for now keep TTLs short.
- Hot-reload trigger — `ConnectorRegistry` is rebuildable from a fresh
  `ConnectorsFile`, but nothing watches the file or exposes a reload endpoint yet
  (wire up in Phase 3 web layer / Phase 4 settings UI).
- DB migrations — auth tables are created via `create_all` (`liberty-admin init-db`),
  no Alembic. Fine while the schema is small; add Alembic before the schema churns.
- Reporting/PDF — v1 has Excel export (`tbl_workbook`/`tbl_sheet`), nomaubl has
  XSLT→PDF via BI Publisher. Out of scope until a user asks.
- JDE Julian date conversion — only if v2 needs to talk to JD Edwards data
  directly (NOMAJDE migration). Port from nomaubl `DynamicResultMapper`.

## 6. How to pick up the work

1. Read `CLAUDE.md` (project root) — it has the current status + run commands.
2. Read this file for the full picture.
3. Done: Phase 0, Phase 1 (connectors), Phase 2a (auth — internal users + OIDC).
   Next is **Phase 2b — the Anthropic tool-use loop** (`liberty/ai/`): port
   nomaubl `AiAssistant.java`. AI tools = plain decorated Python functions whose
   JSON schema is derived from the type hints; the Phase 1 SQL/API connectors are
   the obvious first tools to expose. SSE streaming, max-iteration cap, allowlisted
   `web_fetch`. Gate the chat endpoint with `require_permission(...)` from
   `liberty.auth.dependencies`. After that: Phase 3 (web layer — the connector
   HTTP routes, themselves guarded by the auth deps).
