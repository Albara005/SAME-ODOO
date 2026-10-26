"""Attendance and overtime.

People check in when they start work and out when they stop (attendance.self); HR enters or corrects entries for
anyone. An entry counts on the day it starts, in the organization's time zone (the HR policy), and is at most a day
long; one person's entries never overlap.

For a month, per day with entries that are checked out:

    worked    = the entries' minutes
    overtime  = what is worked beyond the policy's working day (daily_minutes), or all of it on a weekend day or
                public holiday

HR or the person's department manager (never the person) approves overtime for a month, up to what attendance shows,
and the month's pay run pays what is approved (payroll/service.py). Once that run is posted, the approval and that
person's entries for the month stand; cancelling the run frees them.

    GET    /orgs/{org}/hr/attendance/me                     your month: entries, open check-in, overtime
    POST   /orgs/{org}/hr/attendance/check-in | check-out   (attendance.self)
    GET    /orgs/{org}/hr/attendance                        a person's entries for a month (HR, their manager, them)
    POST   /orgs/{org}/hr/attendance                        an entry for anyone (hr.manage)
    PATCH/DELETE /orgs/{org}/hr/attendance/{id}             (hr.manage)
    GET    /orgs/{org}/hr/overtime?month=YYYY-MM            everyone's month: worked, overtime, approved
    PUT/DELETE /orgs/{org}/hr/overtime/{employee_id}/{month}   approve overtime for payment, or withdraw it
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara.automation.models import Notification
from novara.deps import OrgContext, client_ip, get_db, require, require_any
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit
from novara.modules.hr.models import (DEFAULT_DAILY_MINUTES, DEFAULT_TIMEZONE, DEFAULT_WEEKEND, AttendanceEntry,
                                      Department, Employee, Holiday, HrPolicy, OvertimeApproval)
from novara.modules.hr.service import can_decide, lock_employee
from novara.modules.payroll.models import PayrollSettings, PayRun

router = APIRouter(tags=["hr"])

MAX_ENTRY = timedelta(hours=24)
MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


class AttendanceError(ValueError):
    """The entry or approval breaks a rule (HTTP 422)."""


class AttendanceState(RuntimeError):
    """Not possible as things stand (HTTP 409)."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def utc(dt: datetime) -> datetime:
    """SQLite hands datetimes back without a zone; they are UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise AttendanceError(f"unknown time zone '{name}'") from None


def policy(db: Session) -> HrPolicy:
    """The policy to read by (an organization without one gets the defaults; reading never writes)."""
    return db.scalar(select(HrPolicy)) or HrPolicy(weekend=list(DEFAULT_WEEKEND), timezone=DEFAULT_TIMEZONE,
                                                   daily_minutes=DEFAULT_DAILY_MINUTES)


def local_day(p: HrPolicy, dt: datetime) -> date:
    return utc(dt).astimezone(zone(p.timezone)).date()


def month_bounds(month: str) -> tuple[date, date]:
    year, mon = (int(x) for x in month.split("-"))
    return date(year, mon, 1), date(year, mon, calendar.monthrange(year, mon)[1])


def minutes_of(e: AttendanceEntry) -> int | None:
    return None if e.check_out is None else int((utc(e.check_out) - utc(e.check_in)).total_seconds() // 60)


def hm(minutes: int) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}"


# ---------------------------------------------------------------- the month


@dataclass
class Month:
    days_present: int = 0
    worked: int = 0
    overtime: int = 0


def months(db: Session, start: date, end: date, employee_ids: list[str]) -> dict[str, Month]:
    """Worked and overtime minutes for each person over the days start to end (checked-out entries only)."""
    if not employee_ids:
        return {}
    p = policy(db)
    off = set(p.weekend)
    holidays = set(db.scalars(select(Holiday.day).where(Holiday.day >= start, Holiday.day <= end)))
    per_day: dict[str, dict[date, int]] = {}
    for e in db.scalars(select(AttendanceEntry).where(AttendanceEntry.employee_id.in_(employee_ids),
                                                      AttendanceEntry.day >= start, AttendanceEntry.day <= end,
                                                      AttendanceEntry.check_out.is_not(None))):
        days = per_day.setdefault(e.employee_id, {})
        days[e.day] = days.get(e.day, 0) + (minutes_of(e) or 0)
    out = {}
    for emp in employee_ids:
        days = per_day.get(emp, {})
        out[emp] = Month(days_present=len(days), worked=sum(days.values()), overtime=sum(
            m if (d.weekday() in off or d in holidays) else max(m - p.daily_minutes, 0) for d, m in days.items()))
    return out


def run_posted(db: Session, month_start: date) -> bool:
    return db.scalar(select(PayRun.id).where(PayRun.period_start == month_start,
                                             PayRun.status.in_(("posted", "paid")))) is not None


def approval(db: Session, employee_id: str, month_start: date) -> OvertimeApproval | None:
    return db.scalar(select(OvertimeApproval).where(OvertimeApproval.employee_id == employee_id,
                                                    OvertimeApproval.month == month_start))


def paid(db: Session, employee_id: str, day: date) -> bool:
    """The person's overtime for the day's month was approved and its pay run is posted: the month stands."""
    first = day.replace(day=1)
    return approval(db, employee_id, first) is not None and run_posted(db, first)


def open_entry(db: Session, employee_id: str) -> AttendanceEntry | None:
    return db.scalar(select(AttendanceEntry).where(AttendanceEntry.employee_id == employee_id,
                                                   AttendanceEntry.check_out.is_(None)))


def _employed(employee: Employee, day: date) -> None:
    if (employee.hire_date and day < employee.hire_date) or (employee.end_date and day > employee.end_date):
        raise AttendanceError(f"{employee.full_name} isn't employed on {day.isoformat()}")


# ---------------------------------------------------------------- checking in and out


def check_in(db: Session, employee: Employee, *, user_id: str, note: str = "") -> AttendanceEntry:
    t = now()
    day = local_day(policy(db), t)
    _employed(employee, day)
    current = open_entry(db, employee.id)
    if current is not None:
        if t - utc(current.check_in) > MAX_ENTRY:
            raise AttendanceState(f"your check-in on {current.day.isoformat()} was never checked out: ask HR to put in "
                                  "when you left")
        raise AttendanceState("you're already checked in")
    entry = AttendanceEntry(employee_id=employee.id, day=day, check_in=t, source="self", note=note, created_by=user_id)
    db.add(entry)
    db.flush()
    return entry


def check_out(db: Session, employee: Employee) -> AttendanceEntry:
    t = now()
    current = open_entry(db, employee.id)
    if current is None:
        raise AttendanceState("you aren't checked in")
    if t - utc(current.check_in) > MAX_ENTRY:
        raise AttendanceState(f"you checked in on {current.day.isoformat()}, more than a day ago: ask HR to put in "
                              "when you left")
    if t <= utc(current.check_in):
        raise AttendanceState("you've only just checked in")
    current.check_out = t
    return current


def check_entry(db: Session, employee: Employee, check_in_at: datetime, check_out_at: datetime | None,
                skip: str | None = None) -> date:
    """The day an entry from check_in_at to check_out_at counts on, or AttendanceError."""
    start = utc(check_in_at)
    end = utc(check_out_at) if check_out_at is not None else None
    if end is not None and end <= start:
        raise AttendanceError("the check-out is before the check-in")
    if end is not None and end - start > MAX_ENTRY:
        raise AttendanceError("an entry is at most a day long")
    t = now()
    if start > t or (end is not None and end > t):
        raise AttendanceError("an entry can't be in the future")
    day = local_day(policy(db), start)
    _employed(employee, day)
    near = db.scalars(select(AttendanceEntry).where(AttendanceEntry.employee_id == employee.id,
                                                    AttendanceEntry.day >= day - timedelta(days=2),
                                                    AttendanceEntry.day <= day + timedelta(days=2),
                                                    *([AttendanceEntry.id != skip] if skip else [])))
    for other in near:
        o_start, o_end = utc(other.check_in), utc(other.check_out) if other.check_out else None
        if (end is None or o_start < end) and (o_end is None or o_end > start):
            raise AttendanceError(f"it overlaps the entry from {hm_local(db, o_start)} on {other.day.isoformat()}")
    return day


def hm_local(db: Session, dt: datetime) -> str:
    return utc(dt).astimezone(zone(policy(db).timezone)).strftime("%H:%M")


# ---------------------------------------------------------------- schemas


class EntryIn(BaseModel):
    employee_id: str
    check_in: datetime
    check_out: datetime
    note: str = Field(default="", max_length=300)


class EntryUpdate(BaseModel):
    check_in: datetime | None = None
    check_out: datetime | None = None
    note: str | None = Field(default=None, max_length=300)


class CheckIn(BaseModel):
    note: str = Field(default="", max_length=300)


class EntryOut(BaseModel):
    id: str
    employee_id: str
    employee_name: str
    day: date
    check_in: datetime
    check_out: datetime | None
    minutes: int | None
    source: str
    note: str
    locked: bool            # the month's overtime is paid: the entry stands


class MonthOut(BaseModel):
    employee_id: str
    employee_name: str
    department_name: str | None
    days_present: int
    worked_minutes: int
    overtime_minutes: int
    approved_minutes: int | None
    approval_note: str
    paid: bool              # approved and the month's pay run is posted
    checked_in: bool        # has an open check-in now
    can_approve: bool


class MyAttendanceOut(BaseModel):
    employee_id: str | None
    timezone: str
    daily_minutes: int
    month: str
    open: EntryOut | None
    entries: list[EntryOut]
    worked_minutes: int
    overtime_minutes: int
    approved_minutes: int | None
    paid: bool


class ApprovalIn(BaseModel):
    minutes: int = Field(gt=0, le=31 * 24 * 60)
    note: str = Field(default="", max_length=500)


# ---------------------------------------------------------------- helpers


def _errors(fn):
    try:
        return fn()
    except AttendanceState as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except AttendanceError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


def _me(db: Session, ctx: OrgContext) -> Employee | None:
    return db.scalar(select(Employee).where(Employee.user_id == ctx.user.id))


def _locked_me(db: Session, ctx: OrgContext) -> Employee:
    me = _me(db, ctx)
    if me is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "your account isn't linked to an employee record yet; ask HR")
    return lock_employee(db, me.id)


def _entry_out(db: Session, e: AttendanceEntry, name: str, locks: dict | None = None) -> EntryOut:
    locks = locks if locks is not None else {}
    key = (e.employee_id, e.day.replace(day=1))
    if key not in locks:
        locks[key] = paid(db, e.employee_id, e.day)
    return EntryOut(id=e.id, employee_id=e.employee_id, employee_name=name, day=e.day, check_in=utc(e.check_in),
                    check_out=utc(e.check_out) if e.check_out else None, minutes=minutes_of(e), source=e.source,
                    note=e.note, locked=locks[key])


def _can_see(db: Session, ctx: OrgContext, e: Employee) -> bool:
    return e.user_id == ctx.user.id or ctx.has(P.HR_MANAGE) or can_decide(db, ctx.user.id, ctx.permissions, e)


def _say(ctx: OrgContext, ar: str, en: str) -> str:
    return ar if ctx.organization.default_locale == "ar" else en


def _entry(db: Session, entry_id: str) -> AttendanceEntry:
    e = db.get(AttendanceEntry, entry_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "attendance entry not found")
    return e


# ---------------------------------------------------------------- your own


@router.get("/orgs/{org_id}/hr/attendance/me", response_model=MyAttendanceOut)
def my_attendance(month: str | None = Query(default=None, pattern=MONTH),
                  ctx: OrgContext = Depends(require(P.ATTENDANCE_SELF)), db: Session = Depends(get_db)):
    p = policy(db)
    month = month or local_day(p, now()).strftime("%Y-%m")
    start, end = month_bounds(month)
    me = _me(db, ctx)
    if me is None:
        return MyAttendanceOut(employee_id=None, timezone=p.timezone, daily_minutes=p.daily_minutes, month=month,
                               open=None, entries=[], worked_minutes=0, overtime_minutes=0, approved_minutes=None,
                               paid=False)
    entries = list(db.scalars(select(AttendanceEntry).where(AttendanceEntry.employee_id == me.id,
                                                            AttendanceEntry.day >= start, AttendanceEntry.day <= end)
                              .order_by(AttendanceEntry.check_in.desc())))
    current = open_entry(db, me.id)
    m = months(db, start, end, [me.id])[me.id]
    a = approval(db, me.id, start)
    locks: dict = {}
    return MyAttendanceOut(
        employee_id=me.id, timezone=p.timezone, daily_minutes=p.daily_minutes, month=month,
        open=_entry_out(db, current, me.full_name, locks) if current else None,
        entries=[_entry_out(db, e, me.full_name, locks) for e in entries], worked_minutes=m.worked,
        overtime_minutes=m.overtime, approved_minutes=a.minutes if a else None,
        paid=a is not None and run_posted(db, start))


@router.post("/orgs/{org_id}/hr/attendance/check-in", response_model=EntryOut, status_code=status.HTTP_201_CREATED)
def check_in_now(body: CheckIn, ctx: OrgContext = Depends(require(P.ATTENDANCE_SELF)), db: Session = Depends(get_db)):
    me = _locked_me(db, ctx)
    entry = _errors(lambda: check_in(db, me, user_id=ctx.user.id, note=body.note))
    try:
        db.commit()
    except IntegrityError:                          # a second check-in at the same moment
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "you're already checked in")
    return _entry_out(db, entry, me.full_name)


@router.post("/orgs/{org_id}/hr/attendance/check-out", response_model=EntryOut)
def check_out_now(ctx: OrgContext = Depends(require(P.ATTENDANCE_SELF)), db: Session = Depends(get_db)):
    me = _locked_me(db, ctx)
    entry = _errors(lambda: check_out(db, me))
    db.commit()
    return _entry_out(db, entry, me.full_name)


# ---------------------------------------------------------------- entries


@router.get("/orgs/{org_id}/hr/attendance", response_model=list[EntryOut])
def list_entries(employee_id: str, month: str = Query(pattern=MONTH),
                 ctx: OrgContext = Depends(require_any(P.HR_MANAGE, P.HR_READ, P.ATTENDANCE_SELF)),
                 db: Session = Depends(get_db)):
    """A person's entries for a month: for HR, their department's manager, and themselves."""
    e = db.get(Employee, employee_id)
    if e is None or not _can_see(db, ctx, e):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "employee not found")
    start, end = month_bounds(month)
    locks: dict = {}
    return [_entry_out(db, x, e.full_name, locks) for x in db.scalars(
        select(AttendanceEntry).where(AttendanceEntry.employee_id == e.id, AttendanceEntry.day >= start,
                                      AttendanceEntry.day <= end).order_by(AttendanceEntry.check_in))]


def _audit_details(e: Employee, entry: AttendanceEntry, db: Session) -> dict:
    return {"employee": e.full_name, "day": entry.day.isoformat(), "check_in": hm_local(db, entry.check_in),
            "check_out": hm_local(db, entry.check_out) if entry.check_out else None}


@router.post("/orgs/{org_id}/hr/attendance", response_model=EntryOut, status_code=status.HTTP_201_CREATED)
def add_entry(body: EntryIn, request: Request, ctx: OrgContext = Depends(require(P.HR_MANAGE)),
              db: Session = Depends(get_db)):
    """HR puts in someone's hours (a forgotten check-in, work away from the office...)."""
    e = lock_employee(db, body.employee_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "employee not found")
    day = _errors(lambda: check_entry(db, e, body.check_in, body.check_out))
    if paid(db, e.id, day):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{e.full_name}'s overtime for {day:%Y-%m} has been paid")
    entry = AttendanceEntry(employee_id=e.id, day=day, check_in=utc(body.check_in), check_out=utc(body.check_out),
                            source="hr", note=body.note, created_by=ctx.user.id)
    db.add(entry)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="attendance.recorded", target_type="attendance_entry",
                 target_id=entry.id, details=_audit_details(e, entry, db), ip_address=client_ip(request))
    db.commit()
    return _entry_out(db, entry, e.full_name)


