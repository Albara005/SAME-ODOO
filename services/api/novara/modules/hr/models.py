"""People: departments, employee records, and leave.

Leave is counted in working days: the organization's weekend days and public holidays inside
a request don't use up the allowance. A leave type's allowance is per calendar year, so a
request stays inside one year; it can be given all at once or earned month by month, prorated
for people who start or leave during the year, and unused days can carry into the next year up
to a limit. A single day can be taken as a morning or afternoon half day. Pending and approved
requests both hold their days, and one person never has two live requests on the same day
(two halves of one day excepted).

Attendance is what people check in and out for (or HR enters for them), counted on the day it starts in the
organization's time zone. Time beyond the normal working day, and all of it on a weekend day or public holiday, is
overtime; what HR or the department's manager approves for a month is paid with that month's pay run.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint,
                        false, text)
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.money import ExactDecimal
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk

DEFAULT_WEEKEND = [4, 5]  # Friday and Saturday (Monday = 0)
DEFAULT_TIMEZONE = "Asia/Riyadh"
DEFAULT_DAILY_MINUTES = 480


class HrPolicy(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "hr_policies"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_hr_policy_org"),
        CheckConstraint("daily_minutes BETWEEN 60 AND 960", name="ck_hr_policy_daily_minutes"),
    )

    weekend: Mapped[list] = mapped_column(JSON, default=lambda: list(DEFAULT_WEEKEND), nullable=False)
    # The working day attendance is counted in.
    timezone: Mapped[str] = mapped_column(String(64), default=DEFAULT_TIMEZONE, server_default=DEFAULT_TIMEZONE,
                                          nullable=False)
    # A normal working day: time worked beyond it is overtime (and the hourly wage is a day's wage over it).
    daily_minutes: Mapped[int] = mapped_column(Integer, default=DEFAULT_DAILY_MINUTES,
                                               server_default=str(DEFAULT_DAILY_MINUTES), nullable=False)


class Holiday(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "holidays"
    __table_args__ = (UniqueConstraint("organization_id", "day", name="uq_holiday_day"),)

    day: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class Department(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "departments"
    __table_args__ = (org_unique_id(), UniqueConstraint("organization_id", "name", name="uq_department_name"))

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Decides the department's leave requests (as well as anyone with hr.manage).
    manager_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class Employee(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "employees"
    __table_args__ = (
        org_unique_id(),
        same_org_fk("department_id", "departments"),
        UniqueConstraint("organization_id", "user_id", name="uq_employee_user"),
        CheckConstraint("end_date IS NULL OR hire_date IS NULL OR end_date >= hire_date", name="ck_employee_dates"),
    )

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    job_title: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    department_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # The member account, so the person can request their own leave.
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    work_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    hire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # the last working day, once known


class LeaveType(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "leave_types"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "name", name="uq_leave_type_name"),
        CheckConstraint("annual_days IS NULL OR annual_days > 0", name="ck_leave_type_days"),
        CheckConstraint("accrual IN ('upfront', 'monthly')", name="ck_leave_type_accrual"),
        CheckConstraint("carry_over_days BETWEEN 0 AND 366", name="ck_leave_type_carry"),
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # Working days per calendar year; none means no allowance is tracked (e.g. unpaid leave).
    annual_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # upfront: the year's allowance from the first day; monthly: earned day by day, usable up to the end
    # of each month (so it is always in proportion to the time worked).
    accrual: Mapped[str] = mapped_column(String(10), default="upfront", server_default="upfront", nullable=False)
    # An upfront allowance in proportion to the part of the year someone is employed (start or end date).
    prorate: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    # Unused days that move into the next year, at most this many.
    carry_over_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    paid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class LeaveRequest(IdMixin, TimestampMixin, TenantScoped, Base):
    """status: pending | approved | rejected | cancelled"""

    __tablename__ = "leave_requests"
    __table_args__ = (
        org_unique_id(),
        same_org_fk("employee_id", "employees"),
        same_org_fk("leave_type_id", "leave_types"),
        CheckConstraint("end_date >= start_date", name="ck_leave_dates"),
        CheckConstraint("CAST(days AS NUMERIC) > 0", name="ck_leave_days"),
        CheckConstraint("half_day IS NULL OR (half_day IN ('am', 'pm') AND start_date = end_date)", name="ck_leave_half_day"),
    )

    employee_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    leave_type_id: Mapped[str] = mapped_column(String(36), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    days: Mapped[Decimal] = mapped_column(ExactDecimal(6, 1), nullable=False)  # working days (0.5 steps), fixed when requested
    half_day: Mapped[str | None] = mapped_column(String(2), nullable=True)  # am | pm, for a single half day
    reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    requested_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    decided_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str] = mapped_column(String(500), default="", nullable=False)


class AttendanceEntry(IdMixin, TimestampMixin, TenantScoped, Base):
    """A stretch of work: checked in, and checked out once it's done. One person has at most one open entry, and
    their entries don't overlap."""

    __tablename__ = "attendance_entries"
    __table_args__ = (
        same_org_fk("employee_id", "employees"),
        CheckConstraint("check_out IS NULL OR check_out > check_in", name="ck_attendance_times"),
        CheckConstraint("source IN ('self', 'hr')", name="ck_attendance_source"),
        Index("uq_attendance_open", "organization_id", "employee_id", unique=True,
              postgresql_where=text("check_out IS NULL"), sqlite_where=text("check_out IS NULL")),
    )

    employee_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)  # the check-in's date in the organization's zone
    check_in: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(4), nullable=False)  # self: checked in and out; hr: entered by HR
    note: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class OvertimeApproval(IdMixin, TimestampMixin, TenantScoped, Base):
    """Overtime approved for payment for one person's month: at most what their attendance shows."""

    __tablename__ = "overtime_approvals"
    __table_args__ = (
        same_org_fk("employee_id", "employees"),
        UniqueConstraint("organization_id", "employee_id", "month", name="uq_overtime_month"),
        CheckConstraint("minutes > 0 AND minutes <= recorded_minutes", name="ck_overtime_minutes"),
    )

    employee_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    month: Mapped[date] = mapped_column(Date, nullable=False)          # its first day
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_minutes: Mapped[int] = mapped_column(Integer, nullable=False)  # what attendance showed when approved
    note: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
