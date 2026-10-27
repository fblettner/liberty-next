"""liberty.auth.scope — rules, resolution against user attributes, the SQL predicate and the
write check. Roles OR together, columns AND within a role."""

from __future__ import annotations

import pytest

from liberty.auth.scope import DataScope, ScopeRule, resolve_scope


def test_rule_validation() -> None:
    ScopeRule(column="COMPANY", values=["A"])
    ScopeRule(dd="KCO", source="#USER.company#", mode="deny")
    with pytest.raises(ValueError):
        ScopeRule(values=["A"])  # neither column nor dd
    with pytest.raises(ValueError):
        ScopeRule(column="C")  # neither values nor source
    with pytest.raises(ValueError):
        ScopeRule(column="C", values=["A"], source="#USER.x#")
    with pytest.raises(ValueError):
        ScopeRule(column="C", source="USER.x")


def test_resolve_scope() -> None:
    r1 = [ScopeRule(column="COMPANY", source="#USER.company#"), ScopeRule(column="POLE", values=["X"])]
    r2 = [ScopeRule(column="COMPANY", values=["B"])]
    attrs = {"company": ["A", "C"]}
    assert resolve_scope([r1, r2], attrs) == [
        [{"column": "COMPANY", "dd": None, "values": ["A", "C"], "mode": "ignore"},
         {"column": "POLE", "dd": None, "values": ["X"], "mode": "ignore"}],
        [{"column": "COMPANY", "dd": None, "values": ["B"], "mode": "ignore"}],
    ]
    assert resolve_scope([r1, r2], attrs, superuser=True) is None
    assert resolve_scope([r1, []], attrs) is None          # an unscoped role grants everything
    assert resolve_scope([], attrs) is None
    assert resolve_scope([r1], {})[0][0]["values"] == []   # attribute missing → nothing allowed on that axis
    assert resolve_scope([[ScopeRule(column="C", source="#USER.c#")]], {"c": "solo"})[0][0]["values"] == ["solo"]


def _scope(groups):
    return DataScope.from_claims(groups)


def test_predicate_or_across_roles_and_across_columns() -> None:
    sc = _scope([
        [{"column": "COMPANY", "values": ["A"]}, {"column": "POLE", "values": ["X", "Y"]}],
        [{"column": "COMPANY", "values": ["B"]}],
    ])
    cols = {"company": "COMPANY", "pole": "POLE", "amount": "AMOUNT"}
    where, binds = sc.predicate(cols, {})
    assert where == "(COMPANY IN (:rls_0_0_0_0) AND POLE IN (:rls_0_1_0_0, :rls_0_1_0_1)) OR (COMPANY IN (:rls_1_0_0_0))"
    assert binds == {"rls_0_0_0_0": "A", "rls_0_1_0_0": "X", "rls_0_1_0_1": "Y", "rls_1_0_0_0": "B"}
    assert sc.bind_columns(cols, {}) == {"rls_0_0_0_0": "COMPANY", "rls_0_1_0_0": "POLE", "rls_0_1_0_1": "POLE", "rls_1_0_0_0": "COMPANY"}
    # case-insensitive column match, actual spelling kept
    where, _ = _scope([[{"column": "company", "values": ["A"]}]]).predicate({"company": "Company"}, {})
    assert where == "(Company IN (:rls_0_0_0_0))"


def test_predicate_modes_and_dd() -> None:
    cols = {"amount": "AMOUNT", "kco": "KCO"}
    # ignore: the only rule doesn't apply → unrestricted
    assert _scope([[{"column": "COMPANY", "values": ["A"]}]]).predicate(cols, {}) == (None, {})
    # deny: the only role grants nothing → no rows
    assert _scope([[{"column": "COMPANY", "values": ["A"], "mode": "deny"}]]).predicate(cols, {}) == ("1=0", {})
    # deny in one role, the other role still grants
    where, _ = _scope([
        [{"column": "COMPANY", "values": ["A"], "mode": "deny"}],
        [{"column": "KCO", "values": ["1"]}],
    ]).predicate(cols, {})
    assert where == "(KCO IN (:rls_1_0_0_0))"
    # an empty value list allows nothing for that role
    assert _scope([[{"column": "KCO", "values": []}]]).predicate(cols, {}) == ("1=0", {})
    # dd match through the hints: column `kco` carries dd COMPANY_ID
    where, _ = _scope([[{"dd": "COMPANY_ID", "values": ["1"]}]]).predicate(cols, {"kco": "COMPANY_ID"})
    assert where == "(KCO IN (:rls_0_0_0_0))"
    # a mixed rule: both ignored in one role → that role grants everything
    assert _scope([[{"column": "A", "values": ["1"]}, {"column": "B", "values": ["2"]}]]).predicate(cols, {}) == (None, {})


