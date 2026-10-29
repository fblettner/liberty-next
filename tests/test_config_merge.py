"""``liberty.config_merge`` — the additive, provenance-aware merge of a shipped configuration
into the operator's live one (the rule set of liberty-apps install / Package & Deploy)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import tomli_w

from liberty.config_merge import (
    BUNDLED_DIR,
    enumerate_entities,
    flatten,
    merge_config_dir,
    merge_file,
    resolve_path,
)


def _toml(s: str) -> dict:
    return tomllib.loads(s)


# ── the rule matrix on screens.toml ──────────────────────────────────────────────────────────
PREV = _toml("""
[screens.app.users]
label = "Users"
read_query = "users_get"
[screens.app.roles]
label = "Roles"
read_query = "roles_get"
[screens.app.legacy]
label = "Legacy"
read_query = "legacy_get"
[screens.app.old_dropped]
label = "Old"
read_query = "old_get"
""")
LIVE = _toml("""
[screens.app.users]
label = "Users"
read_query = "users_get"
[screens.app.roles]
label = "Roles (ours)"
read_query = "roles_get"
[screens.app.legacy]
label = "Legacy"
read_query = "legacy_get"
override = true
[screens.app.old_dropped]
label = "Old"
read_query = "old_get"
[screens.app.mine]
label = "Customer screen"
read_query = "mine_get"
[screens.app.gone_but_edited]
label = "edited"
read_query = "x"
""")
WHEEL = _toml("""
[screens.app.users]
label = "Users v2"
read_query = "users_get"
auto_load = true
[screens.app.roles]
label = "Roles v2"
read_query = "roles_get"
[screens.app.legacy]
label = "Legacy v2"
read_query = "legacy_get"
[screens.app.brand_new]
label = "New in this release"
read_query = "new_get"
""")
PREV2 = json.loads(json.dumps(PREV))
PREV2["screens"]["app"]["gone_but_edited"] = {"label": "shipped", "read_query": "x"}


def test_rule_matrix_with_snapshot() -> None:
    merged, rep = merge_file("screens.toml", WHEEL, LIVE, PREV2)
    s = merged["screens"]["app"]
    assert rep.added == ["screen/app: brand_new"] and "brand_new" in s
    assert rep.refreshed == ["screen/app: users"] and s["users"]["label"] == "Users v2" and s["users"]["auto_load"] is True
    assert rep.kept_modified == ["screen/app: roles"] and s["roles"]["label"] == "Roles (ours)"
    assert rep.kept_override == ["screen/app: legacy"] and s["legacy"]["label"] == "Legacy"
    assert rep.removed == ["screen/app: old_dropped"] and "old_dropped" not in s
    assert rep.kept_withdrawn == ["screen/app: gone_but_edited"] and "gone_but_edited" in s
    assert rep.kept_custom == ["screen/app: mine"] and "mine" in s
    assert rep.unchanged == 0 and rep.had_snapshot and rep.changed


def test_force_vendor_takes_modified_but_not_override() -> None:
    merged, rep = merge_file("screens.toml", WHEEL, LIVE, PREV2, force_vendor=True)
    s = merged["screens"]["app"]
    assert rep.refreshed_forced == ["screen/app: roles"] and s["roles"]["label"] == "Roles v2"
    assert rep.kept_override == ["screen/app: legacy"] and s["legacy"]["label"] == "Legacy"
    assert rep.kept_modified == []


def test_without_snapshot_modified_entities_are_kept_and_nothing_removed() -> None:
    merged, rep = merge_file("screens.toml", WHEEL, LIVE, None)
    s = merged["screens"]["app"]
    assert not rep.had_snapshot
    # users differs from the wheel and we can't tell whether the operator touched it → kept
    assert set(rep.kept_modified) == {"screen/app: users", "screen/app: roles"}
    assert s["users"]["label"] == "Users"
    assert rep.removed == [] and "old_dropped" in s  # unknown provenance → never deleted
    assert set(rep.kept_custom) == {"screen/app: old_dropped", "screen/app: mine", "screen/app: gone_but_edited"}
    assert rep.added == ["screen/app: brand_new"]


def test_unchanged_is_counted_not_rewritten() -> None:
    merged, rep = merge_file("screens.toml", LIVE, LIVE, LIVE)
    assert rep.unchanged == 5 and rep.kept_override == ["screen/app: legacy"] and not rep.changed and merged == LIVE


def test_no_live_file_takes_the_wheel() -> None:
    merged, rep = merge_file("screens.toml", WHEEL, None, None)
    assert merged == WHEEL and len(rep.added) == 4


# ── connectors.toml: pools + secrets ─────────────────────────────────────────────────────────
C_WHEEL = _toml("""
[pools.jde]
url = "oracle+oracledb://jde@stub/?service_name=STUB"
password = ""
[pools.newpool]
url = "postgresql+asyncpg://x@stub/new"
password = ""
[connectors.ais]
type = "api"
base_url = ""
auth_password = ""
[connectors.jde]
type = "sql"
pool = "jde"
[[connectors.jde.queries]]
name = "new_get"
sql = "SELECT 2"
""")
C_LIVE = _toml("""
[pools.default]
url = "postgresql+asyncpg://liberty@pg/liberty"
password = "ENC:default"
[pools.jde]
url = "oracle+oracledb://jde@REAL/?service_name=PROD"
password = "ENC:jde"
[pools.jde.dblinks]
SY = "@PROD"
[connectors.ais]
type = "api"
base_url = "https://jde.customer/jderest"
auth_password = "ENC:api"
[connectors.jde]
type = "sql"
pool = "jde"
[connectors.mine]
type = "sql"
pool = "default"
[[connectors.mine.queries]]
name = "custom_get"
sql = "SELECT 1"
""")
C_PREV = _toml("""
[pools.jde]
url = "oracle+oracledb://jde@stub/?service_name=STUB"
password = ""
[connectors.ais]
type = "api"
base_url = ""
auth_password = ""
[connectors.jde]
type = "sql"
pool = "jde"
""")


def test_connectors_pools_are_the_live_ones_and_secrets_survive() -> None:
    merged, rep = merge_file("connectors.toml", C_WHEEL, C_LIVE, C_PREV)
    pools = merged["pools"]
    assert pools["default"]["password"] == "ENC:default"                   # not in the wheel at all
    assert pools["jde"]["url"].endswith("REAL/?service_name=PROD") and pools["jde"]["dblinks"] == {"SY": "@PROD"}
    assert pools["newpool"]["password"] == ""                               # wheel-only pool arrives as a stub
    assert "pool: newpool" in rep.added and rep.pools_kept == 2
    # ais unchanged since the shipment once secrets are considered → equal to the wheel too → unchanged
    assert merged["connectors"]["ais"]["auth_password"] == "ENC:api" and merged["connectors"]["ais"]["base_url"].startswith("https://")
    # jde untouched → refreshed: the new query lands
    assert "connector: jde" in rep.refreshed and merged["connectors"]["jde"]["queries"][0]["name"] == "new_get"
    assert "connector: mine" in rep.kept_custom and merged["connectors"]["mine"]["queries"][0]["name"] == "custom_get"
    assert rep.kept_modified == []


def test_connectors_without_snapshot_take_vendor_structure_but_keep_secrets() -> None:
    merged, rep = merge_file("connectors.toml", C_WHEEL, C_LIVE, None)
    assert "connector: jde" in rep.refreshed and merged["connectors"]["jde"]["queries"][0]["name"] == "new_get"
    assert merged["connectors"]["ais"]["auth_password"] == "ENC:api"
    assert merged["pools"]["default"]["password"] == "ENC:default" and "connector: mine" in rep.kept_custom
    assert rep.kept_modified == []


def test_env_placeholders_are_filled_from_the_live_file() -> None:
    """The wheel ships install-specific hosts / users / the TLS switch as `${VAR}` or
    `${VAR:-default}`; a concrete live value wins (a boolean too), a live placeholder is left alone,
    and a literal vendor value still lands."""
    wheel = _toml('[connectors.ais]\ntype = "api"\nbase_url = "${JDE_AIS_URL}"\nverify_ssl = true\nauth_password = ""\n[connectors.x]\ntype = "api"\nbase_url = "${X_URL:-http://x}"\n')
    live = _toml('[connectors.ais]\ntype = "api"\nbase_url = "https://jde.customer/jderest"\nverify_ssl = false\nauth_password = "ENC:api"\n[connectors.x]\ntype = "api"\nbase_url = "http://real"\n')
    merged, rep = merge_file("connectors.toml", wheel, live, None)
    ais = merged["connectors"]["ais"]
    assert ais["base_url"] == "https://jde.customer/jderest" and ais["auth_password"] == "ENC:api"
    assert ais["verify_ssl"] is True                       # a real vendor change still lands
    assert merged["connectors"]["x"]["base_url"] == "http://real"          # a reference with a default is the install's too
    assert rep.secrets_restored == 2                   # x: restored, then equal to the live one (unchanged)
    merged, _ = merge_file("connectors.toml", wheel, _toml('[connectors.ais]\ntype = "api"\nbase_url = "${AIS_PROD_URL}"\n'), None)
    assert merged["connectors"]["ais"]["base_url"] == "${AIS_PROD_URL}"   # the install's own reference (several AIS) wins


def test_connectors_blanked_secret_restored_on_refresh() -> None:
    wheel = json.loads(json.dumps(C_WHEEL))
    wheel["connectors"]["ais"]["timeout"] = 30  # a real change on a secret-bearing connector
    merged, rep = merge_file("connectors.toml", wheel, C_LIVE, C_PREV)
    ais = merged["connectors"]["ais"]
    assert "connector: ais" in rep.refreshed and ais["timeout"] == 30
    assert ais["auth_password"] == "ENC:api" and ais["base_url"] == "https://jde.customer/jderest"
    assert rep.secrets_restored == 2



def test_api_environment_binding_is_the_installs() -> None:
    """`pool` + `targets` (one AIS per JDE environment) are set by the install: an upgrade keeps them,
    still brings the vendor's new endpoints, and --force-config doesn't drop them either."""
    targets = {"jde_dev": {"base_url": "https://ais-dev/jderest", "auth_password": "ENC:dev"}}
    live = json.loads(json.dumps(C_LIVE))
    live["connectors"]["ais"].update(pool="jde", targets=targets)
    wheel = json.loads(json.dumps(C_WHEEL))
    wheel["connectors"]["ais"]["timeout"] = 30
    for force in (False, True):
        merged, rep = merge_file("connectors.toml", wheel, live, C_PREV, force_vendor=force)
        ais = merged["connectors"]["ais"]
        assert "connector: ais" in rep.refreshed and ais["timeout"] == 30           # untouched otherwise → refreshed
        assert ais["pool"] == "jde" and ais["targets"] == targets
        assert ais["auth_password"] == "ENC:api"
    merged, rep = merge_file("connectors.toml", C_WHEEL, live, C_PREV)
    assert "connector: ais" not in rep.kept_modified and merged["connectors"]["ais"]["targets"] == targets

# ── menus: header + items by id, order kept, new appended ────────────────────────────────────
M_PREV = _toml("""
[menus.app]
label = "App"
[[menus.app.items]]
id = "a"
label = "A"
type = "folder"
[[menus.app.items]]
id = "b"
label = "B"
type = "folder"
""")
M_LIVE = _toml("""
[menus.app]
label = "App"
home = "dash1"
[[menus.app.items]]
id = "b"
label = "B"
type = "folder"
[[menus.app.items]]
id = "a"
label = "A renamed"
type = "folder"
[[menus.app.items]]
id = "custom"
label = "Mine"
type = "folder"
""")
M_WHEEL = _toml("""
[menus.app]
label = "App v2"
[[menus.app.items]]
id = "a"
label = "A v2"
type = "folder"
[[menus.app.items]]
id = "b"
label = "B v2"
type = "folder"
[[menus.app.items]]
id = "c"
label = "C"
type = "folder"
[menus.other]
label = "Other app"
[[menus.other.items]]
id = "o1"
label = "O1"
type = "folder"
""")


def test_menus_merge_keeps_order_home_and_custom_items() -> None:
    merged, rep = merge_file("menus.toml", M_WHEEL, M_LIVE, M_PREV)
    app = merged["menus"]["app"]
    assert [it["id"] for it in app["items"]] == ["b", "a", "custom", "c"]   # live order, new appended
    assert app["items"][0]["label"] == "B v2"                                # untouched → refreshed
    assert app["items"][1]["label"] == "A renamed"                            # modified → kept
    assert "menu_item/app: a" in rep.kept_modified and "menu_item/app: custom" in rep.kept_custom
    assert "menu_app: app" in rep.kept_modified and app["home"] == "dash1"  # header changed (home) → kept
    assert merged["menus"]["other"]["items"][0]["id"] == "o1" and "menu_app: other" in rep.added


# ── dictionary: scopes + top-level settings ──────────────────────────────────────────────────
def test_dictionary_scopes_and_settings() -> None:
    wheel = _toml('default_language = "en"\n[entries.A]\nlabel = "A"\n[connectors.app.entries.B]\nlabel = "B"\n[lookups.l1]\nquery = "q"\nvalue = "v"\nlabel = "l"\n')
    live = _toml('default_language = "fr"\n[entries.A]\nlabel = "A"\n')
    merged, rep = merge_file("dictionary.toml", wheel, live, None)
    assert merged["default_language"] == "fr"                       # the operator's setting wins
    assert merged["connectors"]["app"]["entries"]["B"]["label"] == "B" and "entry/app: B" in rep.added
    assert "lookup/shared: l1" in rep.added and rep.unchanged == 1


def test_enumeration_covers_every_file() -> None:
    assert dict(flatten(_toml('[actions.x]\nlabel = "x"\n'), "actions.toml")) == {("action", "x"): {"label": "x"}}
    assert resolve_path("chart/app", "c1", "charts.toml") == (["charts", "app"], "c1")
    assert resolve_path("dashboard/app", "d1", "dashboards.toml") == (["dashboards", "app"], "d1")
    assert resolve_path("sequence/app", "1", "dictionary.toml") == (["connectors", "app", "sequences"], "1")
    kinds = {k for k, _ in enumerate_entities(M_WHEEL, "menus.toml")}
    assert kinds == {"menu_app", "menu_item/app", "menu_item/other"}


# ── directory level ──────────────────────────────────────────────────────────────────────────
def _write(d: Path, files: dict[str, str | bytes]) -> None:
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body if isinstance(body, bytes) else body.encode())


def test_merge_config_dir_end_to_end(tmp_path: Path) -> None:
    wheel, live = tmp_path / "wheel", tmp_path / "live"
    _write(wheel, {
        "screens.toml": tomli_w.dumps(WHEEL), "theme.toml": "[theme]\nname = \"vendor\"\n",
        "presets/jde/address_book.toml": "a = 1\n", "presets/jde/new_preset.toml": "n = 1\n",
        "nomasx1-reference.tar.gz": b"v2",
    })
    _write(live, {
        "screens.toml": tomli_w.dumps(LIVE), "theme.toml": "[theme]\nname = \"customer\"\n",
        "presets/jde/address_book.toml": "a = 1\nmine = true\n",
        "nomasx1-reference.tar.gz": b"v1",
        f"{BUNDLED_DIR}/screens.toml": tomli_w.dumps(PREV2),
        f"{BUNDLED_DIR}/nomasx1-reference.tar.gz": b"v1",
    })
    # dry run: a plan, nothing written
    plan = merge_config_dir(wheel, live, dry_run=True, version="7.0.58")
    assert plan.dry_run and plan.snapshot_dir is None
    assert tomllib.loads((live / "screens.toml").read_text()) == LIVE
    assert (live / "theme.toml").read_text().endswith('"customer"\n')
    text = plan.format_plan()
    assert "dry run" in text and "+ screen/app: brand_new" in text and "! kept, changed locally" in text
    assert "presets/jde/new_preset.toml" in plan.copied and "theme.toml" in plan.preserved
    assert "presets/jde/address_book.toml" in plan.preserved          # customer edited a preset → kept
    assert "nomasx1-reference.tar.gz" in plan.refreshed_files        # identical to the previous shipment → refreshed
    assert not (live / "presets/jde/new_preset.toml").exists()

    rep = merge_config_dir(wheel, live, version="7.0.58")
    s = tomllib.loads((live / "screens.toml").read_text())["screens"]["app"]
    assert "brand_new" in s and s["users"]["label"] == "Users v2" and s["roles"]["label"] == "Roles (ours)"
    assert "old_dropped" not in s and "mine" in s
    assert (live / "theme.toml").read_text().endswith('"customer"\n')
    assert (live / "presets/jde/new_preset.toml").read_text() == "n = 1\n"
    assert (live / "presets/jde/address_book.toml").read_text() == "a = 1\nmine = true\n"
    assert (live / "nomasx1-reference.tar.gz").read_bytes() == b"v2"
    # the shipment is now the snapshot
    snap = live / BUNDLED_DIR
    assert tomllib.loads((snap / "screens.toml").read_text()) == WHEEL
    assert json.loads((snap / "manifest.json").read_text())["version"] == "7.0.58"
    assert rep.needs_review == ["screens.toml — screen/app: roles", "screens.toml — screen/app: gone_but_edited"]
    # second run: nothing to do
    again = merge_config_dir(wheel, live, version="7.0.58")
    assert not any(f.changed for f in again.files) and again.copied == [] and again.refreshed_files == []


def test_merge_config_dir_fresh_install(tmp_path: Path) -> None:
    wheel, live = tmp_path / "wheel", tmp_path / "live"
    _write(wheel, {"screens.toml": tomli_w.dumps(WHEEL), "theme.toml": "[theme]\n", "presets/x.toml": "x = 1\n"})
    rep = merge_config_dir(wheel, live, version="1")
    assert tomllib.loads((live / "screens.toml").read_text()) == WHEEL
    assert sorted(rep.copied) == ["presets/x.toml", "theme.toml"]
    assert len(rep.files[0].added) == 4


def test_invalid_live_toml_is_reported_not_crashed(tmp_path: Path) -> None:
    wheel, live = tmp_path / "wheel", tmp_path / "live"
    _write(wheel, {"screens.toml": tomli_w.dumps(WHEEL)})
    _write(live, {"screens.toml": "this is = not toml ["})
    rep = merge_config_dir(wheel, live)
    assert rep.errors and "screens.toml" in rep.errors[0] and rep.files == []
    assert (live / "screens.toml").read_text() == "this is = not toml ["


# ── nomaflow jobs ────────────────────────────────────────────────────────────────────────────
def _job(jid: str, steps: list[str], **kw) -> dict:
    return {"id": jid, "steps": [{"type": "python", "name": n, "callable": "m:f"} for n in steps], **kw}


def test_jobs_operator_fields_survive_a_product_change() -> None:
    """The operator scheduled and enabled a job and set its application; the product adds a step:
    the step lands, the schedule / enabled / preset / param value stay, a new param comes in."""
    from liberty.config_merge import merge_jobs
    prev = {"jobs": [_job("collect", ["A"], enabled=False, params={"apps_id": 1})]}
    live = {"meta": {"retention": {"days": 7}}, "jobs": [_job(
        "collect", ["A"], enabled=True, schedule="0 2 * * *", params={"apps_id": 3},
        presets=[{"name": "p", "step_enabled": {"A": False}}])]}
    wheel = {"meta": {"retention": {"days": 30}}, "jobs": [_job("collect", ["A", "B"], enabled=False, params={"apps_id": 1, "lang": "fr"})]}
    merged, rep = merge_jobs(wheel, live, prev)
    j = merged["jobs"][0]
    assert [s["name"] for s in j["steps"]] == ["A", "B"]
    assert j["enabled"] is True and j["schedule"] == "0 2 * * *" and j["presets"] == [{"name": "p", "step_enabled": {"A": False}}]
    assert j["params"] == {"apps_id": 3, "lang": "fr"}
    assert merged["meta"] == {"retention": {"days": 7}}                    # the live policies win
    assert rep.refreshed == ["job: collect"] and any("kept your" in n for n in rep.notes)


def test_jobs_vendor_defaults_move_when_the_operator_never_touched_them() -> None:
    from liberty.config_merge import merge_jobs
    prev = {"jobs": [_job("r", ["A"], schedule="0 6 1 * *")]}
    live = {"jobs": [_job("r", ["A"], schedule="0 6 1 * *")]}
    wheel = {"jobs": [_job("r", ["A"], schedule="0 7 1 * *")]}
    merged, _ = merge_jobs(wheel, live, prev)
    assert merged["jobs"][0]["schedule"] == "0 7 1 * *"


def test_jobs_withdrawn_custom_override_and_edited_steps() -> None:
    from liberty.config_merge import merge_jobs
    prev = {"jobs": [_job("old", ["A"]), _job("edited", ["A"]), _job("forked", ["A"])]}
    live = {"jobs": [
        _job("old", ["A"], schedule="*/5 * * * *"),            # withdrawn, only scheduled → removed
        _job("edited", ["A", "MINE"]),                          # operator changed the steps → kept
        _job("forked", ["X"], override=True),                   # the operator's fork → kept
        _job("mine", ["A"]),                                    # never shipped → custom, kept
    ]}
    wheel = {"jobs": [_job("edited", ["A", "B"]), _job("forked", ["A", "B"]), _job("new", ["A"])]}
    merged, rep = merge_jobs(wheel, live, prev)
    ids = [j["id"] for j in merged["jobs"]]
    assert ids == ["edited", "forked", "new", "mine"]
    assert rep.removed == ["job: old"] and rep.kept_modified == ["job: edited"]
    assert rep.kept_override == ["job: forked"] and rep.kept_custom == ["job: mine"] and rep.added == ["job: new"]
    forced, frep = merge_jobs(wheel, live, prev, force_vendor=True)
    assert [s["name"] for s in forced["jobs"][0]["steps"]] == ["A", "B"] and frep.refreshed_forced == ["job: edited"]


def test_jobs_without_a_previous_shipment_and_presets_of_removed_steps() -> None:
    """First upgrade with this merge: no snapshot. The live operator fields still win, the steps
    come from the wheel, a job absent from the wheel is kept (it may be the operator's), and a
    preset entry naming a step that no longer exists is dropped (the job would not load)."""
    from liberty.config_merge import merge_jobs
    live = {"jobs": [_job("c", ["A", "GONE"], schedule="0 1 * * *", presets=[{"name": "p", "step_enabled": {"GONE": False, "A": True}}]), _job("legacy", ["A"])]}
    wheel = {"jobs": [_job("c", ["A", "B"])]}
    merged, rep = merge_jobs(wheel, live, None)
    c = merged["jobs"][0]
    assert [s["name"] for s in c["steps"]] == ["A", "B"] and c["schedule"] == "0 1 * * *"
    assert c["presets"] == [{"name": "p", "step_enabled": {"A": True}}]
    assert rep.kept_custom == ["job: legacy"] and any("removed step" in n for n in rep.notes)


def test_jobs_file_merge_writes_and_snapshots(tmp_path: Path) -> None:
    from liberty.config_merge import merge_jobs_file
    from liberty.jobs.schema import JobsFile
    wheel = tmp_path / "wheel.toml"
    wheel.write_text(tomli_w.dumps({"jobs": [_job("c", ["A", "B"])]}))
    dst, snap = tmp_path / "live" / "jobs.toml", tmp_path / ".bundled" / "jobs.toml"
    rep = merge_jobs_file(wheel, {"jobs": [_job("c", ["A"], schedule="0 1 * * *")]}, dst, snap)
    doc = tomllib.loads(dst.read_text())
    JobsFile.model_validate(doc)
    assert doc["jobs"][0]["schedule"] == "0 1 * * *" and snap.read_bytes() == wheel.read_bytes() and rep.refreshed
    dry = tmp_path / "dry" / "jobs.toml"
    merge_jobs_file(wheel, None, dry, tmp_path / "dry-snap.toml", dry_run=True)
    assert not dry.exists()


def test_env_reference_with_default_keeps_a_live_boolean() -> None:
    """The wheel ships the AIS TLS check as `${JDE_AIS_VERIFY_SSL:-true}`; an install that switched
    it off in Settings (a TOML boolean) keeps it off across an upgrade, with or without a previous
    shipment on record, and the connector's new endpoint still lands."""
    wheel = _toml('[connectors.ais]\ntype = "api"\nbase_url = "${JDE_AIS_URL}"\nverify_ssl = "${JDE_AIS_VERIFY_SSL:-true}"\n[[connectors.ais.endpoints]]\nname = "new"\npath = "/new"\n')
    live = _toml('[connectors.ais]\ntype = "api"\nbase_url = "https://jde.customer/jderest"\nverify_ssl = false\n')
    for prev in (None, _toml('[connectors.ais]\ntype = "api"\nbase_url = "${JDE_AIS_URL}"\nverify_ssl = "${JDE_AIS_VERIFY_SSL:-true}"\n')):
        merged, rep = merge_file("connectors.toml", wheel, live, prev)
        ais = merged["connectors"]["ais"]
        assert ais["verify_ssl"] is False and ais["base_url"] == "https://jde.customer/jderest"
        assert [e["name"] for e in ais.get("endpoints", [])] == ["new"], rep
