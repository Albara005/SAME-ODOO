"""Payroll API. Everything but your own payslips needs payroll.manage; paying a run also needs payments.write.

    GET/POST /orgs/{org}/payroll/contributions, PUT .../contributions/{id}
    GET      /orgs/{org}/payroll/employees                  everyone's monthly pay at a glance
    GET/PUT  /orgs/{org}/payroll/employees/{employee_id}    bank account, contributions and pay items
    GET/POST /orgs/{org}/payroll/runs                       a month's run (created as a draft)
    GET/DELETE /orgs/{org}/payroll/runs/{id}
    POST     .../runs/{id}/recalculate | post | pay | cancel
    GET      .../runs/{id}/bank-file.csv                    who to pay, to which IBAN, how much
    GET      .../runs/{id}/wps.sif                          the UAE wage protection system's salary file
    GET      /orgs/{org}/payroll/my-payslips                the signed-in member's own payslips
    GET/PUT  /orgs/{org}/payroll/settings                   which market's end-of-service rules apply, how overtime is paid
    POST     /orgs/{org}/payroll/end-of-service/preview     a leaver's settlement worked out, nothing saved
    GET/POST /orgs/{org}/payroll/end-of-service             settlements (created as drafts)
    GET/DELETE /orgs/{org}/payroll/end-of-service/{id}
    POST     .../end-of-service/{id}/post | pay | cancel
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara.db import new_id
from novara.deps import OrgContext, client_ip, get_db, org_context, require
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit
from novara.modules.hr.models import Employee
from novara.modules.hr.service import balances, status_of
from novara.modules.organizations.models import Organization
from novara.modules.payroll import eosb, wps
from novara.modules.payroll import service as svc
from novara.modules.payroll.models import Contribution, EmployeePay, EndOfService, PayItem, PayrollSettings, PayRun, Payslip
from novara.money import fmt_decimal, from_minor, to_minor

router = APIRouter(tags=["payroll"])


def iban_ok(iban: str) -> bool:
    """ISO 13616: two letters, two check digits, the account; mod 97 of the rearranged number is 1."""
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}", iban):
        return False
    digits = "".join(str(int(ch, 36)) for ch in iban[4:] + iban[:4])
    return int(digits) % 97 == 1


class ContributionIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    employee_rate: Decimal = Field(ge=0, le=100, max_digits=7, decimal_places=4)
    employer_rate: Decimal = Field(ge=0, le=100, max_digits=7, decimal_places=4)
    base_cap: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=3)
    active: bool = True


class ContributionOut(BaseModel):
    id: str
    name: str
    employee_rate: str
    employer_rate: str
    base_cap: str | None
    active: bool


class PayItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["earning", "deduction"]
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    contributory: bool = False
    end_of_service: bool = False
    basic: bool = False             # part of the basic wage overtime is worked out on


class PayItemOut(BaseModel):
    name: str
    kind: Literal["earning", "deduction"]
    amount: str
    contributory: bool
    end_of_service: bool
    basic: bool


class EmployeePayIn(BaseModel):
    iban: str = Field(default="", max_length=42)
    contribution_ids: list[str] = Field(default_factory=list, max_length=20)
    items: list[PayItemIn] = Field(default_factory=list, max_length=40)
    # UAE wage protection: the person's 14-digit number and their bank's 9-digit routing code (empty: not used).
    wps_person_id: str = Field(default="", pattern=r"^(\d{14})?$")
    wps_routing_code: str = Field(default="", pattern=r"^(\d{9})?$")

    @field_validator("iban")
    @classmethod
    def _iban(cls, v: str) -> str:
        v = v.replace(" ", "").upper()
        if v and not iban_ok(v):
            raise ValueError("that IBAN isn't valid (check the digits)")
        return v


class EmployeePayOut(BaseModel):
    employee_id: str
    employee_name: str
    currency: str
    iban: str
    wps_person_id: str
    wps_routing_code: str
    contribution_ids: list[str]
    items: list[PayItemOut]
    monthly_earnings: str
    monthly_deductions: str


class PaySummaryOut(BaseModel):
    employee_id: str
    employee_name: str
    job_title: str
    status: str
    monthly_earnings: str
    monthly_deductions: str
    has_iban: bool


class PayRunIn(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


class PayRunPaymentIn(BaseModel):
    payment_date: date
    method: Literal["bank", "cash"]


class PayRunCancelIn(BaseModel):
    cancel_date: date


class PayslipLineOut(BaseModel):
    name: str
    kind: Literal["earning", "deduction", "contribution"]
    amount: str
    employer_amount: str
    overtime_minutes: int | None = None     # the overtime line: the approved minutes it pays


class PayslipOut(BaseModel):
    id: str
    run_id: str
    month: str
    run_status: str
    currency: str
    employee_id: str
    employee_name: str
    iban: str
    days_in_period: int
    days_paid: str
    unpaid_leave_days: str
    lines: list[PayslipLineOut]
    gross: str
    deductions: str
    employee_contributions: str
    employer_contributions: str
    net: str
    service_days: int
    end_of_service_accrued: str     # the full end-of-service award as of the month's last day


class PayRunOut(BaseModel):
    id: str
    month: str
    period_start: date
    period_end: date
    status: Literal["draft", "posted", "paid", "cancelled"]
    currency: str
    gross: str
    deductions: str
    employee_contributions: str
    employer_contributions: str
    net: str
    end_of_service_change: str      # how much the end-of-service provision grows (negative: shrinks) with this run
    payslip_count: int
    journal_entry_id: str | None
    payment_entry_id: str | None
    reversal_entry_id: str | None
    payment_date: date | None
    posted_at: datetime | None
    created_at: datetime
    # A draft's warning: people employed that month with no pay set up get no payslip.
    without_pay: list[str] = []
    payslips: list[PayslipOut] = []


def _money(minor: int, currency: str) -> str:
    return str(from_minor(minor, currency))


def _currency(db: Session, ctx: OrgContext) -> str:
    return db.get(Organization, ctx.organization.id).default_currency


def _contribution_out(c: Contribution, currency: str) -> ContributionOut:
    return ContributionOut(id=c.id, name=c.name, employee_rate=fmt_decimal(c.employee_rate),
                           employer_rate=fmt_decimal(c.employer_rate),
                           base_cap=_money(c.base_cap_minor, currency) if c.base_cap_minor else None, active=c.active)


def _slip_out(s: Payslip, run: PayRun) -> PayslipOut:
    c = run.currency
    return PayslipOut(
        id=s.id, run_id=run.id, month=f"{run.period_start:%Y-%m}", run_status=run.status, currency=c,
        employee_id=s.employee_id, employee_name=s.employee_name, iban=s.iban, days_in_period=s.days_in_period,
        days_paid=fmt_decimal(s.days_paid), unpaid_leave_days=fmt_decimal(s.unpaid_leave_days),
        lines=[PayslipLineOut(name=l["name"], kind=l["kind"], amount=_money(l["amount_minor"], c),
                              employer_amount=_money(l["employer_minor"], c), overtime_minutes=l.get("minutes"))
               for l in s.lines],
        gross=_money(s.gross_minor, c), deductions=_money(s.deductions_minor, c),
        employee_contributions=_money(s.employee_contributions_minor, c),
        employer_contributions=_money(s.employer_contributions_minor, c), net=_money(s.net_minor, c),
        service_days=s.service_days, end_of_service_accrued=_money(s.eosb_liability_minor, c))


def _run_out(db: Session, run: PayRun, *, detail: bool = False) -> PayRunOut:
    c = run.currency
    slips = list(db.scalars(select(Payslip).where(Payslip.run_id == run.id).order_by(Payslip.employee_name)))
    return PayRunOut(
        id=run.id, month=f"{run.period_start:%Y-%m}", period_start=run.period_start, period_end=run.period_end,
        status=run.status, currency=c, gross=_money(run.gross_minor, c), deductions=_money(run.deductions_minor, c),
        employee_contributions=_money(run.employee_contributions_minor, c),
        employer_contributions=_money(run.employer_contributions_minor, c), net=_money(run.net_minor, c),
        end_of_service_change=_money(run.eosb_minor, c), payslip_count=len(slips), journal_entry_id=run.journal_entry_id, payment_entry_id=run.payment_entry_id,
        reversal_entry_id=run.reversal_entry_id, payment_date=run.payment_date, posted_at=run.posted_at,
        created_at=run.created_at,
        without_pay=svc.without_pay(db, run.period_start, run.period_end) if detail and run.status == "draft" else [],
        payslips=[_slip_out(s, run) for s in slips] if detail else [])


def _errors(fn):
    try:
        return fn()
    except svc.PayrollState as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except svc.PayrollError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


# ---------------------------------------------------------------- contributions


@router.get("/orgs/{org_id}/payroll/contributions", response_model=list[ContributionOut])
def list_contributions(ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)), db: Session = Depends(get_db)):
    currency = _currency(db, ctx)
    return [_contribution_out(c, currency) for c in db.scalars(select(Contribution).order_by(Contribution.name))]


def _save_contribution(db: Session, ctx: OrgContext, c: Contribution, body: ContributionIn, request: Request) -> ContributionOut:
    currency = _currency(db, ctx)
    c.name, c.employee_rate, c.employer_rate, c.active = body.name.strip(), body.employee_rate, body.employer_rate, body.active
    c.base_cap_minor = to_minor(body.base_cap, currency) if body.base_cap is not None else None
    audit.record(db, actor_user_id=ctx.user.id, action="payroll.contribution_saved", target_type="payroll_contribution",
                 target_id=c.id, details={"name": c.name, "employee_rate": str(c.employee_rate),
                                                "employer_rate": str(c.employer_rate), "active": c.active},
                 ip_address=client_ip(request))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "there is already a contribution with that name")
    return _contribution_out(c, currency)


@router.post("/orgs/{org_id}/payroll/contributions", response_model=ContributionOut, status_code=status.HTTP_201_CREATED)
def create_contribution(body: ContributionIn, request: Request, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
                        db: Session = Depends(get_db)):
    c = Contribution(id=new_id())
    db.add(c)
    return _save_contribution(db, ctx, c, body, request)


@router.put("/orgs/{org_id}/payroll/contributions/{contribution_id}", response_model=ContributionOut)
def update_contribution(contribution_id: str, body: ContributionIn, request: Request,
                        ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)), db: Session = Depends(get_db)):
    c = db.get(Contribution, contribution_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contribution not found")
    return _save_contribution(db, ctx, c, body, request)


# ---------------------------------------------------------------- employees' pay


def _employee(db: Session, employee_id: str) -> Employee:
    e = db.get(Employee, employee_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "employee not found")
    return e


def _sums(items: list[PayItem]) -> tuple[int, int]:
    return (sum(i.amount_minor for i in items if i.kind == "earning"),
            sum(i.amount_minor for i in items if i.kind == "deduction"))


def _pay_out(db: Session, e: Employee, currency: str) -> EmployeePayOut:
    pay = db.scalar(select(EmployeePay).where(EmployeePay.employee_id == e.id))
    items = svc.items_of(db, e.id)
    earn, ded = _sums(items)
    return EmployeePayOut(
        employee_id=e.id, employee_name=e.full_name, currency=currency, iban=pay.iban if pay else "",
        wps_person_id=pay.wps_person_id if pay else "", wps_routing_code=pay.wps_routing_code if pay else "",
        contribution_ids=list(pay.contribution_ids) if pay else [],
        items=[PayItemOut(name=i.name, kind=i.kind, amount=_money(i.amount_minor, currency), contributory=i.contributory,
                          end_of_service=i.end_of_service, basic=i.basic) for i in items],
        monthly_earnings=_money(earn, currency), monthly_deductions=_money(ded, currency))


@router.get("/orgs/{org_id}/payroll/employees", response_model=list[PaySummaryOut])
def pay_summary(ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)), db: Session = Depends(get_db)):
    currency = _currency(db, ctx)
    items: dict[str, list[PayItem]] = {}
    for i in db.scalars(select(PayItem)):
        items.setdefault(i.employee_id, []).append(i)
    ibans = {p.employee_id: bool(p.iban) for p in db.scalars(select(EmployeePay))}
    today = date.today()
    out = []
    for e in db.scalars(select(Employee).order_by(Employee.full_name)):
        earn, ded = _sums(items.get(e.id, []))
        out.append(PaySummaryOut(employee_id=e.id, employee_name=e.full_name, job_title=e.job_title,
                                 status=status_of(e, today), monthly_earnings=_money(earn, currency),
                                 monthly_deductions=_money(ded, currency), has_iban=ibans.get(e.id, False)))
    return out


@router.get("/orgs/{org_id}/payroll/employees/{employee_id}", response_model=EmployeePayOut)
def get_pay(employee_id: str, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)), db: Session = Depends(get_db)):
    return _pay_out(db, _employee(db, employee_id), _currency(db, ctx))


@router.put("/orgs/{org_id}/payroll/employees/{employee_id}", response_model=EmployeePayOut)
def set_pay(employee_id: str, body: EmployeePayIn, request: Request, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
            db: Session = Depends(get_db)):
    """Replace the employee's pay. Runs already posted keep the pay they were worked out with."""
    e = _employee(db, employee_id)
    currency = _currency(db, ctx)
    ids = list(dict.fromkeys(body.contribution_ids))
    if ids and len(list(db.scalars(select(Contribution.id).where(Contribution.id.in_(ids))))) != len(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown contribution")
    amounts = []
    for item in body.items:
        minor = to_minor(item.amount, currency)
        if from_minor(minor, currency) != item.amount:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{item.name}: more decimals than {currency} allows")
        amounts.append(minor)
    pay = db.scalar(select(EmployeePay).where(EmployeePay.employee_id == e.id))
    if pay is None:
        pay = EmployeePay(employee_id=e.id)
        db.add(pay)
    pay.iban, pay.contribution_ids = body.iban, ids
    pay.wps_person_id, pay.wps_routing_code = body.wps_person_id, body.wps_routing_code
    db.execute(delete(PayItem).where(PayItem.employee_id == e.id))
    for n, (item, minor) in enumerate(zip(body.items, amounts)):
        db.add(PayItem(employee_id=e.id, position=n, name=item.name.strip(), kind=item.kind, amount_minor=minor,
                       contributory=item.contributory and item.kind == "earning",
                       end_of_service=item.end_of_service and item.kind == "earning",
                       basic=item.basic and item.kind == "earning"))
    # Who changed whose pay, not the amounts: the audit log is readable more widely than payroll.
    audit.record(db, actor_user_id=ctx.user.id, action="payroll.pay_changed", target_type="employee", target_id=e.id,
                 details={"employee": e.full_name, "items": len(body.items)}, ip_address=client_ip(request))
    db.commit()
    return _pay_out(db, e, currency)


# ---------------------------------------------------------------- runs


def _run(db: Session, run_id: str, *, lock: bool = False) -> PayRun:
    run = svc.lock_run(db, run_id) if lock else db.get(PayRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pay run not found")
    return run


@router.get("/orgs/{org_id}/payroll/runs", response_model=list[PayRunOut])
def list_runs(_: OrgContext = Depends(require(P.PAYROLL_MANAGE)), db: Session = Depends(get_db)):
    return [_run_out(db, r) for r in db.scalars(select(PayRun).order_by(PayRun.period_start.desc(), PayRun.created_at.desc()))]


@router.post("/orgs/{org_id}/payroll/runs", response_model=PayRunOut, status_code=status.HTTP_201_CREATED)
def create_run(body: PayRunIn, request: Request, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
               db: Session = Depends(get_db)):
    start, end = _errors(lambda: svc.month_bounds(body.month))
    if db.scalar(select(PayRun.id).where(PayRun.period_start == start, PayRun.status != "cancelled")):
        raise HTTPException(status.HTTP_409_CONFLICT, f"there is already a pay run for {body.month}")
    run = PayRun(period_start=start, period_end=end, status="draft", currency=_currency(db, ctx), created_by=ctx.user.id)
    db.add(run)
    db.flush()
    _errors(lambda: svc.calculate(db, run))
    audit.record(db, actor_user_id=ctx.user.id, action="payroll.run_created", target_type="pay_run", target_id=run.id,
                 details={"month": body.month}, ip_address=client_ip(request))
    try:
        db.commit()
    except IntegrityError:  # two at once
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"there is already a pay run for {body.month}")
    return _run_out(db, run, detail=True)


@router.get("/orgs/{org_id}/payroll/runs/{run_id}", response_model=PayRunOut)
def get_run(run_id: str, _: OrgContext = Depends(require(P.PAYROLL_MANAGE)), db: Session = Depends(get_db)):
    return _run_out(db, _run(db, run_id), detail=True)


@router.delete("/orgs/{org_id}/payroll/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_run(run_id: str, request: Request, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
               db: Session = Depends(get_db)):
    run = _run(db, run_id, lock=True)
    if run.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "only a draft run can be deleted; cancel a posted one")
    month = f"{run.period_start:%Y-%m}"
    db.execute(delete(Payslip).where(Payslip.run_id == run.id))
    db.delete(run)
    audit.record(db, actor_user_id=ctx.user.id, action="payroll.run_deleted", target_type="pay_run", target_id=run_id,
                 details={"month": month}, ip_address=client_ip(request))
    db.commit()


