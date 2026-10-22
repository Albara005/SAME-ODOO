from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.access.models import Role
from novara.modules.audit import service as audit
from novara.modules.organizations.models import Membership

router = APIRouter(tags=["access"])


class RoleIn(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=120)
    permissions: list[str]


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    permissions: list[str] | None = None


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    name: str
    permissions: list[str]
    is_system: bool


def _validate_permissions(ctx: OrgContext, perms: list[str]) -> list[str]:
    unknown = P.unknown_permissions(perms)
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown permissions: {unknown}")
    beyond = set(perms) - ctx.permissions
    if beyond:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"cannot grant permissions you lack: {sorted(beyond)}")
    return sorted(set(perms))


@router.get("/permissions")
def permission_catalog() -> dict[str, str]:
    return P.CATALOG


@router.get("/orgs/{org_id}/roles", response_model=list[RoleOut])
def list_roles(_: OrgContext = Depends(require(P.MEMBERS_READ)), db: Session = Depends(get_db)):
    return db.scalars(select(Role).order_by(Role.is_system.desc(), Role.key)).all()


@router.post("/orgs/{org_id}/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
def create_role(body: RoleIn, request: Request, ctx: OrgContext = Depends(require(P.ROLES_MANAGE)),
                db: Session = Depends(get_db)):
    perms = _validate_permissions(ctx, body.permissions)
    if db.scalar(select(Role).where(Role.key == body.key)):
        raise HTTPException(status.HTTP_409_CONFLICT, "role key already exists")
    role = Role(key=body.key, name=body.name, permissions=perms)
    db.add(role)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="role.created", target_type="role", target_id=role.id,
                 details={"key": role.key, "permissions": perms}, ip_address=client_ip(request))
    db.commit()
    return role


def _custom_role(db: Session, role_id: str) -> Role:
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "role not found")
    if role.is_system:
        raise HTTPException(status.HTTP_409_CONFLICT, "system roles cannot be changed")
    return role


@router.patch("/orgs/{org_id}/roles/{role_id}", response_model=RoleOut)
def update_role(role_id: str, body: RoleUpdate, request: Request,
                ctx: OrgContext = Depends(require(P.ROLES_MANAGE)), db: Session = Depends(get_db)):
    role = _custom_role(db, role_id)
    _validate_permissions(ctx, role.permissions)  # can't edit a role more powerful than yourself
    details: dict = {}
    if body.name is not None:
        role.name = body.name
        details["name"] = body.name
    if body.permissions is not None:
        perms = _validate_permissions(ctx, body.permissions)
        details["permissions"] = {"from": role.permissions, "to": perms}
        role.permissions = perms
    audit.record(db, actor_user_id=ctx.user.id, action="role.updated", target_type="role", target_id=role.id,
                 details=details, ip_address=client_ip(request))
    db.commit()
    return role


@router.delete("/orgs/{org_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_role(role_id: str, request: Request, ctx: OrgContext = Depends(require(P.ROLES_MANAGE)),
                db: Session = Depends(get_db)):
    role = _custom_role(db, role_id)
    if db.scalar(select(func.count()).select_from(Membership).where(Membership.role_id == role.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "role is still assigned to members")
    audit.record(db, actor_user_id=ctx.user.id, action="role.deleted", target_type="role", target_id=role.id,
                 details={"key": role.key}, ip_address=client_ip(request))
    db.delete(role)
    db.commit()
