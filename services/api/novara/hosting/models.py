"""A request for a dedicated database or environment (plan section 11.2).

Flow: requested (with a cost quote) -> approved by an owner who confirms the monthly cost
-> picked up by platform operators -> delivered (the organization's hosting mode changes)
or rejected. Nothing is provisioned automatically and nothing is created without the
owner's explicit approval.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped


class HostingRequest(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "hosting_requests"

    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # dedicated_database | dedicated_environment
    size: Mapped[str] = mapped_column(String(20), nullable=False)  # small | medium | large
    region: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    quoted_monthly_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # pending_approval | approved | provisioning | delivered | rejected | cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending_approval", nullable=False, index=True)
    requested_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    approved_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    operator_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
