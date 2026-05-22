# nomaflow — UI design

The engine spec is [PHASE13.md](PHASE13.md). This document specs the **operator-facing
UI**: how jobs are authored, edited, run, and monitored. It supersedes PHASE13.md §8,
which assumed the whole UI was "a Screen, zero new UI code" — that's wrong for the
job-authoring side (see §1).

**Decided in the design discussion (2026-05-22):**
- Orchestration is **within a job** — the ordered step pipeline *is* the ETL flow.
  nomaflow stays linear-steps-per-job; no job-to-job DAG (PHASE13.md §1 non-goal holds).
- The job UI is a **custom feature area**, not a config-driven Screen and not a
  Settings config-builder (§1).
- A `nomaflow_jobs` mirror table was considered and **rejected** — it's a second
  source of truth that drifts; the catalogue is read live from `jobs.toml` via the
  registry (§1).
- The feature area is reached via a **new `page` menu leaf type** — menu items can
  point at a registered frontend route, not just queries/endpoints/dashboards (§2).
- Step forms: **hand-write `sql_copy` + `sql_query`** (they need live connector/query
  dropdowns); **`SchemaForm` for `python` / `ldap_sync` / `http`** (flat field sets a
  generic form handles fine). Reorder with up/down, not drag-drop (§4).

---

## 0. TL;DR

A custom React feature area at `frontend/src/pages/Nomaflow/` — the same category as
the Chat or HttpRunner pages, an operational tool (config editing + runtime control +
monitoring), not a config form. Three surfaces: a **Jobs list** (the catalogue +
run/cancel/enable), a **Job editor** (job-level settings + the **step-pipeline
editor** — where ETL jobs are built), and **run monitoring** (the chunk-4 config
Screen, reused — runs *are* connector data). Reached via a new `page` menu leaf type.
Backed by `GET /admin/jobs`, new `GET/PUT /admin/config/jobs/parsed` endpoints, and the
existing run/cancel/reload endpoints. Built in 7 increments; the step-pipeline editor
is the heart.

---

## 1. Why a custom feature area

Three things the nomaflow UI is *not*, and why:

**Not a config-driven Screen.** The Screen engine renders rows from a SQL connector.
The job *catalogue* lives in `jobs.toml` — a file, not a table. The run *history*
lives in `nomaflow_job_runs` — a real table, so the chunk-4 Job Runs Screen is
correct and stays. But the catalogue, and especially the *authoring* experience
(building a step pipeline), is not connector data and not a grid.

**Not a `nomaflow_jobs` mirror table.** Projecting `jobs.toml` into a table so a Screen
could read it was considered and rejected: it's a derived second source of truth that
drifts the moment a sync path is missed — the exact class of bug that the stale
`liberty-next/config/*.toml` copies caused. `jobs.toml` is the source of truth; the UI
reads it live.

