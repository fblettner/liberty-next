# Liberty Next

Connector-driven rewrite of the Liberty low-code framework. Replaces v1's
metadata-table model (`ly_*` tables holding SQL + form layouts) with **SQL / API /
DB connectors** defined in TOML — schema is discovered at query time, not stored —
plus token/OIDC auth, an Anthropic tool-use assistant, schema-driven config
builders, and a React admin UI.

> v1 (`../liberty-framework/`) stays in production until the migration tools have
> moved every screen across. Do not modify v1 source.

## Quick start

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -v                       # the test suite (335+ tests)

./start.sh init-config                    # copy config/*.toml.example → real (uncommitted) files
./start.sh init-db                        # FIRST RUN: create the auth store + an `admin` user (prints the password)
./start.sh                                # builds frontend/dist if stale, then runs FastAPI: SPA at / and API at /api/… on :8000
./start.sh dev                            # same, with auto-reload (backend)
./start.sh frontend                       # Vite dev server on :5173 (HMR) — pair with `./start.sh api dev`
./start.sh help                           # all commands

# or, by hand:
.venv/bin/fastapi dev liberty/main.py     # FastAPI w/ reload (serves frontend/dist if it exists)
.venv/bin/uvicorn liberty.main:app        # plain uvicorn
.venv/bin/liberty-connectors list         # poke at config/connectors.toml without the web layer
(cd frontend && npm install && npm run build)   # → frontend/dist (then the backend serves it)
```

The backend serves the built frontend (`frontend/dist`) at `/` automatically — no
copy step. Set `[app] static_dir` in `config/app.toml` to serve it from elsewhere; if
the directory doesn't exist (e.g. a fresh checkout with no `npm run build`), the app
runs API-only.

Out of the box the `default` DB pool is a local SQLite file (`liberty.db`, gitignored);
set `LIBERTY_DB_URL` for Postgres (e.g. `postgresql+asyncpg://liberty:liberty@localhost/liberty`).
Set `LIBERTY_JWT_SECRET` (else an ephemeral key is generated each start),
`LIBERTY_MASTER_KEY` (for v1-compatible field-level decryption — must match v1's
`MASTER_KEY`), `LIBERTY_LICENSE_KEY` (to unlock licensed connectors), and
`ANTHROPIC_API_KEY` (to enable the assistant). Config files support `${NAME}` and
`${NAME:-default}` references. See `config/app.toml` and `config/*.toml.example`.

## Migrate from v1

```bash
.venv/bin/liberty-migrate all        --source-url postgresql+asyncpg://…/libnsx1 -o migrated.toml          # ly_query / ly_qry_sql / ly_applications → connectors.toml fragment
.venv/bin/liberty-migrate dictionary --source-url postgresql+asyncpg://…/libnsx1 --connector myapp -o config/dictionary.toml
.venv/bin/liberty-migrate menu       --source-url postgresql+asyncpg://…/libnsx1 --connector myapp -o config/menus.toml
.venv/bin/liberty-migrate screen     --source-url postgresql+asyncpg://…/libnsx1 --connector myapp -o config/screens.toml
```

Read-only on the v1 DB (only `SELECT`s). Each command prepends a `# migrated: …`
summary describing what was emitted; review, then merge into `config/`.

## Status

* **Phases 0–4 done** — project foundation, connector core (SQL/API/pool registry +
  shared dictionary), auth (TOML or DB backend, JWT, OIDC), AI tool-use assistant,
  web layer (`/api/*`, `/admin/*`, OpenAPI at `/docs`), and the React frontend (DM
  Sans + emotion + react-i18next, TanStack Table grid, lazy-loaded routes).
* **Phase 5 (migration tools) done** — `liberty-migrate sql | api | all |
  dictionary | menu | screen`. Live data verified against nomasx1 + NOMAJDE.
* **Phase 6 (form/screen engine) in progress** — slice 1 (Screen + ParamBind +
  migration) done, slice 2 (dialog runtime: row click → modal form, lookup
  param-binds, save → update/insert) done. Slices 3–6 (per-field conditions,
  actions/events, AUD audit, row menus) ahead.
* **Phase 7 (schema-driven config builders)** — Pools / Connectors / Dictionary /
  Menus / Screens builders shipped, raw `connectors.toml` Monaco editor as the
  escape hatch. Schema-form drives every input from the Pydantic models.

Full plan + design decisions: [`docs/PLAN.md`](docs/PLAN.md). Working with
Claude Code? See [`CLAUDE.md`](CLAUDE.md).

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 async · asyncpg (PostgreSQL) + oracledb
(Oracle, thin) · Anthropic SDK · authlib (OIDC) · argon2 · cryptography (AES-256-GCM,
v1-compatible) · React 19 + Vite + TypeScript + emotion · TanStack Table · Monaco.

## Layout

```
config/      app.toml (committed) · {connectors,dictionary,menus,screens,auth}.toml (NOT committed — per-deployment)
liberty/     main.py · config.py · crypto.py · {cli,admin_cli,migrate_cli,crypto_cli,license_cli}.py
             · connectors/{config,base,db,sql,api,registry,dictionary}.py
             · licensing/{__init__.py, public.pem}
             · menus/config.py · screens/config.py
             · auth/{authstore,password,tokens,principal,oidc,dependencies,routes,models,db,service}.py
             · ai/{tools,connector_tools,assistant,routes}.py
             · web/{deps,errors,connectors,menus,screens,license,admin}.py
             · migrations/{v1,source}.py
frontend/    Vite + React 19 + TS — built dist/ served by the backend (gitignored)
             src/{api,auth,workspace,types,services,common,pages,components,locales}/*
start.sh     run/dev helper (serve | dev | api | build | frontend | init-db | init-config | help)
tests/       335+ tests
docs/PLAN.md full phased plan + design decisions
```

## License

Open framework: free. Connectors flagged `licensed = true` in `connectors.toml`
(sold separately, distributed in their own repos) are unlocked by an RS256 JWT
license key set via `LIBERTY_LICENSE_KEY`. Without a key the framework runs in
"restricted" mode and those connectors aren't loaded. Inspect a key with
`liberty-license verify`; status at `GET /api/license`.
