"""Integrity engine — broken-reference detection across the loaded config."""

from __future__ import annotations

from types import SimpleNamespace

from liberty.menus.config import parse_menus
from liberty.screens.config import parse_screens
from liberty.web.integrity import check_integrity


class _Cfg:
    def __init__(self, names: list[str], *, home: str | None = None) -> None:
        self._names = names
        self.home = home

    def iter_named_queries(self):
        return [(n, object()) for n in self._names]


class _Registry:
    def __init__(self, conns: dict[str, list[str]], *, homes: dict[str, str] | None = None) -> None:
        self._conns = {n: SimpleNamespace(config=_Cfg(q, home=(homes or {}).get(n))) for n, q in conns.items()}
        self.dictionary = None

    def names(self):
        return list(self._conns)

    def get(self, n):
        return self._conns[n]


def _dashboards(spec: dict[str, list[str]]):
    # {scope: [id, ...]} → a fake DashboardsFile-shaped object (only .dashboards is read here).
    return SimpleNamespace(dashboards={
        scope: {did: SimpleNamespace(connector=None, widgets=[]) for did in ids}
        for scope, ids in spec.items()
    })


def _state(*, conns, screens=None, menus=None, homes=None, dashboards=None, actions=None):
    from liberty.actions import parse_actions
    return SimpleNamespace(
        connectors=_Registry(conns, homes=homes),
        screens=parse_screens({"screens": screens}) if screens is not None else None,
        menus=parse_menus({"menus": menus}) if menus is not None else None,
        dashboards=_dashboards(dashboards) if dashboards is not None else None,
        charts=None,
        actions=parse_actions({"actions": actions}) if actions is not None else None,
    )


def _cats(issues):
    return [(i.severity, i.category, i.message) for i in issues]


def test_detects_broken_menu_and_screen_query() -> None:
    state = _state(
        conns={"jde": ["f0004_get", "f0004_put"]},
        screens={"jde": {"f0004": {"connector": "jde", "read_query": "f0004_get", "update_query": "missing_put"}}},
        menus={"jde": {"items": [
            {"id": "ok", "label": "OK", "type": "screen", "target": "f0004"},
            {"id": "bad", "label": "Bad", "type": "screen", "target": "ghost_screen"},
            {"id": "badq", "label": "BadQ", "type": "query", "target": "no_such_get"},
        ]}},
    )
    issues = check_integrity(state)
    cats = _cats(issues)
    # broken menu → screen, broken menu → query, screen missing update_query
    assert any(c[1] == "Broken menu target" and "ghost_screen" in c[2] for c in cats)
    assert any(c[1] == "Broken menu target" and "no_such_get" in c[2] for c in cats)
    assert any(c[1] == "Missing query" and "missing_put" in c[2] for c in cats)
    # the good menu item produces no issue
    assert not any("OK" in c[2] for c in cats)


def test_clean_config_has_no_errors() -> None:
    state = _state(
        conns={"jde": ["f0004_get"]},
        screens={"jde": {"f0004": {"connector": "jde", "read_query": "f0004_get"}}},
        menus={"jde": {"items": [{"id": "u", "label": "U", "type": "screen", "target": "f0004"}]}},
    )
    issues = check_integrity(state)
    assert not [i for i in issues if i.severity == "error"]


def test_unused_connector_and_orphan_screen_warn() -> None:
    state = _state(
        conns={"jde": ["f0004_get"], "ais_backup": ["ping_get"]},
        screens={"jde": {
            "f0004": {"connector": "jde", "read_query": "f0004_get"},
            "lonely": {"connector": "jde", "read_query": "f0004_get"},  # reachable via the query menu? no — different sid, same rq
        }},
        menus={"jde": {"items": [{"id": "u", "label": "U", "type": "screen", "target": "f0004"}]}},
    )
    issues = check_integrity(state)
    cats = _cats(issues)
    assert any(c[0] == "warning" and c[1] == "Unused connector" and "ais_backup" in c[2] for c in cats)
    assert any(c[0] == "warning" and c[1] == "Orphan screen" and "lonely" in c[2] for c in cats)


