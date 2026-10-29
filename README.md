# Liberty Next

[![PyPI](https://img.shields.io/pypi/v/liberty-next.svg)](https://pypi.org/project/liberty-next/)
[![Python](https://img.shields.io/pypi/pyversions/liberty-next.svg)](https://pypi.org/project/liberty-next/)
[![Docker](https://img.shields.io/badge/ghcr.io-liberty--next-blue?logo=docker)](https://github.com/fblettner/liberty-next/pkgs/container/liberty-next)
[![Release](https://github.com/fblettner/liberty-next/actions/workflows/release.yml/badge.svg)](https://github.com/fblettner/liberty-next/actions/workflows/release.yml)
[![Docs](https://img.shields.io/badge/docs-nomana--it.fr-blue)](https://docs.nomana-it.fr/liberty/getting-started/)

**A connector-driven low-code platform for business applications.** You describe your
data sources, screens, dashboards, jobs and rules in TOML, or build them in the Settings
editors; Liberty Next runs them: a React workspace, a REST API, a scheduler, a data
explorer, row-level security and an AI assistant that works on your configuration and
your data. Nothing is generated and nothing is stored as metadata tables: the form of a
screen comes from the query it runs, discovered at query time.

Three applications run on it in production, shipped as a separate licensed bundle:
**nomasx1** (JD Edwards security, segregation of duties and licence auditing), **nomajde**
(JD Edwards administration) and **nomaflow** (the ETL and scheduler). A demo application,
**nomademo** (sales analytics from spreadsheets), shows the whole platform on one case.

- 📚 Documentation — <https://docs.nomana-it.fr/liberty/getting-started/>
- 🐳 Image — <https://github.com/fblettner/liberty-next/pkgs/container/liberty-next>
- 🚀 Deployment (Compose, Swarm, TLS, backups, upgrades) — [`release/`](release/)
- 📦 Releases — <https://github.com/fblettner/liberty-next/releases> · 🐛 Issues — <https://github.com/fblettner/liberty-next/issues>

---

## What it does

**Connectors.** SQL pools on PostgreSQL and Oracle (thin driver, optional thick mode for
network encryption and LOBs over database links) with named queries, tables with their
read and write statements, schema and dblink placeholders, server-side filters, sequences
and audit mirrors; API connectors over HTTP with five authentication kinds. Secrets are
`ENC:` values encrypted at rest or `${ENV}` references, never plain text.

**Screens.** A screen is the business object: the columns and their rules, the filters
(cascading through lookups), the dialog with its tabs, nested forms and nested tables,
row actions and toolbar actions (chains of query, confirm, prompt, navigate, notify, open an
external address — the install's named addresses, so shipped screens carry none),
lifecycle hooks, row-level edit rules, bulk edit, Excel import and workbook export,
summary, tree and chart views, saved grid views shared or per user, record locks. The grid
streams large results and draws only the visible rows; its column filters follow the
column's type (several coded values at once), a date filters by a period (presets and a
calendar, the default per column), dates and date-times are picked in a themed calendar,
`url` / `email` / `color` values show as a link, a mail link or a swatch, long text can wrap,
and each screen sets its page size. Module catalogues describe a whole application module once (its tables, keys,
grid and dialog, detail tabs, menu); the Screen Assistant previews it against the live
schema and applies it — queries, read-only screens, menu and dictionary in one go.

**Dictionary, menus, dashboards.** One dictionary of data items (labels in every
language, formats, enums, lookups, sequences, rules) shared by every screen. Per-app
menus with a start page per role, a sidebar that folds to its icons. Dashboards of KPIs,
charts, tables, links and job status with a filter bar and a period picker, compared with
the period before.

**Data Explorer.** A model per app (a hub table and its dimensions) explored without SQL:
rows, measures, filters, a period, pivots, charts, drill to the detail and to the
screen's dialog, calculated fields, saved analyses and dashboards of analyses, shared or
private, exported to Excel and PDF. Apps ship analyses as a bundled catalogue.

**nomaflow.** Jobs made of steps: run a query, copy a table between databases,
load a spreadsheet or a CSV into a table (replacing the partitions the file carries,
refusing a closed period, checking a control total, reporting the rejected rows),
call an API, sync from LDAP, run a Python callable, call another job, export a query to
Excel or CSV, render a saved analysis or a report to Excel or PDF and send it with the
run's files attached. Cron schedules with presets and parameters (a run per value when a
parameter holds several), retries, pause and resume, deadline alerts, missed-schedule
detection, retention of runs and files, the upcoming runs day by day, a run history with
the step detail, the log and the files (the uploaded spreadsheet included).

**Notifications.** Rules on screen events, job events and actions deliver to the portal
inbox (live), by e-mail or through a connector; recipients are users, roles or addresses.

**AI assistant.** An Anthropic-backed assistant in a drawer over every page. It sees what
you are looking at (the screen, its filters, the row you clicked), knows what the
installation holds, queries your data read-only, follows the procedures the apps ship
(playbooks), proposes new queries, dictionary items, screens and menu items that you
apply with one click, and reads the server's jobs, log and integrity checks when the
operator allows it. Starter questions per app and screen, follow-up suggestions, a
model picker, a conversation that stays.

**Access.** Local users or OpenID Connect (any provider), roles with colon-glob
permissions, superusers, and **row-level data scope**: a role's rules on a dictionary
item or a column, with values from a user attribute, narrow every query, export,
explorer, digest and write. Login throttling, rotating refresh tokens.

**Operations.** The Liberty Next workspace (superusers) groups the framework's pages by
purpose — Build, Administer, Operate, Deploy, Document. Every configuration file is edited
by its own page there, validated,
snapshotted before each save (version history with diff and restore), hot-reloaded, and
packaged with its dependencies for deployment to another install. The apps bundle merges
into the live configuration without clobbering the operator's changes; upgrades are
journaled with the release notes of each version. Health, readiness, request ids. A
Monitoring page: the live view (connected users, locks, pools, the log tail), the server
log kept on disk (rotated, searched, exported, levels per module changed at run time) and
a retention center (what every store keeps, its size, a preview and a purge). A developer
reference read from the running code (every setting, every API route with its access and
its documented response, the data model, every command, and the impact of a change across
the configuration) and documentation reports of the release and of the install, each copy
kept on the server. Runs at the
site root or under a reverse-proxy sub-path (`[app] base_path`); installable as a web app.

---

## Install

**Full guide:** <https://docs.nomana-it.fr/liberty/getting-started/> and
[`release/README.md`](release/README.md).

### Docker Compose

A host needs only the `release/` directory:

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/fblettner/liberty-next.git
cd liberty-next && git sparse-checkout set release && cd release
./liberty install full                # light = the server + SQLite; full = + Postgres, Traefik, pgAdmin, Portainer
./liberty apps /path/to/liberty_apps-X.Y.Z.whl   # licensed customers: the apps bundle (merge plan + migrations)
./liberty ssl letsencrypt --domain liberty.example.com --email ops@example.com   # optional: TLS
```

`./liberty` is the one operator command: `status | doctor | start | stop | restart | logs |
ssl | apps | db | upgrade | rollback | backup | restore | migrate`. The database can live on an
external PostgreSQL server — at install (`--db external --db-host …`) or later
(`./liberty db external --host …`). An upgrade is
`./liberty upgrade [--tag X.Y.Z]` (doctor → backup → pull → recreate → migrations → report),
undone by `./liberty rollback`. The install prints the `admin` password once; the license key
is set in Settings → App → License (encrypted at rest).

### PyPI

```bash
pipx install 'liberty-next[all]'      # [all] = PDF reports (needs Pango), LDAP, Alembic
export LIBERTY_JWT_SECRET="$(openssl rand -base64 48 | tr -d '\n=+/')"
export LIBERTY_MASTER_KEY="$(openssl rand -base64 32 | tr -d '\n=+/')"
liberty-admin init-db                 # the auth store + the admin user (prints its password)
liberty-next                          # API + UI on http://localhost:8000
```

The licensed apps on a pipx install: `pipx inject liberty-next liberty_apps-X.Y.Z.whl`,
then `liberty-apps install --target <dir>`, `export LIBERTY_APPS_DIR=<dir>` and
`liberty-admin run-install-jobs`. `liberty-ops` is the Python install's counterpart of the
Docker `liberty` command (status, doctor, backup, restore, migrate, upgrade, rollback).

### From source

```bash
git clone https://github.com/fblettner/liberty-next.git && cd liberty-next
python3.12 -m venv .venv && .venv/bin/pip install -c requirements.txt -e ".[dev]"
./start.sh init-db                    # first run
./start.sh                            # builds the frontend when stale, serves on :8000
./start.sh dev                        # backend auto-reload · ./start.sh frontend → Vite on :5173
.venv/bin/pytest -q                   # 1 600+ tests, no live database needed
(cd frontend && npm ci && npm run lint && npm test && npm run build)
# browser regression tests against a running server (test records only; frontend/e2e/README.md)
(cd frontend && LIBERTY_E2E_USER=… LIBERTY_E2E_PASSWORD=… npm run e2e)
```

---

## Configuration

Everything is TOML under `config/`, or under `LIBERTY_APPS_DIR` when the apps bundle is
installed. Every file has a Pydantic model, a Settings editor, a version history and
hot reload.

| File | What it carries | Editor |
|---|---|---|
| `app.toml` | Server, auth, AI, notifications, reports branding, license, OIDC, jobs settings; encrypted secrets | Settings → App |
| `connectors.toml` | Pools, SQL connectors (tables, queries, sequences, lookups), API connectors | Settings → Pools, Connectors |
| `dictionary.toml` | Data items: labels, formats, enums, lookups, sequences, rules | Settings → Dictionary |
| `screens.toml` | Screens: columns, dialogs, actions, hooks, views, exports | Settings → Screens (Visual Designer) |
| `menus.toml` | Per-app navigation and the app's home | Settings → Menus |
| `charts.toml` / `dashboards.toml` | Saved charts; dashboards of KPIs, charts and tables with filters and a period | Settings → Charts, Dashboards |
| `explorer.toml` | Data Explorer models and their bundled analyses and dashboards | Settings → Explorer |
| `actions.toml` | Shared workflows reused by screens | Settings → Actions |
| `notifications.toml` | Notification rules on screen, job and action events | Settings → Notifications |
| `ai.toml` | What the apps ship for the assistant: starter questions and playbooks | Settings → AI |
| `theme.toml` / `reports.toml` | Branding; operator report templates | Settings → Theme, Reports |
| `jobs.toml` | nomaflow jobs, schedules, presets, retention and missed-schedule policies | Nomaflow → Jobs |
| `auth.toml` | Local users and roles (or the database backend) | Settings → Access |

Two secrets stay in the environment: `LIBERTY_JWT_SECRET` and `LIBERTY_MASTER_KEY` (the
AES-256-GCM key behind every `ENC:` value).

---

## Framework and applications

The framework is open. The applications live in a separate repository and ship as a
wheel; connectors flagged `licensed = true` load only under an RS256 license key, set in
Settings → App → License and checked with `liberty-license verify`. Without a key the
framework runs in restricted mode. An installed bundle is merged into the live
configuration per entity: new entities are added, untouched ones refreshed, the
operator's overrides kept.

Building a new application on the framework is a configuration job, not a project. The
`nomademo` app is the worked example, and the repository ships a Claude Code skill,
`/build-app`, that assembles an application from a description of the need.

---

## Operating it

| URL | Purpose |
|---|---|
| `/` | The workspace: sign-in, tabs, Settings, the assistant drawer |
| `/docs`, `/redoc`, `/openapi.json` | The API reference |
| `/api/*`, `/admin/*` | The API; the operator endpoints (superuser) |
| `/health`, `/ready`, `/info` | Liveness, readiness (pool ping), public counts and versions |
| `/monitoring` | Live view, server log, retention (superuser) |

Settings → History holds the per-file version history, the screen bundles and the
upgrade timeline with each version's release notes; Settings → Release notes the full
changelog in English and French (also in the wheel as `liberty/RELEASE.md` and
`RELEASE.fr.md`).

---

## Releasing

`develop` is the working branch; `main` is release-only. Every push to `develop` and
every pull request to `main` runs the CI (ruff, pytest, eslint, vitest, the build). A push
to `main` runs [`release.yml`](.github/workflows/release.yml): it reads the version in
`pyproject.toml` (and bumps the patch when that version is already tagged), requires a
real `## <version>` section in `liberty/RELEASE.md`, builds the wheel and the multi-arch
image, tags, and publishes to PyPI and GitHub. Bump the version in `pyproject.toml` before
merging for a minor or major release. If the PyPI publish fails, the version is burned:
bump and push again.

---

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 async · asyncpg · oracledb · Pydantic 2 · PyJWT ·
authlib · argon2 · APScheduler · python-socketio · tomlkit / tomli-w · openpyxl · WeasyPrint
(optional) · Anthropic SDK — React 19 · Vite · TypeScript · emotion · react-i18next ·
TanStack Table · Recharts · Monaco.

## Repository layout

```
liberty/        the framework: main.py (create_app), runtime.py (the reload path), config.py,
                connectors/ auth/ screens/ menus/ charts/ dashboards/ actions/ explorer/ ai/
                jobs/ etl/ reports/ notify/ changesets/ versioning/ userviews/ web/ (the HTTP layer)
                RELEASE.md · RELEASE.fr.md · the CLIs (cli, admin_cli, ops_cli, migrate_cli, license_cli, crypto_cli)
frontend/       React 19 + Vite + TypeScript; dist/ is served by the backend; e2e/ = Playwright regression tests
config/         app.toml and the *.toml.example templates
release/        Docker Compose layouts, the `liberty` ops command, install / upgrade / backup scripts, Traefik
docker/         the entrypoint; the Oracle Instant Client is fetched at build (pinned checksums)
tests/          pytest, SQLite fixtures, no live database
.claude/skills/ Claude Code skills shared with the repository (build-app)
```

See [`CLAUDE.md`](CLAUDE.md) for the map of the code and the rules of the house.

---

## License

The framework is free to use. `nomasx1`, `nomajde` and the other licensed applications
are sold separately and unlocked by a license key.
