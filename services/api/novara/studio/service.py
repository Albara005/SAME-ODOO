"""Project operations: working copy, versions, diff, restore, deployments."""

from __future__ import annotations

import difflib
import io
import secrets
import zipfile
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novara.db import utcnow
from novara.modules.files.storage import ObjectStorage
from novara.studio import checks as C
from novara.studio.files import FileError, check_limits, clean_path, get_blob, put_blob
from novara.studio.models import AppDeployment, AppProject, AppVersion, SiteRoute
from novara.studio.templates import TEMPLATES, Template, starter_files


class StudioStateError(RuntimeError):
    """Operation not allowed in the current state (HTTP 409)."""


class ProjectFiles:
    """Read and write a project's working copy through content-addressed storage."""

    def __init__(self, storage: ObjectStorage, project: AppProject):
        self.storage, self.project = storage, project

    @property
    def template(self) -> Template:
        return TEMPLATES[self.project.template]

    def read(self, path: str, manifest: dict[str, str] | None = None) -> bytes:
        manifest = self.project.working_files if manifest is None else manifest
        path = clean_path(path)
        if path not in manifest:
            raise FileError(f"no such file: {path}")
        return get_blob(self.storage, self.project.organization_id, manifest[path])

    def read_all(self, manifest: dict[str, str] | None = None) -> dict[str, bytes]:
        manifest = self.project.working_files if manifest is None else manifest
        return {p: get_blob(self.storage, self.project.organization_id, sha) for p, sha in manifest.items()}

    def write(self, path: str, data: bytes) -> None:
        path = clean_path(path)
        sha = put_blob(self.storage, self.project.organization_id, data)
        files = dict(self.project.working_files)
        files[path] = sha
        sizes = {p: len(self.read(p, files)) if p != path else len(data) for p in files}
        check_limits(files, sizes)
        self.project.working_files = files  # reassign so the JSON column is marked dirty

    def delete(self, path: str) -> None:
        path = clean_path(path)
        if path not in self.project.working_files:
            raise FileError(f"no such file: {path}")
        self.project.working_files = {p: s for p, s in self.project.working_files.items() if p != path}


def create_project(db: Session, storage: ObjectStorage, *, name: str, slug: str, template_key: str,
                   description: str, user_id: str) -> AppProject:
    template = TEMPLATES.get(template_key)
    if template is None:
        raise FileError(f"unknown template: {template_key}")
    project = AppProject(name=name, slug=slug, template=template_key, description=description, working_files={},
                         created_by=user_id)
    db.add(project)
    db.flush()
    pf = ProjectFiles(storage, project)
    for path, body in starter_files(template, name=name, description=description).items():
        pf.write(path, body.encode())
    return project


def latest_version(db: Session, project: AppProject) -> AppVersion | None:
    return db.scalar(select(AppVersion).where(AppVersion.project_id == project.id)
                     .order_by(AppVersion.number.desc()).limit(1))


def get_version(db: Session, project: AppProject, number: int) -> AppVersion | None:
    return db.scalar(select(AppVersion).where(AppVersion.project_id == project.id, AppVersion.number == number))


def is_dirty(db: Session, project: AppProject) -> bool:
    latest = latest_version(db, project)
    return latest is None or latest.files != project.working_files


def check_working_copy(storage: ObjectStorage, project: AppProject) -> list[C.CheckResult]:
    pf = ProjectFiles(storage, project)
    return C.run_checks(pf.template, pf.read_all())


def cut_version(db: Session, storage: ObjectStorage, project: AppProject, *, message: str, user_id: str,
                author_kind: str = "user") -> AppVersion:
    """Snapshot the working copy as a new immutable version and record its check results."""
    if not is_dirty(db, project):
        raise StudioStateError("nothing changed since the last version")
    results = check_working_copy(storage, project)
    number = (db.scalar(select(func.coalesce(func.max(AppVersion.number), 0))
                        .where(AppVersion.project_id == project.id)) or 0) + 1
    version = AppVersion(project_id=project.id, number=number, message=message, files=dict(project.working_files),
                         checks=[r.as_dict() for r in results], checks_passed=C.passed(results),
                         author_kind=author_kind, created_by=user_id)
    db.add(version)
    project.base_version = number
    db.flush()
    return version


def restore(project: AppProject, version: AppVersion) -> None:
    """Make the working copy equal to an earlier version (the history itself is untouched)."""
    project.working_files = dict(version.files)
    project.base_version = version.number


