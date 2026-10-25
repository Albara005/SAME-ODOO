"""Closing the books month by month.

Closing a month moves the books' lock date to its last day, so nothing can be dated in it (or before)
any more; the months of a fiscal year are listed with their result and the drafts still dated in
them, so a close is checked before it happens. Only the latest closed month can be reopened, with
a reason, and never inside a fiscal year that has been closed into retained earnings. Every close
and reopen is kept (who, when, why, and what was still a draft).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novara.modules.accounting.models import PeriodClose
from novara.modules.accounting.service import fiscal_year_of, last_year_end, profit_and_loss
from novara.modules.invoicing.models import Invoice
from novara.modules.organizations.models import Organization
from novara.modules.purchasing.models import VendorBill


class PeriodError(ValueError):
    """Not allowed (HTTP 409/422 by the router)."""


def month_end(day: date) -> date:
    return (date(day.year + day.month // 12, day.month % 12 + 1, 1)) - timedelta(days=1)


def months(year_start: date) -> list[tuple[date, date]]:
    out, start = [], year_start
    for _ in range(12):
        end = month_end(start)
        out.append((start, end))
        start = end + timedelta(days=1)
    return out


def drafts_in(db: Session, start: date, end: date) -> dict[str, int]:
    count = lambda model, field: db.scalar(select(func.count()).select_from(model).where(  # noqa: E731
        model.status == "draft", field >= start, field <= end)) or 0
    return {"invoices": count(Invoice, Invoice.issue_date), "bills": count(VendorBill, VendorBill.bill_date)}


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    status: str              # open | partly_closed | closed
    income_minor: int
    expenses_minor: int
    drafts: dict[str, int]
    last: PeriodClose | None  # the latest close or reopen of this month


def periods(db: Session, org: Organization, year_start: date) -> list[Period]:
    locked = org.books_locked_until
    history = {}
    for row in db.scalars(select(PeriodClose).where(PeriodClose.period_end >= year_start)
                          .order_by(PeriodClose.created_at)):
        history[row.period_end] = row
    out = []
    for start, end in months(year_start):
        status = "open" if locked is None or locked < start else "closed" if locked >= end else "partly_closed"
        pnl = profit_and_loss(db, date_from=start, date_to=end)
        out.append(Period(start, end, status, pnl.total_income, pnl.total_expenses, drafts_in(db, start, end),
                          history.get(end)))
    return out


def close(db: Session, org: Organization, period_end: date, user_id: str, today: date | None = None) -> PeriodClose:
    today = today or date.today()
    if period_end != month_end(period_end):
        raise PeriodError("a month is closed on its last day")
    if period_end >= today:
        raise PeriodError("only a month that has ended can be closed")
    if org.books_locked_until is not None and org.books_locked_until >= period_end:
        raise PeriodError("that month is already closed")
    org.books_locked_until = period_end
    row = PeriodClose(period_end=period_end, action="closed", user_id=user_id,
                      drafts=drafts_in(db, date(period_end.year, period_end.month, 1), period_end))
    db.add(row)
    return row


def reopen(db: Session, org: Organization, period_end: date, reason: str, user_id: str) -> PeriodClose:
    if org.books_locked_until != period_end:
        raise PeriodError("only the latest closed month can be reopened")
    year_closed = last_year_end(db)
    if year_closed is not None and period_end <= year_closed:
        raise PeriodError("this month belongs to a fiscal year closed into retained earnings")
    if len(reason.strip()) < 5:
        raise PeriodError("say why the month is reopened")
    org.books_locked_until = date(period_end.year, period_end.month, 1) - timedelta(days=1)
    row = PeriodClose(period_end=period_end, action="reopened", reason=reason.strip(), user_id=user_id)
    db.add(row)
    return row


def fiscal_year_start(org: Organization, day: date) -> date:
    return fiscal_year_of(org.fiscal_year_start_month, day)[0]
