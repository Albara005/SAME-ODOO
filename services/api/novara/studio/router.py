from __future__ import annotations

import mimetypes
import posixpath
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara.ai import assistant as A
from novara.ai.models import Conversation
from novara.ai.router import _usd as usd
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit
from novara.studio import builder
from novara.studio import service as svc
from novara.studio.files import MAX_FILE_BYTES, FileError, get_blob
from novara.studio.models import AppDeployment, AppProject, AppVersion, SiteRoute
from novara.studio.templates import TEMPLATES
from novara.tenancy import bind_tenant

router = APIRouter(tags=["app-studio"])
sites_router = APIRouter(tags=["published-sites"])

_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,58}[a-z0-9])$")


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=3, max_length=60)
    template: str = "static-site"
    description: str = Field(default="", max_length=2000)


class FileInfo(BaseModel):
    path: str
    sha256: str


class ProjectOut(BaseModel):
    id: str
    name: str
    slug: str
    template: str
    description: str
    base_version: int
    has_unsaved_changes: bool
    files: list[FileInfo]
    created_at: datetime


class CheckOut(BaseModel):
    check: str
    status: str
    message: str
    path: str


class VersionIn(BaseModel):
    message: str = Field(default="", max_length=300)


class VersionOut(BaseModel):
    id: str
    number: int
    message: str
    author_kind: str
    checks_passed: bool
    checks: list[CheckOut]
    files: int
    created_at: datetime


class DeployIn(BaseModel):
    version: int = Field(ge=1)
    environment: str = Field(pattern="^(preview|production)$")


class DeploymentOut(BaseModel):
    id: str
    version: int
    environment: str
    status: str
    url: str | None
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime


class BuilderIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class BuilderOut(BaseModel):
    reply: str
    stop: str
    changed_files: list[str]
    tool_calls: list[dict]
    cost_usd: str


def _project_out(db: Session, p: AppProject) -> ProjectOut:
    return ProjectOut(id=p.id, name=p.name, slug=p.slug, template=p.template, description=p.description,
                      base_version=p.base_version, has_unsaved_changes=svc.is_dirty(db, p),
                      files=[FileInfo(path=k, sha256=v) for k, v in sorted(p.working_files.items())],
                      created_at=p.created_at)


def _version_out(v: AppVersion) -> VersionOut:
    return VersionOut(id=v.id, number=v.number, message=v.message, author_kind=v.author_kind,
                      checks_passed=v.checks_passed, checks=[CheckOut(**c) for c in v.checks], files=len(v.files),
                      created_at=v.created_at)


def _site_url(request: Request, key: str) -> str:
    base = request.app.state.settings.sites_base_url or str(request.base_url).rstrip("/") + "/sites"
    return f"{base}/{key}/"


def _deployment_out(db: Session, request: Request, d: AppDeployment) -> DeploymentOut:
    version = db.get(AppVersion, d.version_id)
    return DeploymentOut(id=d.id, version=version.number, environment=d.environment, status=d.status,
                         url=_site_url(request, d.site_key) if d.status == "live" else None,
                         approved_by=d.approved_by, approved_at=d.approved_at, created_at=d.created_at)


def _project(db: Session, project_id: str) -> AppProject:
    p = db.get(AppProject, project_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return p


def _version(db: Session, project: AppProject, number: int) -> AppVersion:
    v = svc.get_version(db, project, number)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"version {number} not found")
    return v


def _errors(fn):
    try:
        return fn()
    except FileError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except svc.StudioStateError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


def _audit(db, ctx, request, action, project, **details):
    audit.record(db, actor_user_id=ctx.user.id, action=action, target_type="app_project", target_id=project.id,
                 details=details, ip_address=client_ip(request))


@router.get("/orgs/{org_id}/studio/templates")
def list_templates(_: OrgContext = Depends(require(P.APPS_READ))):
    return [{"key": t.key, "name": t.name, "description": t.description} for t in TEMPLATES.values()]