@router.post("/orgs/{org_id}/payroll/runs/{run_id}/recalculate", response_model=PayRunOut)
def recalculate(run_id: str, _: OrgContext = Depends(require(P.PAYROLL_MANAGE)), db: Session = Depends(get_db)):
    run = _run(db, run_id, lock=True)
    _errors(lambda: svc.calculate(db, run))
    db.commit()
    return _run_out(db, run, detail=True)


@router.post("/orgs/{org_id}/payroll/runs/{run_id}/post", response_model=PayRunOut)
def post_run(run_id: str, request: Request, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
             db: Session = Depends(get_db)):
    run = _run(db, run_id, lock=True)
    if run.status == "draft":
        _errors(lambda: svc.calculate(db, run))      # with everyone's pay as it is now
    entry = _errors(lambda: svc.post_run(db, run, user_id=ctx.user.id))
    audit.record(db, actor_user_id=ctx.user.id, action="payroll.run_posted", target_type="pay_run", target_id=run.id,
                 details={"month": f"{run.period_start:%Y-%m}", "journal_entry": entry.number,
                          "net": _money(run.net_minor, run.currency)}, ip_address=client_ip(request))
    db.commit()
    return _run_out(db, run, detail=True)


@router.post("/orgs/{org_id}/payroll/runs/{run_id}/pay", response_model=PayRunOut)
def pay_run(run_id: str, body: PayRunPaymentIn, request: Request, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
            db: Session = Depends(get_db)):
    if not ctx.has(P.PAYMENTS_WRITE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission: {P.PAYMENTS_WRITE}")
    run = _run(db, run_id, lock=True)
    entry = _errors(lambda: svc.pay_run(db, run, payment_date=body.payment_date, method=body.method, user_id=ctx.user.id))
    audit.record(db, actor_user_id=ctx.user.id, action="payroll.run_paid", target_type="pay_run", target_id=run.id,
                 details={"month": f"{run.period_start:%Y-%m}", "journal_entry": entry.number if entry else None,
                          "method": body.method}, ip_address=client_ip(request))
    db.commit()
    return _run_out(db, run, detail=True)


@router.post("/orgs/{org_id}/payroll/runs/{run_id}/cancel", response_model=PayRunOut)
def cancel_run(run_id: str, body: PayRunCancelIn, request: Request, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
               db: Session = Depends(get_db)):
    run = _run(db, run_id, lock=True)
    reversal = _errors(lambda: svc.cancel_run(db, run, cancel_date=body.cancel_date, user_id=ctx.user.id))
    audit.record(db, actor_user_id=ctx.user.id, action="payroll.run_cancelled", target_type="pay_run", target_id=run.id,
                 details={"month": f"{run.period_start:%Y-%m}", "reversal_entry": reversal.number},
                 ip_address=client_ip(request))
    db.commit()
    return _run_out(db, run, detail=True)


@router.get("/orgs/{org_id}/payroll/runs/{run_id}/bank-file.csv", response_class=Response)
def bank_file(run_id: str, _: OrgContext = Depends(require(P.PAYROLL_MANAGE)), db: Session = Depends(get_db)):
    """The transfers to make: one row per payslip with net pay (UTF-8 with a BOM, so spreadsheets read Arabic)."""
    run = _run(db, run_id)
    if run.status not in ("posted", "paid"):
        raise HTTPException(status.HTTP_409_CONFLICT, "post the run first")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["employee", "iban", "amount", "currency", "reference"])
    for s in db.scalars(select(Payslip).where(Payslip.run_id == run.id, Payslip.net_minor > 0).order_by(Payslip.employee_name)):
        w.writerow([s.employee_name, s.iban, _money(s.net_minor, run.currency), run.currency,
                    f"Salary {run.period_start:%Y-%m}"])
    name = f"payroll-{run.period_start:%Y-%m}.csv"
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/orgs/{org_id}/payroll/runs/{run_id}/wps.sif", response_class=Response)
def wps_file(run_id: str, _: OrgContext = Depends(require(P.PAYROLL_MANAGE)), db: Session = Depends(get_db)):
    """The salary file for the UAE's wage protection system (payroll/wps.py): what each employee is paid, for the
    bank or exchange house to transfer."""
    run = _run(db, run_id)
    if run.status not in ("posted", "paid"):
        raise HTTPException(status.HTTP_409_CONFLICT, "post the run first")
    s = svc.settings_of(db)
    slips = list(db.scalars(select(Payslip).where(Payslip.run_id == run.id).order_by(Payslip.employee_name)))
    pays = {p.employee_id: p for p in db.scalars(select(EmployeePay).where(
        EmployeePay.employee_id.in_([x.employee_id for x in slips])))} if slips else {}
    try:
        f = wps.build(run, slips, pays, employer_id=s.wps_employer_id, bank_code=s.wps_bank_code, now=wps.now(),
                      reference=f"Salary {run.period_start:%Y-%m}")
    except wps.WpsError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return Response(f.content, media_type="text/plain; charset=us-ascii",
                    headers={"Content-Disposition": f'attachment; filename="{f.name}"'})


