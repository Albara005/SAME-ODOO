"""Metadata for files kept in object storage."""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped


class StoredFile(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "files"

    storage_key: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), default="application/octet-stream", nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
