# Liberty Next — Release notes

## 7.0.59 — unreleased

**Install / upgrade**
- **Fixed: the apps bundle version read as "not installed" on Docker.** The wheel is materialised
  into the `/apps` mount, not installed as a package in the running container, so `liberty status`,
  the upgrade report and the server's own upgrade journal never saw it. The version now comes from
  the bundle manifest the installer writes (`.bundled/manifest.json`), with the package metadata as
  the fallback on pipx installs.
- **New `liberty-admin migrate --app X --stamp REV`** — sets an app's Alembic version without running
  anything, then applies what became pending (`--dry-run` lists it). The repair for a database whose
  version table is ahead of its real schema. (Cause fixed on the apps side: `init_schema` used to
  stamp head on an existing database, marking revisions as applied that never ran — it now stamps
  only a fresh database.)
- The upgrade report's "Image tag" no longer shows "-" for an unpinned install.

**Notifications (first part): the inbox and the dispatcher**
- A notifications inbox (`ly2_notification` on `[notify] pool`, created at startup like the other
  framework tables) and a dispatcher with three channels: **portal** (an inbox row per recipient,
  pushed at once to the user's Socket.IO room), **email** (SMTP from `[notify]` in `app.toml`:
  host, port, user, an `ENC:` password, from, STARTTLS or implicit TLS) and **action** (a
  connector query or endpoint run with the message's values, for a webhook or a log table).
  Recipients: `user:<name>`, `role:<name>` (every active holder), `email:<address>`. Subject,
  body and link take `{placeholders}` from the event's values. A channel that fails is logged and
  reported, never blocks the others. Routes: `GET /api/notifications` (`?unread=1`),
  `GET /api/notifications/unread_count`, `POST /api/notifications/read`, and for a superuser
  `POST /admin/notify/test` to try the channels.
- **Rules** in `notifications.toml` (`[notifications.<id>]`, hot-reloaded): a trigger, the
  channels, the recipients, a subject, a body and a link with `{placeholders}`. Three triggers.
  A **screen** event: an insert, update or delete through a screen, with an optional `when` on
  the written row in the `field = value` shape ("STATUS = closed"); the placeholders are the
  row's columns plus `USER`, `APP`, `SCREEN`, `EVENT`. A **job** event: `failure`, `succeeded`,
  `canceled`, or `long_run` with `long_run_minutes`, for every job or a list; placeholders `JOB`,
  `RUN_ID`, `STATE`, `ERROR`, `DURATION_MINUTES`, `ROWS`. A job's own `alerts.recipients` get a
  failure message even without a rule. An **action**: a new `send_notification` step in a
  screen's chains fires the rule server-side with the row's values; only a rule declared with
  `kind = "action"` can be fired that way, and the rule keeps the channels and recipients.
  Dispatch runs on its own task, so a write or a run never waits on a mail server. The bell, the
  inbox page and the rules editor come next.

**Screens: `editable_when`, a row-level read-only rule**
- A screen can say when an existing row may be changed: `editable_when = [{ field = "PERIOD_STATUS",
  value = ["open", "validated"] }]` keeps the rows of a closed period read-only. The grid's edit mode
  leaves such rows plain and shows a lock instead of the delete cross, the dialog opens them view-only,
  and the server refuses an update or delete whose original row fails the rule (409), including a
  request that leaves the rule's field out. New rows are not concerned. Rules AND together, values
  compare trimmed, like `disabled_when`. Set in the screen editor's General tab.

**Dashboards: a period**
- A dashboard can carry a `period`: a date range control in its bar, with the usual presets
  (today, yesterday, last 7 / 30 days, this / last month, this quarter, this / last year, last 12
  months, all dates), a two-month calendar for a free range, and, when the dashboard lists several
  date bases ("Document date", "Paid on"), the basis picker. The dashboard opens on
  `period.default`. Every widget query receives the range as the `:PERIOD_FROM` / `:PERIOD_TO`
  binds (bound as dates), plus `:PREV_FROM` / `:PREV_TO` for the period before (year to date compares with last year
  to date, a month with the previous month to date, a whole quarter with the previous quarter,
  anything else with the same length just before) and `:PERIOD_BASIS`; a query that does not
  mention them is left alone ("All dates" binds 0001-01-01 to 9999-12-31). When the period names the date column (`column`, or `dd` matched
  through the screens), a widget whose screen carries it is range-filtered server-side without a
  change to its SQL — the same wrap the filters use, now with `<COL>_from` / `<COL>_to` bounds on
  any hinted column. The dashboard editor has a Period block. The date range control and the
  floating panel are shared components for the lists and the explorer to come.
- App-level defaults in Settings → App: `default_period` (the preset a dashboard opens on when it
  names none; a pick lasts as long as the dashboard tab is open),
  `default_page_size` (the grid's initial page size; the SELECT row cap stays on the pool,
  connector and screen) and `dashboard_cache_seconds`. A reopened dashboard reuses each widget's
  result for that long; the bar's Refresh button bypasses it and says when the data was fetched.
- A KPI widget with `compare_previous = true` also runs its query on the period before and shows
  the change (in percent, or in points for a percent KPI, in green or red) and the previous value
  under the number. The widget editor has the checkbox.

**nomaflow: `file_load` step — a spreadsheet or CSV into a table, replayable by partition**
- What the grid's Excel import is not: a load that runs without a person at the keyboard (a
  schedule, a chain of steps) on the volumes a facts table takes, that can be replayed for one
  period without touching the others, and that leaves a trace. The step takes a `path` (with
  `${params.x}` from the run parameters and `${run.id}`), a `sheet`, the `header_row`, the CSV
  `delimiter` (detected when blank), a `target` connector / table, `columns` mapping target
  columns to file headers or `#positions` (the rest match by name), `constants` for the columns the
  file does not carry (`period = "${params.period}"`, `batch_id = "${run.id}"`), `load_mode =
  "append" | "replace_partition"` with `partition_by`, and `on_error = "fail" | "skip"`.
