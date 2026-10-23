"""Organizations and user membership in them."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped

HOSTING_MODES = ("shared", "dedicated_database", "dedicated_environment")


class Organization(IdMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    # Default is shared hosting with strong isolation. Moving to a dedicated
    # database/environment is an explicit, approved operation (see docs).
    hosting_mode: Mapped[str] = mapped_column(String(32), default="shared", nullable=False)
    default_locale: Mapped[str] = mapped_column(String(10), default="ar", nullable=False)
    default_currency: Mapped[str] = mapped_column(String(3), default="SAR", nullable=False)


class Membership(IdMixin, TimestampMixin, TenantScoped, Base):
    """A user's role inside one organization. Roles never carry across organizations."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),)

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[str] = mapped_column(String(36), ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active | suspended


class Invitation(IdMixin, TimestampMixin, TenantScoped, Base):
    """An invitation by email to join an organization with a role; works for people without an account.
    The link carries a random token; only its hash is stored."""

    __tablename__ = "invitations"

    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role_id: Mapped[str] = mapped_column(String(36), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
