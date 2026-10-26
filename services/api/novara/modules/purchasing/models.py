"""Purchase orders, vendor bills and payments to vendors."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.money import ExactDecimal
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk

PO_STATUSES = ("draft", "confirmed", "cancelled")
BILL_STATUSES = ("draft", "posted", "partially_paid", "paid", "credited", "cancelled")


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
        CheckConstraint("amount_paid_minor >= 0 AND amount_credited_minor >= 0 "
                        "AND amount_paid_minor + amount_credited_minor <= total_minor", name="ck_bill_paid_range"),
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
    # The part of vendor credits that reduced what is due on this bill.
    amount_credited_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # In another currency than the organization's: units of its currency per unit of the bill's, as booked. The
    # ledger is kept in the organization's currency: base_* are the posted amounts there, base_settled_minor how
    # much of the payable payments and vendor credits have cleared, and grni_base what posting took out of goods
    # received not invoiced per purchase order line (their receipts' value, at the receipts' rates).
    exchange_rate: Mapped[Decimal] = mapped_column(ExactDecimal(18, 8), default=Decimal(1), server_default="1", nullable=False)
    base_subtotal_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    base_total_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    base_settled_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    grni_base: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    journal_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reversal_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))

    @property
    def amount_due_minor(self) -> int:
        if self.status in ("draft", "cancelled"):
            return 0
        return self.total_minor - self.amount_paid_minor - self.amount_credited_minor

    @property
    def base_due_minor(self) -> int:
        """What is still due, in the organization's currency (the payable's balance for this bill)."""
        if self.status in ("draft", "cancelled"):
            return 0
        return self.base_total_minor - self.base_settled_minor


class VendorBillLine(IdMixin, TenantScoped, Base):
    __tablename__ = "vendor_bill_lines"
    __table_args__ = (
        org_unique_id(),  # vendor credit lines point at the bill line they credit
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
    # A bill in another currency: the rate on the payment date, what left the bank in the organization's currency,
    # and the exchange difference (paid minus what it cleared at the bill's rate: positive is a loss).
    exchange_rate: Mapped[Decimal] = mapped_column(ExactDecimal(18, 8), default=Decimal(1), server_default="1", nullable=False)
    base_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    fx_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)


class VendorCredit(IdMixin, TimestampMixin, TenantScoped, Base):
    """A vendor's credit against a posted bill: goods sent back, a price allowance, or a service not delivered.

    The amount first reduces what is still due on the bill (``applied_minor``); anything beyond that
    the vendor owes back, and is recorded when the money arrives (``refunded_minor``).
    """

    __tablename__ = "vendor_credits"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "number", name="uq_vendor_credit_org_number"),
        same_org_fk("bill_id", "vendor_bills"),
        same_org_fk("vendor_id", "vendors"),
        same_org_fk("journal_entry_id", "journal_entries"),
        CheckConstraint("total_minor > 0", name="ck_vendor_credit_total_positive"),
        CheckConstraint("applied_minor >= 0 AND refunded_minor >= 0 AND applied_minor + refunded_minor <= total_minor",
                        name="ck_vendor_credit_settled_range"),
    )

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    bill_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    vendor_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    vendor_reference: Mapped[str] = mapped_column(String(60), default="", nullable=False)  # the vendor's credit note number
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    applied_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    refunded_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    journal_entry_id: Mapped[str] = mapped_column(String(36), nullable=False)
    stock_reference: Mapped[str] = mapped_column(String(40), default="", nullable=False)  # goods sent back
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    # In the organization's currency, at the bill's rate (like customer credit notes).
    base_total_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    base_applied_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    base_refunded_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)


class VendorCreditLine(IdMixin, TenantScoped, Base):
    __tablename__ = "vendor_credit_lines"
    __table_args__ = (
        same_org_fk("credit_id", "vendor_credits", ondelete="CASCADE"),
        same_org_fk("bill_line_id", "vendor_bill_lines"),
        same_org_fk("product_id", "products"),
        same_org_fk("tax_id", "taxes"),
        CheckConstraint("CAST(quantity AS NUMERIC) > 0", name="ck_vendor_credit_line_qty_positive"),
        CheckConstraint("CAST(returned_quantity AS NUMERIC) >= 0 AND CAST(returned_quantity AS NUMERIC) <= CAST(quantity AS NUMERIC)",
                        name="ck_vendor_credit_line_returned_range"),
    )

    credit_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    bill_line_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(18, 4), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(ExactDecimal(18, 6), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tax_rate: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), default=Decimal(0), nullable=False)
    net_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    returned_quantity: Mapped[Decimal] = mapped_column(ExactDecimal(18, 4), default=Decimal(0), nullable=False)


class VendorCreditTax(IdMixin, TenantScoped, Base):
    """Tax credited per rate, so the last credit of a bill can take the exact remainder."""

    __tablename__ = "vendor_credit_taxes"
    __table_args__ = (
        same_org_fk("credit_id", "vendor_credits", ondelete="CASCADE"),
        same_org_fk("tax_id", "taxes"),
    )

    credit_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    tax_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rate: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), nullable=False)
    base_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)


class VendorRefund(IdMixin, TimestampMixin, TenantScoped, Base):
    """Money a vendor paid back for a credit."""

    __tablename__ = "vendor_refunds"
    __table_args__ = (
        same_org_fk("credit_id", "vendor_credits"),
        same_org_fk("journal_entry_id", "journal_entries"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_vendor_refund_org_idempotency"),
        CheckConstraint("amount_minor > 0", name="ck_vendor_refund_amount_positive"),
    )

    credit_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    refund_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)  # bank | cash
    reference: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    journal_entry_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    base_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
