from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara.automation.models import Notification
from novara.deps import OrgContext, client_ip, get_db, require, require_any
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit
from novara.modules.hr import attendance
from novara.modules.hr import service as svc
from novara.modules.hr.models import Department, Employee, Holiday, HrPolicy, LeaveRequest, LeaveType, WorkSchedule
from novara.modules.organizations.models import Membership, Organization

router = APIRouter(tags=["hr"])


# ---------------------------------------------------------------- schemas


class DepartmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    manager_user_id: str | None = None


class DepartmentOut(BaseModel):
    id: str
    name: str
    manager_user_id: str | None
    employees: int


class EmployeeIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    job_title: str = Field(default="", max_length=120)
    department_id: str | None = None
    user_id: str | None = None
    work_email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=40)
    hire_date: date | None = None
    schedule_id: str | None = None      # their work schedule; none follows the HR policy
    reduced_hours: bool = True          # reduced-hours periods (Ramadan) shorten their day


class EmployeeUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    job_title: str | None = Field(default=None, max_length=120)
    department_id: str | None = None
    user_id: str | None = None
    work_email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    hire_date: date | None = None
    end_date: date | None = None  # setting it marks the person as having left
    schedule_id: str | None = None
    reduced_hours: bool | None = None


class EmployeeOut(BaseModel):
    id: str
    full_name: str
    job_title: str
    department_id: str | None
    department_name: str | None
    user_id: str | None
    work_email: str
    phone: str
    hire_date: date | None
    end_date: date | None
    status: str
    away_today: bool
    schedule_id: str | None
    reduced_hours: bool


class LeaveTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    annual_days: int | None = Field(default=None, gt=0, le=366)
    paid: bool = True
    accrual: Literal["upfront", "monthly"] = "upfront"
    prorate: bool = False
    carry_over_days: int = Field(default=0, ge=0, le=366)


class LeaveTypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    annual_days: int | None = Field(default=None, gt=0, le=366)
    paid: bool | None = None
    active: bool | None = None
    accrual: Literal["upfront", "monthly"] | None = None
    prorate: bool | None = None
    carry_over_days: int | None = Field(default=None, ge=0, le=366)


class LeaveTypeOut(BaseModel):
    id: str
    name: str
    annual_days: int | None
    paid: bool
    active: bool
    accrual: str
    prorate: bool
    carry_over_days: int


class PolicyIn(BaseModel):
    """What is given changes; what is left out stays."""
    weekend: list[int] | None = Field(default=None, max_length=6)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    daily_minutes: int | None = Field(default=None, ge=60, le=960)   # a normal working day

    @field_validator("weekend")
    @classmethod
    def _days(cls, v: list[int] | None) -> list[int] | None:
        if v is not None and any(d < 0 or d > 6 for d in v):
            raise ValueError("weekend days are 0 (Monday) to 6 (Sunday)")
        return None if v is None else sorted(set(v))


class PolicyOut(BaseModel):
    weekend: list[int]
    timezone: str
    daily_minutes: int


class HolidayIn(BaseModel):
    day: date
    name: str = Field(min_length=1, max_length=120)


class HolidayOut(BaseModel):
    id: str
    day: date
    name: str


class BalanceOut(BaseModel):
    leave_type_id: str
    leave_type: str
    paid: bool
    accrual: str
    allowance: float | None   # this year's own allowance (prorated if the type says so)
    carried: float            # unused days brought in from last year
    earned: float | None      # usable so far: the allowance, or what monthly accrual has earned by now
    approved: float
    pending: float
    remaining: float | None   # what can still be asked for now


class LeaveIn(BaseModel):
    leave_type_id: str
    start_date: date
    end_date: date
    half_day: Literal["am", "pm"] | None = None  # the morning or the afternoon of a single day
    reason: str = Field(default="", max_length=500)
    employee_id: str | None = None  # someone else's: hr.manage only


class DecisionIn(BaseModel):
    note: str = Field(default="", max_length=500)


