"""The assistant's write tools (liberty.ai.write_tools over liberty.screens.save): one record
added, changed or deleted through its screen — the screen's queries, the form's rules — behind
three locks ([ai] write_tools, `ai:write`, the queries' own permissions); proposed in the chat,
written over MCP."""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from liberty.ai.tools import current_channel
from liberty.auth.db import AuthDatabase
from liberty.auth.principal import current_principal
from liberty.auth.routes import principal_for
from liberty.auth.service import AuthService
from liberty.config import ActionSettings, AISettings, AppSettings, AuthSettings, ConnectorSettings, ScreenSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

CONNECTORS = """
[pools.default]
url = "{db_url}"

[connectors.shop]
type = "sql"
pool = "default"

[connectors.svc]
type = "api"
base_url = "https://svc.test"

[[connectors.svc.endpoints]]
name = "notify"
method = "POST"
path = "/notify"
body = '{{"who": "{{{{who}}}}"}}'

[[connectors.svc.endpoints]]
name = "boom"
method = "GET"
path = "/boom"

[[connectors.shop.queries]]
name = "customers_get"
sql = "SELECT c.id AS ID, c.name AS NAME, c.status AS STATUS, c.kind AS KIND, c.sub AS SUB, c.vat AS VAT, c.secret AS SECRET, c.klabel AS KLABEL, e.cust_id AS CUST_ID, e.phone AS PHONE FROM customer c LEFT JOIN customer_extra e ON e.cust_id = c.id ORDER BY c.id"

[[connectors.shop.queries]]
name = "kinds_get"
sql = "SELECT code AS CODE, label AS LABEL FROM kind ORDER BY code"

# a connector that returns two rows at most: the list of SUBS (three rows) comes back cut
[connectors.tiny]
type = "sql"
pool = "default"
max_rows = 2

[[connectors.tiny.queries]]
name = "subs_get"
sql = "SELECT code AS CODE, kind AS KIND FROM sub ORDER BY code"

[[connectors.shop.queries]]
name = "customers_post"
sql = "INSERT INTO customer (id, name, status, kind, sub, vat, secret, klabel) VALUES (:ID, :NAME, :STATUS, :KIND, :SUB, :VAT, :SECRET, :KLABEL)"
writable = true

[[connectors.shop.queries]]
name = "customers_put"
sql = "UPDATE customer SET id = :ID, name = :NAME, status = :STATUS, kind = :KIND, sub = :SUB, vat = :VAT, klabel = :KLABEL WHERE id = :ID_ORIGINAL"
writable = true

[[connectors.shop.queries]]
name = "customers_delete"
sql = "DELETE FROM customer WHERE id = :ID"
writable = true

[[connectors.shop.queries]]
name = "customers_set_status"
sql = "UPDATE customer SET status = :STATUS WHERE id = :ID"
writable = true

[[connectors.shop.queries]]
name = "customers_rename"
sql = "UPDATE customer SET name = :NAME WHERE id = :ID"
writable = true

[[connectors.shop.queries]]
name = "customers_one"
sql = "SELECT status AS STATUS, name AS NAME FROM customer WHERE id = :ID"

[[connectors.shop.queries]]
name = "log_post"
sql = "INSERT INTO log (cust_id, who, what) VALUES (:CUST_ID, :WHO, :WHAT)"
writable = true

[[connectors.shop.queries]]
name = "extras_get"
sql = "SELECT cust_id AS CUST_ID, phone AS PHONE FROM customer_extra ORDER BY cust_id"

[[connectors.shop.queries]]
name = "kinds_reserved"
sql = 'SELECT code AS "ORDER", label AS LABEL FROM kind'

[[connectors.shop.queries]]
name = "extra_post"
sql = "INSERT INTO customer_extra (cust_id, phone) VALUES (:CUST_ID, :PHONE)"
writable = true

[[connectors.shop.queries]]
name = "extra_put"
sql = "UPDATE customer_extra SET phone = :PHONE WHERE cust_id = :CUST_ID_ORIGINAL"
writable = true

[[connectors.shop.queries]]
name = "extra_delete"
sql = "DELETE FROM customer_extra WHERE cust_id = :CUST_ID"
writable = true
"""

