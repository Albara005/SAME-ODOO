"""Workflow engine: match events to workflows, then run each workflow's steps.

Guarantees:
* a workflow runs at most once per event (unique constraint on the run);
* each run is processed in its own transaction: its actions and its step records commit
  together, so a crash or error never leaves half an action behind;
* steps that already succeeded are skipped on retry, and actions that create records carry
  an idempotency key (run id + step), so retries never duplicate tasks or notifications;
* failures retry with exponential backoff (1, 2, 4 minutes), then the run fails and the
  workflow owner is notified;
* an approval step pauses the run until someone holding the named permission approves.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.automation.events import EVENT_TYPES
from novara.automation.models import Notification, OutboxEvent, Task, Workflow, WorkflowRun, WorkflowStep
from novara.db import utcnow
from novara.modules.access import permissions as P
from novara.modules.access.models import Role
from novara.modules.identity.models import User
from novara.modules.organizations.models import Membership

MAX_ATTEMPTS = 3
MAX_ACTIONS = 20
OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "exists")
_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")


class WorkflowError(ValueError):
    """Invalid workflow definition (HTTP 422)."""


class ActionFailed(RuntimeError):
    pass


# ---------------------------------------------------------------- definitions


def _lookup(payload: dict, path: str):
    value = payload
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def render(template: str, payload: dict) -> str:
    """Fill {{field}} placeholders from the event. Only lookups, never code."""
    return _TEMPLATE.sub(lambda m: "" if (v := _lookup(payload, m.group(1))) is None else str(v), template or "")


def _number(v) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def condition_holds(cond: dict, payload: dict) -> bool:
    actual, expected, op = _lookup(payload, cond["field"]), cond.get("value"), cond["op"]
    if op == "exists":
        return actual is not None
    if actual is None:
        return False
    if op in ("gt", "gte", "lt", "lte"):
        a, b = _number(actual), _number(expected)
        if a is None or b is None:
            return False
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    if op in ("eq", "ne"):
        a, b = _number(actual), _number(expected)
        same = (a == b) if a is not None and b is not None else str(actual) == str(expected)
        return same if op == "eq" else not same
    if op == "contains":
        return str(expected).lower() in str(actual).lower()
    if op == "in":
        return isinstance(expected, list) and str(actual) in [str(x) for x in expected]
    return False


ACTION_SPECS: dict[str, dict] = {
    "create_task": {"required": ["title"], "optional": ["description", "assignee", "due_in_days"]},
    "notify": {"required": ["title"], "optional": ["body", "to"]},
    "require_approval": {"required": ["permission"], "optional": ["message"]},
    "run_agent": {"required": ["agent_id", "input"], "optional": []},
    # POST the event as signed JSON to an https endpoint on the public internet (see webhooks.py).
    "webhook": {"required": ["url"], "optional": []},
}
SCHEDULE_EVERY = ("day", "week", "month")


def validate_schedule(schedule: dict | None) -> dict:
    if not isinstance(schedule, dict):
        raise WorkflowError("a scheduled workflow needs a schedule")
    if set(schedule) - {"every", "time", "timezone", "weekday", "day"}:
        raise WorkflowError("schedule fields are every, time, timezone, weekday and day")
    every, at, tz = schedule.get("every"), schedule.get("time", ""), schedule.get("timezone", "")
    if every not in SCHEDULE_EVERY:
        raise WorkflowError(f"schedule.every must be one of {SCHEDULE_EVERY}")
    if not (isinstance(at, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at)):
        raise WorkflowError("schedule.time must be HH:MM (24-hour)")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise WorkflowError(f"unknown time zone: {tz!r}")
    out = {"every": every, "time": at, "timezone": tz}
    if every == "week":
        if not (isinstance(schedule.get("weekday"), int) and 0 <= schedule["weekday"] <= 6):
            raise WorkflowError("a weekly schedule needs weekday 0-6 (Monday = 0)")
        out["weekday"] = schedule["weekday"]
    if every == "month":
        if not (isinstance(schedule.get("day"), int) and 1 <= schedule["day"] <= 28):
            raise WorkflowError("a monthly schedule needs day 1-28 (every month has them)")
        out["day"] = schedule["day"]
    return out


def validate(db: Session, *, trigger: str, conditions: list, actions: list, schedule: dict | None = None) -> dict | None:
    """Raises WorkflowError for an invalid definition; returns the normalised schedule (or None)."""
    from novara.agents.models import Agent
    from novara.automation.webhooks import UnsafeDestination, check_url

    if trigger not in EVENT_TYPES:
        raise WorkflowError(f"unknown trigger: {trigger}")
    if trigger == "schedule":
        schedule = validate_schedule(schedule)
    elif schedule is not None:
        raise WorkflowError("only workflows with the schedule trigger take a schedule")
    for c in conditions:
        if not isinstance(c, dict) or set(c) - {"field", "op", "value"} or "field" not in c or c.get("op") not in OPS:
            raise WorkflowError(f"invalid condition {c!r}: needs field and op (one of {OPS})")
    if not 1 <= len(actions) <= MAX_ACTIONS:
        raise WorkflowError(f"a workflow needs 1 to {MAX_ACTIONS} actions")
    for a in actions:
        spec = ACTION_SPECS.get(a.get("type")) if isinstance(a, dict) else None
        if spec is None:
            raise WorkflowError(f"unknown action: {a!r}; choose from {sorted(ACTION_SPECS)}")
        keys = set(a) - {"type"}
        if missing := set(spec["required"]) - keys:
            raise WorkflowError(f"{a['type']}: missing {sorted(missing)}")
        if extra := keys - set(spec["required"]) - set(spec["optional"]):
            raise WorkflowError(f"{a['type']}: unknown fields {sorted(extra)}")
        if a["type"] == "require_approval" and a["permission"] not in P.CATALOG:
            raise WorkflowError(f"unknown permission: {a['permission']}")
        if a["type"] == "run_agent":
            if trigger == "agent.run_failed":
                raise WorkflowError("a workflow triggered by agent failures can't run agents (it could loop)")
            agent = db.get(Agent, a["agent_id"])
            if agent is None or agent.published_version is None:
                raise WorkflowError("run_agent needs a published agent of this organization")
        if a["type"] == "create_task" and "due_in_days" in a and not (
                isinstance(a["due_in_days"], int) and 0 <= a["due_in_days"] <= 365):
            raise WorkflowError("due_in_days must be a whole number of days between 0 and 365")
        if a["type"] == "webhook":
            try:
                check_url(a["url"] if isinstance(a["url"], str) else "")
            except UnsafeDestination as e:
                raise WorkflowError(f"webhook: {e}")
    return schedule


# ---------------------------------------------------------------- actions


def _resolve_user(db: Session, who: str | None, payload: dict, workflow_owner: str) -> list[str]:
    """'owner' (default), 'actor' (who caused the event), 'role:<key>', or a user id - members only."""
    who = who or "owner"
    if who == "owner":
        ids = [workflow_owner]
    elif who == "actor":
        ids = [payload.get("actor_user_id") or workflow_owner]
    elif who.startswith("role:"):
        ids = list(db.scalars(select(Membership.user_id).join(Role, Role.id == Membership.role_id)
                              .where(Role.key == who[5:], Membership.status == "active")))
    else:
        ids = [who]
    members = set(db.scalars(select(Membership.user_id).where(Membership.user_id.in_(ids),
                                                              Membership.status == "active")))
    return [i for i in ids if i in members]


def _create_task(db, run, index, action, payload, today, **_):
    key = f"wf:{run.id}:{index}"
    if db.scalar(select(Task.id).where(Task.idempotency_key == key)):
        return {"task": "exists"}
    assignees = _resolve_user(db, action.get("assignee"), payload, run.definition["owner_user_id"])
    due = today + timedelta(days=action["due_in_days"]) if "due_in_days" in action else None
    task = Task(title=render(action["title"], payload)[:300], description=render(action.get("description", ""), payload),
                assignee_user_id=assignees[0] if assignees else None, due_date=due,
                related_type=str(payload.get("target_type", "")), related_id=str(payload.get("target_id", "")),
                source=f"workflow:{run.workflow_id}", idempotency_key=key)
    db.add(task)
    db.flush()
    return {"task_id": task.id, "assignee": task.assignee_user_id}


def _notify(db, run, index, action, payload, **_):
    recipients = _resolve_user(db, action.get("to"), payload, run.definition["owner_user_id"])
    created = 0
    for uid in recipients:
        key = f"wf:{run.id}:{index}:{uid}"
        if db.scalar(select(Notification.id).where(Notification.idempotency_key == key)):
            continue
        db.add(Notification(user_id=uid, title=render(action["title"], payload)[:300],
                            body=render(action.get("body", ""), payload), idempotency_key=key))
        created += 1
    db.flush()
    return {"notified": len(recipients), "new": created}


def _run_agent(db, run, index, action, payload, provider, **_):
    from novara.agents import service as agents
    from novara.agents.models import Agent
    from novara.deps import member_context

    agent = db.get(Agent, action["agent_id"])
    if agent is None or agent.published_version is None:
        raise ActionFailed("the agent no longer exists or isn't published")
    owner = db.get(User, run.definition["owner_user_id"])
    ctx = member_context(db, run.organization_id, owner) if owner else None
    if ctx is None:
        raise ActionFailed("the workflow owner is no longer an active member")
    try:
        result = agents.run(db, ctx, agent, agents.get_version(db, agent, agent.published_version),
                            render(action["input"], payload), provider=provider, trigger="workflow")
    except agents.AgentDisabled as e:
        raise ActionFailed(str(e))
    if result.stop != "end_turn":
        raise ActionFailed(f"agent run stopped: {result.stop}")
    return {"agent_run_id": result.id, "reply": result.reply[:2000]}


def _webhook(db, run, index, action, payload, webhooks=None, **_):
    from novara.automation.webhooks import UnsafeDestination

    if webhooks is None:
        raise ActionFailed("webhooks are not configured on this server")
    event = db.get(OutboxEvent, run.event_id)
    body = {"event": event.type, "occurred_at": event.created_at.isoformat(), "data": payload,
            "workflow": {"id": run.workflow_id, "name": run.definition["name"]}, "run_id": run.id}
    try:
        code = webhooks.send(db, action["url"], body, idempotency_key=f"wf:{run.id}:{index}")
    except UnsafeDestination as e:
        raise ActionFailed(f"webhook refused: {e}")
    except Exception as e:  # noqa: BLE001 - network errors: retried with backoff like any failed step
        raise ActionFailed(f"webhook delivery failed: {type(e).__name__}")
    if not 200 <= code < 300:
        raise ActionFailed(f"the webhook endpoint answered HTTP {code}")
    return {"status": code}


ACTIONS = {"create_task": _create_task, "notify": _notify, "run_agent": _run_agent, "webhook": _webhook}


# ---------------------------------------------------------------- processing


def match_events(db: Session, limit: int = 200) -> int:
    """Create one queued run per (enabled workflow, pending event) and mark the events processed."""
    created = 0
    events = db.scalars(select(OutboxEvent).where(OutboxEvent.status == "pending")
                        .order_by(OutboxEvent.created_at).limit(limit)).all()
    for ev in events:
        for wf in db.scalars(select(Workflow).where(Workflow.trigger == ev.type, Workflow.enabled.is_(True))):
            if ev.type == "schedule" and ev.payload.get("workflow_id") != wf.id:
                continue  # a schedule event belongs to the one workflow whose time came
            exists = db.scalar(select(WorkflowRun.id).where(WorkflowRun.workflow_id == wf.id,
                                                            WorkflowRun.event_id == ev.id))
            if exists is None:
                db.add(WorkflowRun(workflow_id=wf.id, event_id=ev.id, status="queued", definition={
                    "name": wf.name, "conditions": wf.conditions, "actions": wf.actions,
                    "owner_user_id": wf.owner_user_id, "notify_owner_on_failure": wf.notify_owner_on_failure}))
                created += 1
        ev.status, ev.processed_at = "processed", utcnow()
    db.flush()
    return created


def execute(db: Session, run: WorkflowRun, *, provider, today: date, webhooks=None) -> None:
    """Advance one run as far as it can go. Raises on action failure (the caller rolls back)."""
    payload = db.get(OutboxEvent, run.event_id).payload
    d = run.definition
    if run.current_step == 0 and not all(condition_holds(c, payload) for c in d["conditions"]):
        run.status, run.finished_at = "skipped", utcnow()
        return
    actions = d["actions"]
    while run.current_step < len(actions):
        index, action = run.current_step, actions[run.current_step]
        if action["type"] == "require_approval":
            db.add(WorkflowStep(run_id=run.id, index=index, type=action["type"], status="waiting_approval",
                                result={"permission": action["permission"],
                                        "message": render(action.get("message", ""), payload)}))
            run.status = "waiting_approval"
            return
        result = ACTIONS[action["type"]](db, run, index, action, payload, provider=provider, today=today,
                                         webhooks=webhooks)
        db.add(WorkflowStep(run_id=run.id, index=index, type=action["type"], status="done", result=result))
        run.current_step = index + 1
        db.flush()
    run.status, run.finished_at, run.error = "completed", utcnow(), ""


def _record_failure(db: Session, run: WorkflowRun, error: str, now: datetime) -> None:
    run.attempts += 1
    run.error = error[:2000]
    if run.attempts >= MAX_ATTEMPTS:
        run.status, run.finished_at = "failed", now
        if run.definition.get("notify_owner_on_failure", True):
            db.add(Notification(user_id=run.definition["owner_user_id"],
                                title=f"Workflow '{run.definition['name']}' failed",
                                body=f"Stopped after {run.attempts} attempts: {run.error}",
                                idempotency_key=f"wf:{run.id}:failed"))
    else:
        run.status, run.next_attempt_at = "retrying", now + timedelta(minutes=2 ** (run.attempts - 1))


def process(db: Session, *, provider, now: datetime | None = None, today: date | None = None,
            webhooks=None) -> dict:
    """Process the bound organization: match new events, then run every due run.
    Commits after each run so one failing run never affects another."""
    now = now or utcnow()
    today = today or now.date()
    created = match_events(db)
    db.commit()
    due = [r.id for r in db.scalars(select(WorkflowRun).where(
        WorkflowRun.status.in_(("queued", "retrying"))).order_by(WorkflowRun.created_at))
        if r.next_attempt_at is None or _aware(r.next_attempt_at) <= now]
    stats = {"runs_created": created, "processed": 0, "failed_attempts": 0}
    for run_id in due:
        run = db.get(WorkflowRun, run_id)
        try:
            execute(db, run, provider=provider, today=today, webhooks=webhooks)
            db.commit()
        except Exception as e:  # noqa: BLE001 - any failure is recorded on the run and retried
            db.rollback()
            run = db.get(WorkflowRun, run_id)
            _record_failure(db, run, f"{type(e).__name__}: {e}", now)
            db.commit()
            stats["failed_attempts"] += 1
        stats["processed"] += 1
    return stats


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def decide(db: Session, run: WorkflowRun, *, user_id: str, approve: bool) -> WorkflowRun:
    step = db.scalar(select(WorkflowStep).where(WorkflowStep.run_id == run.id, WorkflowStep.index == run.current_step))
    step.status = "approved" if approve else "rejected"
    step.result = {**step.result, "decided_by": user_id, "decided_at": utcnow().isoformat()}
    if approve:
        run.current_step += 1
        run.status = "queued"
    else:
        run.status, run.finished_at = "rejected", utcnow()
    return run