@router.get("/orgs/{org_id}/payroll/my-payslips", response_model=list[PayslipOut])
def my_payslips(ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    """The signed-in member's own payslips (when their account is linked to an employee record), once posted."""
    me = db.scalar(select(Employee).where(Employee.user_id == ctx.user.id))
    if me is None:
        return []
    rows = db.execute(select(Payslip, PayRun).join(PayRun, PayRun.id == Payslip.run_id)
                      .where(Payslip.employee_id == me.id, PayRun.status.in_(("posted", "paid")))
                      .order_by(PayRun.period_start.desc()))
    return [_slip_out(s, run) for s, run in rows]


# ---------------------------------------------------------------- end of service


class PayrollSettingsIn(BaseModel):
    """What is given changes; what is left out stays."""
    eosb_rule: Literal["saudi", "uae", "none"] | None = None
    overtime_rule: Literal["none", "saudi", "multiplier"] | None = None
    overtime_multiplier: Decimal | None = Field(default=None, ge=1, le=5, max_digits=4, decimal_places=2)
    wps_employer_id: str | None = Field(default=None, pattern=r"^(\d{13})?$")    # "" clears it
    wps_bank_code: str | None = Field(default=None, pattern=r"^(\d{9})?$")


class PayrollSettingsOut(BaseModel):
    eosb_rule: Literal["saudi", "uae", "none"]
    reasons: list[str]              # the reasons for leaving these rules know
    overtime_rule: Literal["none", "saudi", "multiplier"]
    overtime_multiplier: str
    wps_employer_id: str
    wps_bank_code: str


EosbReason = Literal["termination", "contract_end", "resignation", "resignation_full", "dismissal"]


class SettlementIn(BaseModel):
    employee_id: str
    last_day: date
    reason: EosbReason
    leave_days: Decimal = Field(default=Decimal(0), ge=0, le=999, max_digits=5, decimal_places=1)
    notes: str = Field(default="", max_length=2000)


class LeaveBalanceOut(BaseModel):
    leave_type: str
    remaining: str


class SettlementPreviewOut(BaseModel):
    employee_id: str
    employee_name: str
    currency: str
    rule: str
    reason: str
    hire_date: date
    last_day: date
    service_days: int
    service_years: str              # to two decimals, for reading; the award uses the exact days
    wage: str
    months: str                     # months of wage the full award is worth, to four decimals
    share: str                      # "1", "2/3", "1/3", "0"
    full_award: str
    award: str
    leave_days: str
    leave_pay: str
    provision: str
    total: str
    leave_balances: list[LeaveBalanceOut] = []


class SettlementOut(SettlementPreviewOut):
    id: str
    status: Literal["draft", "posted", "paid", "cancelled"]
    notes: str
    journal_entry_id: str | None
    payment_entry_id: str | None
    reversal_entry_id: str | None
    payment_date: date | None
    posted_at: datetime | None
    created_at: datetime


def _settings_out(s: PayrollSettings) -> PayrollSettingsOut:
    return PayrollSettingsOut(eosb_rule=s.eosb_rule, reasons=list(eosb.REASONS[s.eosb_rule]),
                              overtime_rule=s.overtime_rule or "none",
                              overtime_multiplier=fmt_decimal(s.overtime_multiplier or Decimal("1.5")),
                              wps_employer_id=s.wps_employer_id or "", wps_bank_code=s.wps_bank_code or "")


@router.get("/orgs/{org_id}/payroll/settings", response_model=PayrollSettingsOut)
def get_settings(_: OrgContext = Depends(require(P.PAYROLL_MANAGE)), db: Session = Depends(get_db)):
    return _settings_out(svc.settings_of(db))


@router.put("/orgs/{org_id}/payroll/settings", response_model=PayrollSettingsOut)
def put_settings(body: PayrollSettingsIn, request: Request, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
                 db: Session = Depends(get_db)):
    """Changing the rules applies to runs and settlements from now on; what is posted stays as it was."""
    current = db.scalar(select(PayrollSettings))
    if current is None:
        current = PayrollSettings(eosb_rule="none", overtime_rule="none", overtime_multiplier=Decimal("1.5"),
                                  wps_employer_id="", wps_bank_code="")
        db.add(current)
    changes = {}
    for key in ("eosb_rule", "overtime_rule", "overtime_multiplier", "wps_employer_id", "wps_bank_code"):
        value = getattr(body, key)
        if value is not None and value != getattr(current, key):
            before = getattr(current, key)
            changes[key] = [fmt_decimal(before) if isinstance(before, Decimal) else before,
                            fmt_decimal(value) if isinstance(value, Decimal) else value]
            setattr(current, key, value)
    if changes:
        audit.record(db, actor_user_id=ctx.user.id, action="payroll.settings_changed", target_type="payroll_settings",
                     target_id=ctx.organization.id, details=changes, ip_address=client_ip(request))
    try:
        db.commit()
    except IntegrityError:                          # two first saves at once: the other one won
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "the settings were just changed; try again")
    return _settings_out(current)


