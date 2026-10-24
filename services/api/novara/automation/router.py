from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.automation import engine as E
from novara.automation import inbound
from novara.automation import webhooks as W
from novara.automation.events import EVENT_TYPES, scan_overdue, scan_schedules
from novara.automation.models import Notification, Task, Workflow, WorkflowRun, WorkflowStep
from novara.db import utcnow
from novara.deps import OrgContext, client_ip, get_db, org_context, require
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit

router = APIRouter(tags=["automation"])


class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    trigger: str
    # Only with trigger "schedule": {"every": "day"|"week"|"month", "time": "HH:MM", "timezone": "Asia/Riyadh",
    # "weekday": 0-6 for weekly (Monday = 0), "day": 1-28 for monthly}, {"every": "hour", "minute": 0-59, ...}
    # or {"every": "cron", "cron": "*/15 8-17 * * sun-thu", "timezone": ...}
    schedule: dict | None = None
    conditions: list[dict] = Field(default_factory=list, max_length=20)
    actions: list[dict] = Field(min_length=1, max_length=E.MAX_ACTIONS)
    enabled: bool = True
    notify_owner_on_failure: bool = True


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    schedule: dict | None = None
    conditions: list[dict] | None = None
    actions: list[dict] | None = None
    enabled: bool | None = None


class WorkflowOut(BaseModel):
    id: str
    name: str
    trigger: str
    schedule: dict | None = None
    conditions: list[dict]
    actions: list[dict]
    enabled: bool
    owner_user_id: str
    created_at: datetime


class StepOut(BaseModel):
    index: int
    type: str
    status: str
    result: dict


class RunOut(BaseModel):
    id: str
    workflow_id: str
    workflow_name: str
    status: str
    attempts: int
    error: str
    current_step: int
    steps: list[StepOut]
    created_at: datetime


class TaskOut(BaseModel):
    id: str
    title: str
    description: str
    assignee_user_id: str | None
    due_date: date | None
    status: str
    related_type: str
    related_id: str
    source: str


class TaskUpdate(BaseModel):
    status: str = Field(pattern="^(open|done)$")


class NotificationOut(BaseModel):
    id: str
    title: str
    body: str
    read: bool
    created_at: datetime


def _wf_out(w: Workflow) -> WorkflowOut:
    return WorkflowOut(id=w.id, name=w.name, trigger=w.trigger, schedule=w.schedule, conditions=w.conditions, actions=w.actions,
                       enabled=w.enabled, owner_user_id=w.owner_user_id, created_at=w.created_at)


def _run_out(db: Session, r: WorkflowRun) -> RunOut:
    steps = db.scalars(select(WorkflowStep).where(WorkflowStep.run_id == r.id).order_by(WorkflowStep.index))
    workflow = db.get(Workflow, r.workflow_id)
    return RunOut(id=r.id, workflow_id=r.workflow_id, workflow_name=workflow.name if workflow else "",
                  status=r.status, attempts=r.attempts, error=r.error,
                  current_step=r.current_step, created_at=r.created_at,
                  steps=[StepOut(index=s.index, type=s.type, status=s.status, result=s.result) for s in steps])


def _validated(fn):
    try:
        return fn()
    except E.WorkflowError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


