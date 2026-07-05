# Liberty Next — Release notes

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
