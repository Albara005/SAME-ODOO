from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara import crypto
from novara.db import utcnow
from novara.deps import AuthContext, client_ip, current_auth, current_user, get_db
from novara.mail import Message, deliver
from novara.modules.identity import totp
from novara.modules.identity.models import LoginAttempt, RecoveryCode, User, UserSession, UserToken
from novara.security import (
    DUMMY_HASH, MIN_PASSWORD_LENGTH, decode_mfa_token, hash_password, issue_mfa_token, issue_token, verify_password,
)

router = APIRouter(tags=["identity"])

# Sign-in limits: failures since the last success for one email, and failures from one address.
ATTEMPT_WINDOW = timedelta(minutes=15)
MAX_FAILURES_PER_EMAIL = 5
MAX_FAILURES_PER_IP = 30
MFA_TOKEN_TTL = timedelta(minutes=5)
VERIFY_TTL = timedelta(hours=48)
RESET_TTL = timedelta(hours=1)
RESETS_PER_HOUR = 3


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)
    full_name: str = Field(default="", max_length=200)
    locale: str = Field(default="ar", pattern="^(ar|en)$")


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class MfaLoginIn(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=20, description="6-digit app code or a recovery code")


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


class LoginOut(BaseModel):
    """Either a session (access_token) or, for accounts with two-factor sign-in, an mfa_token to finish it."""

    access_token: str | None = None
    token_type: str = "bearer"
    expires_at: datetime | None = None
    mfa_required: bool = False
    mfa_token: str | None = None


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    expires_at: datetime
    user_agent: str
    ip_address: str
    current: bool = False


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class ResetRequestIn(BaseModel):
    email: EmailStr


class ResetConfirmIn(TokenIn):
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)


class PasswordIn(BaseModel):
    password: str


class MfaCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class PasswordAndCodeIn(PasswordIn, MfaCodeIn):
    """Turning two-factor off or replacing recovery codes needs the password and a current code."""


class MfaSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- emails


def _link(request: Request, path: str, token: str) -> str:
    return f"{request.app.state.settings.app_base_url}{path}?token={token}"


def _send(request: Request, user: User, subject: dict[str, str], body: dict[str, str]) -> bool:
    lang = "ar" if user.locale == "ar" else "en"
    return deliver(request.app.state.mailer, Message(to=user.email, subject=subject[lang], text=body[lang]))


def _issue(db: Session, user: User, purpose: str, ttl: timedelta) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(UserToken(user_id=user.id, purpose=purpose, token_hash=crypto.token_hash(raw), expires_at=utcnow() + ttl))
    return raw


def _send_verification(request: Request, db: Session, user: User) -> bool:
    raw = _issue(db, user, "verify_email", VERIFY_TTL)
    db.commit()
    link = _link(request, "/verify-email", raw)
    return _send(request, user,
                 {"ar": "تأكيد بريدك الإلكتروني في نوفارا", "en": "Confirm your email for NOVARA"},
                 {"ar": f"مرحباً {user.full_name or ''}\n\nلتأكيد بريدك الإلكتروني افتح الرابط التالي خلال 48 ساعة:\n{link}\n\n"
                        "إذا لم تنشئ حساباً في نوفارا فتجاهل هذه الرسالة.",
                  "en": f"Hello {user.full_name or ''}\n\nTo confirm your email, open this link within 48 hours:\n{link}\n\n"
                        "If you didn't create a NOVARA account, ignore this message."})


def _use_token(db: Session, raw: str, purpose: str) -> UserToken:
    t = db.scalar(select(UserToken).where(UserToken.token_hash == crypto.token_hash(raw), UserToken.purpose == purpose))
    if t is None or t.used_at is not None or _aware(t.expires_at) <= utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this link is invalid or has expired")
    t.used_at = utcnow()
    return t


# ---------------------------------------------------------------- sign-in limits


def _retry_after(db: Session, email: str, ip: str) -> int | None:
    """Seconds until another attempt is allowed, or None if it is allowed now."""
    now = utcnow()
    since = now - ATTEMPT_WINDOW
    last_ok = db.scalar(select(func.max(LoginAttempt.created_at))
                        .where(LoginAttempt.email == email, LoginAttempt.succeeded.is_(True)))
    start = max(since, _aware(last_ok)) if last_ok else since
    failures = [_aware(d) for d in db.scalars(
        select(LoginAttempt.created_at).where(LoginAttempt.email == email, LoginAttempt.succeeded.is_(False),
                                              LoginAttempt.created_at > start).order_by(LoginAttempt.created_at))]
    waits = []
    if len(failures) >= MAX_FAILURES_PER_EMAIL:
        waits.append(failures[-MAX_FAILURES_PER_EMAIL] + ATTEMPT_WINDOW - now)
    if ip:
        from_ip = [_aware(d) for d in db.scalars(
            select(LoginAttempt.created_at).where(LoginAttempt.ip_address == ip, LoginAttempt.succeeded.is_(False),
                                                  LoginAttempt.created_at > since).order_by(LoginAttempt.created_at))]
        if len(from_ip) >= MAX_FAILURES_PER_IP:
            waits.append(from_ip[-MAX_FAILURES_PER_IP] + ATTEMPT_WINDOW - now)
    return max(1, int(max(waits).total_seconds()) + 1) if waits else None


