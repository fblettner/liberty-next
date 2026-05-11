# Liberty v2

Connector-driven rewrite of the Liberty low-code framework. Replaces v1's
metadata-table model (`ly_*` tables holding SQL + form layouts) with **SQL / API /
DB connectors** defined in TOML — schema is discovered at query time, not stored —
plus token/OIDC auth, an Anthropic tool-use assistant, and a React admin UI.

> v1 (`../liberty-framework/`) stays in production until v2's migration tools land.
> Do not modify v1 source.

## Quick start

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -v                       # the test suite

# one process — FastAPI serving the React SPA at / and the API at /api/…
./start.sh                                # builds frontend/dist if stale, then serves on :8000
./start.sh dev                            # same, with auto-reload (backend)
./start.sh frontend                       # Vite dev server on :5173 (HMR) — pair with `./start.sh api dev`
./start.sh init-db                        # bootstrap the auth tables + an `admin` user (needs a DB)
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
runs API-only. Set `ANTHROPIC_API_KEY` to enable the assistant; `LIBERTY_DB_URL` /
`LIBERTY_JWT_SECRET` for the DB pool and JWT signing (see `config/app.toml` / `config/connectors.toml`).

## Status

Phases 0–4 done — foundation, connectors (SQL/API/DB), auth (users + JWT + OIDC),
AI tool-use assistant, web layer (`/api/*`, `/admin/*`, OpenAPI at `/docs`), and the
React frontend. Phase 5 (migration tools for the v1 `ly_*` tables) is next.
Full plan and design decisions: [`docs/PLAN.md`](docs/PLAN.md).
Working with Claude Code? See [`CLAUDE.md`](CLAUDE.md).

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 async · asyncpg · Anthropic SDK ·
authlib (OIDC/Keycloak) · argon2 · React 19 + Vite + TypeScript (frontend).

## Layout

```
config/      app.toml, connectors.toml
liberty/     main.py, config.py, cli.py, admin_cli.py · connectors/ auth/ ai/ web/ (migrations/ in Phase 5)
frontend/    Vite + React 19 + TS — built dist/ served by the backend (gitignored)
start.sh     run/dev helper
tests/
docs/        PLAN.md
```
