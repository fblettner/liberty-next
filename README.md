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

Two GitHub Actions workflows split the release / build duties cleanly:

| Workflow | Trigger | Output |
|---|---|---|
| [`release.yml`](.github/workflows/release.yml) | **Manual** — "Run workflow" with a version input | Multi-arch Docker image at `ghcr.io/fblettner/liberty-next:<version>` + `:latest`, sdist + wheel on PyPI, annotated git tag `v<version>`, GitHub release. **Atomic, single version for both.** |
| [`docker.yml`](.github/workflows/docker.yml) | Push to `main` (auto), PR (build-only), manual | Rolling `edge` + `sha-<short>` Docker tags for "current main without chasing the SHA". |

### Why manual, not tag-push

A failed tag-push release burns the version: PyPI version slots are consumed
permanently the moment they're accepted, even on partial failure. With a manual
trigger, every pre-publish step (build, validate, Docker push) runs first; if any
fails, you fix the issue and re-run with the **same version** — nothing is consumed
until the actual PyPI upload near the end.

### One-time setup

Before the first release works, do these two things in the GitHub repo:

1. **PyPI token** — get a token at <https://pypi.org/manage/account/token/>
   (account-scoped is fine for the first release; scope to `liberty-next` once it exists).
   Add it at **Settings → Secrets and variables → Actions → New repository secret**:
   - Name: `PYPI_API_TOKEN`
   - Value: `pypi-…`

2. **Docker image visibility** — after the first `docker.yml` run pushes the image,
   it lands as private. Make it public at
   <https://github.com/fblettner?tab=packages> → liberty-next → Package settings →
   Change visibility → Public. (One-time; subsequent pushes inherit the setting.)

The Docker workflow needs no secret — `GITHUB_TOKEN` doubles as the ghcr.io credential.

### Cutting a release

```bash
# 1. Bump pyproject.toml's version + commit + push
$EDITOR pyproject.toml          # change version = "..."
git commit -am "release: v7.0.1"
git push
```

Then in the browser:

1. Go to <https://github.com/fblettner/liberty-next/actions/workflows/release.yml>
2. Click **Run workflow** (top-right).
3. **Leave the version field empty** — it auto-reads `pyproject.toml`. (Optionally
   type it for a typo guard; leading `v` is tolerated.)
4. Click **Run workflow**.

The workflow validates the version, builds sdist + wheel, builds + pushes the
multi-arch Docker image, publishes to PyPI, then creates the `v7.0.1` git tag +
GitHub release. ~6-10 minutes start to finish.

**If something fails before the PyPI step** (pre-flight, build, Docker push) — fix the
issue and re-run the workflow with the same version. Nothing is consumed.

**If the PyPI publish itself fails** (the only non-recoverable step) — bump the
version in `pyproject.toml` and run again. PyPI doesn't allow re-publishing the same
version even when the previous attempt failed.

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
