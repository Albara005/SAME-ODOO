"""Metadata for files kept in object storage."""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk


class StoredFile(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "files"
    __table_args__ = (org_unique_id(),)  # passages point at their file

    storage_key: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), default="application/octet-stream", nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    # Text for search (see text.py): pending | ready | none (no readable text) | failed (try again)
    text_status: Mapped[str] = mapped_column(String(10), default="pending", server_default="pending", nullable=False)
    text_chars: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)


class FileChunk(IdMixin, TenantScoped, Base):
    """A passage of a file's text, with its normalised words for ranked search."""

    __tablename__ = "file_chunks"
    __table_args__ = (same_org_fk("file_id", "files", ondelete="CASCADE"),)

    file_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    terms: Mapped[str] = mapped_column(Text, nullable=False)
