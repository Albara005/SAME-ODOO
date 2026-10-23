"""Quotations and sales orders (one record; a quotation becomes an order when confirmed)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.money import ExactDecimal
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk

ORDER_STATUSES = ("quotation", "confirmed", "cancelled")


class SalesOrder(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "sales_orders"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "number", name="uq_sales_order_org_number"),
        same_org_fk("customer_id", "customers"),
        same_org_fk("warehouse_id", "warehouses"),
    )

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="quotation", nullable=False, index=True)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    warehouse_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class SalesOrderLine(IdMixin, TenantScoped, Base):
    __tablename__ = "sales_order_lines"
    __table_args__ = (
        org_unique_id(),
        same_org_fk("order_id", "sales_orders", ondelete="CASCADE"),
        same_org_fk("product_id", "products"),
        same_org_fk("tax_id", "taxes"),
        CheckConstraint("CAST(quantity AS NUMERIC) > 0", name="ck_so_line_qty_positive"),
        CheckConstraint("CAST(unit_price AS NUMERIC) >= 0", name="ck_so_line_price_nonnegative"),
        CheckConstraint("CAST(discount_percent AS NUMERIC) >= 0 AND CAST(discount_percent AS NUMERIC) <= 100",
                        name="ck_so_line_discount"),
        CheckConstraint("CAST(qty_delivered AS NUMERIC) >= 0 AND CAST(qty_delivered AS NUMERIC) <= CAST(quantity AS NUMERIC)",
                        name="ck_so_line_delivered_range"),
    )

    order_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(18, 4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(ExactDecimal(18, 6), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), default=Decimal(0), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tax_rate: Mapped[Decimal] = mapped_column(ExactDecimal(7, 4), default=Decimal(0), nullable=False)
    net_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    qty_delivered: Mapped[Decimal] = mapped_column(ExactDecimal(18, 4), default=Decimal(0), nullable=False)


class WebOrder(IdMixin, TimestampMixin, TenantScoped, Base):
    """An order request a visitor sent from a published App Studio site (see sales/web_orders.py).
    Nothing is reserved or posted: a person turns it into a quotation, or dismisses it."""

    __tablename__ = "web_orders"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "number", name="uq_web_order_org_number"),
        same_org_fk("project_id", "app_projects", ondelete="CASCADE"),
        same_org_fk("key_id", "app_data_keys", ondelete="CASCADE"),
        same_org_fk("sales_order_id", "sales_orders"),
        same_org_fk("customer_id", "customers"),
    )

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    key_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False, index=True)  # new | converted | dismissed
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # [{product_id, name, sku, quantity, unit_price, tax_rate}] as the catalog stood when it arrived
    lines: Mapped[list] = mapped_column(JSON, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    sales_order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    handled_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
