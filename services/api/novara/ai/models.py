"""Assistant state: settings, conversations, tool calls (with approvals) and usage."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk


DEFAULT_BUDGET_MICROS = 20_000_000  # 20 USD a month


class AISettings(IdMixin, TimestampMixin, TenantScoped, Base):
    """Per-organization switch, budget and model choices. Absent row = defaults."""

    __tablename__ = "ai_settings"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_ai_settings_org"),)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Monthly spend cap in micro-USD (1 USD = 1_000_000). 0 means no AI spend allowed.
    monthly_budget_micros: Mapped[int] = mapped_column(BigInteger, default=DEFAULT_BUDGET_MICROS, nullable=False)
    # Optional per-route overrides: {"assistant": {"model": "...", "effort": "..."}}
    routes: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Optional cap per member per month (micro-USD); empty means only the organization budget applies.
    user_monthly_budget_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Conversation(IdMixin, TimestampMixin, TenantScoped, Base):
    """A conversation belongs to one user; other members (admins included) can't read it."""

    __tablename__ = "ai_conversations"
    __table_args__ = (org_unique_id(),)

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)


class ConversationMessage(IdMixin, TimestampMixin, TenantScoped, Base):
    """API-format message (role + content blocks). Append-only: history is never edited,
    so model thinking blocks stay valid across turns."""

    __tablename__ = "ai_messages"
    __table_args__ = (
        same_org_fk("conversation_id", "ai_conversations", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "conversation_id", "seq", name="uq_ai_message_seq"),
    )

    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    content: Mapped[list] = mapped_column(JSON, nullable=False)


class ToolCall(IdMixin, TimestampMixin, TenantScoped, Base):
    """Every tool the model asked for, and what happened to it.

    status: executed | denied | failed | pending_approval | approved | rejected | approval_failed
    Write tools never run on the model's request alone: they wait for the user to approve.
    """

    __tablename__ = "ai_tool_calls"
    __table_args__ = (same_org_fk("conversation_id", "ai_conversations", ondelete="CASCADE"),)

    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tool_use_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # read | write | unknown
    arguments: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once the model has been told the outcome of an approval decision.
    reported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ModelUsage(IdMixin, TimestampMixin, TenantScoped, Base):
    """One model request: tokens, cost, latency, outcome."""

    __tablename__ = "ai_usage"

    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    route: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stop_reason: Mapped[str] = mapped_column(String(30), default="", nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)


class AssistantMemory(IdMixin, TimestampMixin, TenantScoped, Base):
    """A note a user asked their assistant to remember. Saved only once the user approved it,
    used only in that user's own conversations, and deletable by them at any time."""

    __tablename__ = "ai_memories"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
