"""The organization's e-invoicing device (EGS unit) and the signed documents it issued."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.einvoice.ubl import FIRST_PIH
from novara.tenancy import TenantScoped

ENVIRONMENTS = ("sandbox", "simulation", "production")
DOC_STATUSES = ("pending", "reported", "cleared", "rejected")


class EInvoiceSettings(IdMixin, TimestampMixin, TenantScoped, Base):
    """Seller details ZATCA needs, the device's key and certificates (encrypted), and the document chain."""

    __tablename__ = "einvoice_settings"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_einvoice_settings_org"),
        CheckConstraint("environment IN ('sandbox', 'simulation', 'production')", name="ck_einvoice_environment"),
        CheckConstraint("status IN ('off', 'onboarded')", name="ck_einvoice_status"),
    )

    environment: Mapped[str] = mapped_column(String(12), default="sandbox", nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="off", nullable=False)
    crn: Mapped[str] = mapped_column(String(20), default="", nullable=False)  # commercial registration number
    street: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    building_number: Mapped[str] = mapped_column(String(10), default="", nullable=False)
    district: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    city: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    postal_code: Mapped[str] = mapped_column(String(10), default="", nullable=False)
    branch_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    industry: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    private_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    compliance_request_id: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    compliance_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    compliance_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    production_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    production_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    certificate_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    onboarded_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    # The chain: the last counter value (ICV) used and the last document's hash (the next one's PIH).
    icv: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_hash: Mapped[str] = mapped_column(String(100), default=FIRST_PIH, nullable=False)
    last_error: Mapped[str] = mapped_column(String(500), default="", nullable=False)


class EInvoiceDocument(IdMixin, TimestampMixin, TenantScoped, Base):
    """A signed document as issued: its XML, hash and QR never change; only what ZATCA said about it does."""

    __tablename__ = "einvoice_documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "source_type", "source_id", name="uq_einvoice_document_source"),
        UniqueConstraint("organization_id", "uuid", name="uq_einvoice_document_uuid"),
        CheckConstraint("source_type IN ('invoice', 'credit_note')", name="ck_einvoice_document_source"),
        CheckConstraint("kind IN ('standard', 'simplified')", name="ck_einvoice_document_kind"),
        CheckConstraint("status IN ('pending', 'reported', 'cleared', 'rejected')", name="ck_einvoice_document_status"),
    )

    source_type: Mapped[str] = mapped_column(String(12), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    type_code: Mapped[str] = mapped_column(String(3), nullable=False)    # 388 invoice, 381 credit note
    environment: Mapped[str] = mapped_column(String(12), nullable=False)
    icv: Mapped[int] = mapped_column(Integer, nullable=False)
    uuid: Mapped[str] = mapped_column(String(36), nullable=False)
    invoice_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    xml: Mapped[str] = mapped_column(Text, nullable=False)
    qr: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False, index=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    errors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    note: Mapped[str] = mapped_column(String(300), default="", nullable=False)   # e.g. "ZATCA didn't answer"
    cleared_xml: Mapped[str | None] = mapped_column(Text, nullable=True)        # standard: as stamped by ZATCA
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