def test_unused_query_warns_and_referenced_query_does_not() -> None:
    # ``orphan_get`` is owned by jde but nothing references it (the canonical "lookup-backing
    # query left behind after the dictionary lookup moved connectors" case); ``f0004_get`` is
    # the screen's read_query and must NOT be flagged.
    state = _state(
        conns={"jde": ["f0004_get", "orphan_get"]},
        screens={"jde": {"f0004": {"connector": "jde", "read_query": "f0004_get"}}},
        menus={"jde": {"items": [{"id": "u", "label": "U", "type": "screen", "target": "f0004"}]}},
    )
    msgs = [i.message for i in check_integrity(state) if i.category == "Unused query"]
    assert any("jde.orphan_get" in m for m in msgs)
    assert not any("jde.f0004_get" in m for m in msgs)


def test_dashboard_menu_target_uses_qualified_id() -> None:
    """A `dashboard` menu leaf targets the QUALIFIED `<scope>.<id>` — an existing one must not be
    flagged, a missing one must be. (Regression: the index used bare ids → false positives.)"""
    state = _state(
        conns={"app": ["x_get"]},
        dashboards={"app": ["overview"]},
        menus={"app": {"items": [
            {"id": "ok", "label": "Overview", "type": "dashboard", "target": "app.overview"},
            {"id": "bad", "label": "Ghost", "type": "dashboard", "target": "app.ghost"},
        ]}},
    )
    issues = check_integrity(state)
    msgs = [i.message for i in issues if i.category == "Broken menu target"]
    assert not any("app.overview" in m for m in msgs)   # exists → no issue
    assert any("app.ghost" in m for m in msgs)          # missing → flagged


def test_navigate_to_query_marks_target_screen_reachable() -> None:
    """A screen reached only via a row-menu navigate-to-QUERY (resolved to the screen by its read
    query) must NOT be flagged orphan — find-usages finds it, integrity must agree."""
    state = _state(
        conns={"nomasx1": ["audit_trail_get", "audit_trail_query"]},
        screens={"nomasx1": {
            "audit_trail": {
                "connector": "nomasx1", "read_query": "audit_trail_get",
                "row_menu": [{"id": "d", "type": "navigate", "to": "audit_trail_query"}],
            },
            "audit_trail_query": {"connector": "nomasx1", "read_query": "audit_trail_query"},
        }},
        menus={"nomasx1": {"items": [{"id": "u", "label": "Audit", "type": "screen", "target": "audit_trail"}]}},
    )
    orphans = [i.message for i in check_integrity(state) if i.category == "Orphan screen"]
    assert not any("audit_trail_query" in m for m in orphans)


def test_connector_used_only_by_call_api_action_not_unused() -> None:
    """A connector referenced only by a screen action's `connector` (e.g. a call_api) is in use —
    must not be flagged unused. (Regression: the check didn't walk screen actions.)"""
    state = _state(
        conns={"nomajde": ["f0092_get"], "ais": ["x_get"]},
        screens={"nomajde": {"f0092": {
            "connector": "nomajde", "read_query": "f0092_get",
            "row_menu": [{"id": "ping", "type": "call_api", "connector": "ais", "endpoint": "x_get"}],
        }}},
        menus={"nomajde": {"items": [{"id": "u", "label": "F0092", "type": "screen", "target": "f0092"}]}},
    )
    unused = [i.message for i in check_integrity(state) if i.category == "Unused connector"]
    assert not any("'ais'" in m for m in unused)


def test_nested_form_reference_marks_target_reachable_and_validates() -> None:
    """A screen embedded only via a reference-mode nested_form (``form_screen``) is reached — not an
    orphan; and a form_screen pointing at a missing screen is a broken reference."""
    state = _state(
        conns={"nomasx1": ["parent_get", "child_get"]},
        screens={"nomasx1": {
            "parent": {"connector": "nomasx1", "read_query": "parent_get", "dialog": {"tabs": [
                {"id": "child", "type": "nested_form", "form_screen": "child",
                 "param_binds": [{"param": "APPS_ID", "source": "APPS_ID"}]},
                {"id": "ghost", "type": "nested_form", "form_screen": "no_such_screen"},
            ]}},
            "child": {"connector": "nomasx1", "read_query": "child_get"},
        }},
        menus={"nomasx1": {"items": [{"id": "u", "label": "Parent", "type": "screen", "target": "parent"}]}},
    )
    issues = check_integrity(state)
    orphans = [i.message for i in issues if i.category == "Orphan screen"]
    assert not any("nomasx1.child" in m for m in orphans)   # reached via the nested_form reference
    broken = [i.message for i in issues if i.category == "Broken screen reference"]
    assert any("no_such_screen" in m for m in broken)        # dangling reference flagged