def diff(storage: ObjectStorage, project: AppProject, old: dict[str, str], new: dict[str, str],
         old_label: str, new_label: str) -> list[dict]:
    pf = ProjectFiles(storage, project)
    out = []
    for path in sorted(set(old) | set(new)):
        if old.get(path) == new.get(path):
            continue
        status = "added" if path not in old else "deleted" if path not in new else "modified"
        a = pf.read(path, old).decode("utf-8", "replace").splitlines(keepends=True) if path in old else []
        b = pf.read(path, new).decode("utf-8", "replace").splitlines(keepends=True) if path in new else []
        text = "".join(difflib.unified_diff(a, b, f"{old_label}/{path}", f"{new_label}/{path}"))
        out.append({"path": path, "status": status, "diff": text[:200_000]})
    return out


def export_zip(storage: ObjectStorage, project: AppProject, version: AppVersion) -> bytes:
    """The whole version as a zip: the customer owns their code and can take it anywhere."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, body in sorted(ProjectFiles(storage, project).read_all(version.files).items()):
            z.writestr(f"{project.slug}/{path}", body)
    return buf.getvalue()


# ---------------------------------------------------------------- deployments


def _production_route(db: Session, project: AppProject) -> SiteRoute | None:
    return db.scalar(select(SiteRoute).where(SiteRoute.project_id == project.id,
                                             SiteRoute.environment == "production"))


def deploy_preview(db: Session, project: AppProject, version: AppVersion, *, user_id: str) -> AppDeployment:
    """Previews go live at once on their own unguessable URL, even when checks warn or fail."""
    key = "p-" + secrets.token_urlsafe(18)
    dep = AppDeployment(project_id=project.id, version_id=version.id, environment="preview", site_key=key,
                        status="live", deployed_by=user_id)
    db.add(dep)
    db.flush()
    db.add(SiteRoute(site_key=key, organization_id=project.organization_id, project_id=project.id,
                     environment="preview", deployment_id=dep.id))
    return dep


def request_production(db: Session, project: AppProject, version: AppVersion, *, user_id: str) -> AppDeployment:
    """Production is a separate decision: a request that someone allowed to deploy must approve."""
    if not version.checks_passed:
        raise StudioStateError(f"version {version.number} has failing checks and can't go to production")
    if db.scalar(select(AppDeployment).where(AppDeployment.project_id == project.id,
                                             AppDeployment.status == "pending_approval")):
        raise StudioStateError("another production deployment is already waiting for approval")
    route = _production_route(db, project)
    dep = AppDeployment(project_id=project.id, version_id=version.id, environment="production",
                        site_key=route.site_key if route else "s-" + secrets.token_urlsafe(18),
                        status="pending_approval", deployed_by=user_id)
    db.add(dep)
    db.flush()
    return dep


def approve_production(db: Session, project: AppProject, dep: AppDeployment, *, approver_id: str,
                       now: datetime | None = None) -> AppDeployment:
    if dep.environment != "production" or dep.status != "pending_approval":
        raise StudioStateError("this deployment is not waiting for approval")
    version = db.get(AppVersion, dep.version_id)
    if not version.checks_passed:
        raise StudioStateError("the version's checks failed")
    for live in db.scalars(select(AppDeployment).where(AppDeployment.project_id == project.id,
                                                       AppDeployment.environment == "production",
                                                       AppDeployment.status == "live")):
        live.status = "superseded"
    dep.status, dep.approved_by, dep.approved_at = "live", approver_id, now or utcnow()
    route = _production_route(db, project)
    if route is None:
        db.add(SiteRoute(site_key=dep.site_key, organization_id=project.organization_id, project_id=project.id,
                         environment="production", deployment_id=dep.id))
    else:
        route.deployment_id, route.active = dep.id, True
    db.flush()
    return dep


def reject_production(dep: AppDeployment, *, approver_id: str) -> AppDeployment:
    if dep.status != "pending_approval":
        raise StudioStateError("this deployment is not waiting for approval")
    dep.status, dep.approved_by, dep.approved_at = "rejected", approver_id, utcnow()
    return dep


def take_offline(db: Session, project: AppProject) -> None:
    for route in db.scalars(select(SiteRoute).where(SiteRoute.project_id == project.id)):
        route.active = False
    for dep in db.scalars(select(AppDeployment).where(AppDeployment.project_id == project.id,
                                                      AppDeployment.status == "live")):
        dep.status = "offline"
