"""Password hashing (Argon2id) and signed access tokens."""

from __future__ import annotations

from datetime import datetime

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# Verified against when the email is unknown, so login timing doesn't reveal which emails exist.
DUMMY_HASH = _hasher.hash("novara-timing-equaliser")


def issue_token(*, secret: str, algorithm: str, user_id: str, session_id: str, expires_at: datetime) -> str:
    return jwt.encode({"sub": user_id, "sid": session_id, "exp": expires_at}, secret, algorithm=algorithm)


def decode_token(token: str, *, secret: str, algorithm: str) -> dict:
    return jwt.decode(token, secret, algorithms=[algorithm], options={"require": ["sub", "sid", "exp"]})
