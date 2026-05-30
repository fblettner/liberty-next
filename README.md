# Liberty Next

[![PyPI](https://img.shields.io/pypi/v/liberty-next.svg)](https://pypi.org/project/liberty-next/)
[![Python](https://img.shields.io/pypi/pyversions/liberty-next.svg)](https://pypi.org/project/liberty-next/)
[![Docker](https://img.shields.io/badge/ghcr.io-liberty--next-blue?logo=docker)](https://github.com/fblettner/liberty-next/pkgs/container/liberty-next)
[![Release](https://github.com/fblettner/liberty-next/actions/workflows/release.yml/badge.svg)](https://github.com/fblettner/liberty-next/actions/workflows/release.yml)
[![Docker build](https://github.com/fblettner/liberty-next/actions/workflows/docker.yml/badge.svg)](https://github.com/fblettner/liberty-next/actions/workflows/docker.yml)

**Connector-driven low-code framework.** Configure SQL queries + HTTP endpoints in
TOML; Liberty derives schemas at query time, serves a React admin UI on the same
port, surfaces an Anthropic tool-use assistant for natural-language access, and
wraps everything in a structured-config builder + dependency-aware deployment
packager.

Declarative `connectors.toml` / `screens.toml` / `dictionary.toml` / `menus.toml` /
`charts.toml` / `dashboards.toml` files drive the runtime — schemas derived at query
time, no code-gen step, every field round-trippable through the structured editors
at **Settings → \<tab\>**.

## Quick links

- 📚 **Documentation** — <https://docs.nomana-it.fr/liberty/getting-started/>
- 💻 **Source** — <https://github.com/fblettner/liberty-next>
- 🐳 **Docker image** — <https://github.com/fblettner/liberty-next/pkgs/container/liberty-next>
- 🚀 **Deployment configs** (Compose + Swarm + helper scripts) — [`release/`](https://github.com/fblettner/liberty-next/tree/main/release)
- 🐛 **Issues** — <https://github.com/fblettner/liberty-next/issues>
- 📦 **Releases** — <https://github.com/fblettner/liberty-next/releases>

---

## Install

**Full guide:** <https://docs.nomana-it.fr/liberty/getting-started/>

Three routes — pick what fits.

### Docker Compose (recommended)

Two ready-to-run layouts live under [`release/`](release/):

```bash
git clone https://github.com/fblettner/liberty-next.git
cd liberty-next/release
cp .env.example .env
$EDITOR .env                                              # set the REQUIRED values
docker compose -f docker-compose.light.yml up -d          # 1 container, SQLite
# OR
docker compose -f docker-compose.full.yml up -d           # 5 services (Traefik / pg / pgadmin / portainer)
```

See [`release/README.md`](release/README.md) for the full deployment guide (TLS wiring,
backups, upgrades, common ops).

### PyPI

**Recommended — pipx** (isolates Liberty Next in its own venv; CLI commands stay on
your PATH; no risk of polluting system Python):

```bash
# Install pipx once if you don't have it:
#   macOS:    brew install pipx && pipx ensurepath
#   Linux:    sudo apt install pipx && pipx ensurepath
#                 # or:  python3 -m pip install --user pipx && python3 -m pipx ensurepath
#   Windows:  py -m pip install --user pipx && py -m pipx ensurepath

pipx install liberty-next
liberty-next                      # → API + SPA on http://localhost:8000
```

This gives you every CLI tool the package ships (`liberty-next`, `liberty-admin`,
`liberty-license`, `liberty-crypto`) on the PATH, each one routed through the same
isolated venv. Upgrade with `pipx upgrade liberty-next`; uninstall cleanly with
`pipx uninstall liberty-next` (removes the venv + every shim, leaves nothing behind).

**Plain pip** (only when pipx isn't an option — make a venv yourself to avoid breaking
system packages):

```bash
python3 -m venv ~/.local/liberty-venv
~/.local/liberty-venv/bin/pip install liberty-next
~/.local/liberty-venv/bin/liberty-next
```

First boot generates an `admin` password and prints it once — capture it from the
logs, then sign in at <http://localhost:8000>.

### From source (development)

```bash
git clone https://github.com/fblettner/liberty-next.git
cd liberty-next
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -v               # 900+ tests
./start.sh init-config            # seed config/*.toml from the .example files
./start.sh init-db                # FIRST RUN: create the auth store + `admin` user (prints password)
./start.sh                        # build frontend + serve on :8000
./start.sh dev                    # same, with backend auto-reload
./start.sh frontend               # Vite HMR dev server on :5173 (pair with `./start.sh api dev`)
```

---

## What you get

| URL | Purpose |
|---|---|
| `/` | React SPA — admin UI (sign-in, workspace tabs, Settings, AI assistant) |
| `/docs` | **Swagger UI** — interactive API explorer |
| `/redoc` | **ReDoc** — print-friendly API reference (grouped by tag) |
| `/openapi.json` | OpenAPI 3 spec — generated from FastAPI routes + Pydantic models |
| `/api/*` | Public API surface (auth gates per route) |
| `/admin/*` | Operator-only endpoints — config CRUD, find-usages, packaging, AI scaffold-apply, … |
| `/info` | Public liveness + counts (connectors / screens / pools) — Docker `HEALTHCHECK` hits this |

---

## Configuration in 60 seconds

Six TOML files under `config/` (or wherever `LIBERTY_APPS_DIR` points). Every file
is round-trippable through the structured editors at **Settings → \<tab\>**:

| File | What it carries | Editor |
|---|---|---|
| `app.toml` | App-level settings (host / port / log level / AI model / hot-reload) | Settings → App |
| `connectors.toml` | DB pools + SQL connectors with named queries + API connectors with endpoints | Settings → Pools, Settings → Connectors |
| `dictionary.toml` | Shared + per-connector field metadata (labels / types / rules / lookups / sequences) | Settings → Dictionary |
| `screens.toml` | Screen definitions — per-app grids + dialog forms + actions + row menus | Settings → Screens |
| `charts.toml` | Saved chart specs referenceable from screens + dashboards | Settings → Charts |
| `dashboards.toml` | Widget grids with shared filters | Settings → Dashboards |
| `menus.toml` | Per-app navigation trees | Settings → Menus |

`${VAR}` and `${VAR:-default}` env-var references are expanded at load time so secrets
stay in the environment (`LIBERTY_JWT_SECRET`, `LIBERTY_MASTER_KEY`, `LIBERTY_LICENSE_KEY`,
`ANTHROPIC_API_KEY`, OIDC client secrets) and never live in committed TOML.

---

## Customer / vendor split

Liberty Next ships as an **open framework**. The customer-facing connectors + screens
+ dictionaries live in a separate apps repo (`liberty-apps`); the licensed ones
(nomasx1 / nomajde / nomaflow) are unlocked by `LIBERTY_LICENSE_KEY`. Without a key
the framework runs in **restricted** mode — those connectors aren't loaded.

The **Settings → Package** tab packages selected screens / menu items / dashboards
plus their full dependency closure (connectors / queries / DD entries / lookups / …)
into a ZIP for atomic deployment to another install. Each entity carries an
`override = true` flag operators can flip to mark customer customisations — the
import-package endpoint's `overwrite` strategy preserves flagged entities so vendor
upgrades don't clobber customer forks.

---

## Releasing

One GitHub Actions workflow, [`release.yml`](.github/workflows/release.yml), publishes
every release. **It runs automatically on every push to `main`.** No buttons, no tags,
no manual triggers.

### The flow

```
develop branch         →  work happens here, push freely, NOTHING triggers
                         ↓
                       PR develop → main, merge
                         ↓
main branch push       →  release.yml runs:
                          1. Reads pyproject.toml's version
                          2. If that version is already tagged → auto-bump patch (7.0.1 → 7.0.2)
                             Else use as-is (when you manually bumped for a major/minor)
                          3. Commits the bumped pyproject.toml back to main ([skip ci])
                          4. Builds + pushes multi-arch Docker to ghcr.io as <version> + :latest
                          5. Publishes sdist + wheel to PyPI
                          6. Tags v<version> + creates GitHub release with auto-notes
```

### Version control

- **Bugfix / patch release** (default): just merge to main. Workflow auto-bumps `7.0.1 → 7.0.2`.
- **Minor release** (`7.0.x → 7.1.0`): bump `version = "7.1.0"` in `pyproject.toml` in any commit before merging. Workflow honours it.
- **Major release** (`7.x → 8.0.0`): same — bump in pyproject.toml.

### Setting up the repo (one-time)

1. **Branch protection on `main`** (recommended):
   <https://github.com/fblettner/liberty-next/settings/branches> → add a rule for
   `main` → require pull request before merging. This forces the develop → main flow.

2. **PyPI token**: <https://pypi.org/manage/account/token/> → create a token
   (account-scoped first time; scope to `liberty-next` after the first release).
   Add it as a repo secret:
   <https://github.com/fblettner/liberty-next/settings/secrets/actions> →
   Name: `PYPI_API_TOKEN`, Value: `pypi-…`.

3. **Docker image visibility**: after the first push to ghcr.io, the image lands
   private. Make it public at <https://github.com/fblettner?tab=packages> →
   liberty-next → Package settings → Change visibility → Public. (One-time.)

### Manual override

The workflow also has a `workflow_dispatch` trigger if you need to re-publish a
specific version (rebuild after a base-image CVE, etc.). Go to
<https://github.com/fblettner/liberty-next/actions/workflows/release.yml> →
Run workflow → optional version input.

### Recovering from a failed publish

- **Pre-publish failure** (build, Docker push) — fix and re-trigger; nothing is consumed.
- **PyPI publish failure** (the only irreversible step) — the version is burned. Bump
  `pyproject.toml` to the next version and push again.

---

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 async · asyncpg (PostgreSQL) · oracledb (Oracle,
thin) · APScheduler (nomaflow ETL + cron) · Anthropic SDK · authlib (OIDC) · argon2 ·
cryptography (AES-256-GCM) · React 19 + Vite + TypeScript + emotion ·
TanStack Table · Monaco (SQL editor) · Recharts (visualisation).

---

## Repository layout

```
config/      app.toml (committed) · {connectors,dictionary,menus,screens,charts,dashboards,auth}.toml (NOT committed — per-deployment)
liberty/     main.py · config.py · crypto.py · {cli,admin_cli,crypto_cli,license_cli}.py
             · connectors/{config,base,db,sql,api,registry,dictionary,introspect}.py
             · licensing/{__init__.py, public.pem}
             · menus/config.py · screens/config.py · charts/config.py · dashboards/config.py
             · auth/{authstore,password,tokens,principal,oidc,dependencies,routes,models,db,service}.py
             · ai/{tools,connector_tools,scaffold_tools,proposal,assistant,routes}.py
             · web/{deps,errors,connectors,menus,screens,charts,dashboards,license,theme,admin,
                    dependencies,package,package_import,clone_with_deps,delete_with_deps,usages}.py
frontend/    Vite + React 19 + TS — built dist/ served by the backend
             src/{api,auth,workspace,types,services,common,pages,components,locales}/*
.github/workflows/  pypi-release.yml · docker.yml
docker/      entrypoint.sh — runtime config-init (init-db / init-config when env vars set)
start.sh     run/dev helper (serve | dev | api | build | frontend | init-db | init-config | help)
tests/       335+ tests
docs/        PLAN.md (full phased plan) · DEPLOYMENT.md · NOMAFLOW-UI.md · PHASE13.md (nomaflow)
```

---

## Links

- **Docs (getting started, config reference, walkthroughs):** <https://docs.nomana-it.fr/liberty/getting-started/>
- **GitHub:** <https://github.com/fblettner/liberty-next>
- **PyPI:** <https://pypi.org/project/liberty-next/>
- **Docker image:** <https://github.com/fblettner/liberty-next/pkgs/container/liberty-next>
- **Deployment configs:** [`release/`](release/) (light + full Docker Compose layouts)
- **API reference:** `https://<your-install>/redoc`
- **CLI reference:** `liberty-next --help` (also `liberty-admin`, `liberty-license`, `liberty-crypto`)
- **Working with Claude Code?** See [`CLAUDE.md`](CLAUDE.md)
- **Full plan + design decisions:** [`docs/PLAN.md`](docs/PLAN.md)

---

## License

Open framework: free. Connectors flagged `licensed = true` in `connectors.toml`
(sold separately, distributed in their own repos) are unlocked by an RS256 JWT
license key set via `LIBERTY_LICENSE_KEY`. Without a key the framework runs in
"restricted" mode and those connectors aren't loaded. Inspect a key with
`liberty-license verify`; status at `GET /api/license`.
