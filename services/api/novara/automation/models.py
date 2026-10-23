"""Workflow automation state.

Events are written to an outbox in the same transaction as the change that caused them,
so an event exists if and only if the change committed. A workflow runs at most once
per event (unique constraint), and every step's outcome is recorded.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from novara.db import Base, IdMixin, TimestampMixin
from novara.tenancy import TenantScoped, org_unique_id, same_org_fk


class OutboxEvent(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (org_unique_id(), UniqueConstraint("organization_id", "dedup_key", name="uq_outbox_dedup"))

    type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    dedup_key: Mapped[str | None] = mapped_column(String(120), nullable=True)  # e.g. one "overdue" per invoice
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Workflow(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "workflows"
    __table_args__ = (org_unique_id(),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    trigger: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    # For trigger "schedule": {"every": "day"|"week"|"month", "time": "HH:MM", "timezone": "...",
    #                          "weekday": 0-6 (Monday = 0, weekly), "day": 1-28 (monthly)}
    schedule: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    conditions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    actions: Mapped[list] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_owner_on_failure: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Actions that act as a user (run_agent) act as the owner, with the owner's current permissions.
    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


class WorkflowRun(IdMixin, TimestampMixin, TenantScoped, Base):
    """status: queued | waiting_approval | retrying | completed | skipped | failed | rejected"""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        org_unique_id(),
        same_org_fk("workflow_id", "workflows", ondelete="CASCADE"),
        same_org_fk("event_id", "outbox_events", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "workflow_id", "event_id", name="uq_workflow_run_once_per_event"),
    )

    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False, index=True)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)  # snapshot: later edits don't change a run
    current_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowStep(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        same_org_fk("run_id", "workflow_runs", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "run_id", "index", name="uq_workflow_step_once"),
    )

    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # done | waiting_approval | approved | rejected
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Task(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "tasks"
    __table_args__ = (org_unique_id(), UniqueConstraint("organization_id", "idempotency_key", name="uq_task_idem"))

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    assignee_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"),
                                                         index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False, index=True)  # open | done
    related_type: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    related_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)


class Notification(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key", name="uq_notification_idem"),)

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
