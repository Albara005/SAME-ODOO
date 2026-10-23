from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.automation.models import Task
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.accounting.models import Tax
from novara.modules.audit import service as audit
from novara.modules.identity.models import User
from novara.modules.invoicing.lines import LineIn
from novara.modules.invoicing.router import InvoiceIn, add_draft_invoice
from novara.modules.organizations.models import Membership
from novara.modules.partners.models import Customer
from novara.modules.projects import service as svc
from novara.modules.projects.models import Project, TimeEntry
from novara.money import fmt_decimal, from_minor, to_minor

router = APIRouter(tags=["projects"])
PROJECT_TASK = "project"  # tasks of a project are ordinary tasks (they show in everyone's task list)


class WorkProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    customer_id: str | None = None
    hourly_rate: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    tax_id: str | None = None
    budget_hours: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=2)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    status: str | None = Field(default=None, pattern="^(active|closed)$")
    hourly_rate: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    budget_hours: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=2)


class WorkProjectOut(BaseModel):
    id: str
    name: str
    description: str
    status: str
    customer_id: str | None
    customer_name: str | None
    hourly_rate: str | None
    tax_id: str | None
    budget_hours: str | None
    hours: str
    billable_hours: str
    unbilled_hours: str
    unbilled_amount: str | None  # before tax, at the hourly rate
    budget_used_percent: str | None
    currency: str
    created_at: datetime


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)
    assignee_user_id: str | None = None
    due_date: date | None = None


class ProjectTaskOut(BaseModel):
    id: str
    title: str
    description: str
    assignee_user_id: str | None
    due_date: date | None
    status: str
    hours: str


class TimeIn(BaseModel):
    entry_date: date
    hours: Decimal = Field(gt=0, le=24, max_digits=5, decimal_places=2)
    description: str = Field(default="", max_length=500)
    task_id: str | None = None
    billable: bool = True
    user_id: str | None = None  # someone else's hours: project managers only

    @model_validator(mode="after")
    def _not_ahead(self):
        if self.entry_date > date.today():
            raise ValueError("hours can't be logged for a future day")
        return self


class TimeOut(BaseModel):
    id: str
    user_id: str
    user_email: str
    user_name: str
    entry_date: date
    hours: str
    description: str
    billable: bool
    task_id: str | None
    invoice_id: str | None
    billed: bool


class InvoiceHoursIn(BaseModel):
    issue_date: date
    up_to: date | None = None  # hours logged on or before; defaults to the issue date


class InvoicedOut(BaseModel):
    invoice_id: str
    hours: str


def _project(db: Session, project_id: str, lock: bool = False) -> Project:
    p = db.get(Project, project_id, with_for_update=lock)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return p


def _outs(db: Session, projects: list[Project], currency: str) -> list[WorkProjectOut]:
    figs = svc.figures(db, [p.id for p in projects])
    names = {c.id: c.name for c in db.scalars(select(Customer).where(Customer.id.in_(
        {p.customer_id for p in projects if p.customer_id})))}
    out = []
    for p in projects:
        f = figs[p.id]
        amount = (str(from_minor(to_minor(f.unbilled_hours * p.hourly_rate, currency), currency))
                  if p.hourly_rate is not None and p.customer_id else None)
        used = f.budget_used(p)
        out.append(WorkProjectOut(
            id=p.id, name=p.name, description=p.description, status=p.status, customer_id=p.customer_id,
            customer_name=names.get(p.customer_id or ""), currency=currency,
            hourly_rate=fmt_decimal(p.hourly_rate) if p.hourly_rate is not None else None, tax_id=p.tax_id,
            budget_hours=fmt_decimal(p.budget_hours) if p.budget_hours is not None else None,
            hours=fmt_decimal(f.hours), billable_hours=fmt_decimal(f.billable_hours),
            unbilled_hours=fmt_decimal(f.unbilled_hours), unbilled_amount=amount,
            budget_used_percent=fmt_decimal(used) if used is not None else None, created_at=p.created_at))
    return out


