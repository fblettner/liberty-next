from __future__ import annotations

import pytest
import pytest_asyncio

from liberty.auth.db import AuthDatabase
from liberty.auth.password import verify_password
from liberty.auth.service import AuthError, AuthService
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry


@pytest_asyncio.fixture
async def auth_db(tmp_path):
    pools = PoolRegistry({"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")})
    db = AuthDatabase(pools, "default")
    await db.create_schema()
    yield db
    await pools.dispose()


@pytest.mark.asyncio
async def test_create_user_and_authenticate(auth_db: AuthDatabase) -> None:
    async with auth_db.session() as s:
        svc = AuthService(s)
        await svc.create_user("alice", password="alicepw", email="a@x.test", full_name="Alice A")
    # fresh session — verifies it persisted
    async with auth_db.session() as s:
        svc = AuthService(s)
        user = await svc.authenticate("alice", "alicepw")
        assert user is not None and user.username == "alice" and user.email == "a@x.test"
        assert await svc.authenticate("alice", "wrong") is None
        assert await svc.authenticate("nobody", "x") is None


@pytest.mark.asyncio
async def test_duplicate_username_rejected(auth_db: AuthDatabase) -> None:
    async with auth_db.session() as s:
        svc = AuthService(s)
        await svc.create_user("dup", password="p")
        with pytest.raises(AuthError):
            await svc.create_user("dup", password="p2")


@pytest.mark.asyncio
async def test_inactive_user_cannot_authenticate(auth_db: AuthDatabase) -> None:
    async with auth_db.session() as s:
        svc = AuthService(s)
        await svc.create_user("bob", password="bobpw", is_active=False)
    async with auth_db.session() as s:
        assert await AuthService(s).authenticate("bob", "bobpw") is None


@pytest.mark.asyncio
async def test_oidc_user_has_no_password_login(auth_db: AuthDatabase) -> None:
    async with auth_db.session() as s:
        svc = AuthService(s)
        await svc.create_user("ext", provider="oidc", provider_subject="sub-1")
    async with auth_db.session() as s:
        assert await AuthService(s).authenticate("ext", "anything") is None


@pytest.mark.asyncio
async def test_set_password(auth_db: AuthDatabase) -> None:
    async with auth_db.session() as s:
        svc = AuthService(s)
        user = await svc.create_user("carol", password="old")
        await svc.set_password(user, "new")
    async with auth_db.session() as s:
        svc = AuthService(s)
        assert await svc.authenticate("carol", "old") is None
        assert await svc.authenticate("carol", "new") is not None


@pytest.mark.asyncio
async def test_roles_and_permissions(auth_db: AuthDatabase) -> None:
    async with auth_db.session() as s:
        svc = AuthService(s)
        await svc.get_or_create_role("reader", permissions=["sql:liberty:read"])
        await svc.get_or_create_role("editor", permissions=["sql:liberty:write"])
        user = await svc.create_user("dave", password="p", roles=["reader"])
        await svc.assign_roles(user, ["editor"])
    async with auth_db.session() as s:
        user = await AuthService(s).get_user_by_username("dave")
        assert user is not None
        assert user.role_names == ["editor", "reader"]
        assert user.permissions == ["sql:liberty:read", "sql:liberty:write"]


@pytest.mark.asyncio
async def test_assign_unknown_role_rejected(auth_db: AuthDatabase) -> None:
    async with auth_db.session() as s:
        svc = AuthService(s)
        with pytest.raises(AuthError):
            await svc.create_user("eve", password="p", roles=["ghost"])


@pytest.mark.asyncio
async def test_get_or_create_role_updates(auth_db: AuthDatabase) -> None:
    async with auth_db.session() as s:
        svc = AuthService(s)
        r1 = await svc.get_or_create_role("ops", permissions=["a:b:c"])
        r2 = await svc.get_or_create_role("ops", permissions=["x:y:z"], description="ops team")
        assert r1.id == r2.id
        assert r2.permissions == ["x:y:z"]
        assert r2.description == "ops team"


@pytest.mark.asyncio
async def test_provision_oidc_user_creates_then_updates(auth_db: AuthDatabase) -> None:
    claims = {"sub": "kc-123", "preferred_username": "frank", "email": "f@x.test", "name": "Frank F"}
    async with auth_db.session() as s:
        svc = AuthService(s)
        u1 = await svc.provision_oidc_user(claims)
        assert u1.provider == "oidc" and u1.provider_subject == "kc-123"
        assert u1.username == "frank" and u1.password_hash is None
    # second login with an updated email → same row, refreshed
    async with auth_db.session() as s:
        svc = AuthService(s)
        u2 = await svc.provision_oidc_user({**claims, "email": "frank@new.test"})
        assert u2.id == u1.id
        assert u2.email == "frank@new.test"


@pytest.mark.asyncio
async def test_provision_oidc_username_collision(auth_db: AuthDatabase) -> None:
    async with auth_db.session() as s:
        svc = AuthService(s)
        await svc.create_user("frank", password="p")  # local user owns the name
    async with auth_db.session() as s:
        svc = AuthService(s)
        u = await svc.provision_oidc_user({"sub": "kc-9", "preferred_username": "frank"})
        assert u.username == "frank-2" and u.provider == "oidc"


@pytest.mark.asyncio
async def test_provision_oidc_requires_sub(auth_db: AuthDatabase) -> None:
    async with auth_db.session() as s:
        with pytest.raises(AuthError):
            await AuthService(s).provision_oidc_user({"preferred_username": "x"})


@pytest.mark.asyncio
async def test_password_is_hashed_not_stored_plaintext(auth_db: AuthDatabase) -> None:
    async with auth_db.session() as s:
        svc = AuthService(s)
        user = await svc.create_user("gina", password="s3cr3t")
        assert user.password_hash != "s3cr3t"
        assert verify_password(user.password_hash, "s3cr3t")


@pytest.mark.asyncio
async def test_count_users(auth_db: AuthDatabase) -> None:
    async with auth_db.session() as s:
        svc = AuthService(s)
        assert await svc.count_users() == 0
        await svc.create_user("h1", password="p")
        await svc.create_user("h2", password="p")
        assert await svc.count_users() == 2
