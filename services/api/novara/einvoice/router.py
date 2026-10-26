"""E-invoicing (ZATCA phase 2) settings, onboarding and the documents sent.

    GET/PUT /orgs/{org}/einvoicing                    seller details and the device's state (org.manage)
    POST    /orgs/{org}/einvoicing/onboard            {otp}: certificates from ZATCA, or a renewed one (org.manage)
    POST    /orgs/{org}/einvoicing/off                stop issuing e-invoices (org.manage)
    GET     /orgs/{org}/einvoicing/status             on or off, for the invoice screens (invoices.read)
    GET     /orgs/{org}/einvoicing/documents          the latest documents and what ZATCA said
    GET     /orgs/{org}/einvoicing/documents/{id}/xml the signed XML (as cleared by ZATCA when it was)
    POST    /orgs/{org}/einvoicing/documents/{id}/send  send a pending document again (invoices.post)
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novara.deps import OrgContext, client_ip, get_db, require
from novara.einvoice import service as svc
from novara.einvoice.models import EInvoiceDocument
from novara.einvoice.schemas import EInvoiceSummary, summary
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit
from novara.modules.organizations.models import Organization

router = APIRouter(tags=["e-invoicing"])
Environment = Literal["sandbox", "simulation", "production"]


class EInvoiceSettingsIn(BaseModel):
    environment: Environment = "sandbox"
    crn: str = Field(default="", max_length=20)
    street: str = Field(default="", max_length=120)
    building_number: str = Field(default="", max_length=10)
    district: str = Field(default="", max_length=120)
    city: str = Field(default="", max_length=120)
    postal_code: str = Field(default="", max_length=10)
    branch_name: str = Field(default="", max_length=64)
    industry: str = Field(default="", max_length=64)


class EInvoiceSettingsOut(EInvoiceSettingsIn):
    status: Literal["off", "onboarded"]
    vat_number: str
    problems: list[str]
    onboarded_at: datetime | None
    certificate_expires_at: datetime | None
    certificate_state: Literal["ok", "expiring", "expired"] | None
    certificate_days_left: int | None
    documents: int
    pending: int
    rejected: int


class OnboardIn(BaseModel):
    otp: str = Field(pattern=r"^\d{6}$")


class EInvoiceStatusOut(BaseModel):
    active: bool
    environment: Environment | None
    # The certificate expires within 30 days (expiring) or has (expired: invoices can't be posted until it's renewed).
    certificate_state: Literal["ok", "expiring", "expired"] | None = None
    certificate_expires_at: datetime | None = None


def _errors(fn):
    try:
        return fn()
    except svc.EInvoiceError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except svc.EInvoiceState as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


def _settings_out(db: Session, ctx: OrgContext) -> EInvoiceSettingsOut:
    s = svc.settings_row(db)
    org = db.get(Organization, ctx.organization.id)
    counts = dict(db.execute(select(EInvoiceDocument.status, func.count()).group_by(EInvoiceDocument.status)).all())
    return EInvoiceSettingsOut(
        environment=s.environment, crn=s.crn, street=s.street, building_number=s.building_number, district=s.district,
        city=s.city, postal_code=s.postal_code, branch_name=s.branch_name, industry=s.industry, status=s.status,
        vat_number=org.vat_number, problems=svc.seller_problems(org, s), onboarded_at=s.onboarded_at,
        certificate_expires_at=s.certificate_expires_at, certificate_state=svc.certificate_state(s),
        certificate_days_left=svc.certificate_days_left(s), documents=sum(counts.values()),
        pending=counts.get("pending", 0), rejected=counts.get("rejected", 0))


@router.get("/orgs/{org_id}/einvoicing", response_model=EInvoiceSettingsOut)
def read_settings(ctx: OrgContext = Depends(require(P.ORG_MANAGE)), db: Session = Depends(get_db)):
    out = _settings_out(db, ctx)
    db.commit()
    return out


@router.put("/orgs/{org_id}/einvoicing", response_model=EInvoiceSettingsOut)
def change_settings(body: EInvoiceSettingsIn, request: Request, ctx: OrgContext = Depends(require(P.ORG_MANAGE)),
                    db: Session = Depends(get_db)):
    """Seller details for e-invoices. The environment can only change while e-invoicing is off."""
    s = svc.settings_row(db)
    if s.status == "onboarded" and body.environment != s.environment:
        raise HTTPException(status.HTTP_409_CONFLICT, "turn e-invoicing off before moving to another ZATCA environment")
    for field, value in body.model_dump().items():
        setattr(s, field, value.strip() if isinstance(value, str) else value)
    audit.record(db, actor_user_id=ctx.user.id, action="einvoicing.settings_changed", target_type="organization",
                 target_id=ctx.organization.id, details={"environment": s.environment}, ip_address=client_ip(request))
    db.commit()
    return _settings_out(db, ctx)


@router.post("/orgs/{org_id}/einvoicing/onboard", response_model=EInvoiceSettingsOut)
def onboard(body: OnboardIn, request: Request, ctx: OrgContext = Depends(require(P.ORG_MANAGE)),
            db: Session = Depends(get_db)):
    """Onboard the device with a one-time password from ZATCA's Fatoora portal (the sandbox takes 123345). Doing it
    again while onboarded renews the certificate, and the device's chain of documents carries on."""
    s = svc.settings_row(db)
    renewing = s.status == "onboarded"
    _errors(lambda: svc.onboard(db, request.app.state.settings, s, otp=body.otp, user_id=ctx.user.id,
                                http=request.app.state.einvoice_http))
    audit.record(db, actor_user_id=ctx.user.id, action="einvoicing.renewed" if renewing else "einvoicing.onboarded",
                 target_type="organization", target_id=ctx.organization.id, details={"environment": s.environment},
                 ip_address=client_ip(request))
    db.commit()
    return _settings_out(db, ctx)


