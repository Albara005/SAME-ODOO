"""Business events that can trigger workflows.

Most events come from the audit trail: when an audited action listed in EVENT_TYPES
happens and at least one enabled workflow listens for it, an outbox event is written in
the same transaction. ``invoice.overdue`` comes from a daily scan instead.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
    "schedule": "On a schedule: every hour, day, week or month at a set time (details: scheduled_for, date)",
    "webhook.received": "Another system called this workflow's webhook address (details: the JSON it sent, under data)",
    "credit_note.created": "A credit note was issued against an invoice (details: number, invoice, total)",
    "purchase_order.confirmed": "A purchase order was confirmed",
    "purchase_order.received": "Goods were received for a purchase order",
    "bill.posted": "A vendor bill was posted (details: number, total)",
    "web_order.received": "An order request arrived from a published site (details: number, site, customer_name, total)",
    "leave.requested": "Someone asked for leave (details: employee, leave_type, start, end, days)",
    "leave.approved": "A leave request was approved (details: employee, leave_type, start, end, days)",
    "booking.requested": "A published site asked for a booking (details: number, service, starts_at, name, site)",
    "booking.confirmed": "A booking was confirmed (details: number, service, starts_at, name)",
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


def last_slot(schedule: dict, now: datetime) -> datetime | None:
    """The latest scheduled time at or before ``now`` (UTC), in the schedule's own time zone."""
    tz = ZoneInfo(schedule["timezone"])
    if schedule["every"] == "cron":
        from novara.automation import cron

        return cron.last_fire(cron.parse(schedule["cron"]), tz, now)
    local = now.astimezone(tz)
    if schedule["every"] == "hour":
        slot = local.replace(minute=schedule["minute"], second=0, microsecond=0)
        if slot > local:
            slot -= timedelta(hours=1)
        return slot.astimezone(timezone.utc)
    hour, minute = (int(x) for x in schedule["time"].split(":"))
    slot = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule["every"] == "day":
        if slot > local:
            slot -= timedelta(days=1)
    elif schedule["every"] == "week":
        slot -= timedelta(days=(local.weekday() - schedule["weekday"]) % 7)
        if slot > local:
            slot -= timedelta(days=7)
    else:  # month; days 1-28 exist in every month
        slot = slot.replace(day=schedule["day"])
        if slot > local:
            first = slot.replace(day=1) - timedelta(days=1)
            slot = first.replace(day=schedule["day"])
    return slot.astimezone(timezone.utc)


def scan_schedules(session: Session, now: datetime) -> int:
    """Emit one ``schedule`` event per workflow per scheduled time. If the worker was down, only the
    latest missed time fires (once), never a backlog; times before the workflow existed never fire."""
    emitted = 0
    for wf in session.scalars(select(Workflow).where(Workflow.trigger == "schedule", Workflow.enabled.is_(True))):
        if not wf.schedule:
            continue
        slot = last_slot(wf.schedule, now)
        created = wf.created_at if wf.created_at.tzinfo else wf.created_at.replace(tzinfo=timezone.utc)
        if slot is None or slot < created:
            continue
        payload = {"target_type": "workflow", "target_id": wf.id, "workflow_id": wf.id,
                   "scheduled_for": slot.isoformat(),
                   "date": slot.astimezone(ZoneInfo(wf.schedule["timezone"])).date().isoformat()}
        if emit(session, "schedule", payload, dedup_key=f"schedule:{wf.id}:{slot.isoformat()}") is not None:
            emitted += 1
    try:
        session.flush()
    except IntegrityError:  # another worker emitted the same slot concurrently
        session.rollback()
        return 0
    return emitted


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
                   "amount_due": str(from_minor(inv.amount_due_minor, inv.currency))}
        if emit(session, "invoice.overdue", payload, dedup_key=f"invoice.overdue:{inv.id}") is not None:
            emitted += 1
    try:
        session.flush()
    except IntegrityError:  # another worker emitted the same events concurrently
        session.rollback()
        return 0
    return emitted
