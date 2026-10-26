"""Project figures and invoicing billable hours."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from novara.automation.models import Task
from novara.modules.invoicing.models import Invoice
from novara.modules.projects.models import Project, TimeEntry


class ProjectError(ValueError):
    """Not possible for this project (HTTP 422/409 by the router)."""


def unbilled(stmt):
    """Billable entries not on a live invoice: never invoiced, or their invoice was cancelled."""
    return (stmt.outerjoin(Invoice, Invoice.id == TimeEntry.invoice_id)
            .where(TimeEntry.billable.is_(True), or_(TimeEntry.invoice_id.is_(None), Invoice.status == "cancelled")))


@dataclass(frozen=True)
class Figures:
    hours: Decimal
    billable_hours: Decimal
    unbilled_hours: Decimal

    def budget_used(self, project: Project) -> Decimal | None:
        if not project.budget_hours:
            return None
        return (self.hours * 100 / project.budget_hours).quantize(Decimal("0.1"))


def figures(db: Session, project_ids: list[str]) -> dict[str, Figures]:
    hours: dict[str, Decimal] = defaultdict(Decimal)
    billable: dict[str, Decimal] = defaultdict(Decimal)
    open_: dict[str, Decimal] = defaultdict(Decimal)
    for pid, h, b in db.execute(select(TimeEntry.project_id, TimeEntry.hours, TimeEntry.billable)
                                .where(TimeEntry.project_id.in_(project_ids))):
        hours[pid] += h
        if b:
            billable[pid] += h
    for pid, h in db.execute(unbilled(select(TimeEntry.project_id, TimeEntry.hours)
                                      .where(TimeEntry.project_id.in_(project_ids)))):
        open_[pid] += h
    return {pid: Figures(hours[pid], billable[pid], open_[pid]) for pid in project_ids}


def billed(entry: TimeEntry, db: Session) -> bool:
    if entry.invoice_id is None:
        return False
    inv = db.get(Invoice, entry.invoice_id)
    return inv is not None and inv.status != "cancelled"


def lines_to_invoice(db: Session, project: Project, up_to: date) -> tuple[list[TimeEntry], list[dict]]:
    """Unbilled billable hours up to a date, one invoice line per task (and one for work outside tasks)."""
    if project.customer_id is None:
        raise ProjectError("an internal project (without a customer) isn't invoiced")
    if project.hourly_rate is None:
        raise ProjectError("set the project's hourly rate before invoicing its hours")
    entries = list(db.scalars(unbilled(select(TimeEntry).where(TimeEntry.project_id == project.id,
                                                              TimeEntry.entry_date <= up_to))
                              .order_by(TimeEntry.entry_date)))
    if not entries:
        raise ProjectError("no unbilled hours up to that date")
    per_task: dict[str | None, Decimal] = defaultdict(Decimal)
    for e in entries:
        per_task[e.task_id] += e.hours
    titles = {t.id: t.title for t in db.scalars(select(Task).where(Task.id.in_([k for k in per_task if k])))}
    lines = []
    for task_id, hours in sorted(per_task.items(), key=lambda kv: (kv[0] is None, titles.get(kv[0] or "", ""))):
        label = titles.get(task_id or "", "") if task_id else ""
        lines.append({"description": f"{project.name}: {label}" if label else project.name,
                      "quantity": hours, "unit_price": project.hourly_rate, "tax_id": project.tax_id})
    return entries, lines


def mark_billed(db: Session, entries: list[TimeEntry], invoice_id: str) -> None:
    for e in entries:
        e.invoice_id = invoice_id


def release_invoice(db: Session, invoice_id: str) -> None:
    """A draft invoice is being deleted: its hours become billable again."""
    db.execute(update(TimeEntry).where(TimeEntry.invoice_id == invoice_id).values(invoice_id=None))


def has_hours(db: Session, invoice_id: str) -> bool:
    return db.scalar(select(TimeEntry.id).where(TimeEntry.invoice_id == invoice_id).limit(1)) is not None
