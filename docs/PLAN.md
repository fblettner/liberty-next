# Liberty v2 — Rebuild Plan

> Living document. Update the **Status** markers as phases complete.

## 1. Why a rebuild

Liberty v1 (`../liberty-framework/`) is a metadata-driven low-code framework:
~50 `ly_*` tables (`ly_query`, `ly_qry_sql`, `ly_dlg_frm`, `ly_dlg_tab`,
`ly_dlg_col`, `ly_dlg_filters`, `ly_tbl_col`, `ly_actions`, `ly_act_tasks`,
`ly_cdn_params`, `ly_lookup`, `ly_menus`, …) hold raw SQL and form layouts that
the backend re-traverses on every request to generate screens.

Concrete problems:

- **Adding a screen = coordinated inserts across 5+ tables** (`ly_dlg_frm` +
  `ly_dlg_tab` + `ly_dlg_col` + `ly_dlg_filters` + `ly_qry_sql` …). High friction.
- **No caching.** Every request walks `ly_qry_fmw → ly_tbl_col → dictionary
  tables`. Lookups are the main perf bottleneck.
- **All logic lives in SQL strings in the DB** — hard to read, debug, version.
- **Limited story for complex forms** — field rules, custom actions are bolted on
  via `ly_actions`/`ly_act_tasks`/`ly_cdn_params`/`ly_act_branch` (a mini workflow
  DSL inside the DB). Awkward.
- AI is hardcoded to OpenAI; user wants Anthropic.
- The React frontend depends on shared libraries that are painful to maintain;
  user wants it back inside the project.

## 2. The model we're moving to

Borrowed from **nomaubl** (`../../JavaProjects/nomaubl`, Java e-invoicing app):
**configuration drives discovery, not code drives configuration.**

Instead of storing form/column metadata in tables, store **executable queries and
endpoint definitions** in config files, and discover the result schema at runtime
(`cursor.description`). Add a column to a SQL query → it appears in the API
response and the UI, with zero schema-table edits.

Three connector types:

