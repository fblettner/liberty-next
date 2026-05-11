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

Python 3.12 · FastAPI · SQLAlchemy 2.0 async · asyncpg · Anthropic SDK ·
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
  `[pools.*]` may carry an explicit `dialect`; else it's derived from the URL.
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
  `cursor.description` types, `max_rows` cap. (JDE Julian date/time conversion from nomaubl
  `DynamicResultMapper`: deferred to Phase 5, only if NOMAJDE migration needs it.)
- `api.py` — `APIConnector`: `httpx.AsyncClient`; auth `none`/`basic`/`bearer`/
  `api_key`/`oauth2` (OAuth2 = token-endpoint POST + dot-path token extraction +
  TTL cache + one refresh on 401); `{{placeholder}}` substitution in
  path/query/headers/body (built-ins `{{username}}`/`{{password}}`/`{{token}}`);
  dot-path response extraction (`data.0.id` indexes lists) via `response_field`
  and/or `response_map`; `multipart/form-data` bodies (`name=value` text parts,
  `name=@path;filename=X;contentType=Y` file parts).
- `registry.py` — `ConnectorRegistry`: builds connectors from `ConnectorsFile`,
  owns the pool registry, `aclose()` disposes engines + HTTP clients. Rebuildable
  → basis for hot-reload.
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
  to what the caller may use — **metadata only: no SQL text, no credentials, no pool**;
  `GET /api/sql/{c}/{q}` (SELECT-only, params from the query string) and
  `POST /api/sql/{c}/{q}` (any allowed statement; body `{"params": {…}}` or a flat
  `{name: value}`) execute a query → `QueryResult.to_dict()`; `POST /api/http/{c}/{e}`
  calls an API endpoint → `ApiResult.to_dict()` (returned as HTTP 200 even on upstream
  failure — inspect `success`/`status_code`/`error`). Permission strings: `sql:{c}:{q}`
  / `api:{c}:{e}` (glob-aware — `sql:liberty:*`, `sql:*`, `*`). The permission is checked
  *before* the connector is looked up, so callers can't enumerate names they lack access
  to. A mutating query needs *both* its TOML `writable = true` and the caller's perm.
- `admin.py` — `POST /admin/reload` (superuser): rebuild `ConnectorRegistry` from
  `connectors.toml`, swap `app.state.connectors`, re-point `app.state.auth_db`, dispose
  the old registry. (The AI assistant's connector tools refresh on restart, not on reload;
  in-flight requests keep the registry they started with.)
- `deps.py` — `get_connectors`, `require_permission(principal, perm)` (imperative — the
  perm string depends on path params), `public_connector` (the SQL/credential-stripped view).
- `errors.py` — `ConnectorError` → HTTP: not-found→404, statement/writable→422, other→400;
  SQLAlchemy errors during execute → 502.
- Also (added in Phase 4): `GET /admin/config/connectors` (raw `connectors.toml` text) and
  `PUT /admin/config/connectors` (validates the TOML against the schema, then writes — does
  *not* reload; call `POST /admin/reload` after). Both superuser.
OpenAPI auto-doc at `/docs` (`/openapi.json`) covers everything — replaces v1's
hand-rolled "get screen metadata" endpoint. WebSocket: not needed yet (SSE covers AI).

**Phase 4 (Frontend) — DONE.** `frontend/` — React 19 + Vite + TS, built `dist/` served
as static by the backend. (No Tailwind/MUI/i18n/Monaco yet — hand-rolled CSS, plain
textarea for config; those are TODOs.)
- `src/api.ts` — `fetch` wrapper: attaches the Bearer token, parses JSON, 401 → calls the
  registered "log out" hook; `streamSSE(path, body, onEvent)` for `POST /ai/chat`.
- `src/auth.tsx` — `AuthProvider` / `useAuth()`: login (`POST /auth/login`), token in
  `localStorage`, validates on mount via `/auth/me`, `oidcLogin()` → navigates to
  `/auth/oidc/login`, `setTokens()` for the OIDC fragment hand-off.