def test_dd_prefix_binds_every_column_carrying_the_item() -> None:
    # JDE: ABCO (F0101) and MCCO (F0006) both carry the data item CO; a query aliasing `ABCO AS CO`
    # carries it by name; a hint saying dd = "CO" on any column carries it too
    cols = {"aban8": "ABAN8", "abco": "ABCO", "mcco": "MCCO", "co": "CO", "xx": "XX"}
    sc = _scope([[{"dd": "CO", "values": ["00001", "00050"]}]])
    where, binds = sc.predicate(cols, {"xx": "CO"}, prefix=2)
    assert where == (
        "(ABCO IN (:rls_0_0_0_0, :rls_0_0_0_1) AND MCCO IN (:rls_0_0_1_0, :rls_0_0_1_1)"
        " AND CO IN (:rls_0_0_2_0, :rls_0_0_2_1) AND XX IN (:rls_0_0_3_0, :rls_0_0_3_1))"
    )
    assert binds["rls_0_0_1_1"] == "00050" and sc.bind_columns(cols, {"xx": "CO"}, prefix=2)["rls_0_0_1_1"] == "MCCO"
    # without the prefix only the alias and the hinted column match
    where, _ = sc.predicate(cols, {"xx": "CO"})
    assert where == "(CO IN (:rls_0_0_0_0, :rls_0_0_0_1) AND XX IN (:rls_0_0_1_0, :rls_0_0_1_1))"
    # a hint's dd wins over the stripped name (ABCO hinted as something else is not CO any more)
    where, _ = sc.predicate({"abco": "ABCO"}, {"abco": "OTHER"}, prefix=2)
    assert where is None
    # a prefix never eats a name that is too short
    assert sc.predicate({"co": "CO"}, {}, prefix=2)[0] == "(CO IN (:rls_0_0_0_0, :rls_0_0_0_1))"
    # writes: every matching column must be inside
    assert sc.allows_row({"ABCO": "00001", "MCCO": "00050"}, prefix=2)
    assert not sc.allows_row({"ABCO": "00001", "MCCO": "00099"}, prefix=2)


def test_exclude_rule() -> None:
    # a black-list: everything but SY '00' / '01'; nulls stay visible
    sc = _scope([[{"dd": "SY", "values": ["00", "01"], "exclude": True}]])
    where, binds = sc.predicate({"drsy": "DRSY", "drrt": "DRRT"}, {}, prefix=2)
    assert where == "((DRSY IS NULL OR DRSY NOT IN (:rls_0_0_0_0, :rls_0_0_0_1)))"
    assert binds == {"rls_0_0_0_0": "00", "rls_0_0_0_1": "01"}
    assert sc.allows_row({"DRSY": "55"}, prefix=2) and not sc.allows_row({"DRSY": "00"}, prefix=2)
    assert not sc.allows_row({"DRSY": "00        "}, prefix=2)   # blank-padded CHAR(N) value
    # the SQL connector renders the column / bind sides (TRIM, CAST)
    where, _ = sc.predicate({"drsy": "DRSY"}, {}, prefix=2, quote=lambda c: f"TRIM(t.{c})", bind_ref=lambda b, c: f"CAST(:{b} AS VARCHAR2(4000))")
    assert where == "((TRIM(t.DRSY) IS NULL OR TRIM(t.DRSY) NOT IN (CAST(:rls_0_0_0_0 AS VARCHAR2(4000)), CAST(:rls_0_0_0_1 AS VARCHAR2(4000)))))"
    # an empty exclusion list excludes nothing
    assert _scope([[{"column": "SY", "values": [], "exclude": True}]]).predicate({"sy": "SY"}, {}) == (None, {})
    assert ScopeRule(dd="SY", values=["00"], exclude=True).exclude
    raw = resolve_scope([[ScopeRule(dd="SY", values=["00"], exclude=True)]], {})
    assert raw == [[{"column": None, "dd": "SY", "values": ["00"], "mode": "ignore", "exclude": True}]]
    assert DataScope.from_claims(raw).to_claims() == raw


def test_rule_connector_filter() -> None:
    sc = _scope([[{"dd": "CO", "values": ["00001"], "mode": "deny", "connector": "nomajde"}]])
    cols = {"abco": "ABCO"}
    assert sc.predicate(cols, {}, prefix=2, connector="nomajde")[0] == "(ABCO IN (:rls_0_0_0_0))"
    # on another connector the rule says nothing — not even its deny
    assert sc.predicate({"id": "ID"}, {}, connector="nomasx1") == (None, {})
    assert sc.allows_row({"ID": 1}, connector="nomasx1")
    assert not sc.allows_row({"ID": 1}, connector="nomajde")
    assert ScopeRule(dd="CO", values=["1"], connector="nomajde").connector == "nomajde"
    raw = resolve_scope([[ScopeRule(dd="CO", values=["1"], connector="nomajde")]], {})
    assert raw == [[{"column": None, "dd": "CO", "values": ["1"], "mode": "ignore", "connector": "nomajde"}]]
    assert DataScope.from_claims(raw).to_claims() == raw


def test_allows_row() -> None:
    sc = _scope([
        [{"column": "COMPANY", "values": ["A"]}, {"column": "POLE", "values": ["X"], "mode": "deny"}],
        [{"column": "COMPANY", "values": ["B"]}],
    ])
    assert sc.allows_row({"company": "A", "POLE": "X"})
    assert not sc.allows_row({"COMPANY": "A", "POLE": "Z"})
    assert not sc.allows_row({"COMPANY": "A"})            # POLE absent → deny for role 1, role 2 fails on COMPANY
    assert sc.allows_row({"COMPANY": "B"})                # role 2, POLE irrelevant
    assert sc.allows_row({"COMPANY": "B", "POLE": "Z"})
    # no scoped column at all: role 1 denies (POLE), role 2 ignores its only rule → grants everything
    assert sc.allows_row({"ID": 7})
    assert not _scope([[{"column": "COMPANY", "values": ["B"], "mode": "deny"}]]).allows_row({"ID": 7})


def test_claims_round_trip_and_wanted() -> None:
    raw = [[{"column": "C", "dd": None, "values": ["1"], "mode": "ignore"}]]
    sc = DataScope.from_claims(raw)
    assert sc.to_claims() == raw and sc.wanted_columns() == {"c"}
    assert DataScope.from_claims(None) is None and DataScope.from_claims([]) is None
