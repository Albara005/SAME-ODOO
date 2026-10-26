"""Request dependencies: database session, current user, organization context, permission checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from typing import Iterator

import jwt
from fastapi import Depends, HTTPException, Path, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.db import utcnow
from novara.modules.access.models import Role
from novara.modules.access.permissions import effective_permissions
from novara.modules.identity.models import User, UserSession
from novara.modules.organizations.models import Membership, Organization
from novara.security import decode_token
from novara.tenancy import bind_tenant, bind_user

_bearer = HTTPBearer(auto_error=False)


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.sessionmaker()
    try:
        yield session
    finally:
        session.close()


@dataclass
class AuthContext:
    user: User
    session: UserSession


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated", headers={"WWW-Authenticate": "Bearer"})


def current_auth(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> AuthContext:
    if creds is None:
        raise _unauthorized()
    settings = request.app.state.settings
    try:
        claims = decode_token(creds.credentials, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm)
    except jwt.PyJWTError:
        raise _unauthorized()
    login = db.get(UserSession, claims["sid"])
    if login is None or login.user_id != claims["sub"] or login.revoked_at is not None:
        raise _unauthorized()
    expires_at = login.expires_at if login.expires_at.tzinfo else login.expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= utcnow():
        raise _unauthorized()
    user = db.get(User, login.user_id)
    if user is None or user.status != "active":
        raise _unauthorized()
    bind_user(db, user.id)
    return AuthContext(user=user, session=login)


def current_user(auth: AuthContext = Depends(current_auth)) -> User:
    return auth.user


def two_step(auth: AuthContext) -> bool:
    """Whether this sign-in meets a two-step requirement: the account has the app on (so every password
    sign-in asks for a code), or this session was opened with a passkey or a code."""
    return auth.user.mfa_enabled or auth.session.two_step


@dataclass
class OrgContext:
    organization: Organization
    membership: Membership
    role: Role
    user: User
    two_step: bool = False  # see two_step(); set for requests, False for the workflow worker

    @property
    def permissions(self) -> set[str]:
        return effective_permissions(self.role)

    def has(self, permission: str) -> bool:
        return permission in self.permissions


MFA_REQUIRED = ("this organization requires two-step sign-in: sign in with a passkey, or turn on an authenticator "
                "app in your account, to continue")


def membership_context(
    org_id: str = Path(...),
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
) -> OrgContext:
    """Membership only, without the organization's two-step sign-in requirement. Used just to
    describe the organization to its member (so the app can explain what is needed)."""
    ctx = member_context(db, org_id, auth.user)
    if ctx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "organization not found")
    ctx.two_step = two_step(auth)
    return ctx


def org_context(ctx: OrgContext = Depends(membership_context)) -> OrgContext:
    """Bind the request's DB session to the organization in the URL after checking membership.

    Non-members get 404 rather than 403 so organization ids can't be probed. When the
    organization requires two-step sign-in, a sign-in without a second step gets 403.
    """
    if ctx.organization.require_mfa and not ctx.two_step:
        raise HTTPException(status.HTTP_403_FORBIDDEN, MFA_REQUIRED)
    return ctx


def member_context(db: Session, org_id: str, user: User) -> OrgContext | None:
    """The user's context in an organization, or None if they aren't an active member.
    Binds the session to that organization. Also used outside requests (workflow worker)."""
    org = db.get(Organization, org_id)
    if org is None:
        return None
    bind_tenant(db, org.id)
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
    if membership is None or membership.status != "active" or user.status != "active":
        return None
    role = db.get(Role, membership.role_id)
    return OrgContext(organization=org, membership=membership, role=role, user=user)


def require(permission: str):
    def checker(ctx: OrgContext = Depends(org_context)) -> OrgContext:
        if not ctx.has(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission: {permission}")
        return ctx

    return checker


def require_any(*permissions: str):
    def checker(ctx: OrgContext = Depends(org_context)) -> OrgContext:
        if not ctx.permissions.intersection(permissions):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing one of: {', '.join(permissions)}")
        return ctx

    return checker


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""