SCREENS = """
[screens.shop.customers]
label = "Customers"
read_query = "customers_get"
insert_query = "customers_post"
update_query = "customers_put"
delete_query = "customers_delete"
editable_when = [{ field = "STATUS", value = ["A", "I"] }]

[[screens.shop.customers.columns]]
name = "ID"
key = true
disable_on_edit = true

[[screens.shop.customers.columns]]
name = "NAME"
label = "Name"
required = true

[[screens.shop.customers.columns]]
name = "STATUS"
rules = "ENUM"
rules_values = "STATUS"
default = "A"

# picking a kind also fills its label on the record, from the picked row of the list
[[screens.shop.customers.columns]]
name = "KIND"
rules = "LOOKUP"
rules_values = "KINDS"
return_binds = [{ param = "LABEL", column = "KLABEL" }]

[[screens.shop.customers.columns]]
name = "KLABEL"

[[screens.shop.customers.columns]]
name = "SUB"
rules = "LOOKUP"
rules_values = "SUBS"
lookup_param_binds = [{ param = "KIND", source = "KIND" }]

[[screens.shop.customers.columns]]
name = "VAT"
label = "VAT number"
sensitive = true
visible_when = [{ field = "KIND", value = "B" }]
required_when = [{ field = "KIND", value = "B" }]

[[screens.shop.customers.columns]]
name = "SECRET"
format = "password"

[[screens.shop.customers.columns]]
name = "PHONE"
group = "extra"

[[screens.shop.customers.column_groups]]
id = "extra"
insert_query = "extra_post"
update_query = "extra_put"
delete_query = "extra_delete"
key_columns = ["CUST_ID"]
param_binds = [{ param = "CUST_ID", source = "ID" }]

# a tab of the customer's form that is another screen: its link column is hidden and locked there
[screens.shop.customers.dialog]
title = "Customer"

[[screens.shop.customers.dialog.tabs]]
id = "phones"
label = "Phones"
screen = "extras"
param_binds = [{ param = "CUST_ID", source = "ID" }]

# the row menu: a confirmed change, a condition on what a query read, a shared action, a prompt, a drill
[[screens.shop.customers.row_menu]]
id = "block"
label = "Block"
type = "chain"
steps = [
  { id = "ask", type = "confirm", message = "Block {NAME} on behalf of #LOGIN_USER#?" },
  { id = "set", type = "run_query", query = "customers_set_status", param_binds = [{ param = "ID", source = "ID" }, { param = "STATUS", value = "I" }] },
  { id = "done", type = "notify", message = "{NAME} is blocked." },
]

[[screens.shop.customers.row_menu]]
id = "toggle"
label = "Toggle"
type = "chain"
steps = [
  { id = "cur", type = "run_query", query = "customers_one", bind_result = true, param_binds = [{ param = "ID", source = "ID" }] },
  { id = "which", type = "if", condition = { source = "cur.first_row.STATUS", operator = "equals", value = "A" }, then_steps = [
      { id = "off", type = "run_query", query = "customers_set_status", param_binds = [{ param = "ID", source = "ID" }, { param = "STATUS", value = "I" }] },
    ], else_steps = [
      { id = "on", type = "run_query", query = "customers_set_status", param_binds = [{ param = "ID", source = "ID" }, { param = "STATUS", value = "A" }] },
    ] },
]

[[screens.shop.customers.row_menu]]
id = "audit"
label = "Audit"
type = "call_action"
ref = "log_it"
param_binds = [{ param = "cust", source = "ID" }, { param = "what", value = "audited" }]

[[screens.shop.customers.row_menu]]
id = "rename"
label = "Rename"
type = "run_query"
query = "customers_rename"
param_binds = [{ param = "ID", source = "ID" }, { param = "NAME", source = "INPUT.NEW_NAME" }]
prompt_fields = [{ name = "NEW_NAME", label = "New name", required = true }]

[[screens.shop.customers.row_menu]]
id = "broken"
label = "Broken"
type = "chain"
steps = [
  { id = "set", type = "run_query", query = "customers_set_status", param_binds = [{ param = "ID", source = "ID" }, { param = "STATUS", value = "I" }] },
  { id = "boom", type = "run_query", query = "no_such_query" },
  { id = "never", type = "run_query", query = "customers_rename", param_binds = [{ param = "ID", source = "ID" }, { param = "NAME", value = "never" }] },
]

[[screens.shop.customers.row_menu]]
id = "drill"
label = "Open the orders"
type = "navigate"
to = "customers_get"

[screens.shop.extras]
label = "Phones"
read_query = "extras_get"
insert_query = "extra_post"
delete_query = "extra_delete"

[[screens.shop.extras.columns]]
name = "CUST_ID"
key = true
hidden = true
disabled = true

[[screens.shop.extras.columns]]
name = "PHONE"
key = true

# a screen whose save runs actions: a hook after the insert and after the delete
[screens.shop.hooked]
label = "With save actions"
read_query = "customers_get"
insert_query = "customers_post"
update_query = "customers_put"
delete_query = "customers_delete"

[[screens.shop.hooked.on_insert]]
id = "say"
type = "notify"
message = "{NAME} was created."

[[screens.shop.hooked.on_insert]]
id = "log"
type = "call_action"
ref = "log_it"
param_binds = [{ param = "cust", source = "ID" }, { param = "what", value = "created" }]

[[screens.shop.hooked.on_delete]]
id = "log"
type = "run_query"
query = "log_post"
param_binds = [{ param = "CUST_ID", source = "ID" }, { param = "WHO", source = "#LOGIN_USER#" }, { param = "WHAT", value = "deleted" }]

# actions whose steps call an API endpoint and a plugin, and use what they answer
[[screens.shop.hooked.row_menu]]
id = "ping"
label = "Ping"
type = "chain"
steps = [
  { id = "api", type = "call_api", connector = "svc", endpoint = "notify", bind_result = true, param_binds = [{ param = "who", source = "NAME" }] },
  { id = "keep", type = "run_query", query = "customers_rename", param_binds = [{ param = "ID", source = "ID" }, { param = "NAME", source = "api.first_row.echo" }] },
]

[[screens.shop.hooked.row_menu]]
id = "ping_fail"
label = "Ping (fails)"
type = "chain"
steps = [
  { id = "api", type = "call_api", connector = "svc", endpoint = "boom" },
  { id = "never", type = "run_query", query = "customers_rename", param_binds = [{ param = "ID", source = "ID" }, { param = "NAME", value = "never" }] },
]

[[screens.shop.hooked.row_menu]]
id = "stamp"
label = "Stamp"
type = "chain"
steps = [
  { id = "p", type = "call_plugin", callable = "wtplg.audit:j_stamp", bind_result = true, param_binds = [{ param = "cust", source = "ID" }, { param = "note", value = "from the menu" }] },
  { id = "log", type = "run_query", query = "log_post", param_binds = [{ param = "CUST_ID", source = "p.first_row.cust" }, { param = "WHO", source = "#LOGIN_USER#" }, { param = "WHAT", source = "p.first_row.stamped" }] },
]

[[screens.shop.hooked.columns]]
name = "ID"
key = true
"""

ACTIONS = """
[actions.log_it]
label = "Log it"
params = [{ name = "cust" }, { name = "what", default = "seen" }, { name = "who", default = "#LOGIN_USER#" }]

[[actions.log_it.steps]]
id = "write"
type = "run_query"
connector = "shop"
query = "log_post"
param_binds = [{ param = "CUST_ID", source = "INPUT.cust" }, { param = "WHO", source = "INPUT.who" }, { param = "WHAT", source = "INPUT.what" }]
"""

DICTIONARY = """
default_language = "en"

[entries.NAME]
sensitive = true

[lookups.KINDS]
query = "kinds_get"
connector = "shop"
value = "CODE"
label = "LABEL"

[lookups.SUBS]
query = "subs_get"
connector = "tiny"
value = "CODE"
label = "CODE"

[enums.STATUS]
label = "Status"
values = [{ value = "A", label = "Active" }, { value = "I", label = "Inactive" }, { value = "X", label = "Archived" }]
"""


def _app(tmp_path, *, write_tools: bool = True, hide_sensitive: bool = False, allowed: list[str] | None = None):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    (tmp_path / "connectors.toml").write_text(CONNECTORS.format(db_url=db_url))
    (tmp_path / "screens.toml").write_text(textwrap.dedent(SCREENS))
    (tmp_path / "dictionary.toml").write_text(DICTIONARY)
    (tmp_path / "actions.toml").write_text(ACTIONS)

    async def seed() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with pools.engine("default").begin() as conn:
            await conn.execute(text("CREATE TABLE customer (id INTEGER PRIMARY KEY, name TEXT, status TEXT, kind TEXT, sub TEXT, vat TEXT, secret TEXT, klabel TEXT)"))
            await conn.execute(text("CREATE TABLE kind (code TEXT, label TEXT)"))
            await conn.execute(text("CREATE TABLE sub (code TEXT, kind TEXT)"))
            await conn.execute(text("INSERT INTO kind VALUES ('B ','Business'),('P','Person')"))        # 'B ' padded, as a JDE code reads back
            await conn.execute(text("INSERT INTO sub VALUES ('B1','B'),('B2','B'),('P1','P')"))
            await conn.execute(text("CREATE TABLE customer_extra (cust_id INTEGER PRIMARY KEY, phone TEXT)"))
            await conn.execute(text("CREATE TABLE log (cust_id INTEGER, who TEXT, what TEXT)"))
            await conn.execute(text("INSERT INTO customer VALUES (1,'Acme','A','B',NULL,'FR1','s3cret',NULL),(2,'Bolt','A','P',NULL,NULL,NULL,NULL),(3,'Old','X','P',NULL,NULL,NULL,NULL)"))
            await conn.execute(text("INSERT INTO customer_extra VALUES (1,'0101')"))
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("writer", permissions=["ai:chat", "ai:write", "mcp:use", "sql:shop:*", "sql:tiny:*"])
            await svc.get_or_create_role("reader", permissions=["ai:chat", "ai:write", "sql:shop:customers_get"])
            await svc.get_or_create_role("halfway", permissions=["ai:chat", "ai:write", "sql:shop:customers_get", "sql:shop:customers_post", "sql:shop:customers_one"])
            await svc.get_or_create_role("nowrite", permissions=["ai:chat", "sql:shop:*"])
            await svc.get_or_create_role("power", permissions=["ai:chat", "ai:write", "sql:shop:*", "api:svc:*", "plugin:*"])
            for name in ("writer", "reader", "nowrite", "halfway", "power"):
                await svc.create_user(name, password=f"{name}pw-123", roles=[name])
        await pools.dispose()

    asyncio.run(seed())
    return create_app(Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml"), dictionary_path=Path(tmp_path / "dictionary.toml")),
        screens=ScreenSettings(config_path=Path(tmp_path / "screens.toml")),
        actions=ActionSettings(config_path=Path(tmp_path / "actions.toml")),
        auth=AuthSettings(backend="db", jwt_secret="write-tools-test-secret-0123456789abcdef", pool="default"),
        ai=AISettings(enabled=True, api_key="", config_path=tmp_path / "ai.toml", write_tools=write_tools, mcp_enabled=True, hide_sensitive=hide_sensitive, allowed_connectors=allowed or []),
    ))


