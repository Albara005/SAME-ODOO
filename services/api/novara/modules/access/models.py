"""Roles: named sets of permissions, defined per organization."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped


class Role(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="uq_role_org_key"),)

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