@router.patch("/orgs/{org_id}/hr/attendance/{entry_id}", response_model=EntryOut)
def update_entry(entry_id: str, body: EntryUpdate, request: Request, ctx: OrgContext = Depends(require(P.HR_MANAGE)),
                 db: Session = Depends(get_db)):
    """Correct an entry's times (or put in when someone left, for a check-in never checked out)."""
    entry = _entry(db, entry_id)
    e = lock_employee(db, entry.employee_id)
    db.refresh(entry)
    if paid(db, e.id, entry.day):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{e.full_name}'s overtime for {entry.day:%Y-%m} has been paid")
    changes = body.model_dump(exclude_unset=True)
    if "check_out" in changes and changes["check_out"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "an entry that has been checked out stays checked out")
    start = changes.get("check_in") or entry.check_in
    end = changes.get("check_out") or entry.check_out
    day = _errors(lambda: check_entry(db, e, start, end, skip=entry.id))
    if day != entry.day and paid(db, e.id, day):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{e.full_name}'s overtime for {day:%Y-%m} has been paid")
    before = _audit_details(e, entry, db)
    entry.check_in, entry.check_out, entry.day = utc(start), utc(end) if end else None, day
    if changes.get("note") is not None:
        entry.note = changes["note"]
    audit.record(db, actor_user_id=ctx.user.id, action="attendance.changed", target_type="attendance_entry",
                 target_id=entry.id, details={"before": before, "after": _audit_details(e, entry, db)},
                 ip_address=client_ip(request))
    db.commit()
    return _entry_out(db, entry, e.full_name)