@pytest.fixture
def app(tmp_path):
    return _app(tmp_path)


def _h(client: TestClient, user: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + client.post("/auth/login", json={"username": user, "password": f"{user}pw-123"}).json()["access_token"]}


def _write(client: TestClient, user: str, **body):
    return client.post("/ai/screen-write", json={"app": "shop", "screen": "customers", **body}, headers=_h(client, user))


def _rows(client: TestClient, where: str = "1=1", table: str = "customer") -> list[dict]:
    async def go() -> list[dict]:
        async with client.app.state.connectors.pools.engine("default").connect() as conn:
            return [dict(r._mapping) for r in (await conn.execute(text(f"SELECT * FROM {table} WHERE {where} ORDER BY 1"))).all()]
    return client.portal.call(go)


def _refused(r, *words: str) -> bool:
    return r.status_code == 409 and all(w in r.json()["detail"] for w in words)


def test_an_update_changes_only_what_is_given(app) -> None:
    with TestClient(app) as client:
        r = _write(client, "writer", operation="update", key={"ID": 2}, values={"name": "Bolt Ltd"})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["written"] is True and out["rowcount"] == 1
        assert out["key"] == {"ID": 2} and out["values"] == {"NAME": "Bolt Ltd"} and out["before"] == {"NAME": "Bolt"}
        assert _rows(client, "id = 2") == [{"id": 2, "name": "Bolt Ltd", "status": "A", "kind": "P", "sub": None, "vat": None, "secret": None, "klabel": None}]
        # the other records are untouched, and a stored secret is never overwritten by the row that was read
        assert _rows(client, "id = 1")[0]["secret"] == "s3cret" and _rows(client, "id = 1")[0]["name"] == "Acme"
        r = _write(client, "writer", operation="update", key={"ID": 1}, values={"NAME": "Acme SA"})
        assert r.status_code == 200 and _rows(client, "id = 1")[0]["secret"] == "s3cret"
        # the same values again: nothing to write
        again = _write(client, "writer", operation="update", key={"ID": 1}, values={"NAME": "Acme SA"}).json()
        assert again["written"] is False and "nothing to change" in again["note"]


def test_a_dry_run_checks_and_writes_nothing(app) -> None:
    with TestClient(app) as client:
        out = _write(client, "writer", operation="update", key={"ID": 2}, values={"NAME": "Zed"}, dry_run=True).json()
        assert out["written"] is False and out["values"] == {"NAME": "Zed"} and out["before"] == {"NAME": "Bolt"}
        assert _rows(client, "id = 2")[0]["name"] == "Bolt"


def test_an_insert_takes_the_defaults_and_writes_the_related_table(app) -> None:
    with TestClient(app) as client:
        r = _write(client, "writer", operation="insert", values={"ID": 10, "NAME": "New", "KIND": "P", "PHONE": "0606"})
        assert r.status_code == 200, r.text
        assert r.json()["values"]["STATUS"] == "A" and r.json()["related_tables"] == ["extra"]        # the column's default
        assert _rows(client, "id = 10") == [{"id": 10, "name": "New", "status": "A", "kind": "P", "sub": None, "vat": None, "secret": None, "klabel": "Person"}]
        assert _rows(client, "cust_id = 10", "customer_extra") == [{"cust_id": 10, "phone": "0606"}]   # linked by the group's bind
        # an update of the related column: the row exists (1) → updated; it does not (2) → inserted
        assert _write(client, "writer", operation="update", key={"ID": 1}, values={"PHONE": "0102"}).status_code == 200
        assert _write(client, "writer", operation="update", key={"ID": 2}, values={"PHONE": "0202"}).status_code == 200
        assert _rows(client, table="customer_extra") == [{"cust_id": 1, "phone": "0102"}, {"cust_id": 2, "phone": "0202"}, {"cust_id": 10, "phone": "0606"}]


def test_the_forms_rules_refuse(app) -> None:
    with TestClient(app) as client:
        before = _rows(client)
        assert _refused(_write(client, "writer", operation="insert", values={"ID": 11, "KIND": "P"}), "required", "Name (NAME)")
        assert _refused(_write(client, "writer", operation="update", key={"ID": 2}, values={"NAME": ""}), "required", "NAME")
        # a list: only its values
        assert _refused(_write(client, "writer", operation="update", key={"ID": 2}, values={"STATUS": "ZZ"}), "not one of its values", "'A'")
        # required_when / visible_when follow the record's other values
        assert _refused(_write(client, "writer", operation="update", key={"ID": 2}, values={"KIND": "B"}), "required", "VAT number")
        assert _refused(_write(client, "writer", operation="update", key={"ID": 2}, values={"VAT": "FR9"}), "does not apply")
        assert _write(client, "writer", operation="update", key={"ID": 2}, values={"KIND": "B", "VAT": "FR9"}).status_code == 200
        _write(client, "writer", operation="update", key={"ID": 2}, values={"KIND": "P"})
        # a column locked on edit, a column the screen does not write, a record the screen holds read-only
        assert _refused(_write(client, "writer", operation="update", key={"ID": 1}, values={"ID": 99}), "cannot be changed")
        assert _refused(_write(client, "writer", operation="update", key={"ID": 1}, values={"COLOUR": "red"}), "not written by", "NAME")
        assert _refused(_write(client, "writer", operation="update", key={"ID": 3}, values={"NAME": "x"}), "read-only")
        assert _refused(_write(client, "writer", operation="delete", key={"ID": 3}), "read-only")
        # the record must exist, and be named by its whole key
        assert _refused(_write(client, "writer", operation="update", key={"ID": 404}, values={"NAME": "x"}), "no record")
        assert _refused(_write(client, "writer", operation="update", key={}, values={"NAME": "x"}), "key")
        after = _rows(client)
        assert [r for r in after if r["id"] != 2] == [r for r in before if r["id"] != 2]