class LeaveOut(BaseModel):
    id: str
    employee_id: str
    employee_name: str
    department_name: str | None
    leave_type_id: str
    leave_type: str
    start_date: date
    end_date: date
    days: float
    half_day: str | None
    reason: str
    status: str
    decided_at: datetime | None
    decision_note: str
    created_at: datetime
    mine: bool
    can_decide: bool


class AwayOut(BaseModel):
    """Who is off and when, without the kind of leave or the reason."""
    employee_id: str
    employee_name: str
    department_name: str | None
    start_date: date
    end_date: date


class MyLeaveOut(BaseModel):
    employee: EmployeeOut | None
    year: int
    balances: list[BalanceOut]


class DaysOut(BaseModel):
    days: int


# ---------------------------------------------------------------- helpers


def _active_member(db: Session, user_id: str) -> bool:
    return db.scalar(select(Membership.id).where(Membership.user_id == user_id, Membership.status == "active")) is not None


def _department(db: Session, dept_id: str | None) -> Department | None:
    if dept_id is None:
        return None
    d = db.get(Department, dept_id)
    if d is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "department not found")
    return d


def _work_schedule(db: Session, schedule_id: str | None) -> None:
    if schedule_id is not None and db.get(WorkSchedule, schedule_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "work schedule not found")


def _employee(db: Session, employee_id: str) -> Employee:
    e = db.get(Employee, employee_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "employee not found")
    return e


def _away_today(db: Session, ids: list[str], today: date) -> set[str]:
    if not ids:
        return set()
    return set(db.scalars(select(LeaveRequest.employee_id).where(
        LeaveRequest.employee_id.in_(ids), LeaveRequest.status == "approved",
        LeaveRequest.start_date <= today, LeaveRequest.end_date >= today)))


def _employee_out(db: Session, e: Employee, away: set[str], depts: dict[str, str] | None = None) -> EmployeeOut:
    if depts is None:
        d = db.get(Department, e.department_id) if e.department_id else None
        dept_name = d.name if d else None
    else:
        dept_name = depts.get(e.department_id or "")
    return EmployeeOut(id=e.id, full_name=e.full_name, job_title=e.job_title, department_id=e.department_id,
                       department_name=dept_name, user_id=e.user_id, work_email=e.work_email, phone=e.phone,
                       hire_date=e.hire_date, end_date=e.end_date, status=svc.status_of(e, date.today()),
                       away_today=e.id in away, schedule_id=e.schedule_id, reduced_hours=e.reduced_hours)


def _num(d: Decimal | None) -> float | None:
    return None if d is None else float(d)


def _balances_out(bals: list[svc.Balance]) -> list[BalanceOut]:
    return [BalanceOut(leave_type_id=b.leave_type.id, leave_type=b.leave_type.name, paid=b.leave_type.paid,
                       accrual=b.leave_type.accrual, allowance=_num(b.allowance), carried=float(b.carried),
                       earned=_num(b.earned), approved=float(b.approved), pending=float(b.pending),
                       remaining=_num(b.remaining))
            for b in bals]


def _leave_out(db: Session, ctx: OrgContext, r: LeaveRequest, cache: dict | None = None) -> LeaveOut:
    cache = cache if cache is not None else {}
    e = cache.get(("e", r.employee_id)) or cache.setdefault(("e", r.employee_id), db.get(Employee, r.employee_id))
    t = cache.get(("t", r.leave_type_id)) or cache.setdefault(("t", r.leave_type_id), db.get(LeaveType, r.leave_type_id))
    dept = None
    if e.department_id:
        dept = cache.get(("d", e.department_id)) or cache.setdefault(("d", e.department_id),
                                                                      db.get(Department, e.department_id))
    decide = r.status == "pending" and svc.can_decide(db, ctx.user.id, ctx.permissions, e)
    return LeaveOut(id=r.id, employee_id=e.id, employee_name=e.full_name, department_name=dept.name if dept else None,
                    leave_type_id=t.id, leave_type=t.name, start_date=r.start_date, end_date=r.end_date,
                    days=float(r.days), half_day=r.half_day, reason=r.reason, status=r.status,
                    decided_at=r.decided_at, decision_note=r.decision_note,
                    created_at=r.created_at, mine=e.user_id == ctx.user.id, can_decide=decide)


