from __future__ import annotations

import pytest

from liberty.auth.principal import Principal


def test_from_claims() -> None:
    p = Principal.from_claims(
        {
            "sub": "5",
            "username": "alice",
            "email": "a@x.test",
            "roles": ["reader", "editor"],
            "perms": ["sql:liberty:read"],
            "sup": False,
            "provider": "oidc",
        }
    )
    assert p.id == "5"
    assert p.username == "alice"
    assert p.roles == ("reader", "editor")
    assert p.permissions == ("sql:liberty:read",)
    assert p.provider == "oidc"
    assert p.is_superuser is False


def test_from_claims_defaults_username_to_sub() -> None:
    p = Principal.from_claims({"sub": "9"})
    assert p.username == "9"
    assert p.roles == () and p.permissions == ()


@pytest.mark.parametrize(
    ("perms", "ask", "ok"),
    [
        (["sql:liberty:read"], "sql:liberty:read", True),
        (["sql:liberty:read"], "sql:liberty:write", False),
        (["sql:liberty:*"], "sql:liberty:write", True),
        (["sql:*"], "sql:liberty:read", True),
        (["sql:*"], "api:svc:call", False),
        (["*:liberty:read"], "sql:liberty:read", True),
        (["sql:liberty"], "sql:liberty:read", False),
        ([], "anything", False),
        (["*"], "anything:at:all", True),
    ],
)
def test_has_permission(perms: list[str], ask: str, ok: bool) -> None:
    p = Principal(id="1", username="u", permissions=tuple(perms))
    assert p.has_permission(ask) is ok


def test_superuser_bypasses_everything() -> None:
    p = Principal(id="1", username="root", is_superuser=True)
    assert p.has_permission("whatever:you:want") is True
    assert p.has_role("any-role") is True
    p.require_permission("x:y:z")  # does not raise


def test_superuser_ignores_deny() -> None:
    # A superuser bypasses even an explicit deny (the flag is absolute).
    p = Principal(id="1", username="root", is_superuser=True, permissions=("!sql:x:y",))
    assert p.has_permission("sql:x:y") is True


@pytest.mark.parametrize(
    "perms,ask,ok",
    [
        # all-access baseline minus a specific deny ("all access but disable X")
        (["*", "!sql:nomasx1:security_users_get"], "sql:nomasx1:security_users_get", False),
        (["*", "!sql:nomasx1:security_users_get"], "sql:nomasx1:security_roles_get", True),
        # deny a whole connector while keeping everything else
        (["*", "!sql:nomasx1:*"], "sql:nomasx1:anything", False),
        (["*", "!sql:nomasx1:*"], "sql:nomajde:x", True),
        # deny wins even when a more specific allow is present
        (["sql:nomasx1:*", "!sql:nomasx1:security_users_get"], "sql:nomasx1:security_users_get", False),
        (["sql:nomasx1:*", "!sql:nomasx1:security_users_get"], "sql:nomasx1:other_get", True),
        # first-class menu / dashboard deny
        (["*", "!menu:nomasx1:security"], "menu:nomasx1:security", False),
        (["*", "!dashboard:nomasx1_overview"], "dashboard:nomasx1_overview", False),
        (["*", "!menu:nomasx1:security"], "menu:nomasx1:license", True),
    ],
)
def test_deny_rules(perms: list[str], ask: str, ok: bool) -> None:
    p = Principal(id="1", username="u", permissions=tuple(perms))
    assert p.has_permission(ask) is ok


def test_is_denied_distinguishes_from_not_granted() -> None:
    p = Principal(id="1", username="u", permissions=("*", "!menu:nomasx1:security"))
    assert p.is_denied("menu:nomasx1:security") is True
    assert p.is_denied("menu:nomasx1:license") is False   # not denied, even though...
    # ...a no-grant role: not denied, just not allowed
    p2 = Principal(id="2", username="u2", permissions=("sql:x:y",))
    assert p2.is_denied("menu:a:b") is False
    assert p2.has_permission("menu:a:b") is False


def test_require_permission_raises() -> None:
    p = Principal(id="1", username="u", permissions=("sql:liberty:read",))
    with pytest.raises(PermissionError):
        p.require_permission("sql:liberty:write")


def test_has_role() -> None:
    p = Principal(id="1", username="u", roles=("reader",))
    assert p.has_role("reader") is True
    assert p.has_role("admin") is False


def test_from_user_uses_derived_props() -> None:
    class _Role:
        def __init__(self, name, perms):
            self.name = name
            self.permissions = perms

    class _User:
        id = 3
        username = "bob"
        email = "b@x.test"
        is_superuser = False
        provider = "local"
        roles = [_Role("reader", ["sql:liberty:read"]), _Role("zeta", ["api:svc:*"])]

        @property
        def role_names(self):
            return sorted(r.name for r in self.roles)

        @property
        def permissions(self):
            out = set()
            for r in self.roles:
                out.update(r.permissions)
            return sorted(out)

    p = Principal.from_user(_User())
    assert p.id == "3"
    assert p.roles == ("reader", "zeta")
    assert set(p.permissions) == {"sql:liberty:read", "api:svc:*"}
    assert p.has_permission("api:svc:call") is True
