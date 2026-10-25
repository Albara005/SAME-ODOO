"""Customer invoices, payments, credit notes and refunds."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.money import ExactDecimal
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk

# "credited": settled entirely by credit notes, nothing paid.
INVOICE_STATUSES = ("draft", "posted", "partially_paid", "paid", "credited", "cancelled")


class Invoice(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "number", name="uq_invoice_org_number"),
        same_org_fk("customer_id", "customers"),
        same_org_fk("journal_entry_id", "journal_entries"),
        same_org_fk("reversal_entry_id", "journal_entries"),
        same_org_fk("debit_note_of_id", "invoices"),
        CheckConstraint("amount_paid_minor >= 0 AND amount_credited_minor >= 0 "
                        "AND amount_paid_minor + amount_credited_minor <= total_minor", name="ck_invoice_paid_range"),
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
    # The part of credit notes that reduced what the customer owes on this invoice.
    amount_credited_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # In another currency than the organization's: units of its currency per unit of the invoice's, as booked.
    # The ledger is kept in the organization's currency: base_* are the posted amounts there, and
    # base_settled_minor is how much of the receivable payments and credit notes have cleared.
    exchange_rate: Mapped[Decimal] = mapped_column(ExactDecimal(18, 8), default=Decimal(1), server_default="1", nullable=False)
    base_subtotal_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    base_total_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    base_settled_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    journal_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reversal_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    # A debit note: charges more on an earlier invoice (an undercharged price, a late fee...). It is an invoice of its
    # own (paid, credited and aged like one), numbered DN-, and e-invoiced as type 383 referring to that invoice.
    debit_note_of_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    reason: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)  # a debit note's: why

    @property
    def amount_due_minor(self) -> int:
        if self.status in ("draft", "cancelled"):
            return 0
        return self.total_minor - self.amount_paid_minor - self.amount_credited_minor

    @property
    def base_due_minor(self) -> int:
        """What is still due, in the organization's currency (the receivable's balance for this invoice)."""
        if self.status in ("draft", "cancelled"):
            return 0
        return self.base_total_minor - self.base_settled_minor


class InvoiceLine(IdMixin, TenantScoped, Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (
        org_unique_id(),  # credit note lines point at the invoice line they credit
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
        org_unique_id(),  # online payment links point at the payment they made
        same_org_fk("invoice_id", "invoices"),
        same_org_fk("journal_entry_id", "journal_entries"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_payment_org_idempotency"),
        CheckConstraint("amount_minor > 0", name="ck_payment_amount_positive"),
    )

    invoice_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)  # bank | cash | online (a payment link)
    reference: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    journal_entry_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    # A payment of an invoice in another currency: the rate on the payment date, what reached the bank in the
    # organization's currency, and the exchange gain (+) or loss (-) against the invoice's booked rate.
    exchange_rate: Mapped[Decimal] = mapped_column(ExactDecimal(18, 8), default=Decimal(1), server_default="1", nullable=False)
    base_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    fx_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)


class CreditNote(IdMixin, TimestampMixin, TenantScoped, Base):
    """Credits part or all of a posted invoice: fewer units, a price allowance, or goods returned.

    The amount first reduces what is still due on the invoice (``applied_minor``); anything beyond
    that is owed back to the customer and paid out with refunds (``refunded_minor``).
    """

    __tablename__ = "credit_notes"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "number", name="uq_credit_note_org_number"),
        same_org_fk("invoice_id", "invoices"),
        same_org_fk("customer_id", "customers"),
        same_org_fk("journal_entry_id", "journal_entries"),
        CheckConstraint("total_minor > 0", name="ck_credit_note_total_positive"),
        CheckConstraint("applied_minor >= 0 AND refunded_minor >= 0 AND applied_minor + refunded_minor <= total_minor",
                        name="ck_credit_note_settled_range"),
    )

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    invoice_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    applied_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    refunded_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    journal_entry_id: Mapped[str] = mapped_column(String(36), nullable=False)
    stock_reference: Mapped[str] = mapped_column(String(40), default="", nullable=False)  # goods returned
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    # In the organization's currency, at the invoice's rate: the whole note, the part that settled the invoice,
    # and what has been refunded (so the last refund leaves nothing behind).
    base_total_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    base_applied_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    base_refunded_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)


class CreditNoteLine(IdMixin, TenantScoped, Base):
    __tablename__ = "credit_note_lines"
    __table_args__ = (
        same_org_fk("credit_note_id", "credit_notes", ondelete="CASCADE"),
        same_org_fk("invoice_line_id", "invoice_lines"),
        same_org_fk("product_id", "products"),
        same_org_fk("tax_id", "taxes"),
        CheckConstraint("CAST(quantity AS NUMERIC) > 0", name="ck_credit_line_qty_positive"),
        CheckConstraint("CAST(returned_quantity AS NUMERIC) >= 0 AND CAST(returned_quantity AS NUMERIC) <= CAST(quantity AS NUMERIC)",
                        name="ck_credit_line_returned_range"),
    )

    credit_note_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    invoice_line_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(18, 4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(ExactDecimal(18, 6), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), default=Decimal(0), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tax_rate: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), default=Decimal(0), nullable=False)
    net_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    returned_quantity: Mapped[Decimal] = mapped_column(ExactDecimal(18, 4), default=Decimal(0), nullable=False)


class CreditNoteTax(IdMixin, TenantScoped, Base):
    """Tax credited per rate, kept so the last credit note of an invoice can credit the exact remainder."""

    __tablename__ = "credit_note_taxes"
    __table_args__ = (
        same_org_fk("credit_note_id", "credit_notes", ondelete="CASCADE"),
        same_org_fk("tax_id", "taxes"),
    )

    credit_note_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    tax_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rate: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), nullable=False)
    base_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Refund(IdMixin, TimestampMixin, TenantScoped, Base):
    """Money paid back to a customer for a credit note."""

    __tablename__ = "refunds"
    __table_args__ = (
        same_org_fk("credit_note_id", "credit_notes"),
        same_org_fk("journal_entry_id", "journal_entries"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_refund_org_idempotency"),
        CheckConstraint("amount_minor > 0", name="ck_refund_amount_positive"),
    )

    credit_note_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    refund_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)  # bank | cash
    reference: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    journal_entry_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    base_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
