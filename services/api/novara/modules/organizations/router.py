from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara.deps import OrgContext, client_ip, current_user, get_db, org_context, require
from novara.modules.access import permissions as P
from novara.modules.access.models import Role
from novara.modules.access.service import active_owner_count, create_system_roles, ensure_system_role
from novara.modules.accounting.models import JournalEntry
from novara.modules.accounting.service import ensure_chart
from novara.modules.audit import service as audit
from novara.modules.identity.models import User
from novara.modules.organizations.models import Membership, Organization
from novara.money import CURRENCY_DIGITS
from novara.tenancy import bind_tenant, system_scope

router = APIRouter(tags=["organizations"])

_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,78}[a-z0-9])$")


class OrgIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(min_length=3, max_length=80)
    default_locale: str = Field(default="ar", pattern="^(ar|en)$")
    default_currency: str = Field(default="SAR", pattern="^[A-Z]{3}$")


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    default_locale: str | None = Field(default=None, pattern="^(ar|en)$")
    default_currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    hosting_mode: str
    default_locale: str
    default_currency: str
    created_at: datetime


class MyOrgOut(OrgOut):
    role: str
    permissions: list[str]


class MemberIn(BaseModel):
    email: EmailStr
    role: str


class MemberUpdate(BaseModel):
    role: str | None = None
    status: str | None = Field(default=None, pattern="^(active|suspended)$")


class MemberOut(BaseModel):
    id: str
    user_id: str
    email: str
    full_name: str
    role: str
    status: str
    created_at: datetime


def _member_out(m: Membership, user: User, role: Role) -> MemberOut:
    return MemberOut(
        id=m.id, user_id=user.id, email=user.email, full_name=user.full_name,
        role=role.key, status=m.status, created_at=m.created_at,
    )


@router.post("/orgs", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
def create_org(body: OrgIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    slug = body.slug.lower()
    if not _SLUG.match(slug):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "slug must be lowercase letters, digits and dashes")
    _check_currency(body.default_currency)
    org = Organization(name=body.name, slug=slug, default_locale=body.default_locale, default_currency=body.default_currency)
    db.add(org)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "slug already taken")
    bind_tenant(db, org.id)
    roles = create_system_roles(db)
    ensure_chart(db)
    db.add(Membership(user_id=user.id, role_id=roles[P.OWNER_ROLE].id))
    audit.record(db, actor_user_id=user.id, action="org.created", target_type="organization",
                 target_id=org.id, details={"slug": slug}, ip_address=client_ip(request))
    db.commit()
    return org


@router.get("/orgs", response_model=list[MyOrgOut])
def my_orgs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    # Deliberate cross-organization read, limited to the caller's own memberships.
    with system_scope(db):
        rows = db.execute(
            select(Organization, Role)
            .join(Membership, Membership.organization_id == Organization.id)
            .join(Role, Role.id == Membership.role_id)
            .where(Membership.user_id == user.id, Membership.status == "active")
            .order_by(Organization.name)
        ).all()
    return [
        MyOrgOut(**OrgOut.model_validate(org).model_dump(), role=role.key, permissions=sorted(P.effective_permissions(role)))
        for org, role in rows
    ]


@router.get("/orgs/{org_id}", response_model=MyOrgOut)
def get_org(ctx: OrgContext = Depends(org_context)):
    return MyOrgOut(**OrgOut.model_validate(ctx.organization).model_dump(),
                    role=ctx.role.key, permissions=sorted(ctx.permissions))


@router.patch("/orgs/{org_id}", response_model=OrgOut)
def update_org(body: OrgUpdate, request: Request, ctx: OrgContext = Depends(require(P.ORG_MANAGE)),
               db: Session = Depends(get_db)):
    changes = body.model_dump(exclude_none=True)
    if "default_currency" in changes and changes["default_currency"] != ctx.organization.default_currency:
        _check_currency(changes["default_currency"])
        if db.scalar(select(JournalEntry.id).limit(1)):
            raise HTTPException(status.HTTP_409_CONFLICT, "currency cannot change once the ledger has entries")
    for field, value in changes.items():
        setattr(ctx.organization, field, value)
    audit.record(db, actor_user_id=ctx.user.id, action="org.updated", target_type="organization",
                 target_id=ctx.organization.id, details=changes, ip_address=client_ip(request))
    db.commit()
    return ctx.organization


