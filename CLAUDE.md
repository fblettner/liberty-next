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
  substitution at load time.
- `base.py` — connector exceptions; `detect_statement_type` + `find_bind_params`
  (SQL text scanner skipping literals/comments/`::` casts); `ALLOWED_STATEMENTS`
  / `WRITE_STATEMENTS`.
- `db.py` — `PoolRegistry`: one SQLAlchemy async engine per named pool, created
  lazily (unreachable DB never blocks startup; tests inject their own engine).
- `sql.py` — `SQLConnector`: named queries, `:param` binding via SQLAlchemy
  `text()` (never string-substituted), statement-type allow-list, `writable`
  gate for mutations, any `:name` the caller omits → SQL NULL, runtime schema
  from `result.keys()` + best-effort `cursor.description` types, `max_rows` cap.
  (JDE Julian date/time conversion from nomaubl `DynamicResultMapper`: deferred
  to Phase 5, only if NOMAJDE migration needs it.)
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

178 tests pass.

## Run it

```bash
.venv/bin/pytest -v               # tests
./start.sh init-db                # FIRST RUN: create the auth tables + an `admin` user (prints the password)
./start.sh                        # builds frontend/dist if stale, then runs FastAPI serving the SPA + API on :8000
./start.sh dev                    # same, with --reload   ·   ./start.sh frontend → Vite :5173 (HMR)   ·   ./start.sh help
# by hand: .venv/bin/fastapi dev liberty/main.py   |   .venv/bin/uvicorn liberty.main:app --reload   |   .venv/bin/liberty-v2
.venv/bin/liberty-connectors list # poke at config/connectors.toml without the web layer
(cd frontend && npm install && npm run build)   # → frontend/dist (the backend serves it at /; no copy step)
# HTTP: GET /api/connectors  ·  GET/POST /api/sql/{c}/{q}  ·  POST /api/http/{c}/{e}  ·  /docs (OpenAPI)
# AI: set ANTHROPIC_API_KEY, then POST /ai/chat (SSE) with an `ai:chat`-permitted token
# fresh checkout: python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

`start.sh` (repo root): `serve` (default) | `dev` | `api [dev]` | `build` | `frontend` |
`init-db` | `help`. `fastapi-cli` is a dependency, so `fastapi dev liberty/main.py` works too.

**DB / secrets:** `config/connectors.toml`'s `[pools.default]` is
`${LIBERTY_DB_URL:-sqlite+aiosqlite:///./liberty.db}` — set `LIBERTY_DB_URL` for Postgres,
else it uses a local `liberty.db` (gitignored). `substitute_env` supports
`${NAME}` and `${NAME:-default}` (shell `:-` = unset *or* empty → default), in both
`connectors.toml` and `app.toml`. An empty pool URL raises `UnknownPoolError`, and any
`ConnectorError` that isn't caught per-route (e.g. an unconfigured DB on `/auth/login`)
becomes a clean **503** via a global exception handler in `liberty/main.py`. `LIBERTY_JWT_SECRET`
empty → ephemeral key + a warning (fine for dev; set it for prod).

## Layout

```
config/         app.toml, connectors.toml
liberty/        main.py, config.py, cli.py, admin_cli.py
                · connectors/{config,base,db,sql,api,registry}.py
                · auth/{models,password,tokens,db,principal,service,oidc,dependencies,routes}.py
                · ai/{tools,connector_tools,assistant,routes}.py
                · web/{deps,errors,connectors,admin}.py
                · migrations/ (Phase 5)
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