def test_a_delete_removes_the_related_row_first(app) -> None:
    with TestClient(app) as client:
        preview = _write(client, "writer", operation="delete", key={"ID": 1}, dry_run=True).json()
        assert preview["written"] is False and preview["record"]["NAME"] == "Acme" and preview["record"]["SECRET"] != "s3cret"
        assert preview["related_tables"] == ["extra"] and len(_rows(client)) == 3
        out = _write(client, "writer", operation="delete", key={"ID": 1}).json()
        assert out["written"] is True and out["rowcount"] == 1 and "record" not in out
        assert [r["id"] for r in _rows(client)] == [2, 3] and _rows(client, table="customer_extra") == []


def _save(client: TestClient, user: str, screen: str, **body):
    return client.post("/ai/screen-write", json={"app": "shop", "screen": screen, **body}, headers=_h(client, user))


def _act(client: TestClient, user: str, action: str, **body):
    return client.post("/ai/screen-action", json={"app": "shop", "screen": "customers", "action": action, **body}, headers=_h(client, user))


def test_a_save_runs_the_screens_hooks(app) -> None:
    """on_insert / on_delete run on the server after the write, as the form runs them: the
    record as written is their context, a shared action takes its params and its defaults."""
    with TestClient(app) as client:
        preview = _save(client, "writer", "hooked", operation="insert", values={"ID": 20, "NAME": "Hook", "STATUS": "A"}, dry_run=True).json()
        assert preview["written"] is False and preview["then_runs"]["does"] == ["query shop.log_post"]
        assert _rows(client, "id = 20") == [] and _rows(client, table="log") == []          # a dry run runs no hook
        out = _save(client, "writer", "hooked", operation="insert", values={"ID": 20, "NAME": "Hook", "STATUS": "A"}).json()
        assert out["written"] is True and out["actions"]["ok"] is True and "then_runs" not in out
        assert _rows(client, table="log") == [{"cust_id": 20, "who": "writer", "what": "created"}]   # #LOGIN_USER#, the caller's bind
        out = _save(client, "writer", "hooked", operation="delete", key={"ID": 20}).json()
        assert out["written"] is True and out["actions"]["ok"] is True
        assert _rows(client, "id = 20") == [] and _rows(client, "what = 'deleted'", "log") == [{"cust_id": 20, "who": "writer", "what": "deleted"}]
        # a hook the user may not run: the record is written, the answer says the hook failed
        out = _save(client, "halfway", "hooked", operation="insert", values={"ID": 21, "NAME": "Half"}).json()
        assert out["written"] is True and out["actions"]["ok"] is False and "sql:shop:log_post" in out["actions"]["error"]
        assert "was written" in out["note"] and len(_rows(client, "id = 21")) == 1 and _rows(client, "cust_id = 21", "log") == []


def test_an_action_of_the_row_menu_runs_as_in_the_screen(app) -> None:
    with TestClient(app) as client:
        # a dry run reads the record and says what would run — the screen's confirmation, filled
        preview = _act(client, "writer", "block", key={"ID": 2}, dry_run=True).json()
        assert preview["ran"] is False and preview["key"] == {"ID": 2} and preview["label"] == "Block"
        assert preview["confirms"] == ["Block Bolt on behalf of writer?"] and preview["does"] == ["query <the screen connector>.customers_set_status"]
        assert _rows(client, "id = 2")[0]["status"] == "A"
        out = _act(client, "writer", "BLOCK", key={"ID": 2}).json()
        assert out["ran"] is True and out["ok"] is True and out["messages"] == ["Bolt is blocked."] and out["confirmed"] == ["Block Bolt on behalf of writer?"]
        assert _rows(client, "id = 2")[0]["status"] == "I" and _rows(client, "id = 1")[0]["status"] == "A"
        # a step reads, the next decides on what it read
        assert _act(client, "writer", "toggle", key={"ID": 2}).json()["ok"] is True and _rows(client, "id = 2")[0]["status"] == "A"
        assert _act(client, "writer", "toggle", key={"ID": 2}).json()["ok"] is True and _rows(client, "id = 2")[0]["status"] == "I"
        # a shared action: the caller's binds, the action's own defaults
        assert _act(client, "writer", "audit", key={"ID": 1}).json()["ok"] is True
        assert _rows(client, table="log") == [{"cust_id": 1, "who": "writer", "what": "audited"}]
        # an action runs on a record the form holds read-only (the screen's menu does too)
        assert _act(client, "writer", "toggle", key={"ID": 3}).json()["ok"] is True and _rows(client, "id = 3")[0]["status"] == "A"


def test_an_action_asks_its_values_and_says_what_failed(app) -> None:
    with TestClient(app) as client:
        r = _act(client, "writer", "rename", key={"ID": 2})                      # the prompt's value is missing: nothing runs
        assert r.status_code == 409 and "NEW_NAME" in r.json()["detail"] and _rows(client, "id = 2")[0]["name"] == "Bolt"
        assert _act(client, "writer", "rename", key={"ID": 2}, inputs={"new_name": "Bolt 2"}).json()["ok"] is True
        assert _rows(client, "id = 2")[0]["name"] == "Bolt 2"
        # a failing step stops the chain: what ran before stays, what comes after does not run
        out = _act(client, "writer", "broken", key={"ID": 1}).json()
        assert out["ran"] is True and out["ok"] is False and "no_such_query" in out["error"] and len(out["steps"]) == 1
        assert _rows(client, "id = 1")[0]["status"] == "I" and _rows(client, "id = 1")[0]["name"] == "Acme"
        # each step under its own permission
        out = _act(client, "halfway", "toggle", key={"ID": 2}).json()
        assert out["ok"] is False and "sql:shop:customers_set_status" in out["error"]
        # unknown, a drill (the browser's), a record that is not there, no ai:write
        assert _act(client, "writer", "nope", key={"ID": 2}).status_code == 409
        r = _act(client, "writer", "drill", key={"ID": 2})
        assert r.status_code == 409 and "block" in r.json()["detail"] and "drill" not in r.json()["detail"].split("its actions:")[1]
        assert _act(client, "writer", "block", key={"ID": 404}).status_code == 409
        assert _act(client, "nowrite", "block", key={"ID": 2}).status_code == 403


