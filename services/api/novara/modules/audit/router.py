from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.deps import OrgContext, get_db, require
from novara.modules.access import permissions as P
from novara.modules.audit.models import AuditLog

router = APIRouter(tags=["audit"])


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    actor_user_id: str | None
    action: str
    target_type: str
    target_id: str
    details: dict
    ip_address: str


@router.get("/orgs/{org_id}/audit-logs", response_model=list[AuditOut])
def list_audit_logs(
    action: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    _: OrgContext = Depends(require(P.AUDIT_READ)),
    db: Session = Depends(get_db),
):
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    return db.scalars(stmt).all()
