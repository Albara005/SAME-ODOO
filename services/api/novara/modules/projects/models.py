"""Projects: work for a customer (or internal), its tasks, and the hours people log on it.

Billable hours are invoiced at the project's hourly rate into a draft invoice; each time entry
then points at that invoice, so the same hour is never billed twice. Deleting the draft, or
cancelling the posted invoice, makes its hours billable again.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.money import ExactDecimal
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk


class Project(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "projects"
    __table_args__ = (
        org_unique_id(),
        same_org_fk("customer_id", "customers"),
        same_org_fk("tax_id", "taxes"),
        CheckConstraint("hourly_rate IS NULL OR CAST(hourly_rate AS NUMERIC) >= 0", name="ck_project_rate"),
        CheckConstraint("budget_hours IS NULL OR CAST(budget_hours AS NUMERIC) > 0", name="ck_project_budget"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)  # none: internal work
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, index=True)  # active | closed
    # The price of an hour of billable work; none means the project isn't billed by the hour.
    hourly_rate: Mapped[Decimal | None] = mapped_column(ExactDecimal(18, 6), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    budget_hours: Mapped[Decimal | None] = mapped_column(ExactDecimal(10, 2), nullable=True)
    manager_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class TimeEntry(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "time_entries"
    __table_args__ = (
        same_org_fk("project_id", "projects"),
        same_org_fk("task_id", "tasks"),
        same_org_fk("invoice_id", "invoices"),
        CheckConstraint("CAST(hours AS NUMERIC) > 0 AND CAST(hours AS NUMERIC) <= 24", name="ck_time_entry_hours"),
    )

    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    hours: Mapped[Decimal] = mapped_column(ExactDecimal(5, 2), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    billable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Set when the hours were put on an invoice (cleared if that draft is deleted).
    invoice_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
