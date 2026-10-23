"""Bookings: services people can book, the opening hours they fit in, and the bookings themselves.

The organization keeps one calendar: two live bookings (requested or confirmed) never overlap.
Times are stored in UTC and laid out in the booking time zone. A published App Studio site
holding a data key with the "bookings" scope can list free times and ask for one; the request
holds its time until someone confirms or declines it.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.money import ExactDecimal
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk


class BookingSettings(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "booking_settings"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_booking_settings_org"),
        CheckConstraint("slot_minutes IN (15, 30, 60)", name="ck_booking_slot"),
        CheckConstraint("notice_hours >= 0 AND notice_hours <= 720 AND days_ahead > 0 AND days_ahead <= 366",
                        name="ck_booking_window"),
    )

    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Riyadh", nullable=False)
    slot_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)  # how often a booking may start
    notice_hours: Mapped[int] = mapped_column(Integer, default=2, nullable=False)   # sites book at least this far ahead
    days_ahead: Mapped[int] = mapped_column(Integer, default=60, nullable=False)    # and at most this far


class OpeningHours(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "opening_hours"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_opening_weekday"),
        CheckConstraint("opens_at < closes_at", name="ck_opening_range"),
    )

    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # Monday = 0
    opens_at: Mapped[str] = mapped_column(String(5), nullable=False)   # "HH:MM" in the booking time zone
    closes_at: Mapped[str] = mapped_column(String(5), nullable=False)


class BookableService(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "bookable_services"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "name", name="uq_bookable_service_name"),
        CheckConstraint("duration_minutes > 0 AND duration_minutes <= 720", name="ck_service_duration"),
        CheckConstraint("price IS NULL OR CAST(price AS NUMERIC) >= 0", name="ck_service_price"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(ExactDecimal(18, 6), nullable=True)  # shown to visitors
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Booking(IdMixin, TimestampMixin, TenantScoped, Base):
    """status: requested | confirmed | declined | cancelled"""

    __tablename__ = "bookings"
    __table_args__ = (
        org_unique_id(),
        UniqueConstraint("organization_id", "number", name="uq_booking_org_number"),
        same_org_fk("service_id", "bookable_services"),
        same_org_fk("customer_id", "customers"),
        CheckConstraint("starts_at < ends_at", name="ck_booking_range"),
    )

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    service_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="requested", nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    note: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Where it came from: "" when the team booked it, else the published site's name. A site's
    # request also keeps its key and the sender's address (for rate limits); deleting the site
    # leaves its bookings in place.
    source: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    key_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    decided_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str] = mapped_column(String(500), default="", nullable=False)
