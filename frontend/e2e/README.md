# End-to-end regression tests

Browser scenarios run with Playwright against a **live** server and its real databases. They
check what the screens actually save, not just that the page renders. They are not part of CI
(`npm test` and the build only look at `src/`); run them by hand before and after a change to
the data screens (the grid, the record form).

```bash
cd frontend
LIBERTY_E2E_USER=… LIBERTY_E2E_PASSWORD=… npm run e2e      # all scenarios, ~25 s
LIBERTY_E2E_USER=… LIBERTY_E2E_PASSWORD=… npx playwright test settings-applications
```

| Variable | Default | |
|---|---|---|
| `LIBERTY_E2E_URL` | `http://127.0.0.1:8000` | the server |
| `LIBERTY_E2E_USER` / `LIBERTY_E2E_PASSWORD` | — | a **superuser**: the database checks use the SQL editor's test route |
| `LIBERTY_E2E_RECORD=1` | off | rewrite the reference rows instead of comparing with them |

## The scenarios

| File | Screens | What it does |
|---|---|---|
| `settings-applications.spec.ts` | nomasx1 Settings → Applications | form: add (4 tabs, the JD Edwards tab saved to `SETTINGS_JDEDWARDS`), a row in the Task Views sub-table (task 91 "test", Collect checked → `SETTINGS_JDE_TV`), Duplicate, Delete; bulk edit: add, change, clone, delete |
| `jde-user-security.spec.ts` | nomajde F0092, F00950 | add user `LYTEST01` (`on_insert` → the `reset_password` AIS call), give it application security on P0092, delete that record; import it from an .xlsx in bulk edit, re-import it changed (an edit, not a new row); the Import dialog's check of two rows on F0092 (one breaks a screen rule), cancelled; a form action with a prompt (Reset Password: cancelled, then confirmed); delete the user (`on_delete` → `delete_user` in every table) |
| `record-lock.spec.ts` | nomajde F0092 | two sessions open user JDE: the second is read-only with "Locked by", no Save, until the first closes — also when it connected after the lock was taken (read-only, nothing saved) |
| `jde-servers.spec.ts` | nomajde Monitoring → JDE Servers | every health check; the period opens on "Last 7 days", then All dates, Last month, Today (one day on a timestamp) — each compared live with the query (read-only) |
| `row-menu.spec.ts` | nomasx1 Security → Users | right-click user JDE → Display Roles: the assignments screen opens filtered on the user (read-only, compared live with the query) |

## Safety

- Only test records are created: applications named `LYTEST-…` (plus id 9901 for the bulk clone),
  JDE user `LYTEST01` (`LYTEST02` / `LYTEST03` only in a cancelled import check). A scenario stops **before touching anything** if one already exists.
- An existing record is never changed: before a bulk-edit Save, every edited or deleted row must
  hold a test value, otherwise the edit is cancelled and the test fails.
- If a scenario fails half way, it deletes what it created (by the test keys only).
- Passwords are never stored or compared (`apps_password`, `F98OWSEC` password columns).

## Reference rows

After each step the scenario reads the rows of the test keys from the database and compares them
with `e2e/.baseline/<step>.json` (git-ignored — they belong to this install's data). The first run,
or `LIBERTY_E2E_RECORD=1`, writes them; read them before trusting them. Columns that change on
every run (update date / time / job, audit date, generated ids) are left out.

A failed run leaves a screenshot, the page snapshot and a trace under `e2e/.results/`
(`npx playwright show-trace e2e/.results/<test>/trace.zip`).
