"""Working out a month's pay, and the journal entries for it.

For each employee employed during the month (between their start date and last day):

    days paid    = calendar days employed in the month - unpaid leave days
                   (approved leave of an unpaid type, by calendar day inside the month; a half day is 0.5)
    each earning x days paid / days in the month, rounded to the currency's minor unit (a whole month: exact)
    deductions   in full
    contributions: each covering contribution's rates x the contributory earnings (up to its monthly cap),
                   from the employee (taken from pay) and from the employer (a cost on top)
    net          = earnings - deductions - employee contributions (a run where that is negative is refused)

Posting a run (dated the month's last day):
    Dr Salaries and Wages (6100)          earnings
    Dr Employer Contributions (6110)      employer contributions
        Cr Salaries Payable (2250)            net
        Cr Contributions Payable (2260)       employee + employer contributions
        Cr Employee Advances (1350)           deductions (they pay back advances; reclassify others by hand)
Paying it:  Dr Salaries Payable, Cr Bank or Cash (the net). Contributions are paid with a journal entry when due.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from novara.db import utcnow
from novara.modules.accounting.models import JournalEntry
from novara.modules.accounting.service import LineSpec, post_entry, reverse_entry, system_account
from novara.modules.hr.models import Employee, LeaveRequest, LeaveType
from novara.modules.invoicing.service import PAYMENT_METHODS
from novara.modules.payroll.models import Contribution, EmployeePay, PayItem, PayRun, Payslip


class PayrollError(ValueError):
    """The request breaks a payroll rule (HTTP 422)."""


class PayrollState(RuntimeError):
    """The run is not in a state that allows this (HTTP 409)."""


def month_bounds(month: str) -> tuple[date, date]:
    try:
        year, mon = (int(x) for x in month.split("-"))
        start = date(year, mon, 1)
    except ValueError:
        raise PayrollError("give the month as YYYY-MM") from None
    return start, date(year, mon, calendar.monthrange(year, mon)[1])


def _round(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def employed(start: date, end: date):
    """Employees employed at some point between start and end."""
    return (or_(Employee.hire_date.is_(None), Employee.hire_date <= end),
            or_(Employee.end_date.is_(None), Employee.end_date >= start))


def unpaid_days(db: Session, employee_id: str, start: date, end: date) -> Decimal:
    rows = db.execute(select(LeaveRequest.start_date, LeaveRequest.end_date, LeaveRequest.half_day)
                      .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
                      .where(LeaveRequest.employee_id == employee_id, LeaveRequest.status == "approved",
                             LeaveType.paid.is_(False), LeaveRequest.start_date <= end, LeaveRequest.end_date >= start))
    total = Decimal(0)
    for first, last, half in rows:
        total += Decimal("0.5") if half else Decimal((min(last, end) - max(first, start)).days + 1)
    return total


@dataclass
class Worked:
    lines: list[dict]
    days_in_period: int
    days_paid: Decimal
    unpaid: Decimal
    gross: int
    deductions: int
    employee_contributions: int
    employer_contributions: int

    @property
    def net(self) -> int:
        return self.gross - self.deductions - self.employee_contributions


def work_out(db: Session, employee: Employee, items: list[PayItem], contributions: list[Contribution],
             start: date, end: date) -> Worked:
    first = max(start, employee.hire_date) if employee.hire_date else start
    last = min(end, employee.end_date) if employee.end_date else end
    days = (end - start).days + 1
    unpaid = unpaid_days(db, employee.id, first, last)
    paid = max(Decimal((last - first).days + 1) - unpaid, Decimal(0))
    lines: list[dict] = []
    gross = deductions = base = 0
    for item in items:
        if item.kind == "earning":
            amount = item.amount_minor if paid == days else _round(Decimal(item.amount_minor) * paid / days)
            gross += amount
            base += amount if item.contributory else 0
        else:
            amount = item.amount_minor
            deductions += amount
        lines.append({"name": item.name, "kind": item.kind, "amount_minor": amount, "employer_minor": 0})
    own = employer = 0
    for c in contributions:
        covered = min(base, c.base_cap_minor) if c.base_cap_minor else base
        e_share = _round(Decimal(covered) * c.employee_rate / 100)
        r_share = _round(Decimal(covered) * c.employer_rate / 100)
        own += e_share
        employer += r_share
        lines.append({"name": c.name, "kind": "contribution", "amount_minor": e_share, "employer_minor": r_share})
    return Worked(lines=lines, days_in_period=days, days_paid=paid, unpaid=unpaid, gross=gross, deductions=deductions,
                  employee_contributions=own, employer_contributions=employer)


def items_of(db: Session, employee_id: str) -> list[PayItem]:
    return list(db.scalars(select(PayItem).where(PayItem.employee_id == employee_id).order_by(PayItem.position)))


def covering(db: Session, pay: EmployeePay | None) -> list[Contribution]:
    if pay is None or not pay.contribution_ids:
        return []
    return list(db.scalars(select(Contribution).where(Contribution.id.in_(pay.contribution_ids),
                                                      Contribution.active.is_(True)).order_by(Contribution.name)))


def without_pay(db: Session, start: date, end: date) -> list[str]:
    """Names of people employed in the month with no earnings set up (they get no payslip)."""
    has_items = select(PayItem.employee_id).where(PayItem.kind == "earning")
    return list(db.scalars(select(Employee.full_name).where(*employed(start, end), Employee.id.not_in(has_items))
                           .order_by(Employee.full_name)))


def calculate(db: Session, run: PayRun) -> None:
    """(Re)build a draft run's payslips from everyone's current pay."""
    if run.status != "draft":
        raise PayrollState("only a draft run can be recalculated")
    db.execute(delete(Payslip).where(Payslip.run_id == run.id))
    totals = dict(gross=0, deductions=0, own=0, employer=0, net=0)
    employees = db.scalars(select(Employee).where(*employed(run.period_start, run.period_end)).order_by(Employee.full_name))
    for e in employees:
        items = items_of(db, e.id)
        if not any(i.kind == "earning" for i in items):
            continue
        pay = db.scalar(select(EmployeePay).where(EmployeePay.employee_id == e.id))
        w = work_out(db, e, items, covering(db, pay), run.period_start, run.period_end)
        if w.net < 0:
            raise PayrollError(f"{e.full_name}'s deductions and contributions are more than their pay this month")
        db.add(Payslip(run_id=run.id, employee_id=e.id, employee_name=e.full_name, iban=pay.iban if pay else "",
                       days_in_period=w.days_in_period, days_paid=w.days_paid, unpaid_leave_days=w.unpaid,
                       lines=w.lines, gross_minor=w.gross, deductions_minor=w.deductions,
                       employee_contributions_minor=w.employee_contributions,
                       employer_contributions_minor=w.employer_contributions, net_minor=w.net))
        totals["gross"] += w.gross
        totals["deductions"] += w.deductions
        totals["own"] += w.employee_contributions
        totals["employer"] += w.employer_contributions
        totals["net"] += w.net
    run.gross_minor, run.deductions_minor = totals["gross"], totals["deductions"]
    run.employee_contributions_minor, run.employer_contributions_minor = totals["own"], totals["employer"]
    run.net_minor = totals["net"]
    db.flush()


def lock_run(db: Session, run_id: str) -> PayRun | None:
    return db.scalar(select(PayRun).where(PayRun.id == run_id).with_for_update())


def _label(run: PayRun) -> str:
    return f"Payroll {run.period_start:%Y-%m}"


def post_run(db: Session, run: PayRun, *, user_id: str) -> JournalEntry:
    if run.status != "draft":
        raise PayrollState(f"only a draft run can be posted (status: {run.status})")
    if run.gross_minor <= 0:
        raise PayrollError("nobody is paid in this run: set up pay for the employees first")
    contributions = run.employee_contributions_minor + run.employer_contributions_minor
    label = _label(run)
    entry = post_entry(db, entry_date=run.period_end, currency=run.currency, memo=label, source_type="pay_run",
                       source_id=run.id, created_by=user_id, lines=[
                           LineSpec(system_account(db, "salaries"), debit_minor=run.gross_minor, label=label),
                           LineSpec(system_account(db, "employer_contributions"), debit_minor=run.employer_contributions_minor,
                                    label=f"{label}: employer contributions"),
                           LineSpec(system_account(db, "salaries_payable"), credit_minor=run.net_minor, label=f"{label}: net pay"),
                           LineSpec(system_account(db, "contributions_payable"), credit_minor=contributions,
                                    label=f"{label}: contributions"),
                           LineSpec(system_account(db, "employee_advances"), credit_minor=run.deductions_minor,
                                    label=f"{label}: deductions"),
                       ])
    run.status = "posted"
    run.journal_entry_id = entry.id
    run.posted_at = utcnow()
    run.posted_by = user_id
    return entry


def pay_run(db: Session, run: PayRun, *, payment_date: date, method: str, user_id: str) -> JournalEntry | None:
    if run.status != "posted":
        raise PayrollState(f"only a posted run can be paid (status: {run.status})")
    if method not in PAYMENT_METHODS:
        raise PayrollError(f"unknown payment method: {method}")
    if payment_date < run.period_start:
        raise PayrollError("the payment can't be dated before the month it pays")
    label = _label(run)
    entry = post_entry(db, entry_date=payment_date, currency=run.currency, memo=f"{label}: paid", source_type="pay_run",
                       source_id=run.id, created_by=user_id, lines=[
                           LineSpec(system_account(db, "salaries_payable"), debit_minor=run.net_minor, label=f"{label}: net pay"),
                           LineSpec(system_account(db, PAYMENT_METHODS[method]), credit_minor=run.net_minor, label=label),
                       ]) if run.net_minor else None
    run.status = "paid"
    run.payment_entry_id = entry.id if entry else None
    run.payment_date = payment_date
    return entry


def cancel_run(db: Session, run: PayRun, *, cancel_date: date, user_id: str) -> JournalEntry:
    if run.status != "posted":
        raise PayrollState("only a posted run that hasn't been paid can be cancelled (a draft is deleted)")
    if cancel_date < run.period_end:
        raise PayrollError("the cancellation can't be dated before the month's last day")
    original = db.get(JournalEntry, run.journal_entry_id)
    reversal = reverse_entry(db, original, entry_date=cancel_date, memo=f"Cancellation of {_label(run)}", created_by=user_id)
    run.status = "cancelled"
    run.reversal_entry_id = reversal.id
    return reversal