@router.delete("/orgs/{org_id}/hr/attendance/{entry_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_entry(entry_id: str, request: Request, ctx: OrgContext = Depends(require(P.HR_MANAGE)),
                 db: Session = Depends(get_db)):
    entry = _entry(db, entry_id)
    e = lock_employee(db, entry.employee_id)
    if paid(db, e.id, entry.day):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{e.full_name}'s overtime for {entry.day:%Y-%m} has been paid")
    audit.record(db, actor_user_id=ctx.user.id, action="attendance.removed", target_type="attendance_entry",
                 target_id=entry.id, details=_audit_details(e, entry, db), ip_address=client_ip(request))
    db.delete(entry)
    db.commit()


# ---------------------------------------------------------------- overtime


def _rows(db: Session, ctx: OrgContext, people: list[Employee], start: date, end: date) -> list[MonthOut]:
    ids = [e.id for e in people]
    if not ids:
        return []
    worked = months(db, start, end, ids)
    approvals = {a.employee_id: a for a in db.scalars(select(OvertimeApproval).where(
        OvertimeApproval.month == start, OvertimeApproval.employee_id.in_(ids)))}
    open_now = set(db.scalars(select(AttendanceEntry.employee_id).where(
        AttendanceEntry.employee_id.in_(ids), AttendanceEntry.check_out.is_(None))))
    depts = {d.id: d.name for d in db.scalars(select(Department))}
    posted = run_posted(db, start)
    out = []
    for e in people:
        m, a = worked[e.id], approvals.get(e.id)
        out.append(MonthOut(employee_id=e.id, employee_name=e.full_name, department_name=depts.get(e.department_id or ""),
                            days_present=m.days_present, worked_minutes=m.worked, overtime_minutes=m.overtime,
                            approved_minutes=a.minutes if a else None, approval_note=a.note if a else "",
                            paid=a is not None and posted, checked_in=e.id in open_now,
                            can_approve=not posted and can_decide(db, ctx.user.id, ctx.permissions, e)))
    return out