def _check_refs(db: Session, customer_id: str | None, tax_id: str | None) -> None:
    if customer_id is not None:
        customer = db.get(Customer, customer_id)
        if customer is None or not customer.active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown or archived customer")
    if tax_id is not None and db.get(Tax, tax_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown tax")


def _member(db: Session, user_id: str) -> bool:
    return db.scalar(select(Membership.id).where(Membership.user_id == user_id, Membership.status == "active")) is not None


@router.get("/orgs/{org_id}/projects", response_model=list[WorkProjectOut])
def list_projects(status_filter: str | None = None, ctx: OrgContext = Depends(require(P.PROJECTS_READ)),
                  db: Session = Depends(get_db)):
    stmt = select(Project).order_by(Project.status, Project.name)
    if status_filter in ("active", "closed"):
        stmt = stmt.where(Project.status == status_filter)
    return _outs(db, list(db.scalars(stmt)), ctx.organization.default_currency)


@router.post("/orgs/{org_id}/projects", response_model=WorkProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: WorkProjectIn, request: Request, ctx: OrgContext = Depends(require(P.PROJECTS_MANAGE)),
                   db: Session = Depends(get_db)):
    _check_refs(db, body.customer_id, body.tax_id)
    p = Project(**body.model_dump(), manager_user_id=ctx.user.id, created_by=ctx.user.id)
    db.add(p)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="project.created", target_type="project", target_id=p.id,
                 details={"name": p.name}, ip_address=client_ip(request))
    db.commit()
    return _outs(db, [p], ctx.organization.default_currency)[0]


@router.get("/orgs/{org_id}/projects/{project_id}", response_model=WorkProjectOut)
def read_project(project_id: str, ctx: OrgContext = Depends(require(P.PROJECTS_READ)), db: Session = Depends(get_db)):
    return _outs(db, [_project(db, project_id)], ctx.organization.default_currency)[0]


@router.patch("/orgs/{org_id}/projects/{project_id}", response_model=WorkProjectOut)
def update_project(project_id: str, body: ProjectUpdate, request: Request,
                   ctx: OrgContext = Depends(require(P.PROJECTS_MANAGE)), db: Session = Depends(get_db)):
    p = _project(db, project_id)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if value is None and field in ("name", "description", "status"):
            continue
        setattr(p, field, value)
    audit.record(db, actor_user_id=ctx.user.id, action="project.updated", target_type="project", target_id=p.id,
                 details={k: str(v) for k, v in changes.items()}, ip_address=client_ip(request))
    db.commit()
    return _outs(db, [p], ctx.organization.default_currency)[0]


# ---------------------------------------------------------------- tasks


@router.get("/orgs/{org_id}/projects/{project_id}/tasks", response_model=list[ProjectTaskOut])
def list_project_tasks(project_id: str, _: OrgContext = Depends(require(P.PROJECTS_READ)), db: Session = Depends(get_db)):
    p = _project(db, project_id)
    tasks = list(db.scalars(select(Task).where(Task.related_type == PROJECT_TASK, Task.related_id == p.id)
                            .order_by(Task.status.desc(), Task.due_date.is_(None), Task.due_date, Task.created_at)))
    hours: dict[str, Decimal] = {}
    for task_id, h in db.execute(select(TimeEntry.task_id, TimeEntry.hours).where(TimeEntry.project_id == p.id,
                                                                                TimeEntry.task_id.is_not(None))):
        hours[task_id] = hours.get(task_id, Decimal(0)) + h
    return [ProjectTaskOut(id=t.id, title=t.title, description=t.description, assignee_user_id=t.assignee_user_id,
                           due_date=t.due_date, status=t.status, hours=fmt_decimal(hours.get(t.id, Decimal(0))))
            for t in tasks]


@router.post("/orgs/{org_id}/projects/{project_id}/tasks", response_model=ProjectTaskOut,
             status_code=status.HTTP_201_CREATED)
def add_project_task(project_id: str, body: TaskIn, request: Request,
                     ctx: OrgContext = Depends(require(P.PROJECTS_MANAGE)), db: Session = Depends(get_db)):
    p = _project(db, project_id)
    if body.assignee_user_id and not _member(db, body.assignee_user_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "the assignee isn't a member of this organization")
    t = Task(title=body.title, description=body.description, assignee_user_id=body.assignee_user_id,
             due_date=body.due_date, related_type=PROJECT_TASK, related_id=p.id, source=f"project:{p.name}"[:80])
    db.add(t)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="project.task_added", target_type="project", target_id=p.id,
                 details={"task": t.title}, ip_address=client_ip(request))
    db.commit()
    return ProjectTaskOut(id=t.id, title=t.title, description=t.description, assignee_user_id=t.assignee_user_id,
                          due_date=t.due_date, status=t.status, hours="0")


# ---------------------------------------------------------------- hours


