# Shapes to copy

The reference implementation is **nomademo** in `../liberty-apps` (commits of 2026-10-27):
`plugins/nomademo/{__init__,accounts}.py`, `plugins/nomademo/db/{__init__,models}.py`,
`plugins/nomademo/samples/make_samples.py`, the `nomademo` sections at the end of
`config/{connectors,dictionary,screens,menus,dashboards,explorer,ai}.toml` and
`plugins/nomaflow/jobs.toml`. Copy from there; the snippets below are the shapes.

## Connector

```toml
[pools.<app>]
url = "${<APP>_DB_URL:-postgresql+asyncpg://<app>@pg:5432/<app>}"
password = "${<APP>_DB_PASSWORD:-<app>}"
dialect = "postgresql"
pool_size = 5
max_overflow = 0
max_rows = 500000

[connectors.<app>]
type = "sql"
pool = "<app>"
licensed = false
home = "overview"

[[connectors.<app>.tables]]
name = "dim_item"
label = "Items"
[connectors.<app>.tables.get]
sql = "SELECT item_code, item_name, family_code, family_name FROM dim_item ORDER BY item_code"
[connectors.<app>.tables.post]
sql = "INSERT INTO dim_item (item_code, item_name) VALUES (:ITEM_CODE, :ITEM_NAME)"
writable = true
[connectors.<app>.tables.put]
sql = "UPDATE dim_item SET item_name = :ITEM_NAME, updated_at = now() WHERE item_code = :ITEM_CODE_ORIGINAL"
writable = true
[connectors.<app>.tables.delete]
sql = "DELETE FROM dim_item WHERE item_code = :ITEM_CODE"
writable = true

[[connectors.<app>.queries]]
name = "period_set_status"
sql = "UPDATE period_status SET status = CAST(:STATUS AS VARCHAR(16)), status_by = {{LOGIN}}, status_at = now() WHERE period = :PERIOD"
writable = true

[[connectors.<app>.queries]]
name = "refuse_closed_period"      # the load guard: one row back = refused, first column = the reason
sql = "SELECT 'period ' || period || ' is closed — reopen it before loading' AS reason FROM period_status WHERE period = to_char(make_date(CAST(:YEAR AS integer), CAST(:MONTH AS integer), 1), 'YYYY-MM') AND status = 'closed'"

[[connectors.<app>.queries]]
name = "lookup_company"
sql = "SELECT company_code, company_name, business_unit_code FROM dim_company ORDER BY company_name"
```

## Dictionary

```toml
[connectors.<app>.entries.COMPANY_CODE]
format = "integer"
label = "Company"
rules = "LOOKUP"
rules_values = "get_company"
[connectors.<app>.entries.COMPANY_CODE.l]
fr = "Société"

[connectors.<app>.enums.PERIOD_STATUS]
label = "Period status"
values = [{ value = "open", label = "Open" }, { value = "validated", label = "Validated" }, { value = "closed", label = "Closed" }]

[connectors.<app>.lookups.get_company]
connector = "<app>"
query = "lookup_company"
value = "COMPANY_CODE"
label = "COMPANY_NAME"
key_columns = ["BUSINESS_UNIT_CODE"]
```

## Screens

```toml
[screens.<app>.sales]
id = "sales"
label = "Sales"
read_query = "fact_sales_get"
auto_load = false
read_only = true
max_rows = 5000
columns = [
    { name = "PERIOD", filter = true },
    { name = "BUSINESS_UNIT_CODE", filter = true },
    { name = "COMPANY_CODE", filter = true, filter_from = [{ source = "BUSINESS_UNIT_CODE", column = "BUSINESS_UNIT_CODE" }] },
    { name = "QUANTITY" },
]

[screens.<app>.periods]
id = "periods"
label = "Periods"
read_query = "period_status_get"
update_query = "period_status_put"
auto_load = true
disable_add = true
key_columns = ["PERIOD"]
editable_when = [{ field = "STATUS", value = ["open", "validated"] }]
columns = [{ name = "PERIOD", key = true }, { name = "STATUS", filter = true }, { name = "STATUS_BY", disabled = true }, { name = "COMMENT" }]
[screens.<app>.periods.dialog]
[[screens.<app>.periods.dialog.tabs]]
id = "period"
label = "Period"
type = "form"
cols = 2
fields = [{ name = "PERIOD" }, { name = "STATUS" }, { name = "STATUS_BY" }, { name = "COMMENT" }]
[[screens.<app>.periods.row_menu]]
id = "close"
label = "Close"
type = "chain"
steps = [
    { id = "ask", type = "confirm", message = "Close this period?" },
    { id = "set", type = "run_query", connector = "<app>", query = "period_set_status", param_binds = [{ param = "PERIOD", source = "PERIOD" }, { param = "STATUS", value = "closed" }] },
    { id = "done", type = "notify", message = "Period closed.", tone = "ok" },
]

# a widget screen (no menu entry): the dashboard filter binds through its dd + filter column
[screens.<app>.dash_period_totals]
id = "dash_period_totals"
label = "Totals per period and business unit"
read_query = "dash_period_totals"
read_only = true
columns = [{ name = "PERIOD" }, { name = "BUSINESS_UNIT_CODE", dd = "BUSINESS_UNIT_CODE", filter = true }, { name = "QUANTITY" }]
```