def _years(days: int) -> str:
    return str((Decimal(days) / eosb.YEAR).quantize(Decimal("0.01")))


def _months(fraction: str) -> str:
    num, _, den = fraction.partition("/")
    return str((Decimal(num) / Decimal(den or 1)).quantize(Decimal("0.0001")))


def _preview_out(db: Session, e: Employee, s: svc.Settlement, currency: str) -> SettlementPreviewOut:
    a = s.award
    return SettlementPreviewOut(
        employee_id=e.id, employee_name=e.full_name, currency=currency, rule=s.rule, reason=s.reason,
        hire_date=s.hire_date, last_day=s.last_day, service_days=a.days, service_years=_years(a.days),
        wage=_money(s.wage_minor, currency), months=_months(svc._fraction(a.months)), share=svc._fraction(a.share),
        full_award=_money(a.full_minor, currency), award=_money(a.award_minor, currency),
        leave_days=fmt_decimal(s.leave_days), leave_pay=_money(s.leave_pay_minor, currency),
        provision=_money(s.provision_minor, currency), total=_money(s.total_minor, currency),
        leave_balances=[LeaveBalanceOut(leave_type=b.leave_type.name, remaining=fmt_decimal(b.remaining))
                        for b in balances(db, e, s.last_day.year, s.last_day) if b.leave_type.paid and b.remaining is not None])


