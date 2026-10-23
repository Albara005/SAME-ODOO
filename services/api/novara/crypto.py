"""Encryption for secrets kept in the database (AES-256-GCM)."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX = "v1:"


def _key(settings) -> bytes:
    material = settings.encryption_key or f"novara-encryption:{settings.jwt_secret}"
    return hashlib.sha256(material.encode()).digest()


def encrypt(settings, plaintext: str, *, context: str) -> str:
    """``context`` (e.g. the user id) is bound to the ciphertext, so it can't be moved to another row."""
    nonce = os.urandom(12)
    data = AESGCM(_key(settings)).encrypt(nonce, plaintext.encode(), context.encode())
    return _PREFIX + base64.urlsafe_b64encode(nonce + data).decode()


def decrypt(settings, token: str, *, context: str) -> str:
    if not token.startswith(_PREFIX):
        raise ValueError("unknown ciphertext format")
    raw = base64.urlsafe_b64decode(token[len(_PREFIX):])
    return AESGCM(_key(settings)).decrypt(raw[:12], raw[12:], context.encode()).decode()


def token_hash(token: str) -> str:
    """One-time tokens and recovery codes are stored only as their SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()