@router.post("/orgs/{org_id}/studio/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectIn, request: Request, ctx: OrgContext = Depends(require(P.APPS_WRITE)),
                   db: Session = Depends(get_db)):
    if not _SLUG.match(body.slug):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "slug must be lowercase letters, digits and dashes")
    try:
        project = _errors(lambda: svc.create_project(db, request.app.state.storage, name=body.name, slug=body.slug,
                                                     template_key=body.template, description=body.description,
                                                     user_id=ctx.user.id))
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "a project with this slug already exists")
    _audit(db, ctx, request, "app.created", project, template=body.template)
    db.commit()
    return _project_out(db, project)


@router.get("/orgs/{org_id}/studio/projects", response_model=list[ProjectOut])
def list_projects(_: OrgContext = Depends(require(P.APPS_READ)), db: Session = Depends(get_db)):
    return [_project_out(db, p) for p in db.scalars(select(AppProject).order_by(AppProject.name))]


@router.get("/orgs/{org_id}/studio/projects/{project_id}", response_model=ProjectOut)
def read_project(project_id: str, _: OrgContext = Depends(require(P.APPS_READ)), db: Session = Depends(get_db)):
    return _project_out(db, _project(db, project_id))


@router.get("/orgs/{org_id}/studio/projects/{project_id}/files/{path:path}")
def read_file(project_id: str, path: str, request: Request, version: int | None = None,
              _: OrgContext = Depends(require(P.APPS_READ)), db: Session = Depends(get_db)) -> Response:
    p = _project(db, project_id)
    manifest = _version(db, p, version).files if version else None
    body = _errors(lambda: svc.ProjectFiles(request.app.state.storage, p).read(path, manifest))
    return Response(body, media_type="application/octet-stream", headers={"X-Content-Type-Options": "nosniff"})


@router.put("/orgs/{org_id}/studio/projects/{project_id}/files/{path:path}", response_model=ProjectOut)
async def write_file(project_id: str, path: str, request: Request, ctx: OrgContext = Depends(require(P.APPS_WRITE)),
                     db: Session = Depends(get_db)):
    """Replace one file of the working copy with the raw request body."""
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large")
    p = _project(db, project_id)
    _errors(lambda: svc.ProjectFiles(request.app.state.storage, p).write(path, bytes(data)))
    db.commit()
    return _project_out(db, p)


@router.delete("/orgs/{org_id}/studio/projects/{project_id}/files/{path:path}", response_model=ProjectOut)
def delete_file(project_id: str, path: str, request: Request, ctx: OrgContext = Depends(require(P.APPS_WRITE)),
                db: Session = Depends(get_db)):
    p = _project(db, project_id)
    _errors(lambda: svc.ProjectFiles(request.app.state.storage, p).delete(path))
    db.commit()
    return _project_out(db, p)


@router.post("/orgs/{org_id}/studio/projects/{project_id}/checks", response_model=list[CheckOut])
def run_checks(project_id: str, request: Request, _: OrgContext = Depends(require(P.APPS_READ)),
               db: Session = Depends(get_db)):
    """Check the working copy without creating a version."""
    return [CheckOut(**r.as_dict()) for r in svc.check_working_copy(request.app.state.storage, _project(db, project_id))]


@router.get("/orgs/{org_id}/studio/projects/{project_id}/diff")
def diff(project_id: str, request: Request, base: int | None = None, target: int | None = None,
         _: OrgContext = Depends(require(P.APPS_READ)), db: Session = Depends(get_db)):
    """Unified diff from version ``base`` (default: latest) to version ``target`` (default: working copy)."""
    p = _project(db, project_id)
    latest = svc.latest_version(db, p)
    old_v = _version(db, p, base) if base else latest
    old, old_label = (old_v.files, f"v{old_v.number}") if old_v else ({}, "empty")
    new, new_label = (_version(db, p, target).files, f"v{target}") if target else (p.working_files, "working")
    return {"base": old_label, "target": new_label,
            "files": svc.diff(request.app.state.storage, p, old, new, old_label, new_label)}


@router.post("/orgs/{org_id}/studio/projects/{project_id}/versions", response_model=VersionOut,
             status_code=status.HTTP_201_CREATED)
