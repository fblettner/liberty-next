# Liberty Next — Claude Code context

Auto-loaded by Claude Code. This file is a **map**: what the project is, the rules, where
things live, how to run and ship it. It points at the code instead of narrating it. The
phase-by-phase build story (Phases 0–8, every Phase 6 slice, the design rationale) is frozen
in `docs/history/CLAUDE-2026-05-phases.md`; per-version changes are in `liberty/RELEASE.md`
(EN) / `RELEASE.fr.md`. Working documents (review, plan, ledgers) live in `docs/work/`
(git-ignored, never committed).

## What this is

**Liberty Next** (v2) is a connector-driven low-code framework: TOML configuration drives
discovery (connectors, screens, menus, dashboards, jobs), nothing is stored as metadata
tables. It replaced the v1 Liberty framework (`../liberty-framework/`, ~50 `ly_*` metadata
tables, now **retired**). Three applications run on it in production, from the private repo
`../liberty-apps`: **nomasx1** (JD Edwards security / SoD / licence auditing, Postgres),
**nomajde** (JDE administration, Oracle) and **nomaflow** (the ETL + scheduler jobs). The
connector pattern comes from `../../JavaProjects/nomaubl` (a Java e-invoicing app whose
React UI is the model for this frontend — see `docs/work/shared-components.md`).

Published as the PyPI package `liberty-next` and the image `ghcr.io/fblettner/liberty-next`;
docs at https://docs.nomana-it.fr/liberty/ (a separate Docusaurus repo).

## Hard rules

- **Never touch `../liberty-framework/` source** (v1). It is retired, kept for reference.
- **No `ly_*` metadata tables.** Form / column schema is discovered at query time from
  `cursor.description`; connector, screen, menu, dashboard, job definitions live in TOML.
- **Anthropic, not OpenAI.** The AI assistant uses the `anthropic` SDK. Default model
  `claude-opus-4-7` unless the user names another; consult the `claude-api` skill before
  changing `liberty/ai/`.