def test_the_three_locks(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        body = {"operation": "update", "key": {"ID": 2}, "values": {"NAME": "x"}}
        assert _write(client, "nowrite", **body).status_code == 403                    # no ai:write
        r = _write(client, "reader", **body)                                           # ai:write, not the screen's update query
        assert r.status_code == 403 and "sql:shop:customers_put" in r.json()["detail"]
        assert client.post("/ai/screen-write", json={"app": "shop", "screen": "customers", **body}).status_code == 401
        assert _rows(client, "id = 2")[0]["name"] == "Bolt"
        # the tools are offered to who holds ai:write
        names = lambda user: {t.name for t in app.state.ai.tools.offered(_principal(client, user))}   # noqa: E731
        assert {"screen_form", "screen_write"} <= names("writer") and not ({"screen_form", "screen_write"} & names("nowrite"))
    off = _app(tmp_path / "off", write_tools=False) if (tmp_path / "off").mkdir() is None else None
    with TestClient(off) as client:
        assert _write(client, "writer", operation="update", key={"ID": 2}, values={"NAME": "x"}).status_code == 404
        assert off.state.ai.tools.get("screen_write") is None


def _principal(client: TestClient, user: str):
    async def go():
        return principal_for(await client.app.state.auth_backend.get_by_username(user))
    return client.portal.call(go)


def _tool(client: TestClient, user: str, name: str, arguments: dict, channel: str = "chat"):
    who = _principal(client, user)

    async def go():
        current_principal.set(who)
        current_channel.set(channel)
        content, is_error = await client.app.state.ai.tools.execute(name, arguments)
        return json.loads(content), is_error
    return client.portal.call(go)


def test_screen_form_says_what_the_screen_lets_enter(app) -> None:
    with TestClient(app) as client:
        out, err = _tool(client, "writer", "screen_form", {"app": "shop", "screen": "customers"})
        assert not err, out
        assert out["key_columns"] == ["ID"] and out["operations"] == {"insert": True, "update": True, "delete": True}
        by = {f["name"]: f for f in out["fields"]}
        assert by["ID"]["key"] is True and by["ID"]["read_only_on"] == "update"
        assert by["NAME"]["required"] is True and by["NAME"]["label"] == "Name"
        assert [v["value"] for v in by["STATUS"]["values"]] == ["A", "I", "X"] and by["STATUS"]["default"] == "A"
        assert by["VAT"]["required_when"] == [{"field": "KIND", "value": "B"}]
        assert by["SECRET"]["secret"] is True and "PHONE" in by and out["related_tables"] == ["extra"]
        # what the caller may not do is said, with the reason
        ops = _tool(client, "reader", "screen_form", {"app": "shop", "screen": "customers"})[0]["operations"]
        assert ops["update"] != True and "sql:shop:customers_put" in ops["update"][0]   # noqa: E712
        # the actions the screen offers (a drill is the browser's), and what a save runs by itself
        acts = {a["id"]: a for a in out["actions"]}
        assert set(acts) == {"block", "toggle", "audit", "rename", "broken"} and acts["block"]["on"] == "record"
        assert acts["block"]["confirms"] == ["Block {NAME} on behalf of #LOGIN_USER#?"] and acts["audit"]["does"] == ["query shop.log_post"]
        assert acts["rename"]["needs"] == [{"name": "NEW_NAME", "label": "New name", "required": True}]
        hooked = _tool(client, "writer", "screen_form", {"app": "shop", "screen": "hooked"})[0]
        assert hooked["operations"]["insert"] is True and hooked["a_save_also_runs"]["insert"]["does"] == ["query shop.log_post"]


def test_in_the_chat_the_tool_proposes_and_over_mcp_it_writes(app) -> None:
    with TestClient(app) as client:
        args = {"app": "shop", "screen": "customers", "operation": "update", "key": {"ID": 2}, "values": {"NAME": "Bolt Ltd"}}
        out, err = _tool(client, "writer", "screen_write", args)                       # the Liberty chat
        assert not err, out
        p = out["proposal"]
        assert p["kind"] == "screen_write" and "Bolt" in p["summary"] and "Bolt Ltd" in p["summary"]
        assert p["payload"]["operation"] == "update" and p["payload"]["key"] == {"ID": 2} and p["payload"]["preview"]["written"] is False
        assert _rows(client, "id = 2")[0]["name"] == "Bolt"                            # nothing written
        # the card's Apply: the payload, posted as it is
        applied = client.post("/ai/screen-write", json={k: p["payload"][k] for k in ("app", "screen", "operation", "values", "key", "params")}, headers=_h(client, "writer"))
        assert applied.status_code == 200 and _rows(client, "id = 2")[0]["name"] == "Bolt Ltd"
        # a refusal is the tool's error, in words
        out, err = _tool(client, "writer", "screen_write", {**args, "values": {"STATUS": "ZZ"}})
        assert err and "not one of its values" in out["error"]
        # over MCP the client asked its user: the tool writes
        out, err = _tool(client, "writer", "screen_write", {**args, "values": {"NAME": "Bolt SA"}}, channel="mcp")
        assert not err and out["written"] is True and _rows(client, "id = 2")[0]["name"] == "Bolt SA"
        # without the permission of the query, the tool says so
        out, err = _tool(client, "reader", "screen_write", args, channel="mcp")
        assert err and "permission denied" in out["error"]


def test_the_record_is_found_by_its_key_in_a_large_table(app) -> None:
    """The key narrows the read in SQL: the record is found however many rows the screen holds."""
    with TestClient(app) as client:
        async def fill() -> None:
            async with client.app.state.connectors.pools.engine("default").begin() as conn:
                for i in range(1000, 3000):
                    await conn.execute(text("INSERT INTO customer (id, name, status, kind) VALUES (:i, :n, 'A', 'P')"), {"i": i, "n": f"c{i}"})
        client.portal.call(fill)
        r = _write(client, "writer", operation="update", key={"ID": 2990}, values={"NAME": "found"})
        assert r.status_code == 200, r.text
        assert _rows(client, "id = 2990")[0]["name"] == "found" and _rows(client, "id = 2989")[0]["name"] == "c2989"
        # a key given as text names the same record
        assert _write(client, "writer", operation="update", key={"id": "2991"}, values={"NAME": "too"}).status_code == 200
        assert _rows(client, "id = 2991")[0]["name"] == "too"


def test_screen_action_proposes_in_the_chat_and_runs_over_mcp(app) -> None:
    with TestClient(app) as client:
        args = {"app": "shop", "screen": "customers", "action": "block", "key": {"ID": 2}}
        out, err = _tool(client, "writer", "screen_action", args)
        assert not err, out
        p = out["proposal"]
        assert p["kind"] == "screen_action" and p["summary"] == "Block — shop/customers — ID=2"
        assert p["notes"] == ["Block Bolt on behalf of writer?", "runs: query <the screen connector>.customers_set_status"]
        assert _rows(client, "id = 2")[0]["status"] == "A"                         # nothing ran
        applied = client.post("/ai/screen-action", json={k: p["payload"][k] for k in ("app", "screen", "action", "key", "inputs", "params")}, headers=_h(client, "writer"))
        assert applied.status_code == 200 and applied.json()["ok"] is True and _rows(client, "id = 2")[0]["status"] == "I"
        out, err = _tool(client, "writer", "screen_action", {**args, "action": "toggle"}, channel="mcp")
        assert not err and out["ran"] is True and out["ok"] is True and _rows(client, "id = 2")[0]["status"] == "A"
        out, err = _tool(client, "writer", "screen_action", {**args, "action": "rename"}, channel="mcp")
        assert err and "NEW_NAME" in out["error"]
        assert "screen_action" in {t.name for t in app.state.ai.tools.offered(_principal(client, "writer"))}


def test_a_lookup_value_is_checked_against_its_list(app) -> None:
    """A code taken from a query: the list is read as the form reads it and checked as the import
    checks it — trimmed, with the parameters that apply to THIS record."""
    with TestClient(app) as client:
        assert _refused(_write(client, "writer", operation="update", key={"ID": 2}, values={"KIND": "ZZ"}), "not a value of its list", "shop.kinds_get")
        assert _rows(client, "id = 2")[0]["kind"] == "P"
        # a padded code of the list is the code ('B ' in the table)
        assert _write(client, "writer", operation="update", key={"ID": 2}, values={"KIND": "B", "VAT": "FR2"}).status_code == 200
        # a list narrowed by another column of the record: B1 belongs to B, P1 does not
        assert _write(client, "writer", operation="update", key={"ID": 2}, values={"SUB": "B1"}).status_code == 200
        assert _refused(_write(client, "writer", operation="update", key={"ID": 2}, values={"SUB": "P1"}), "not a value of its list", "KIND=B")
        # … following the value given in the same write. The list is longer than its connector's row
        # cap (two rows: B1, B2): P1 is past the cut, and is found by a read narrowed to it
        cut = client.get("/api/sql/tiny/subs_get", headers=_h(client, "writer")).json()
        assert cut["truncated"] is True and [r["CODE"] for r in cut["rows"]] == ["B1", "B2"]
        assert _write(client, "writer", operation="update", key={"ID": 2}, values={"KIND": "P", "SUB": "P1"}).status_code == 200
        assert _rows(client, "id = 2")[0]["sub"] == "P1"
        # an insert is checked too; a list the caller may not read refuses the write rather than passing it
        assert _refused(_write(client, "writer", operation="insert", values={"ID": 30, "NAME": "N", "KIND": "nope"}), "not a value of its list")
        r = _save(client, "halfway", "customers", operation="insert", values={"ID": 31, "NAME": "N", "KIND": "P"})
        assert r.status_code == 409 and "could not be read" in r.json()["detail"] and "sql:shop:kinds_get" in r.json()["detail"]
        assert _rows(client, "id in (30, 31)") == []


def test_a_tab_screen_takes_its_parent_link_and_a_blank_key_part_names_a_record(app) -> None:
    """Found on JDE (Role Relationships under a user): the tab's link column is hidden and locked
    in the form, which seeds it from the parent — written from here it must be given, never left
    blank; and a record whose key holds a blank is still a record (updated, deleted)."""
    with TestClient(app) as client:
        def extras(**body):
            return client.post("/ai/screen-write", json={"app": "shop", "screen": "extras", **body}, headers=_h(client, "writer"))
        r = extras(operation="insert", values={"PHONE": "0707"})                       # no parent: refused, not written blank
        assert r.status_code == 409 and "belongs to a parent record" in r.json()["detail"] and "shop/customers.ID" in r.json()["detail"]
        assert len(_rows(client, table="customer_extra")) == 1
        assert extras(operation="insert", values={"CUST_ID": 2, "PHONE": ""}).status_code == 200   # given, although locked in the form
        form, _err = _tool(client, "writer", "screen_form", {"app": "shop", "screen": "extras"})
        link = {f["name"]: f for f in form["fields"]}["CUST_ID"]
        assert link["link_to_parent"] == "shop/customers.ID" and link["required_on"] == "insert" and "hidden" not in link and "read_only" not in link
        # the record whose key holds a blank: every key column named, the blank one as ""
        r = extras(operation="delete", key={"CUST_ID": 2})
        assert r.status_code == 409 and "PHONE" in r.json()["detail"] and "empty string" in r.json()["detail"]
        assert extras(operation="delete", key={"CUST_ID": 2, "PHONE": ""}).status_code == 200
        assert _rows(client, table="customer_extra") == [{"cust_id": 1, "phone": "0101"}]


def test_a_filter_on_a_column_named_like_a_reserved_word(app) -> None:
    """A query that aliases a column to a reserved word (`ULUSER "USER"` on JDE) could not be
    filtered: the wrap referred to it unquoted (ORA-01747)."""
    with TestClient(app) as client:
        out, err = _tool(client, "writer", "sql_query", {"connector": "shop", "query": "kinds_reserved", "filters": {"ORDER": "P"}})
        assert not err, out
        assert out["row_count"] == 1 and out["rows"][0]["LABEL"] == "Person"


_PLUGIN = '''
async def j_stamp(*, connectors, cust: int, note: str = "") -> dict:
    return {"cust": cust + 0, "stamped": f"stamped ({note})", "got_connectors": connectors is not None}
'''


def test_an_action_calls_an_api_and_a_plugin_and_uses_their_answers(tmp_path, monkeypatch) -> None:
    """`call_api` and `call_plugin` steps run through the code behind their routes — the caller's
    `api:` / `plugin:` permission, the plugins' allow-list, typed arguments — and a later step
    binds what they answered."""
    import httpx

    (tmp_path / "config").mkdir()
    pkg = tmp_path / "plugins" / "wtplg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "audit.py").write_text(_PLUGIN)
    monkeypatch.setenv("LIBERTY_APPS_DIR", str(tmp_path / "config"))      # the plugins live next to it
    app = _app(tmp_path)
    calls: list[tuple[str, str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.content.decode()))
        if request.url.path == "/boom":
            return httpx.Response(500, json={"err": "upstream"})
        return httpx.Response(200, json={"echo": json.loads(request.content)["who"] + " (seen)"})

    with TestClient(app) as client:
        app.state.connectors.api("svc")._client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))

        def act(user: str, action: str, **body):
            return client.post("/ai/screen-action", json={"app": "shop", "screen": "hooked", "action": action, **body}, headers=_h(client, user))

        # each step under its own permission: sql:shop:* alone runs neither the endpoint nor the plugin
        out = act("writer", "ping", key={"ID": 2}).json()
        assert out["ok"] is False and "api:svc:notify" in out["error"] and calls == []
        out = act("writer", "stamp", key={"ID": 2}).json()
        assert out["ok"] is False and "plugin:wtplg.audit:j_stamp" in out["error"]

        # the endpoint is called with the record's value; the next step binds its answer
        preview = act("power", "ping", key={"ID": 2}, dry_run=True).json()
        assert preview["ran"] is False and preview["does"] == ["API svc.notify", "query <the screen connector>.customers_rename"] and calls == []
        out = act("power", "ping", key={"ID": 2}).json()
        assert out["ok"] is True and len(out["steps"]) == 2, out
        assert calls == [("/notify", '{"who": "Bolt"}')] and _rows(client, "id = 2")[0]["name"] == "Bolt (seen)"
        # an upstream failure stops the chain (the route answers 200 with success = false: not an exception)
        out = act("power", "ping_fail", key={"ID": 2}).json()
        assert out["ok"] is False and "500" in out["error"] and "steps" not in out
        assert _rows(client, "id = 2")[0]["name"] == "Bolt (seen)"

        # the plugin gets typed arguments and the connectors; the next step binds its answer
        out = act("power", "stamp", key={"ID": 2}).json()
        assert out["ok"] is True and len(out["steps"]) == 2, out
        assert _rows(client, table="log") == [{"cust_id": 2, "who": "power", "what": "stamped (from the menu)"}]