def _say(org: Organization, ar: str, en: str) -> str:
    return ar if org.default_locale == "ar" else en


def _notify(db: Session, users: list[str], title: str, body: str, key: str) -> None:
    for uid in dict.fromkeys(users):
        db.add(Notification(user_id=uid, title=title[:300], body=body, idempotency_key=f"{key}:{uid}"))


# ---------------------------------------------------------------- policy and holidays


def _policy_out(p: HrPolicy) -> PolicyOut:
    return PolicyOut(weekend=list(p.weekend), timezone=p.timezone, daily_minutes=p.daily_minutes)


@router.get("/orgs/{org_id}/hr/policy", response_model=PolicyOut)
def get_policy(_: OrgContext = Depends(require_any(P.HR_READ, P.LEAVE_REQUEST, P.HR_MANAGE, P.ATTENDANCE_SELF)),
               db: Session = Depends(get_db)):
    return _policy_out(attendance.policy(db))


@router.put("/orgs/{org_id}/hr/policy", response_model=PolicyOut)
def set_policy(body: PolicyIn, request: Request, ctx: OrgContext = Depends(require(P.HR_MANAGE)),
               db: Session = Depends(get_db)):
    """The weekend days, the time zone attendance is counted in and the normal working day. Leave already asked for
    keeps the days it was counted with; overtime already approved keeps its hours."""
    if body.timezone is not None:
        try:
            attendance.zone(body.timezone)
        except attendance.AttendanceError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    policy = db.scalar(select(HrPolicy))
    if policy is None:
        policy = attendance.policy(db)
        db.add(policy)
    if body.weekend is not None and body.weekend != list(policy.weekend):
        policy.weekend = body.weekend
        audit.record(db, actor_user_id=ctx.user.id, action="hr.weekend_changed", target_type="organization",
                     target_id=ctx.organization.id, details={"weekend": body.weekend}, ip_address=client_ip(request))
    day = {k: v for k, v in (("timezone", body.timezone), ("daily_minutes", body.daily_minutes))
           if v is not None and v != getattr(policy, k)}
    if day:
        for k, v in day.items():
            setattr(policy, k, v)
        audit.record(db, actor_user_id=ctx.user.id, action="hr.working_day_changed", target_type="organization",
                     target_id=ctx.organization.id, details=day, ip_address=client_ip(request))
    try:
        db.commit()
    except IntegrityError:                          # two first saves at once: the other one won
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "the policy was just changed; try again")
    return _policy_out(policy)


@router.get("/orgs/{org_id}/hr/holidays", response_model=list[HolidayOut])
def list_holidays(year: int | None = None, _: OrgContext = Depends(require_any(P.HR_READ, P.LEAVE_REQUEST, P.HR_MANAGE)),
                  db: Session = Depends(get_db)):
    stmt = select(Holiday).order_by(Holiday.day)
    if year:
        stmt = stmt.where(Holiday.day >= date(year, 1, 1), Holiday.day <= date(year, 12, 31))
    return [HolidayOut(id=h.id, day=h.day, name=h.name) for h in db.scalars(stmt)]


@router.post("/orgs/{org_id}/hr/holidays", response_model=HolidayOut, status_code=status.HTTP_201_CREATED)
def add_holiday(body: HolidayIn, request: Request, ctx: OrgContext = Depends(require(P.HR_MANAGE)),
                db: Session = Depends(get_db)):
    """A public holiday isn't counted in leave asked for from now on."""
    if db.scalar(select(Holiday.id).where(Holiday.day == body.day)):
        raise HTTPException(status.HTTP_409_CONFLICT, "that day is already a holiday")
    h = Holiday(day=body.day, name=body.name)
    db.add(h)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="hr.holiday_added", target_type="holiday", target_id=h.id,
                 details={"day": body.day.isoformat(), "name": body.name}, ip_address=client_ip(request))
    db.commit()
    return HolidayOut(id=h.id, day=h.day, name=h.name)