## Menu

```toml
[menus.<app>]
label = "Sales demo"
[[menus.<app>.items]]
id = "overview"
label = "Overview"
type = "dashboard"
target = "<app>.overview"
[[menus.<app>.items]]
id = "sales"
label = "Sales"
[[menus.<app>.items]]
id = "sales_lines"
parent = "sales"
label = "Sales lines"
type = "screen"
target = "sales"
[[menus.<app>.items]]
id = "explorer"
parent = "sales"
label = "Data Explorer"
type = "page"
target = "/explorer"
```

## Dashboard

```toml
[dashboards.<app>.overview]
label = "Sales overview"
  [[dashboards.<app>.overview.filters]]
  id = "business_unit"
  label = "Business unit"
  dictionary_key = "BUSINESS_UNIT_CODE"
    [dashboards.<app>.overview.filters.options]
    connector = "<app>"
    query = "lookup_business_unit"
    value_column = "BUSINESS_UNIT_CODE"
    label_column = "BUSINESS_UNIT_NAME"
  [[dashboards.<app>.overview.widgets]]
  type = "kpi"
  label = "Quantity sold"
  connector = "<app>"
  query = "dash_period_totals"
  column = "QUANTITY"
  aggregation = "sum"
  col_span = 3
  row_span = 1
  [[dashboards.<app>.overview.widgets]]
  type = "chart"
  label = "Monthly quantity"
  connector = "<app>"
  query = "dash_period_totals"
  col_span = 7
  row_span = 2
    [dashboards.<app>.overview.widgets.spec]
    type = "line"
    x = "PERIOD"
    y = ["QUANTITY"]
    aggregation = "sum"
    sort_by_x = true
  [[dashboards.<app>.overview.widgets]]
  type = "table"
  label = "Periods"
  connector = "<app>"
  query = "period_status_get"
  columns = ["PERIOD", "STATUS", "LINES"]
  col_span = 5
  row_span = 2
```

## Explorer

```toml
[explorer.<app>.sales]
label = "Sales"
hub = "sales"
[explorer.<app>.sales.entities.sales]
label = "Sales lines"
table = "fact_sales"
key = ["YEAR", "MONTH", "INVOICE_LINE_ID"]
default_date = "PERIOD_DATE"
hidden = ["BATCH_ID", "LOADED_AT"]
[explorer.<app>.sales.entities.sales.measures]
QUANTITY = "sum"
REVENUE = "sum"
[explorer.<app>.sales.entities.company]
label = "Company"
table = "dim_company"
key = ["COMPANY_CODE"]
[[explorer.<app>.sales.relations]]
from = "sales"
to = "company"
on = [["COMPANY_CODE", "COMPANY_CODE"]]
[explorer.<app>.sales.analyses.quantity_by_business_unit]
version = 1
label = "Quantity by business unit"
audience = "sales"
definition = { entity = "sales", view = "table", rows = [{ field = "company.BUSINESS_UNIT_NAME" }], measures = [{ field = "sales.QUANTITY", agg = "sum" }], filterRows = [], period = { field = "sales.PERIOD_DATE", preset = "all", range = { dateFrom = "", dateTo = "" } }, sort = [{ key = "sum(sales.QUANTITY)", dir = "desc" }], limit = 100, calculated = [] }
[explorer.<app>.sales.dashboards.sales_overview]
label = "Sales overview"
definition = { tiles = [{ analysis = "quantity_by_business_unit", w = 2, h = "m" }] }
```

## A connector on an existing database (no model, the source's tables)

