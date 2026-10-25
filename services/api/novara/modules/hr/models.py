"""People: departments, employee records, and leave.

Leave is counted in working days: the organization's weekend days and public holidays inside
a request don't use up the allowance. A leave type's allowance is per calendar year, so a
request stays inside one year; it can be given all at once or earned month by month, prorated
for people who start or leave during the year, and unused days can carry into the next year up
to a limit. A single day can be taken as a morning or afternoon half day. Pending and approved
requests both hold their days, and one person never has two live requests on the same day
(two halves of one day excepted).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.money import ExactDecimal
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk

DEFAULT_WEEKEND = [4, 5]  # Friday and Saturday (Monday = 0)


class HrPolicy(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "hr_policies"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_hr_policy_org"),)

    weekend: Mapped[list] = mapped_column(JSON, default=lambda: list(DEFAULT_WEEKEND), nullable=False)


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
