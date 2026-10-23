"""Working days, leave balances and the rules for requesting and deciding leave."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from novara.modules.access.service import holders
from novara.modules.hr.models import DEFAULT_WEEKEND, Department, Employee, Holiday, HrPolicy, LeaveRequest, LeaveType

LIVE = ("pending", "approved")  # requests that hold their days
MAX_SPAN_DAYS = 366


class LeaveError(ValueError):
    """The request breaks a leave rule (HTTP 422 by the router)."""


def still_here(today: date):
    """Employees whose last day hasn't passed (someone working out their notice is still here)."""
    return or_(Employee.end_date.is_(None), Employee.end_date >= today)


def status_of(employee: Employee, today: date) -> str:
    return "left" if employee.end_date and employee.end_date < today else "active"


def weekend(db: Session) -> list[int]:
    policy = db.scalar(select(HrPolicy))
    return list(policy.weekend) if policy else list(DEFAULT_WEEKEND)


def working_days(db: Session, start: date, end: date) -> int:
    """Days from start to end inclusive, without the weekend and public holidays."""
    if end < start:
        raise LeaveError("the leave ends before it starts")
    if (end - start).days >= MAX_SPAN_DAYS:
        raise LeaveError("a request covers at most a year")
    off = set(weekend(db))
    holidays = set(db.scalars(select(Holiday.day).where(Holiday.day >= start, Holiday.day <= end)))
    n, day = 0, start
    while day <= end:
        if day.weekday() not in off and day not in holidays:
            n += 1
        day += timedelta(days=1)
    return n


@dataclass(frozen=True)
class Balance:
    leave_type: LeaveType
    allowance: int | None
    approved: int
    pending: int

    @property
    def remaining(self) -> int | None:
        return None if self.allowance is None else self.allowance - self.approved - self.pending


def _held(db: Session, employee_id: str, type_id: str, year: int, status: str, skip: str | None = None) -> int:
    stmt = select(func.coalesce(func.sum(LeaveRequest.days), 0)).where(
        LeaveRequest.employee_id == employee_id, LeaveRequest.leave_type_id == type_id, LeaveRequest.status == status,
        LeaveRequest.start_date >= date(year, 1, 1), LeaveRequest.start_date <= date(year, 12, 31))
    if skip:
        stmt = stmt.where(LeaveRequest.id != skip)
    return int(db.scalar(stmt) or 0)


def balances(db: Session, employee: Employee, year: int) -> list[Balance]:
    types = db.scalars(select(LeaveType).where(LeaveType.active.is_(True)).order_by(LeaveType.name))
    return [Balance(t, t.annual_days, _held(db, employee.id, t.id, year, "approved"),
                    _held(db, employee.id, t.id, year, "pending")) for t in types]


def check_request(db: Session, employee: Employee, leave_type: LeaveType, start: date, end: date,
                  skip: str | None = None) -> int:
    """The request's working days, or LeaveError if it can't be made."""
    if employee.end_date and end > employee.end_date:
        raise LeaveError("the leave runs past the person's last day")
    if not leave_type.active:
        raise LeaveError("this leave type is no longer offered")
    if start.year != end.year:
        raise LeaveError("allowances are per calendar year: split the leave at the end of December")
    if employee.hire_date and start < employee.hire_date:
        raise LeaveError("the leave starts before the hire date")
    days = working_days(db, start, end)
    if days == 0:
        raise LeaveError("those days are all weekend or public holidays")
    overlap = db.scalar(select(LeaveRequest.id).where(
        LeaveRequest.employee_id == employee.id, LeaveRequest.status.in_(LIVE),
        LeaveRequest.start_date <= end, LeaveRequest.end_date >= start,
        *([LeaveRequest.id != skip] if skip else [])).limit(1))
    if overlap:
        raise LeaveError("there is already a request on some of those days")
    if leave_type.annual_days is not None:
        held = sum(_held(db, employee.id, leave_type.id, start.year, s, skip) for s in LIVE)
        if held + days > leave_type.annual_days:
            left = max(leave_type.annual_days - held, 0)
            raise LeaveError(f"only {left} day(s) of {leave_type.name} left in {start.year}")
    return days


def lock_employee(db: Session, employee_id: str) -> Employee | None:
    """Requests and decisions for one person go one at a time, so two can't both pass the checks."""
    return db.scalar(select(Employee).where(Employee.id == employee_id).with_for_update())


def can_decide(db: Session, user_id: str, permissions: set[str], employee: Employee) -> bool:
    """HR managers decide anyone's requests, a department's manager decides that department's; nobody their own."""
    if employee.user_id == user_id:
        return False
    if "hr.manage" in permissions:
        return True
    if employee.department_id is None:
        return False
    dept = db.get(Department, employee.department_id)
    return dept is not None and dept.manager_user_id == user_id


def deciders(db: Session, employee: Employee) -> list[str]:
    """Who to tell about a new request: the department's manager, else everyone with hr.manage."""
    if employee.department_id:
        dept = db.get(Department, employee.department_id)
        if dept and dept.manager_user_id and dept.manager_user_id != employee.user_id:
            return [dept.manager_user_id]
    return [u for u in holders(db, "hr.manage") if u != employee.user_id]