@router.delete("/orgs/{org_id}/hr/holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def remove_holiday(holiday_id: str, request: Request, ctx: OrgContext = Depends(require(P.HR_MANAGE)),
                   db: Session = Depends(get_db)):
    h = db.get(Holiday, holiday_id)
    if h is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "holiday not found")
    audit.record(db, actor_user_id=ctx.user.id, action="hr.holiday_removed", target_type="holiday", target_id=h.id,
                 details={"day": h.day.isoformat(), "name": h.name}, ip_address=client_ip(request))
    db.delete(h)
    db.commit()


@router.get("/orgs/{org_id}/hr/leave-days", response_model=DaysOut)
def leave_days(start: date, end: date, employee_id: str | None = None,
               ctx: OrgContext = Depends(require_any(P.HR_MANAGE, P.LEAVE_REQUEST)), db: Session = Depends(get_db)):
    """How many working days a leave from start to end takes: for the employee (HR) or yourself, on their schedule."""
    who = db.get(Employee, employee_id) if employee_id and ctx.has(P.HR_MANAGE) else \
        db.scalar(select(Employee).where(Employee.user_id == ctx.user.id))
    try:
        return DaysOut(days=svc.working_days(db, start, end, who))
    except svc.LeaveError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


# ---------------------------------------------------------------- departments


@router.get("/orgs/{org_id}/hr/departments", response_model=list[DepartmentOut])
def list_departments(_: OrgContext = Depends(require_any(P.HR_READ, P.HR_MANAGE)), db: Session = Depends(get_db)):
    counts: dict[str, int] = {}
    for dept_id in db.scalars(select(Employee.department_id).where(svc.still_here(date.today()))):
        if dept_id:
            counts[dept_id] = counts.get(dept_id, 0) + 1
    return [DepartmentOut(id=d.id, name=d.name, manager_user_id=d.manager_user_id, employees=counts.get(d.id, 0))
            for d in db.scalars(select(Department).order_by(Department.name))]


def _check_manager(db: Session, user_id: str | None) -> None:
    if user_id and not _active_member(db, user_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "the manager isn't a member of this organization")


@router.post("/orgs/{org_id}/hr/departments", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
def create_department(body: DepartmentIn, request: Request, ctx: OrgContext = Depends(require(P.HR_MANAGE)),
                      db: Session = Depends(get_db)):
    _check_manager(db, body.manager_user_id)
    if db.scalar(select(Department.id).where(Department.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "a department with that name exists")
    d = Department(name=body.name, manager_user_id=body.manager_user_id)
    db.add(d)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="department.created", target_type="department", target_id=d.id,
                 details={"name": d.name}, ip_address=client_ip(request))
    db.commit()
    return DepartmentOut(id=d.id, name=d.name, manager_user_id=d.manager_user_id, employees=0)


@router.put("/orgs/{org_id}/hr/departments/{dept_id}", response_model=DepartmentOut)
def update_department(dept_id: str, body: DepartmentIn, request: Request,
                      ctx: OrgContext = Depends(require(P.HR_MANAGE)), db: Session = Depends(get_db)):
    d = db.get(Department, dept_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "department not found")
    _check_manager(db, body.manager_user_id)
    if db.scalar(select(Department.id).where(Department.name == body.name, Department.id != d.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "a department with that name exists")
    d.name, d.manager_user_id = body.name, body.manager_user_id
    audit.record(db, actor_user_id=ctx.user.id, action="department.updated", target_type="department", target_id=d.id,
                 details={"name": d.name, "manager_user_id": d.manager_user_id}, ip_address=client_ip(request))
    db.commit()
    n = len(list(db.scalars(select(Employee.id).where(Employee.department_id == d.id, svc.still_here(date.today())))))
    return DepartmentOut(id=d.id, name=d.name, manager_user_id=d.manager_user_id, employees=n)


# ---------------------------------------------------------------- employees


@router.get("/orgs/{org_id}/hr/employees", response_model=list[EmployeeOut])
def list_employees(status_filter: str | None = "active", _: OrgContext = Depends(require_any(P.HR_READ, P.HR_MANAGE)),
                   db: Session = Depends(get_db)):
    stmt = select(Employee).order_by(Employee.full_name)
    if status_filter == "active":
        stmt = stmt.where(svc.still_here(date.today()))
    elif status_filter == "left":
        stmt = stmt.where(~svc.still_here(date.today()))
    people = list(db.scalars(stmt))
    depts = {d.id: d.name for d in db.scalars(select(Department))}
    away = _away_today(db, [e.id for e in people], date.today())
    return [_employee_out(db, e, away, depts) for e in people]


def _check_link(db: Session, user_id: str | None, employee_id: str | None = None) -> None:
    if user_id is None:
        return
    if not _active_member(db, user_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "that account isn't a member of this organization")
    other = db.scalar(select(Employee.id).where(Employee.user_id == user_id))
    if other and other != employee_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "that member is already linked to another employee")


@router.post("/orgs/{org_id}/hr/employees", response_model=EmployeeOut, status_code=status.HTTP_201_CREATED)
def create_employee(body: EmployeeIn, request: Request, ctx: OrgContext = Depends(require(P.HR_MANAGE)),
                    db: Session = Depends(get_db)):
    _department(db, body.department_id)
    _work_schedule(db, body.schedule_id)
    _check_link(db, body.user_id)
    e = Employee(**body.model_dump())
    db.add(e)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "that member is already linked to another employee")
    audit.record(db, actor_user_id=ctx.user.id, action="employee.created", target_type="employee", target_id=e.id,
                 details={"name": e.full_name}, ip_address=client_ip(request))
    db.commit()
    return _employee_out(db, e, set())