- `src/App.tsx` — `react-router-dom` v7; `/login`, `/oidc/callback`, and a `RequireAuth`
  `Layout` with children `/` (Connectors), `/sql/:c/:q` (TableView), `/http/:c/:e`
  (HttpRunner), `/chat` (Chat), `/settings` (Settings, superuser-only link).
- `components/`: `Connectors` (lists `GET /api/connectors`, drills to queries/endpoints),
  `TableView` (param form from the query's `params`/`bind_params`; SELECT → `GET` + a
  client-side sorted/paged table rendering columns from `result.columns`; writable → confirm
  + `POST` + affected-rows), `HttpRunner` (`POST /api/http/...` + pretty `ApiResult`),
  `Chat` (consumes the `/ai/chat` SSE — tokens + tool_call/tool_result lines), `Settings`
  (textarea over `GET/PUT /admin/config/connectors` + a Reload button), `Login` + `OidcCallback`.
- Backend wiring: `liberty/main.py` mounts a `SPAStaticFiles` (StaticFiles with index.html
  fallback for client routes) at `/` **last** (so it never shadows `/api`, `/auth`, `/ai`,
  `/admin`, `/health`, `/info`, `/docs`); only mounts if `[app] static_dir` exists (default
  `frontend/dist` — absent on a fresh checkout → API-only, which is fine). New settings:
  `[app] static_dir`, `[oidc] frontend_redirect` (when set, `/auth/oidc/callback` redirects
  there with `#access_token=…&refresh_token=…` instead of returning JSON — for SPAs).
  `/info` reports `frontend`.
- `frontend/.gitignore` excludes `node_modules/` and `dist/`; `package-lock.json` is committed.
  Dev: `cd frontend && npm install && npm run dev` (proxies the API paths to `:8000`);
  prod build: `npm run build` → `dist/` → served automatically by the backend.
  ⚠ The current UI is a deliberately-minimal hand-rolled shell — the intended look/stack is
  nomaubl's React app (emotion + dark mode, react-i18next EN/FR, lucide, @tanstack/react-table,
  Monaco, react-markdown); adopt it from `../../JavaProjects/nomaubl/src/web-react/` when polishing.