def test_a_record_open_in_its_form_is_not_changed_from_here(app) -> None:
    """The form locks the record it shows (the Socket.IO room of the screen and the key): a write
    from the assistant would be a second writer — and the open form would write its old values
    back at its next Save."""
    from liberty.sio import canonical_lock_key

    with TestClient(app) as client:
        locks = app.state.sio.locks
        room = canonical_lock_key("shop", "customers", {"ID": 2})
        locks.try_acquire(room, app="shop", screen="customers", key_values={"ID": 2}, sid="tab-1", user_id="u1", username="alice")
        r = _write(client, "writer", operation="update", key={"ID": 2}, values={"NAME": "x"})
        assert r.status_code == 409 and "alice has this record open" in r.json()["detail"]
        assert _refused(_write(client, "writer", operation="delete", key={"ID": 2}), "alice has this record open")
        assert _refused(_write(client, "writer", operation="update", key={"ID": 2}, values={"NAME": "x"}, dry_run=True), "open in its form")
        assert _write(client, "writer", operation="update", key={"ID": 1}, values={"NAME": "Acme 2"}).status_code == 200   # another record
        locks.release(room, sid="tab-1")
        # the user's own open form counts too
        locks.try_acquire(room, app="shop", screen="customers", key_values={"ID": 2}, sid="tab-2", user_id="u2", username="writer")
        assert _refused(_write(client, "writer", operation="update", key={"ID": 2}, values={"NAME": "x"}), "you have this record open")
        locks.release(room, sid="tab-2")
        assert _write(client, "writer", operation="update", key={"ID": 2}, values={"NAME": "Bolt 3"}).status_code == 200
        assert _rows(client, "id = 2")[0]["name"] == "Bolt 3"


