---
name: build-app
description: Build or extend a Liberty Next application in liberty-apps from a user's description of a need (the data they have — files, an existing database, an API — the figures they want to see, the statuses or workflow, who sees what). Use whenever the user describes an application to create or extend — "build an app for…", "create a demo app", "the customer wants to load X and see Y", "nouvelle application", "ajoute une application" — or asks how an app is put together. Asks what the description leaves open (above all where the data lives), then produces the bundled app: connector(s), schema plugin when the app owns tables, dictionary, screens, menu, overview dashboard, explorer model with analyses, jobs (file loads, database copies, refreshes) with guards, accounts and scope, assistant material; validated with the framework's loaders before anything runs.
---

# Build a Liberty Next application from a description

The user describes the need the way a customer would. Everything else is this procedure.
Ask what the description leaves open — first of all **where the data lives** — and settle it
before building; never invent a column, a rule or a source.

Repos: the framework `liberty-next` (changed for an app only when a feature is missing — then
developed, tested, noted in RELEASE.md EN + FR), the applications `../liberty-apps` (the
bundled apps: `config/*.toml`, `plugins/<app>/`). Read `CLAUDE.md` first. The TOML shapes to
copy are in `references/shapes.md` next to this file; the worked example is the `nomademo`
app in liberty-apps (a sales demo built from customer files). Copy its shapes, not its
subject: an application is assembled from the modules below, and a need picks the modules.

## 0. Read the need, ask the open questions

Write the **facts** the user stated and the **gaps**, then ask. The questions that decide the
build, in order:

1. **Where is the data?** One of, or a mix of:
   - **files** the users bring (Excel / CSV, by hand or dropped in a folder) → the app owns the
     tables, loads them (§8a);
   - **an existing database** (an ERP such as JD Edwards, a data warehouse, another product's
     schema) → a connector straight on it for screens and the explorer (§2), and/or copies into
     the app's own schema on a schedule when the source is slow, remote or must be frozen
     (§8b);
   - **an API** → an API connector (`type = "api"`, its endpoints), screens on the HTTP runner,
     `http` job steps.
   The assistant inside Liberty cannot receive a file; when the only material is a file, ask
   for its column list (header row) and a few sample rows, or a path on the server.
2. **What is a row?** The grain of the main table (an invoice line, a user, a document) and its
   key; the dimensions it points at (item, company, customer…) and their classifications.
3. **What figures, by what?** The sums / counts and the axes (period, company, family…); the
   period: a date column, or year + month to derive from.
4. **Which statuses or workflow?** Validated / closed / reopened, by whom; what a closed state
   blocks (edits, loads).
5. **Who sees what?** A projection per role: everything, one company, one business unit, …;
   which attribute of the user carries it.
6. **Names.** Generic English, for every customer (`item`, `company`, `business_unit`,
   `region`, `quantity`, `revenue`, `period`); never a customer's codes or a French word — the
   French goes in the dictionary's `l.fr`. The app id = the connector name; one app owns one
   pool (one database or schema) when it owns tables.

Then state the plan as a list of the modules below that apply, and build in that order.

## 1. The model — only when the app owns tables

`plugins/<app>/db/models.py`: SQLAlchemy declarative, lowercase columns (unquoted Postgres),
integer codes, `Numeric` quantities and amounts, timezone-aware stamps with
`server_default=func.now()`, natural primary keys, a `batch_id` and `loaded_at` on a loaded
fact. A period is **generated** from its parts (`Computed(...)`), never computed by a load.
`plugins/<app>/db/__init__.py` exposes `init_schema` / `upgrade_schema` through
`liberty.schema_sync.sync_tables` (create + forward-add): a column added to the model is the
migration. The `<app>-init-db` python job calls it. On an existing database the app does not
own, there is no model: the tables are the source's, read by queries.

## 2. The connector(s) — `config/connectors.toml`

- The app's own pool and connector (`[pools.<app>]` with `${<APP>_DB_URL:-…}`,
  `[connectors.<app>] type = "sql" pool = "<app>" licensed = … home = "overview"`).