- **Match the surrounding code's style.** Type hints everywhere (`from __future__ import
  annotations`). Tests for new modules: pytest on the backend, Vitest for the frontend's pure
  modules (`*.test.ts` next to the module). `ruff check` and `npm run lint` must stay at zero
  errors (CI enforces both; `.pre-commit-config.yaml` mirrors them).
- **Work step by step and never commit before the operator has reviewed the diff.** Finish
  one step, run the tests, leave the tree uncommitted, report, wait. "Commit" means a **local**
  commit on `develop`; push only when the operator says "push" (every push runs CI — push once
  a plan phase is done, not per step). Verify a claim against the current code and `git log` (and measure, for
  anything about performance) before proposing a change — a stale comment is not evidence.
- **TOML round-trips: never `tomlkit.parse` a big nested config file.** tomlkit is O(n²) on
  deeply nested tables — measured on a real install: `screens.toml` (558 KB) **67 s**,
  `dictionary.toml` (119 KB) **83 s**, vs ~100 ms / 21 ms through `tomllib` + `tomli_w`. The
  cliff is about nesting, not size (`connectors.toml`, 146 KB, parses in 0.66 s). The split,
  already applied everywhere: **screens / dictionary / menus / charts / dashboards / actions /
  theme** go through `tomllib` + `tomli_w` (comments dropped — the builders own those files);
  **connectors.toml, app.toml, jobs.toml, reports** stay on `tomlkit` (small, hand-edited,
  comments kept). Single source of truth: `_BIG_LABELS` + `_load_doc` / `_dump_doc` in
  `liberty/web/rename.py` (used by `move.py`); every `put_*_parsed` / clone / delete helper in
  `liberty/web/admin.py` follows it. Don't "fix" config-save speed with threads or caches: a
  save is ~100 ms, only tomlkit was ever slow and it's routed around.
- **Frontend theme discipline.** Styled components take every colour / size / radius /
  shadow from `frontend/src/theme.ts` (CSS vars set in `index.css`, dark + `.theme-light`);
  no hard-coded hex / rgba. Every dropdown is the shared `SearchSelect`, never a native
  `<select>`. Pages stay small: helpers under `pages/<X>/`, reusable bits in `common/`,
  plain logic in `services/` / shapes in `types/` (no React there).

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 async · asyncpg (Postgres) + oracledb (Oracle thin;
thick-mode option for NNE / LOB-over-dblink) · Pydantic 2 · PyJWT (HS256) · authlib (OIDC,
any provider) · argon2-cffi · python-socketio · APScheduler · tomlkit / tomli-w · openpyxl ·
Anthropic SDK. Optional extras: `reports` (WeasyPrint, needs Pango), `ldap` (ldap3),
`alembic` (+ psycopg2) — `liberty-next[all]` for every feature (the image, the dev venv and
CI install `[all]`). Frontend: React 19 + Vite + TypeScript + @emotion/styled +
react-i18next (EN/FR) + @tanstack/react-table + react-virtual + recharts + Monaco (bundled,
lazy) + socket.io-client; built `frontend/dist/` is served by the backend.

## Repository map

```
liberty/                  the framework (≈44k lines, 85 test files, 1280+ tests)
  main.py                 create_app(): lifespan builds everything onto app.state, mounts the
                          routers, /health (liveness) /ready (pool ping) /info, serves the SPA
  runtime.py              load_registry() + rebuild_runtime(app, sections=…): THE reload path
                          (lifespan, POST /admin/reload, hot reload, license rotation) — one
                          lock, rebinds every pool holder, disposes the old registry last
  config.py               Settings (app.toml): one *Settings class per section, see below
  crypto.py / crypto_cli  AES-256-GCM "ENC:" field encryption, byte-compatible with v1
  licensing/              RS256 license-key verification (embedded public.pem)
  theme.py                theme.toml + GET /api/theme (operator branding)
  framework_enums.py      the widget / enum registries the builders render from
  coercion.py             kwargs coercion shared by python job steps and reports
  cli.py / admin_cli.py / migrate_cli.py / license_cli.py   the console scripts (below)
  connectors/             config.py (Pydantic for connectors.toml + ParamBind + ColumnHint),
                          db.py (PoolRegistry: lazy engines, ENC: passwords, terminal
                          dispose), sql.py (SQLConnector: text() binds, filter / summary wraps,
                          form rules, SEQUENCE, audit mirror, Oracle quirks, NDJSON streaming,
                          multi-pool), api.py (APIConnector: httpx, 5 auth kinds, templating),
                          registry.py (ConnectorRegistry + the shared dictionary), dictionary.py
                          (dictionary.toml: labels, enums, lookups, sequences, rules),
                          introspect.py (schema walk), thick.py / thick_engine.py (Oracle OCI)
  auth/                   scope.py (row-level data scope: role rules → resolved per user → JWT
                          `scope` claim → SQLConnector wrap / write check), authstore.py (AuthBackend: toml | db), tokens.py (PyJWT access /
                          refresh with rotation), throttle.py (login throttle), principal.py
                          (colon-glob permissions), oidc.py, dependencies.py, routes.py;
                          models.py / db.py / service.py = the DB backend's ly2_* tables
  screens/config.py       Screen = the business object: columns (ColumnHint), dialog (tabs:
                          form | nested_form | nested_table), actions / row_menu / lifecycle
                          hooks (the Action union incl. chain / if / loop / return), export
  menus/ charts/ dashboards/ actions/   their TOML schemas + loaders
  ai/                     assistant.py (streaming agentic loop; the page context = a 2nd uncached system
                          block), tools.py, connector_tools.py, scaffold_tools.py (proposal-only), routes.py
                          (SSE; `/ai/tools` also lists the pickable models + starters), catalog.py (the model list),
                          appconfig.py (ai.toml: what the APPS ship — starters per app / screen, playbooks),
                          instance_tools.py (describe_instance, list_playbooks / load_playbook: read app.state at call time),
                          ops_tools.py (jobs_status / server_log / integrity_checks — opt-in `[ai] ops_tools`)
  jobs/ (nomaflow)        schema.py (jobs.toml), registry, runner (state machine, retries,
                          cancel), scheduler (APScheduler, single process), recovery, retention,
                          missed.py (missed-schedule check: expected cron instants vs the run
                          history → MISSED runs + the `missed` job event), runlog, wiring, steps/{sql_query,sql_copy,http,python,ldap_sync,call_job,report (a saved
                          explorer analysis / dashboard or a plugin report → xlsx / pdf in the run's work area, under
                          a user's or role's scope), notify (the dispatcher, the run's files attached),
                          file_load (xlsx / csv → table, replace_partition in one transaction; the file
                          from `path`, a per-fire upload or a watched folder)}, files.py (the work area:
                          `[jobs] files_dir`, uploads, rejects reports)
  etl/                    streaming copy + snapshot / truncate / audit primitives for the steps
  reports/                registry (plugin + operator Jinja templates), render (markdown → PDF)
  changesets/             change packages: capture writes, lifecycle, compaction, apply bundle
  versioning/             config snapshot history (.versions/) + upgrade journal
  userviews/              DB-backed saved grid / chart views (shared + per user)
  presets.py              the Screen Assistant's table presets (config/presets/**.toml): the catalogue loader
                          shared by the assistant endpoint and the explorer (a model may derive from a preset; a
                          preset with `app` + `explorer = true` ships its model by default, built at first use)
  explorer/               the Data Explorer (C3): config.py (explorer.toml: hub, entities (a query or a table)
                          with cardinality / key / measures / default_date, relations on column pairs, an
                          optional `preset`), model.py (fields discovered from each entity query's columns +
                          dictionary / screen hints, type classes, materialize_preset = apply_preset + resolve_relations
                          on data items with dd_prefix (the builder's "apply a preset", POST /admin/explorer/
                          from-preset), ModelCache per app / model cleared on reload), engine.py (one
                          GROUP BY per request: row sources ≤ one many-side entity, one-side LEFT
                          JOINs, correlated sub-queries / EXISTS for many-side measures / filters,
                          scope per entity via SQLConnector.scoped_select, buckets / merge / sort /
                          Top N in Python; detail mode paged + drill from a cell), calc.py (the
                          calculated-field language: typed, compiled to dialect SQL, never free SQL),
                          store.py (saved analyses / dashboards on the user-views store: shared under
                          the `*` user, private under the owner; sync_bundled applies the catalogue
                          of explorer.toml once, refreshes untouched copies, keeps deletions),
                          report.py (the server-side reading of a saved AnalysisDefinition: period
                          presets, forced window, build_sections → xlsx / pdf through reports/)
  notify/                 notifications: store.py (ly2_notification inbox, schema_sync), dispatcher.py
                          (Message → portal rows + Socket.IO push / SMTP email / a connector query or
                          endpoint; recipients user: role: email:; channels isolated), rules.py
                          (notifications.toml: screen | job | action triggers, `when` on the row),
                          events.py (fire_screen_event from the write route, fire_job_event from the
                          runner's on_finished hook, fire_action_rule from `send_notification`)
  plugins/invoke.py       module:function resolution for the apps' Python (allow-listed)
  config_merge.py         per-entity additive merge of a shipped config into the live one (the apps
                          installer + Package & Deploy): override / untouched-vs-.bundled snapshot /
                          custom / withdrawn rules, secrets always from the live connectors.toml
  schema_sync.py          create_all + forward-add model columns on the framework's own tables
  observability.py        X-Request-ID middleware (ASGI), request-id log filter, JSON formatter
  logfile.py              the server log on disk (rotating JSON lines, repeats collapsed, `[app] log_levels`),
                          installed by the lifespan; searched / exported / levels set by web/logs.py (/admin/logs)
  retention_center.py     every store's retention in one place (policy, where set, size, dry run / purge) for
                          web/retention.py (/admin/retention, Monitoring → Retention)
  migrations/             v1 → v2 migration + `liberty-migrate diff` (kept for reference)
  sio.py                  Socket.IO: record locks (rooms), technical dashboard, log tail
  web/                    the HTTP layer — see the router table
frontend/src/             see the frontend map
config/                   app.toml (committed) + *.toml.example templates; the live per-
                          deployment TOMLs are NOT here (see liberty-apps); auth.toml is local
release/                  docker-compose layouts (light / full / swarm / TLS / apps), `liberty`
                          (the one ops command: install|start|stop|status|doctor|ssl|apps|
                          upgrade|backup|restore|logs|migrate — the compose chain is STATE in
                          .env, only `ssl`/`apps` change it) wrapping install / upgrade /
                          backup scripts, Traefik config
docker/                   entrypoint + vendored Oracle Instant Client zips
tests/                    pytest (asyncio auto mode); SQLite fixtures; no live DB needed
```

### HTTP routers (`liberty/main.py` mounts them; all need a JWT except `/health`, `/ready`, `/info`, `/api/theme`, `/auth/login|refresh|oidc/*`)

| Prefix | Module | What |
|---|---|---|
| `/auth` | `auth/routes.py` | login (throttled), refresh (rotating), me, profile, change-password, OIDC |
| `/api` | `web/connectors.py` | `GET/POST /api/sql/{c}/{q}` (+ `?_stream=1` NDJSON, `_limit`, `_chunk_size`, screen-aware), `/api/http/{c}/{e}`, `/api/connectors`, `_schemas` / `_schema` introspection, `_import` |
| `/api` | `web/menus.py`, `web/screens.py`, `web/export.py`, `web/charts.py`, `web/dashboards.py`, `web/actions.py`, `web/license.py`, `web/theme.py` | permission-pruned catalogs: menus, screens (+ dialog / actions resolved), workbook export, charts, dashboards, shared actions, license status, theme |
| `/api/reports` `/api/views` `/api/plugins` | `web/reports.py`, `web/views.py`, `web/plugins.py` | run / download reports, saved views, invoke app callables |
| `/api/notifications` `/admin/notify/test` | `web/notifications.py` | my inbox (list, unread count, mark read); a superuser's test send through the dispatcher |
| `/api/explorer` | `web/explorer.py` | the explorer models the caller may use (perm `explorer:{app}:{model}`), `{app}/{model}/model` with the discovered fields (`?refresh=1`), `POST {app}/query` (aggregate: rows / measures / filters bag / period / sort / limit), `POST {app}/detail` (rows, paged, `drill`), `POST {app}/calc/test`, `GET/PUT {app}/saved/{analyses|dashboards}` + `DELETE …/{id}`, `POST {app}/export` (xlsx / pdf) |
| `/ai` | `ai/routes.py` | `POST /ai/chat` (SSE), `GET /ai/tools` — perm `ai:chat` |
| `/admin` | `web/admin.py` (3.3k lines — the config builders' backend), `web/dictgen.py` | reload, `GET /admin/config/schema`, `GET/PUT /admin/config/<section>/parsed`, rename / move / clone / delete-with-deps / find-usages / dependencies / integrity, packages (build / import), versions, AI proposal apply, API test, dictionary scan — **superuser** |
| `/admin/access` `/admin/jobs` `/admin/changesets` | `web/access.py`, `web/jobs.py`, `web/changesets.py` | roles / permissions, nomaflow jobs + runs + presets + `{job}/upload` / `runs/{id}/files/{name}` + `retention` / `missed` policies, change packages |
| `/socket.io` | `sio.py` | JWT on handshake; locks, dashboard, log rooms |

Row-level scope: a role's `scope` rules (dictionary item `dd` or result `column` → values, from a
user attribute or literal, optional `exclude` / `connector`) are resolved at login into the JWT and applied by
`SQLConnector` to every SELECT as a `lib_rls` wrap (and to writes); a `dd` rule reaches every column
carrying the item — hint `dd`, same name, or name minus the connector's `dd_prefix` (JDE: 2, `ABCO`
→ `CO`); the auth dependency publishes it in `liberty.auth.scope.current_scope` so code without
principal plumbing is covered; jobs are unrestricted. Attributes come from `liberty-admin`, the
Access API or `[oidc] attribute_claims`. Permission strings: `sql:{connector}:{query}`, `api:{connector}:{endpoint}`, `plugin:{ref}`,
`ai:chat`, with colon-segment globs (`sql:nomasx1:*`, `*`); superusers bypass. The permission
is checked **before** the connector lookup (no name enumeration). A mutating query needs both
`writable = true` in TOML and the caller's permission. DB errors: non-superusers get the
driver line + an `error_id`; superusers the full text; the statement and binds are logged
only when the pool's `debug_sql` is on (`web/errors.py::db_error_detail`). `#LOGIN_USER#` is
still resolved in the browser (a convenience, not a security boundary — the scope is).

### Config files ↔ settings ↔ builders

Every file is validated by a Pydantic model, edited by a Settings builder through
`/admin/config/<section>/parsed`, snapshotted by `versioning/` before each write, and
hot-reloadable (`[app] hot_reload = true`, `web/hot_reload.py`) or reloaded via
`POST /admin/reload`. With `LIBERTY_APPS_DIR` set, every `config_path` default resolves to
`${LIBERTY_APPS_DIR}/<file>.toml` (the apps repo); `auth.toml` always stays local.

| File | Settings section | Model | Builder (Settings tab) |
|---|---|---|---|
| `app.toml` | the whole `Settings` | `config.py` | App (+ Reports → Branding) |
| `connectors.toml` | `[connectors]` | `connectors/config.py::ConnectorsFile` (`[pools.*]`, `[connectors.*]` sql \| api) | Pools, Connectors (Tables · Sequences · Lookups; API editor) |
| `dictionary.toml` | (next to connectors) | `connectors/dictionary.py::DictionaryFile` | Dictionary (Entries · Enums · Lookups · Sequences · Framework) |
| `screens.toml` | `[screens]` | `screens/config.py::ScreensFile` | Screens (list + Visual Designer) |
| `menus.toml` | `[menus]` | `menus/config.py::MenusFile` | Menus |
| `charts.toml` / `dashboards.toml` | `[charts]` / `[dashboards]` | `charts/config.py`, `dashboards/config.py` | Charts, Dashboards |
| `actions.toml` | `[actions]` | `actions/config.py` | Actions (shared workflows) |
| `theme.toml` | `[theme]` | `theme.py::ThemeFile` | Theme |
| `reports.toml` | `[reports] templates_config_path` | `reports/schema.py` | Reports |
| `jobs.toml` | `[jobs]` (default `${LIBERTY_APPS_DIR}/../plugins/nomaflow/jobs.toml`) | `jobs/schema.py` | nomaflow → Jobs |
| `explorer.toml` | `[explorer]` | `explorer/config.py::ExplorerFile` (`[explorer.<app>.<model>]`, perm `explorer:<app>:<model>`) | Explorer (models per app: hub, entities, relations, bundled analyses) |
| `ai.toml` | `[ai] config_path` | `ai/appconfig.py::AiFile` (`[ai.<app>] starters`, `[ai.<app>.screens.<id>] starters`) | AI (what the apps ship; the operator's `[ai] starters` in app.toml win) |
| `notifications.toml` | `[notify] config_path` (+ the inbox pool and SMTP, edited under App) | `notify/rules.py::NotificationsFile` | Notifications |
| `auth.toml` | `[auth] backend = "toml"` | `auth/authstore.py::AuthFile` | Access |

Secrets: `${ENV_VAR}` / `${VAR:-default}` substitution in every TOML; `ENC:` values decrypted
at runtime with `[crypto] master_key` (`LIBERTY_MASTER_KEY`, same key as v1). Never put a
secret in a committed file.

### Screens are the source of truth for display

A connector holds **queries only** (`sql` as a string or a per-dialect map, `params`,
`writable`). A `Screen` owns everything else: `columns` (ColumnHint: dd / label / format /
rule / hidden / filter / filter_from / visible_when / key / default…), `read_query` +
`update_query` / `insert_query` / `delete_query`, `auto_load`, `audit_table`, `max_rows`, `editable_when`
(row-level read-only rule, enforced by the grid, the dialog and the write route), the
`dialog`, `actions`, `row_menu`, lifecycle hooks (`on_insert` / `on_update` / `on_delete`,
dialog `on_load` / `on_save` / `on_cancel`), `export`, `summary`. The web layer looks up the
screen for `(connector, query)` and threads its hints into `SQLConnector.execute()`; a query
with no screen runs bare. The dictionary resolves labels / formats / rules per request
language (`X-Liberty-Lang`). Write conventions the migration established and the UI relies
on: `_get` / `_put` / `_post` / `_delete` query suffixes, uppercase binds, `:<COL>_ORIGINAL`
rebinds on updates, filter-flagged columns wrapped server-side as
`SELECT * FROM (<sql>) lib_flt WHERE …`.

### Frontend map (`frontend/src/`, ≈52k lines)

```
main.tsx / App.tsx     providers (Branding → Modals → Auth → Sio → Workspace → Tabs); routes are thin
                       <TabRoute> markers; screens render inside components/TabHost (every open tab
                       stays mounted, each inside an ErrorBoundary + Suspense)
api/client.ts          fetch wrapper: X-Liberty-Lang / X-Liberty-Pool headers, single-flight 401
                       refresh, streamSSE (AI) and streamNDJSON (SQL rows)
auth/ workspace/ tabs/ sio/ branding/   the contexts (AuthContext, WorkspaceContext = connectors +
                       menus + screens + license + current app / pool, TabsContext, SioContext)
common/                shared primitives: Button Card Input SearchSelect Checkbox FileInput Tag Banner Spinner
                       PageLayout Modal Modals(useModals) ErrorDetails ErrorBoundary SchemaForm
                       SchemaNavigator SubNav EditorModalShell DateRangeFilter (presets + two-month
                       calendar + date basis; logic in services/period.ts) PortalPanel (fixed popover); NOT barrelled (heavy): DataTable
                       (TanStack grid: virtualized, grouping, filters, edit mode, context menu,
                       saved views), Markdown, SqlEditor (Monaco, lazy), SqlWizardModal, SqlTestRunner
pages/TableView/       the data screen: index (params, streaming run), ResultTable (grid + bulk edit
                       + import + row menu + export), ScreenDialog (the form, nested tabs), FilterPanel,
                       actionRunner.ts (chain / if / loop / return runtime), Summary / Tree / Chart views
pages/Settings/        one *Builder per config section + the Visual Designer (ScreenVisualBuilder,
                       ScreenEditor, ActionListEditor…), ApiConnectorEditor, wizards, ConfigVersions,
                       AccessBuilder (+ Access/: RoleEditor panel, MenuTree inherit|allow|deny, OtherAccess,
                       permissions.ts = strings ⇄ tree model + the backend's glob matcher, ScopeRulesEditor +
                       scopeRules.ts = data-scope rules / attributes helpers, both tested),
                       AppBuilder, ReportsBuilder, ThemeBuilder, TechnicalDashboard,
                       Package / ChangePackages / IntegrityBuilder (reached from nomaflow pages)
pages/Nomaflow/        JobsList, JobEditor, StepEditor, RunDetail, Schedule, RetentionPanel
pages/Explorer/        the Data Explorer page: index (model, the AnalysisDefinition, runs, drill, views), FieldsPane
                       (+ calculated fields), Shelves (rows / measures / filters chips), PeriodBar, ResultTable, PivotView,
                       ChartView (bar / line / pie via TableView/ChartCanvas, KPI), CalcModal (Test before Save),
                       RecordDialog (a detail row in its screen's dialog), SaveModal, DashboardsView (tiles grid, each
                       running a saved analysis); logic in services/explorer.ts (definition ⇄ requests, filter bag,
                       click-to-add), services/explorerPivot.ts (pivot tree, chart shaping, KPI totals), both tested,
                       services/explorerSaved.ts (the saved routes, dashboard shape); shapes in types/explorer.ts
pages/DashboardView/ Chat/ (AiDrawer) Reports/ Notifications/ (the inbox; components/NotificationBell
                       in the header, live via SioContext.subscribeNotifications) ScreenAssistant/ HttpRunner/ Monitoring/ Login/
services/ types/       no-React helpers (lookups, cells, gridViews, poolSchema, sqlCompletion, aiContext = the
                       page context the TableView publishes and the chat drawer sends, aiChat = starters / persisted
                       history / picked model…) and the backend response shapes
locales/{en,fr}.ts     react-i18next; keys must exist in both (fr is typed against en)
theme.ts + index.css   the tokens (CSS vars; dark default, .theme-light)
```

Bundle: entry ≈ 226 kB gz (react / locales / socket / table vendor chunks preloaded);
Monaco, xlsx, recharts, markdown are lazy chunks (`vite.config.ts` `manualChunks` — Vite's
preload helper is pinned to the `react` chunk on purpose, see the comment there).

## The three applications (`../liberty-apps`, private)

`config/` holds the six TOMLs plus `actions.toml`, `theme.toml`, `presets/` (Screen Creation
Assistant catalogs — JDE Address Book so far); `plugins/{nomasx1,nomajde,nomaflow}` the
Python callables, Alembic migrations (nomasx1) and `jobs.toml`; `docs/DEPLOYMENT.md` and
`docs/NOMAFLOW.md` the operator guides. Shipped as a **wheel** (`build-wheel.sh`,
`liberty-apps install`) overlaid on the public image by `release/install-apps.sh`;
distribution of the wheel is the license gate (`licensed = true` connectors need a key).
Dev: `export LIBERTY_APPS_DIR=$PWD/../liberty-apps/config`.

**Release notes and version, every time.** liberty-apps keeps its own notes in
`liberty_apps/RELEASE.md` (EN) + `RELEASE.fr.md` (FR), served at `/admin/release-notes`. Every
new feature or fix in liberty-apps gets a line under the top `## <version> — unreleased` section
in BOTH files, in the same commit, and the version in `pyproject.toml` and
`liberty_apps/__init__.py::__version__` is bumped to that section's number (the three stay
aligned; the heading's date is set when the wheel is built). The framework has the same rule
with `liberty/RELEASE.md` / `RELEASE.fr.md` and `pyproject.toml` (the release workflow refuses a
push to `main` without a real section for the version).

## Run it

```bash
python3.12 -m venv .venv && .venv/bin/pip install -c requirements.txt -e ".[dev]"   # the lock = what the image runs
./start.sh init-db            # first run: auth store + an `admin` user (prints the password)
./start.sh                    # build frontend/dist if stale, serve SPA + API on :8000
./start.sh dev                # --reload   ·   ./start.sh frontend → Vite :5173 with API proxy
.venv/bin/pytest -q           # ~100 s, no live DB needed   ·   .venv/bin/ruff check liberty tests
(cd frontend && npm ci && npm run lint && npm test && npm run build)   # eslint · vitest · tsc + vite
# CLIs: liberty-next · liberty-connectors list|describe|run · liberty-admin init-db|create-user [--attribute k=v]|set-attributes|
#       create-role [--scope RULE]|set-scope|list-roles|…|migrate [--dry-run]|record-upgrade
#       liberty-ops status|doctor|start|stop|restart|logs|backup|restore|migrate|upgrade|rollback (Python installs; Docker twin = release/liberty)
#       (migrate = each plugins/<app>/db upgrade_schema / pending_revisions; journaled as <app>-schema)
#       liberty-migrate (v1 → v2, incl. `diff`) · liberty-crypto encrypt|decrypt · liberty-license verify|status
# Dev env (typical): LIBERTY_JWT_SECRET (≥ 32 bytes), LIBERTY_MASTER_KEY, LIBERTY_APPS_DIR,
#                    LIBERTY_APP_CONFIG=config/app-dev.toml, ANTHROPIC_API_KEY
```

## Branches, CI, releases

- `develop` is the working branch; `main` is release-only. CI (`.github/workflows/ci.yml`)
  runs ruff + pytest and `npm ci` + eslint + vitest + the build on every push to develop and PR
  to main.
- A push to `main` runs `release.yml`: the CI checks → version resolution (auto-bumps the
  patch when `pyproject.toml`'s version is already tagged) → **a real `## <version>` section
  in `liberty/RELEASE.md` is mandatory** (the run fails on a missing or placeholder one — so
  write the notes under the *next* version, EN + FR, before merging) → wheel → multi-arch
  image → git tag → PyPI → GitHub release. The bot commits the bump to main; fast-forward
  `develop` afterwards.
- Dependabot: weekly grouped PRs (pip, npm, actions, docker) against develop.

## Conventions worth knowing

- **Reload**: never call `load_connectors` directly from a route or handler — go through
  `liberty.runtime.rebuild_runtime` so every pool holder (auth backend, changesets DB, user
  views, nomaflow job DB + executors, AI tools, reports) is rebound.
- **Tokens**: HS256 via PyJWT; access 1 h / refresh 14 d; refresh tokens rotate (60 s reuse
  grace for racing tabs). Login throttle: 10 failures / 5 min per user, 5× per address.
- **Streaming**: SELECTs stream as NDJSON (`?_stream=1`); writes are plain POSTs.
- **Single-process assumptions**: APScheduler's in-memory jobstore and the Socket.IO lock
  rooms are per process — run one replica (PLAN D3 before scaling out).
- **Audit**: `Screen.audit_table` mirrors writes into `AUD_<table>` in the same transaction.
- **Framework tables** (nomaflow run history, change packages, saved views, optional `ly2_*` auth):
  every store's `create_schema()` goes through `liberty.schema_sync.sync_tables` — `create_all` plus
  forward-adding the model columns a live table lacks — and returns the changes, journaled as
  `framework-schema` via `runtime.journal_schema`. Adding a column to a model is therefore enough;
  a rename or type change needs a one-off script. The apps' schemas use Alembic (`liberty-admin migrate`).
- **Dependencies are locked**: `requirements.txt` is the pip-compile lock of `.[all]` (image, CI and
  dev venv install with `-c requirements.txt`); refresh it with `pip-compile --extra all
  --strip-extras --output-file requirements.txt pyproject.toml` and drop the `liberty-next`
  self-reference line it emits. Base / service images are version-pinned (Dependabot bumps them).
- **The container runs as uid 1000** (`liberty`): the entrypoint starts as root, fixes the ownership
  of the mounts, drops privileges; `docker exec -u liberty` for anything that writes.
- **Optional features fail clearly**: PDF without the `reports` extra → 503 with the install
  hint; `ldap_sync` without `ldap3` → a StepFailed naming the extra.
- **i18n**: every `t('key')` needs the key in both locale files; ~700 keys still exist only
  as inline English defaults in the newer builders (PLAN C10).

## Reference

- nomaubl (the model): `../../JavaProjects/nomaubl` — `src/custom/ubl/api/{ApiConnectorClient,
  SqlConnectorClient}.java`, `src/custom/ubl/web/AiAssistant.java`,
  `src/custom/ubl/auth/AuthManager.java`, `src/web-react/src/{common,pages/Explorer}`.
- History: `docs/history/CLAUDE-2026-05-phases.md` (the full build narrative up to 7.0.26).
- Working docs (local, git-ignored): `docs/work/PLAN.md` (the roadmap — read it at the start
  of a session), `docs/work/REVIEW-2026-10.md`, `docs/work/shared-components.md`.
