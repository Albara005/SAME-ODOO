"""Payroll: what each employee is paid every month, the contributions taken on it, and monthly pay runs.

Generic on purpose: an organization sets up its own earnings and deductions per employee and its own
contributions (a share of the contributory pay from the employee and from the employer, up to a
monthly cap: social insurance such as GOSI in Saudi Arabia is one). End-of-service benefits follow the
market's rules (payroll/eosb.py): provisioned every month and settled when someone leaves. Wage
protection file formats and overtime are not built in.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint, false, text)
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.money import ExactDecimal
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk

RUN_STATUSES = ("draft", "posted", "paid", "cancelled")


class Contribution(IdMixin, TimestampMixin, TenantScoped, Base):
    """A share of each covered employee's contributory pay, from the employee and from the employer."""

    __tablename__ = "payroll_contributions"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "name", name="uq_payroll_contribution_name"),
        CheckConstraint("CAST(employee_rate AS NUMERIC) BETWEEN 0 AND 100 AND CAST(employer_rate AS NUMERIC) BETWEEN 0 AND 100",
                        name="ck_payroll_contribution_rates"),
        CheckConstraint("base_cap_minor IS NULL OR base_cap_minor > 0", name="ck_payroll_contribution_cap"),
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    employee_rate: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), nullable=False)  # percent
    employer_rate: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), nullable=False)
    # The contributory pay counts up to this much a month (e.g. GOSI's 45,000 SAR); none: no cap.
    base_cap_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class EmployeePay(IdMixin, TimestampMixin, TenantScoped, Base):
    """How an employee is paid: their bank account and the contributions that cover them."""

    __tablename__ = "employee_pay"
    __table_args__ = (
        same_org_fk("employee_id", "employees", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "employee_id", name="uq_employee_pay_employee"),
    )

    employee_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    iban: Mapped[str] = mapped_column(String(34), default="", nullable=False)
    contribution_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class PayItem(IdMixin, TimestampMixin, TenantScoped, Base):
    """A monthly amount on an employee's pay: an earning (basic salary, housing...) or a deduction (an advance
    being paid back...). Earnings are prorated for part of a month; deductions are taken in full."""

    __tablename__ = "pay_items"
    __table_args__ = (
        same_org_fk("employee_id", "employees", ondelete="CASCADE"),
        CheckConstraint("kind IN ('earning', 'deduction')", name="ck_pay_item_kind"),
        CheckConstraint("amount_minor > 0", name="ck_pay_item_amount"),
    )

    employee_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Counts toward the base contributions are taken on (e.g. basic salary and housing for GOSI).
    contributory: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Counts toward the wage end-of-service benefits are worked out on (e.g. basic salary and housing in Saudi Arabia).
    end_of_service: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)


class PayRun(IdMixin, TimestampMixin, TenantScoped, Base):
    """One month's pay for everyone employed in it: draft (recalculated freely) -> posted -> paid, or cancelled."""

    __tablename__ = "pay_runs"
    __table_args__ = (
        org_unique_id(),
        same_org_fk("journal_entry_id", "journal_entries"),
        same_org_fk("payment_entry_id", "journal_entries"),
        same_org_fk("reversal_entry_id", "journal_entries"),
        CheckConstraint("status IN ('draft', 'posted', 'paid', 'cancelled')", name="ck_pay_run_status"),
        CheckConstraint("period_end >= period_start", name="ck_pay_run_period"),
        # One live run per month.
        Index("uq_pay_run_month", "organization_id", "period_start", unique=True,
              postgresql_where=text("status <> 'cancelled'"), sqlite_where=text("status <> 'cancelled'")),
    )

    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="draft", nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    gross_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    deductions_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    employee_contributions_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    employer_contributions_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    net_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # How much the end-of-service provision grows (or shrinks) with this month: posted with the run.
    eosb_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    journal_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payment_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reversal_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class Payslip(IdMixin, TenantScoped, Base):
    """One employee's pay in a run, with the lines it was worked out from (kept as they were)."""

    __tablename__ = "payslips"
    __table_args__ = (
        same_org_fk("run_id", "pay_runs", ondelete="CASCADE"),
        same_org_fk("employee_id", "employees"),
        UniqueConstraint("organization_id", "run_id", "employee_id", name="uq_payslip_run_employee"),
        CheckConstraint("net_minor >= 0", name="ck_payslip_net"),
    )

    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    employee_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    employee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    iban: Mapped[str] = mapped_column(String(34), default="", nullable=False)
    days_in_period: Mapped[int] = mapped_column(Integer, nullable=False)
    days_paid: Mapped[Decimal] = mapped_column(ExactDecimal(5, 1), nullable=False)
    unpaid_leave_days: Mapped[Decimal] = mapped_column(ExactDecimal(5, 1), nullable=False)
    # [{"name", "kind": earning | deduction | contribution, "amount_minor", "employer_minor"}]
    lines: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deductions_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    employee_contributions_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    employer_contributions_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    net_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # End of service as of the month's last day: days served, and the full award the provision holds for them.
    service_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    eosb_liability_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)


class PayrollSettings(IdMixin, TimestampMixin, TenantScoped, Base):
    """The organization's payroll rules: which market's end-of-service rules apply (payroll/eosb.py)."""

    __tablename__ = "payroll_settings"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_payroll_settings_org"),
        CheckConstraint("eosb_rule IN ('saudi', 'uae', 'none')", name="ck_payroll_settings_rule"),
    )

    eosb_rule: Mapped[str] = mapped_column(String(10), default="none", nullable=False)


class EndOfService(IdMixin, TimestampMixin, TenantScoped, Base):
    """A leaver's final settlement: the end-of-service award under the rules in force, and unused leave paid out.
    draft (worked out again freely) -> posted -> paid, or cancelled with a reversing entry."""

    __tablename__ = "end_of_service"
    __table_args__ = (
        org_unique_id(),
        same_org_fk("employee_id", "employees"),
        same_org_fk("journal_entry_id", "journal_entries"),
        same_org_fk("payment_entry_id", "journal_entries"),
        same_org_fk("reversal_entry_id", "journal_entries"),
        CheckConstraint("status IN ('draft', 'posted', 'paid', 'cancelled')", name="ck_end_of_service_status"),
        CheckConstraint("award_minor >= 0 AND leave_pay_minor >= 0 AND provision_minor >= 0",
                        name="ck_end_of_service_amounts"),
        CheckConstraint("CAST(leave_days AS NUMERIC) >= 0", name="ck_end_of_service_leave_days"),
        # One live settlement per employee.
        Index("uq_end_of_service_employee", "organization_id", "employee_id", unique=True,
              postgresql_where=text("status <> 'cancelled'"), sqlite_where=text("status <> 'cancelled'")),
    )

    employee_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    employee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="draft", nullable=False)
    rule: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    hire_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_day: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    service_days: Mapped[int] = mapped_column(Integer, nullable=False)
    wage_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    months: Mapped[str] = mapped_column(String(40), nullable=False)       # exact, as a fraction ("15/2")
    share: Mapped[str] = mapped_column(String(10), nullable=False)        # "1", "2/3", "1/3", "0"
    full_award_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    award_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    leave_days: Mapped[Decimal] = mapped_column(ExactDecimal(5, 1), nullable=False)
    leave_pay_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # What the end-of-service provision held for the employee when the settlement was posted (it is released).
    provision_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    journal_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payment_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reversal_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))

    @property
    def total_minor(self) -> int:
        return self.award_minor + self.leave_pay_minor
