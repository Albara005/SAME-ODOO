from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.deps import OrgContext, client_ip, get_db, require
from novara.hosting import service as svc
from novara.hosting.models import HostingRequest
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit

router = APIRouter(tags=["hosting"])


class QuoteIn(BaseModel):
    kind: str
    size: str
    region: str


class RequestIn(QuoteIn):
    reason: str = Field(default="", max_length=2000)


class ApproveIn(BaseModel):
    confirm_monthly_usd: str = Field(description="Type the quoted monthly price, e.g. '400.00', to approve it")


class RequestOut(BaseModel):
    id: str
    kind: str
    size: str
    region: str
    specification: str
    reason: str
    monthly_usd: str
    status: str
    requested_by: str | None
    approved_by: str | None
    approved_at: datetime | None
    operator_note: str
    created_at: datetime


def _usd(cents: int) -> str:
    return f"{cents / 100:.2f}"


def _out(r: HostingRequest) -> RequestOut:
    return RequestOut(id=r.id, kind=r.kind, size=r.size, region=r.region, specification=svc.SPECS[r.size],
                      reason=r.reason, monthly_usd=_usd(r.quoted_monthly_cents), status=r.status,
                      requested_by=r.requested_by, approved_by=r.approved_by, approved_at=r.approved_at,
                      operator_note=r.operator_note, created_at=r.created_at)


def _get(db: Session, request_id: str) -> HostingRequest:
    r = db.get(HostingRequest, request_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "hosting request not found")
    return r


@router.get("/orgs/{org_id}/hosting")
def hosting_status(ctx: OrgContext = Depends(require(P.HOSTING_MANAGE))):
    return {"hosting_mode": ctx.organization.hosting_mode, "options": {
        "kinds": svc.KINDS, "sizes": svc.SPECS, "regions": svc.REGIONS,
        "note": "Shared hosting with strong isolation is the default. Dedicated options are provisioned by the "
                "platform team after an owner approves the quoted cost; nothing is created automatically."}}


@router.post("/orgs/{org_id}/hosting/quote")
def get_quote(body: QuoteIn, _: OrgContext = Depends(require(P.HOSTING_MANAGE))):
    try:
        cents = svc.quote(body.kind, body.size, body.region)
    except svc.HostingError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return {"monthly_usd": _usd(cents), "specification": svc.SPECS[body.size]}


@router.post("/orgs/{org_id}/hosting/requests", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
def create_request(body: RequestIn, request: Request, ctx: OrgContext = Depends(require(P.HOSTING_MANAGE)),
                   db: Session = Depends(get_db)):
    try:
        cents = svc.quote(body.kind, body.size, body.region)
    except svc.HostingError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    if db.scalar(select(HostingRequest.id).where(HostingRequest.status.in_(("pending_approval", "approved",
                                                                             "provisioning")))):
        raise HTTPException(status.HTTP_409_CONFLICT, "there is already an open hosting request")
    r = HostingRequest(kind=body.kind, size=body.size, region=body.region, reason=body.reason,
                       quoted_monthly_cents=cents, requested_by=ctx.user.id)
    db.add(r)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="hosting.requested", target_type="hosting_request",
                 target_id=r.id, details={"kind": r.kind, "size": r.size, "monthly_usd": _usd(cents)},
                 ip_address=client_ip(request))
    db.commit()
    return _out(r)


@router.get("/orgs/{org_id}/hosting/requests", response_model=list[RequestOut])
def list_requests(_: OrgContext = Depends(require(P.HOSTING_MANAGE)), db: Session = Depends(get_db)):
    return [_out(r) for r in db.scalars(select(HostingRequest).order_by(HostingRequest.created_at.desc()))]


@router.post("/orgs/{org_id}/hosting/requests/{request_id}/approve", response_model=RequestOut)
def approve_request(request_id: str, body: ApproveIn, request: Request, ctx: OrgContext = Depends(require(P.ORG_MANAGE)),
                    db: Session = Depends(get_db)):
    """Owner approval: confirms the monthly cost. The request then goes to the platform team."""
    r = _get(db, request_id)
    try:
        cents = int((Decimal(body.confirm_monthly_usd.replace(",", "")) * 100).to_integral_value())
        svc.approve(r, user_id=ctx.user.id, confirmed_monthly_cents=cents)
    except InvalidOperation:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "confirm_monthly_usd must be a number")
    except svc.HostingError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    audit.record(db, actor_user_id=ctx.user.id, action="hosting.approved", target_type="hosting_request",
                 target_id=r.id, details={"monthly_usd": _usd(r.quoted_monthly_cents)}, ip_address=client_ip(request))
    db.commit()
    return _out(r)


@router.post("/orgs/{org_id}/hosting/requests/{request_id}/cancel", response_model=RequestOut)
def cancel_request(request_id: str, request: Request, ctx: OrgContext = Depends(require(P.HOSTING_MANAGE)),
                   db: Session = Depends(get_db)):
    r = _get(db, request_id)
    try:
        svc.cancel(r)
    except svc.HostingError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    audit.record(db, actor_user_id=ctx.user.id, action="hosting.cancelled", target_type="hosting_request",
                 target_id=r.id, details={}, ip_address=client_ip(request))
    db.commit()
    return _out(r)
