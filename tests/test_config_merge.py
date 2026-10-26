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


def test_connectors_blanked_secret_restored_on_refresh() -> None:
    wheel = json.loads(json.dumps(C_WHEEL))
    wheel["connectors"]["ais"]["timeout"] = 30  # a real change on a secret-bearing connector
    merged, rep = merge_file("connectors.toml", wheel, C_LIVE, C_PREV)
    ais = merged["connectors"]["ais"]
    assert "connector: ais" in rep.refreshed and ais["timeout"] == 30
    assert ais["auth_password"] == "ENC:api" and ais["base_url"] == "https://jde.customer/jderest"
    assert rep.secrets_restored == 2


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