- The file is streamed, never loaded whole; cells are coerced to the target column's type
  (integers, decimals with either separator, dates in the usual shapes, booleans, text — a code the
  spreadsheet turned into a number comes back as text). `replace_partition` deletes the partitions
  the load carries (from the constants, else the distinct values found in the file) and inserts the
  rows in **one transaction**: a failed load leaves the previous data in place, a corrected month
  replaces that month only. The run log and the step result say rows read, inserted, rejected,
  partitions replaced and rows deleted, with the first rejects quoted. The step editor has its form.
- Three ways to name the file. The step's `path`; a file picked in the Run-with-parameters modal,
  uploaded to the job's work area and handed to the step for that fire (it wins over the path); or
  a `watch_dir` + `watch_pattern`, where every matching file is loaded in name order, each in its
  own transaction, then moved to `done/` or `failed/` under a time-stamped name — the scheduled
  "drop the file on the share" case. Rejected rows land in a CSV (file, row, reason) the run detail
  offers for download. `[jobs] files_dir` names the work area (default `<config dir>/.nomaflow/files`),
  `max_upload_mb` caps an upload.
- Two guards. `refuse_query`, a named query run before anything is touched, with the run
  parameters and the constants as binds: a row back refuses the load and its first column is the
  reason ("period 2026-02 is closed"). `control_column` + `control_total`: the column is summed
  over the loaded rows and compared, within `control_tolerance`, with a value, a parameter, or a
  cell of the file (`@Totals!B2`); a mismatch rolls that file's load back and names both numbers.

**Row-level data scope (first part)**
- A role can now restrict **which rows** its holders see, on top of which queries they may run:
  `[roles.<r>] scope = [{dd = "CO", values = ["00001"]}, …]` in `auth.toml` (the DB backend keeps
  the same list in `ly2_roles.scope`). A rule pins a dictionary item (`dd`) or one result column
  (`column`) to a set of values — literal, or taken from a user attribute (`source =
  "#USER.company#"`; users carry free-form `attributes`). Roles OR together, columns AND within a
  role; a role without scope grants everything; superusers bypass. `mode` decides what a query
  without the item does: `ignore` (unrestricted on that axis) or `deny` (no rows). `exclude = true`
  turns the list into a black-list (`SY NOT IN ('00')`, nulls kept). A rule may name
  its `connector`; elsewhere it says nothing. The scope is resolved at login and rides in the token
  — no database hit per request.
- The `dd` rule is the one that secures an item across tables. A result column carries the item
  when the screen's hint says so, when it is named like it, or — on a connector with the new
  `dd_prefix` (JDE: `dd_prefix = 2`, so `ABCO`, `MCCO`, `SDCO` all carry `CO`) — when its name
  minus the table prefix is the item. Every matching column of a query is restricted (a join of
  F0101 and F0006 binds both `ABCO` and `MCCO`), and no screen hint is needed per column. Text
  columns follow the filters' dialect rules: with the pool's `trim_strings` (JDE CHAR / NCHAR,
  blank-padded) the column is compared trimmed and the values stripped.