def _settlement_out(d: EndOfService) -> SettlementOut:
    c = d.currency
    return SettlementOut(
        id=d.id, status=d.status, employee_id=d.employee_id, employee_name=d.employee_name, currency=c, rule=d.rule,
        reason=d.reason, hire_date=d.hire_date, last_day=d.last_day, service_days=d.service_days,
        service_years=_years(d.service_days), wage=_money(d.wage_minor, c), months=_months(d.months), share=d.share,
        full_award=_money(d.full_award_minor, c), award=_money(d.award_minor, c), leave_days=fmt_decimal(d.leave_days),
        leave_pay=_money(d.leave_pay_minor, c), provision=_money(d.provision_minor, c), total=_money(d.total_minor, c),
        notes=d.notes, journal_entry_id=d.journal_entry_id, payment_entry_id=d.payment_entry_id,
        reversal_entry_id=d.reversal_entry_id, payment_date=d.payment_date, posted_at=d.posted_at, created_at=d.created_at)


def _work_out(db: Session, body: SettlementIn) -> tuple[Employee, svc.Settlement]:
    e = _employee(db, body.employee_id)
    return e, _errors(lambda: svc.work_out_settlement(db, e, last_day=body.last_day, reason=body.reason,
                                                      leave_days=body.leave_days))


