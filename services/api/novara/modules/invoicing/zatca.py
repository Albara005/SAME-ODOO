"""ZATCA (Saudi e-invoicing) QR code, phase 1: the five TLV fields of a simplified tax invoice.

The value is base64 of tag-length-value records (one byte tag, one byte length, UTF-8 value):
1 seller name, 2 seller VAT number, 3 time stamp, 4 total with VAT, 5 VAT total.
Phase 2 (signed UBL XML, cryptographic stamp, clearance through the Fatoora platform) is not done.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone


def _field(tag: int, value: str) -> bytes:
    raw = value.encode()
    if len(raw) > 255:  # one length byte: cut long names on a character boundary
        raw = raw[:255].decode(errors="ignore").encode()
    return bytes([tag, len(raw)]) + raw


def qr_payload(*, seller: str, vat_number: str, issued_at: datetime, total: str, vat_total: str) -> str:
    stamp = (issued_at if issued_at.tzinfo else issued_at.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    data = b"".join([
        _field(1, seller), _field(2, vat_number), _field(3, stamp.strftime("%Y-%m-%dT%H:%M:%SZ")),
        _field(4, total), _field(5, vat_total),
    ])
    return base64.b64encode(data).decode()


def decode(payload: str) -> dict[int, str]:
    """For tests and support: the fields of a QR payload."""
    raw, out, i = base64.b64decode(payload), {}, 0
    while i < len(raw):
        tag, length = raw[i], raw[i + 1]
        out[tag] = raw[i + 2:i + 2 + length].decode()
        i += 2 + length
    return out
