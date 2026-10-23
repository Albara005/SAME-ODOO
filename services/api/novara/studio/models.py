"""App projects, their working copy, immutable versions and deployments.

Files are content-addressed: each distinct file body is stored once in object storage
under its SHA-256, and a version is a manifest {path: sha256}. Versions are never
changed; editing happens in the project's working copy until a new version is cut.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk


class AppProject(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "app_projects"
    __table_args__ = (org_unique_id(), UniqueConstraint("organization_id", "slug", name="uq_app_project_slug"))

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(60), nullable=False)
    template: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Working copy: {path: sha256}. Edited freely; versions snapshot it.
    working_files: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    base_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class AppVersion(IdMixin, TimestampMixin, TenantScoped, Base):
    """An immutable snapshot of a project's files, with the result of its checks."""

    __tablename__ = "app_versions"
    __table_args__ = (
        org_unique_id(),
        same_org_fk("project_id", "app_projects", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "project_id", "number", name="uq_app_version_number"),
    )

    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    files: Mapped[dict] = mapped_column(JSON, nullable=False)  # {path: sha256}
    checks: Mapped[list] = mapped_column(JSON, nullable=False)  # [{check, status, message, path}]
    checks_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    author_kind: Mapped[str] = mapped_column(String(20), default="user", nullable=False)  # user | assistant
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class AppDeployment(IdMixin, TimestampMixin, TenantScoped, Base):
    """A version published to an environment. Production deployments need a separate approval."""

    __tablename__ = "app_deployments"
    __table_args__ = (
        same_org_fk("project_id", "app_projects", ondelete="CASCADE"),
        same_org_fk("version_id", "app_versions"),
    )

    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    environment: Mapped[str] = mapped_column(String(20), nullable=False)  # preview | production
    site_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # preview: live | offline; production: pending_approval -> live -> superseded, or rejected / offline
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deployed_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class SiteRoute(Base):
    """Public URL key -> deployment. Deliberately not tenant-scoped: serving a site must find
    its organization before any tenant is bound. It holds routing data only, never content.

    Production keeps one key per project (re-pointed on each deploy or rollback);
    every preview gets its own key.
    """

    __tablename__ = "app_site_routes"

    site_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"),
                                                 index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(20), nullable=False)
    deployment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AppDataKey(IdMixin, TimestampMixin, TenantScoped, Base):
    """A publishable key a project's site uses to read a fixed, public view of business data.

    Published sites are public, so a key embedded in one is public too: it can only ever read
    the scopes chosen here (the product catalog, optionally whether each product is in stock),
    never costs, quantities, customers or anything else; with the "orders" scope it can also
    send order requests, which wait for a person in Sales. Only its SHA-256 is stored.
    """

    __tablename__ = "app_data_keys"
    __table_args__ = (
        org_unique_id(),  # order requests point at the key they came with
        same_org_fk("project_id", "app_projects", ondelete="CASCADE"),
        UniqueConstraint("key_hash", name="uq_app_data_key_hash"),
    )

    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hint: Mapped[str] = mapped_column(String(16), nullable=False)  # the first characters, to tell keys apart
    scopes: Mapped[list] = mapped_column(JSON, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
