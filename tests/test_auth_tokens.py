from __future__ import annotations

import pytest

from liberty.auth.tokens import ACCESS, REFRESH, TokenConfig, TokenError, TokenService


def _svc(**overrides) -> TokenService:
    return TokenService(TokenConfig(secret="unit-test-secret", issuer="liberty-test", **overrides))


def test_access_token_roundtrip() -> None:
    svc = _svc()
    issued = svc.access_token(
        subject="42",
        username="alice",
        email="alice@example.test",
        roles=["reader"],
        permissions=["sql:liberty:read"],
        is_superuser=False,
        provider="local",
    )
    assert issued.token_type == ACCESS
    claims = svc.decode(issued.token, expected_type=ACCESS)
    assert claims["sub"] == "42"
    assert claims["username"] == "alice"
    assert claims["roles"] == ["reader"]
    assert claims["perms"] == ["sql:liberty:read"]
    assert claims["sup"] is False
    assert claims["iss"] == "liberty-test"
    assert claims["exp"] - claims["iat"] == 3600


def test_refresh_token_minimal_claims() -> None:
    svc = _svc()
    issued = svc.refresh_token(subject="7")
    claims = svc.decode(issued.token, expected_type=REFRESH)
    assert claims["sub"] == "7"
    assert claims["typ"] == REFRESH
    assert "perms" not in claims


def test_wrong_type_rejected() -> None:
    svc = _svc()
    access = svc.access_token(subject="1", username="u")
    with pytest.raises(TokenError):
        svc.decode(access.token, expected_type=REFRESH)


def test_expired_token_rejected() -> None:
    svc = _svc(access_ttl=-5)
    issued = svc.access_token(subject="1", username="u")
    with pytest.raises(TokenError, match="expired"):
        svc.decode(issued.token)


def test_wrong_secret_rejected() -> None:
    good = _svc()
    issued = good.access_token(subject="1", username="u")
    other = TokenService(TokenConfig(secret="different-secret", issuer="liberty-test"))
    with pytest.raises(TokenError):
        other.decode(issued.token)


def test_wrong_issuer_rejected() -> None:
    issued = _svc().access_token(subject="1", username="u")
    other = TokenService(TokenConfig(secret="unit-test-secret", issuer="someone-else"))
    with pytest.raises(TokenError):
        other.decode(issued.token)


def test_garbage_rejected() -> None:
    with pytest.raises(TokenError):
        _svc().decode("not.a.jwt")


def test_ephemeral_config_has_random_secret() -> None:
    a = TokenConfig.ephemeral()
    b = TokenConfig.ephemeral()
    assert a.secret != b.secret and len(a.secret) > 20


# ── A3.5: PyJWT + refresh rotation ───────────────────────────────────────────────────


def test_decode_rejects_wrong_issuer_and_tampering() -> None:
    svc = _svc()
    other = TokenService(TokenConfig(secret="unit-test-secret", issuer="someone-else"))
    with pytest.raises(TokenError):
        svc.decode(other.access_token(subject="1", username="a").token)
    tok = svc.access_token(subject="1", username="a").token
    with pytest.raises(TokenError):
        svc.decode(tok[:-3] + "xyz")  # broken signature


def test_refresh_rotation_consumes_the_token_after_the_grace() -> None:
    svc = _svc(refresh_reuse_grace=0)  # strict single use
    claims = svc.decode(svc.refresh_token(subject="1").token, expected_type=REFRESH)
    svc.consume_refresh(claims)  # first exchange
    with pytest.raises(TokenError, match="already used"):
        svc.consume_refresh(claims)  # replay


def test_refresh_rotation_allows_a_racing_second_tab_inside_the_grace() -> None:
    svc = _svc(refresh_reuse_grace=60)
    claims = svc.decode(svc.refresh_token(subject="1").token, expected_type=REFRESH)
    svc.consume_refresh(claims)
    svc.consume_refresh(claims)  # a second tab presenting the same stored token right away


def test_refresh_rotation_is_per_token_not_per_user() -> None:
    svc = _svc(refresh_reuse_grace=0)
    a = svc.decode(svc.refresh_token(subject="1").token, expected_type=REFRESH)
    b = svc.decode(svc.refresh_token(subject="1").token, expected_type=REFRESH)  # second device
    svc.consume_refresh(a)
    svc.consume_refresh(b)  # the other device's token is untouched
