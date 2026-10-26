"""Working out a month's pay, and the journal entries for it.

For each employee employed during the month (between their start date and last day):

    days paid    = calendar days employed in the month - unpaid leave days
                   (approved leave of an unpaid type, by calendar day inside the month; a half day is 0.5)
    each earning x days paid / days in the month, rounded to the currency's minor unit (a whole month: exact)
    overtime     the hours approved for the month (hr/attendance.py) x the hourly overtime rate, an earning that
                 doesn't count for contributions or end of service:
                   saudi       (wage + half the basic wage) / 30 / the working day's hours (Labor Law, article 107:
                               the hourly wage plus 50% of the hourly basic wage)
                   multiplier  basic wage x the multiplier / 30 / the working day's hours
                 where the wage is every earning in full and the basic wage the earnings marked basic
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
        Dr End-of-Service Benefits (6120) / Cr End-of-Service Provision (2270)
                                              how much the provision grows this month (the other way round
                                              when it shrinks, e.g. a lower wage)
Paying it:  Dr Salaries Payable, Cr Bank or Cash (the net). Contributions are paid with a journal entry when due.

The provision holds, for each employee, the full award they would get if their service ended on the run's last day
(payroll/eosb.py). Settling someone's end of service (dated their last day) releases what the provision held for them:
    Dr End-of-Service Provision           what it held
    Dr/Cr End-of-Service Benefits         the award minus that (a resignation's smaller award gives some back)
    Dr Salaries and Wages                 unused leave paid out
        Cr Salaries Payable                   award + leave pay, until it is paid
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from novara.db import utcnow
from novara.modules.accounting.models import JournalEntry
from novara.modules.accounting.service import LineSpec, post_entry, reverse_entry, system_account
from novara.modules.hr.models import DEFAULT_DAILY_MINUTES, Employee, HrPolicy, LeaveRequest, LeaveType, OvertimeApproval
from novara.modules.invoicing.service import PAYMENT_METHODS
from novara.modules.payroll import eosb
from novara.modules.payroll.models import Contribution, EmployeePay, EndOfService, PayItem, PayrollSettings, PayRun, Payslip


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


@dataclass
class OvertimeRule:
    rule: str                   # none | saudi | multiplier
    multiplier: Decimal
    daily_minutes: int


def overtime_rule(db: Session) -> OvertimeRule:
    s = settings_of(db)
    policy = db.scalar(select(HrPolicy))
    return OvertimeRule(s.overtime_rule, s.overtime_multiplier, policy.daily_minutes if policy else DEFAULT_DAILY_MINUTES)


def overtime_pay(rule: OvertimeRule, items: list[PayItem], minutes: int, who: str) -> int:
    """What the approved overtime minutes are paid (see the module's docstring)."""
    if rule.rule == "none":
        raise PayrollError(f"overtime is approved for {who}, but the payroll settings don't say how overtime is paid")
    wage = sum(i.amount_minor for i in items if i.kind == "earning")
    basic = sum(i.amount_minor for i in items if i.kind == "earning" and i.basic)
    if basic <= 0:
        raise PayrollError(f"none of {who}'s earnings are marked as basic salary: overtime is worked out on it")
    per = Fraction(30 * rule.daily_minutes)
    if rule.rule == "saudi":
        exact = Fraction(minutes) * (Fraction(wage) + Fraction(basic, 2)) / per
    else:
        exact = Fraction(minutes) * Fraction(basic) * Fraction(str(rule.multiplier)) / per
    return int(exact + Fraction(1, 2))          # half up (always positive)


def work_out(db: Session, employee: Employee, items: list[PayItem], contributions: list[Contribution],
             start: date, end: date, overtime: tuple[OvertimeRule, int] | None = None) -> Worked:
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
    if overtime and overtime[1]:
        rule, minutes = overtime
        amount = overtime_pay(rule, items, minutes, employee.full_name)
        gross += amount
        lines.append({"name": "Overtime", "kind": "earning", "amount_minor": amount, "employer_minor": 0,
                      "minutes": minutes})
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


def settings_of(db: Session) -> PayrollSettings:
    return db.scalar(select(PayrollSettings)) or PayrollSettings(eosb_rule="none", overtime_rule="none",
                                                                 overtime_multiplier=Decimal("1.5"))


def eosb_wage(items: list[PayItem]) -> int:
    """The monthly wage end-of-service benefits are worked out on (earnings marked for it, in full)."""
    return sum(i.amount_minor for i in items if i.kind == "earning" and i.end_of_service)


def settled(db: Session, employee_id: str) -> bool:
    return db.scalar(select(EndOfService.id).where(EndOfService.employee_id == employee_id,
                                                   EndOfService.status.in_(("posted", "paid")))) is not None


def provision_of(db: Session, employee_id: str, before: date | None = None) -> int:
    """What the end-of-service provision holds for an employee: the award on their latest posted payslip (none once
    their end of service is settled)."""
    if settled(db, employee_id):
        return 0
    q = (select(Payslip.eosb_liability_minor).join(PayRun, PayRun.id == Payslip.run_id)
         .where(Payslip.employee_id == employee_id, PayRun.status.in_(("posted", "paid"))))
    if before is not None:
        q = q.where(PayRun.period_start < before)
    return db.scalar(q.order_by(PayRun.period_start.desc()).limit(1)) or 0


def calculate(db: Session, run: PayRun) -> None:
    """(Re)build a draft run's payslips from everyone's current pay."""
    if run.status != "draft":
        raise PayrollState("only a draft run can be recalculated")
    db.execute(delete(Payslip).where(Payslip.run_id == run.id))
    rule = settings_of(db).eosb_rule
    ot_rule = overtime_rule(db)
    approved = {a.employee_id: a.minutes for a in db.scalars(select(OvertimeApproval)
                                                             .where(OvertimeApproval.month == run.period_start))}
    totals = dict(gross=0, deductions=0, own=0, employer=0, net=0, eosb=0)
    employees = db.scalars(select(Employee).where(*employed(run.period_start, run.period_end)).order_by(Employee.full_name))
    for e in employees:
        items = items_of(db, e.id)
        if not any(i.kind == "earning" for i in items):
            continue
        pay = db.scalar(select(EmployeePay).where(EmployeePay.employee_id == e.id))
        w = work_out(db, e, items, covering(db, pay), run.period_start, run.period_end,
                     overtime=(ot_rule, approved.get(e.id, 0)))
        if w.net < 0:
            raise PayrollError(f"{e.full_name}'s deductions and contributions are more than their pay this month")
        last = min(run.period_end, e.end_date) if e.end_date else run.period_end
        before = provision_of(db, e.id, before=run.period_start)
        if settled(db, e.id):                       # already settled: nothing more is provisioned
            days, owed = (eosb.service_days(e.hire_date, last) if e.hire_date else 0), before
        else:
            days, owed = eosb.liability(rule, eosb_wage(items), e.hire_date, last)
        db.add(Payslip(run_id=run.id, employee_id=e.id, employee_name=e.full_name, iban=pay.iban if pay else "",
                       days_in_period=w.days_in_period, days_paid=w.days_paid, unpaid_leave_days=w.unpaid,
                       lines=w.lines, gross_minor=w.gross, deductions_minor=w.deductions,
                       employee_contributions_minor=w.employee_contributions,
                       employer_contributions_minor=w.employer_contributions, net_minor=w.net,
                       service_days=days, eosb_liability_minor=owed))
        totals["eosb"] += owed - before
        totals["gross"] += w.gross
        totals["deductions"] += w.deductions
        totals["own"] += w.employee_contributions
        totals["employer"] += w.employer_contributions
        totals["net"] += w.net
    run.gross_minor, run.deductions_minor = totals["gross"], totals["deductions"]
    run.employee_contributions_minor, run.employer_contributions_minor = totals["own"], totals["employer"]
    run.net_minor = totals["net"]
    run.eosb_minor = totals["eosb"]
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
                           *_eosb_lines(db, run.eosb_minor, f"{label}: end-of-service provision"),
                       ])
    run.status = "posted"
    run.journal_entry_id = entry.id
    run.posted_at = utcnow()
    run.posted_by = user_id
    return entry


