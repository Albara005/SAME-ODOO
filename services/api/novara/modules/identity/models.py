"""User accounts and login sessions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active | disabled
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locale: Mapped[str] = mapped_column(String(10), default="ar", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Riyadh", nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class UserSession(IdMixin, TimestampMixin, Base):
    """A login on one device. Tokens reference it so it can be revoked."""

    __tablename__ = "user_sessions"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