def test_sensitive_data_is_not_given_to_the_assistant(tmp_path) -> None:
    """[ai] hide_sensitive: what the installation flagged — a dictionary item (NAME), a screen
    column (VAT) — is left out of everything the tools hand to the model. The screens are
    unchanged, and without the setting nothing is hidden."""
    hidden = "(hidden: sensitive)"
    app = _app(tmp_path, hide_sensitive=True)
    with TestClient(app) as client:
        # the screen's own route: everything, the columns flagged for the page
        grid = client.get("/api/sql/shop/customers_get", headers=_h(client, "writer")).json()
        flagged = {c["name"] for c in grid["columns"] if c.get("sensitive")}
        assert flagged == {"NAME", "VAT"} and grid["rows"][0]["NAME"] == "Acme"
        assert client.get("/ai/tools", headers=_h(client, "writer")).json()["hide_sensitive"] is True

        # rows read: the columns are gone, and named; a filter on a value the user gave still applies
        out, err = _tool(client, "writer", "sql_query", {"connector": "shop", "query": "customers_get"})
        assert not err, out
        assert out["hidden_columns"] == ["NAME", "VAT"] and "sensitive" in out["hidden_note"]
        assert not ({"NAME", "VAT"} & {c["name"] for c in out["columns"]}) and all("NAME" not in r and "VAT" not in r for r in out["rows"])
        assert "Acme" not in json.dumps(out) and "FR1" not in json.dumps(out) and out["rows"][0]["KIND"] == "B"
        out, err = _tool(client, "writer", "sql_query", {"connector": "shop", "query": "customers_get", "filters": {"NAME": "Bolt"}})
        assert not err and [r["ID"] for r in out["rows"]] == [2] and "NAME" not in out["rows"][0]

        # a write: what the user asked is theirs, what the record held is not shown
        args = {"app": "shop", "screen": "customers", "operation": "update", "key": {"ID": 2}, "values": {"NAME": "Bolt Ltd", "KIND": "P"}}
        out, err = _tool(client, "writer", "screen_write", {**args, "values": {"NAME": "Bolt Ltd", "STATUS": "I"}})
        assert not err, out
        preview = out["proposal"]["payload"]["preview"]
        assert preview["values"] == {"NAME": "Bolt Ltd", "STATUS": "I"} and preview["before"] == {"NAME": hidden, "STATUS": "A"}
        assert "'Bolt'" not in out["proposal"]["summary"] and hidden in out["proposal"]["summary"]
        rec = _write(client, "writer", operation="delete", key={"ID": 1}, dry_run=True).json()["record"]
        assert rec["NAME"] == hidden and rec["VAT"] == hidden and rec["STATUS"] == "A" and rec["SECRET"] not in ("s3cret", hidden)

        # an action: the screen's messages are filled without the hidden values, the steps still bind them
        out, err = _tool(client, "writer", "screen_action", {"app": "shop", "screen": "customers", "action": "block", "key": {"ID": 2}})
        assert not err and out["proposal"]["notes"][0] == f"Block {hidden} on behalf of writer?" and "Bolt" not in json.dumps(out)
        ran = _act(client, "writer", "block", key={"ID": 2}).json()
        assert ran["ok"] is True and ran["messages"] == [f"{hidden} is blocked."] and _rows(client, "id = 2")[0]["status"] == "I"

        # the messages of what a screen runs AFTER a write are filled the same way
        out = _save(client, "writer", "hooked", operation="insert", values={"ID": 40, "NAME": "Quiet", "STATUS": "A"}).json()
        assert out["written"] is True and out["actions"]["messages"] == [f"{hidden} was created."]
        # a hidden column is filtered on a whole value only: a range would find its values out
        out, err = _tool(client, "writer", "sql_query", {"connector": "shop", "query": "customers_get", "filters": {"NAME_from": "A", "NAME_to": "C"}})
        assert err and "only on a whole value" in out["error"]
        out, err = _tool(client, "writer", "sql_query", {"connector": "shop", "query": "customers_get", "filters": {"ID_from": 2}})
        assert not err and [r["ID"] for r in out["rows"]][:1] == [2]

    (tmp_path / "shown").mkdir()
    with TestClient(_app(tmp_path / "shown")) as client:                    # the setting off: as before
        assert _save(client, "writer", "hooked", operation="insert", values={"ID": 40, "NAME": "Loud", "STATUS": "A"}).json()["actions"]["messages"] == ["Loud was created."]
        out, err = _tool(client, "writer", "sql_query", {"connector": "shop", "query": "customers_get"})
        assert not err and "hidden_columns" not in out and out["rows"][0]["NAME"] == "Acme" and "sensitive" not in json.dumps(out["columns"])
        assert _write(client, "writer", operation="update", key={"ID": 2}, values={"NAME": "Zed"}, dry_run=True).json()["before"] == {"NAME": "Bolt"}