def create_version(project_id: str, body: VersionIn, request: Request,
                   ctx: OrgContext = Depends(require(P.APPS_WRITE)), db: Session = Depends(get_db)):
    p = _project(db, project_id)
    v = _errors(lambda: svc.cut_version(db, request.app.state.storage, p, message=body.message, user_id=ctx.user.id))
    _audit(db, ctx, request, "app.version_created", p, version=v.number, checks_passed=v.checks_passed)
    db.commit()
    return _version_out(v)


@router.get("/orgs/{org_id}/studio/projects/{project_id}/versions", response_model=list[VersionOut])
def list_versions(project_id: str, _: OrgContext = Depends(require(P.APPS_READ)), db: Session = Depends(get_db)):
    p = _project(db, project_id)
    rows = db.scalars(select(AppVersion).where(AppVersion.project_id == p.id).order_by(AppVersion.number.desc()))
    return [_version_out(v) for v in rows]


@router.post("/orgs/{org_id}/studio/projects/{project_id}/versions/{number}/restore", response_model=ProjectOut)
def restore_version(project_id: str, number: int, request: Request, ctx: OrgContext = Depends(require(P.APPS_WRITE)),
                    db: Session = Depends(get_db)):
    """Put an earlier version back into the working copy. Versions themselves never change."""
    p = _project(db, project_id)
    svc.restore(p, _version(db, p, number))
    _audit(db, ctx, request, "app.version_restored", p, version=number)
    db.commit()
    return _project_out(db, p)


@router.get("/orgs/{org_id}/studio/projects/{project_id}/versions/{number}/export.zip")
def export_version(project_id: str, number: int, request: Request, _: OrgContext = Depends(require(P.APPS_READ)),
                   db: Session = Depends(get_db)) -> Response:
    p = _project(db, project_id)
    data = svc.export_zip(request.app.state.storage, p, _version(db, p, number))
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{p.slug}-v{number}.zip"'})


@router.post("/orgs/{org_id}/studio/projects/{project_id}/deployments", response_model=DeploymentOut,
             status_code=status.HTTP_201_CREATED)
def deploy(project_id: str, body: DeployIn, request: Request, ctx: OrgContext = Depends(require(P.APPS_WRITE)),
           db: Session = Depends(get_db)):
    """Previews go live at once. Production creates a request that someone with apps.deploy approves."""
    p = _project(db, project_id)
    v = _version(db, p, body.version)
    if body.environment == "preview":
        dep = svc.deploy_preview(db, p, v, user_id=ctx.user.id)
    else:
        dep = _errors(lambda: svc.request_production(db, p, v, user_id=ctx.user.id))
    _audit(db, ctx, request, f"app.{body.environment}_deploy_requested" if body.environment == "production"
           else "app.preview_deployed", p, version=v.number)
    db.commit()
    return _deployment_out(db, request, dep)


@router.get("/orgs/{org_id}/studio/projects/{project_id}/deployments", response_model=list[DeploymentOut])
def list_deployments(project_id: str, request: Request, _: OrgContext = Depends(require(P.APPS_READ)),
                     db: Session = Depends(get_db)):
    p = _project(db, project_id)
    rows = db.scalars(select(AppDeployment).where(AppDeployment.project_id == p.id)
                      .order_by(AppDeployment.created_at.desc()))
    return [_deployment_out(db, request, d) for d in rows]


def _deployment(db: Session, project: AppProject, deployment_id: str) -> AppDeployment:
    d = db.get(AppDeployment, deployment_id)
    if d is None or d.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "deployment not found")
    return d


@router.post("/orgs/{org_id}/studio/projects/{project_id}/deployments/{deployment_id}/approve",
             response_model=DeploymentOut)
def approve_deployment(project_id: str, deployment_id: str, request: Request,
                       ctx: OrgContext = Depends(require(P.APPS_DEPLOY)), db: Session = Depends(get_db)):
    p = _project(db, project_id)
    d = _errors(lambda: svc.approve_production(db, p, _deployment(db, p, deployment_id), approver_id=ctx.user.id))
    _audit(db, ctx, request, "app.production_deployed", p, deployment=d.id)
    db.commit()
    return _deployment_out(db, request, d)


