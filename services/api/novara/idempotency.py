"""Idempotency keys for operations that must not run twice when a client retries.

The client sends ``Idempotency-Key``; the first request stores the key with a
hash of its payload and the id of what it created. A retry with the same key
and payload gets the original result; the same key with a different payload
is refused.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped


class IdempotencyConflict(RuntimeError):
    pass


class IdempotencyRecord(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("organization_id", "scope", "key", name="uq_idempotency_scope_key"),)

    scope: Mapped[str] = mapped_column(String(60), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)


def request_hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def replayed(db: Session, scope: str, key: str | None, payload_hash: str) -> str | None:
    """Return the resource id created by an earlier request with this key, if any."""
    if not key:
        return None
    record = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.scope == scope, IdempotencyRecord.key == key))
    if record is None:
        return None
    if record.request_hash != payload_hash:
        raise IdempotencyConflict("idempotency key already used with a different request")
    return record.resource_id


def remember(db: Session, scope: str, key: str | None, payload_hash: str, resource_id: str) -> None:
    if key:
        db.add(IdempotencyRecord(scope=scope, key=key, request_hash=payload_hash, resource_id=resource_id))