- Enforced in one place, the SQL connector: every SELECT (grid, stream, summary, export, dashboards,
  the AI assistant's queries, reports) is wrapped as `SELECT * FROM (<sql>) lib_rls WHERE …` with
  real binds, and an update or delete whose row lies outside the scope is refused (403). `/auth/me`
  reports the attributes and the resolved scope.
- **Roles are edited in a panel, on a menu tree.** Settings → Access → Roles shows the role next to
  the list instead of a modal full of permission strings. The role is the sidebar's own tree, one
  block per app, with inherit / allow / deny on every node: a folder cascades, a leaf shows what it
  runs, and an eye tells whether the sidebar will show it for a holder of the role, globs included.
  "Full access minus the Security folder" is the baseline plus one deny on the folder; "that folder
  only" is the empty baseline plus one allow. Rules menus don't cover (a whole connector, a query,
  an endpoint, a dashboard, the assistant, an expert pattern) sit below the tree. The strings
  written are the ones the previous editor wrote, so existing roles open as they are and nothing
  changes server-side.
- **Data scope in the role editor, attributes on the user.** The role panel has a Data scope tab:
  one line per rule ("Company (CO) in 00001, 00050", "SY not in 00", "CO = user.company"), with a
  form that picks the item from the dictionary, or a plain column name for a column that has no
  item (suggested from the screens of the connector), the values from the item's
  enum or lookup when it has one, the user attribute to read instead, exclusion, what a query
  without the item returns, and an optional connector. The user form gets a "Data-scope
  attributes" list (`company = 00001, 00050`, a comma makes a list; keys suggested from the roles'
  rules), and the profile shows the user's attributes and resolved scope in words.
- Setting it up without the UI: `liberty-admin create-role <r> --scope '{dd =
  "CO", source = "#USER.company#", mode = "deny"}'`, `set-scope <r> --rule … | --clear`,
  `list-roles`, `create-user --attribute company=00001,00050`, `set-attributes <u> KEY=VALUE…
  [--replace]` (`KEY=` removes); the same through `PUT /admin/access/roles/{name}` (`scope`) and
  the users' `attributes`. With OIDC, `[oidc] attribute_claims = { company = "company", pole =
  "department" }` fills the attributes from the ID token at every login (a mapped claim that
  disappears removes its attribute; attributes set locally are kept).

## 7.0.58 — 2026-10-27

**Install / upgrade**
- **Fixed: the image could not open its own default database.** `aiosqlite`, the driver behind the
  shipped `sqlite+aiosqlite://` default pool (and the Light layout), was a development-only
  dependency since the first release. The process still booted (the TOML auth backend needs no
  database), but change packages, saved views and nomaflow were silently disabled and `/ready`
  answered 503 on every Light install. The driver is now a runtime dependency.
- **Fixed: re-running `install.sh` (the documented way to switch TLS) or `docker compose -f
  docker-compose.full.yml pull` dropped the TLS and apps overlays** from the compose chain, so the
  container came back without `/apps` and seeded the empty templates — the install looked wiped.
  `install.sh` now reuses the `COMPOSE_FILE` recorded in `.env`; only `--ssl` swaps the TLS overlay.
- **New `release/liberty` command** — the one entry point for a Docker install: `install`, `start`,
  `stop`, `restart`, `status`, `doctor`, `ssl letsencrypt|provided|off`, `apps <wheel>`, `upgrade
  [--tag] [--apps]`, `backup`, `restore <dir>`, `logs`, `migrate`. The compose chain is state in
  `.env`, changed only by `ssl` and `apps`. `doctor` lists what is wrong and how to fix it;
  `upgrade` = doctor → backup gate → pull and recreate → schema migrations → status.
- **New `liberty-admin migrate [--dry-run]`** — applies (or lists) every app plugin's pending
  Alembic revisions (`plugins/<app>/db`), journaled under Settings → History → Upgrades. Replaces
  the manual run of the `nomasx1-upgrade-schema-1` job after an apps upgrade.
- `liberty-admin` now verifies the license key like the server does; before, every admin command
  silently dropped the licensed connectors.