def test_nested_form_tab_reference_validated_and_reachable() -> None:
    """A nested_form tab is reference-only (``form_screen``): a dangling reference is flagged as a
    Broken screen reference, and a valid one marks the reused screen reachable (not orphan)."""
    state = _state(
        conns={"nomajde": ["f0092_get", "child_get"]},
        screens={"nomajde": {
            "f0092": {"connector": "nomajde", "read_query": "f0092_get", "dialog": {"tabs": [
                {"id": "main", "type": "form", "fields": [{"name": "A"}]},
                {"id": "ref", "type": "nested_form", "form_screen": "child_screen",
                 "param_binds": [{"param": "PID", "source": "A"}]},
                {"id": "bad", "type": "nested_form", "form_screen": "no_such_screen"},
            ]}},
            "child_screen": {"connector": "nomajde", "read_query": "child_get"},
        }},
        menus={"nomajde": {"items": [{"id": "u", "label": "F0092", "type": "screen", "target": "f0092"}]}},
    )
    issues = check_integrity(state)
    broken = [i.message for i in issues if i.category == "Broken screen reference"]
    assert any("no_such_screen" in m for m in broken)               # dangling form_screen flagged
    orphans = [i.message for i in issues if i.category == "Orphan screen"]
    assert not any("child_screen" in m for m in orphans)            # reused form_screen reachable


def test_column_group_queries_and_refs_validated() -> None:
    """A column_group's write queries must exist, and every column.group must reference a defined
    group (the 1:1 related-table write-back feature)."""
    state = _state(
        conns={"nomajde": ["f0092_get", "f0092_put", "f0101_put"]},
        screens={"nomajde": {"f0092": {
            "connector": "nomajde", "read_query": "f0092_get", "update_query": "f0092_put",
            "columns": [
                {"name": "ULUSER", "key": True},
                {"name": "ABALPH", "group": "addr"},
                {"name": "BOGUS", "group": "ghost"},
            ],
            "column_groups": [
                {"id": "addr", "update_query": "f0101_put", "param_binds": [{"param": "ABAN8", "source": "ULUSER"}]},
                {"id": "bad", "update_query": "no_such_put", "delete_query": "no_such_delete"},
            ],
        }}},
        menus={"nomajde": {"items": [{"id": "u", "label": "F0092", "type": "screen", "target": "f0092"}]}},
    )
    issues = check_integrity(state)
    missing = [i.message for i in issues if i.category == "Missing query"]
    assert any("no_such_put" in m for m in missing)                 # group's bad query flagged
    assert any("no_such_delete" in m for m in missing)              # group's bad delete query flagged too
    broken = [i.message for i in issues if i.category == "Broken column group"]
    assert any("ghost" in m for m in broken)                        # column → undefined group flagged
    assert not any("'addr'" in m for m in broken)                   # valid group not flagged


def test_call_action_ref_validated() -> None:
    """A screen action referencing a shared action by id must resolve to a defined
    ``[actions.<id>]`` — a dangling ref is a "Broken action reference"; a defined one is clean."""
    state = _state(
        conns={"jde": ["f0004_get"]},
        screens={"jde": {"f0004": {
            "connector": "jde", "read_query": "f0004_get",
            "on_insert": [{"id": "a", "type": "call_action", "ref": "create_role"}],
            "on_delete": [{"id": "b", "type": "call_action", "ref": "ghost_action"}],
        }}},
        menus={"jde": {"items": [{"id": "u", "label": "F0004", "type": "screen", "target": "f0004"}]}},
        actions={"create_role": {"label": "Create", "steps": []}},
    )
    issues = check_integrity(state)
    broken = [i.message for i in issues if i.category == "Broken action reference"]
    assert any("ghost_action" in m for m in broken)        # undefined ref flagged
    assert not any("create_role" in m for m in broken)     # defined ref clean