@router.get("/orgs/{org_id}/hr/employees/{employee_id}", response_model=EmployeeOut)
def get_employee(employee_id: str, _: OrgContext = Depends(require_any(P.HR_READ, P.HR_MANAGE)), db: Session = Depends(get_db)):
    e = _employee(db, employee_id)
    return _employee_out(db, e, _away_today(db, [e.id], date.today()))


@router.patch("/orgs/{org_id}/hr/employees/{employee_id}", response_model=EmployeeOut)
def update_employee(employee_id: str, body: EmployeeUpdate, request: Request,
                    ctx: OrgContext = Depends(require(P.HR_MANAGE)), db: Session = Depends(get_db)):
    e = _employee(db, employee_id)
    changes = body.model_dump(exclude_unset=True)
    for required in ("full_name", "job_title", "work_email", "phone", "reduced_hours"):
        if required in changes and changes[required] is None:
            del changes[required]   # these can be emptied, not removed
    if "department_id" in changes:
        _department(db, changes["department_id"])
    if "schedule_id" in changes:
        _work_schedule(db, changes["schedule_id"])
    if "user_id" in changes:
        _check_link(db, changes["user_id"], e.id)
    for k, v in changes.items():
        setattr(e, k, v)
    if e.end_date and e.hire_date and e.end_date < e.hire_date:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "the end date is before the hire date")
    if e.end_date:
        # Leave after the last day no longer happens.
        for r in db.scalars(select(LeaveRequest).where(LeaveRequest.employee_id == e.id,
                                                       LeaveRequest.status.in_(svc.LIVE),
                                                       LeaveRequest.start_date > e.end_date)):
            r.status, r.decided_by, r.decided_at = "cancelled", ctx.user.id, datetime.now(timezone.utc)
    audit.record(db, actor_user_id=ctx.user.id, action="employee.updated", target_type="employee", target_id=e.id,
                 details={k: (v.isoformat() if isinstance(v, date) else v) for k, v in changes.items()},
                 ip_address=client_ip(request))
    db.commit()
    return _employee_out(db, e, _away_today(db, [e.id], date.today()))


@router.get("/orgs/{org_id}/hr/employees/{employee_id}/balances", response_model=list[BalanceOut])
def employee_balances(employee_id: str, year: int | None = None, ctx: OrgContext = Depends(require_any(P.HR_READ, P.HR_MANAGE)),
                      db: Session = Depends(get_db)):
    e = _employee(db, employee_id)
    if e.user_id != ctx.user.id and not svc.can_decide(db, ctx.user.id, ctx.permissions, e):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only HR, the person's department manager, or the person")
    return _balances_out(svc.balances(db, e, year or date.today().year))


# ---------------------------------------------------------------- leave types


