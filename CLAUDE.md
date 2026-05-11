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
connector + pool names. 42 tests pass.

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

148 tests pass. Deps used: `anthropic` (already pinned), `itsdangerous` (auth's
`SessionMiddleware`), `aiosqlite` (dev). Note: the `claude-api` skill (`/claude-api`)
holds the live Anthropic-SDK guidance — re-consult it before changing the AI module
or bumping the model. **Use `claude-opus-4-7` unless the user names another model.**

## Run it

```bash
.venv/bin/pytest -v               # tests
.venv/bin/liberty-v2              # dev server on :8000  (or: .venv/bin/uvicorn liberty.main:app --reload)
.venv/bin/liberty-connectors list # poke at config/connectors.toml without the web layer
.venv/bin/liberty-admin init-db   # create auth tables + bootstrap admin (needs [auth] pool reachable)
# AI: set ANTHROPIC_API_KEY, then POST /ai/chat (SSE) with an `ai:chat`-permitted token
# fresh checkout: python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

## Layout

```
config/         app.toml, connectors.toml
liberty/        main.py, config.py, cli.py, admin_cli.py
                · connectors/{config,base,db,sql,api,registry}.py
                · auth/{models,password,tokens,db,principal,service,oidc,dependencies,routes}.py
                · ai/{tools,connector_tools,assistant,routes}.py
                · web/ migrations/ (added per phase)
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