def test_broken_home_detected() -> None:
    state = _state(
        conns={"jde": ["f0004_get"]},
        homes={"jde": "no_such_item"},
        menus={"jde": {"items": [{"id": "u", "label": "U", "type": "screen", "target": "x"}]}},
        screens={"jde": {"x": {"connector": "jde", "read_query": "f0004_get"}}},
    )
    issues = check_integrity(state)
    assert any(i.category == "Broken home" and "no_such_item" in i.message for i in issues)


def test_detects_broken_refs_inside_shared_actions() -> None:
    """A shared action's own steps (run_query / call_api / call_action) are validated like a
    screen's — a dangling query / endpoint / nested-action ref is flagged even though no screen
    hook reaches it directly. Regression: previously only screen actions were scanned."""
    state = _state(
        conns={"jde": ["f0004_get"]},
        screens={"jde": {"f0004": {"connector": "jde", "read_query": "f0004_get"}}},
        actions={"sync_user": {"steps": [
            {"type": "run_query", "id": "s1", "connector": "jde", "query": "ghost_q"},
            {"type": "call_action", "id": "s2", "ref": "no_such_action"},
        ]}},
    )
    cats = _cats(check_integrity(state))
    assert any(c[1] == "Missing query" and "ghost_q" in c[2] and "sync_user" in c[2] for c in cats)
    assert any(c[1] == "Broken action reference" and "no_such_action" in c[2] for c in cats)


def test_query_used_only_by_a_shared_action_is_not_flagged_unused() -> None:
    """A query referenced only from a shared action's step counts as used — find-usages walks
    shared actions now, so the unused-query check (which reuses it) won't false-flag it."""
    state = _state(
        conns={"jde": ["f0004_get", "helper_q"]},
        screens={"jde": {"f0004": {"connector": "jde", "read_query": "f0004_get"}}},
        actions={"helper": {"steps": [{"type": "run_query", "id": "s1", "connector": "jde", "query": "helper_q"}]}},
    )
    unused = [i.message for i in check_integrity(state) if i.category == "Unused query"]
    assert not any("helper_q" in m for m in unused)
    assert any("f0004_get" not in m for m in unused) or not unused  # f0004_get is the screen read query → used


# ── what the other files name: jobs, Explorer models, notification rules, the dictionary scan ──────────

def _jobs(*jobs: dict):
    """A stand-in for the nomaflow components parked on ``app.state.jobs`` (only ``registry.jobs()`` is read)."""
    from liberty.jobs.schema import JobsFile

    parsed = JobsFile.model_validate({"jobs": list(jobs)}).jobs
    return SimpleNamespace(registry=SimpleNamespace(jobs=lambda: parsed))


def test_a_query_only_a_job_runs_is_not_unused() -> None:
    """A step's query, a file load's ``refuse_query`` (on the target's connector) and a run parameter's choices
    are references: the query is not "unused", and Find usages names the job."""
    from liberty.web.usages import find_usages

    state = _state(conns={"demo": ["truncate_items", "refuse_closed", "period_choices", "left_over"], "tools": []})
    state.jobs = _jobs(
        {"id": "load", "steps": [
            {"name": "empty", "type": "sql_query", "connector": "demo", "query": "truncate_items"},
            {"name": "sales", "type": "file_load", "path": "/tmp/sales.csv", "refuse_query": "refuse_closed",
             "target": {"connector": "demo", "table": "sales"}},
            {"name": "roles", "type": "python", "callable": "app.db:create", "op_kwargs": {"admin_connector": "tools"}},
        ], "user_params": [{"name": "period", "choices_from": "query:demo/period_choices:PERIOD:LABEL"}]},
    )
    issues = check_integrity(state)
    unused = [i.message for i in issues if i.category == "Unused query"]
    assert unused == ["query 'demo.left_over' is referenced by nothing in the loaded config"]
    assert not [i for i in issues if i.category == "Unused connector"]        # demo and tools: both named by the job
    assert [(u.type, u.label) for u in find_usages(state, kind="query", name="refuse_closed", scope="demo")] == [
        ("nomaflow_step_query", "job load · step sales · refuse_query")]
    assert [(u.type, u.deep_link) for u in find_usages(state, kind="query", name="period_choices", scope="demo")] == [
        ("nomaflow_param_choices", {"editor": "jobs", "job": "load"})]
    assert [u.label for u in find_usages(state, kind="connector", name="tools")] == ["job load · step roles · admin_connector"]


