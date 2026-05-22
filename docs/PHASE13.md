# Phase 13 — nomaflow

**ETL + scheduler app for liberty-next. Replaces the v1 Airflow plugin set.**

This document is the spec. The 10-line summary lives in [PLAN.md §7](PLAN.md#7-phases-11--12--13--repo-split-airflow-legacy-nomaflow). The v1 source it replaces is in [../../liberty-apps/legacy/](../../liberty-apps/legacy/).

---

## 0. TL;DR

nomaflow is a configuration-driven ETL + scheduler that runs **inside the liberty-next process**. Jobs are declared in `jobs.toml` (one file per deployment, lives in `liberty-apps/plugins/nomaflow/`); each job is a sequence of typed steps that call existing v2 connectors. APScheduler fires jobs on cron/interval; a `JobRunner` executes the step list; run state lands in a new `nomaflow_job_runs` table; the existing Screen engine surfaces the monitoring UI (zero new UI code). No Spark, no Airflow webserver, no separate metadata DB — one process, one binary, the same config-driven philosophy as every other v2 concern.

---

## 1. Goals & non-goals

### Goals

1. **Replace 100% of the production Airflow plugin workload** (nomasx1 agents + nomajde JDE table sync) with declarative config.
2. **Reuse v2 primitives.** Pools, connectors, queries, the encryption layer, the WS broadcaster, the Screen engine — nomaflow consumes them, doesn't reinvent them.
3. **Operator UX = the rest of v2.** Editing jobs.toml lives behind the Settings page, like every other config file. Viewing runs is a Screen.
4. **Single process.** No cluster, no Redis, no Celery, no Airflow scheduler. A background asyncio task + APScheduler in-process.
5. **No data-shape regressions vs v1.** The JDE type-coercion behavior (Decimal(p,0) → Integer when p ≤ 9, Long otherwise; trim strings; strip null bytes; lowercase column names) must be byte-equivalent on the resulting Postgres tables.

### Non-goals

- **Distributed execution.** v1's Spark setup was justified by perceived scale; the actual JDE table volumes (millions of rows, not billions) do not need it. If a job ever grows beyond single-node throughput, revisit then.
- **A general-purpose DAG engine.** No branching, no XCom, no SubDAGs. Linear step sequences only; if you need a DAG, write a Python step.
- **Backwards compatibility with Airflow DAG files.** v1 source stays in `liberty-apps/legacy/` as reference. Once nomaflow covers production, the Airflow deployment is decommissioned.
- **An unauthenticated job submission API.** Triggers come from three places: the scheduler (cron), the admin-protected `POST /admin/jobs/<id>/run` (used by the Settings page "Run now" button — same auth as every other `/admin/*` endpoint), and other jobs calling out via `http`. No public surface, no per-job webhook URLs, no anonymous trigger tokens.

---

## 2. Architecture

```
                   ┌──────────────────────────────────────────────────┐
                   │             liberty-next process                 │
                   │                                                  │
  config/          │   ┌──────────────────┐   ┌────────────────────┐ │
  jobs.toml ──────►│   │  JobRegistry     │──►│  APScheduler       │ │
                   │   │  (parses TOML,   │   │  (AsyncIOScheduler)│ │
                   │   │   validates,     │   └─────────┬──────────┘ │
                   │   │   hot-reloads)   │             │ fires      │
                   │   └──────────────────┘             ▼            │
                   │                            ┌────────────────┐   │
                   │   ┌────────────────────────┤  JobRunner     │   │
                   │   │                        │  (per-run      │   │
                   │   │                        │   asyncio task)│   │
                   │   │                        └─┬──────────────┘   │
                   │   ▼ uses                     │ invokes          │
                   │ ┌──────────────────┐         ▼                  │
                   │ │ ConnectorRegistry│   ┌──────────────────┐     │
                   │ │  (existing)      │◄──┤ StepExecutor[*]  │     │
                   │ └──────────────────┘   │  sql_copy        │     │
                   │                        │  sql_query       │     │
                   │   ┌──────────────────┐ │  python          │     │
                   │   │ Pool 'session'   │◄┤  ldap_sync       │     │
                   │   │ (existing)       │ │  http            │     │
                   │   └────────┬─────────┘ └─────┬────────────┘     │
                   │            │ writes          │ broadcasts       │
                   │            ▼                 ▼                  │
                   │  ┌──────────────────┐  ┌──────────────────┐     │
                   │  │ nomaflow_job_runs│  │ Socket.IO        │     │
                   │  │ nomaflow_step_   │  │ /technical room  │     │
                   │  │   runs           │  └──────────────────┘     │
                   │  └──────────────────┘                           │
                   └──────────────────────────────────────────────────┘
                              ▲                            ▲
                              │ SELECTs via               │ live updates
                              │ JobRuns connector         │
                              │                            │
                   ┌──────────┴────────────────────────────┴──────────┐
                   │   Screen "nomaflow.runs"                         │
                   │   (config-driven, uses existing Screen engine)   │
                   └──────────────────────────────────────────────────┘
```

**Two new modules** in liberty-next:
- `liberty/jobs/` — `JobRegistry`, `JobRunner`, `StepExecutor` implementations, `nomaflow_*` ORM models. Aim for a focused module, not a framework — most logic per step type belongs in `steps/<type>.py`.
- `liberty/jobs/connectors/` — a synthetic `JobRunsConnector` so the Screen engine can read `nomaflow_job_runs` like any other table.

**Zero new code** in:
- The frontend (the monitoring Screen is just TOML).
- The connector layer (jobs reuse `SQLConnector` / `APIConnector` etc.).
- Auth/permissions (jobs run with a synthetic `system` user; manual triggers carry the caller's identity).

---

## 3. The job model — `jobs.toml`

A single file. One section per job. Lives in `liberty-apps/plugins/nomaflow/jobs.toml` (env: `${NOMAFLOW_JOBS}` overrides; defaults to `${LIBERTY_APPS_DIR}/plugins/nomaflow/jobs.toml`).

### 3.1 Top-level shape

```toml
[meta]
version = 1

[[jobs]]
id = "nomajde-daily-sync"           # unique, stable; used in run history + logs
description = "Sync JDE control + data dictionary tables to Nomajde"
schedule = "30 2 * * *"              # cron (5-field); omit for manual-only
timezone = "Europe/Paris"            # IANA; default = system tz
enabled = true                       # disable without deleting
tags = ["nomajde", "etl"]            # for the monitoring UI

# retry policy — applies to each step independently
[jobs.retry]
attempts = 3
backoff = "exponential"              # "fixed" | "exponential"
base_seconds = 60

# alert policy — fans out via the existing Socket.IO /technical room
[jobs.alerts]
on_failure = true
on_long_run_minutes = 60             # warn if a single run exceeds N minutes
recipients = ["admin"]               # optional; usernames or roles

# the actual work — linear sequence, no branching
[[jobs.steps]]
type = "sql_copy"
name = "copy F0004"
source = { connector = "jde", schema = "PS920CTL", table = "F0004" }
target = { connector = "nomajde", schema = "nomajde", table = "f0004" }
mode = "overwrite"                   # "overwrite" | "append" | "upsert"
type_coercion = "jde"                # "jde" | "none" — JDE = Decimal→int + trim + null-strip
batch_size = 10000

[[jobs.steps]]
type = "sql_copy"
name = "copy F0005"
source = { connector = "jde", schema = "PS920CTL", table = "F0005" }
target = { connector = "nomajde", schema = "nomajde", table = "f0005" }
# inherits mode + type_coercion from a job-level [jobs.step_defaults] if present
```

### 3.2 Step types (the closed set)

The framework ships **five** step types. Anything else lives in a user `python` step.

| Type | What it does | Example v1 plugin it replaces |
|---|---|---|
| `sql_copy` | Stream rows from a source SQL connector to a target SQL connector. Optional type coercion + schema discovery + DDL upsert. | `db_copy.copy_table` |
| `sql_query` | Run a named query on a connector (read or write). Useful for purges, refreshes, materialized-view rebuilds. | `db_backup`, `db_purge` |
| `python` | Call a registered Python function with `op_kwargs`. Last-resort escape hatch for things that aren't expressible as the other four. | the agent/ETL plugins that need custom logic |
| `ldap_sync` | Bind, search, write user/group rows to a target connector via a configured mapping. | `nomasx1.agent.ldap` |
| `http` | Fan-out HTTP requests (calling an `APIConnector` or a raw URL) with parameter substitution. | future webhook needs |

**Why a closed set:** Airflow's "anything goes" model is the reason v1 has ~10 KLOC of operators (measured: `airflow-plugins-enterprise/` is 9974 lines). Most of those operators are variants of "read table A, transform, write table B" — the boilerplate that vanishes when configuration replaces code. Five well-spec'd step types cover everything in production; the long tail goes through `python`.

### 3.3 Substitution

Same `${VAR}` and `${VAR:-default}` substitution as `connectors.toml` / `app.toml` (Phase 5 decision). Plus a per-run context:

- `${run.id}` — uuid of the current run
- `${run.started_at}` — ISO timestamp
- `${job.id}` — job id from the section
- `${step.name}` — current step name
- `${prev.rows_affected}` — output of the previous step (only `sql_copy` / `sql_query` populate this)

### 3.4 Worked example — reproducing `nomajde-sync.py`

The v1 DAG ([legacy/airflow-plugins-enterprise/dags/nomajde-sync.py](../../liberty-apps/legacy/airflow-plugins-enterprise/dags/nomajde-sync.py)) is 65 lines of Python that loops over 7 tables and calls `copy_table()`. nomaflow equivalent:

```toml
[[jobs]]
id = "nomajde-daily-sync"
description = "Sync JDE control + data dictionary tables to Nomajde"
schedule = "30 2 * * *"

[jobs.retry]
attempts = 2
backoff = "fixed"
base_seconds = 60

[jobs.step_defaults]
type = "sql_copy"
mode = "overwrite"
type_coercion = "jde"
source = { connector = "jde" }
target = { connector = "nomajde" }

[[jobs.steps]]
name = "F0004"
source = { schema = "PS920CTL", table = "F0004" }
target = { schema = "nomajde",  table = "f0004" }

[[jobs.steps]]
name = "F0005"
source = { schema = "PS920CTL", table = "F0005" }
target = { schema = "nomajde",  table = "f0005" }

[[jobs.steps]]
name = "F9200"
source = { schema = "DD920", table = "F9200" }
target = { schema = "nomajde", table = "f9200" }

[[jobs.steps]]
name = "F9202"
source = { schema = "DD920", table = "F9202" }
target = { schema = "nomajde", table = "f9202" }

[[jobs.steps]]
name = "F9210"
source = { schema = "DD920", table = "F9210" }
target = { schema = "nomajde", table = "f9210" }

[[jobs.steps]]
name = "F9860"
source = { schema = "OL920", table = "F9860" }
target = { schema = "nomajde", table = "f9860" }

[[jobs.steps]]
name = "F9865"
source = { schema = "OL920", table = "F9865" }
target = { schema = "nomajde", table = "f9865" }

# step_defaults provides connector/mode/coercion;
# each step supplies its own schema/table pair.
```

65 lines of Python → ~40 lines of TOML, with explicit retry policy, monitoring out of the box, and no Spark startup cost. (Inline tables, not the semicolon syntax that isn't valid TOML — every key/value pair gets its own line or lives inside `{...}`.)

### 3.5 Worked example — reproducing `nomasx1-agent.py`

The v1 DAG factory ([legacy/airflow-plugins-enterprise/dags/nomasx1-agent.py](../../liberty-apps/legacy/airflow-plugins-enterprise/dags/nomasx1-agent.py)) registers six DAG variants (daily ACTIVITY_LOG, OUT, AUDIT_TRAIL; weekly all-modules; unscheduled). nomaflow uses a `python` step with parameterized op_kwargs:

```toml
[[jobs]]
id = "nomasx1-activity-log"
description = "Collect nomasx1 activity log"
schedule = "30 2 * * *"

[[jobs.steps]]
type = "python"
name = "collect"
callable = "nomaflow.nomasx1.collect:run"
op_kwargs = { apps_id = "10", module = "ACTIVITY_LOG", debug_enabled = "N", table = "all", user_id = "all", role_only = "N" }

[[jobs]]
id = "nomasx1-agent-weekly"
description = "Weekly full nomasx1 agent run"
schedule = "0 3 * * SUN"

[[jobs.steps]]
type = "python"
name = "collect"
callable = "nomaflow.nomasx1.collect:run"
op_kwargs = { apps_id = "10", module = "all", debug_enabled = "N", table = "all", user_id = "all", role_only = "N" }
```

The `nomaflow.nomasx1.collect` module is the ported version of the v1
`collect_nomasx1_dag` body, living at `liberty-apps/plugins/nomaflow/nomasx1/collect.py`
(see §5.3 for how `plugins/` becomes the importable package root). The TOML stays
small; the heavy lifting is in the Python (which is fine — that's what `python` is for).

---

## 4. The `JobRunner` contract

```python
class JobRunner:
    async def run(self, job: Job, trigger: Trigger) -> JobRun:
        """
        Executes job's steps in order. Persists JobRun + StepRun rows.
        Broadcasts state transitions over Socket.IO /technical.

        Returns the completed (or failed) JobRun.

        Idempotency: a JobRun with state in {RUNNING, QUEUED} for the same
        (job_id, scheduled_at) is treated as a duplicate; the second call
        returns the existing run without re-running.

        Error semantics: a step failure → that step retries per job.retry policy
        → on exhausted retries, the run is FAILED, remaining steps SKIPPED.
        There's no "continue on failure" mode in v1 of nomaflow (yagni).
        """
```

**State machine:**

```
QUEUED ──► RUNNING ──► SUCCEEDED
                  └──► FAILED
                  └──► CANCELED (manual stop)
```

No PAUSED, no UPSTREAM_FAILED, no DEFERRED — Airflow's full taxonomy isn't needed for linear sequences. (The `enabled = false` flag in jobs.toml is *scheduler-level*, not run-level: it tells APScheduler not to register the cron trigger at all. The state machine above describes the lifecycle of a `nomaflow_job_runs` row, which only ever gets created when a job actually fires.)

**Concurrency:** one `JobRunner` instance per worker process; jobs run in parallel asyncio tasks. Within a job, steps are strictly serial. No `max_active_runs_per_dag` — if a schedule fires while the previous run is still RUNNING, the new fire is **dropped and an alert fires on the Phase 9 Socket.IO `/technical` room** (same channel the alerts policy in §3.1 uses). Matching v1's `depends_on_past=False, catchup=False` defaults on the drop semantics; differing from v1 by being loud about it — a long-running job that silently skips its next day is the exact failure mode this design fixes. Operators see the dropped fire in real time and can decide whether to investigate the long run or accept the skip.

**Timeouts:** each step gets `step.timeout_seconds` (default 3600); the runner cancels the asyncio task on timeout. `job.timeout_seconds` (default 14400 = 4h) is a hard ceiling on the whole run.

---

## 5. Step executors — the hard ones

### 5.1 `sql_copy` — replacing Spark JDBC

The critical path. v1's `db_copy.copy_table` ([legacy/.../db_copy.py](../../liberty-apps/legacy/airflow-plugins/liberty/airflow/plugins/database/utils/db_copy.py)) does roughly:

1. Spark `read.format("jdbc")` from source.
2. For each column: cast `DecimalType(p,0)` to `IntegerType` (p ≤ 9) or `LongType` (p > 9). Trim strings, strip `\x00`.
3. Lowercase column names.
4. Spark `write.format("jdbc").mode("overwrite")` to target with explicit `createTableColumnTypes`.

nomaflow equivalent in pure Python/SQLAlchemy:

```python
async def execute_sql_copy(self, step: SqlCopyStep, ctx: RunContext) -> StepResult:
    src_pool = self.pools[step.source.connector]
    tgt_pool = self.pools[step.target.connector]

    # 1. Discover source schema (once)
    columns = await self._introspect(src_pool, step.source.schema, step.source.table)

    # 2. Derive target DDL (apply jde coercion → pick PG types)
    target_ddl = build_target_ddl(columns, coercion=step.type_coercion)

    # 3. For overwrite mode: build a fresh "_new" table; rename atomically at the end.
    #    Production stays pointed at the existing rows until the swap commits, so a
    #    mid-stream failure leaves the previous run's data intact — no "stale → empty"
    #    window. (v1 Spark mode="overwrite" did DROP→CREATE→INSERT, which DID have
    #    that window; this is an intentional improvement.)
    tgt_table = step.target.table
    work_table = f"{tgt_table}__new" if step.mode == "overwrite" else tgt_table

    rows_written = 0
    async with tgt_pool.connect() as tgt:               # one target connection for the whole step
        if step.mode == "overwrite":
            await tgt.execute(text(
                f'DROP TABLE IF EXISTS "{step.target.schema}"."{work_table}"'))
            await tgt.execute(text(target_ddl.replace(tgt_table, work_table)))

        # 4. Stream + write in batches, all on the same target connection
        async with src_pool.connect() as src:           # one source connection too
            stmt = text(
                f'SELECT * FROM "{step.source.schema}"."{step.source.table}"')
            result = await src.stream(stmt)
            insert = insert_stmt(step.target.schema, work_table, columns)
            async for batch in result.partitions(step.batch_size):
                transformed = [coerce_row(r, columns, step.type_coercion) for r in batch]
                await tgt.execute(insert, transformed)
                rows_written += len(transformed)
                await self._broadcast_progress(ctx, rows_written)

        # 5. Atomic swap (overwrite mode only). On Postgres this is one transaction.
        if step.mode == "overwrite":
            async with tgt.begin():
                await tgt.execute(text(
                    f'DROP TABLE IF EXISTS "{step.target.schema}"."{tgt_table}"'))
                await tgt.execute(text(
                    f'ALTER TABLE "{step.target.schema}"."{work_table}" '
                    f'RENAME TO "{tgt_table}"'))

    return StepResult(rows_affected=rows_written)
```

Two behaviors worth noting in this sketch beyond the data-loss fix:

- **Single source + single target connection per step.** Opening/closing per batch
  is a common anti-pattern that shows up in async ETL code — a sync of N rows at
  batch size B does N/B connect/close cycles, all redundant. The sketch holds one
  of each for the step's whole lifetime.
- **`_new` suffix + RENAME** assumes the target dialect supports cheap `ALTER TABLE
  ... RENAME TO`. Postgres, Oracle, MySQL: yes. SQLite: no (requires CREATE+COPY).
  Production target is Postgres, so this works. If a tenant ever targets SQLite the
  executor falls back to v1's DROP-then-INSERT semantics with a logged warning.

**Why this is going to work without Spark:**

- `conn.stream()` + `result.partitions(N)` is the **same streaming pattern Phase 9 already uses** for big-grid SELECTs. Same drivers (cx_Oracle / asyncpg / pyodbc), same fetch-size tuning.
- Spark's wins (parallel executors, partition pruning) all require a real cluster. Run Spark single-node and you get its overhead without its parallelism — which is what v1 actually deployed. nomaflow drops the overhead and matches the parallelism (zero, in both cases).
- The `numPartitions` Spark option is gone, but it was only meaningful when Spark could parallelize the read across executors. Single-process, it was a no-op.
- The Spark JVM startup cost (tens of seconds per task on a cold start) is gone too; small jobs go from "slow" to "instant."

Real per-table wall times aren't known until 13a runs the harness. The Phase 13 acceptance criterion (§12) is "not significantly slower than the Spark equivalent on the same hardware" — measured, not asserted here.

**The `type_coercion = "jde"` profile** reproduces v1's rules. The "JDE convention" they encode: JD Edwards never actually stores decimals — every numeric column in the JDE schema is integer-valued at the source even when the source's data dictionary types it as `Decimal(p, s)` with `s > 0`. v1's Spark→Postgres path discovered this the hard way: without an explicit cast, Spark's default JDBC adapter mapped Oracle `NUMBER` to Postgres `float` (a lossy round-trip for what JDE actually stores). The integer/long cast in `db_copy.py` was the workaround that forced the Postgres schema to bigint/integer, matching JDE's actual data shape:

| Source type | v1 Spark cast | nomaflow target (Postgres) |
|---|---|---|
| `Decimal(p, 0)` with p ≤ 9 | `IntegerType` | `integer` |
| `Decimal(p, 0)` with p > 9 | `LongType` | `bigint` |
| `Decimal(p, s)` with s > 0 | `LongType` (truncates) | `bigint` (truncates) — see `decimal_mode` below |
| `String` | `trim` + strip `\x00` | server-side `trim()` + Python `.replace("\x00", "")` per row |
| anything else | identity | identity |

Plus: lowercase all column names (v1 `toDF(*[c.lower() for c in cols])`).

**`decimal_mode` — escape hatch for non-JDE sources.** The truncating cast is correct for JDE but wrong for any source where decimal places actually carry information. The step takes an optional `decimal_mode`:

```toml
[[jobs.steps]]
type = "sql_copy"
type_coercion = "jde"
decimal_mode = "truncate"   # default. JDE: numeric columns are integer-valued at source.
                            # other values:
                            #   "preserve" — cast Decimal(p, s>0) to Postgres numeric(p, s)
                            #               instead of bigint; keeps the decimal places.
```

Default is `"truncate"` so existing nomajde tables stay byte-equivalent across the v1→nomaflow cutover (acceptance criterion §10). New jobs targeting non-JDE sources set `decimal_mode = "preserve"` explicitly. The Decimal(p,s>0) regression test in §11 covers both modes.

A diff-test against the existing nomajde Postgres tables is the cutover acceptance criterion: same row count, same column types, same byte values per cell. Detailed in §10.

### 5.2 `ldap_sync` — replacing `nomasx1.agent.ldap`

v1 uses `ldap3` synchronously inside a PythonOperator. nomaflow wraps it in `loop.run_in_executor` (default thread pool) — `ldap3` doesn't have an async equivalent, and the LDAP bind/search isn't latency-sensitive enough to justify writing one. Config:

```toml
[[jobs.steps]]
type = "ldap_sync"
name = "sync users from corp AD"
server = "ldaps://ad.corp.example.com"
bind_dn = "${LDAP_BIND_DN}"
bind_password = "${LDAP_BIND_PASSWORD}"   # or ENC: + master_key
search_base = "OU=Users,DC=corp,DC=example,DC=com"
search_filter = "(&(objectClass=user)(memberOf=CN=NomaUsers,...))"
attributes = ["sAMAccountName", "mail", "displayName", "memberOf"]
target_connector = "session"
target_query = "upsert_ldap_user"          # named query already in connectors.toml
mapping = { username = "sAMAccountName", email = "mail", full_name = "displayName" }
```

The connector + named query do the writes; nomaflow only owns the LDAP iteration.

### 5.3 `python` — the escape hatch

```toml
[[jobs.steps]]
type = "python"
name = "custom"
callable = "my.module:fn"                  # entrypoint-style; resolved with importlib
op_kwargs = { foo = "bar", run_id = "${run.id}" }
```

The function signature: `def fn(**kwargs) -> dict | None`. The return dict's `rows_affected` (if present) populates `${prev.rows_affected}` for the next step. Sync or async — the runner detects via `inspect.iscoroutinefunction`.

**Where the callable lives.** When `LIBERTY_APPS_DIR` is set, liberty-next prepends `${LIBERTY_APPS_DIR}/plugins/` to `sys.path` at startup. That makes every subdirectory of `plugins/` (each with an `__init__.py`) an importable top-level package. So `liberty-apps/plugins/nomaflow/nomasx1/collect.py` resolves as `nomaflow.nomasx1.collect`. No registration step, no central allowlist — the package layout *is* the contract. (Option (b), an explicit `[nomaflow.callables]` allowlist in `app.toml`, was considered and rejected for v1: the plugins repo is private and trusted, and an allowlist would force every new job author to touch app.toml. Re-evaluate if/when third-party job authors appear.)

---

## 6. Scheduler — APScheduler integration

- **Library:** `apscheduler` ≥ 4.0 (the async-first rewrite). Falls back to 3.x with `AsyncIOScheduler` if 4.0 isn't stable enough by implementation time.
- **Job store:** in-memory. Schedules come from `jobs.toml` at startup + on `POST /admin/reload`; persisting them in a DB would just be a stale duplicate of the TOML.
- **Execution:** APScheduler fires a coroutine → `JobRunner.run(job, ScheduledTrigger(fired_at=now))`.
- **Missed fires:** if the process was down when a schedule should have fired, the next startup does NOT replay missed fires (catchup=False, matching v1). A `[scheduler] catchup_window_minutes = N` option can later allow a grace period if needed.
- **Manual trigger:** a `POST /admin/jobs/<id>/run` endpoint (admin permission required) → enqueues a `ManualTrigger(triggered_by=user_id)` run. This is what the Settings page UI fires when an operator clicks "Run now."
- **Crash recovery on startup.** If the process died mid-run, `nomaflow_job_runs` will have rows in state `RUNNING` whose owning process is gone — without intervention they stay `RUNNING` forever, blocking the dedup `UNIQUE (job_id, scheduled_at)` from firing the next scheduled instance. On scheduler startup, before any schedules are registered, a sweep marks every `RUNNING`/`QUEUED` row as `FAILED` with `error_message = "abandoned: process restart during run"`. The same sweep cascades to `nomaflow_step_runs` (any RUNNING step gets `FAILED` + the same message). Cheap (one UPDATE … WHERE state IN (...)), runs once at boot, idempotent.

**Why APScheduler and not a hand-rolled cron loop:** cron parsing alone is enough to justify a library; APScheduler also handles DST, timezones, interval triggers, and per-job tz overrides. The glue layer is small — a registration loop over `jobs.toml` and a coroutine hand-off to `JobRunner.run`.

---

## 7. Persistence — `nomaflow_*` tables

Lives in the existing `session` pool (the auth/session DB). Two tables:

```sql
CREATE TABLE nomaflow_job_runs (
    id              UUID        PRIMARY KEY,
    job_id          TEXT        NOT NULL,
    trigger_kind    TEXT        NOT NULL,   -- 'scheduled' | 'manual'
    triggered_by    TEXT,                   -- username for manual; null for scheduled
    scheduled_at    TIMESTAMPTZ,            -- the cron firing time; null for manual
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    state           TEXT        NOT NULL,   -- QUEUED|RUNNING|SUCCEEDED|FAILED|CANCELED
    error_message   TEXT,
    rows_affected   BIGINT,                 -- sum across steps; convenience
    UNIQUE (job_id, scheduled_at)           -- dedup scheduled fires
);
CREATE INDEX idx_job_runs_job_id_started ON nomaflow_job_runs (job_id, started_at DESC);

CREATE TABLE nomaflow_step_runs (
    id              UUID        PRIMARY KEY,
    run_id          UUID        NOT NULL REFERENCES nomaflow_job_runs(id) ON DELETE CASCADE,
    step_index      INTEGER     NOT NULL,
    step_name       TEXT        NOT NULL,
    step_type       TEXT        NOT NULL,
    attempt         INTEGER     NOT NULL DEFAULT 1,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    state           TEXT        NOT NULL,
    error_message   TEXT,
    rows_affected   BIGINT,
    log_excerpt     TEXT                    -- last ~4 KB of step logs (full logs go to the file logger)
);
CREATE INDEX idx_step_runs_run ON nomaflow_step_runs (run_id, step_index);
```

**Migration:** Phase 6 already moved auth-table creation to `liberty-admin init-db` (no Alembic yet, see PLAN.md §6). nomaflow adds two `Table.metadata.create_all` calls there. When Alembic lands (deferred), backfill the migration.

**Retention:** a built-in `nomaflow-purge` system job runs nightly, keeps the last 90 days of `nomaflow_job_runs` (config: `[nomaflow] retention_days = 90`). Old rows cascade-delete their step_runs.

**Full logs:** the structured logger already routes per-request logs via the existing `liberty/logging.py`. The runner uses `logger.bind(run_id=...)` so log lines are filterable in the existing log-tail Socket.IO feed (Phase 9). `log_excerpt` in the DB is just the last 4 KB for at-a-glance triage; for the full log, the operator opens the log tail filtered by `run_id`.

---

## 8. Monitoring UI — zero new code

Two pieces of TOML in `liberty-apps/config/`:

**A connector** (added to `connectors.toml`):

```toml
[connectors.nomaflow]
type = "sql"
pool = "session"

[connectors.nomaflow.queries.list_runs]
sql = """
SELECT id, job_id, trigger_kind, triggered_by, started_at, finished_at,
       state, rows_affected,
       EXTRACT(EPOCH FROM (finished_at - started_at)) AS duration_seconds
FROM nomaflow_job_runs
WHERE (:job_id IS NULL OR job_id = :job_id)
ORDER BY started_at DESC
LIMIT 500
"""
params = ["job_id"]

[connectors.nomaflow.queries.list_steps]
sql = """
SELECT step_index, step_name, step_type, attempt, started_at, finished_at,
       state, rows_affected, error_message, log_excerpt
FROM nomaflow_step_runs
WHERE run_id = :run_id
ORDER BY step_index, attempt
"""
params = ["run_id"]
```

**A screen** (added to `screens.toml`): a `tableview` over `nomaflow.list_runs` with a master/detail dialog opening `nomaflow.list_steps`. Row actions: "Re-run" (calls `/admin/jobs/<id>/run`), "Cancel" (calls `/admin/jobs/runs/<id>/cancel`). The chart toggle gives a free duration trend; the existing filter chips work out of the box.

That's the entire monitoring UI. The same Screen engine that renders nomajde tables renders the nomaflow run history — no React component, no Vite build for the UI.

---

## 9. Module layout

### liberty-next

```
liberty/
  jobs/
    __init__.py
    registry.py          # JobRegistry — TOML parsing, validation, hot-reload
    runner.py            # JobRunner, RunContext, state machine
    scheduler.py         # APScheduler glue
    models.py            # SQLAlchemy ORM for nomaflow_job_runs, nomaflow_step_runs
    triggers.py          # ScheduledTrigger, ManualTrigger
    steps/
      __init__.py
      base.py            # StepExecutor abstract base
      sql_copy.py        # sql_copy — the big one
      sql_query.py
      python.py
      ldap_sync.py
      http.py
    coercion.py          # type_coercion = "jde" rules (lives apart for testability)
    connector.py         # JobRunsConnector (synthetic, for the Screen engine)
  admin/
    jobs.py              # /admin/jobs/<id>/run, /admin/jobs/runs/<id>/cancel
tests/
  jobs/
    test_registry.py
    test_runner.py
    test_sql_copy.py     # incl. JDE-coercion byte-equivalence vs golden tables
    test_scheduler.py
    test_coercion.py
```

### liberty-apps

```
plugins/
  nomaflow/
    __init__.py
    jobs.toml            # the live job catalog
    nomasx1/
      collect.py         # ported from legacy/.../nomasx1/agent/agent.py (called via python step)
      __init__.py
```

---

## 10. Sub-phases of Phase 13

The whole thing is too big for one push. Three sub-phases:

### 13a — Framework

Everything in `liberty/jobs/` + the `nomaflow_*` tables + the admin endpoints + tests. Acceptance: `pytest tests/jobs/` green; a hello-world `jobs.toml` with one `sql_query` step runs on its schedule + shows up in the Screen. **No production workload touches it yet.**

### 13b — Cutover: nomajde JDE sync

Port `nomajde-sync.py` to TOML (§3.4). Build the JDE-coercion test harness: pick 3 representative source tables (small / int-heavy / decimal-heavy), run both v1 Spark and nomaflow against the same source DB, byte-diff the resulting Postgres tables. **All three must match.** Cut over when they do; v1 nomajde stays running in parallel as a fallback for at least one full schedule cycle (one week of daily runs).

### 13c — Cutover: nomasx1 agents

Port `nomasx1-agent.py` + the underlying `collect_nomasx1_dag` body into `nomaflow.nomasx1.collect`. The agent + security + etl + target subtrees in `airflow-plugins-enterprise/` measure 5,422 lines today — the bulk of the v1 enterprise plugin LOC. Most of it ports as-is into a `python` step; the SQL-heavy bits (security writes, audit upserts) become named queries on existing connectors and get called from `sql_query` steps. Acceptance: parallel runs over one full schedule cycle (daily + weekly) produce equivalent rows in the target tables.

**On sequencing:** the three sub-phases are sequential — 13b can't start until 13a is green, and 13c needs the framework patterns 13b shakes out. Don't estimate calendar time here; estimates without measurement are noise. Track 13a's tests-green date as the real signal.

After 13c lands, the Airflow deployment is decommissioned and `liberty-apps/legacy/` becomes deletable (but stays until someone audits and confirms).

---

## 11. Open decisions / risks

1. **APScheduler 4.0 vs 3.x.** 4.0 has a much cleaner async story but as of writing was still beta. Decide at start of 13a; if 4.0 isn't trustworthy yet, 3.x with `AsyncIOScheduler` works.
2. **Single-process vs gunicorn workers.** If liberty-next runs under multiple gunicorn workers, only one should run the scheduler. Solutions: a startup election via Postgres advisory lock (`pg_try_advisory_lock(NOMAFLOW_LOCK_KEY)`) — the worker that gets it owns the scheduler, others run job execution from a shared queue. Defer to 13a implementation; the lock-based approach is well-known.
3. ~~Where `python` step callables live.~~ **Decided (in spec).** Option (a): `${LIBERTY_APPS_DIR}/plugins/` goes on `sys.path`; callables are referenced by their natural import path (`nomaflow.nomasx1.collect:run`). No allowlist. See §5.3 "Where the callable lives." Revisit if a tenant ever runs untrusted job authors.
4. ~~JDE Decimal(p,s>0) → bigint is a v1 bug-or-feature.~~ **Decided (in spec).** Not a bug — it's the JDE convention (JDE never stores decimals; the cast forces the Postgres schema to bigint instead of float, which is what v1's default Spark JDBC adapter produced). `type_coercion = "jde"` defaults to `decimal_mode = "truncate"` to keep existing nomajde tables byte-equivalent; non-JDE sources set `decimal_mode = "preserve"` to map to `numeric(p, s)` instead. Two regression tests (one per mode); see §5.1.
5. **`upsert` mode for `sql_copy`.** Specified above but the executor doesn't have a clean cross-DB implementation. Postgres has `ON CONFLICT`, Oracle has `MERGE`, MSSQL has `MERGE`. Either (a) require a `primary_key` column and emit the right dialect, or (b) drop `upsert` from v1 and force users to write a `sql_query` step against a pre-existing MERGE statement. Decide during 13a; (b) is the YAGNI choice.
6. **Logs storage beyond 4 KB.** The file logger already exists; the question is operator UX. If `journalctl`-style filtering by run_id over the Socket.IO log tail (Phase 9) is unwieldy, add a `GET /admin/jobs/runs/<id>/logs` endpoint that returns the full log slice from the on-disk file. Punt until an operator complains.
7. **License gating.** v2's license layer (Phase 5) gates `licensed = true` connectors. Should nomaflow itself be license-gated? Lean **no** — it's a generic capability; license-gating individual jobs (or the nomasx1/nomajde-specific job templates) is the right granularity. The framework is free; the customer-specific job catalog is the licensed asset.

---

## 12. Acceptance criteria (Phase 13 complete)

1. **Functional parity:** every job that ran on v1 Airflow in production runs on nomaflow with equivalent output. Acceptance is byte-equivalence for the JDE tables (§10) and row-count + spot-check parity for the nomasx1 agents.
2. **Operability:** an admin can create / edit / disable a job from the Settings page → reload → see the schedule change reflect in APScheduler within 1 reload cycle. Can manually trigger any job; can cancel a running job; can see run history + step detail + recent logs from a Screen.
3. **Reliability:** a representative JDE table sync (pick a small / int-heavy / decimal-heavy trio in 13b) completes within the same order of magnitude as the v1 Spark equivalent on the same hardware. Worse than that → investigate; we're not racing Spark on parallelism, but we shouldn't be paying overhead it doesn't.
4. **Footprint:** `liberty/jobs/` stays *substantially* smaller than the v1 plugin code it replaces (the enterprise plugins alone are 9,974 lines). If `liberty/jobs/` approaches that size, the framework is absorbing logic that should be in user `python` steps — re-evaluate the split.
5. **Decommission:** Airflow deployment is shut down. `liberty-apps/legacy/` is deletable (kept for one release as historical reference, then dropped).
