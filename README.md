# Liberty v2

Connector-driven rewrite of the Liberty low-code framework. Replaces v1's
metadata-table model (`ly_*` tables holding SQL + form layouts) with **SQL / API /
DB connectors** defined in TOML — schema is discovered at query time, not stored.

> v1 (`../liberty-framework/`) stays in production until v2's migration tools land.
> Do not modify v1 source.

## Quick start

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -v          # 3 passing tests (Phase 0)
.venv/bin/liberty-v2          # dev server → http://localhost:8000  (/health, /info, /docs)
```

## Status

**Phase 0 (Foundation): done.** Phase 1 (connector core) is next.
Full plan and design decisions: [`docs/PLAN.md`](docs/PLAN.md).
Working with Claude Code? See [`CLAUDE.md`](CLAUDE.md).

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 async · asyncpg · Anthropic SDK ·
authlib (OIDC/Keycloak) · argon2 · React 19 + Vite + TS (frontend, Phase 4).

## Layout

```
config/      app.toml, connectors.toml
liberty/     main.py, config.py  (connectors/ auth/ ai/ web/ migrations/ added per phase)
tests/
docs/        PLAN.md
```