def _type_out(t: LeaveType) -> LeaveTypeOut:
    return LeaveTypeOut(id=t.id, name=t.name, annual_days=t.annual_days, paid=t.paid, active=t.active,
                        accrual=t.accrual, prorate=t.prorate, carry_over_days=t.carry_over_days)


@router.get("/orgs/{org_id}/hr/leave-types", response_model=list[LeaveTypeOut])
def list_leave_types(_: OrgContext = Depends(require_any(P.HR_READ, P.LEAVE_REQUEST, P.HR_MANAGE)), db: Session = Depends(get_db)):
    return [_type_out(t) for t in db.scalars(select(LeaveType).order_by(LeaveType.active.desc(), LeaveType.name))]


@router.post("/orgs/{org_id}/hr/leave-types", response_model=LeaveTypeOut, status_code=status.HTTP_201_CREATED)
def create_leave_type(body: LeaveTypeIn, request: Request, ctx: OrgContext = Depends(require(P.HR_MANAGE)),
                      db: Session = Depends(get_db)):
    if db.scalar(select(LeaveType.id).where(LeaveType.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "a leave type with that name exists")
    t = LeaveType(name=body.name, annual_days=body.annual_days, paid=body.paid, accrual=body.accrual,
                  prorate=body.prorate, carry_over_days=body.carry_over_days)
    db.add(t)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="leave_type.created", target_type="leave_type", target_id=t.id,
                 details={"name": t.name, "annual_days": t.annual_days, "accrual": t.accrual, "prorate": t.prorate,
                          "carry_over_days": t.carry_over_days}, ip_address=client_ip(request))
    db.commit()
    return _type_out(t)


@router.patch("/orgs/{org_id}/hr/leave-types/{type_id}", response_model=LeaveTypeOut)
def update_leave_type(type_id: str, body: LeaveTypeUpdate, request: Request,
                      ctx: OrgContext = Depends(require(P.HR_MANAGE)), db: Session = Depends(get_db)):
    """A new allowance applies to new requests; ones already approved stand."""
    t = db.get(LeaveType, type_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "leave type not found")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("name") and db.scalar(select(LeaveType.id).where(LeaveType.name == changes["name"],
                                                                     LeaveType.id != t.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "a leave type with that name exists")
    for k, v in changes.items():
        setattr(t, k, v)
    audit.record(db, actor_user_id=ctx.user.id, action="leave_type.updated", target_type="leave_type", target_id=t.id,
                 details=changes, ip_address=client_ip(request))
    db.commit()
    return _type_out(t)


# ---------------------------------------------------------------- my leave and who is away


def _me(db: Session, ctx: OrgContext) -> Employee | None:
    return db.scalar(select(Employee).where(Employee.user_id == ctx.user.id))


@router.get("/orgs/{org_id}/hr/me", response_model=MyLeaveOut)
def my_leave(year: int | None = None, ctx: OrgContext = Depends(require(P.LEAVE_REQUEST)),
             db: Session = Depends(get_db)):
    """Your employee record and leave balances (none until HR links your account to a record)."""
    year = year or date.today().year
    e = _me(db, ctx)
    if e is None:
        return MyLeaveOut(employee=None, year=year, balances=[])
    return MyLeaveOut(employee=_employee_out(db, e, _away_today(db, [e.id], date.today())), year=year,
                      balances=_balances_out(svc.balances(db, e, year)))


@router.get("/orgs/{org_id}/hr/away", response_model=list[AwayOut])
def who_is_away(start: date | None = None, end: date | None = None,
                _: OrgContext = Depends(require_any(P.HR_READ, P.LEAVE_REQUEST, P.HR_MANAGE)), db: Session = Depends(get_db)):
    """Approved leave overlapping the range (today and the next 13 days by default)."""
    start = start or date.today()
    end = end or start + timedelta(days=13)
    if end < start or (end - start).days > 92:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "pick a range of up to three months")
    rows = db.execute(select(LeaveRequest, Employee).join(Employee, Employee.id == LeaveRequest.employee_id)
                      .where(LeaveRequest.status == "approved", LeaveRequest.start_date <= end,
                             LeaveRequest.end_date >= start).order_by(LeaveRequest.start_date, Employee.full_name))
    depts = {d.id: d.name for d in db.scalars(select(Department))}
    return [AwayOut(employee_id=e.id, employee_name=e.full_name, department_name=depts.get(e.department_id or ""),
                    start_date=r.start_date, end_date=r.end_date) for r, e in rows]