@router.post("/orgs/{org_id}/studio/projects/{project_id}/deployments/{deployment_id}/reject",
             response_model=DeploymentOut)
def reject_deployment(project_id: str, deployment_id: str, request: Request,
                      ctx: OrgContext = Depends(require(P.APPS_DEPLOY)), db: Session = Depends(get_db)):
    p = _project(db, project_id)
    d = _errors(lambda: svc.reject_production(_deployment(db, p, deployment_id), approver_id=ctx.user.id))
    _audit(db, ctx, request, "app.production_rejected", p, deployment=d.id)
    db.commit()
    return _deployment_out(db, request, d)


@router.post("/orgs/{org_id}/studio/projects/{project_id}/offline", response_model=list[DeploymentOut])
def take_offline(project_id: str, request: Request, ctx: OrgContext = Depends(require(P.APPS_DEPLOY)),
                 db: Session = Depends(get_db)):
    """Stop serving every deployment of the project at once."""
    p = _project(db, project_id)
    svc.take_offline(db, p)
    _audit(db, ctx, request, "app.taken_offline", p)
    db.commit()
    return list_deployments(project_id, request, ctx, db)


@router.post("/orgs/{org_id}/studio/projects/{project_id}/builder/messages", response_model=BuilderOut)
def builder_message(project_id: str, body: BuilderIn, request: Request,
                    ctx: OrgContext = Depends(require(P.APPS_WRITE)), db: Session = Depends(get_db)):
    """Ask the AI builder to change the app. It edits the working copy only."""
    if not ctx.has(P.AI_USE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission: {P.AI_USE}")
    p = _project(db, project_id)
    title = f"studio:{p.id}"
    conv = db.scalar(select(Conversation).where(Conversation.user_id == ctx.user.id, Conversation.title == title))
    if conv is None:
        conv = Conversation(user_id=ctx.user.id, title=title)
        db.add(conv)
        db.flush()
    before = dict(p.working_files)
    note = f"Project: {p.name} (template {p.template}); working copy based on version {p.base_version or 'none'}."
    try:
        result = A.run_turn(db, ctx, conv, body.content, provider=request.app.state.ai_provider,
                            route_name="app_builder", ip=client_ip(request), registry=builder.BUILDER_TOOLS,
                            system=builder.system_prompt(p.template), context_note=note,
                            extra={"project": p, "storage": request.app.state.storage})
    except A.AssistantUnavailable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e))
    except A.BudgetExceeded as e:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(e))
    after = p.working_files
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    db.commit()
    return BuilderOut(reply=result.reply, stop=result.stop, changed_files=changed,
                      tool_calls=[{"name": c.name, "status": c.status, "arguments": {k: v for k, v in c.arguments.items()
                                                                                    if k != "content"}}
                                  for c in result.tool_calls],
                      cost_usd=usd(sum(u.cost_micros for u in result.usage)))


# ---------------------------------------------------------------- serving published sites

# The site's documents run in a sandbox with an opaque origin: their scripts can't read the
# platform's cookies or storage even when served from the same host. Production should still
# serve sites from a separate domain (NOVARA_SITES_BASE_URL).
SITE_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups allow-modals",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Cache-Control": "public, max-age=60",
}


@sites_router.get("/sites/{site_key}/{path:path}")
def serve_site(site_key: str, path: str, request: Request, db: Session = Depends(get_db)) -> Response:
    route = db.get(SiteRoute, site_key)
    if route is None or not route.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "site not found")
    bind_tenant(db, route.organization_id)
    dep = db.get(AppDeployment, route.deployment_id)
    if dep is None or dep.status != "live":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "site not found")
    files = db.get(AppVersion, dep.version_id).files
    path = posixpath.normpath(path) if path else "index.html"
    if path in ("", "."):
        path = "index.html"
    if path not in files and f"{path}/index.html" in files:
        path = f"{path}/index.html"
    if path not in files:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    body = get_blob(request.app.state.storage, route.organization_id, files[path])
    media = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if media.startswith("text/") or media in ("application/javascript", "application/json", "image/svg+xml"):
        media += "; charset=utf-8"
    return Response(body, media_type=media, headers=SITE_HEADERS)
