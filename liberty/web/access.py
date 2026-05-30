"""``/admin/access`` — RBAC management (users + roles), superuser-only.

The framework already *enforces* role-based access (``Principal.has_permission`` against the
colon-segmented permission strings — ``sql:<connector>:<query>``, ``api:<connector>:<endpoint>``,
``ai:chat``, ``*`` wildcards — plus the superuser bypass). Until now those users + roles were
managed only via the ``liberty-admin`` CLI / hand-edited ``auth.toml``. These endpoints expose the
same operations to the Settings → Access UI: list / create / edit users, set their roles + active
+ superuser + password, and define roles' permission sets.

Works against whichever auth backend is configured (``[auth] backend`` = ``toml`` | ``db``) — both
implement the same :class:`~liberty.auth.authstore.AuthBackend` protocol. A superuser can't lock
*itself* out (deactivate / de-superuser its own account) — guarded below.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from liberty.auth.authstore import AuthBackend, AuthError
from liberty.auth.dependencies import CurrentPrincipal, get_auth_backend, require_superuser
from liberty.auth.principal import Principal

router = APIRouter(prefix="/admin/access", tags=["access"])

Superuser = Annotated[Principal, Depends(require_superuser)]
Backend = Annotated[AuthBackend, Depends(get_auth_backend)]


# ── schemas ───────────────────────────────────────────────────────────────────────────────
class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str | None = Field(default=None, min_length=8)
    email: str | None = Field(default=None, max_length=320)
    full_name: str | None = Field(default=None, max_length=200)
    roles: list[str] = Field(default_factory=list)
    is_superuser: bool = False
    is_active: bool = True


class UserUpdate(BaseModel):
    # All optional — only the provided fields are applied (PATCH semantics).
    email: str | None = None
    full_name: str | None = None
    roles: list[str] | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None


class PasswordSet(BaseModel):
    password: str = Field(min_length=8)


class RoleUpsert(BaseModel):
    permissions: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=500)


def _role_dict(t: tuple[str, list[str], str | None]) -> dict[str, Any]:
    name, perms, desc = t
    return {"name": name, "permissions": perms, "description": desc}


# ── users ─────────────────────────────────────────────────────────────────────────────────
@router.get("/users", summary="List users")
async def list_users(_: Superuser, backend: Backend) -> dict[str, Any]:
    return {"users": [u.public_dict() for u in await backend.list_users()]}


@router.post("/users", status_code=status.HTTP_201_CREATED, summary="Create user")
async def create_user(body: UserCreate, _: Superuser, backend: Backend) -> dict[str, Any]:
    try:
        u = await backend.create_user(
            body.username, password=body.password, email=body.email, full_name=body.full_name,
            is_superuser=body.is_superuser, is_active=body.is_active, roles=body.roles,
        )
    except AuthError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return u.public_dict()


@router.patch("/users/{username}", summary="Update user")
async def update_user(username: str, body: UserUpdate, principal: CurrentPrincipal, _: Superuser, backend: Backend) -> dict[str, Any]:
    # Self-lockout guard — don't let a superuser strip its own access in a way that could leave
    # the install unmanageable (deactivate self / drop own superuser).
    if username == principal.username:
        if body.is_active is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="You can't deactivate your own account.")
        if body.is_superuser is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="You can't remove your own superuser flag.")
    try:
        if body.email is not None or body.full_name is not None:
            # update_profile sets both; fetch current to preserve the one not being changed.
            cur = await backend.get_by_username(username)
            email = body.email if body.email is not None else (cur.email if cur else None)
            full_name = body.full_name if body.full_name is not None else (cur.full_name if cur else None)
            await backend.update_profile(username, full_name=(full_name or None) if full_name is not None else None, email=(email or None) if email is not None else None)
        if body.roles is not None:
            await backend.assign_roles(username, body.roles, replace=True)
        if body.is_active is not None:
            await backend.set_active(username, body.is_active)
        if body.is_superuser is not None:
            await backend.set_superuser(username, body.is_superuser)
    except AuthError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    u = await backend.get_by_username(username)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown user {username!r}")
    return u.public_dict()


@router.post("/users/{username}/password", summary="Set user password")
async def set_user_password(username: str, body: PasswordSet, _: Superuser, backend: Backend) -> dict[str, Any]:
    try:
        await backend.set_password(username, body.password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"ok": True}


# ── roles ─────────────────────────────────────────────────────────────────────────────────
@router.get("/roles", summary="List roles")
async def list_roles(_: Superuser, backend: Backend) -> dict[str, Any]:
    return {"roles": [_role_dict(r) for r in await backend.list_roles()]}


@router.put("/roles/{name}", summary="Upsert role")
async def upsert_role(name: str, body: RoleUpsert, _: Superuser, backend: Backend) -> dict[str, Any]:
    # get_or_create_role is an upsert — creates the role or replaces its permissions / description.
    t = await backend.get_or_create_role(name, permissions=body.permissions, description=body.description)
    return _role_dict(t)
