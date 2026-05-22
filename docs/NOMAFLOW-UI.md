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

---

## 0. TL;DR

A custom React feature area at `frontend/src/pages/Nomaflow/` — the same category as
the Chat or HttpRunner pages, a domain tool rather than a generic form. Three surfaces:
a **Jobs list** (the catalogue + run/cancel/enable), a **Job editor** (job-level
settings + the **step-pipeline editor** — where ETL jobs are built), and **run
monitoring** (the chunk-4 config Screen, reused — runs *are* connector data). Backed by
`GET /admin/jobs`, new `GET/PUT /admin/config/jobs/parsed` endpoints, and the existing
run/cancel/reload endpoints. Built in increments; the step-pipeline editor is the heart.

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

**Not a generic Settings config-builder.** The Pools/Connectors/Screens builders are
`SchemaForm`-driven CRUD over a TOML file. nomaflow's job editor needs more than a
generic form: a `sql_copy` step's source/target should be **connector dropdowns**
populated from the live connector registry, not free-text; the step list is an
**ordered, reorderable pipeline**, not a flat key/value map; "build an ETL job" is a
guided flow. A generic schema form can't deliver that.

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

**Reaching it.** The chunk-4 `[menus.nomaflow]` app already puts a "nomaflow" entry in
the app picker with an "Operations → Job Runs" item. That menu grows a "Jobs" item
pointing at `/nomaflow`. The run screens stay menu items too. The feature area and the
config Screens coexist under one menu app — consistent with how nomasx1/nomajde mix
screens today.

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
them**, doesn't rebuild them. The Job editor and Jobs list deep-link into a filtered
run view ("show runs for this job").

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
shows: a drag handle, the step name, a type badge (`sql_copy` / `sql_query` /
`python` / `ldap_sync` / `http`), and a one-line summary —
e.g. `jdedwards.PS920CTL.F0004 → nomajde.f0004` for a sql_copy. Expanding a card
reveals its typed form. Cards reorder by drag (or up/down buttons). A **+ Add step**
control at the bottom opens a type picker, then the new card's form.

**Per-type forms** — hand-written, not generic `SchemaForm`, because the inputs need to
be smart:

- **`sql_copy`** — source `{connector, schema, table}` + target `{connector, schema,
  table}`; `mode` (overwrite/append/upsert); `type_coercion` (jde/none); `decimal_mode`
  (truncate/preserve); `batch_size`. The connector fields are **dropdowns** populated
  from the live connector registry (`GET /api/connectors`); schema fields offer the
  pool's known schemas where available.
- **`sql_query`** — `connector` (dropdown) + `query` (dropdown of that connector's named
  queries) + `params` (key/value editor).
- **`python`** — `callable` (a `module:function` string, with a format hint) +
  `op_kwargs` (key/value editor).
- **`ldap_sync`** — server / bind / search fields + the attribute → column `mapping`
  editor + target connector/query.
- **`http`** — url / method / headers / body.

**Step defaults.** PHASE13.md §3 has a `[jobs.step_defaults]` block — a hand-authoring
shortcut merged into every step at load time. The editor works on **fully-expanded
steps**: it doesn't surface `step_defaults`, and saving a builder-edited job writes
explicit per-step config. A job hand-written with `step_defaults` (like
`nomajde-daily-sync`) still runs fine; opening it in the editor and saving expands it.
This is the same normalize-on-save behaviour the other config builders have. A future
"job defaults" convenience (pre-fill new steps from the previous one) can be added
without reintroducing the `step_defaults` concept to the file.

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

**New backend work** (small, all in `liberty/web/`):

1. `GET/PUT /admin/config/jobs/parsed` — the parsed-config pattern every other section
   has (`tomlkit` round-trip on PUT). One wrinkle: `jobs.toml`'s top level is a `[[jobs]]`
   *array*, not a dict keyed by id like `pools`/`connectors` — so the body is
   `{jobs: [...]}` and the GET returns a list. The GET returns **merged** jobs
   (`step_defaults` expanded — see §4).
2. Extend `GET /admin/jobs`: join the latest `nomaflow_job_runs` row per job for the
   last-run badge, and read APScheduler's `next_run_time` per job for the next-fire
   column.

No new tables. No mirror. No new Screen-engine capability.

---

## 6. Build increments

Ordered so each step is independently useful and on the path:

1. **Backend** — `GET/PUT /admin/config/jobs/parsed` + the `GET /admin/jobs` extension.
   Tested without any frontend.
2. **Feature-area scaffold + Jobs list** — `pages/Nomaflow/` + routing; the Jobs list
   page (catalogue, last-run badges, Run/Cancel/enable, New Job). First visible result.
3. **Job editor — shell + job-level panel** — load a job, edit id/schedule/retry/
   alerts, Save. Steps shown read-only at this stage.
4. **Step-pipeline editor** — add/reorder/delete steps; the `sql_copy` + `sql_query`
   forms first (they cover `nomajde-daily-sync` end-to-end).
5. **Remaining step forms** — `python`, `ldap_sync`, `http`.
6. **Schedule editor polish** — cron presets + next-fire preview.
7. **Schedule landscape view** — the cross-job overview.

Increments 1–4 deliver a usable "see, edit, run nomajde-style jobs" tool. 5–7 round it out.

---

## 7. Open questions

- **Cron editing UX** — presets + raw field + a next-fire preview is the plan; is a
  full visual cron builder (per-field minute/hour/dow pickers) worth it, or is the
  preset+raw combo enough?
- **Job id immutability** — `id` is the dedup key in `nomaflow_job_runs` and the
  scheduler's APScheduler job id. Editing it orphans run history. Treat `id` as
  immutable after creation (rename = new job), or support a rename that also
  rewrites history? Lean immutable.
- **Concurrent edits** — two operators editing `jobs.toml` via the editor will
  last-write-wins clobber each other (same as every other config builder today). Fine
  for now; note it.
- **`python` callable discovery** — the editor could offer a dropdown of importable
  `nomaflow.*` callables instead of a free-text `module:function`. Needs a backend
  endpoint that introspects the `plugins/` package. Nice-to-have, not v1.
- **Run-now from the editor on an unsaved job** — disable Run-now while the editor is
  dirty (you can only run what's saved), or auto-save first? Lean: disable while dirty.