**Not a Settings config-builder.** This is the real distinction — and it isn't
"generic form vs custom form" (the Connectors builder already has bespoke editors like
`ApiConnectorEditor` and `EditQueryModal`; the builders aren't generic either). The
distinction is *what the surface does*. Every Settings tab is pure **config CRUD** —
read a TOML file, edit it, write it back. The nomaflow UI is config editing **plus
runtime control** (run a job, cancel an in-flight run) **plus monitoring** (run
history, last-run status, next-fire time) — one cohesive operational experience. A
Settings tab is the wrong home for runtime control; an operational app is a different
category of surface.

What it *is*: a domain feature area — `pages/Nomaflow/` — like the Chat page or the
HttpRunner. It reuses config-builder *plumbing* (the `/admin/config/jobs/parsed`
GET/PUT pattern every other section has) but presents a purpose-built *experience* on
top. It reuses the run-monitoring Screen as-is (don't rebuild what works).

---

## 2. Where it lives

A top-level route, `/nomaflow`, with nested routes:

```
/nomaflow                  → Jobs list (the home)
/nomaflow/jobs/new         → Job editor, create mode
/nomaflow/jobs/:id         → Job editor, edit mode
/nomaflow/runs             → run history (reuses the chunk-4 Job Runs Screen)
/nomaflow/runs/:runId      → run detail (the chunk-4 Run Detail Screen)
/nomaflow/schedule         → schedule landscape (later increment)
```

**Reaching it — a new `page` menu leaf type.** The chunk-4 `[menus.nomaflow]` app
already puts a "nomaflow" entry in the app picker with an "Operations → Job Runs" item.
But menu leaf items today are typed `query` / `endpoint` / `dashboard` — none can point
at a custom React route. So the menu schema gains a fourth leaf type:

```toml
[[menus.nomaflow.items]]
id = "jobs"
label = "Jobs"
parent = "operations"
type = "page"
target = "/nomaflow"          # a registered frontend route
```

A `page` leaf navigates to `target` (a route the SPA registers) instead of opening a
Screen. This is a **small framework change** — the menu config schema (one new `type`
literal + the `target`-as-route interpretation) and the frontend menu renderer (a
`page` branch that does a router push). It's reusable: any future custom page becomes
menu-addressable. Permission gating for a `page` leaf uses the item's existing `roles`
field — there's no `sql:`/`api:` permission to synthesise, and the target page enforces
its own auth (superuser, for nomaflow editing) regardless. With this, the nomaflow Jobs
pages and the run-monitoring Screens sit together under one `[menus.nomaflow]` app —
consistent with how nomasx1/nomajde mix screens today.

**Permissions.** Editing jobs is a config-write operation — superuser, same gate as the
other `/admin/config/*` endpoints. Running/cancelling a job is also superuser today
(the chunk-3 endpoints are superuser-gated). A future `nomaflow:run` permission could
let non-superusers trigger jobs without editing them; out of scope for v1.

---

## 3. Surfaces

### 3.1 Jobs list — the catalogue home

A card or row per job. Each shows:

- **id** + description
- **schedule** — human-readable ("Daily 02:30 Europe/Paris"), not raw cron
- **enabled** — a toggle
- **last run** — a status badge (SUCCEEDED / FAILED / RUNNING / never), from the most
  recent `nomaflow_job_runs` row, with relative time ("2h ago")
- **next run** — when APScheduler will next fire it (null for manual-only / disabled)
- **tags**

Per-row actions: **Run now**, **Cancel** (only while RUNNING), **Edit**, **enable
toggle**. Top of page: **New Job**, a filter box, a search.

**The enable toggle is a save, not a flag flip.** `enabled` lives in `jobs.toml`, so
toggling it is a `PUT /admin/config/jobs/parsed` (the whole file is rewritten) + a
`POST /admin/reload`. That's fine — but the UI should treat it as the real operation it
is (a spinner, an error path), not a cheap optimistic toggle. The alternative — moving
enable/disable into the editor only — is cleaner but costs a click; keep it on the list
row, just don't pretend it's free.

Data: `GET /admin/jobs` — but that endpoint must be **extended** to include the
last-run summary + next-fire time (§5). Today it returns only catalogue + scheduler
flags.

### 3.2 Job editor — job-level + step pipeline

Reached via Edit or New Job. Two regions:

**Job-level panel** — id, description, schedule, timezone, enabled, tags, retry policy
(attempts / backoff / base_seconds), alerts (on_failure / on_long_run_minutes /
recipients). The schedule field gets a friendly editor: presets (Hourly / Daily /
Weekly / Monthly) that generate the cron, plus a raw-cron escape hatch with a
live "next 5 fire times" preview.

**Step pipeline** — the ETL-building surface. See §4 — it's the heart of this spec.

Footer: **Save** (PUT the job back), **Run now**, **Cancel edit**. Save validates
server-side against the `JobsFile` schema; field errors surface inline.

### 3.3 Run monitoring — reuse the chunk-4 Screen

The chunk-4 `[screens.nomaflow.runs]` / `[screens.nomaflow.run_detail]` Screens already
render run history + per-step detail from `nomaflow_job_runs` / `nomaflow_step_runs`.
Runs *are* connector data — a Screen is the right tool. The feature area **links to
them**, doesn't rebuild them.

**Open: per-job deep-link.** The Jobs list / editor would ideally link to "runs for
*this* job" — a pre-filtered run Screen. That assumes the Screen engine supports
**URL-driven pre-applied filters** (`?filter=job_id:nomajde-daily-sync` or similar).
**Not verified.** If the engine doesn't, the options are: (a) add URL-filter support to
the Screen engine, (b) a second `list_runs_for_job` query bound to a route param, or
(c) skip the deep-link in v1 and let the operator filter manually on the runs Screen.
Verify before increment 2 relies on it.

### 3.4 Schedule landscape — later increment

A cross-job view of "what runs when" — a table sorted by next-fire, or a day/week
calendar grid of upcoming fires. Since orchestration is within-job, there's no DAG to
draw; this is purely a scheduling overview. Lowest priority — ships after the editor.

---

## 4. The step-pipeline editor

This is where ETL jobs are built. A job's `steps` list is an **ordered pipeline** —
extract, transform, load are just steps in sequence (PHASE13.md §3.2). The editor
makes that list first-class.

**Layout.** A vertical list of **step cards**, in execution order. Each collapsed card
shows: the step name, a type badge (`sql_copy` / `sql_query` / `python` / `ldap_sync` /
`http`), up/down reorder buttons, and a one-line summary —
e.g. `jdedwards.PS920CTL.F0004 → nomajde.f0004` for a sql_copy. Expanding a card
reveals its form. A **+ Add step** control at the bottom opens a type picker, then the
new card's form. Reorder is **up/down buttons, not drag-drop** — robust, a tenth of the
code, and a 5–10-step pipeline never needs drag.

**Per-type forms — split by where the UX value is:**

The two SQL step types get **hand-written forms** — their inputs must be smart:

- **`sql_copy`** — source `{connector, schema, table}` + target `{connector, schema,
  table}`; `mode` (overwrite/append/upsert); `type_coercion` (jde/none); `decimal_mode`
  (truncate/preserve); `batch_size`. The connector fields are **dropdowns** populated
  from the live connector registry (`GET /api/connectors`); schema fields offer the
  pool's known schemas where available. Free-text connector names would be the single
  worst part of the editor — this is where bespoke earns its cost.
- **`sql_query`** — `connector` (dropdown) + `query` (dropdown of that connector's named
  queries) + `params` (key/value editor).

The other three get a **generic `SchemaForm`** over their step-type schema — they're
flat field sets where a bespoke form adds little:

- **`python`** — `callable` (`module:function` string + format hint), `op_kwargs` map.
- **`ldap_sync`** — server / bind / search fields, the attribute→column `mapping` map,
  target connector/query.
- **`http`** — url / method / headers / body.

(Any of the three can be upgraded to bespoke later if a real UX need appears — the
split is a v1 effort call, not a permanent boundary.)

**No `step_defaults` in the editor.** PHASE13.md §3's `[jobs.step_defaults]` block is a
*hand-authoring* shortcut, merged into every step at load time. The editor works on
**fully-expanded steps** — it never surfaces `step_defaults`, and saving a
builder-edited job writes explicit per-step config (the same normalize-on-save the
other builders do). A job hand-written with `step_defaults` (like `nomajde-daily-sync`)
still runs fine; opening it in the editor and saving expands it. To keep adding similar
steps cheap without the `step_defaults` concept, **"Add step" clones the previously
selected step** as its starting point — the operator tweaks the schema/table and moves
on. That covers the nomajde case (7 near-identical `sql_copy` steps) directly.

**Validation.** Client-side: required fields per type, the `module:function` shape for
`python`. Server-side: the `PUT /admin/config/jobs/parsed` re-validates the whole file
against `JobsFile` — the editor surfaces any 422 field errors inline.

---

## 5. Data layer

| Endpoint | Status | Used by |
|---|---|---|
| `GET /admin/jobs` | exists (chunk 3) — **extend** with last-run summary + next-fire time | Jobs list |
| `GET /admin/config/jobs/parsed` | **new** — the parsed `jobs.toml` (`{path, jobs: [...]}`) | Job editor (load) |
| `PUT /admin/config/jobs/parsed` | **new** — validate via `JobsFile`, write via `tomlkit` | Job editor (save) |
| `POST /admin/jobs/<id>/run` | exists (chunk 3) | Run-now buttons |
| `POST /admin/jobs/runs/<id>/cancel` | exists (chunk 3) | Cancel buttons |
| `POST /admin/reload` | exists — applies a saved `jobs.toml` | Save flow (save → reload) |
| `GET /api/connectors` | exists | sql_copy / sql_query connector dropdowns |
| nomaflow connector `list_runs` / `list_steps` | exists (chunk 4) | run-monitoring Screens |

**New backend / framework work** (all small):

1. `GET/PUT /admin/config/jobs/parsed` (`liberty/web/admin.py`) — the parsed-config
   pattern every other section has (`tomlkit` round-trip on PUT). One wrinkle:
   `jobs.toml`'s top level is a `[[jobs]]` *array*, not a dict keyed by id like
   `pools`/`connectors` — so the body is `{jobs: [...]}` and the GET returns a list.
   The GET returns **merged** jobs (`step_defaults` expanded — see §4).
2. Extend `GET /admin/jobs` (`liberty/web/jobs.py`): the last-run badge needs the
   latest `nomaflow_job_runs` row *per job* — a `DISTINCT ON` (Postgres) /
   correlated-subquery (SQLite) query, so it wants the **per-dialect SQL map**
   treatment the chunk-4 monitoring queries already use; not a one-liner. Next-fire
   reads APScheduler's `next_run_time` per job (cheap — in-memory).
3. The **`page` menu leaf type** (§2) — `liberty/menus/config.py` (one new `type`
   literal, `target` interpreted as a route) + the frontend menu renderer (a `page`
   branch → router push). Reusable beyond nomaflow.

No new tables. No mirror. No new Screen-engine capability.

---

## 6. Build increments

Ordered so each step is independently useful and on the path:

1. **Backend** — `GET/PUT /admin/config/jobs/parsed`, the `GET /admin/jobs` extension,
   and the `page` menu leaf type (schema + renderer). Tested without the feature area.
2. **Feature-area scaffold + Jobs list** — `pages/Nomaflow/` + the `/nomaflow` routes;
   the Jobs list page (catalogue, last-run badges, Run/Cancel/enable, New Job); the
   `[menus.nomaflow]` `page` leaf wiring. First visible result.
3. **Job editor — shell + job-level panel** — load a job, edit id/schedule/retry/
   alerts, Save. Steps shown read-only at this stage.
4. **Step-pipeline editor** — add (clone-previous) / reorder (up-down) / delete steps;
   the hand-written `sql_copy` + `sql_query` forms (they cover `nomajde-daily-sync`
   end-to-end).
5. **Remaining step forms** — `python`, `ldap_sync`, `http` via `SchemaForm`.
6. **Schedule editor polish** — cron presets + next-fire preview.
7. **Schedule landscape view** — the cross-job overview.

Increments 1–4 deliver a usable "see, edit, run nomajde-style jobs" tool. 5–7 round it
out. A Vitest component baseline for `pages/Nomaflow/` rides along with increment 2 —
this feature area is exactly the surface PHASE13.md §6 flagged as needing frontend
tests.

---

## 7. Open questions

- **Job id immutability** — `id` is the dedup key in `nomaflow_job_runs` and the
  scheduler's APScheduler job id. Editing it orphans run history. Treat `id` as
  immutable after creation (rename = new job), or support a rename that also
  rewrites history? Lean immutable.
- **`PUT` validation errors → inline fields** — `PUT /admin/config/jobs/parsed`
  rejects an invalid file with a Pydantic error whose location path looks like
  `jobs.3.steps.2.source.connector`. Mapping that back to the right step card's
  field needs an error-path parser in the editor. Not hard, but real work — the
  editor isn't "done" until a 422 lands on the offending field, not in a banner.
- **Cron editing UX** — presets + raw field + a next-fire preview is the plan; is a
  full visual cron builder (per-field minute/hour/dow pickers) worth it, or is the
  preset+raw combo enough? (And: compute the next-fire preview client-side with a
  cron lib, or via a tiny backend endpoint? Lean client-side.)
- **Editing a job mid-run** — Save → reload while a run of that job is in flight: the
  running job keeps the definition the runner captured at fire time; the new
  definition applies from the next fire. Believed fine (the runner holds its own
  `Job` reference) — confirm, and surface a hint in the editor if the job is RUNNING.
- **Concurrent edits** — two operators editing `jobs.toml` via the editor last-write-
  wins clobber each other (same as every other config builder today). Fine for now;
  note it.
- **`python` callable discovery** — the editor could offer a dropdown of importable
  `nomaflow.*` callables instead of a free-text `module:function`. Needs a backend
  endpoint that introspects the `plugins/` package. Nice-to-have, not v1.
- **Run-now from the editor on an unsaved job** — disable Run-now while the editor is
  dirty (you can only run what's saved), or auto-save first? Lean: disable while dirty.

**Resolved during the design review** (no longer open): nav reachability — a `page`
menu leaf type (§2); step-form fidelity — hand-write the two SQL types, `SchemaForm`
for the rest (§4); reorder — up/down, not drag.
