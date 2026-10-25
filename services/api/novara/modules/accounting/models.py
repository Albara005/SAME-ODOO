"""Double-entry ledger: chart of accounts, tax rates, journal entries and document numbering."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.money import ExactDecimal
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk

ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")


class Account(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "code", name="uq_account_org_code"),
        UniqueConstraint("organization_id", "system_key", name="uq_account_org_system_key"),
    )

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Accounts the platform posts to automatically (receivable, sales, vat_output, bank, cash...).
    system_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Tax(IdMixin, TimestampMixin, TenantScoped, Base):
    """A sales tax rate. The rate is fixed once created; a new rate means a new tax record."""

    __tablename__ = "taxes"
    __table_args__ = (
        org_unique_id(),
        same_org_fk("account_id", "accounts"),
        CheckConstraint("CAST(rate AS NUMERIC) >= 0 AND CAST(rate AS NUMERIC) <= 100", name="ck_tax_rate_range"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rate: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), nullable=False)  # percent
    account_id: Mapped[str] = mapped_column(String(36), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class NumberSequence(IdMixin, TenantScoped, Base):
    """Gap-free document numbering per organization (e.g. invoices per year)."""

    __tablename__ = "number_sequences"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="uq_sequence_org_key"),)

    key: Mapped[str] = mapped_column(String(60), nullable=False)
    next_value: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class JournalEntry(IdMixin, TimestampMixin, TenantScoped, Base):
    """A posted, balanced set of journal lines. Never edited; corrected by a reversing entry."""

    __tablename__ = "journal_entries"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "number", name="uq_journal_org_number"),
        same_org_fk("reversal_of_id", "journal_entries"),
        # An entry is reversed at most once, however many requests try at the same moment.
        Index("uq_journal_entry_reversed_once", "organization_id", "reversal_of_id", unique=True,
              postgresql_where=text("reversal_of_id IS NOT NULL"), sqlite_where=text("reversal_of_id IS NOT NULL")),
    )

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    memo: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    reversal_of_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class JournalLine(IdMixin, TenantScoped, Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        same_org_fk("entry_id", "journal_entries", ondelete="CASCADE"),
        same_org_fk("account_id", "accounts"),
        same_org_fk("customer_id", "customers"),
        same_org_fk("vendor_id", "vendors"),
        CheckConstraint(
            "debit_minor >= 0 AND credit_minor >= 0 AND (debit_minor = 0) <> (credit_minor = 0)",
            name="ck_journal_line_one_side",
        ),
    )

    entry_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    debit_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    credit_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    vendor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(300), default="", nullable=False)


class PeriodClose(IdMixin, TimestampMixin, TenantScoped, Base):
    """A month closed (the books locked up to its last day) or reopened, with who did it and why.
    Append-only: the lock date says what is closed now; these rows say how it got there."""

    __tablename__ = "period_closes"
    __table_args__ = (CheckConstraint("action IN ('closed', 'reopened')", name="ck_period_close_action"),)

    period_end: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    # What was still a draft in the month when it was closed: {"invoices": n, "bills": n}.
    drafts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