# ---------------------------------------------------------------- leave requests


@router.get("/orgs/{org_id}/hr/leave-requests", response_model=list[LeaveOut])
def list_leave(status_filter: str | None = None, ctx: OrgContext = Depends(require_any(P.HR_MANAGE, P.LEAVE_REQUEST)),
               db: Session = Depends(get_db)):
    """HR sees every request; a department manager sees the department's; everyone sees their own."""
    stmt = select(LeaveRequest).join(Employee, Employee.id == LeaveRequest.employee_id)
    if not ctx.has(P.HR_MANAGE):
        managed = list(db.scalars(select(Department.id).where(Department.manager_user_id == ctx.user.id)))
        stmt = stmt.where(or_(Employee.user_id == ctx.user.id, Employee.department_id.in_(managed)))
    if status_filter in ("pending", "approved", "rejected", "cancelled"):
        stmt = stmt.where(LeaveRequest.status == status_filter)
    cache: dict = {}
    rows = db.scalars(stmt.order_by(LeaveRequest.start_date.desc()).limit(500))
    return [_leave_out(db, ctx, r, cache) for r in rows]


@router.post("/orgs/{org_id}/hr/leave-requests", response_model=LeaveOut, status_code=status.HTTP_201_CREATED)
def request_leave(body: LeaveIn, request: Request, ctx: OrgContext = Depends(require_any(P.HR_MANAGE, P.LEAVE_REQUEST)),
                  db: Session = Depends(get_db)):
    """Ask for leave (your own; HR can file for anyone). It waits for HR or the department's manager."""
    if body.employee_id is None:
        me = _me(db, ctx)
        if me is None or not ctx.has(P.LEAVE_REQUEST):
            raise HTTPException(status.HTTP_409_CONFLICT, "your account isn't linked to an employee record yet; ask HR")
        employee_id = me.id
    else:
        employee_id = body.employee_id
    e = svc.lock_employee(db, employee_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "employee not found")
    if e.user_id != ctx.user.id and not ctx.has(P.HR_MANAGE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "asking for someone else's leave needs hr.manage")
    t = db.get(LeaveType, body.leave_type_id)
    if t is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "leave type not found")
    try:
        days = svc.check_request(db, e, t, body.start_date, body.end_date, half_day=body.half_day)
    except svc.LeaveError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err))
    r = LeaveRequest(employee_id=e.id, leave_type_id=t.id, start_date=body.start_date, end_date=body.end_date,
                     days=days, half_day=body.half_day, reason=body.reason, requested_by=ctx.user.id)
    db.add(r)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="leave.requested", target_type="leave_request", target_id=r.id,
                 details={"employee": e.full_name, "leave_type": t.name, "start": r.start_date.isoformat(),
                          "end": r.end_date.isoformat(), "days": svc.fmt_days(days), "half_day": r.half_day},
                 ip_address=client_ip(request))
    org = ctx.organization
    _notify(db, [u for u in svc.deciders(db, e) if u != ctx.user.id],
            _say(org, f"طلب إجازة من {e.full_name}", f"Leave request from {e.full_name}"),
            _say(org, f"{t.name}: {svc.fmt_days(days)} يوم عمل من {r.start_date} إلى {r.end_date}",
                 f"{t.name}: {svc.fmt_days(days)} working day(s), {r.start_date} to {r.end_date}"), f"leave:{r.id}:new")
    db.commit()
    return _leave_out(db, ctx, r)


def _decide(db: Session, ctx: OrgContext, request_id: str) -> tuple[LeaveRequest, Employee]:
    r = db.get(LeaveRequest, request_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "leave request not found")
    e = svc.lock_employee(db, r.employee_id)
    db.refresh(r)
    if r.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"this request is already {r.status}")
    if e.user_id == ctx.user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "someone else decides your own leave")
    if not svc.can_decide(db, ctx.user.id, ctx.permissions, e):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only HR or the department's manager decides this request")
    return r, e


