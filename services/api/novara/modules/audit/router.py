from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.deps import OrgContext, get_db, org_context, require
from novara.modules.access import permissions as P
from novara.modules.audit.models import AuditLog
from novara.modules.identity.models import User

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


# ---------------------------------------------------------------- one record's history

# Who may read a record's history: whoever may read the record (the audit log itself stays audit.read).
HISTORY_READERS = {
    "invoice": P.INVOICES_READ,
    "credit_note": P.INVOICES_READ,
    "sales_order": P.SALES_READ,
    "web_order": P.SALES_READ,
    "purchase_order": P.PURCHASES_READ,
    "vendor_bill": P.PURCHASES_READ,
    "vendor_credit": P.PURCHASES_READ,
    "vendor": P.PURCHASES_READ,
    "customer": P.CUSTOMERS_READ,
    "product": P.PRODUCTS_READ,
    "project": P.PROJECTS_READ,
    "booking": P.BOOKINGS_READ,
    "employee": P.HR_MANAGE,
    "pay_run": P.PAYROLL_MANAGE,
}


class HistoryOut(BaseModel):
    id: str
    created_at: datetime
    action: str
    actor_name: str | None
    details: dict


@router.get("/orgs/{org_id}/history/{target_type}/{target_id}", response_model=list[HistoryOut])
def record_history(target_type: str, target_id: str, limit: int = Query(default=50, ge=1, le=200),
                   ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    """What happened to one record, newest first, and who did it (without the addresses the audit log keeps)."""
    needed = HISTORY_READERS.get(target_type)
    if needed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this kind of record has no history")
    if not (ctx.has(needed) or ctx.has(P.AUDIT_READ)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "you can't see this record")
    logs = list(db.scalars(select(AuditLog).where(AuditLog.target_type == target_type, AuditLog.target_id == target_id)
                           .order_by(AuditLog.created_at.desc()).limit(limit)))
    actors = {x.actor_user_id for x in logs if x.actor_user_id}
    names = {u.id: (u.full_name or u.email) for u in db.scalars(select(User).where(User.id.in_(actors)))} if actors else {}
    return [HistoryOut(id=x.id, created_at=x.created_at, action=x.action, actor_name=names.get(x.actor_user_id or ""),
                       details=x.details) for x in logs]
