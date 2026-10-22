"""Products and services that can be sold."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.money import ExactDecimal
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk


class Product(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "products"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "sku", name="uq_product_org_sku"),
        same_org_fk("default_tax_id", "taxes"),
        CheckConstraint("CAST(sale_price AS NUMERIC) >= 0", name="ck_product_price_nonnegative"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="goods", nullable=False)  # goods | service
    unit: Mapped[str] = mapped_column(String(20), default="unit", nullable=False)
    sale_price: Mapped[Decimal] = mapped_column(ExactDecimal(18, 6), default=Decimal(0), nullable=False)
    default_tax_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