@router.post("/orgs/{org_id}/payroll/end-of-service/preview", response_model=SettlementPreviewOut)
def preview_settlement(body: SettlementIn, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
                       db: Session = Depends(get_db)):
    e, s = _work_out(db, body)
    return _preview_out(db, e, s, _currency(db, ctx))


@router.get("/orgs/{org_id}/payroll/end-of-service", response_model=list[SettlementOut])
def list_settlements(_: OrgContext = Depends(require(P.PAYROLL_MANAGE)), db: Session = Depends(get_db)):
    return [_settlement_out(d) for d in db.scalars(select(EndOfService).order_by(EndOfService.last_day.desc(),
                                                                                  EndOfService.created_at.desc()))]


@router.post("/orgs/{org_id}/payroll/end-of-service", response_model=SettlementOut, status_code=status.HTTP_201_CREATED)
def create_settlement(body: SettlementIn, request: Request, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
                      db: Session = Depends(get_db)):
    e, s = _work_out(db, body)
    if db.scalar(select(EndOfService.id).where(EndOfService.employee_id == e.id, EndOfService.status != "cancelled")):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{e.full_name} already has an end-of-service settlement")
    doc = EndOfService(employee_id=e.id, employee_name=e.full_name, status="draft", currency=_currency(db, ctx),
                       notes=body.notes.strip(), created_by=ctx.user.id)
    svc.fill_settlement(doc, s)
    db.add(doc)
    audit.record(db, actor_user_id=ctx.user.id, action="payroll.end_of_service_created", target_type="employee",
                 target_id=e.id, details={"employee": e.full_name, "reason": body.reason,
                                          "last_day": body.last_day.isoformat()}, ip_address=client_ip(request))
    try:
        db.commit()
    except IntegrityError:                          # two at once
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"{e.full_name} already has an end-of-service settlement")
    return _settlement_out(doc)


