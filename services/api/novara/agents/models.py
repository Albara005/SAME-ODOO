"""Custom agents. A version is immutable once tested or published; edits make a new draft."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk


class Agent(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "agents"
    __table_args__ = (org_unique_id(),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # instant stop switch
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Tools switched off right now, on top of any version - revokes access without republishing.
    disabled_tools: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    monthly_budget_micros: Mapped[int] = mapped_column(BigInteger, default=5_000_000, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class AgentVersion(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        org_unique_id(),
        same_org_fk("agent_id", "agents", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "agent_id", "number", name="uq_agent_version_number"),
    )

    agent_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)  # draft | frozen
    instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tools: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # allowlist of tool names
    route: Mapped[str] = mapped_column(String(40), default="assistant", nullable=False)
    max_steps: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    max_cost_micros: Mapped[int] = mapped_column(BigInteger, default=200_000, nullable=False)  # per run
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))


class AgentTestCase(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "agent_test_cases"
    __table_args__ = (org_unique_id(), same_org_fk("agent_id", "agents", ondelete="CASCADE"))

    agent_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    expect_tools: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    forbid_tools: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    reply_contains: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class AgentRun(IdMixin, TimestampMixin, TenantScoped, Base):
    """One execution of an agent version: a test, a manual run, or a workflow step."""

    __tablename__ = "agent_runs"
    __table_args__ = (same_org_fk("agent_id", "agents", ondelete="CASCADE"),)

    agent_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)  # test | manual | workflow
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    test_case_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    reply: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stop: Mapped[str] = mapped_column(String(30), default="", nullable=False)
    tool_calls: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # [{name, status}]
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # tests only
    failures: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
