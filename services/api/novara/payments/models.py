"""Online payment settings per organization, and the payment links sent to customers."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped, same_org_fk

PROVIDERS = ("none", "stripe", "test")
LINK_STATUSES = ("open", "paid", "unapplied", "expired", "cancelled")


class PaymentSettings(IdMixin, TimestampMixin, TenantScoped, Base):
    """Which provider takes the organization's online payments. Secrets are encrypted (crypto.py), bound to the
    organization, and never shown again; the webhook address carries a token looked up by its SHA-256."""

    __tablename__ = "payment_settings"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_payment_settings_org"),
        CheckConstraint("provider IN ('none', 'stripe', 'test')", name="ck_payment_settings_provider"),
    )

    provider: Mapped[str] = mapped_column(String(10), default="none", nullable=False)
    stripe_secret_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    stripe_key_hint: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    stripe_webhook_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_token: Mapped[str] = mapped_column(Text, nullable=False)
    webhook_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class PaymentLink(IdMixin, TimestampMixin, TenantScoped, Base):
    """A checkout page for (part of) what is due on one invoice. Paid once, then it's done.

    "unapplied": the provider took the money but the invoice couldn't take it (paid another way meanwhile,
    cancelled, or a different amount); the person who made the link is told, nothing is posted."""

    __tablename__ = "payment_links"
    __table_args__ = (
        same_org_fk("invoice_id", "invoices"),
        same_org_fk("payment_id", "payments"),
        UniqueConstraint("provider", "session_id", name="uq_payment_link_session"),
        CheckConstraint("amount_minor > 0", name="ck_payment_link_amount_positive"),
        CheckConstraint("status IN ('open', 'paid', 'unapplied', 'expired', 'cancelled')", name="ck_payment_link_status"),
    )

    invoice_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="open", nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider_reference: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    note: Mapped[str] = mapped_column(String(300), default="", nullable=False)  # why it's unapplied
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