def _check_limit(db: Session, email: str, ip: str) -> None:
    wait = _retry_after(db, email, ip)
    if wait is not None:
        minutes = (wait + 59) // 60
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"too many failed attempts; try again in {minutes} minute{'s' if minutes != 1 else ''}",
                            headers={"Retry-After": str(wait)})


def _record(db: Session, email: str, ip: str, succeeded: bool) -> None:
    db.add(LoginAttempt(email=email, ip_address=ip, succeeded=succeeded))
    # Keep the table small: attempts older than a day no longer matter.
    db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < utcnow() - timedelta(days=1)))
    db.commit()


def _new_session(db: Session, request: Request, user: User) -> TokenOut:
    settings = request.app.state.settings
    expires_at = utcnow() + timedelta(minutes=settings.session_ttl_minutes)
    login_session = UserSession(user_id=user.id, expires_at=expires_at,
                                user_agent=request.headers.get("user-agent", "")[:500], ip_address=client_ip(request))
    db.add(login_session)
    db.commit()
    token = issue_token(secret=settings.jwt_secret, algorithm=settings.jwt_algorithm, user_id=user.id,
                        session_id=login_session.id, expires_at=expires_at)
    return TokenOut(access_token=token, expires_at=expires_at)


# ---------------------------------------------------------------- account


@router.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)) -> User:
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
    _send_verification(request, db, user)
    return user


@router.post("/auth/login", response_model=LoginOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> LoginOut:
    email, ip = _normalise_email(body.email), client_ip(request)
    _check_limit(db, email, ip)
    user = db.scalar(select(User).where(User.email == email))
    ok = verify_password(user.password_hash if user else DUMMY_HASH, body.password)
    if not ok or user is None or user.status != "active":
        _record(db, email, ip, succeeded=False)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
    settings = request.app.state.settings
    if user.mfa_enabled:
        # The password was right; the session starts only after the second factor (POST /auth/login/mfa).
        token = issue_mfa_token(secret=settings.jwt_secret, algorithm=settings.jwt_algorithm, user_id=user.id,
                                expires_at=utcnow() + MFA_TOKEN_TTL, nonce=secrets.token_hex(8))
        return LoginOut(mfa_required=True, mfa_token=token)
    _record(db, email, ip, succeeded=True)
    return LoginOut(**_new_session(db, request, user).model_dump())


def _second_factor_ok(db: Session, request: Request, user: User, code: str) -> bool:
    """An authenticator code (each usable once) or an unused recovery code."""
    secret = crypto.decrypt(request.app.state.settings, user.mfa_secret, context=user.id) if user.mfa_secret else None
    step = totp.matching_step(secret, code, after=user.mfa_last_step) if secret else None
    if step is not None:
        user.mfa_last_step = step
        return True
    normalised = code.strip().upper()
    for rc in db.scalars(select(RecoveryCode).where(RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None))):
        if secrets.compare_digest(rc.code_hash, crypto.token_hash(normalised)):
            rc.used_at = utcnow()
            return True
    return False