```toml
[pools.erp]
url = "${ERP_DB_URL}"                       # oracle+oracledb://user@host:1521/?service_name=…
password = "${ERP_DB_PASSWORD}"
dialect = "oracle"
trim_strings = true                         # CHAR(N) codes come back trimmed, binds are padded back
[pools.erp.schemas]                         # #SCHEMA.X# placeholders in the SQL
CTL = "PS920CTL"
DTA = "PS920DTA"

[connectors.erp]
type = "sql"
pool = "erp"
dd_prefix = 2                               # F0101.ABAN8 carries the data item AN8
licensed = true

[[connectors.erp.tables]]
name = "f0101"
label = "Address Book"
[connectors.erp.tables.get]
sql = "SELECT ABAN8, ABALPH, ABAT1, ABMCU FROM #SCHEMA.DTA#.F0101 ORDER BY ABAN8"
```

## Jobs

```toml
[[jobs]]
id = "<app>-init-db"
enabled = false
tags = ["<app>", "install"]
[[jobs.steps]]
type = "python"
name = "init-schema"
callable = "<app>.db:init_schema"
[jobs.steps.op_kwargs]
target_connector = "<app>"

[[jobs]]
id = "<app>-load-sales"
enabled = false
tags = ["<app>", "load"]
[[jobs.steps]]
type = "file_load"
name = "load"
connector = "<app>"
sheet = "Sales"
load_mode = "replace_partition"
partition_by = ["year", "month", "company_code"]
refuse_query = "refuse_closed_period"
on_error = "skip"
[jobs.steps.target]
connector = "<app>"
table = "fact_sales"
[jobs.steps.constants]
batch_id = "${run.id}"
[[jobs.steps]]
type = "sql_query"
name = "periods"
connector = "<app>"
query = "period_status_refresh"

# a copy from an existing database into the app's own tables, on a schedule
[[jobs]]
id = "<app>-daily-sync"
schedule = "30 2 * * *"
timezone = "Europe/Paris"
enabled = false
tags = ["<app>", "sync"]
[[jobs.steps]]
type = "sql_copy"
name = "F0101"
mode = "overwrite"                          # overwrite | append | upsert
type_coercion = "jde"                       # Julian dates, padded codes
batch_size = 10000
[jobs.steps.source]
connector = "erp"
schema = "PS920DTA"
table = "F0101"
[jobs.steps.target]
connector = "<app>"
table = "f0101"

[[jobs]]
id = "<app>-monthly-digest"
enabled = false
tags = ["<app>", "digest"]
[[jobs.steps]]
type = "report"
name = "revenue"
report = "analysis:revenue_by_business_unit"
app = "<app>"
model = "sales"
format = "xlsx"
period = "lastMonth"
as_role = "<app>_group"
[[jobs.steps]]
type = "notify"
name = "send"
recipients = ["role:<app>_group"]
subject = "Sales digest {DATE} — {JOB}"
body = "Attached: {FILES}."
```

## Assistant material

```toml
[ai.<app>]
starters = ["Which business unit sold the most last month?", "How do I load a new month of sales?"]
[ai.<app>.screens.periods]
starters = ["How do I close a period?"]
[ai.<app>.playbooks.load_sales_month]
title = "Load a month of sales"
description = "A new month, or a month to reload after an error."
body = """
1. …
"""
```

## Validation (run from liberty-next, before any server run)

```python
# LIBERTY_APPS_DIR=$PWD/../liberty-apps/config .venv/bin/python - <<'EOF'
import os, sys, tomllib
from pathlib import Path
sys.path.insert(0, "../liberty-apps/plugins")
cfg = Path(os.environ["LIBERTY_APPS_DIR"])
from liberty.connectors.config import ConnectorsFile
from liberty.connectors.dictionary import DictionaryFile
from liberty.screens.config import ScreensFile
from liberty.menus.config import MenusFile
from liberty.dashboards.config import parse_dashboards
from liberty.explorer import parse_explorer
from liberty.explorer.report import definition_to_aggregate
from liberty.jobs.schema import JobsFile
from liberty.ai.appconfig import load_ai_config
load = lambda n: tomllib.loads((cfg / n).read_text())
ConnectorsFile.model_validate(load("connectors.toml")); DictionaryFile.model_validate(load("dictionary.toml"))
ScreensFile.model_validate(load("screens.toml")); MenusFile.model_validate(load("menus.toml"))
parse_dashboards(load("dashboards.toml"))
for app, mid, m in parse_explorer(load("explorer.toml")).items():
    for aid, b in m.analyses.items():
        definition_to_aggregate(dict(b.definition))     # raises on a bad field / sort key
JobsFile.model_validate(tomllib.loads((cfg / "../plugins/nomaflow/jobs.toml").read_text()))
load_ai_config(cfg / "ai.toml")
import importlib; importlib.import_module("<app>.db"); importlib.import_module("<app>.accounts")
print("all valid")
# EOF
```