**Phase 5 (Migration tools) — IN PROGRESS.** `liberty/migrations/` + the
`liberty-migrate` CLI — turn a v1 Liberty DB's `ly_*` metadata into v2 `connectors.toml`:
- `v1.py` — pure transforms over row dicts: `slugify`; `migrate_sql_queries(ly_query rows,
  ly_qry_sql rows, dbtype=…, connector_prefix=…)` → one **SQL connector per `query_pool`**,
  one query per `(query_id, query_crud)` — the per-`query_dbtype` SQL variants become a
  `sql = { default = …, oracle = …, … }` dialect map (a single distinct statement collapses
  to a plain string; `--dbtype` keeps just one variant; ORDER BY appended for SELECTs;
  `writable=true` for INSERT/UPDATE/DELETE/MERGE; pool stubs `[pools.<name>] url =
  "${LIBERTY_DB_URL_<NAME>}"`); `migrate_api(ly_api_conn, ly_api,
  ly_api_header, ly_api_params, …)` → an **API connector per `ly_api_conn`** (`base_url=conn_url`,
  basic auth from `conn_user` + the v1 `conn_password` carried over **verbatim** — it's an
  `ENC:…` blob, and v2 decrypts it at runtime with the same key, see *Crypto* below) with
  endpoints from the `ly_api` rows; connectionless `ly_api` → a single
  `legacy_api` connector (`base_url=""`, absolute-URL paths); `merge_connectors(*)`;
  `render_toml(d)` (via `tomli-w`). The `# migrated: …` header notes any `ENC:` secrets it
  carried over (set `LIBERTY_MASTER_KEY` to v1's `MASTER_KEY`).
- `source.py` — async `read_sql_queries(engine)` / `read_api(engine)` (SELECT-only;
  `make_engine(url)` accepts any async URL — `postgresql+asyncpg://…` for a real v1 DB).
- `liberty/migrate_cli.py` (`liberty-migrate` script) — `sql | api | all`,
  `--source-url <v1-db-url>`, `--dbtype`, `--prefix`, `-o out.toml` (else stdout); prepends a
  `# migrated: …` summary + the `${…}` placeholders the operator must fill in.
v1 (`../liberty-framework/`) is **read-only** — the readers only SELECT. The output is a
fragment to review + merge into `config/connectors.toml`. *Not yet done:* `ly_tbl_col` /
`ly_dlg_col` UI-hint mapping (needs a v2 column-hints concept first); validate-by-diff against
nomasx1's read paths; migrate the real apps (nomasx1 → NOMAJDE → AIRFLOW). Deps: `tomli-w`.

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
- The master key lives in `[crypto] master_key` in `config/app.toml`
  (`= "${LIBERTY_MASTER_KEY}"`; v1's stock default is `"3zTvzr3p67VC61jmV54rIYu1545x4TlY"`,
  but use whatever your v1 `secrets.json` `MASTER_KEY` actually is). `liberty/config.py` →
  `Settings.crypto.master_key`; `/info` reports `crypto.configured` (bool, never the key).
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

229 tests pass.

## Run it

```bash
.venv/bin/pytest -v               # tests
./start.sh init-db                # FIRST RUN: create the auth tables + an `admin` user (prints the password)
./start.sh                        # builds frontend/dist if stale, then runs FastAPI serving the SPA + API on :8000
./start.sh dev                    # same, with --reload   ·   ./start.sh frontend → Vite :5173 (HMR)   ·   ./start.sh help
# by hand: .venv/bin/fastapi dev liberty/main.py   |   .venv/bin/uvicorn liberty.main:app --reload   |   .venv/bin/liberty-v2
.venv/bin/liberty-connectors list # poke at config/connectors.toml without the web layer
.venv/bin/liberty-migrate all --source-url postgresql+asyncpg://…/liberty -o migrated.toml   # v1 ly_* → v2 TOML
.venv/bin/liberty-crypto encrypt 'secret' --master-key "$LIBERTY_MASTER_KEY"   # v1-compatible ENC:… (decrypt / is-encrypted too)
(cd frontend && npm install && npm run build)   # → frontend/dist (the backend serves it at /; no copy step)
# HTTP: GET /api/connectors  ·  GET/POST /api/sql/{c}/{q}  ·  POST /api/http/{c}/{e}  ·  /docs (OpenAPI)
# AI: set ANTHROPIC_API_KEY, then POST /ai/chat (SSE) with an `ai:chat`-permitted token
# fresh checkout: python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

`start.sh` (repo root): `serve` (default) | `dev` | `api [dev]` | `build` | `frontend` |
`init-db` | `help`. `fastapi-cli` is a dependency, so `fastapi dev liberty/main.py` works too.

**Pools / DB / secrets:** `config/connectors.toml` is the *deployment* config (it ships
with a real example — currently the migrated **nomasx1** app). Convention: `[pools.default]`
is the **framework pool** — it holds v2's own `ly2_users`/`ly2_roles`/`ly2_user_roles`
(created by `liberty-admin init-db`), shared across every app; `[auth] pool` (in
`config/app.toml`) points here. Per-*app* pools (`[pools.nomasx1]`, future `[pools.nomajde]`,
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
config/         app.toml, connectors.toml
liberty/        main.py, config.py, crypto.py, cli.py, admin_cli.py, migrate_cli.py, crypto_cli.py
                · connectors/{config,base,db,sql,api,registry}.py
                · auth/{models,password,tokens,db,principal,service,oidc,dependencies,routes}.py
                · ai/{tools,connector_tools,assistant,routes}.py
                · web/{deps,errors,connectors,admin}.py
                · migrations/{v1,source}.py
frontend/       Vite + React 19 + TS — src/{api,auth,types,App,main}.tsx + src/components/*.tsx
                (built dist/ served by liberty/main.py; gitignored)
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
