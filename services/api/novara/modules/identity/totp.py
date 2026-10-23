"""Time-based one-time passwords (RFC 6238): 6 digits, 30-second steps, SHA-1, as authenticator apps expect."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # also accept the previous and next step (clock drift)


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def code_at(secret: str, when: float) -> str:
    return _code(secret, int(when // STEP_SECONDS))


def matching_step(secret: str, code: str, *, now: float | None = None, after: int | None = None) -> int | None:
    """The time step the code belongs to, or None. Steps at or before ``after`` (already used) never match."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    current = int((time.time() if now is None else now) // STEP_SECONDS)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if (after is None or step > after) and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str = "NOVARA") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")


def recovery_codes(n: int = 10) -> list[str]:
    """Readable single-use codes like 7KQ4-M2ZD."""
    alphabet = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)) for _ in range(n)]