@router.post("/orgs/{org_id}/hr/leave-requests/{request_id}/approve", response_model=LeaveOut)
def approve_leave(request_id: str, body: DecisionIn, request: Request,
                  ctx: OrgContext = Depends(require_any(P.HR_MANAGE, P.LEAVE_REQUEST, P.HR_READ)),
                  db: Session = Depends(get_db)):
    r, e = _decide(db, ctx, request_id)
    t = db.get(LeaveType, r.leave_type_id)
    free = svc.available(db, e, t, r.start_date, ("approved",), skip=r.id)
    if free is not None and r.days > free:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"approving would go over the {t.name} allowance ({svc.fmt_days(max(free, Decimal(0)))} day(s) free)")
    r.status, r.decided_by, r.decided_at, r.decision_note = "approved", ctx.user.id, datetime.now(timezone.utc), body.note
    audit.record(db, actor_user_id=ctx.user.id, action="leave.approved", target_type="leave_request", target_id=r.id,
                 details={"employee": e.full_name, "leave_type": t.name, "start": r.start_date.isoformat(),
                          "end": r.end_date.isoformat(), "days": svc.fmt_days(r.days)}, ip_address=client_ip(request))
    if e.user_id:
        org = ctx.organization
        _notify(db, [e.user_id], _say(org, "وُوفق على إجازتك", "Your leave was approved"),
                f"{t.name}: {r.start_date} → {r.end_date}" + (f"\n{body.note}" if body.note else ""), f"leave:{r.id}:done")
    db.commit()
    return _leave_out(db, ctx, r)


@router.post("/orgs/{org_id}/hr/leave-requests/{request_id}/reject", response_model=LeaveOut)
def reject_leave(request_id: str, body: DecisionIn, request: Request,
                 ctx: OrgContext = Depends(require_any(P.HR_MANAGE, P.LEAVE_REQUEST, P.HR_READ)),
                 db: Session = Depends(get_db)):
    r, e = _decide(db, ctx, request_id)
    t = db.get(LeaveType, r.leave_type_id)
    r.status, r.decided_by, r.decided_at, r.decision_note = "rejected", ctx.user.id, datetime.now(timezone.utc), body.note
    audit.record(db, actor_user_id=ctx.user.id, action="leave.rejected", target_type="leave_request", target_id=r.id,
                 details={"employee": e.full_name, "leave_type": t.name}, ip_address=client_ip(request))
    if e.user_id:
        org = ctx.organization
        _notify(db, [e.user_id], _say(org, "رُفض طلب إجازتك", "Your leave request was declined"),
                f"{t.name}: {r.start_date} → {r.end_date}" + (f"\n{body.note}" if body.note else ""), f"leave:{r.id}:done")
    db.commit()
    return _leave_out(db, ctx, r)


@router.post("/orgs/{org_id}/hr/leave-requests/{request_id}/cancel", response_model=LeaveOut)
def cancel_leave(request_id: str, request: Request,
                 ctx: OrgContext = Depends(require_any(P.HR_MANAGE, P.LEAVE_REQUEST)), db: Session = Depends(get_db)):
    """Withdraw a pending request, or give back approved leave that hasn't started. HR can cancel any."""
    r = db.get(LeaveRequest, request_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "leave request not found")
    e = svc.lock_employee(db, r.employee_id)
    db.refresh(r)
    hr = ctx.has(P.HR_MANAGE)
    if e.user_id != ctx.user.id and not hr:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "leave request not found")
    if r.status not in svc.LIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"this request is already {r.status}")
    if r.status == "approved" and r.start_date <= date.today() and not hr:
        raise HTTPException(status.HTTP_409_CONFLICT, "leave that has started is changed by HR")
    was = r.status
    r.status = "cancelled"
    audit.record(db, actor_user_id=ctx.user.id, action="leave.cancelled", target_type="leave_request", target_id=r.id,
                 details={"employee": e.full_name, "was": was}, ip_address=client_ip(request))
    db.commit()
    return _leave_out(db, ctx, r)