@router.get("/orgs/{org_id}/hr/overtime", response_model=list[MonthOut])
def overtime(month: str = Query(pattern=MONTH), ctx: OrgContext = Depends(require_any(P.HR_MANAGE, P.HR_READ)),
             db: Session = Depends(get_db)):
    """Everyone employed in the month (HR), or the departments you manage."""
    start, end = month_bounds(month)
    stmt = select(Employee).where(or_(Employee.hire_date.is_(None), Employee.hire_date <= end),
                                  or_(Employee.end_date.is_(None), Employee.end_date >= start))
    if not ctx.has(P.HR_MANAGE):
        managed = list(db.scalars(select(Department.id).where(Department.manager_user_id == ctx.user.id)))
        if not managed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "only HR and department managers see everyone's hours")
        stmt = stmt.where(Employee.department_id.in_(managed))
    return _rows(db, ctx, list(db.scalars(stmt.order_by(Employee.full_name))), start, end)


def _deciding(db: Session, ctx: OrgContext, employee_id: str, month: str) -> tuple[Employee, date, date]:
    if not re.fullmatch(MONTH, month):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "give the month as YYYY-MM")
    e = lock_employee(db, employee_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "employee not found")
    if e.user_id == ctx.user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "someone else approves your own overtime")
    if not can_decide(db, ctx.user.id, ctx.permissions, e):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only HR or the department's manager approves overtime")
    start, end = month_bounds(month)
    if run_posted(db, start):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"the pay run for {month} is posted: cancel it to change that month's overtime")
    return e, start, end


