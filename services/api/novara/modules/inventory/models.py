"""Warehouses, stock levels and stock moves (perpetual inventory at weighted average cost)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.money import ExactDecimal
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk


class Warehouse(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "warehouses"
    __table_args__ = (org_unique_id(), UniqueConstraint("organization_id", "code", name="uq_warehouse_org_code"))

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StockLevel(IdMixin, TenantScoped, Base):
    """Current quantity and total value of one product in one warehouse.

    Average unit cost is value / quantity; storing the total value (not the
    average) means stock that goes to zero also goes to exactly zero value.
    """

    __tablename__ = "stock_levels"
    __table_args__ = (
        UniqueConstraint("organization_id", "product_id", "warehouse_id", name="uq_stock_level"),
        same_org_fk("product_id", "products"),
        same_org_fk("warehouse_id", "warehouses"),
        CheckConstraint("CAST(quantity AS NUMERIC) >= 0", name="ck_stock_level_nonnegative"),
        CheckConstraint("value_minor >= 0", name="ck_stock_value_nonnegative"),
    )

    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(String(36), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(18, 4), default=Decimal(0), nullable=False)
    value_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class StockMove(IdMixin, TimestampMixin, TenantScoped, Base):
    """One change to a stock level. Append-only: the history is the source of truth."""

    __tablename__ = "stock_moves"
    __table_args__ = (
        same_org_fk("product_id", "products"),
        same_org_fk("warehouse_id", "warehouses"),
        same_org_fk("journal_entry_id", "journal_entries"),
        CheckConstraint("CAST(quantity AS NUMERIC) <> 0", name="ck_stock_move_nonzero"),
    )

    reference: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # receipt | delivery | adjustment | return | vendor_return
    product_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    warehouse_id: Mapped[str] = mapped_column(String(36), nullable=False)
    move_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(18, 4), nullable=False)  # signed: + in, - out
    value_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)  # signed, same sign as quantity
    source_type: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    source_line_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    journal_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
