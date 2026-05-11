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