def _settlement(db: Session, doc_id: str, *, lock: bool = False) -> EndOfService:
    doc = svc.lock_settlement(db, doc_id) if lock else db.get(EndOfService, doc_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "settlement not found")
    return doc


@router.get("/orgs/{org_id}/payroll/end-of-service/{doc_id}", response_model=SettlementOut)
def get_settlement(doc_id: str, _: OrgContext = Depends(require(P.PAYROLL_MANAGE)), db: Session = Depends(get_db)):
    return _settlement_out(_settlement(db, doc_id))


@router.delete("/orgs/{org_id}/payroll/end-of-service/{doc_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_settlement(doc_id: str, request: Request, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
                      db: Session = Depends(get_db)):
    doc = _settlement(db, doc_id, lock=True)
    if doc.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "only a draft settlement can be deleted; cancel a posted one")
    db.delete(doc)
    audit.record(db, actor_user_id=ctx.user.id, action="payroll.end_of_service_deleted", target_type="employee",
                 target_id=doc.employee_id, details={"employee": doc.employee_name}, ip_address=client_ip(request))
    db.commit()


@router.post("/orgs/{org_id}/payroll/end-of-service/{doc_id}/post", response_model=SettlementOut)
def post_settlement(doc_id: str, request: Request, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
                    db: Session = Depends(get_db)):
    doc = _settlement(db, doc_id, lock=True)
    entry = _errors(lambda: svc.post_settlement(db, doc, user_id=ctx.user.id))
    audit.record(db, actor_user_id=ctx.user.id, action="payroll.end_of_service_posted", target_type="employee",
                 target_id=doc.employee_id, details={"employee": doc.employee_name, "journal_entry": entry.number},
                 ip_address=client_ip(request))
    db.commit()
    return _settlement_out(doc)