def _eosb_lines(db: Session, change: int, label: str) -> list[LineSpec]:
    if change == 0:
        return []
    expense, provision = system_account(db, "eosb_expense"), system_account(db, "eosb_provision")
    if change > 0:
        return [LineSpec(expense, debit_minor=change, label=label), LineSpec(provision, credit_minor=change, label=label)]
    return [LineSpec(provision, debit_minor=-change, label=label), LineSpec(expense, credit_minor=-change, label=label)]


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


# ---------------------------------------------------------------- end of service


@dataclass
class Settlement:
    rule: str
    reason: str
    hire_date: date
    last_day: date
    wage_minor: int
    award: eosb.Award
    leave_days: Decimal
    leave_pay_minor: int
    provision_minor: int

    @property
    def total_minor(self) -> int:
        return self.award.award_minor + self.leave_pay_minor


def work_out_settlement(db: Session, employee: Employee, *, last_day: date, reason: str, leave_days: Decimal) -> Settlement:
    rule = settings_of(db).eosb_rule
    if rule == "none":
        raise PayrollError("choose the end-of-service rules in the payroll settings first")
    if employee.hire_date is None:
        raise PayrollError(f"{employee.full_name} has no hire date: add it to their record first")
    if employee.end_date and employee.end_date != last_day:
        raise PayrollError(f"{employee.full_name}'s last day on their record is {employee.end_date.isoformat()}")
    if leave_days < 0 or leave_days * 2 != int(leave_days * 2):
        raise PayrollError("unused leave is counted in whole or half days")
    wage = eosb_wage(items_of(db, employee.id))
    if wage <= 0:
        raise PayrollError(f"none of {employee.full_name}'s earnings count for end of service: mark them in their pay")
    try:
        a = eosb.award(rule, wage, employee.hire_date, last_day, reason)
    except eosb.EndOfServiceError as e:
        raise PayrollError(str(e)) from None
    return Settlement(rule=rule, reason=reason, hire_date=employee.hire_date, last_day=last_day, wage_minor=wage, award=a,
                      leave_days=leave_days, leave_pay_minor=eosb.leave_pay(wage, Fraction(str(leave_days))),
                      provision_minor=provision_of(db, employee.id))