@router.post("/auth/login/mfa", response_model=TokenOut)
def login_mfa(body: MfaLoginIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    settings = request.app.state.settings
    try:
        claims = decode_mfa_token(body.mfa_token, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign-in expired; enter your password again")
    # Locked, so the same code (or recovery code) sent twice at once can't start two sessions.
    user = db.get(User, claims["sub"], with_for_update=True)
    if user is None or user.status != "active" or not user.mfa_enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign-in expired; enter your password again")
    ip = client_ip(request)
    _check_limit(db, user.email, ip)
    if not _second_factor_ok(db, request, user, body.code):
        db.rollback()
        _record(db, user.email, ip, succeeded=False)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid code")
    _record(db, user.email, ip, succeeded=True)
    return _new_session(db, request, user)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def logout(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)) -> None:
    auth.session.revoked_at = utcnow()
    db.commit()


@router.post("/auth/verify-email")
def verify_email(body: TokenIn, db: Session = Depends(get_db)) -> dict:
    t = _use_token(db, body.token, "verify_email")
    db.get(User, t.user_id).email_verified = True
    db.commit()
    return {"verified": True}


@router.post("/me/verify-email", status_code=status.HTTP_202_ACCEPTED)
def resend_verification(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if user.email_verified:
        return {"sent": False, "already_verified": True}
    last = db.scalar(select(func.max(UserToken.created_at))
                     .where(UserToken.user_id == user.id, UserToken.purpose == "verify_email"))
    if last and utcnow() - _aware(last) < timedelta(minutes=1):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "an email was just sent; wait a minute",
                            headers={"Retry-After": "60"})
    return {"sent": _send_verification(request, db, user)}


@router.post("/auth/password-reset", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(body: ResetRequestIn, request: Request, db: Session = Depends(get_db)) -> dict:
    """Always answers the same way, so it can't be used to find out which emails have accounts."""
    user = db.scalar(select(User).where(User.email == _normalise_email(body.email)))
    if user is not None and user.status == "active":
        recent = db.scalar(select(func.count()).select_from(UserToken).where(
            UserToken.user_id == user.id, UserToken.purpose == "reset_password",
            UserToken.created_at > utcnow() - timedelta(hours=1)))
        if recent < RESETS_PER_HOUR:
            raw = _issue(db, user, "reset_password", RESET_TTL)
            db.commit()
            link = _link(request, "/reset-password", raw)
            _send(request, user,
                  {"ar": "إعادة تعيين كلمة المرور في نوفارا", "en": "Reset your NOVARA password"},
                  {"ar": f"طُلبت إعادة تعيين كلمة المرور لحسابك. افتح الرابط خلال ساعة:\n{link}\n\n"
                         "إذا لم تطلب ذلك فتجاهل الرسالة، وكلمة مرورك الحالية تبقى كما هي.",
                   "en": f"Someone asked to reset your password. Open this link within an hour:\n{link}\n\n"
                         "If it wasn't you, ignore this message; your password stays the same."})
    return {"sent": True}


@router.post("/auth/password-reset/confirm")
def confirm_password_reset(body: ResetConfirmIn, db: Session = Depends(get_db)) -> dict:
    t = _use_token(db, body.token, "reset_password")
    user = db.get(User, t.user_id)
    user.password_hash = hash_password(body.password)
    user.email_verified = True  # they just proved they read this inbox
    for s in db.scalars(select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))):
        s.revoked_at = utcnow()  # every device signs in again with the new password
    db.commit()
    return {"reset": True}


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def change_password(body: PasswordChangeIn, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    if not verify_password(auth.user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "current password is wrong")
    auth.user.password_hash = hash_password(body.new_password)
    for s in db.scalars(select(UserSession).where(UserSession.user_id == auth.user.id,
                                                  UserSession.revoked_at.is_(None), UserSession.id != auth.session.id)):
        s.revoked_at = utcnow()  # other devices sign in again; this one stays signed in
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


# ---------------------------------------------------------------- two-factor sign-in


def _require_password(user: User, password: str) -> None:
    if not verify_password(user.password_hash, password):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "password is wrong")


def _new_recovery_codes(db: Session, user: User) -> list[str]:
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = totp.recovery_codes()
    db.add_all(RecoveryCode(user_id=user.id, code_hash=crypto.token_hash(c)) for c in codes)
    return codes


@router.post("/me/mfa/setup", response_model=MfaSetupOut)
def mfa_setup(body: PasswordIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Start two-factor setup: scan the secret into an authenticator app, then confirm with a code."""
    _require_password(user, body.password)
    if user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor sign-in is already on")
    secret = totp.new_secret()
    user.mfa_pending_secret = crypto.encrypt(request.app.state.settings, secret, context=user.id)
    db.commit()
    return MfaSetupOut(secret=secret, otpauth_uri=totp.provisioning_uri(secret, account=user.email))


@router.post("/me/mfa/enable", response_model=RecoveryCodesOut)
def mfa_enable(body: MfaCodeIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor sign-in is already on")
    if not user.mfa_pending_secret:
        raise HTTPException(status.HTTP_409_CONFLICT, "start the setup first")
    secret = crypto.decrypt(request.app.state.settings, user.mfa_pending_secret, context=user.id)
    step = totp.matching_step(secret, body.code)
    if step is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "the code doesn't match; check the time on your phone")
    user.mfa_secret, user.mfa_pending_secret, user.mfa_last_step, user.mfa_enabled = (
        user.mfa_pending_secret, None, step, True)
    codes = _new_recovery_codes(db, user)
    db.commit()
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/me/mfa/disable", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def mfa_disable(body: PasswordAndCodeIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_password(user, body.password)
    if not user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor sign-in is off")
    if not _second_factor_ok(db, request, user, body.code):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid code")
    user.mfa_enabled, user.mfa_secret, user.mfa_pending_secret, user.mfa_last_step = False, None, None, None
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    db.commit()


@router.post("/me/mfa/recovery-codes", response_model=RecoveryCodesOut)
def mfa_new_recovery_codes(body: PasswordAndCodeIn, request: Request, user: User = Depends(current_user),
                           db: Session = Depends(get_db)):
    """Replace all recovery codes (the old ones stop working)."""
    _require_password(user, body.password)
    if not user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor sign-in is off")
    if not _second_factor_ok(db, request, user, body.code):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid code")
    codes = _new_recovery_codes(db, user)
    db.commit()
    return RecoveryCodesOut(recovery_codes=codes)


@router.get("/me/mfa/recovery-codes/remaining")
def mfa_remaining(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return {"remaining": db.scalar(select(func.count()).select_from(RecoveryCode)
                                   .where(RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None)))}

