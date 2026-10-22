"""Business events that can trigger workflows.

Most events come from the audit trail: when an audited action listed in EVENT_TYPES
happens and at least one enabled workflow listens for it, an outbox event is written in
the same transaction. ``invoice.overdue`` comes from a daily scan instead.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara.automation.models import OutboxEvent, Workflow
from novara.modules.audit import service as audit
from novara.modules.audit.models import AuditLog

EVENT_TYPES: dict[str, str] = {
    "customer.created": "A customer was created",
    "invoice.created": "A draft invoice was created",
    "invoice.posted": "An invoice was posted (details: number, total)",
    "invoice.payment_registered": "A payment was recorded on an invoice (details: amount, method)",
    "invoice.cancelled": "A posted invoice was cancelled",
    "invoice.overdue": "An invoice passed its due date with money still owed (daily scan)",
    "sales_order.created": "A quotation was created",
    "sales_order.confirmed": "A quotation was confirmed as a sales order",
    "sales_order.delivered": "Goods were delivered for a sales order",
    "stock.received": "Stock was received",
    "stock.adjusted": "A stock count adjustment was made",
    "app.version_created": "An App Studio version was created (details: version, checks_passed)",
    "app.production_deployed": "An app was deployed to production",
    "agent.run_failed": "A custom agent run stopped without finishing",
}


def _listened(session: Session, event_type: str) -> bool:
    return session.scalar(select(Workflow.id).where(Workflow.trigger == event_type, Workflow.enabled.is_(True))
                          .limit(1)) is not None


def emit(session: Session, event_type: str, payload: dict, *, dedup_key: str | None = None) -> OutboxEvent | None:
    """Write an event to the outbox if a workflow listens for it. Duplicate dedup_keys are ignored."""
    if not _listened(session, event_type):
        return None
    if dedup_key and session.scalar(select(OutboxEvent.id).where(OutboxEvent.dedup_key == dedup_key)):
        return None
    ev = OutboxEvent(type=event_type, payload=payload, dedup_key=dedup_key)
    session.add(ev)
    return ev


@audit.subscribe
def _from_audit(session: Session, entry: AuditLog) -> None:
    if entry.action in EVENT_TYPES:
        emit(session, entry.action, {"target_type": entry.target_type, "target_id": entry.target_id,
                                     "actor_user_id": entry.actor_user_id, **(entry.details or {})})


def scan_overdue(session: Session, today: date) -> int:
    """Emit invoice.overdue once per invoice that is past due with money owed."""
    from novara.modules.invoicing.models import Invoice
    from novara.modules.partners.models import Customer
    from novara.money import from_minor

    if not _listened(session, "invoice.overdue"):
        return 0
    emitted = 0
    rows = session.execute(select(Invoice, Customer.name).join(Customer, Customer.id == Invoice.customer_id).where(
        Invoice.status.in_(("posted", "partially_paid")), Invoice.due_date < today))
    for inv, name in rows:
        payload = {"target_type": "invoice", "target_id": inv.id, "number": inv.number, "customer_id": inv.customer_id,
                   "customer_name": name, "due_date": inv.due_date.isoformat(), "days_overdue": (today - inv.due_date).days,
                   "amount_due": str(from_minor(inv.total_minor - inv.amount_paid_minor, inv.currency))}
        if emit(session, "invoice.overdue", payload, dedup_key=f"invoice.overdue:{inv.id}") is not None:
            emitted += 1
    try:
        session.flush()
    except IntegrityError:  # another worker emitted the same events concurrently
        session.rollback()
        return 0
    return emitted