def _time_out(db: Session, e: TimeEntry, users: dict[str, User]) -> TimeOut:
    who = users.get(e.user_id)
    return TimeOut(id=e.id, user_id=e.user_id, user_email=who.email if who else "",
                   user_name=who.full_name if who else "", entry_date=e.entry_date,
                   hours=fmt_decimal(e.hours), description=e.description, billable=e.billable, task_id=e.task_id,
                   invoice_id=e.invoice_id, billed=svc.billed(e, db))


@router.get("/orgs/{org_id}/projects/{project_id}/time", response_model=list[TimeOut])
def list_time(project_id: str, _: OrgContext = Depends(require(P.PROJECTS_READ)), db: Session = Depends(get_db)):
    p = _project(db, project_id)
    entries = list(db.scalars(select(TimeEntry).where(TimeEntry.project_id == p.id)
                              .order_by(TimeEntry.entry_date.desc(), TimeEntry.created_at.desc()).limit(500)))
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({e.user_id for e in entries})))}
    return [_time_out(db, e, users) for e in entries]


@router.post("/orgs/{org_id}/projects/{project_id}/time", response_model=TimeOut, status_code=status.HTTP_201_CREATED)
def log_time(project_id: str, body: TimeIn, ctx: OrgContext = Depends(require(P.TIMESHEETS_WRITE)),
             db: Session = Depends(get_db)):
    """Log hours on a project (your own; project managers can log for any member)."""
    p = _project(db, project_id)
    if p.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "the project is closed")
    user_id = body.user_id or ctx.user.id
    if user_id != ctx.user.id:
        if not ctx.has(P.PROJECTS_MANAGE):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "logging someone else's hours needs projects.manage")
        if not _member(db, user_id):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "that person isn't a member of this organization")
    if body.task_id is not None:
        task = db.get(Task, body.task_id)
        if task is None or task.related_type != PROJECT_TASK or task.related_id != p.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "that task isn't part of this project")
    e = TimeEntry(project_id=p.id, user_id=user_id, entry_date=body.entry_date, hours=body.hours,
                  description=body.description, billable=body.billable, task_id=body.task_id)
    db.add(e)
    db.commit()
    return _time_out(db, e, {user_id: db.get(User, user_id)})


@router.delete("/orgs/{org_id}/projects/{project_id}/time/{entry_id}", status_code=status.HTTP_204_NO_CONTENT,
               response_model=None)
def delete_time(project_id: str, entry_id: str, ctx: OrgContext = Depends(require(P.TIMESHEETS_WRITE)),
                db: Session = Depends(get_db)):
    p = _project(db, project_id)
    e = db.get(TimeEntry, entry_id)
    if e is None or e.project_id != p.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "time entry not found")
    if p.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "the project is closed")
    if e.user_id != ctx.user.id and not ctx.has(P.PROJECTS_MANAGE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only your own hours")
    if svc.billed(e, db):
        raise HTTPException(status.HTTP_409_CONFLICT, "these hours are on an invoice")
    db.delete(e)
    db.commit()


@router.post("/orgs/{org_id}/projects/{project_id}/invoice", response_model=InvoicedOut,
             status_code=status.HTTP_201_CREATED)
def invoice_hours(project_id: str, body: InvoiceHoursIn, request: Request,
                  ctx: OrgContext = Depends(require(P.PROJECTS_MANAGE)), db: Session = Depends(get_db)):
    """A draft invoice for the customer with the project's unbilled billable hours (one line per task)."""
    if not ctx.has(P.INVOICES_WRITE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing permission: invoices.write")
    p = _project(db, project_id, lock=True)   # a second request waits, then finds these hours billed
    up_to = body.up_to or body.issue_date
    try:
        entries, lines = svc.lines_to_invoice(db, p, up_to)
    except svc.ProjectError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    inv = add_draft_invoice(db, ctx, InvoiceIn(
        customer_id=p.customer_id, issue_date=body.issue_date, notes=f"{p.name}: {up_to.isoformat()}",
        lines=[LineIn(**l) for l in lines]), ip=client_ip(request), via=f"project:{p.id}")
    svc.mark_billed(db, entries, inv.id)
    total = sum((e.hours for e in entries), Decimal(0))
    audit.record(db, actor_user_id=ctx.user.id, action="project.invoiced", target_type="project", target_id=p.id,
                 details={"hours": fmt_decimal(total), "invoice_id": inv.id}, ip_address=client_ip(request))
    db.commit()
    return InvoicedOut(invoice_id=inv.id, hours=fmt_decimal(total))
