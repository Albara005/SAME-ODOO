"""Purchase orders, vendor bills and payments to vendors."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.money import ExactDecimal
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk

PO_STATUSES = ("draft", "confirmed", "cancelled")
BILL_STATUSES = ("draft", "posted", "partially_paid", "paid", "cancelled")


class PurchaseOrder(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "number", name="uq_purchase_order_org_number"),
        same_org_fk("vendor_id", "vendors"),
        same_org_fk("warehouse_id", "warehouses"),
    )

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    vendor_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    warehouse_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class PurchaseOrderLine(IdMixin, TenantScoped, Base):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        org_unique_id(),
        same_org_fk("order_id", "purchase_orders", ondelete="CASCADE"),
        same_org_fk("product_id", "products"),
        same_org_fk("tax_id", "taxes"),
        CheckConstraint("CAST(quantity AS NUMERIC) > 0", name="ck_po_line_qty_positive"),
        CheckConstraint("CAST(unit_cost AS NUMERIC) >= 0", name="ck_po_line_cost_nonnegative"),
        CheckConstraint("CAST(qty_received AS NUMERIC) >= 0 AND CAST(qty_received AS NUMERIC) <= CAST(quantity AS NUMERIC)",
                        name="ck_po_line_received_range"),
    )

    order_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(18, 4), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(ExactDecimal(18, 6), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tax_rate: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), default=Decimal(0), nullable=False)
    net_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    qty_received: Mapped[Decimal] = mapped_column(ExactDecimal(18, 4), default=Decimal(0), nullable=False)


class VendorBill(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "vendor_bills"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "number", name="uq_vendor_bill_org_number"),
        same_org_fk("vendor_id", "vendors"),
        same_org_fk("journal_entry_id", "journal_entries"),
        same_org_fk("reversal_entry_id", "journal_entries"),
        CheckConstraint("amount_paid_minor >= 0 AND amount_paid_minor <= total_minor", name="ck_bill_paid_range"),
    )

    # Our number, assigned when posted; the vendor's own invoice number is vendor_reference.
    number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    vendor_reference: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    vendor_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    bill_date: Mapped[date] = mapped_column(Date, nullable=False)
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

    @property
    def amount_due_minor(self) -> int:
        return 0 if self.status in ("draft", "cancelled") else self.total_minor - self.amount_paid_minor


class VendorBillLine(IdMixin, TenantScoped, Base):
    __tablename__ = "vendor_bill_lines"
    __table_args__ = (
        same_org_fk("bill_id", "vendor_bills", ondelete="CASCADE"),
        same_org_fk("product_id", "products"),
        same_org_fk("tax_id", "taxes"),
        same_org_fk("account_id", "accounts"),
        same_org_fk("purchase_order_line_id", "purchase_order_lines"),
        CheckConstraint("CAST(quantity AS NUMERIC) > 0", name="ck_bill_line_qty_positive"),
        CheckConstraint("CAST(unit_cost AS NUMERIC) >= 0", name="ck_bill_line_cost_nonnegative"),
    )

    bill_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(18, 4), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(ExactDecimal(18, 6), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tax_rate: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), default=Decimal(0), nullable=False)
    net_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Expense account debited; empty for received goods, which clear "goods received not invoiced".
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    purchase_order_line_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class BillPayment(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "bill_payments"
    __table_args__ = (
        same_org_fk("bill_id", "vendor_bills"),
        same_org_fk("journal_entry_id", "journal_entries"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_bill_payment_org_idempotency"),
        CheckConstraint("amount_minor > 0", name="ck_bill_payment_amount_positive"),
    )

    bill_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)  # bank | cash
    reference: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    journal_entry_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