def _fraction(x: Fraction) -> str:
    return str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}"


def fill_settlement(doc: EndOfService, s: Settlement) -> None:
    doc.rule, doc.reason, doc.hire_date, doc.last_day = s.rule, s.reason, s.hire_date, s.last_day
    doc.service_days, doc.wage_minor = s.award.days, s.wage_minor
    doc.months, doc.share = _fraction(s.award.months), _fraction(s.award.share)
    doc.full_award_minor, doc.award_minor = s.award.full_minor, s.award.award_minor
    doc.leave_days, doc.leave_pay_minor, doc.provision_minor = s.leave_days, s.leave_pay_minor, s.provision_minor


def lock_settlement(db: Session, doc_id: str) -> EndOfService | None:
    return db.scalar(select(EndOfService).where(EndOfService.id == doc_id).with_for_update())


def _settlement_label(doc: EndOfService) -> str:
    return f"End of service: {doc.employee_name}"


def post_settlement(db: Session, doc: EndOfService, *, user_id: str) -> JournalEntry:
    """Work the settlement out again with the provision as it is now, post it on the last day, and record the last
    day on the employee's record."""
    if doc.status != "draft":
        raise PayrollState(f"only a draft settlement can be posted (status: {doc.status})")
    employee = db.get(Employee, doc.employee_id)
    s = work_out_settlement(db, employee, last_day=doc.last_day, reason=doc.reason, leave_days=doc.leave_days)
    fill_settlement(doc, s)
    if doc.total_minor == 0 and doc.provision_minor == 0:
        raise PayrollError("nothing is owed and nothing was provisioned: there is nothing to post")
    label = _settlement_label(doc)
    expense = doc.award_minor - doc.provision_minor
    lines = [
        LineSpec(system_account(db, "eosb_provision"), debit_minor=doc.provision_minor, label=f"{label}: provision released"),
        LineSpec(system_account(db, "eosb_expense"), debit_minor=max(expense, 0), credit_minor=max(-expense, 0),
                 label=f"{label}: award beyond the provision" if expense >= 0 else f"{label}: provision not needed"),
        LineSpec(system_account(db, "salaries"), debit_minor=doc.leave_pay_minor, label=f"{label}: unused leave"),
        LineSpec(system_account(db, "salaries_payable"), credit_minor=doc.total_minor, label=f"{label}: owed"),
    ]
    entry = post_entry(db, entry_date=doc.last_day, currency=doc.currency, memo=label, source_type="end_of_service",
                       source_id=doc.id, created_by=user_id, lines=lines)
    doc.status, doc.journal_entry_id, doc.posted_at, doc.posted_by = "posted", entry.id, utcnow(), user_id
    if employee.end_date is None:
        employee.end_date = doc.last_day
    return entry


def pay_settlement(db: Session, doc: EndOfService, *, payment_date: date, method: str, user_id: str) -> JournalEntry | None:
    if doc.status != "posted":
        raise PayrollState(f"only a posted settlement can be paid (status: {doc.status})")
    if method not in PAYMENT_METHODS:
        raise PayrollError(f"unknown payment method: {method}")
    if payment_date < doc.last_day:
        raise PayrollError("the payment can't be dated before the last day")
    label = _settlement_label(doc)
    entry = post_entry(db, entry_date=payment_date, currency=doc.currency, memo=f"{label}: paid", source_type="end_of_service",
                       source_id=doc.id, created_by=user_id, lines=[
                           LineSpec(system_account(db, "salaries_payable"), debit_minor=doc.total_minor, label=label),
                           LineSpec(system_account(db, PAYMENT_METHODS[method]), credit_minor=doc.total_minor, label=label),
                       ]) if doc.total_minor else None
    doc.status, doc.payment_entry_id, doc.payment_date = "paid", entry.id if entry else None, payment_date
    return entry


def cancel_settlement(db: Session, doc: EndOfService, *, cancel_date: date, user_id: str) -> JournalEntry:
    if doc.status != "posted":
        raise PayrollState("only a posted settlement that hasn't been paid can be cancelled (a draft is deleted)")
    if cancel_date < doc.last_day:
        raise PayrollError("the cancellation can't be dated before the last day")
    original = db.get(JournalEntry, doc.journal_entry_id)
    reversal = reverse_entry(db, original, entry_date=cancel_date, memo=f"Cancellation of {_settlement_label(doc)}",
                             created_by=user_id)
    doc.status, doc.reversal_entry_id = "cancelled", reversal.id
    return reversal
