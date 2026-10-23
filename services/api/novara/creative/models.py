"""Brand kits and their logos."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk


class BrandKit(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "brand_kits"
    __table_args__ = (org_unique_id(),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    tagline: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    primary_color: Mapped[str] = mapped_column(String(7), nullable=False)
    secondary_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    font_pair: Mapped[str] = mapped_column(String(30), default="kufi", nullable=False)
    # Derived from the colours; regenerated whenever they change.
    system: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class BrandLogo(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "brand_logos"
    __table_args__ = (org_unique_id(), same_org_fk("brand_kit_id", "brand_kits", ondelete="CASCADE"))

    brand_kit_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="user", nullable=False)  # user | assistant
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
