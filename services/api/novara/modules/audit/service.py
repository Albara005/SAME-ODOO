from __future__ import annotations

from typing import Callable

from sqlalchemy import event
from sqlalchemy.orm import Session

from novara.modules.audit.models import AuditLog

# Called with (session, entry) for every audit entry, in the same transaction.
# Workflow automation subscribes here to turn audited actions into events.
_subscribers: list[Callable[[Session, AuditLog], None]] = []


def subscribe(fn: Callable[[Session, AuditLog], None]) -> Callable[[Session, AuditLog], None]:
    _subscribers.append(fn)
    return fn


class AuditLogImmutable(RuntimeError):
    pass


def record(
    session: Session,
    *,
    actor_user_id: str | None,
    action: str,
    target_type: str = "",
    target_id: str = "",
    details: dict | None = None,
    ip_address: str = "",
) -> AuditLog:
    """Add an audit entry to the current transaction, so it commits or rolls back with the change."""
    entry = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
        ip_address=ip_address,
    )
    session.add(entry)
    for fn in _subscribers:
        fn(session, entry)
    return entry


@event.listens_for(Session, "before_flush")
def _audit_is_append_only(session: Session, _ctx, _instances) -> None:
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, AuditLog) and (obj in session.deleted or session.is_modified(obj)):
            raise AuditLogImmutable("audit log entries cannot be modified or deleted")
