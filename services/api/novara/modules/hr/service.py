"""Working days, leave balances and the rules for requesting and deciding leave."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from novara.modules.access.service import holders
from novara.modules.hr.models import (DEFAULT_WEEKEND, Department, Employee, Holiday, HrPolicy, LeaveRequest, LeaveType,
                                      WorkSchedule)

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


def days_off(db: Session, employee: Employee | None) -> set[int]:
    """The weekdays someone doesn't work: their work schedule's days off, or the organization's weekend."""
    s = db.get(WorkSchedule, employee.schedule_id) if employee is not None and employee.schedule_id else None
    if s is not None:
        return {i for i, m in enumerate(s.minutes) if not m}
    return set(weekend(db))


def working_days(db: Session, start: date, end: date, employee: Employee | None = None) -> int:
    """Days from start to end inclusive, without the person's days off (or the weekend) and public holidays."""
    if end < start:
        raise LeaveError("the leave ends before it starts")
    if (end - start).days >= MAX_SPAN_DAYS:
        raise LeaveError("a request covers at most a year")
    off = days_off(db, employee)
    holidays = set(db.scalars(select(Holiday.day).where(Holiday.day >= start, Holiday.day <= end)))
    n, day = 0, start
    while day <= end:
        if day.weekday() not in off and day not in holidays:
            n += 1
        day += timedelta(days=1)
    return n


HALF = Decimal("0.5")
CARRY_LOOKBACK_YEARS = 5


def half_days(x: Decimal) -> Decimal:
    """Rounded to the nearest half day (halves round up)."""
    return (x * 2).quantize(Decimal(1), rounding=ROUND_HALF_UP) / 2


def _employed_days(employee: Employee, year: int, until: date) -> int:
    first = max(date(year, 1, 1), employee.hire_date or date(year, 1, 1))
    last = min(date(year, 12, 31), until, employee.end_date or date(year, 12, 31))
    return max((last - first).days + 1, 0)


def _days_in(year: int) -> int:
    return (date(year, 12, 31) - date(year, 1, 1)).days + 1


def allowance(t: LeaveType, e: Employee, year: int) -> Decimal | None:
    """The year's allowance, before anything carried over: the whole of it, or (prorate or monthly accrual)
    in proportion to the days employed that year."""
    if t.annual_days is None:
        return None
    if t.accrual == "upfront" and not t.prorate:
        return Decimal(t.annual_days)
    return half_days(Decimal(t.annual_days) * _employed_days(e, year, date(year, 12, 31)) / _days_in(year))


def earned(t: LeaveType, e: Employee, year: int, on: date) -> Decimal | None:
    """What can be used by ``on``: the allowance, or with monthly accrual what has been earned up to the
    end of that month."""
    full = allowance(t, e, year)
    if full is None or t.accrual != "monthly":
        return full
    if on.year > year:
        return full
    if on.year < year:
        return Decimal(0)
    month_end = date(on.year, 12, 31) if on.month == 12 else date(on.year, on.month + 1, 1) - timedelta(days=1)
    return half_days(Decimal(t.annual_days) * _employed_days(e, year, month_end) / _days_in(year))


def _days(db: Session, employee_id: str, type_id: str, year: int, statuses: tuple[str, ...],
          skip: str | None = None) -> Decimal:
    stmt = select(LeaveRequest.days).where(
        LeaveRequest.employee_id == employee_id, LeaveRequest.leave_type_id == type_id,
        LeaveRequest.status.in_(statuses),
        LeaveRequest.start_date >= date(year, 1, 1), LeaveRequest.start_date <= date(year, 12, 31))
    if skip:
        stmt = stmt.where(LeaveRequest.id != skip)
    return sum((Decimal(d) for d in db.scalars(stmt)), Decimal(0))  # summed here: SQLite keeps exact decimals as text


