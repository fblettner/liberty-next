# Liberty Next — Release notes

## 7.0.67 — unreleased

- Development: end-to-end regression tests (`frontend/e2e/`, Playwright, `npm run e2e`) drive the data screens on a
  live server and check what they save in the database — the record form (add on several tabs, duplicate, delete),
  bulk edit (add, change, clone, delete), the lifecycle actions and the row menu; test records only, never run in CI.
- Record lock: a record opened in another tab or by another user now opens read-only with the "locked by" banner even
  when this tab connected after the lock was taken. The server refused the second lock, but the browser only knew of
  the locks taken while it was connected, so the form stayed editable.
- Visual Designer: a save rewrites only the screens that changed. Every other screen keeps its table as it is in
  `screens.toml` (the keys the model ignores — a screen-level `l` —, an explicit `key_columns`), and the edited one is
  written without its default values, in the file's key order — editing one label changes one line. The save used to
  write every default of every screen (the file grew by 15 %: 30 724 → 35 300 lines on the apps' file) and dropped the
  keys the model ignores.
- Internal (no change of behaviour): `liberty/web/admin.py` (3 800 lines) is the package `liberty/web/admin/`, one
  module per section (versions, builders, assistant, module catalogues, theme, jobs, app settings, usages / packages,
  proposals, clone, delete, rename…) — the routes register in the same order, every name stays importable from
  `liberty.web.admin`; the SQL connector's pure text helpers (ORDER BY split, aggregate / filter wraps, schema / dblink
  placeholders) are `liberty/connectors/sqltext.py`. Frontend: the SQL Wizard's parser and generator are
  `services/sqlParse.ts`, the data grid's column helpers `resultColumns.ts` and its inline-edit cell `EditCell.tsx` (all
  with tests), and the builders share their page frame, toolbar and empty line (`common/builderShell.ts`).

## 7.0.66 — unreleased

- Developer reference → Impact: every configuration entity (pools, connectors, queries, API endpoints, dictionary
  items, screens, menu items, charts, dashboards, shared actions, Explorer models, jobs, notification rules, AI
  blocks) with what it uses and what uses it, followed through — what a change reaches, counted by kind (a query →
  its screens → the menu items opening them, the charts → their dashboards, the jobs and actions running it); the
  used-by tree, the direct uses and everything the entity needs; a node re-centres the view (with Back), "Open in
  editor" goes to its builder; the entities nothing points at (entry points excepted) and the references to
  undefined entities are listed (`/admin/dev/impact`). The links are the dependency walk of Package & Deploy.
- Package & Deploy / promotion: a menu item now carries its target — the walk followed only the v1 item types
  (table / form / chart), so a `screen`, `query`, `endpoint` or `job` item reached nothing but its connector; a
  dashboard named `<app>.<id>` (as menus name it) resolves in its app instead of the first app holding that id
  (`overview` exists in several); a notification rule carries the screen or the jobs its trigger names.
- Developer reference → Command line: every command the package installs, read from its entry points and its
  argument parser — what it does, its usage (copy), the options of every command, each sub-command with its arguments
  (value, default, choices, required, repeatable); left, the commands with a search (`/admin/dev/cli`). The technical
  report's command-line section is built from it and now lists each command's arguments. `liberty-crypto` and
  `liberty-license` build their parser in `build_parser()` like the other commands.
- Developer reference → Impact: the groups on the left start folded, with Expand all / Collapse all; a search or the
  unused filter opens the groups holding matches.