@router.post("/orgs/{org_id}/payroll/end-of-service/{doc_id}/pay", response_model=SettlementOut)
def pay_settlement(doc_id: str, body: PayRunPaymentIn, request: Request, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
                   db: Session = Depends(get_db)):
    if not ctx.has(P.PAYMENTS_WRITE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission: {P.PAYMENTS_WRITE}")
    doc = _settlement(db, doc_id, lock=True)
    entry = _errors(lambda: svc.pay_settlement(db, doc, payment_date=body.payment_date, method=body.method,
                                               user_id=ctx.user.id))
    audit.record(db, actor_user_id=ctx.user.id, action="payroll.end_of_service_paid", target_type="employee",
                 target_id=doc.employee_id, details={"employee": doc.employee_name,
                                                     "journal_entry": entry.number if entry else None},
                 ip_address=client_ip(request))
    db.commit()
    return _settlement_out(doc)


@router.post("/orgs/{org_id}/payroll/end-of-service/{doc_id}/cancel", response_model=SettlementOut)
def cancel_settlement(doc_id: str, body: PayRunCancelIn, request: Request, ctx: OrgContext = Depends(require(P.PAYROLL_MANAGE)),
                      db: Session = Depends(get_db)):
    doc = _settlement(db, doc_id, lock=True)
    reversal = _errors(lambda: svc.cancel_settlement(db, doc, cancel_date=body.cancel_date, user_id=ctx.user.id))
    audit.record(db, actor_user_id=ctx.user.id, action="payroll.end_of_service_cancelled", target_type="employee",
                 target_id=doc.employee_id, details={"employee": doc.employee_name, "reversal_entry": reversal.number},
                 ip_address=client_ip(request))
    db.commit()
    return _settlement_out(doc)