def carried(db: Session, t: LeaveType, e: Employee, year: int) -> Decimal:
    """Unused days brought into ``year``: what each earlier year left (its allowance plus what it brought in,
    less what was approved), at most carry_over_days, starting from the hire year or a few years back."""
    if t.annual_days is None or not t.carry_over_days:
        return Decimal(0)
    first = max(year - CARRY_LOOKBACK_YEARS, e.hire_date.year if e.hire_date else year - CARRY_LOOKBACK_YEARS)
    carry = Decimal(0)
    for y in range(first, year):
        left = (allowance(t, e, y) or Decimal(0)) + carry - _days(db, e.id, t.id, y, ("approved",))
        carry = min(Decimal(t.carry_over_days), max(left, Decimal(0)))
    return carry


@dataclass(frozen=True)
class Balance:
    leave_type: LeaveType
    allowance: Decimal | None   # this year's own allowance
    carried: Decimal            # brought in from last year
    earned: Decimal | None      # usable so far (the allowance, or what monthly accrual has earned by today)
    approved: Decimal
    pending: Decimal

    @property
    def remaining(self) -> Decimal | None:
        """What can still be asked for now."""
        return None if self.earned is None else self.earned + self.carried - self.approved - self.pending


def balances(db: Session, employee: Employee, year: int, today: date | None = None) -> list[Balance]:
    today = today or date.today()
    types = db.scalars(select(LeaveType).where(LeaveType.active.is_(True)).order_by(LeaveType.name))
    return [Balance(t, allowance(t, employee, year), carried(db, t, employee, year), earned(t, employee, year, today),
                    _days(db, employee.id, t.id, year, ("approved",)), _days(db, employee.id, t.id, year, ("pending",)))
            for t in types]


def available(db: Session, employee: Employee, t: LeaveType, start: date, statuses: tuple[str, ...] = LIVE,
              skip: str | None = None) -> Decimal | None:
    """Days of this type still free for a leave starting on ``start`` (None: not tracked)."""
    usable = earned(t, employee, start.year, start)
    if usable is None:
        return None
    return usable + carried(db, t, employee, start.year) - _days(db, employee.id, t.id, start.year, statuses, skip)


def fmt_days(days: Decimal) -> str:
    return f"{days.normalize():f}"


def check_request(db: Session, employee: Employee, leave_type: LeaveType, start: date, end: date,
                  skip: str | None = None, half_day: str | None = None) -> Decimal:
    """The request's working days, or LeaveError if it can't be made."""
    if employee.end_date and end > employee.end_date:
        raise LeaveError("the leave runs past the person's last day")
    if not leave_type.active:
        raise LeaveError("this leave type is no longer offered")
    if start.year != end.year:
        raise LeaveError("allowances are per calendar year: split the leave at the end of December")
    if employee.hire_date and start < employee.hire_date:
        raise LeaveError("the leave starts before the hire date")
    if half_day is not None and (half_day not in ("am", "pm") or start != end):
        raise LeaveError("a half day is the morning or the afternoon of a single day")
    whole = working_days(db, start, end, employee)
    if whole == 0:
        raise LeaveError("those days are all days off or public holidays")
    days = HALF if half_day else Decimal(whole)
    for other in db.scalars(select(LeaveRequest).where(
            LeaveRequest.employee_id == employee.id, LeaveRequest.status.in_(LIVE),
            LeaveRequest.start_date <= end, LeaveRequest.end_date >= start,
            *([LeaveRequest.id != skip] if skip else []))):
        other_half = half_day and other.half_day and other.half_day != half_day and other.start_date == start
        if not other_half:
            raise LeaveError("there is already a request on some of those days")
    free = available(db, employee, leave_type, start, LIVE, skip)
    if free is not None and days > free:
        left = fmt_days(max(free, Decimal(0)))
        if leave_type.accrual == "monthly":
            raise LeaveError(f"only {left} day(s) of {leave_type.name} earned by the end of {start:%B %Y}")
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
