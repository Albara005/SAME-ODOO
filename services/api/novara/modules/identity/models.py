"""User accounts and login sessions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text
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
    # Two-factor (TOTP) secrets, encrypted (novara/crypto.py). The pending one exists only during setup.
    mfa_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_pending_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Last time step accepted, so a code can't be used twice.
    mfa_last_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class UserSession(IdMixin, TimestampMixin, Base):
    """A login on one device. Tokens reference it so it can be revoked."""

    __tablename__ = "user_sessions"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)


class LoginAttempt(IdMixin, TimestampMixin, Base):
    """Sign-in attempts, for limiting password guessing per email and per address."""

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_email_created", "email", "created_at"),
                      Index("ix_login_attempts_ip_created", "ip_address", "created_at"))

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)


class UserToken(IdMixin, TimestampMixin, Base):
    """One-time tokens sent by email (verify the address, reset the password). Only the hash is kept."""

    __tablename__ = "user_tokens"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)  # verify_email | reset_password
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecoveryCode(IdMixin, TimestampMixin, Base):
    """Single-use codes for signing in when the authenticator app is lost. Only the hash is kept."""

    __tablename__ = "mfa_recovery_codes"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
