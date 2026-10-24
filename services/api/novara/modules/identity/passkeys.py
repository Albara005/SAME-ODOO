"""Passkeys (WebAuthn): sign in with the device's lock instead of a password.

A passkey is created from the account page (after the password is checked again) and used from
the sign-in page. Every ceremony gets a fresh challenge kept on the server and deleted on first
use, so a captured response can't be replayed; the device must verify the person (PIN,
fingerprint, face). Because a passkey is something you have plus that check, signing in with
one counts as two-step sign-in. The relying party is the web app's own host
(NOVARA_APP_BASE_URL); only the public key is stored.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options, generate_registration_options, options_to_json, verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria, AuthenticatorTransport, PublicKeyCredentialDescriptor, ResidentKeyRequirement,
    UserVerificationRequirement,
)

from novara.db import utcnow
from novara.deps import client_ip, current_user, get_db
from novara.modules.identity import router as identity
from novara.modules.identity.models import Passkey, PasskeyChallenge, User

router = APIRouter(tags=["identity"])

CHALLENGE_TTL = timedelta(minutes=5)
MAX_PASSKEYS = 10


class RegisterOptionsIn(BaseModel):
    password: str


class CeremonyOut(BaseModel):
    challenge_id: str
    options: dict  # pass to navigator.credentials.create / .get (base64url strings for binary fields)


class PasskeyIn(BaseModel):
    challenge_id: str
    name: str = Field(default="", max_length=80)
    credential: dict


class PasskeyRename(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class PasskeyOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    last_used_at: datetime | None


class PasskeySignInIn(BaseModel):
    challenge_id: str
    credential: dict


def _relying_party(request: Request) -> tuple[str, str]:
    """(rp_id, origin) of the web app: the host people sign in on."""
    base = urlsplit(request.app.state.settings.app_base_url)
    return base.hostname or "localhost", f"{base.scheme}://{base.netloc}"


def _new_challenge(db: Session, purpose: str, challenge: bytes, user_id: str | None = None) -> str:
    now = utcnow()
    db.execute(delete(PasskeyChallenge).where(PasskeyChallenge.expires_at < now))  # keep the table small
    row = PasskeyChallenge(user_id=user_id, purpose=purpose, challenge=bytes_to_base64url(challenge),
                           expires_at=now + CHALLENGE_TTL)
    db.add(row)
    db.commit()
    return row.id


def _take_challenge(db: Session, challenge_id: str, purpose: str, user_id: str | None = None) -> bytes:
    """The challenge's bytes, deleting it so it can't be used twice (row lock against a race)."""
    row = db.scalar(select(PasskeyChallenge).where(PasskeyChallenge.id == challenge_id).with_for_update())
    if row is None or row.purpose != purpose or (user_id is not None and row.user_id != user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this passkey request expired; try again")
    expired = identity._aware(row.expires_at) < utcnow()
    challenge = base64url_to_bytes(row.challenge)
    db.delete(row)
    db.flush()
    if expired:
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this passkey request expired; try again")
    return challenge


def _out(p: Passkey) -> PasskeyOut:
    return PasskeyOut(id=p.id, name=p.name, created_at=p.created_at, last_used_at=p.last_used_at)


# ---------------------------------------------------------------- managing your passkeys


@router.get("/me/passkeys", response_model=list[PasskeyOut])
def list_passkeys(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_out(p) for p in db.scalars(select(Passkey).where(Passkey.user_id == user.id).order_by(Passkey.created_at))]


@router.post("/me/passkeys/options", response_model=CeremonyOut)
def passkey_registration_options(body: RegisterOptionsIn, request: Request, user: User = Depends(current_user),
                                 db: Session = Depends(get_db)):
    """Start adding a passkey (the password is checked again, as for two-step sign-in)."""
    identity._require_password(user, body.password)
    existing = list(db.scalars(select(Passkey).where(Passkey.user_id == user.id)))
    if len(existing) >= MAX_PASSKEYS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"at most {MAX_PASSKEYS} passkeys; remove one first")
    rp_id, _ = _relying_party(request)
    options = generate_registration_options(
        rp_id=rp_id, rp_name="NOVARA", user_id=user.id.encode(), user_name=user.email,
        user_display_name=user.full_name or user.email,
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,
                                                               user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id)) for p in existing])
    challenge_id = _new_challenge(db, "register", options.challenge, user.id)
    return CeremonyOut(challenge_id=challenge_id, options=json.loads(options_to_json(options)))


@router.post("/me/passkeys", response_model=PasskeyOut, status_code=status.HTTP_201_CREATED)
def add_passkey(body: PasskeyIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    challenge = _take_challenge(db, body.challenge_id, "register", user.id)
    rp_id, origin = _relying_party(request)
    try:
        verified = verify_registration_response(credential=body.credential, expected_challenge=challenge,
                                                expected_rp_id=rp_id, expected_origin=origin,
                                                require_user_verification=True)
    except (InvalidRegistrationResponse, ValueError, KeyError, TypeError) as e:
        db.commit()  # the challenge is spent either way
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"the passkey couldn't be checked: {e}")
    credential_id = bytes_to_base64url(verified.credential_id)
    if db.scalar(select(Passkey.id).where(Passkey.credential_id == credential_id)):
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "this passkey is already registered")
    transports = [t for t in body.credential.get("response", {}).get("transports", [])
                  if t in {x.value for x in AuthenticatorTransport}]
    p = Passkey(user_id=user.id, credential_id=credential_id, public_key=bytes_to_base64url(verified.credential_public_key),
                sign_count=verified.sign_count, transports=transports,
                name=body.name.strip() or ("مفتاح مرور" if user.locale == "ar" else "Passkey"))
    db.add(p)
    db.commit()
    identity._send(request, user,
                   {"ar": "أُضيف مفتاح مرور إلى حسابك", "en": "A passkey was added to your account"},
                   {"ar": f"أُضيف مفتاح المرور «{p.name}» إلى حسابك في NOVARA. إن لم تكن أنت، فاحذفه وغيّر كلمة المرور.",
                    "en": f"The passkey \"{p.name}\" was added to your NOVARA account. If this wasn't you, remove it and "
                          "change your password."})
    return _out(p)


@router.patch("/me/passkeys/{passkey_id}", response_model=PasskeyOut)
def rename_passkey(passkey_id: str, body: PasskeyRename, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Passkey, passkey_id)
    if p is None or p.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "passkey not found")
    p.name = body.name.strip()
    db.commit()
    return _out(p)


@router.delete("/me/passkeys/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def remove_passkey(passkey_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    p = db.get(Passkey, passkey_id)
    if p is None or p.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "passkey not found")
    db.delete(p)
    db.commit()


# ---------------------------------------------------------------- signing in


@router.post("/auth/passkey/options", response_model=CeremonyOut)
def passkey_signin_options(request: Request, db: Session = Depends(get_db)):
    """Start signing in with a passkey: the device offers the passkeys it holds for this site."""
    rp_id, _ = _relying_party(request)
    options = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED)
    challenge_id = _new_challenge(db, "signin", options.challenge)
    return CeremonyOut(challenge_id=challenge_id, options=json.loads(options_to_json(options)))


@router.post("/auth/passkey", response_model=identity.TokenOut)
def passkey_signin(body: PasskeySignInIn, request: Request, db: Session = Depends(get_db)) -> identity.TokenOut:
    """Finish signing in with a passkey. It counts as two-step sign-in, so no app code is asked for."""
    ip = client_ip(request)
    challenge = _take_challenge(db, body.challenge_id, "signin")
    raw_id = str(body.credential.get("id") or body.credential.get("rawId") or "")
    p = db.scalar(select(Passkey).where(Passkey.credential_id == raw_id).with_for_update()) if raw_id else None
    user = db.get(User, p.user_id) if p else None
    if p is None or user is None or user.status != "active":
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "this passkey isn't registered here")
    identity._check_limit(db, user.email, ip)
    rp_id, origin = _relying_party(request)
    try:
        verified = verify_authentication_response(
            credential=body.credential, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origin,
            credential_public_key=base64url_to_bytes(p.public_key), credential_current_sign_count=p.sign_count,
            require_user_verification=True)
    except (InvalidAuthenticationResponse, ValueError, KeyError, TypeError):
        db.commit()
        identity._record(db, user.email, ip, succeeded=False)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "the passkey couldn't be verified")
    p.sign_count, p.last_used_at = verified.new_sign_count, utcnow()
    db.commit()
    identity._record(db, user.email, ip, succeeded=True)
    return identity._new_session(db, request, user)
