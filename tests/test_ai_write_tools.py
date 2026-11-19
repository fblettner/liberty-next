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

[[connectors.shop.queries]]
name = "customers_get"
sql = "SELECT c.id AS ID, c.name AS NAME, c.status AS STATUS, c.kind AS KIND, c.vat AS VAT, c.secret AS SECRET, e.cust_id AS CUST_ID, e.phone AS PHONE FROM customer c LEFT JOIN customer_extra e ON e.cust_id = c.id ORDER BY c.id"

[[connectors.shop.queries]]
name = "customers_post"
sql = "INSERT INTO customer (id, name, status, kind, vat, secret) VALUES (:ID, :NAME, :STATUS, :KIND, :VAT, :SECRET)"
writable = true

[[connectors.shop.queries]]
name = "customers_put"
sql = "UPDATE customer SET id = :ID, name = :NAME, status = :STATUS, kind = :KIND, vat = :VAT WHERE id = :ID_ORIGINAL"
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

[[screens.shop.customers.columns]]
name = "KIND"

[[screens.shop.customers.columns]]
name = "VAT"
label = "VAT number"
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

# a screen whose save runs actions: a hook after the insert and after the delete
[screens.shop.hooked]
label = "With save actions"
read_query = "customers_get"
insert_query = "customers_post"
update_query = "customers_put"
delete_query = "customers_delete"

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

[enums.STATUS]
label = "Status"
values = [{ value = "A", label = "Active" }, { value = "I", label = "Inactive" }, { value = "X", label = "Archived" }]
"""


def _app(tmp_path, *, write_tools: bool = True):
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
            await conn.execute(text("CREATE TABLE customer (id INTEGER PRIMARY KEY, name TEXT, status TEXT, kind TEXT, vat TEXT, secret TEXT)"))
            await conn.execute(text("CREATE TABLE customer_extra (cust_id INTEGER PRIMARY KEY, phone TEXT)"))
            await conn.execute(text("CREATE TABLE log (cust_id INTEGER, who TEXT, what TEXT)"))
            await conn.execute(text("INSERT INTO customer VALUES (1,'Acme','A','B','FR1','s3cret'),(2,'Bolt','A','P',NULL,NULL),(3,'Old','X','P',NULL,NULL)"))
            await conn.execute(text("INSERT INTO customer_extra VALUES (1,'0101')"))
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("writer", permissions=["ai:chat", "ai:write", "mcp:use", "sql:shop:*"])
            await svc.get_or_create_role("reader", permissions=["ai:chat", "ai:write", "sql:shop:customers_get"])
            await svc.get_or_create_role("halfway", permissions=["ai:chat", "ai:write", "sql:shop:customers_get", "sql:shop:customers_post", "sql:shop:customers_one"])
            await svc.get_or_create_role("nowrite", permissions=["ai:chat", "sql:shop:*"])
            for name in ("writer", "reader", "nowrite", "halfway"):
                await svc.create_user(name, password=f"{name}pw-123", roles=[name])
        await pools.dispose()

    asyncio.run(seed())
    return create_app(Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml"), dictionary_path=Path(tmp_path / "dictionary.toml")),
        screens=ScreenSettings(config_path=Path(tmp_path / "screens.toml")),
        actions=ActionSettings(config_path=Path(tmp_path / "actions.toml")),
        auth=AuthSettings(backend="db", jwt_secret="write-tools-test-secret-0123456789abcdef", pool="default"),
        ai=AISettings(enabled=True, api_key="", config_path=tmp_path / "ai.toml", write_tools=write_tools, mcp_enabled=True),
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
        assert _rows(client, "id = 2") == [{"id": 2, "name": "Bolt Ltd", "status": "A", "kind": "P", "vat": None, "secret": None}]
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
        assert _rows(client, "id = 10") == [{"id": 10, "name": "New", "status": "A", "kind": "P", "vat": None, "secret": None}]
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
