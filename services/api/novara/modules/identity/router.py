from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara.db import utcnow
from novara.deps import AuthContext, client_ip, current_auth, current_user, get_db
from novara.modules.identity.models import User, UserSession
from novara.security import DUMMY_HASH, MIN_PASSWORD_LENGTH, hash_password, issue_token, verify_password

router = APIRouter(tags=["identity"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)
    full_name: str = Field(default="", max_length=200)
    locale: str = Field(default="ar", pattern="^(ar|en)$")


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    locale: str
    timezone: str
    email_verified: bool
    mfa_enabled: bool


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    expires_at: datetime
    user_agent: str
    ip_address: str
    current: bool = False


def _normalise_email(email: str) -> str:
    return email.strip().lower()


@router.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, db: Session = Depends(get_db)) -> User:
    email = _normalise_email(body.email)
    if db.scalar(select(func.count()).select_from(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    user = User(email=email, password_hash=hash_password(body.password), full_name=body.full_name, locale=body.locale)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    return user


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    user = db.scalar(select(User).where(User.email == _normalise_email(body.email)))
    ok = verify_password(user.password_hash if user else DUMMY_HASH, body.password)
    if not ok or user is None or user.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
    settings = request.app.state.settings
    expires_at = utcnow() + timedelta(minutes=settings.session_ttl_minutes)
    login_session = UserSession(
        user_id=user.id,
        expires_at=expires_at,
        user_agent=request.headers.get("user-agent", "")[:500],
        ip_address=client_ip(request),
    )
    db.add(login_session)
    db.commit()
    token = issue_token(
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        user_id=user.id,
        session_id=login_session.id,
        expires_at=expires_at,
    )
    return TokenOut(access_token=token, expires_at=expires_at)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def logout(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)) -> None:
    auth.session.revoked_at = utcnow()
    db.commit()


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user


@router.get("/me/sessions", response_model=list[SessionOut])
def my_sessions(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)) -> list[SessionOut]:
    rows = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == auth.user.id, UserSession.revoked_at.is_(None))
        .order_by(UserSession.created_at.desc())
    )
    return [SessionOut.model_validate(r).model_copy(update={"current": r.id == auth.session.id}) for r in rows]


@router.delete("/me/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def revoke_session(session_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)) -> None:
    target = db.get(UserSession, session_id)
    if target is None or target.user_id != auth.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    target.revoked_at = utcnow()
    db.commit()