def _check_currency(code: str) -> None:
    if code not in CURRENCY_DIGITS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unsupported currency: {code}")


# ---------------------------------------------------------------- members


def _role_by_key(db: Session, key: str) -> Role:
    role = db.scalar(select(Role).where(Role.key == key))
    if role is None and key in P.SYSTEM_ROLES:
        # System roles added to the catalog after this organization was created.
        role = ensure_system_role(db, key)
    if role is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown role: {key}")
    return role


def _ensure_can_grant(ctx: OrgContext, role: Role) -> None:
    """Nobody can hand out (or take away) permissions they don't hold themselves."""
    missing = P.effective_permissions(role) - ctx.permissions
    if missing:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"cannot manage role with permissions you lack: {sorted(missing)}")


@router.get("/orgs/{org_id}/members", response_model=list[MemberOut])
def list_members(ctx: OrgContext = Depends(require(P.MEMBERS_READ)), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Membership, User, Role)
        .join(User, User.id == Membership.user_id)
        .join(Role, Role.id == Membership.role_id)
        .order_by(User.email)
    ).all()
    return [_member_out(m, u, r) for m, u, r in rows]


@router.post("/orgs/{org_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(body: MemberIn, request: Request, ctx: OrgContext = Depends(require(P.MEMBERS_MANAGE)),
               db: Session = Depends(get_db)):
    role = _role_by_key(db, body.role)
    _ensure_can_grant(ctx, role)
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None:
        # Invitations for people without an account come with the notifications module.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no account with that email")
    if db.scalar(select(Membership).where(Membership.user_id == user.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "already a member")
    m = Membership(user_id=user.id, role_id=role.id)
    db.add(m)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="member.added", target_type="membership",
                 target_id=m.id, details={"user_id": user.id, "role": role.key}, ip_address=client_ip(request))
    db.commit()
    return _member_out(m, user, role)


def _load_member(db: Session, member_id: str) -> tuple[Membership, User, Role]:
    m = db.get(Membership, member_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    return m, db.get(User, m.user_id), db.get(Role, m.role_id)


def _ensure_owner_remains(db: Session, current_role: Role) -> None:
    if current_role.key == P.OWNER_ROLE and active_owner_count(db) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "an organization must keep at least one active owner")


@router.patch("/orgs/{org_id}/members/{member_id}", response_model=MemberOut)
def update_member(member_id: str, body: MemberUpdate, request: Request,
                  ctx: OrgContext = Depends(require(P.MEMBERS_MANAGE)), db: Session = Depends(get_db)):
    m, user, role = _load_member(db, member_id)
    _ensure_can_grant(ctx, role)
    details: dict = {}
    new_role = role
    if body.role is not None and body.role != role.key:
        new_role = _role_by_key(db, body.role)
        _ensure_can_grant(ctx, new_role)
        _ensure_owner_remains(db, role)
        m.role_id = new_role.id
        details["role"] = {"from": role.key, "to": new_role.key}
    if body.status is not None and body.status != m.status:
        if body.status != "active":
            _ensure_owner_remains(db, role)
        details["status"] = {"from": m.status, "to": body.status}
        m.status = body.status
    if details:
        audit.record(db, actor_user_id=ctx.user.id, action="member.updated", target_type="membership",
                     target_id=m.id, details=details, ip_address=client_ip(request))
    db.commit()
    return _member_out(m, user, new_role)


@router.delete("/orgs/{org_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def remove_member(member_id: str, request: Request, ctx: OrgContext = Depends(require(P.MEMBERS_MANAGE)),
                  db: Session = Depends(get_db)):
    m, user, role = _load_member(db, member_id)
    _ensure_can_grant(ctx, role)
    if m.status == "active":
        _ensure_owner_remains(db, role)
    audit.record(db, actor_user_id=ctx.user.id, action="member.removed", target_type="membership",
                 target_id=m.id, details={"user_id": user.id, "role": role.key}, ip_address=client_ip(request))
    db.delete(m)
    db.commit()
