"""Package import as a plan (C11 step 1a): entity-level states and diffs, connectors merged item by
item, jobs keeping the target's schedule, dry run, warnings."""

from __future__ import annotations

import io
import json
import textwrap
import tomllib
import zipfile
from pathlib import Path
from types import SimpleNamespace

from liberty.crypto import encrypt
from liberty.web.package_import import apply_package_zip, field_diff

FILES = ("connectors", "screens", "menus", "charts", "dashboards", "actions", "explorer", "notifications", "jobs", "ai")


def _settings(tmp: Path, master_key: str = "target-key") -> SimpleNamespace:
    def sect(name: str) -> SimpleNamespace:
        return SimpleNamespace(config_path=tmp / f"{name}.toml")
    s = SimpleNamespace(**{f: sect(f) for f in FILES})
    s.notify = s.notifications
    s.connectors.dictionary_path = None
    s.crypto = SimpleNamespace(master_key=master_key)
    return s


def _write(p: Path, body: str) -> None:
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


def _zip(files: dict[str, str], *, version: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, textwrap.dedent(text).lstrip())
        if version:
            zf.writestr("manifest.json", json.dumps({"source": {"install": "dev", "version": version}}))
    return buf.getvalue()


def _entities(report) -> dict[tuple[str, str], dict]:
    return {(e["kind"], e["name"]): e for f in report.files for e in f.entities}


CONNECTORS = """
    # the operator's comment
    [connectors.a]
    type = "sql"  # inline comment
    pool = "default"

    [[connectors.a.queries]]
    name = "q1"
    sql = "SELECT 1"

    [[connectors.a.queries]]
    name = "q2"
    sql = "SELECT 2"
"""

PKG_ITEMS = {"connectors.toml": '''
    [[connectors.a.queries]]
    name = "q2"
    sql = """SELECT 22
    FROM t"""

    [[connectors.a.queries]]
    name = "q3"
    sql = "SELECT 3"

    [[connectors.missing.queries]]
    name = "x"
    sql = "SELECT 9"
'''}


def test_connector_items_merge_one_by_one_and_keep_the_file(tmp_path) -> None:
    _write(tmp_path / "connectors.toml", CONNECTORS)
    s = _settings(tmp_path)
    plan = apply_package_zip(zip_bytes=_zip(PKG_ITEMS), strategy="overwrite", settings=s, dry_run=True)
    ents = _entities(plan)
    assert ents[("query/a", "q2")]["state"] == "changed"
    assert ents[("query/a", "q2")]["diff"] == [{"key": "sql", "from": "SELECT 2", "to": "SELECT 22\nFROM t"}]
    assert ents[("query/a", "q3")]["state"] == "new"
    assert ents[("query/missing", "x")]["state"] == "skipped" and "include the connector" in ents[("query/missing", "x")]["detail"]
    assert ("connector", "a") not in ents   # items only: the connector's header isn't touched
    assert plan.summary()["new"] == 1 and plan.summary()["changed"] == 1
    assert "SELECT 22" not in (tmp_path / "connectors.toml").read_text()   # a dry run writes nothing

    apply_package_zip(zip_bytes=_zip(PKG_ITEMS), strategy="overwrite", settings=s)
    text = (tmp_path / "connectors.toml").read_text()
    assert "# the operator's comment" in text and "# inline comment" in text   # tomlkit: comments kept
    queries = {q["name"]: q["sql"] for q in tomllib.loads(text)["connectors"]["a"]["queries"]}
    assert queries == {"q1": "SELECT 1", "q2": "SELECT 22\nFROM t", "q3": "SELECT 3"}   # q1 survives
    assert "missing" not in tomllib.loads(text)["connectors"]


def test_screen_states_override_and_merge(tmp_path) -> None:
    _write(tmp_path / "screens.toml", """
        [screens.app.same]
        read_query = "q"
        [screens.app.edited]
        read_query = "old_q"
        [screens.app.forked]
        read_query = "mine"
        override = true
    """)
    pkg = _zip({"screens.toml": """
        [screens.app.same]
        read_query = "q"
        [screens.app.edited]
        read_query = "new_q"
        label = "Edited"
        [screens.app.forked]
        read_query = "vendor"
        [screens.app.fresh]
        read_query = "f"
    """})
    s = _settings(tmp_path)
    ents = _entities(apply_package_zip(zip_bytes=pkg, strategy="overwrite", settings=s, dry_run=True))
    assert {n: e["state"] for (_, n), e in ents.items()} == {"same": "identical", "edited": "changed", "forked": "kept", "fresh": "new"}
    assert ents[("screen/app", "edited")]["diff"] == [
        {"key": "read_query", "from": "old_q", "to": "new_q"}, {"key": "label", "from": None, "to": "Edited"}]
    merged = _entities(apply_package_zip(zip_bytes=pkg, strategy="merge", settings=s, dry_run=True))
    assert merged[("screen/app", "edited")]["state"] == "skipped" and merged[("screen/app", "fresh")]["state"] == "new"
    apply_package_zip(zip_bytes=pkg, strategy="overwrite", settings=s)
    screens = tomllib.loads((tmp_path / "screens.toml").read_text())["screens"]["app"]
    assert screens["edited"]["read_query"] == "new_q" and screens["forked"]["read_query"] == "mine" and "fresh" in screens