| Connector | Role | nomaubl analogue |
|---|---|---|
| **SQLConnector** | Named SQL queries, `:param` binding, parameterised, `writable` gate for mutations, runtime schema discovery | `SqlConnectorClient.java` + `DynamicResultMapper.java` |
| **APIConnector** | Generic HTTP client: pluggable auth (NONE / BASIC / BEARER / API_KEY / OAUTH2), `{{placeholder}}` substitution, JSON-path response extraction, token cache w/ auto-refresh on 401 | `ApiConnectorClient.java` |
| **DBConnector** | Pool registry — multiple databases (v1's `apps_pool` multi-tenant concept) | nomaubl `DatabaseDialect` |

Connector configs live in `config/connectors.toml` (and friends), hot-reloadable.

## 3. Decisions taken (with the user)

| Question | Decision |
|---|---|
| Rewrite vs in-place refactor | **Greenfield v2.** The metadata-table model *is* the disease; refactoring in place keeps it. |
| Existing apps (nomasx1, NOMAJDE, AIRFLOW) | **Must keep running on v1.** v2 ships migration tools (Phase 5); apps move one at a time. v1 source untouched. |
| Language | **Python** — keep the Liberty stack (FastAPI + SQLAlchemy async). Port nomaubl's *patterns*, not its Java. |
| Frontend | **Fresh React 19 + Vite + TS inside v2**, embedded as static. No shared libraries. |
| Location | **Sibling directory** `../liberty-v2/`. |
| Config format | **TOML** files on disk (nomaubl uses flat XML properties — the flat-config insight stands, TOML is the Python-idiomatic form). |
| AI | **Anthropic SDK**, drop OpenAI. Port nomaubl's `AiAssistant.java` tool-use loop. |
| Auth | Internal users (**argon2**) + **OIDC via authlib** (Keycloak-ready). |

## 4. Phased plan

### Phase 0 — Foundation — ✅ DONE
- `pyproject.toml` with the full dep set pinned; pip + venv (no uv/poetry on this machine).
- `liberty/config.py` — TOML loader, Pydantic-validated `Settings`.
- `liberty/main.py` — FastAPI app, `/health`, `/info`, `liberty-v2` CLI entry.
- `config/app.toml`, `config/connectors.toml` (placeholder).
- `tests/test_health.py` — 3 passing tests.

### Phase 1 — Connector core — ⏳ NEXT  (~3–4 wks)
The heart of v2. Build in this order:
1. **SQLConnector** — load named queries from TOML; execute with `:name` bound
   params via SQLAlchemy `text()`; `writable` flag gates INSERT/UPDATE/DELETE;
   validate statement type; map `cursor.description` → JSON schema + rows.
   Handle the JDE date/time conversions nomaubl does (`DynamicResultMapper`) if
   we'll touch JDE data — defer unless needed.
2. **APIConnector** — generic HTTP via `httpx`; auth strategies (NONE / BASIC /
   BEARER / API_KEY / OAUTH2 with token cache + auto-refresh on 401);
   `{{placeholder}}` substitution from runtime params + secrets; response field
   extraction by dot-path; multipart/form-data + file streaming.
3. **DBConnector / pool registry** — named pools (engine per pool), referenced by
   SQLConnectors. v1's `apps_pool`/`apps_dbtype` is the model.
4. CLI to list/run connectors locally; tests for each.

Config shape (target):
```toml
[connectors.liberty]
type = "sql"
pool = "default"

[[connectors.liberty.queries]]
name = "users_list"
sql = "SELECT usr_id, usr_name FROM ly_users WHERE usr_status = :status"
writable = false
```

### Phase 2 — Auth + AI — (~2 wks)
- Internal users table (argon2 hashes), roles/permissions. Bootstrap an `admin`.
- OIDC via `authlib` — Keycloak discovery URL, code flow, JWT validation.
- JWT issuance/validation for internal logins.
- AI module: Anthropic SDK, tool-use loop ported from nomaubl `AiAssistant.java`
  — tools = decorated Python functions w/ type hints; SSE token streaming;
  max-iteration cap; optional `web_fetch` restricted to allowlisted domains.

### Phase 3 — Web layer — (~2 wks)
- `GET/POST /api/sql/{connector}/query/{name}` — params in querystring/body.
- `POST /api/api/{connector}/call/{endpoint}`.
- SSE for AI streaming and long-running queries.
- OpenAPI auto-doc replaces v1's hand-rolled "get screen metadata" endpoint.
- WebSocket if needed for live updates (v1 uses Socket.IO; evaluate vs SSE).

### Phase 4 — Frontend — (~3–4 wks)
- Fresh `frontend/` — React 19 + Vite + TS. Built `dist/` served as static by
  FastAPI (mounted in `liberty/main.py`).
- Login screen: internal form + "Sign in with OIDC" button.
- Generic components driven by runtime-discovered schema:
  - `<TableView connector queryName params>` — fetches, renders columns from the
    response metadata, sorting/paging/filtering client-side or server-side.
  - `<FormView connector queryName>` — CRUD against a connector's GET/POST/PUT/DELETE.
  - `<Lookup connector queryName>` — dropdown backed by a SQL query.
  - Settings UI: CRUD the connector TOML configs (write-back + hot-reload).
- i18n (react-i18next), Monaco editor for SQL editing (like nomaubl).

### Phase 5 — Migration tools — (~4–6 wks)
- Read v1's `ly_qry_sql` → emit SQLConnector TOML.
- Read v1's `ly_api` + `ly_api_conn` → emit APIConnector TOML.
- Map `ly_tbl_col`/`ly_dlg_col` labels → optional UI hint config (column titles,
  visibility) — but the *schema* comes from the query, not these tables.
- Validate by running nomasx1's read paths against v2 and diffing results.
- Migrate nomasx1 first (read-heavy, lower risk), then NOMAJDE, then AIRFLOW.

### Phase 6 — Custom form logic — (deferred, decide after Phases 1–3)
v1 solves field rules + custom actions via `ly_actions` / `ly_act_tasks` /
`ly_cdn_params` / `ly_act_branch` — a workflow DSL stored in the DB. nomaubl has
no strong equivalent. Options to compare once real screens exist:
- **Python plugins** — hook functions registered per form/field event. Most power, needs deploys.
- **Declarative rules** (YAML/TOML) — `when field X = Y, require Z` / `disable on edit` — covers 80%, no deploy.
- **Embedded JS hooks** — like v1's `ly_function`, evaluated client-side. Flexible, harder to test.
Likely a mix: declarative rules for common cases + a Python plugin escape hatch.
**Do not design this in the abstract** — wait for Phases 1–3 to produce screens
to validate against.

## 5. Open questions / parking lot

- Multipart/file upload story in APIConnector — port nomaubl's streaming approach.
- WebSocket vs SSE for live updates (v1 = Socket.IO).
- Secrets handling — v1 uses Fernet + `secrets.json`. Decide: env vars, a secrets
  file, or a vault. Connector auth configs must reference secrets, not inline them.
- Reporting/PDF — v1 has Excel export (`tbl_workbook`/`tbl_sheet`), nomaubl has
  XSLT→PDF via BI Publisher. Out of scope until a user asks.
- JDE Julian date conversion — only if v2 needs to talk to JD Edwards data
  directly (NOMAJDE migration). Port from nomaubl `DynamicResultMapper`.

## 6. How to pick up the work

1. Read `CLAUDE.md` (project root) — it has the current status + run commands.
2. Read this file for the full picture.
3. Phase 1 starts with `liberty/connectors/sql.py` + `liberty/connectors/base.py`
   and a `[connectors.*]` schema in `config/connectors.toml`.