@router.put("/orgs/{org_id}/hr/overtime/{employee_id}/{month}", response_model=MonthOut)
def approve_overtime(employee_id: str, month: str, body: ApprovalIn, request: Request,
                     ctx: OrgContext = Depends(require_any(P.HR_MANAGE, P.HR_READ)), db: Session = Depends(get_db)):
    """Approve overtime for payment with the month's pay run: up to what attendance shows (less, if some of it
    wasn't asked for). Approving again replaces the amount."""
    e, start, end = _deciding(db, ctx, employee_id, month)
    settings = db.scalar(select(PayrollSettings))
    if settings is None or settings.overtime_rule == "none":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "overtime isn't paid yet: choose how in the payroll settings first")
    m = months(db, start, end, [e.id])[e.id]
    if body.minutes > m.overtime:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"{e.full_name}'s attendance shows {hm(m.overtime)} hours of overtime in {month}")
    a = approval(db, e.id, start)
    if a is None:
        a = OvertimeApproval(employee_id=e.id, month=start)
        db.add(a)
    a.minutes, a.recorded_minutes, a.note, a.approved_by = body.minutes, m.overtime, body.note, ctx.user.id
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="overtime.approved", target_type="employee", target_id=e.id,
                 details={"employee": e.full_name, "month": month, "hours": hm(body.minutes),
                          "recorded": hm(m.overtime)}, ip_address=client_ip(request))
    if e.user_id:
        db.add(Notification(
            user_id=e.user_id, title=_say(ctx, f"وُوفق على ساعاتك الإضافية لشهر {month}", f"Your overtime for {month} was approved"),
            body=_say(ctx, f"{hm(body.minutes)} ساعة تُدفع مع رواتب الشهر", f"{hm(body.minutes)} hours, paid with the month's pay")
            + (f"\n{body.note}" if body.note else ""),
            idempotency_key=f"overtime:{e.id}:{month}:{body.minutes}:{e.user_id}"))
    db.commit()
    return _rows(db, ctx, [e], start, end)[0]


@router.delete("/orgs/{org_id}/hr/overtime/{employee_id}/{month}", status_code=status.HTTP_204_NO_CONTENT,
               response_model=None)
def withdraw_overtime(employee_id: str, month: str, request: Request,
                      ctx: OrgContext = Depends(require_any(P.HR_MANAGE, P.HR_READ)), db: Session = Depends(get_db)):
    e, start, _ = _deciding(db, ctx, employee_id, month)
    a = approval(db, e.id, start)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no overtime is approved for that month")
    audit.record(db, actor_user_id=ctx.user.id, action="overtime.withdrawn", target_type="employee", target_id=e.id,
                 details={"employee": e.full_name, "month": month, "hours": hm(a.minutes)}, ip_address=client_ip(request))
    db.delete(a)
    db.commit()