def test_jobs_keep_the_target_schedule_and_arrive_disabled(tmp_path) -> None:
    _write(tmp_path / "jobs.toml", """
        # prod jobs
        [[jobs]]
        id = "load"
        schedule = "0 6 * * *"
        enabled = true
        [[jobs.steps]]
        name = "s1"
        type = "sql_query"
        query = "old"
    """)
    pkg = _zip({"jobs.toml": """
        [[jobs]]
        id = "load"
        schedule = "*/5 * * * *"
        enabled = false
        [[jobs.steps]]
        name = "s1"
        type = "sql_query"
        query = "new"

        [[jobs]]
        id = "extra"
        enabled = true
        schedule = "0 1 * * *"
    """})
    s = _settings(tmp_path)
    ents = _entities(apply_package_zip(zip_bytes=pkg, strategy="overwrite", settings=s, dry_run=True))
    assert ents[("job", "load")]["state"] == "changed"
    assert [d["key"] for d in ents[("job", "load")]["diff"]] == ["steps[s1].query"]   # schedule / enabled aren't a change
    assert ents[("job", "extra")]["state"] == "new" and ents[("job", "extra")]["detail"] == "arrives disabled"
    apply_package_zip(zip_bytes=pkg, strategy="overwrite", settings=s)
    text = (tmp_path / "jobs.toml").read_text()
    jobs = {j["id"]: j for j in tomllib.loads(text)["jobs"]}
    assert "# prod jobs" in text
    assert jobs["load"]["schedule"] == "0 6 * * *" and jobs["load"]["enabled"] is True and jobs["load"]["steps"][0]["query"] == "new"
    assert jobs["extra"]["enabled"] is False


def test_new_kinds_land_in_their_files(tmp_path) -> None:
    pkg = _zip({
        "actions.toml": '[actions.ping]\nlabel = "Ping"\n',
        "explorer.toml": '[explorer.app.sales]\nhub = "f"\n[explorer.app.sales.entities.f]\nquery = "f_get"\n',
        "notifications.toml": '[notifications.r1]\nsubject = "Hi"\ntrigger = { kind = "job", events = ["failure"] }\n',
        "ai.toml": '[ai.app]\nstarters = ["Who?"]\n',
    })
    s = _settings(tmp_path)
    report = apply_package_zip(zip_bytes=pkg, strategy="overwrite", settings=s)
    assert report.summary()["new"] == 4 and not report.warnings
    assert tomllib.loads((tmp_path / "notifications.toml").read_text())["notifications"]["r1"]["subject"] == "Hi"
    assert tomllib.loads((tmp_path / "explorer.toml").read_text())["explorer"]["app"]["sales"]["hub"] == "f"
    assert "ping" in tomllib.loads((tmp_path / "actions.toml").read_text())["actions"]
    assert tomllib.loads((tmp_path / "ai.toml").read_text())["ai"]["app"]["starters"] == ["Who?"]


def test_replace_all_lists_what_goes(tmp_path) -> None:
    _write(tmp_path / "screens.toml", '[screens.app.old]\nread_query = "q"\n')
    s = _settings(tmp_path)
    plan = apply_package_zip(zip_bytes=_zip({"screens.toml": '[screens.app.new]\nread_query = "q"\n'}),
                             strategy="replace_all", settings=s, dry_run=True)
    assert {(e["name"], e["state"]) for e in plan.files[0].entities} == {("new", "new"), ("old", "removed")}


def test_warnings_newer_source_and_foreign_secret(tmp_path) -> None:
    s = _settings(tmp_path, master_key="target-key")
    pkg = _zip({"connectors.toml": f'[pools.p]\nurl = "x"\npassword = "{encrypt("pw", "source-key")}"\n'}, version="7.1.0")
    plan = apply_package_zip(zip_bytes=pkg, strategy="overwrite", settings=s, dry_run=True, version="7.0.63")
    assert plan.source == {"install": "dev", "version": "7.1.0"}
    assert any("7.1.0" in w and "7.0.63" in w for w in plan.warnings)
    assert any("can't decrypt" in w and "pools:p:password" in w for w in plan.warnings)
    same = apply_package_zip(zip_bytes=_zip({"connectors.toml": f'[pools.p]\npassword = "{encrypt("pw", "target-key")}"\n'}),
                             strategy="overwrite", settings=s, dry_run=True, version="7.0.63")
    assert not same.warnings


def test_field_diff_keys_lists_and_masks_secrets() -> None:
    old = {"columns": [{"name": "A", "label": "a"}, {"name": "B"}], "password": "ENC:abc", "tags": [1, 2]}
    new = {"columns": [{"name": "A", "label": "A!"}, {"name": "C"}], "password": "ENC:def", "tags": [1, 3]}
    assert field_diff(old, new) == [
        {"key": "columns[A].label", "from": "a", "to": "A!"},
        {"key": "columns[C]", "from": None, "to": '{"name": "C"}'},
        {"key": "columns[B]", "from": '{"name": "B"}', "to": None},
        {"key": "password", "from": "••••", "to": "••••"},
        {"key": "tags", "from": "[1, 2]", "to": "[1, 3]"},
    ]


def test_a_menu_header_is_compared_without_its_items(tmp_path) -> None:
    menus = '[menus.app]\nlabel = "App"\n[[menus.app.items]]\nid = "a"\nlabel = "A"\n'
    _write(tmp_path / "menus.toml", menus)
    plan = apply_package_zip(zip_bytes=_zip({"menus.toml": menus}), strategy="overwrite", settings=_settings(tmp_path), dry_run=True)
    assert {(e["kind"], e["state"]) for e in plan.files[0].entities} == {("menu_app", "identical"), ("menu_item/app", "identical")}