- **`liberty upgrade` writes a report and `liberty rollback` undoes an upgrade.** Each run records
  what happened (versions before / after, image tag, compose chain, backup directory, doctor
  findings, every step's outcome, the schema migrations applied) as JSON + Markdown under the
  config volume (`.versions/upgrades/`) and journals it in Settings → History → Upgrades next to the
  version bumps (`liberty-admin record-upgrade`). Before pulling, the running image is kept under a
  local `rollback-<stamp>` tag; `liberty rollback` restores the pre-upgrade snapshot and re-runs
  that image.
- The container's healthcheck now probes `/ready` (the framework database answers) instead of
  `/info` (the process is up): a stack whose database is unreachable is reported `unhealthy`.
- The container warns in its log at boot when app schema migrations are pending;
  `LIBERTY_AUTO_MIGRATE=1` applies them at boot.
- **Fixed: a release that adds a column to one of the framework's own tables no longer breaks
  upgraded installs.** The nomaflow run history, change packages, saved views and the optional
  `ly2_*` auth tables were created with `create_all`, which never alters an existing table (change
  packages carried a hand-written `ALTER TABLE` list for exactly that). The stores now compare
  their models with the live tables at boot and add the missing columns, with the model's default
  so existing rows get a value; what was added is journaled as a `framework-schema` row in
  Settings → History → Upgrades, next to the apps' migrations.
- **Fixed: upgrading the licensed apps no longer loses operator edits — or misses product
  additions.** `liberty-apps install` copied whole files: without `--force-config` an existing file
  was kept as is (a new product screen, menu item or dictionary entry never reached the install);
  with it every file was replaced (your edits in screens, menus, dictionary, dashboards, charts and
  actions were lost — the `override` flag was ignored, only `connectors.toml` kept its secrets). The
  installer now merges **per entity**, with the rules Settings → Package & Deploy already applied:
  product additions are added; `override = true` entities, your own entities and entities you
  changed are kept; vendor entities you never touched are refreshed; what the vendor withdrew is
  removed only when untouched. `[pools.*]` and every `ENC:` secret always come from your file,
  `theme.toml` is yours. The shipment is recorded under `<config>/.bundled/` so the next upgrade
  can tell "untouched" from "edited". `--force-config` now means "also refresh vendor entities I
  changed without flagging them"; `--dry-run` prints the plan, and `liberty apps` / `liberty upgrade
  --apps` show it before writing. Entities kept because they changed locally are listed for review.
  (`liberty.config_merge`, shared with Package & Deploy.)
- **Hardening of the shipped layouts.** The container runs the app as the `liberty` user (uid
  1000) instead of root — it starts as root only to fix the ownership of the config volume and
  the apps mount of an older install, then drops privileges (`LIBERTY_RUN_AS_ROOT=1` opts out).
  Postgres is published on `127.0.0.1` only (`POSTGRES_BIND=0.0.0.0` opens it). The Traefik
  dashboard gets a random password per install (`liberty dashboard-password` regenerates it;
  `doctor` flags the historic default). The TLS overlays redirect HTTP to HTTPS and send
  security headers. Every `LIBERTY_*` / `POSTGRES_*` / `PGADMIN_*` variable also reads from
  `<NAME>_FILE` (Docker / Swarm / Kubernetes secrets).
- **Supply chain.** Python dependencies are pinned in `requirements.txt` (a pip-compile lock shared
  by the image, CI and the dev venv — the lock equals what the 7.0.57 image already ran); base
  images and the compose services are version-pinned and bumped by Dependabot; the release
  attaches an SBOM and provenance to the image and runs Trivy (fails on fixable CRITICAL / HIGH).
  The Oracle Instant Client is fetched from Oracle at image build time against a pinned SHA-256
  instead of 146 MB of zips in git (a local copy still works for air-gapped builds).
- **Request ids and JSON logs.** Every response carries `X-Request-ID` (the caller's or a fresh
  one) and every log line written while handling it carries the same id; `[app] log_format =
  "json"` emits one JSON object per line.
- **New `liberty-ops` command for Python installs** (pipx or venv, with or without systemd) — the
  twin of `release/liberty`: `status`, `doctor`, `start` / `stop` / `restart` / `logs`, `backup` /
  `restore` (config dir, apps dir, SQLite file, `pg_dump`), `migrate`, `upgrade [--version X]
  [--apps WHEEL]` (doctor → backup gate → `pipx upgrade` or pinned install → apps wheel →
  migrations → restart → report → status) and `rollback` (previous version + the pre-upgrade
  backup). Same report in Settings → History → Upgrades as the Docker path.

## 7.0.57 — 2026-10-26

**Reload**
- **Fixed: a config reload left some subsystems on the previous pool registry.** Change packages,
  saved grid views, the nomaflow job database and (on the file-watcher path) the auth backend kept
  the registry from before the reload and, because a disposed registry silently rebuilt engines
  from its old configuration, kept running with the previous pool credentials until a restart.
  Every reload path (Settings → Reload, the file watcher, a license rotation) now goes through one
  `rebuild_runtime` that re-points every component and closes the old registry for good.
- The framework now boots when the default database is down at startup (nomaflow is disabled for
  that run with a warning instead of blocking the whole app). New `GET /ready` readiness probe:
  `SELECT 1` on the default pool, 503 when it fails; `/health` stays a pure liveness check.

**Security**
- **DB errors no longer echo the SQL statement and the bound values** to regular users: they get the
  driver message plus an `error_id`; superusers keep the full text. The statement and binds are
  written to the server log only when the pool's `debug_sql` is on.
- **Refresh tokens rotate.** Each `/auth/refresh` returns a new refresh token and the one presented
  is consumed; presenting it again more than `[auth] refresh_reuse_grace` seconds later (default
  60 s — two browser tabs racing the same stored token) is refused. API clients must store the
  refresh token from every reply (the web app already does).
- **Login throttle**: after `[auth] login_max_failures` failed logins for a username inside
  `login_window_seconds` (default 10 in 5 min; 5× that per client address), `/auth/login` answers
  429 with `Retry-After`. `login_max_failures = 0` disables it.
- JWTs are signed with PyJWT (python-jose, unmaintained, is gone — same claims and secret, existing
  sessions keep working). A `LIBERTY_JWT_SECRET` shorter than 32 bytes is flagged once at startup.
- Anonymous `/info` reports counts and flags only; connector / pool names, the auth store location
  and the AI model show to authenticated callers.
- `POST /admin/import-package`: 50 MB upload cap and a 200 MB unpacked-size guard.

**Install**
- **Optional extras.** WeasyPrint (PDF reports), ldap3 (the `ldap_sync` job step) and Alembic +
  psycopg2 (the apps' schema migrations) are no longer core dependencies: install
  `liberty-next[all]` (or `[reports]` / `[ldap]` / `[alembic]`). The Docker image ships every extra
  unchanged. A core-only install boots without Pango; a PDF request then answers 503 with the
  install hint and markdown reports keep working.
- **Fixed: Package & Deploy upload never worked outside a venv that happened to carry
  `python-multipart`** — the dependency is now declared, and the upload check that rejected every
  real file as "missing 'package' file part" is corrected.

**Web app**
- **Faster first load**: the Monaco editor left the startup bundle (816 → 226 kB gzipped before the
  login page paints); it loads the first time a SQL editor opens.
- A rendering error in one tab no longer blanks the whole app: the tab shows the error with a
  Reload button and the other tabs keep working.
- The AI assistant reply can be **stopped** mid-stream; what was received is kept.
- The AI Assistant button follows the theme (a proper light-mode variant).

**Docs**
- `CLAUDE.md` is now a short map of the codebase; the full build narrative moved to
  `docs/history/`.

## 7.0.56 — 2026-10-26

**Connectors**
- **Fixed:** a hot reload of `connectors.toml` / `dictionary.toml` rebuilt the Oracle pools as *thin*
  engines (the file watcher didn't carry `[app] oracle_thick` into the rebuilt registry), so Native
  Network Encryption connections failed after any config edit until the next restart.

**Build, release and tests**
- **CI gates every release.** A new CI workflow runs the backend test suite and the frontend
  TypeScript check + production build on every push to `develop` and every pull request; the
  release workflow now waits for it, so nothing reaches PyPI or the Docker registry red.
- **Release notes are mandatory.** A release stops with a clear error when `RELEASE.md` has no
  section for the version being published (the previous behaviour inserted an empty placeholder —
  7.0.55 shipped that way). The git tag is now created *before* the PyPI publish.
- **Frontend lockfile repaired.** `package-lock.json` carried an inconsistent `yaml` entry that
  npm 10 rejected in the image build; it is regenerated and the image (and CI) now install with
  `npm ci`, which fails loudly if the lockfile ever drifts again.
- The health-endpoint tests no longer depend on the developer machine's local configuration.
- Dependabot keeps Python, npm, GitHub Actions and Docker base-image dependencies current
  (weekly, grouped, against `develop`).

**Docs**
- Removed references to planning documents that no longer exist (`docs/PLAN.md`,
  `docs/PHASE13.md`) and to a `liberty-apps/Dockerfile` that never did; the nomaflow operating
  guide lives in the apps repo (`docs/NOMAFLOW.md`).

## 7.0.55 — 2026-07-16

**Connectors — Oracle thick mode (Native Network Encryption)**
- **App-wide `[app] oracle_thick`.** A customer enabled Oracle Native Network Encryption, which
  python-oracledb's thin (pure-Python) mode can't negotiate (`DPY-3001`). When the flag is on, every
  Oracle pool is built on the **thick** OCI client (the Instant Client bundled in the image) instead
  of the thin async driver — `ThickAsyncEngine` presents the same `AsyncEngine` surface the framework
  uses (execute / stream / begin / dispose) over a sync SQLAlchemy engine, running each connection's
  DB calls on its own single-thread executor (oracledb connections aren't thread-safe). Postgres
  pools are untouched.
- The flag is **process-wide, not per pool**: `init_oracle_client()` is global to the process, so
  thick vs thin is all-or-nothing. It's exposed in Settings → App; a restart applies it.

## 7.0.54 — 2026-07-14

**Connectors — multi-environment (one connector, many pools)**
- **A SQL connector can now run against several pools.** Declare `pools = ["…"]` alongside the
  default `pool`, and the *same* queries + screens run against any of them — one connector serving
  multiple DB / JDE instances, no cloning of connectors or duplicating screens. The pool is chosen
  at runtime:
  - **UI** — the app switcher shows a **Pool** picker when the current app spans a multi-pool
    connector; picking a pool re-runs its screens against that instance. The choice rides on the
    `X-Liberty-Pool` header and is applied only to connectors that actually have that pool (an app's
    screens hit several connectors — a single-pool one keeps its default rather than erroring). The
    pick is per app and persisted.
  - **Jobs** — `sql_copy` / `sql_query` steps take `source_pool` / `target_pool` to target a
    specific instance without new connectors.
- The connector's `pool` (default) and `pools` (extra) are edited from a **dropdown of configured
  pools** in Settings → Connectors → Settings…, instead of typing names.
- Fully back-compatible: a connector with only `pool` behaves exactly as before. The allowed set is
  `[pool] + pools`; a request/job can't reach a pool outside it. Pool *names* are exposed to the UI
  picker — never the pool URL or credentials.

## 7.0.53 — 2026-07-14

**nomaflow**
- **Schedulable presets.** A job preset (a saved set of run parameters) can now carry its own
  cron + timezone. The scheduler fires the job on that schedule with the preset's params /
  op_kwargs / step toggles / log level — so one job runs on several schedules with different
  parameters, without cloning. Each preset has its own **enable** toggle, **independent of the
  job's `enabled`**: an enabled preset fires even when the job's own schedule is off (the job's
  `enabled` gates only the job's own cron). The Schedule view now lists one row per schedule (the
  job's cron plus each preset's), and a scheduled-but-disabled trigger is marked so it's clear
  why it isn't firing.
- **Name a clone; delete a job.** Duplicating a job now prompts for the new id (the editor's id is
  immutable, so the old auto `<id>_copy` couldn't be renamed). A **Delete** action removes a job
  from `jobs.toml` (config only — past run history is kept).
- **Fixed** the Run-with-parameters modal overflowing horizontally and pushing the "Save as
  preset" button off-screen.

**Docker**
- **pgAdmin binds to IPv4.** Recent `dpage/pgadmin4` images default the gunicorn listen address to
  `[::]` (IPv6); on a host with IPv6 disabled that fails to bind (`Address family not supported by
  protocol`) and pgAdmin never starts. The full-stack compose now sets
  `PGADMIN_LISTEN_ADDRESS=0.0.0.0`.

## 7.0.52 — 2026-07-05

**Docker**
- **Oracle thick-mode client now loads on the current base image.** The bundled Instant Client
  needs `libaio.so.1`, but Debian trixie ships libaio as `libaio1t64` providing `libaio.so.1t64`
  (the time_t package rename) — so `init_oracle_client()` failed with `DPI-1047: libaio.so.1:
  cannot open shared object file` even though the client and libaio were both present. The image
  now installs libaio in its own layer (no `--no-install-recommends`, which had skipped the real
  package) and adds a `libaio.so.1 → libaio.so.1t64` compat symlink — ABI-identical on the 64-bit
  arches we build, so thick-mode LOB-over-dblink fetches work out of the box with no manual step.
- **Release notes now bundled in the image.** `.dockerignore` excluded `**/*.md`, which stripped
  `RELEASE.md` / `RELEASE.fr.md` from the build context before they could be packaged — so
  Settings → Release notes showed "No release notes bundled with this build" in the container
  (it worked from source on a dev box). The two release-note files are now re-included.

## 7.0.51 — 2026-07-05

**Connectors**
- **Thick-mode fetch for LOBs over a database link.** python-oracledb's async (thin) mode can't
  fetch a LOB across a DB link (fails with ORA-22992 / ORA-03149 — the LOB comes back as a remote
  locator). `liberty.connectors.thick.fetch_thick(pools, pool, sql)` runs such a query in a
  short-lived subprocess that turns on the thick (OCI) client *there and only there*, returning the
  rows (LOBs materialised as bytes) to the async caller — the main app stays thin/async. The Oracle
  **Instant Client is now bundled in the image** (amd64 + arm64), and `PoolRegistry.oracle_connect_params`
  exposes a pool's decrypted DSN/creds for it. Use only where thin genuinely can't (a LOB SELECT over
  a dblink); everything else stays on the async driver.

## 7.0.50 — 2026-07-04

**Connectors**
- **Per-schema DB links (`#DBLINK.<NAME>#`)** — a pool can now map a DB-link suffix per schema
  (`[pools.X] dblinks = { SY = "@ORCLPROD", … }`), appended to a table *after* its
  `#SCHEMA.<NAME>#` prefix (`…F0092#DBLINK.SY#` → `SY920.F0092@ORCLPROD`). An unmapped or empty
  token drops out, so the same query runs locally where the schema isn't remote; a non-empty value
  must be a bare `@link` reference (config-injection guard).

**nomaflow**
- **Plugin log lines now reach the UI run log.** The per-run `RunLogHandler` was only bound to the
  `liberty.*` logger tree, so a plugin namespace registered before startup (e.g. `nomasx1`) had its
  records appear on stdout but never in the UI run log. `install()` now binds *every* registered
  namespace, honouring the deferred-attach contract.
- **Configurable run-log buffer** — `[jobs] run_log_max_lines` (default 5000) caps the per-run log
  ring buffer; raise it for jobs that legitimately emit tens of thousands of lines (e.g. a per-table
  ETL over thousands of tables) so the run's head isn't dropped.

## 7.0.49 — 2026-06-21

**Docs**
- French release notes (`RELEASE.fr.md`) brought up to date through 7.0.48 — native French entries
  for 7.0.39–7.0.48 (the file had been frozen at 7.0.27).

## 7.0.48 — 2026-06-21

**Screens**
- **`default_when` lock flag** — a conditional default can now seed a value without locking the
  field. `lock = false` fills the value when the condition becomes active but leaves it editable
  (e.g. default a lookup column to `*ALL` while the operator can still narrow it); `lock` omitted /
  `true` keeps the previous force-and-disable behaviour.

**Settings**
- Release notes render at the panel's text scale (the changelog no longer shows oversized headings).

## 7.0.47 — 2026-06-15

**Dialog**
- Hidden / non-field **key columns now rebind correctly** on save — the edit dialog snapshots every
  loaded column for the `_put`'s `:<COL>_ORIGINAL` binds (not just the rendered fields), so a row
  whose key is a hidden column updates the right record.
- **Dirty-detection** is scoped to the editable fields again, so cancelling a dialog without changes
  no longer pops the "unsaved changes" prompt.

**Wizards**
- The CRUD generator emits **upper-cased** column identifiers and bind names (matching the SELECT
  wizard) — JDE columns come out uppercase.
- The lookup / sequence generator no longer requires a table: you can **write the SQL directly** and
  still "Use this query".

**SQL / errors**
- Database errors split the **SQL and bound parameters into readable blocks** (one bind per line) in
  the expandable error detail.
- Colons inside string literals are escaped before SQLAlchemy parses binds, so a literal like
  `':0'` is no longer mistaken for a bind parameter.

## 7.0.46 — 2026-06-15

**Auth**
- **Silent token refresh** — the access token is refreshed automatically (a proactive timer plus a
  refresh-and-retry on any 401), so an active session no longer drops roughly every hour.

**Errors**
- Raw database errors collapse to a **concise summary with an expandable detail** in the screen
  dialog and the grid bulk-edit / proxy banners, instead of dumping the whole driver message.

**Dialog**
- Disabled **BOOLEAN / ENUM** fields render as a read-only checkbox / label (matching the grid),
  not the raw stored code.

## 7.0.45 — 2026-06-14

**Screen Creation Assistant**
- Build a screen from an **existing connector query** (read-only reuse), not only a physical table;
  query-backed catalog presets reuse the query instead of generating a duplicate.
- The **Grid view** step (the default shared view) is separated from the optional **Dialog columns**
  step; the read query selects every column and the view/dialog choose what's shown.
- Created screens set **`auto_load`** and the workspace refreshes after scaffolding, so the new
  screen opens without a manual browser reload.
- Column pickers: **tree-style parent-menu** picker, **collapsible + searchable** source-table
  groups, full-height scroll panes, per-table "add all", and loading spinners.

## 7.0.44 — 2026-06-13

**Audit trail**
- **In-flight value diff** — a row expands to its field-level BEFORE / AFTER values parsed on demand
  from the stored DML statement, so the source values table can be purged and still reconstructed.
- **Purge** and **rebuild-values** jobs to manage the audit history.
- Summary-view UX refinements (native expandable sub-rows in place of a nested grid).

## 7.0.43 — 2026-06-13

**Grid**
- **Saved grid views** — named shared views plus per-user views (columns, sort, grouping, page
  size), available from the grid's view picker.
- **Summary view** — server-aggregated parent rows (accurate counts over the full set) with a
  chevron that lazy-loads the underlying rows; per-day / month / year bucketing.

**Filters & jobs**
- Day-grain filtering on timestamp columns; a **WARNING** job run level.

## 7.0.42 — 2026-06-13

**Security**
- **PASSWORD-ruled columns** are encrypted at rest (AES-GCM `ENC:`), and a blank password on an
  UPDATE preserves the stored secret instead of overwriting it.

## 7.0.41 — 2026-06-13

**Reports**
- **Run-form dropdowns** — report parameters can declare `options`, and the run dialog renders a
  searchable select instead of a free input. Choices resolve server-side (a static list, the
  configured connectors, a connector's schemas, or a named connector query mapped to value/label),
  and cascade — e.g. an Application picker that lists apps by name on the chosen connector.

## 7.0.40 — 2026-06-12

**Docs**
- README: upgrade history & release notes, `release/upgrade.sh`, and a failed-publish recovery note
  (incl. the GitHub-App workflow-file tag-push limitation).

## 7.0.39 — 2026-06-12

**Config history**
- **Upgrade history** tracks software version changes on startup — framework **and** licensed apps,
  independently — surfaced under Settings → History → Upgrades with the release notes inline.
- **Release notes** shipped in the wheel (`RELEASE.md` / `.fr`), served per component.

## 7.0.27 — 2026-06-12

**Screen Creation Assistant**
- New superuser **Screen Creation Assistant** (sidebar, above Monitoring) — a guided wizard that turns
  picked tables into a live screen + dialog + menu in one pass: tables & joins, columns → tabs,
  dictionary review, and menu placement.
- **Catalog presets** — operator-managed table presets (`config/presets/`), browsable and searchable;
  selecting one pre-wires the joins. Ships a JD Edwards Address Book catalog.

**Dictionary**
- Shared scan table between the Dictionary editor and the assistant: existing entries are detected and
  only missing ones are proposed, with Format/Rule dropdowns, rule value, **UDC lookup params**, and
  default.

**Config history**
- **Upgrade history** — Settings → History → Upgrades records each application version change.
- **Release notes** — this view.

## 7.0.26 — 2026-06-01

- Screen + dependency **bundle versioning** (Phase 2): every screen save snapshots its dependency
  closure; restore reverts the screen *with* its queries/lookups/dictionary entries.
- **Retention purge** job for config-version snapshots; per-version and per-file delete.
- Read-only screen mode; move a field between dialog tabs from the inspector.
