"""Append-only record of sensitive operations."""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped


class AuditLog(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "audit_logs"

    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
