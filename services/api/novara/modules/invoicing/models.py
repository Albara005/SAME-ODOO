"""Customer invoices and payments."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.money import ExactDecimal
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk

INVOICE_STATUSES = ("draft", "posted", "partially_paid", "paid", "cancelled")


class Invoice(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "number", name="uq_invoice_org_number"),
        same_org_fk("customer_id", "customers"),
        same_org_fk("journal_entry_id", "journal_entries"),
        same_org_fk("reversal_entry_id", "journal_entries"),
        CheckConstraint("amount_paid_minor >= 0 AND amount_paid_minor <= total_minor", name="ck_invoice_paid_range"),
    )

    # Assigned when posted, so numbering is gap-free (drafts can be deleted).
    number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    amount_paid_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    journal_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reversal_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class InvoiceLine(IdMixin, TenantScoped, Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (
        same_org_fk("invoice_id", "invoices", ondelete="CASCADE"),
        same_org_fk("product_id", "products"),
        same_org_fk("tax_id", "taxes"),
        same_org_fk("sales_order_line_id", "sales_order_lines"),
        CheckConstraint("CAST(quantity AS NUMERIC) > 0", name="ck_invoice_line_qty_positive"),
        CheckConstraint("CAST(unit_price AS NUMERIC) >= 0", name="ck_invoice_line_price_nonnegative"),
        CheckConstraint("CAST(discount_percent AS NUMERIC) >= 0 AND CAST(discount_percent AS NUMERIC) <= 100", name="ck_invoice_line_discount"),
    )

    invoice_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(18, 4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(ExactDecimal(18, 6), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), default=Decimal(0), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Snapshot of the tax rate used, so later reporting never depends on a mutable record.
    tax_rate: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), default=Decimal(0), nullable=False)
    net_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Set when the line was invoiced from a sales order line.
    sales_order_line_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class Payment(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "payments"
    __table_args__ = (
        same_org_fk("invoice_id", "invoices"),
        same_org_fk("journal_entry_id", "journal_entries"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_payment_org_idempotency"),
        CheckConstraint("amount_minor > 0", name="ck_payment_amount_positive"),
    )

    invoice_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)  # bank | cash
    reference: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    journal_entry_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
