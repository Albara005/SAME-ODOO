"""Customers (business partners)."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped, org_unique_id


class Customer(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "customers"
    __table_args__ = (org_unique_id(),)

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="company", nullable=False)  # company | individual
    email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    tax_number: Mapped[str] = mapped_column(String(40), default="", nullable=False)  # VAT / TRN
    commercial_registration: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    address_line: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    city: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    postal_code: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    country: Mapped[str] = mapped_column(String(2), default="", nullable=False)  # ISO 3166-1 alpha-2
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