def test_a_query_an_explorer_model_or_a_notification_rule_reads_is_not_unused() -> None:
    from liberty.explorer.config import parse_explorer
    from liberty.notify.rules import parse_notification_rules
    from liberty.web.usages import find_usages

    state = _state(conns={"app": ["apps_get", "mark_sent", "left_over"], "data": ["facts_get"]})
    state.explorer = parse_explorer({"explorer": {"app": {"sales": {"hub": "fact", "entities": {
        "fact": {"connector": "data", "query": "facts_get"}, "application": {"query": "apps_get"}},
        "relations": [{"from": "fact", "to": "application", "on": [["APP_ID", "APP_ID"]]}]}}}})
    state.notification_rules = parse_notification_rules({"notifications": {"sent": {
        "trigger": {"kind": "action"}, "subject": "Sent", "channels": ["action"], "action": {"connector": "app", "query": "mark_sent"}}}})
    issues = check_integrity(state)
    assert [i.message for i in issues if i.category == "Unused query"] == ["query 'app.left_over' is referenced by nothing in the loaded config"]
    assert not [i for i in issues if i.category == "Unused connector"]
    assert [(u.type, u.label) for u in find_usages(state, kind="query", name="apps_get", scope="app")] == [
        ("explorer_entity_query", "app · explorer model sales · entity application")]
    assert [u.type for u in find_usages(state, kind="query", name="mark_sent", scope="app")] == ["notification_action_query"]


def test_the_dictionary_scan_queries_are_used_by_their_name() -> None:
    """"Scan a table" reads its per-table queries by name on the connector scanned: shipping one is using it."""
    state = _state(conns={"jde": ["dictionary_data_items_for_table_get", "dictionary_data_items_filters_for_table_get", "left_over"]},
                   menus={"jde": {"items": [{"id": "m", "label": "M", "type": "query", "target": "left_over"}]}})
    assert not [i for i in check_integrity(state) if i.category == "Unused query"]


def test_a_lookup_may_read_an_api_endpoint_that_returns_rows() -> None:
    """A lookup's rows may come from an API connector's endpoint (served as a query's): not a missing query.
    An endpoint that returns no rows, or none at all, is the error."""
    from liberty.connectors.dictionary import parse_dictionary
    from liberty.web.usages import find_usages

    state = _state(conns={"app": []})
    state.connectors._conns["sm"] = SimpleNamespace(config=SimpleNamespace(endpoints=[
        SimpleNamespace(name="instances", rows_field="items", writable=False), SimpleNamespace(name="restart", rows_field=None, writable=True)]))
    state.connectors.dictionary = parse_dictionary({"connectors": {"app": {"lookups": {
        "ok": {"connector": "sm", "query": "instances", "value": "ID", "label": "NAME"},
        "no_rows": {"connector": "sm", "query": "restart", "value": "ID", "label": "NAME"},
        "gone": {"connector": "sm", "query": "nothing", "value": "ID", "label": "NAME"}}}}})
    errors = [(i.category, i.message) for i in check_integrity(state) if i.severity == "error"]
    assert errors == [
        ("Missing endpoint", "app · lookup 'gone' → endpoint 'sm.nothing' does not exist"),
        ("Missing endpoint", "app · lookup 'no_rows' → endpoint 'sm.restart' is not an endpoint that returns rows"),
    ]
    assert [(u.type, u.label) for u in find_usages(state, kind="api_endpoint", name="instances", scope="sm")] == [("lookup_endpoint", "app · lookup ok")]