- An existing database: a pool on it and a connector (Oracle: `#SCHEMA.X#` / `#DBLINK.X#`
  placeholders, `dd_prefix` when the columns carry a data-item prefix like JDE's), or the
  app's connector reading it through a dblink — the choice depends on where screens must
  write. Secrets as `${ENV}` or `ENC:` values, never plain.
- One `[[connectors.<app>.tables]]` per table (or source table) with `get` and, when
  editable, `post` / `put` / `delete` (`:COL` upper-case binds, `WHERE key = :KEY_ORIGINAL`,
  audit stamps as `{{LOGIN}}` / `now()` tokens in the SQL); the read of a fact **joins its
  dimensions** so the screen filters on them; the workflow queries; the guards; the
  post-load refreshes; the lookups (`lookup_*`: code, label, parent code); the dashboard
  aggregates (`dash_*`).
- **ORDER BY uses output column names, never `alias.col` nor `SUM(...)`** (a filter or a
  scope wraps the query). A bind used several times in one statement is cast explicitly.

## 3. The dictionary — `config/dictionary.toml`

`[connectors.<app>.entries.<ITEM>]` (upper-case ids = column names, or data items on a
`dd_prefix` connector): `label`, `l.fr`, `format` — **`integer` on every code and count**
(else a filter or scope binds text on an integer column), `number` on quantities and
amounts, `date` / `timestamp`, `textarea`; `rules = "LOOKUP"` + `rules_values` on codes,
`rules = "ENUM"` on statuses. `[connectors.<app>.enums.<NAME>]` values. `[connectors.<app>
.lookups.<id>]` with `connector`, `query`, `value`, `label`, `key_columns` (the parent codes
a `filter_from` cascades on). On an existing database the dictionary may already exist
(nomajde's JDE items): reuse, do not duplicate.

## 4. The screens — `config/screens.toml`

Minimal TOML per `[screens.<app>.<id>]` (defaults fill the rest; the builders rewrite in
full): the queries, `auto_load`, `key_columns`, `columns = [{ name, key, filter,
filter_from = [{ source, column }], hidden, disabled, disable_on_edit, rules, rules_values,
dd }]`, `l.fr`, a `dialog` (form tab, `cols = 2`, `fields`). Dimensions: CRUD with a dialog.
Facts: `read_only = true`, `max_rows`, cascading filters. A workflow screen: `disable_add`,
`editable_when` on the status, `row_menu` chains `confirm → run_query → notify`, stamp
fields `disabled`. Widget screens (§6) carry the filter item as a `filter = true` column with
its `dd`. For a screen built on a source table, the Screen Assistant (presets) or the
assistant's scaffold tools do the first draft from the table's columns.

## 5. The menu — `config/menus.toml`

`[menus.<app>] label`; items: the overview first (`type = "dashboard"`), a folder per
subject, the screens, the Data Explorer page (`type = "page"`, `/explorer`), `l.fr`.

## 6. The overview — `config/dashboards.toml`

A filter on the main dimension (`dictionary_key`, options from a lookup query), KPIs, charts
(inline `spec`), tables. **A filter reaches a widget only through a screen on the widget's
query whose column carries the item with `filter = true`** — every filterable `dash_*` query
gets its screen. A dashboard `period` only when the data is current.

## 7. The Explorer — `config/explorer.toml`

`[explorer.<app>.<model>]`: `hub` = the fact (`table` or `query`, `key`, `default_date`,
`measures`, `hidden`), the dimensions, `relations` on the codes; bundled analyses
(`definition` = the frontend's AnalysisDefinition: fields `entity.COLUMN`, sort keys
`sum(entity.COLUMN)`, `period { field, preset = "all", range }`) and a dashboard of tiles;
**bump `version` after an installed definition changes**. Amounts in several currencies are
summed by currency or by the axis that fixes the currency, never across. Check every
definition with `liberty.explorer.report.definition_to_aggregate`.

## 8. The jobs — `plugins/nomaflow/jobs.toml` (all `enabled = false`, tagged)

- **a. Files**: one `file_load` per dimension file (an emptying `sql_query` then `append`),
  the fact load in `replace_partition` on the period parts (and the company) with a
  `refuse_query` guard — it runs **per partition the file carries**, binds `:YEAR` /
  `:MONTH`… — and `on_error = "skip"` with the rejects report; the file arrives as an
  upload at run time, a `path`, or a `watch_dir`. Headers equal to the column names need no
  `columns` mapping; a customer's headers are mapped there.
- **b. An existing database**: `sql_copy` steps (source → target table, `mode = "overwrite"
  | "append" | "upsert"`, `type_coercion = "jde"` for JDE, `batch_size`) on a cron, or
  `sql_query` steps running the source's own SQL (an `INSERT … SELECT` over a dblink).
- Post-load `sql_query` steps (customers from the facts, status rows refresh) and a
  `<app>-refresh-*` job for a refresh by hand; `<app>-init-db`; `<app>-demo-accounts`
  (python); a `<app>-monthly-digest` (`report` + `notify`, as the group role) when a
  digest is wanted.

## 9. Accounts and scope — `plugins/<app>/accounts.py`

A python step: `build_auth_backend(settings, connectors.pools)`; a group role with
`["sql:<app>:*", "explorer:<app>:*", "ai:chat"]` (plus `sql:<source>:*` on a source
connector); a scoped role with the same plus `ScopeRule(connector=…, column=…,
source="#USER.<attribute>#")` — a scope on a lookup column needs the values listed in the
attribute (the framework does not resolve them through a dimension); one user per
projection; idempotent (roles refreshed, passwords kept).

## 10. The assistant — `config/ai.toml`

`[ai.<app>] starters`, `[ai.<app>.screens.<id>] starters`, `[ai.<app>.playbooks.<id>]`
(`title`, `description`, `body`): the procedures of the app (load, close, analyse, fix).

## 11. Sample files — only when the data comes as files

`plugins/<app>/samples/make_samples.py`: reads the customer's exports from a folder, writes
the demo set elsewhere (never in git) with the app's headers, integer codes, a missing
column synthesised deterministically, several periods sampled around the real one.
`openpyxl` write-only; run with `python -I` on untrusted files.

## 12. Validate, then hand over

Before any run, validate every file with the framework's loaders (snippet in
`references/shapes.md`) and import the plugin. Then tell the user what to do: create the
database (own tables), restart (plugin) or Settings → Reload config (TOML; a new file is not
watched until then), run init-db, the loads or copies, the accounts job; try each account.
Commit in liberty-apps **by explicit path**, never `git add -A` (`config/.nomaflow/` and the
customer's data stay out).

## Pitfalls met once — do not meet them twice

- `ORDER BY alias.col` / `ORDER BY SUM(...)` under a filter or scope wrap → output names.
- Code columns without `format = "integer"` → "integer = character varying".
- A bind reused in one statement without a cast → "inconsistent types deduced".
- A dashboard filter on a query without a widget screen → silently ignored.
- A bundled analysis edited after install without a `version` bump → the old copy stays.
- A lookup whose code and label are the same column shows the column twice.
- A guard that depends on a typed parameter → it is forgotten; bind the file's values.