def test_what_the_assistant_wrote_is_on_record(app) -> None:
    """An applied card is kept in the usage record (channel `apply`): who, which screen, which
    record — the turn that proposed it only recorded a proposal. A check that wrote nothing is not."""
    from sqlalchemy import select

    from liberty.ai.usage import AiCall

    def applied(client) -> list[dict]:
        async def go():
            async with client.app.state.ai_usage.session() as s:
                return [r.to_dict() for r in (await s.execute(select(AiCall).where(AiCall.channel == "apply").order_by(AiCall.at))).scalars()]
        return client.portal.call(go)

    with TestClient(app) as client:
        _write(client, "writer", operation="update", key={"ID": 2}, values={"NAME": "Zed"}, dry_run=True)       # a check: not a change
        assert applied(client) == []
        assert _write(client, "writer", operation="update", key={"ID": 2}, values={"NAME": "Zed"}).status_code == 200
        assert _write(client, "writer", operation="update", key={"ID": 2}, values={"STATUS": "ZZ"}).status_code == 409    # refused
        assert _act(client, "writer", "block", key={"ID": 1}).json()["ok"] is True
        rows = applied(client)
        assert [(r["username"], r["app"], r["tools"][0]["name"], r["tools"][0]["ok"]) for r in rows] == [
            ("writer", "shop", "screen_write", True), ("writer", "shop", "screen_write", False), ("writer", "shop", "screen_action", True)]
        assert "operation=update" in rows[0]["tools"][0]["input"] and "'ID': 2" in rows[0]["tools"][0]["input"] and "Zed" not in json.dumps(rows[0])
        assert "not one of its values" in rows[1]["tools"][0]["error"] and "action=block" in rows[2]["tools"][0]["input"]


def test_a_picked_lookup_value_fills_its_sibling_columns(app) -> None:
    """In the form, picking a value of some lists also sets sibling fields from the picked row
    (`return_binds` — on the JDE Security Workbench, the object gives its product code). Written
    from here, the screen fills them the same way: the caller gives the value, not its siblings."""
    with TestClient(app) as client:
        out = _write(client, "writer", operation="update", key={"ID": 2}, values={"KIND": "B", "VAT": "FR2"}).json()
        assert out["written"] is True and out["values"]["KLABEL"] == "Business" and out["filled_by_the_screen"] == ["KLABEL"]
        assert _rows(client, "id = 2")[0]["klabel"] == "Business"
        # a sibling the caller gives is the caller's
        _write(client, "writer", operation="update", key={"ID": 2}, values={"KIND": "P", "KLABEL": "my own"})
        assert _rows(client, "id = 2")[0]["klabel"] == "my own"
        # the value is unchanged: nothing is picked, nothing is filled
        out = _write(client, "writer", operation="update", key={"ID": 2}, values={"NAME": "Bolt 9"}).json()
        assert "filled_by_the_screen" not in out and _rows(client, "id = 2")[0]["klabel"] == "my own"
        # a dry run shows what the screen would fill
        out = _write(client, "writer", operation="update", key={"ID": 1}, values={"KIND": "P"}, dry_run=True).json()
        assert out["values"] == {"KIND": "P", "KLABEL": "Person"} and out["filled_by_the_screen"] == ["KLABEL"]


def test_the_write_tools_follow_the_allowed_connectors(tmp_path) -> None:
    """[ai] allowed_connectors restricts every tool of the assistant: a screen whose data is on
    another connector does not exist for the write tools either — the tools and the Apply routes."""
    with TestClient(_app(tmp_path, allowed=["tiny"])) as client:
        r = _write(client, "writer", operation="update", key={"ID": 2}, values={"STATUS": "I"})
        assert r.status_code == 409 and "no screen shop/customers" in r.json()["detail"] and _rows(client, "id = 2")[0]["status"] == "A"
        assert _act(client, "writer", "block", key={"ID": 2}).status_code == 409
        out, err = _tool(client, "writer", "screen_form", {"app": "shop", "screen": "customers"})
        assert err and "no screen" in out["error"]
    (tmp_path / "in").mkdir()
    with TestClient(_app(tmp_path / "in", allowed=["shop", "tiny"])) as client:
        assert _write(client, "writer", operation="update", key={"ID": 2}, values={"STATUS": "I"}).status_code == 200


def test_several_records_on_one_card(app) -> None:
    """screen_write_many: one operation over a list of records of one screen — all checked before
    any is written, then written in order; one card in the chat, one line per record on record."""
    def many(client, user, **body):
        return client.post("/ai/screen-write-many", json={"app": "shop", "screen": "customers", **body}, headers=_h(client, user))

    with TestClient(app) as client:
        new = [{"values": {"ID": 50 + i, "NAME": f"Batch {i}", "KIND": "P"}} for i in range(3)]
        # the tool proposes one card for the list
        out, err = _tool(client, "writer", "screen_write_many", {"app": "shop", "screen": "customers", "operation": "insert", "records": new})
        assert not err, out
        p = out["proposal"]
        assert p["kind"] == "screen_write_many" and p["summary"] == "Add 3 records — shop/customers" and len(p["notes"]) == 3
        assert _rows(client, "id >= 50") == []
        # one refused record refuses the whole list, and is named
        r = many(client, "writer", operation="insert", records=[*new, {"values": {"ID": 60, "KIND": "P"}}, {"values": {"ID": 61, "NAME": "x", "STATUS": "ZZ"}}])
        assert r.status_code == 409 and "2 of 5 records are refused, nothing was written" in r.json()["detail"]
        assert "record 4:" in r.json()["detail"] and "record 5:" in r.json()["detail"] and _rows(client, "id >= 50") == []
        # applied: all written
        r = many(client, "writer", operation="insert", records=new)
        assert r.status_code == 200 and r.json()["written"] == 3 and [x["id"] for x in _rows(client, "id >= 50")] == [50, 51, 52]
        # an update and a delete of several records, by their keys
        r = many(client, "writer", operation="update", records=[{"key": {"ID": 50}, "values": {"NAME": "B0"}}, {"key": {"ID": 51}, "values": {"NAME": "B1"}}])
        assert r.json()["written"] == 2 and [x["name"] for x in _rows(client, "id in (50, 51)")] == ["B0", "B1"]
        # a statement that fails at write time stops the list there: the ones before stay written
        r = many(client, "writer", operation="insert", records=[{"values": {"ID": 70, "NAME": "ok", "KIND": "P"}}, {"values": {"ID": 50, "NAME": "dup", "KIND": "P"}}, {"values": {"ID": 71, "NAME": "never", "KIND": "P"}}])
        out = r.json()
        assert r.status_code == 200 and out["written"] == 1 and out["stopped_at"] == 2 and "records 1 to 1 are written" in out["note"]
        assert [x["id"] for x in _rows(client, "id in (70, 71)")] == [70]
        assert many(client, "writer", operation="delete", records=[{"key": {"ID": i}} for i in (50, 51, 52, 70)]).json()["written"] == 4
        # limits and locks
        assert many(client, "writer", operation="insert", records=[{"values": {"ID": 100 + i, "NAME": "n"}} for i in range(51)]).status_code == 409
        assert many(client, "nowrite", operation="insert", records=new).status_code == 403
        # over MCP the tool writes the list
        out, err = _tool(client, "writer", "screen_write_many", {"app": "shop", "screen": "customers", "operation": "insert", "records": new}, channel="mcp")
        assert not err and out["written"] == 3
