"""Proof of work for requests from published sites: a CAPTCHA without puzzles or third parties.

Before an order or booking request, the site's page asks for a challenge:

    GET /api/v1/public/challenge     (X-Novara-Key header)  ->  {"challenge": "...", "difficulty": 16}

and looks for a number n such that SHA-256("<challenge>:<n>") starts with `difficulty` zero bits
(about 65,000 tries at 16 bits: a second or so in a phone's browser, see /public/proof.js). The
request then carries {"proof": {"challenge": ..., "nonce": "n"}}. One person barely notices; a script
sending thousands of requests pays for every one of them.

The challenge is signed (HMAC) with the key it was issued for, its difficulty and an expiry, so
nothing is stored until it is spent; spending records it until it expires, so each works once.
Addresses that keep sending requests get harder challenges.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara.db import utcnow
from novara.studio.models import AppDataKey, SpentProof

BASE_BITS = 16
MAX_BITS = 20
TTL_SECONDS = 300
RECENT = timedelta(minutes=10)


class ProofError(ValueError):
    pass


def _secret(signing_key: str) -> bytes:
    return hmac.new(signing_key.encode(), b"novara proof-of-work v1", hashlib.sha256).digest()


def _sign(signing_key: str, key_id: str, salt: str, bits: int, expires: int) -> str:
    message = f"{key_id}.{salt}.{bits}.{expires}".encode()
    return hmac.new(_secret(signing_key), message, hashlib.sha256).hexdigest()[:32]


def leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte:
            return bits + 8 - byte.bit_length()
        bits += 8
    return bits


def difficulty_for(db: Session, ip: str) -> int:
    """Harder for an address that has sent requests recently: one more bit (twice the work) for every two."""
    recent = db.scalar(select(func.count()).select_from(SpentProof).where(
        SpentProof.ip_address == ip, SpentProof.created_at >= utcnow() - RECENT)) or 0
    return min(MAX_BITS, BASE_BITS + recent // 2)


def issue(db: Session, signing_key: str, key: AppDataKey, ip: str, now: float | None = None) -> dict:
    bits = difficulty_for(db, ip)
    salt = secrets.token_hex(12)
    expires = int(now if now is not None else time.time()) + TTL_SECONDS
    challenge = f"{salt}.{bits}.{expires}.{_sign(signing_key, key.id, salt, bits, expires)}"
    return {"algorithm": "SHA-256", "challenge": challenge, "difficulty": bits, "expires_in": TTL_SECONDS}


def spend(db: Session, signing_key: str, key: AppDataKey, proof: object, ip: str, now: float | None = None) -> None:
    """Check a proof for this key and record it as used. Raises ProofError with the reason."""
    if not isinstance(proof, dict) or not isinstance(proof.get("challenge"), str) or \
            not isinstance(proof.get("nonce"), (str, int)):
        raise ProofError("this request needs a proof-of-work: get a challenge from /api/v1/public/challenge")
    challenge, nonce = proof["challenge"], str(proof["nonce"])
    parts = challenge.split(".")
    if len(parts) != 4 or not parts[1].isdigit() or not parts[2].isdigit() or len(nonce) > 20:
        raise ProofError("the proof-of-work isn't valid; get a new challenge")
    salt, bits, expires, signature = parts[0], int(parts[1]), int(parts[2]), parts[3]
    if not hmac.compare_digest(signature, _sign(signing_key, key.id, salt, bits, expires)):
        raise ProofError("the proof-of-work isn't valid; get a new challenge")
    if expires < (now if now is not None else time.time()):
        raise ProofError("the proof-of-work challenge expired; get a new one")
    if bits < BASE_BITS or leading_zero_bits(hashlib.sha256(f"{challenge}:{nonce}".encode()).digest()) < bits:
        raise ProofError("the proof-of-work isn't solved")
    db.execute(delete(SpentProof).where(SpentProof.expires_at < utcnow()))
    try:
        with db.begin_nested():
            db.add(SpentProof(salt=salt, ip_address=ip,
                              expires_at=datetime.fromtimestamp(expires, tz=timezone.utc)))
    except IntegrityError:
        raise ProofError("this proof-of-work was already used; get a new challenge")
