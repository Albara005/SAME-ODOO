from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.automation import engine as E
from novara.automation.events import EVENT_TYPES, scan_overdue
from novara.automation.models import Notification, Task, Workflow, WorkflowRun, WorkflowStep
from novara.db import utcnow
from novara.deps import OrgContext, client_ip, get_db, org_context, require
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit

router = APIRouter(tags=["automation"])


class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    trigger: str
    conditions: list[dict] = Field(default_factory=list, max_length=20)
    actions: list[dict] = Field(min_length=1, max_length=E.MAX_ACTIONS)
    enabled: bool = True
    notify_owner_on_failure: bool = True


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    conditions: list[dict] | None = None
    actions: list[dict] | None = None
    enabled: bool | None = None


class WorkflowOut(BaseModel):
    id: str
    name: str
    trigger: str
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
    return WorkflowOut(id=w.id, name=w.name, trigger=w.trigger, conditions=w.conditions, actions=w.actions,
                       enabled=w.enabled, owner_user_id=w.owner_user_id, created_at=w.created_at)


def _run_out(db: Session, r: WorkflowRun) -> RunOut:
    steps = db.scalars(select(WorkflowStep).where(WorkflowStep.run_id == r.id).order_by(WorkflowStep.index))
    return RunOut(id=r.id, workflow_id=r.workflow_id, status=r.status, attempts=r.attempts, error=r.error,
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


@router.post("/orgs/{org_id}/workflows", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
def create_workflow(body: WorkflowIn, request: Request, ctx: OrgContext = Depends(require(P.WORKFLOWS_MANAGE)),
                    db: Session = Depends(get_db)):
    _validated(lambda: E.validate(db, trigger=body.trigger, conditions=body.conditions, actions=body.actions))
    w = Workflow(name=body.name, trigger=body.trigger, conditions=body.conditions, actions=body.actions,
                 enabled=body.enabled, notify_owner_on_failure=body.notify_owner_on_failure, owner_user_id=ctx.user.id)
    db.add(w)
    db.flush()
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
    _validated(lambda: E.validate(db, trigger=w.trigger, conditions=conditions, actions=actions))
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
    db.commit()
    return {"overdue_events": emitted, **E.process(db, provider=request.app.state.ai_provider)}


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
        E.process(db, provider=request.app.state.ai_provider)  # continue right away
    return _run_out(db, db.get(WorkflowRun, run_id))


@router.post("/orgs/{org_id}/workflow-runs/{run_id}/approve", response_model=RunOut)
def approve_step(run_id: str, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    return _decide(run_id, True, request, ctx, db)


@router.post("/orgs/{org_id}/workflow-runs/{run_id}/reject", response_model=RunOut)
def reject_step(run_id: str, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    return _decide(run_id, False, request, ctx, db)


@router.get("/orgs/{org_id}/approvals", response_model=list[RunOut])
def my_approvals(ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    """Workflow steps waiting for an approval you are allowed to give."""
    out = []
    for run in db.scalars(select(WorkflowRun).where(WorkflowRun.status == "waiting_approval")):
        _, step = _waiting_run(db, run.id)
        if ctx.has(step.result["permission"]):
            out.append(_run_out(db, run))
    return out


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