@router.post("/orgs/{org_id}/einvoicing/off", response_model=EInvoiceSettingsOut)
def turn_off(request: Request, ctx: OrgContext = Depends(require(P.ORG_MANAGE)), db: Session = Depends(get_db)):
    """Stop issuing e-invoices. Documents already issued stay as they are; onboarding again starts a new device."""
    s = svc.settings_row(db)
    s.status = "off"
    audit.record(db, actor_user_id=ctx.user.id, action="einvoicing.turned_off", target_type="organization",
                 target_id=ctx.organization.id, details={"environment": s.environment}, ip_address=client_ip(request))
    db.commit()
    return _settings_out(db, ctx)


@router.get("/orgs/{org_id}/einvoicing/status", response_model=EInvoiceStatusOut)
def einvoicing_status(_: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    s = svc.active(db)
    return EInvoiceStatusOut(active=s is not None, environment=s.environment if s else None,
                             certificate_state=svc.certificate_state(s) if s else None,
                             certificate_expires_at=s.certificate_expires_at if s else None)


@router.get("/orgs/{org_id}/einvoicing/documents", response_model=list[EInvoiceSummary])
def list_documents(status_filter: Literal["pending", "reported", "cleared", "rejected"] | None = Query(default=None, alias="status"),
                   _: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    q = select(EInvoiceDocument).order_by(EInvoiceDocument.created_at.desc()).limit(100)
    if status_filter:
        q = q.where(EInvoiceDocument.status == status_filter)
    return [summary(d) for d in db.scalars(q)]


def _document(db: Session, doc_id: str) -> EInvoiceDocument:
    doc = db.get(EInvoiceDocument, doc_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "e-invoice not found")
    return doc


@router.get("/orgs/{org_id}/einvoicing/documents/{doc_id}/xml", response_class=Response)
def document_xml(doc_id: str, _: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    doc = _document(db, doc_id)
    return Response(doc.cleared_xml or doc.xml, media_type="application/xml; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{doc.number}.xml"'})


@router.post("/orgs/{org_id}/einvoicing/documents/{doc_id}/send", response_model=EInvoiceSummary)
def send_again(doc_id: str, request: Request, _: OrgContext = Depends(require(P.INVOICES_POST)),
               db: Session = Depends(get_db)):
    doc = _document(db, doc_id)
    if doc.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"only a pending document is sent again (this one is {doc.status})")
    svc.submit(db, request.app.state.settings, doc, http=request.app.state.einvoice_http)
    db.commit()
    return summary(doc)