- API documentation: the responses of the main read routes — connectors (list, describe, run a query, call an
  endpoint, schemas, import), menus, screens, the external addresses, the Data Explorer (models, a model's fields,
  aggregate and detail queries, saved analyses / dashboards) and nomaflow (jobs, run now, runs, a run's detail) — are
  documented (`/docs`, Developer
  reference → API: the Response section). Documentation only: declared beside the error responses, the routes return
  exactly what they did; a test validates each route's JSON against its model so the documentation cannot drift.
- Developer reference → Command line: the scripts on the left start folded, with Expand all / Collapse all.
- `liberty-crypto` no longer has a `decrypt` command: a secret is never printed back in clear from the command line
  (a terminal, a log, the shell history holding `--master-key`). Nothing used it — the server, the pools and the
  jobs (nomasx1's LDAP and audit-trail steps reading `settings_applications`) decrypt in their own process; `encrypt`
  and `is-encrypted` stay. To change a secret, encrypt the new value.
- Documentation reports: every report built is kept on the server (`<config dir>/.reports/`, next to `.versions/`, so a
  backup carries it) with who built it, the language and the sections; the report's page lists its kept copies to
  download again or delete (`/admin/dev/report-copies`), the Retention page shows their number and size. A copy
  that cannot be written never blocks the download.
- Developer reference → API: a route's request / response example is no longer squeezed to one line in its detail
  (the block shrank when the detail scrolled); the same for the error list of the grid's Excel import.

## 7.0.65 — unreleased

- Developer reference (a new page in the sidebar, superuser; documentation, nothing is edited there):
  every setting of app.toml, read from the running code — sections on the left with what is not in
  the UI yet, the grid on the right (type, the screen that edits it or why it stays in the file /
  the environment, the modules that read it, default; filters, export), and a row opens the detail
  with each line of code that reads it (`/admin/dev/config`).
- Settings → App: every setting of app.toml is now set from the screen — new sections Server log
  (console format, the file on disk or off, its size and rotation), Sign-in (accounts store and
  pool, token issuer and lifetimes, sign-in throttle), nomaflow (on / off, pool, run log lines,
  work area, largest upload) and Change packages (on / off, pool, retention); the sub-path
  (`base_path`), the OIDC attributes read from claims and the session secret (stored encrypted), the
  Explorer tools of the assistant, the inbox pool. What stays in the file — the paths of the
  configuration files, the signing algorithm, the master key and the JWT secret — is listed
  read-only with the reason. A test now fails when a new setting is neither editable nor explained.
- Settings → App: editing only the notification settings now enables Save (they were left out of
  the unsaved-changes check).
- Developer reference → API: every route of the running server — method, path, summary, the module
  it belongs to (by category: Access, Workspace, Features, nomaflow & deployment, Settings,
  Operations, Assistant) and the access it requires, read from the code (public, any signed-in user,
  superuser, or the permission it checks such as `sql:{connector}:{query}`); a route opens on its
  handler (file:line), description, parameters, request (its fields and an example JSON) and
  response (from the declared models); links to /docs and /redoc (`/admin/dev/api`).
  Configuration now shows each setting's label as in its screen.
- Developer reference → Data model: the framework's own tables (accounts, nomaflow, change packages,
  saved views, notifications — from their models: columns, types, keys, references) and each
  connector's declared tables — the physical table its read query selects from, its key, its queries,
  the screens / dashboards / charts / jobs / Explorer models that use it, the related tables (the screens' nested tables,
  clickable) and its columns with the dictionary's labels and the screens showing them
  (a table no screen shows — a dashboard's query — reads its columns from its SQL's select list)
  (`/admin/dev/data`). Configuration: the header counts what is edited in a screen, what stays in
  the file / environment and what is not in the UI, and a status filter replaces the checkbox.
- The Liberty Next workspace (superuser): the framework's pages are no longer listed under every application's menu —
  they form a workspace of their own, picked in the application switcher like an application or with the new button
  of the top bar (which returns to the last application). The Settings page is split: each editor is its own page
  (`/liberty/<page>`, every one with the "reload the configuration" button), grouped by purpose — Build, in the order an
  application is made (screen assistant, connectors, dictionary, screens, menus, actions, charts, dashboards, Explorer
  models, report templates, notification rules, AI, integrity), Administer (app, pools, access, theme), Operate (monitoring, jobs), Deploy (package,
  changes, history), Document (developer reference, documentation reports, release notes). Package, Changes and
  Integrity leave the nomaflow application (liberty-apps 0.1.3). The former `/settings?tab=…` and `/nomaflow/package |
  changes | integrity` links redirect to the new pages; the editors open on the last application used; messages that
  pointed to "Settings → …" name the new place.
- Liberty Next → Document → Documentation reports (superuser): the implementation report and the technical report,
  picked from two cards; the sections to include, the language, the cover's date label, PDF or Markdown (`/admin/dev/reports`). The
  technical report documents the release from the running code (architecture, data model, configuration reference
  with its gaps, API with the access of every route, command lines, packaging, code references); the implementation
  report documents this install (enabled components, applications and menus, screens, connectors and pools with their
  load state, dashboards / charts / Explorer models, jobs, notification rules, roles and users, dictionary, settings
  and API as appendices), secrets masked.
- PDF reports: a table with long identifiers (query names, paths) no longer runs past the right margin —
  the cells wrap them after `_`, `.` or `/`. Developer reference → Configuration and the technical report
  show a default path as `${LIBERTY_APPS_DIR}/…` instead of the absolute path of the server.
- Installable as an app (PWA): Chrome / Edge offer "Install" and Safari "Add to Dock" — Liberty Next opens in its
  own window, named after the instance (the theme's app name). The service worker caches nothing: every request goes
  to the server as in the browser, so a release is used on the next load and no data stays on the device. Needs
  HTTPS (or localhost); works under `[app] base_path`. Icons 192 / 512 px, maskable and Apple touch.
- nomaflow without superuser: `nomaflow:view` follows the jobs (list, schedule, runs with their log and files),
  `job:<id>` (`job:*` for all) runs a job, uploads its file and cancels its runs — the permission a job menu item already
  asks — and `nomaflow:edit` creates, edits, deletes, pauses and resumes jobs and applies the change
  (`POST /admin/jobs/reload`, the jobs part of the reload); edit includes view. The retention / missed-schedule
  policies, their manual runs and the job export stay with a superuser. Granted in Access → Other access → nomaflow; the
  nomaflow menu pages show only to who holds view (or a menu allow), and the nomaflow application appears in the picker
  with its connector (`sql:nomaflow:*`). Editing jobs is a strong grant: their steps run SQL and allow-listed Python
  without row scope. The Run-with-parameters dialog reads only the job run (`GET /admin/jobs/{id}/definition`).
- Module catalogues (catalogue v2, step 1): a `[[modules]]` block beside the presets describes a whole application
  module — one screen per table with its key, grid columns, dialog tabs and detail tabs (the child screen and the key
  it inherits), and the menu folder — columns named by data item, the JDE prefix resolved later from the live schema
  (`liberty/modules.py`: model, loader, validation of the detail graph). Apply a module
  (`/admin/assistant/modules`, `…/{id}/preview`, `…/{id}/apply`, superuser): the preview reads the live tables and
  says, per screen, the prefix found, the columns, the data items the table lacks, what is new and what exists; the
  apply writes the new read queries (`<screen>_get`, ordered by the key), the read-only screens with their detail tabs
  (filtered on the inherited key, hidden), the menu folder and items — additively, what exists is never overwritten,
  the three files validated before any is written — then the dictionary entries of its tables under the app's scope
  (JDE labels, formats, UDC lookups) and a reload. A table or a key column not found blocks it. A detail can be
  one-to-one (`cardinality = "one"`: the child's form as a tab, not a grid); a screen can join a lookup table for a
  few columns (`[[modules.screens.joins]]` — the address's name on a customer list), placed in the module's column
  order; `filters` names the columns of the advanced filter panel (filtered in the database before the rows are
  loaded, joined columns included); `?replace=1` rewrites the module's own queries, screens and menu items in place.
- A `read_only` screen with a dialog opens it on a row again (view-only: fields disabled, no Save / Delete, its
  detail tabs read-only) — the grid only opened the dialog when the screen had an update or insert query.
- Screen Assistant → A module: the module catalogue (search, groups), the preview read on the live tables (per
  screen, as a tree of its details: table, prefix, columns, data items the table lacks, new / exists / replace; the menu;
  what blocks it), Replace with its warning, Apply — the sidebar refreshed, then Open the module's first screen.
- Detail tabs (nested table / form) take their link values from the whole loaded row, not only from the dialog's
  fields — a link column the screen keeps out of the form (a detail's key) was dropped, and the tab then filtered on
  part of its key and showed other records' rows (Who's Who → Email listed every address's line-1 email); a tab whose
  link is incomplete now loads nothing.
- A `read_only` screen takes no record lock (it writes nothing; its dialog showed "Locked by …" to the next reader).
- A read-only lookup field shows its description beside the code, and a grid lookup cell finds its description when
  the stored code is shorter than the JDE UDC key (`"C"` against `"       C"`) — the trim-tolerant match the dropdown
  already applied (`matchOption`).
- A shared grid view that lists a LOOKUP / ENUM column keeps its description column right after it (`<col>__lookup`),
  unless the view places it itself — a view naming the code hid the label the grid shows beside it by default.
- Apply module: the grid's columns go in a default view; the dialog-only columns are no longer `hidden` (the dialog
  dropped a hidden column's field — the Flags / Category codes / Related addresses tabs were empty).
- `open_url` action: open an external address from a row menu, a toolbar button, a dialog tab, a drill column or a
  chain — `url` is a template (`{COLUMN}` from the row / form, prompt fields, chain paths, URL-encoded; `#LOGIN_USER#`
  and the other built-ins; a leading placeholder is the address itself), `new_tab`
  (default on), the prompt-before-fire of `navigate`. Only http(s) addresses and paths of this server are opened —
  refused at load and checked again on the filled URL. A lone `open_url` in a row menu fires from the row handle. A
  `url` menu item opens an address in a new tab (shown unless the menu denies it). The action editors offer it.
- Liberty Next → Administer → App: the `[app]` settings in three groups — Server (name, host, port, log level, hot
  reload, Oracle thick mode, base path), Display (default language and period, page size, dashboard cache, tabs,
  drill-downs) and External addresses; app.toml unchanged. The page lists its sections in alphabetical order
  (of the current language), the read-only deployment list last.
- External addresses (`[app] external_urls`, Liberty Next → Administer → App): named addresses of the systems an
  install links to — `JDE_WEB = "https://jde:8080/jde"` — used as `#URL.JDE_WEB#` at the start of an `open_url` / a
  `url` menu item, so shipped screens carry no customer address; served to signed-in users (`/api/external-urls`),
  live on save. An address the install did not set leaves no URL (refused, never opened as a local path).
- Module catalogues: a screen's `row_menu` (as in screens.toml); an `open_url`'s `{ITEM}` placeholder names a data item
  and becomes the physical column (`{AN8}` → `{ABAN8}`).
- nomaflow job editor: saving a job rewrites only the jobs that changed. The editor receives every job with its
  `${…}` references resolved, and a save wrote them all back — one server's paths (`${LIBERTY_APPS_DIR}/…` turned into
  `/srv/…`) landed in every other job of the file; the unchanged jobs now keep their text as it is on disk (comments,
  `${…}`, `step_defaults`), and the edited job gets its `${…}` references back where the value still matches.

## 7.0.64 — unreleased

- Package & Deploy: an import can be previewed (`/admin/import-package?dry_run=true`): the plan
  lists every entity — new, changed (with its fields before / after), identical, kept (`override
  = true` here), skipped, removed — with warnings when the package comes from a newer framework or
  carries `ENC:` values this instance's master key can't decrypt. An apply returns the same plan.
- Package & Deploy: a package now also carries shared actions, Data Explorer models (with their
  analyses), notification rules, nomaflow jobs and an application's AI starters and playbooks.
  A job that already exists on the target keeps its schedule, enabled flag, timezone, presets,
  alerts, retry, log level and timeout; a new job arrives disabled.
- Package & Deploy: a package is built from the files as written (unset values and `${VAR}`
  references kept), and a connector's queries, tables, sequences, lookups and endpoints are
  imported one by one — importing a package no longer replaces a whole connector (its other
  queries were lost), and a screen's queries ship even when its connector isn't selected.
  `connectors.toml` and `jobs.toml` keep their comments through an import.
- Settings → Package: Import previews the package as soon as it is picked (and again when the
  strategy changes): counts per state, the warnings, then each file's entities — what changes
  first, identical ones on demand — and a changed entity opens on its fields, here now / after the
  import; "Apply N change(s)" then writes it and shows the result. Build offers the new kinds:
  Explorer models, nomaflow jobs, notification rules, shared actions, the assistant per application.
- Promotion to another instance (API; the screens follow): `[promotion.targets.<name>]` in
  app.toml — the target's URL (with its sub-path) and a local superuser account there, the password
  stored encrypted (`/admin/promotion/targets`, a sign-in test per target). `POST
  /admin/promotion/push` builds a config package (the Package selection; connectors, pools and API
  endpoints stay the target's unless picked) or takes an approved change package, signs in on the
  target and returns its preview — or applies it there. An applied promotion is kept in a history
  (`<config>/.promotion/`, `/admin/promotion/history`) and marks the change package exported.
- "Promote to…" on Settings → Package (Build) and on an approved change package (nomaflow →
  Changes): pick a target instance — it previews at once (the package's plan with its diffs, or the
  change package's report where a conflict or an unverifiable change can be forced) — then "Apply on
  <target>". Package → Promotion edits the target instances (Test signs in there) and lists the
  promotions applied from here; a row opens on the target's report.
- Changes → Apply: a change whose drift can't be verified (no read query) can be forced — it never
  applied before, the force box only showed on conflicts.
- Deployment: the database can live on a PostgreSQL server outside the stack. At install,
  `./liberty install full --db external --db-host H [--db-port P] [--db-user U] [--db-name D]
  [--db-password-env VAR]` starts no bundled Postgres; later, `./liberty db external --host H …`
  moves an install (the app's default pool rewritten, the pools that named the bundled server —
  the apps' — moved, the bundled Postgres stopped with its volume kept) and `./liberty db
  embedded` comes back. Recorded in the compose chain (`docker-compose.external-db.yml`,
  `docker-compose.no-embedded-db.yml`) and `.env`, so start / upgrade / backup follow it;
  `./liberty db dump` takes a `pg_dumpall` of the bundled Postgres to carry the data over;
  `status`, `doctor` and `backup` show where the database is. New `liberty-admin set-default-pool`
  and `retarget-pools`.

## 7.0.63 — unreleased

- Monitoring → Server log: the server log is now kept on disk — a size-rotated JSON-lines file
  (`[app] log_file`, default `<config dir>/.logs/liberty.log`; `log_file_max_mb` 10,
  `log_file_backups` 5) fed by the framework, the apps and the libraries' warnings, whatever
  launches the server; a burst of the same message is written once with "repeated N times". The
  page searches it (level, module, text in the message / logger / exception / request id,
  period), opens an exception, filters on one request, exports the matches as text and downloads
  the files; the levels per module change at once and are saved to `[app] log_levels`
  (`/admin/logs`, superuser). The live view stays as the first tab.
- Monitoring → Retention: every store the server keeps growing in one place — job runs, job
  files, notifications, config versions, change packages, saved views, the server log — with
  what it keeps, where that is set, its size, a Preview (a dry run: what would go, nothing
  deleted) and Run now (`/admin/retention`, superuser). Change packages get a policy:
  `[changesets] retention_days` (0 = keep, the default) deletes exported / promoted packages and
  import logs older than that, checked with the notifications retention every six hours; drafts
  are never touched. Saved views are the users' own work: shown, never purged.
- Sidebar: "Open on hover" — the menu folds to its icons and floats over the page while the mouse
  is on it (picking a page folds it back); "Keep open" returns to the fixed menu; remembered per
  browser.
- Menus: a start page per role — `home_by_role = { auditor = "<menu item>" }` on an app's
  connector: a user lands on the item of their first role listed there, else on `home`.
- An installable web app manifest (`/manifest.webmanifest`, public) named after the instance (the
  theme's app name, else `[app] name`).
- Profile: no Security tab for a single-sign-on session (no local password).
- Dropdowns: options with a group sit under a header per group — the Data Explorer's models per
  preset module ("JDE · Accounts Payable": a preset's `group`, or a model's own `group`), else per
  application (the closed picker shows the group too), a data-scope rule's columns per
  connector; an optional × clears; a compact size. A multi-value dropdown (chips on the trigger, "Select shown", Clear),
  first used for a notification rule's jobs.
- A placeholder picker beside templated fields: the { } button lists the tokens and inserts the
  one picked at the caret — a notification rule's subject, body and link (the job or screen
  event's fields), a notify step's subject and body.
- Grid column filters: a coded column (an enum) picks several values at once and takes a code
  typed beyond its list; a lookup column keeps its single picker. A saved view holding one value
  still opens.
- Grids: `wrap = true` on a screen column wraps long text over several lines instead of cutting
  it; a screen's `page_size` sets its grid's rows per page (else `[app] default_page_size`); the
  rows-per-page picker is the themed dropdown.
- Views menu: "Reset to shipped" brings the grid back to the screen's default shared view, else its
  base layout.
- Serving under a sub-path behind a reverse proxy: `[app] base_path = "/liberty"` (restart) — the
  page, its assets, the API, downloads, the live socket and the links resolve under it, whether
  the proxy strips the prefix or not; the URLs the server builds (the single sign-on callback)
  carry it. Every download and upload now goes through the API client (the session refresh
  applies).
- Fix: nomaflow → Jobs → Export downloads the job's package again (it called a wrong address
  without the session and did nothing).
- Monitoring, Settings → Package and Settings → Access pick their views from a row of cards — an
  icon, the name and one line on what the view is for — instead of plain tabs.

## 7.0.62 — unreleased

- nomaflow: the spreadsheet / CSV uploaded for a run can be downloaded again — under the
  `file_load` step on Run detail, and on the user's job page ("File loaded"). Uploads are kept in
  the job's work area; `GET /admin/jobs/runs/{run}/inputs/{step}` and
  `GET /api/jobs/{job}/runs/{run}/inputs/{step}` serve them.
- nomaflow: the retention sweep now also deletes the job work area's old files — uploads, rejects
  reports, reports, exports, and the file_load watched folders' `done/` / `failed/` archives —
  older than `[meta.retention] files_days` (blank = the runs' `days`, 30 by default; 0 = keep the
  files). **On upgrade, files older than 30 days go at the first sweep** unless `files_days` is
  set; an export's copy in its `export_dir` is never touched. Set it in the Retention panel; the
  sweep reports the files and bytes deleted. Saving the retention policy no longer fails on a
  blank field.

## 7.0.61 — unreleased

- Jobs: a plugin report step (`report:<scope>:<id>`) takes the run's job params and user
  parameters on top of its own `params`, typed by the report's declared parameters.
- Dashboards: the page grows and scrolls to its end; a full-width links card shows all its links;
  more sidebar icons for the app menus.
- Jobs: a user parameter without `step` is a job-level parameter, passed to every step; its
  choices can come from a read query (`choices_from = "query:<connector>/<query>:<value>:<label>"`).
  The admin "Run with parameters" dialog offers the same dropdowns.
- nomaflow: the jobs list is searchable (id, description, tag), filtered by application, status
  and tag (kept per browser), grouped by application with collapsible groups, one line per job
  with icon actions.
- Jobs: a `job` menu item can pin one of the job's presets (`params = { preset = "…" }`); the
  user's page runs it with that preset's parameters and step switches.
- nomaflow: the Schedule page lists the upcoming runs day by day with the schedule in words
  ("every day at 03:00"), then the schedules switched off; the jobs run by hand only are folded
  away. No more "Back to jobs" button. The sidebar no longer highlights Jobs on the Schedule page.
- nomaflow: picking a preset in "Run with parameters" starts from the job's saved values, as the
  scheduler does; it no longer keeps the steps the previous preset switched off. Jobs tagged
  `main` are listed first, in a "Main jobs" group.
- Config merge: `merge_jobs` / `merge_jobs_file` merge a shipped `jobs.toml` per job — the
  product's steps refreshed, the operator's fields (enabled, schedule, timezone, presets, alerts,
  retry, log level, timeout, parameter values) kept; a job gets an `override` flag. Used by the
  apps installer.
- Jobs: `for_each = "<param>"` runs a job once per value when that parameter holds several (a
  list, `"1,3"`, or `"all"` = every choice of the user parameter of that name), one run after
  another, each with a single value; a scheduled fire or preset with `"all"` covers every value.
  A user parameter with `multiple = true` shows as checkboxes with "All", on the user's page and
  in "Run with parameters".
- nomaflow: a Job Runs page (`/nomaflow/runs`) replaces the generic grid: search, job, period
  and state filters (the state chips count the runs), runs grouped by day with their parameters
  (the application of a per-application run), trigger, duration, rows, the error under a failed
  run and a progress bar while one runs; it refreshes itself while a run is going. Backed by
  `GET /admin/jobs/runs`. The Run detail page loses its back buttons.
- Sidebar: a `shieldalert` menu icon.
- Connectors: a field may hold an `${ENV}` reference even when it is a boolean or a number
  (`verify_ssl = "${JDE_AIS_VERIFY_SSL:-true}"`): saving connectors in Settings, the delete with
  dependencies, the scaffold and the API test now validate the values as the runtime resolves
  them (they refused the file before). The API connector editor shows and keeps such a reference
  for the TLS check.
- Config merge (apps upgrade, Package & Deploy): a shipped value that is a whole `${VAR}` or
  `${VAR:-default}` reference belongs to the install — the live value wins, booleans, numbers and
  the install's own reference included (several AIS connections, each on its own variable). Before, a TLS check switched off in Settings was switched back on.
- Jobs: a deadline — `alerts.finish_by = "07:00"` (the job's timezone). On a day the job is
  scheduled, a successful run must have finished by then; otherwise the new `late` job event goes
  to the notification rules and the job's alert recipients, saying whether the run is still going,
  failed or never started. Checked with the missed schedules; set in the job editor (Alerts).
- Dashboards: chart and table widgets carry their title and description inside their card, like
  the other widgets.
- nomaflow: a reload no longer leaves the "All" of a per-value run on the previous connectors.
- Dashboards: a `jobs` widget — the nomaflow jobs (the ids listed, or every scheduled job, by
  tag) with the next fire or "paused", the schedule in words, the last state, and Run now /
  Pause / Resume. Superusers only (dropped for anyone else); the dashboard editor offers it.
- nomaflow: pausing or resuming a job (`POST /admin/jobs/{id}/enabled`) edits that job in
  jobs.toml and re-registers its schedule, without a full reload; the Jobs page uses it too. The
  job editor's Alerts block is laid out on a grid again.
- Jobs: a `file_export` step — a named read query written to an xlsx or csv file in the run's
  work area (the `notify` step attaches it, Run detail downloads it), optionally copied to
  `export_dir`. Binds from the run parameters and templated `params`; the file name and folder
  take `${params.x}` / `${run.id}`; the rows stream from the database, headers are the column
  labels. A form in the step editor.
- nomaflow: on a user's job page, the Run button lines up with the fields.

## 7.0.60 — unreleased

**Data Explorer: models shipped by the presets, dictionary entries in one click**
- A Screen Assistant preset carrying `app` + `explorer = true` ships a Data Explorer model by
  default (`explorer.<app>.<preset id>`): listed with the others, built from the preset at first
  use, never written to `explorer.toml`. Declare a model on the same preset to customise it. A
  preset table takes a `label` (the entity label) and a `cardinality` (`one` for a lookup joined to
  a transaction hub: a plain LEFT JOIN).
- The JDE catalogue ships eight new preset files — General Accounting, Accounts Payable, Accounts
  Receivable, Inventory, Sales, Manufacturing, Product Data Management, Product Costing — from the
  Oracle E1 data models; the fifteen "full" presets (Address Book included) ship their model.
- Applying a preset in Settings → Explorer creates the dictionary entries its tables lack (the
  assistant's Dictionary step, filed under the app: a JDE source gets its data dictionary
  descriptions). On the Explorer page a superuser sees how many columns show their raw name and
  creates the entries in one click (`POST /admin/explorer/{app}/{model}/dictionary`).

**JDE amounts: the data dictionary's decimals**
- New dictionary format `jdenumber` with a `decimals` entry field: a JD Edwards amount stored
  without its decimal point (F9210.FRCDEC) reads as the real value, writes back as the stored
  integer, and filters / explorer bounds are bound in real units; explorer sums and detail
  columns are scaled in SQL, the result columns carry `decimals` and the Explorer table shows
  exactly that many places. `decimals` on the other numeric formats is the places shown.
- The dictionary scan (the assistant, the standalone page, the explorer preset path) reads
  `DD_DECIMALS` from the per-table JDE query; the review tables get a Decimals column. The apps'
  `dictionary_data_items_for_table_get` now emits `jdenumber` / `jdedate` / `integer` types,
  the decimals, the size and the justify rule next to the UDC lookup rule.

**Jobs for users: a menu item that runs a job and shows its log**
- Menu item type `job` (target = the nomaflow job id, permission `job:<id>`, `!job:<id>` hides
  it): the leaf opens the job's own page — the description, one file picker per `file_load`
  step, Run, the run's log followed live, the previous runs with their state, duration and
  files. Routes under `/api/jobs/{id}` (the job, upload, run with the uploaded files only, a
  run's detail and files); nothing else of the nomaflow control plane is exposed. Settings →
  Menus offers the jobs as targets; Settings → Access has a Job rule family.
- The apps ship the menus: nomademo gets an Import folder (items, companies, a month of sales,
  refresh periods), nomasx1 a Jobs folder (collect security, collect licences), nomajde a Jobs
  folder (the two security re-merges).
- The nomaflow Overview dashboard takes a period (last 7 days by default): the figures, the
  runs per day, what needs attention, the runs and durations per job and the recent runs
  follow it; "In flight" stays live.
- The dictionary refresh of the Explorer models is also a job: `liberty.jobs.dictionary:
  refresh_model_dictionary` (`app`, optional `models`) scans every table of the app's models,
  writes the missing entries and rebuilds the connectors, with the tables and entries in the
  run's log. Python job steps receive the live runtime as `app_state` when they declare it.
  nomajde ships it as `nomajde-dictionary-refresh` under its Jobs menu.
- A job declares what a user may set when running it from its page: `[[jobs.user_params]]`
  (`name`, `label` + `l`, `step`, `default`, `required`, fixed `choices` or
  `choices_from = "explorer_models:<app>"`). The page shows a dropdown or a text input per
  parameter; the value reaches the step's parameters, anything else is refused. The nomajde
  dictionary refresh takes the Explorer model to refresh (blank = every model).

**Dashboards: text and links widgets — an overview for a management application**
- `text`: a block of Markdown (what the application is for, how to read the page); `links`: a
  card of entry points, each a menu item of the dashboard's app (shown only when the user can
  open it, under the item's label by default) or a URL. Both in the widget editor, both
  localisable, both exported (the text as it is, the links as a table).
- nomajde gets its Overview: what Nomajde does, the live figures of the JDE instance (users,
  roles, security records, UDC codes, objects) and the entry points of each domain; the app
  opens on it.

**AI assistant: the Data Explorer as tools**
- `explorer_models`, `explorer_fields` and `explorer_query` let the assistant answer a total,
  a ranking, a trend or a comparison through the Explorer models (dimensions, measures,
  filters, period, sort, Top N) under the caller's scope, instead of declaring that no
  aggregate query exists. On by default (`[ai] explorer_tools = false` to remove them).
- The assistant also saves what the user asks for: `explorer_saved`, `explorer_save_analysis` (the
  definition is run once before it is stored, private unless shared) and `explorer_save_dashboard`
  (tiles of saved analyses). The Explorer page publishes its context to the chat (the model, the
  analysis on the canvas), and `[ai.<app>.screens.explorer]` starters show there.

**Workspace tabs: an operator policy** (`[app]` in app.toml, Settings → App)
- `tabs_multi` (off: one page at a time, opening another replaces it), `tabs_close_on_app_switch`
  (the tabs close when the operator picks another application, the new home kept) and `tabs_max`
  (default 5; the oldest tab closes when the limit is reached).

**Grid: a visible handle for the row menu**
- Every row of a screen with `row_menu` actions carries a quiet handle in its first column (so it
  stays in view on a wide table), brighter on hover: a lone `navigate` action fires at once (the
  handle shows its label), several actions open the same menu as the right-click. Nothing else
  in the drill mechanics changes.
- In-place drill, opt-in: a `navigate` action with `inline = true` opens its target (a query or a
  screen, with the row's binds) as a panel inside the current tab, over the grid it came from, with
  a breadcrumb and a Back button; the grid behind keeps its rows and filters, and a drill from the
  panel nests the same way. `[app] drill_inline` (Settings → App) sets the default for every
  drill whose action does not say; an action's own `inline` overrides it either way. Off by
  default, so nothing changes until the operator turns it on.
- Link-style cells: a column hint `drill = "<row-menu action id>"` renders the value as a link
  that fires that `navigate` action for the row — the key column is the drill, no handle or menu
  needed. Automatic too: a screen with a single `navigate` action and a key column gets the
  link on its first key column without any setting. Settings → Screens offers the screen's
  row-menu actions in the field; a wrong id is refused at load.
- Dashboards: a list row's tag sits on the title line, so a row stays two lines high.

## 7.0.59 — unreleased

**Fixes from the documentation review (2026-10-27)**
- Dialog: a `required` field, or a `required_when` that holds, now blocks Save when empty (the
  asterisk was only decorative).
- Dialog → Duplicate: the related 1:1 rows (column groups) are inserted under the new key. Since
  June the copy re-seeded the source row behind the form, so the group ran an UPDATE on the new
  key (0 rows, silently) instead of its INSERT; the source key also reappeared in the form.
- Settings → Menus: a `dashboard` item's target dropdown lists the dashboards (by qualified id,
  the menu's own app first); it offered nothing.
- Parameters: a declared default or a supplied value reaches its bind whatever the case
  (`status` declared, `:STATUS` in the SQL).
- nomaflow: a cloned job's presets lose their schedules (they fired twice: the scheduler runs a
  preset's cron even on a disabled job); a job's `alerts.on_long_run_minutes` now sends the long-run
  message to its `alerts.recipients`.
- Notifications: `[notify] retention_days` purges read notifications (at boot and every six hours);
  the e-mail carries the rule's link; a rule recipient without prefix that is an address is sent to
  that address, another bare word is logged instead of silently dropped.
- Screen Assistant: the generated `_post` / `_put` write the JDE audit items (UPMJ, UPMT, USER,
  MUSE, PID, JOBN; never a key column) as `{{JDEDATE}}` / `{{JDETIME}}` / `{{LOGIN}}` / `{{PID}}` /
  `{{JOBN}}` tokens, the convention of the shipped queries — a bound audit column kept the value the
  dialog sent back on an update.
- `liberty-admin create-role` on an existing role keeps its permissions when `--permission` is not
  given; `set-scope` refuses to run without `--rule` or `--clear` (it silently cleared the scope).
- Settings → App: changing `oracle_thick` reports that a restart is needed (thick mode is set once
  per process). The Chart view's "Save…" uses the one chart id rule.
- The writable-query confirm showed the literal "table.run". The `ColumnHint.key` and
  `Screen.actions` descriptions, the connector's SYSDATE comment and the `liberty-admin` docstring
  say what the code does. `release/README.md`, `install.sh` and the Traefik config no longer
  mention `--license-key`, a read-only apps mount, `admin/admin` or `reset-admin-password`.

**AI assistant: the page context (C6)**
- The assistant now sees what you are looking at. While a data screen is the active tab, the
  chat carries its context with every message: the app, the screen (connector and read query),
  the active filters, the loaded rows' count and columns, and the row you last clicked or
  opened the row menu on. "This screen", "these rows" or "the selected row" resolve without
  retyping names; the query names in the context are the real ones, so the assistant can run
  them. A chip above the composer shows what is sent; its × drops it for that page. The context
  rides as a second, uncached system block capped at 4 000 characters, so the cached prompt and
  tool list are untouched (`POST /ai/chat` takes an optional `context`).
- **Starter questions, a model picker, a conversation that stays.** Settings → App → AI
  Assistant gains `starters`: questions shown on an empty conversation, everywhere (`*`), per
  app or per `app/screen` (the most specific set for the page wins; a click sends the question),
  and `allowed_models`: the models a user may pick in the chat besides the configured one (the
  picker shows only when there is a choice; the server refuses any other id). The conversation
  now survives closing the drawer and a reload (the last 40 messages, on this device); "new
  conversation" clears it. The App editor's dropdowns (model, effort, log level, the allowed
  connectors) are the shared searchable select; the model list adds the Claude 5 family.
- **The applications ship their own starter questions.** A new per-app file, `ai.toml`
  (`[ai.<app>] starters`, `[ai.<app>.screens.<id>] starters`), travels with the apps bundle like
  the other per-app files (merged on install, hot-reloaded, versioned) and is edited under
  Settings → AI. The operator's questions under App → AI Assistant win over it, key by key.
- **Playbooks, and an assistant that knows the installation.** `ai.toml` also carries
  `[ai.<app>.playbooks.<id>]` (a title, an optional description, the procedure in Markdown),
  edited under Settings → AI. When a user asks how to do something in an application, the
  assistant lists the playbooks, loads the one that fits and follows its steps, saying which one.
  A new `describe_instance` tool summarises what this installation holds (applications, menus,
  screens, dashboards, explorer models, jobs, playbooks), so "where do I find…" is answered from
  the real configuration. A tool result above 60 000 characters is cut with a note asking the
  assistant to narrow the call.
- **Operations tools** (`[ai] ops_tools`, off by default, a checkbox under App → AI Assistant):
  `jobs_status` (the nomaflow jobs with schedule, last run and next fire; one job's recent runs
  with the failing step), `server_log` (the recent log lines, by level and text) and
  `integrity_checks` (the configuration cross-reference checks). The assistant reads the running
  server instead of guessing when something failed, is late or is missing. Off by default because
  the log can carry what the server logged.
- **The Explorer's PDF is a synthesis, with its charts.** An analysis or a dashboard exported to
  PDF (from the page or by a `report` job step) now reads like nomaubl's reports: a cover with
  the application, the period, the date, the author and the version, then the sections flowing
  one after the other (no contents page, no page per section). Every chart is drawn
  server-side from the section's data (`liberty/explorer/chart_svg.py`: bar, grouped, stacked or
  horizontal, line, pie or donut) in print colours with a legend, compact axis ticks and the
  values on the bars — the same figure whether a user exports or a job renders a digest. KPIs
  become cards; the numbers follow the reader's language (`1 234,5` / `1,234.5`); the
  template's own words (cover labels, confidential lines) follow it too. Excel keeps the data
  only.
- **Every UI string in both languages (C10).** The 870-odd strings the newer builders carried
  only as inline English defaults (`t('key', 'Default')` and `defaultValue:` options) now live
  in `locales/en.ts` and `locales/fr.ts`, translated; the lint forbids a new inline default so
  the French file never silently misses a key. The assistant's starter questions take a
  language block in `ai.toml` (`[ai.<app>.l.fr]` with `starters`, `groups`, `screens`), served
  by `/ai/tools` in the request language; the three apps ship their French starters.
- **Settings → Dashboards says which widgets a filter reaches.** Under each filter, the editor
  lists the widgets whose screen carries the dictionary key or whose result has a column named
  like it (the dictionary bind), and the ones it misses.
- **Dashboards in the reader's language.** A dashboard, a filter and a widget take an `l`
  block of per-language overrides for their texts — `l.fr = { label = "…", description = "…",
  sub = "…", count_label = "…" }` — including the column a list reads a text from
  (`title_column = "TITLE_FR"`), so a query can carry a French column next to the English one.
  The routes and the export apply the block of the request language (`X-Liberty-Lang`,
  region stripped); the block itself never travels. The Explorer PDF's note under a section
  (period, filters, groups) follows the language too. The widget and chart editors show a
  configured column in the sample's spelling, so an upper-case name in the TOML matches the
  lower-case column Postgres returns.
- **Explorer: the pickers follow the model.** Switching models quickly (the first model, then
  the current app's) could leave another model's saved analyses in the pickers: a response for a
  model the page has left is now dropped, and a failed list empties the pickers. After a deploy
  replaces the bundle, a page that fails to load a lazy chunk reloads itself once instead of
  showing "Failed to fetch dynamically imported module".
- **Charts pivot on a column.** A chart spec takes a `series` column: one series per distinct
  value of it (a process, a status…), each the aggregate of the first `y` column — conflicts
  per refresh stacked by process from a three-column query, no pivot in SQL. In the chart
  editors as "Pivot by"; the dashboard export draws the same pivot. The widget editor dialog
  edits the three synthesis kinds (figure, list, bars) with their columns, formats and a live
  preview, and every widget's subtitle; a PDF card shows its sub line in small type under the
  figure.
- **Synthesis widgets for the dashboards.** Three widget kinds for a synthesis page like
  nomaubl's billing overview, all configuration: `stat` (a figure card from the query's first
  row: the value formatted with a unit, a sub line templated over the row — `"{ENABLED:integer}
  enabled · {DISABLED:integer} disabled"`), `list` (a decision or ranked list: title, subtitle,
  figure, count, a tone dot ok / warn / error / info, a tag pill) and `bars` (a ranked list with
  a proportion bar per row, as a share of the total or of the largest). Every widget takes a
  `description` subtitle. The card widgets carry their title inside the card; the dashboard
  export shows a stat as a card and a list or bars as a table. The visual editor adds the three
  kinds with their preview.
- **A menu link opens the Explorer on one model.** A `page` menu item may target
  `/explorer/<app>/<model>`: the tab opens the Data Explorer on that model (the picker still
  lets the user switch). The apps bundle uses it to put a "Data Explorer" entry in each domain
  folder (nomasx1: Security, Conflicts, Licenses; nomajde: Security, UDC, Development).
- **Dashboard filters and period on a query without a screen.** A dashboard filter (and the
  period's date column given as a dictionary item) used to bind only through a widget screen's
  `dd` map; a widget on a plain query ignored it. The page now sends the pick as a *dictionary
  bind* (`_dd_<ITEM>`, `_dd_<ITEM>_from` / `_to`) and the SQL connector binds it on the result
  column carrying the item — found as the data scope finds its columns: a hint's `dd`, the
  column's own name, or the name minus the connector's `dd_prefix` (`ABCO` → `CO`), from a
  one-time probe. An item no column carries is ignored, as before. The export does the same.
- **Dashboards export to Excel and PDF.** A config dashboard (`dashboards.toml`) has Excel and
  PDF buttons in its bar, next to Refresh. The export runs server-side with exactly what the bar
  holds — the filter picks bound through each widget's screen, the period's binds and date range,
  under the caller's row-level scope (`POST /api/dashboards/{id}/export`) — and goes through the
  same renderers as the Explorer: one sheet per widget (the KPIs together as "Key figures"), or a
  PDF synthesis whose cover names the period and the filters, with the charts drawn and the KPIs as
  cards. A widget whose query fails becomes an error section; the rest still renders.
- **The chat drawer on nomaubl's layout**: a header line (title, the model picker when there is a
  choice, "+" new conversation, ×), a context line under it when the page gives one, a greeting
  bubble, and the starter questions as chips above the composer, under headings when the app
  ships them (`ai.toml` `groups`, per app or per screen); the follow-ups take the chips' place
  after a reply. The dashboard's KPI card carries its label inside, the figure left-aligned, the
  detail under it, as nomaubl's.
- The chat drawer's top line keeps only what is actionable (the model picker when there is a
  choice, "new conversation"); a table in a reply scrolls sideways in a smaller font with each
  figure on one line. The assistant knows today's date (an uncached line with the page context),
  so "last month" no longer needs a guess.
- **Follow-up chips.** The assistant ends each reply with two or three questions the user is
  likely to ask next; the chat shows them as chips under the reply (a click sends one) and never
  shows the marker line the model wrote.
- The chat folds a turn's tool calls into one muted line ("3 tool calls", open to see them); a
  failed call keeps its message visible.
- Dropdowns: a long code (a model id) is no longer cut short in the list.
- A query ordered by an alias-qualified column (`ORDER BY s.company_name`) failed with "missing
  FROM-clause entry" as soon as a filter or a data scope wrapped it: the hoisted ORDER BY now
  drops the qualifiers.
- Data scope: a rule's values are bound as the dictionary item's type even when the result
  column comes back lower-cased (Postgres), so a company code scoped from a user attribute no
  longer fails with "integer = character varying" on a screen or in the Explorer.
- nomaflow `file_load`: with `partition_by`, the refuse query runs once per partition the file
  carries, with the partition columns as binds (`:YEAR`, `:MONTH`…), so a closed-period guard
  follows the file rather than a typed parameter.
- nomaflow: a step's database error reads as the driver's sentence ("invalid input for query argument
  $1: 2025 (expected str, got int)") instead of the exception class, dialect prefix, statement and
  bound values. The statement still goes to the server log.
- Charts: the Y axis shows compact ticks (1.2k, 27.5M) in a wider gutter; a large value was cut
  on the left ("0000" of "20,000,000"). The tooltip keeps the full number.
- Data Explorer: a chart in a dashboard tile (and on the page) stayed an empty frame when the
  layout settled after the chart measured its height; the chart now draws at the measured height.
- Data Explorer: a table entity's fields now carry their dictionary labels. The items were looked
  up under the data connector (`jdedwards`) while the dictionary files them under the app
  (`nomajde`), as the screens do; and a field with no label showed its lower-cased name next to
  its upper-cased one.

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
  Dispatch runs on its own task, so a write or a run never waits on a mail server.
- **The UI.** A bell in the header with the unread count, updated live through Socket.IO, and a
  dropdown of the latest notifications; a Notifications page (all / unread, mark all read; a row
  opens its link); Settings → App gets a Notifications section for the SMTP account (host, port,
  from, user, password encrypted at rest, STARTTLS or implicit TLS, retention); Settings →
  Notifications edits the rules: the trigger (a screen event with app, screen, events and the
  row condition; a job event with jobs, events and the long-run threshold; or "fired by a screen
  action"), the channels, the recipients picked from users and roles or typed as addresses, the
  subject / body / link templates, the action channel's target, and a "Send a test" button that
  dispatches the rule with sample values.
- **Missed schedules.** The scheduler keeps its cron triggers in memory, so a fire that falls
  while the server is down is never known, and a fire that lands while the previous run of the
  same job is still running is skipped: both left a silent hole in the run history. A periodic
  check (`[meta.missed]` in `jobs.toml`: `enabled`, `grace_minutes` 10, `check_interval_minutes`
  5, `lookback_hours` 24) now lists the instants each active trigger should have fired since the
  last check and looks for a run near each one; a gap is recorded as a **`MISSED`** run (visible in
  the Runs list and as the job's last run; recorded once, a restart never reports it twice) and
  raised as the new **`missed`** job event for the rules, with the `EXPECTED_AT` placeholder. A
  dropped concurrent fire is recorded the same way. A manual run started within the grace counts
  for the fire; a job that never ran is checked from the scheduler's start only. On the Jobs page
  a "Missed schedules" panel next to the retention one edits the policy, shows the last check and
  runs one ("Check now"); routes `GET /admin/jobs/missed`, `POST /admin/jobs/missed/check`.

**Data Explorer (first part): the model**
- `explorer.toml`, several models per app (`[explorer.<app>.<model>]` — one data mart each, tables that
  do not relate are different models): a **hub** entity and the entities around
  it, each a parameterless query of a connector with a `cardinality` (one row per hub row, or
  many), a `key`, a `default_date`, `measures` (column → default aggregate) and `hidden` columns;
  `relations` join entities on column pairs (`on = [["COMPANY", "COMPANY"]]`, any key, one or
  more hops to the hub). No SQL in the file: the **fields** are discovered from each entity
  query's result columns at request time, labelled, formatted and typed through the dictionary
  and the entity's `screen` column hints (a LOOKUP or ENUM rule becomes the field's reference
  list), classified as `string` / `number` / `date` / `datetime` / `boolean` and as dimension or
  measure; field names are upper-cased (`sales.QTY` on Postgres as on Oracle, the framework's
  convention) so an analysis moves between dialects. `GET /api/explorer` lists the models the caller may use (new permission
  `explorer:<app>:<model>`, `explorer:<app>:*` for all), `GET /api/explorer/<app>/<model>/model` returns the model with its fields
  (`?refresh=1` bypasses the per-app cache, which every reload empties); a broken entity is
  reported on the model, not raised. Settings → Explorer edits the model: the hub, the entities
  (the entity editor discovers the query's columns and lets the operator tick the key, the
  measures with their aggregate, the hidden ones and the default date) and the relations;
  `GET/PUT /admin/config/explorer/parsed`, hot-reloaded, part of the apps bundle merge. The
  Access editor offers "Data Explorer" as a rule type.
- **The engine (aggregate mode).** `POST /api/explorer/<app>/query` takes rows (dimensions, a date
  one with a `bucket` day / week / month / quarter / year), measures (`sum` / `avg` / `min` /
  `max` / `count` / `count_distinct` of a numeric field, or `count(*)`), a flat filter bag
  (`key`, `key__op`, `key__from` / `key__to`, a list = IN; operators by type), a period on a date
  field, a sort and a Top N, and runs **one** `SELECT dims, aggs … GROUP BY` that is the same on
  Oracle, Postgres and SQLite: the row sources are the primary entity (the hub, or `entity`) plus
  the entities of the dimensions, at most one of them many-side so rows never multiply twice;
  one-side entities are LEFT JOINed through the relations (any number of hops); a many-side
  entity that is not among the rows is reached by a correlated sub-query for a measure and by
  `EXISTS` for a filter. Every entity query runs with the caller's row-level scope applied inside
  it (`SQLConnector.scoped_select`). SQL groups at day level; Python folds the dates into the
  bucket, merges the groups, computes `avg` from sum + count, sorts and cuts to the limit
  (`groups`, `scanned`, `truncated` in the reply). A request the model cannot serve is a 422 that
  says why; `debug = true` returns the SQL and binds to a superuser.
- **Detail mode, drill, calculated fields.** `POST /api/explorer/<app>/detail` returns the rows
  themselves — the columns asked for (any entity, same join rules) or the primary entity's
  visible fields — with the same filters and period, a sort, `total` and a page (`page` /
  `page_size`, up to 5 000); `drill` takes an aggregate cell's dimension values and turns them
  into filters (a month label becomes the month's day range, a null group the empty rows), so
  the rows behind a figure are one call away. **Calculated fields** are declared in the request
  (`calculated = [{name, label, expr}]`) and used as `calc.<name>` in rows, measures, filters and
  columns: a small typed language (`REVENUE / QTY`, `QTY >= 4 ? 'big' : 'small'`, `company.REGION
  = 'EU'`, `year(SOLD_ON)`, `days(ORDERED, SHIPPED)`, `round`, `abs`, `coalesce`, `upper` /
  `lower` / `length` / `concat`, `in (…)`, `is empty`, `and` / `or` / `not`) compiled to the
  pool's dialect inside the same statement, never free SQL; a division is real and guarded by
  `NULLIF`, a boolean renders `Y` / `N`, a JDE Julian date is converted before a date function. A
  numeric result is a measure, any other a dimension; a type error names the position.
  `POST /api/explorer/<app>/calc/test` compiles one and samples eight values — the editor's
  "Test" before "Save".
- **The page (first part).** A Data Explorer page (`/explorer`, the compass in the header when the
  caller has a model; a `page` menu item can point at it too) with the model's **fields pane**
  (entities with their 1..1 / 1..n badge, dimensions then Σ measures, a search; a click adds the
  field, the funnel adds it as a filter, a drag drops it on a shelf; double-click makes an entity
  the analysed one), the **shelves** — Rows (a date with its bucket), Measures (an aggregate each,
  "+" for a row count, a dimension dropped there counts its distinct values) and Filters (an
  operator by type, an enum or lookup field picks from its values, a date gets from → to; chips
  reorder by drag) — a **period bar** (the date field, the shared presets and calendar), Top N,
  and the result as a table: measures as numbers, dimensions through their enum / lookup rule,
  sortable by header. The analysis re-runs on its own when its definition changes. **Detail**
  view lists the rows with the Rows shelf as columns, paged; a click on an aggregate row
  **drills** to the rows behind it (the primary's keys, the dimensions, the measures' fields),
  with the cell's values shown and a way back. A superuser can show the SQL.
- **The page (second part): pivot, charts, KPI, calculated fields, the record.** The result
  switches between views without a new query. The **pivot** builds the row dimensions into a
  collapsible hierarchy (expand / collapse all) with one dimension spread as columns (the last
  one, or the one picked), subtotals on the group rows for an additive measure only (a sum of
  averages is meaningless, so a non-additive one keeps leaf values), a share of the parent or of
  the total beside each value, a grand total, Excel export, and a drill from any leaf cell.
  **Bars, lines and pie** go through the shared chart canvas: x is the first dimension, the series
  the second one (or the measures), the top 10 series or slices plus "Others", dates in
  chronological order and other categories by value, a stacked option; **KPI** shows one card per
  measure with its total. The table and the detail export to Excel too. The fields pane gains the
  **calculated fields**: "New calculated field" opens the editor (name, label, the expression
  with insert-a-field and insert-a-function pickers, a Test that compiles it on the server and
  shows the type and eight sample values; Save needs a passing test), the field then sits in a
  `calc` entity of the pane as a measure or a dimension, with edit and delete. In detail view and
  after a drill, a row of an entity that has a **screen** opens that screen's dialog: the screen's
  read query is run with the row's key values and the record shows as it does anywhere else,
  edits included.
- **Saved analyses and dashboards of analyses.** One `AnalysisDefinition` object drives the
  editor, the dashboards and (next) the digest. The page gains a mode bar — **Analyses** and
  **Dashboards** — with a picker of the saved ones (grouped by audience, "mine" flagged), Save,
  Save as and Delete. An analysis or a dashboard is stored on the saved-views store (`ly2_user_view`,
  kinds `analysis` / `explorer_dashboard`, the app as key, the id a slug of the name): **private**
  under its owner, or **shared** under the reserved `*` user so everyone with the app's explorer
  permission sees it; the owner, the audience, a version and the `bundled` marker ride in the
  record. Saving a shared object changes it for everyone (the modal says so, "Save as" makes
  your own copy); deleting a shared one is for its owner or a superuser. A **dashboard** is a grid
  of tiles (a saved analysis each, 1–4 columns wide, three heights), each running its analysis in
  its own view, with a period that overrides the analyses that have a period field; "Edit
  layout" moves, resizes, removes and adds tiles; the pencil opens the analysis in the editor, a
  drill from a tile too, with the cell's values as filters. A **bundled catalogue** ships with the
  app: `[explorer.<app>.analyses.<id>]` / `.dashboards.<id>` in `explorer.toml`, each with a
  `version`, installed once as shared objects at boot and on reload, refreshed on a version bump
  only while nobody edited the copy (a user save drops the marker), and never reinstalled once a
  user deleted it (an installed-ids marker row remembers). Routes `GET / PUT
  /api/explorer/<app>/saved/<analyses|dashboards>`, `DELETE …/<id>`.
- **Export and the headless run.** `POST /api/explorer/<app>/export` renders an analysis (saved,
  or the one on the page) or a dashboard (one section per tile) to **xlsx** (one sheet per
  section, bold headers and subtotals, the period and filters as a note) or **pdf** (through the
  reports pipeline, with the install's branding: a title, one chapter per section, GFM tables,
  bold group rows; needs the `reports` extra). A pivot exports flattened with its subtotals, a
  chart as its data table, a KPI as a row of totals, a detail as its rows; 3 000 rows at most per
  section. The buttons sit in the mode bar. Underneath, `liberty.explorer.report` holds the one
  server-side reading of a saved definition — the period presets resolved like the page does, an
  optional forced window (`from` / `to`) — and `build_sections`, which the C5 digest will call
  headlessly.
- **A model from a Screen Assistant preset.** The join graph is declared once, in the preset
  (`config/presets/**.toml`, shipped with the app like the model). In Settings → Explorer, picking
  a preset in the model's header **fills the model**: every preset table becomes an entity
  (`SELECT *` on the table, or the preset's fast-columns `query`; the id is the table name, the
  connector the preset's), the base table the hub, the joins relations on **data items**
  (`F0101.AN8 = F0111.AN8`) resolved on the discovered columns: the column of that name, the one
  whose dictionary item it is, or on a `dd_prefix` connector the one whose name minus the prefix
  is it (`ABAN8`, `WWAN8`). Entities already declared are kept and completed (the key, the
  measures, the default date, a cardinality stay yours); the result is what gets saved and the
  runtime reads only that. An entity may now be a `table` (+ `schema`) instead of a query, and the
  entity editor discovers a table's columns too. Routes `POST /admin/explorer/from-preset`,
  `POST /admin/explorer/describe-entity`; the Screen Assistant's catalogue reads the same files
  through the new `liberty.presets` loader.

**nomaflow: the digest — `report` and `notify` steps**
- Two new step types make a scheduled, e-mailed digest a plain job. **`report`** renders a saved
  Explorer analysis (`report = "analysis:<id>"`) or dashboard (`dashboard:<id>`, one section per
  tile) of an `app` / `model`, or a plugin report (`report:<scope>:<id>`, with `params`), to
  `format` xlsx or pdf, into the run's work area (the Run detail offers the file). The period is
  the step's `period` preset (`lastMonth`, `thisQuarter`…) or `window_from` / `window_to`, else
  the analysis's own. **Visibility**: `as_user = "cfo"` renders under that user's row-level scope
  (their roles' rules with their attributes — a "filiale" digest contains that company only);
  `as_role` under a role's literal rules; neither, unrestricted. **`notify`** sends the message
  through the notification dispatcher — `recipients` (`user:` / `role:` / `email:`), `subject`
  and `body` with `{JOB}` `{RUN_ID}` `{FILES}` `{DATE}`, `channels` — with the run's files
  **attached** (`attach = ["*"]`, or names). Both edit in the Job editor (the model, the shared
  analyses and dashboards, the reports, the roles are picked from lists); the schedule is the
  job's cron or presets as usual. The executors read the live app state, so a reload is seen.

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
