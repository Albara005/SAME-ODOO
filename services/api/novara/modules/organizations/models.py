"""Organizations and user membership in them."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
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