def _workflow(db: Session, workflow_id: str) -> Workflow:
    w = db.get(Workflow, workflow_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow not found")
    return w


@router.get("/orgs/{org_id}/workflows/catalog")
def catalog(_: OrgContext = Depends(require(P.WORKFLOWS_MANAGE))):
    return {"triggers": EVENT_TYPES, "operators": E.OPS, "actions": E.ACTION_SPECS}


class SchedulePreviewIn(BaseModel):
    schedule: dict


class SchedulePreviewOut(BaseModel):
    schedule: dict           # as it would be saved
    next_runs: list[datetime]  # in the schedule's time zone


@router.post("/orgs/{org_id}/workflows/schedule-preview", response_model=SchedulePreviewOut)
def schedule_preview(body: SchedulePreviewIn, _: OrgContext = Depends(require(P.WORKFLOWS_MANAGE))):
    """Check a schedule and show when it would run next, before saving it."""
    from zoneinfo import ZoneInfo

    from novara.automation import cron
    from novara.automation.events import last_slot

    schedule = _validated(lambda: E.validate_schedule(body.schedule))
    tz, now = ZoneInfo(schedule["timezone"]), utcnow()
    if schedule["every"] == "cron":
        runs = cron.next_fires(cron.parse(schedule["cron"]), tz, now)
    else:  # step forward from the last slot until three future runs are found
        runs, probe = [], now
        while len(runs) < 3:
            probe += timedelta(minutes=30)
            slot = last_slot(schedule, probe)
            if slot and slot > now and (not runs or slot > runs[-1]):
                runs.append(slot)
        runs = [r.astimezone(tz) for r in runs]
    return SchedulePreviewOut(schedule=schedule, next_runs=runs)


@router.post("/orgs/{org_id}/workflows", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
def create_workflow(body: WorkflowIn, request: Request, ctx: OrgContext = Depends(require(P.WORKFLOWS_MANAGE)),
                    db: Session = Depends(get_db)):
    schedule = _validated(lambda: E.validate(db, trigger=body.trigger, conditions=body.conditions,
                                             actions=body.actions, schedule=body.schedule))
    w = Workflow(name=body.name, trigger=body.trigger, schedule=schedule, conditions=body.conditions, actions=body.actions,
                 enabled=body.enabled, notify_owner_on_failure=body.notify_owner_on_failure, owner_user_id=ctx.user.id)
    db.add(w)
    db.flush()
    if w.trigger == inbound.TRIGGER:
        inbound.hook_token(db, request.app.state.settings, w)
    audit.record(db, actor_user_id=ctx.user.id, action="workflow.created", target_type="workflow", target_id=w.id,
                 details={"name": w.name, "trigger": w.trigger}, ip_address=client_ip(request))
    db.commit()
    return _wf_out(w)


@router.get("/orgs/{org_id}/workflows", response_model=list[WorkflowOut])
def list_workflows(_: OrgContext = Depends(require(P.WORKFLOWS_MANAGE)), db: Session = Depends(get_db)):
    return [_wf_out(w) for w in db.scalars(select(Workflow).order_by(Workflow.name))]


@router.patch("/orgs/{org_id}/workflows/{workflow_id}", response_model=WorkflowOut)
def update_workflow(workflow_id: str, body: WorkflowUpdate, request: Request,
                    ctx: OrgContext = Depends(require(P.WORKFLOWS_MANAGE)), db: Session = Depends(get_db)):
    """Edits apply to future runs; runs already started keep the definition they started with."""
    w = _workflow(db, workflow_id)
    conditions = w.conditions if body.conditions is None else body.conditions
    actions = w.actions if body.actions is None else body.actions
    schedule = w.schedule if body.schedule is None else body.schedule
    w.schedule = _validated(lambda: E.validate(db, trigger=w.trigger, conditions=conditions, actions=actions,
                                               schedule=schedule))
    w.conditions, w.actions = conditions, actions
    if body.name is not None:
        w.name = body.name
    if body.enabled is not None:
        w.enabled = body.enabled
    audit.record(db, actor_user_id=ctx.user.id, action="workflow.updated", target_type="workflow", target_id=w.id,
                 details=body.model_dump(exclude_none=True), ip_address=client_ip(request))
    db.commit()
    return _wf_out(w)


@router.get("/orgs/{org_id}/workflows/{workflow_id}/runs", response_model=list[RunOut])
def list_runs(workflow_id: str, _: OrgContext = Depends(require(P.WORKFLOWS_MANAGE)), db: Session = Depends(get_db)):
    w = _workflow(db, workflow_id)
    rows = db.scalars(select(WorkflowRun).where(WorkflowRun.workflow_id == w.id)
                      .order_by(WorkflowRun.created_at.desc()).limit(200))
    return [_run_out(db, r) for r in rows]


@router.post("/orgs/{org_id}/automation/process")
def process_now(request: Request, ctx: OrgContext = Depends(require(P.WORKFLOWS_MANAGE)),
                db: Session = Depends(get_db)):
    """Process this organization's automation now (the background worker does this continuously)."""
    emitted = scan_overdue(db, date.today())
    scheduled = scan_schedules(db, utcnow())
    db.commit()
    return {"overdue_events": emitted, "scheduled_events": scheduled,
            **E.process(db, provider=request.app.state.ai_provider, webhooks=request.app.state.webhooks)}


def _waiting_run(db: Session, run_id: str) -> tuple[WorkflowRun, WorkflowStep]:
    run = db.get(WorkflowRun, run_id)
    if run is None or run.status != "waiting_approval":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no run waiting for approval")
    step = db.scalar(select(WorkflowStep).where(WorkflowStep.run_id == run.id, WorkflowStep.index == run.current_step))
    return run, step


def _decide(run_id: str, approve: bool, request: Request, ctx: OrgContext, db: Session) -> RunOut:
    run, step = _waiting_run(db, run_id)
    needed = step.result["permission"]
    if not ctx.has(needed):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"approving this step needs permission '{needed}'")
    E.decide(db, run, user_id=ctx.user.id, approve=approve)
    audit.record(db, actor_user_id=ctx.user.id, action=f"workflow.step_{'approved' if approve else 'rejected'}",
                 target_type="workflow_run", target_id=run.id, details={"step": step.index},
                 ip_address=client_ip(request))
    db.commit()
    if approve:
        E.process(db, provider=request.app.state.ai_provider, webhooks=request.app.state.webhooks)  # continue right away
    return _run_out(db, db.get(WorkflowRun, run_id))


@router.post("/orgs/{org_id}/workflow-runs/{run_id}/approve", response_model=RunOut)
def approve_step(run_id: str, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    return _decide(run_id, True, request, ctx, db)


@router.post("/orgs/{org_id}/workflow-runs/{run_id}/reject", response_model=RunOut)
def reject_step(run_id: str, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    return _decide(run_id, False, request, ctx, db)


def approvable_runs(db: Session, ctx: OrgContext) -> list[WorkflowRun]:
    """Runs waiting at an approval step this user holds the permission for."""
    out = []
    for run in db.scalars(select(WorkflowRun).where(WorkflowRun.status == "waiting_approval")):
        _, step = _waiting_run(db, run.id)
        if ctx.has(step.result["permission"]):
            out.append(run)
    return out


@router.get("/orgs/{org_id}/approvals", response_model=list[RunOut])
def my_approvals(ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    """Workflow steps waiting for an approval you are allowed to give."""
    return [_run_out(db, run) for run in approvable_runs(db, ctx)]


@router.get("/orgs/{org_id}/tasks", response_model=list[TaskOut])
def list_tasks(all_tasks: bool = False, status_filter: str | None = None, ctx: OrgContext = Depends(org_context),
               db: Session = Depends(get_db)):
    """Your tasks; workflow managers can pass all_tasks=true."""
    stmt = select(Task).order_by(Task.due_date.is_(None), Task.due_date, Task.created_at)
    if not (all_tasks and ctx.has(P.WORKFLOWS_MANAGE)):
        stmt = stmt.where(Task.assignee_user_id == ctx.user.id)
    if status_filter:
        stmt = stmt.where(Task.status == status_filter)
    return [TaskOut(id=t.id, title=t.title, description=t.description, assignee_user_id=t.assignee_user_id,
                    due_date=t.due_date, status=t.status, related_type=t.related_type, related_id=t.related_id,
                    source=t.source) for t in db.scalars(stmt)]


@router.patch("/orgs/{org_id}/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: str, body: TaskUpdate, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if t is None or (t.assignee_user_id != ctx.user.id and not ctx.has(P.WORKFLOWS_MANAGE)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    t.status = body.status
    db.commit()
    return TaskOut(id=t.id, title=t.title, description=t.description, assignee_user_id=t.assignee_user_id,
                   due_date=t.due_date, status=t.status, related_type=t.related_type, related_id=t.related_id,
                   source=t.source)


@router.get("/orgs/{org_id}/notifications", response_model=list[NotificationOut])
def my_notifications(unread_only: bool = False, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    stmt = select(Notification).where(Notification.user_id == ctx.user.id).order_by(Notification.created_at.desc())
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return [NotificationOut(id=n.id, title=n.title, body=n.body, read=n.read_at is not None, created_at=n.created_at)
            for n in db.scalars(stmt.limit(200))]


@router.post("/orgs/{org_id}/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: str, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != ctx.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "notification not found")
    n.read_at = n.read_at or utcnow()
    db.commit()
    return NotificationOut(id=n.id, title=n.title, body=n.body, read=True, created_at=n.created_at)


# ---------------------------------------------------------------- webhook signing secret


class WebhookSecretOut(BaseModel):
    secret: str


@router.get("/orgs/{org_id}/automation/webhook-secret", response_model=WebhookSecretOut)
def webhook_secret(request: Request, ctx: OrgContext = Depends(require(P.WORKFLOWS_MANAGE)),
                   db: Session = Depends(get_db)):
    """The secret webhook receivers use to check the X-Novara-Signature header."""
    secret = W.org_secret(db, request.app.state.settings)
    db.commit()
    return WebhookSecretOut(secret=secret)


@router.post("/orgs/{org_id}/automation/webhook-secret/rotate", response_model=WebhookSecretOut)
def rotate_webhook_secret(request: Request, ctx: OrgContext = Depends(require(P.WORKFLOWS_MANAGE)),
                          db: Session = Depends(get_db)):
    """A new secret; deliveries are signed with it from now on (update your receivers)."""
    secret = W.org_secret(db, request.app.state.settings, rotate=True)
    audit.record(db, actor_user_id=ctx.user.id, action="automation.webhook_secret_rotated", target_type="automation",
                 target_id="", details={}, ip_address=client_ip(request))
    db.commit()
    return WebhookSecretOut(secret=secret)


# ---------------------------------------------------------------- inbound webhook address


class HookOut(BaseModel):
    token: str
    path: str  # POST JSON here, on the API's own origin
    signing_secret: str | None  # set: only signed calls are taken


def _hook_out(db: Session, settings, w: Workflow, token: str) -> HookOut:
    return HookOut(token=token, path=f"/api/v1/hooks/{token}", signing_secret=inbound.signing_secret(db, settings, w))


def _hook_workflow(db: Session, workflow_id: str) -> Workflow:
    w = _workflow(db, workflow_id)
    if w.trigger != inbound.TRIGGER:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this workflow isn't started by a webhook")
    return w


@router.get("/orgs/{org_id}/workflows/{workflow_id}/hook", response_model=HookOut)
def workflow_hook(workflow_id: str, request: Request, _: OrgContext = Depends(require(P.WORKFLOWS_MANAGE)),
                  db: Session = Depends(get_db)):
    """The secret address other systems POST to (anyone holding it can start the workflow, unless calls
    must also be signed)."""
    w = _hook_workflow(db, workflow_id)
    token = inbound.hook_token(db, request.app.state.settings, w)
    db.commit()
    return _hook_out(db, request.app.state.settings, w, token)


@router.post("/orgs/{org_id}/workflows/{workflow_id}/hook/rotate", response_model=HookOut)
def rotate_workflow_hook(workflow_id: str, request: Request, ctx: OrgContext = Depends(require(P.WORKFLOWS_MANAGE)),
                         db: Session = Depends(get_db)):
    """A new address; the old one stops working at once."""
    w = _hook_workflow(db, workflow_id)
    token = inbound.hook_token(db, request.app.state.settings, w, rotate=True)
    audit.record(db, actor_user_id=ctx.user.id, action="workflow.hook_rotated", target_type="workflow", target_id=w.id,
                 details={"name": w.name}, ip_address=client_ip(request))
    db.commit()
    return _hook_out(db, request.app.state.settings, w, token)


@router.post("/orgs/{org_id}/workflows/{workflow_id}/hook/signing-secret", response_model=HookOut)
def set_hook_signing(workflow_id: str, request: Request, ctx: OrgContext = Depends(require(P.WORKFLOWS_MANAGE)),
                     db: Session = Depends(get_db)):
    """Require signed calls, with a new signing secret (replacing any earlier one at once)."""
    w = _hook_workflow(db, workflow_id)
    settings = request.app.state.settings
    token = inbound.hook_token(db, settings, w)
    inbound.new_signing_secret(db, settings, w)
    audit.record(db, actor_user_id=ctx.user.id, action="workflow.hook_signing_set", target_type="workflow",
                 target_id=w.id, details={"name": w.name}, ip_address=client_ip(request))
    db.commit()
    return _hook_out(db, settings, w, token)


@router.delete("/orgs/{org_id}/workflows/{workflow_id}/hook/signing-secret", response_model=HookOut)
def remove_hook_signing(workflow_id: str, request: Request, ctx: OrgContext = Depends(require(P.WORKFLOWS_MANAGE)),
                        db: Session = Depends(get_db)):
    """Stop requiring signatures: the address alone is enough again."""
    w = _hook_workflow(db, workflow_id)
    settings = request.app.state.settings
    token = inbound.hook_token(db, settings, w)
    w.hook_signing_secret = None
    audit.record(db, actor_user_id=ctx.user.id, action="workflow.hook_signing_removed", target_type="workflow",
                 target_id=w.id, details={"name": w.name}, ip_address=client_ip(request))
    db.commit()
    return _hook_out(db, settings, w, token)
