from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara import idempotency
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.accounting.models import Tax
from novara.modules.audit import service as audit
from novara.modules.inventory import service as stock
from novara.modules.purchasing import credit, service as svc
from novara.modules.partners.models import Vendor
from novara.modules.purchasing.models import VendorBill, VendorCredit, VendorCreditLine, VendorCreditTax, VendorRefund
from novara.money import fmt_decimal, from_minor, to_minor

router = APIRouter(tags=["vendor credits"])


class VendorCreditLineIn(BaseModel):
    bill_line_id: str
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    # Units sent back to the vendor (goods received against a purchase order only).
    returned_quantity: Decimal = Field(default=Decimal(0), ge=0, max_digits=18, decimal_places=4)


class VendorCreditIn(BaseModel):
    issue_date: date
    reason: str = Field(min_length=1, max_length=500)
    vendor_reference: str = Field(default="", max_length=60)
    lines: list[VendorCreditLineIn] = Field(min_length=1, max_length=500)


class VendorRefundIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    refund_date: date
    method: str = Field(pattern="^(bank|cash)$")
    reference: str = Field(default="", max_length=120)


class VendorCreditLineOut(BaseModel):
    bill_line_id: str
    description: str
    quantity: str
    unit_cost: str
    tax_rate: str
    net_amount: str
    returned_quantity: str


class VendorCreditTaxOut(BaseModel):
    tax_id: str
    name: str
    rate: str
    base: str
    amount: str


class VendorCreditOut(BaseModel):
    id: str
    number: str
    bill_id: str
    bill_number: str
    vendor_id: str
    vendor_name: str
    vendor_reference: str
    issue_date: date
    currency: str
    reason: str
    subtotal: str
    tax: str
    total: str
    applied: str
    refunded: str
    refundable: str  # what the vendor still owes back
    stock_reference: str
    journal_entry_id: str
    created_at: datetime
    lines: list[VendorCreditLineOut]
    taxes: list[VendorCreditTaxOut]


class VendorCreditSummary(BaseModel):
    id: str
    number: str
    bill_id: str
    bill_number: str
    vendor_id: str
    vendor_name: str
    issue_date: date
    total: str
    refundable: str


class VendorRefundOut(BaseModel):
    id: str
    credit_id: str
    refund_date: date
    amount: str
    method: str
    reference: str
    journal_entry_id: str
    created_at: datetime


def _money(minor: int, currency: str) -> str:
    return str(from_minor(minor, currency))


def credit_out(db: Session, vc: VendorCredit) -> VendorCreditOut:
    c = vc.currency
    bill = db.get(VendorBill, vc.bill_id)
    lines = db.scalars(select(VendorCreditLine).where(VendorCreditLine.credit_id == vc.id)
                       .order_by(VendorCreditLine.position)).all()
    taxes = db.scalars(select(VendorCreditTax).where(VendorCreditTax.credit_id == vc.id)).all()
    names = {t.id: t.name for t in db.scalars(select(Tax).where(Tax.id.in_([t.tax_id for t in taxes])))}
    return VendorCreditOut(
        id=vc.id, number=vc.number, bill_id=vc.bill_id, bill_number=bill.number or "", vendor_id=vc.vendor_id,
        vendor_name=db.get(Vendor, vc.vendor_id).name, vendor_reference=vc.vendor_reference, issue_date=vc.issue_date,
        currency=c, reason=vc.reason, subtotal=_money(vc.subtotal_minor, c), tax=_money(vc.tax_minor, c),
        total=_money(vc.total_minor, c), applied=_money(vc.applied_minor, c), refunded=_money(vc.refunded_minor, c),
        refundable=_money(credit.refundable(vc), c), stock_reference=vc.stock_reference,
        journal_entry_id=vc.journal_entry_id, created_at=vc.created_at,
        lines=[VendorCreditLineOut(bill_line_id=l.bill_line_id, description=l.description,
                                   quantity=fmt_decimal(l.quantity), unit_cost=fmt_decimal(l.unit_cost),
                                   tax_rate=fmt_decimal(l.tax_rate), net_amount=_money(l.net_minor, c),
                                   returned_quantity=fmt_decimal(l.returned_quantity)) for l in lines],
        taxes=[VendorCreditTaxOut(tax_id=t.tax_id, name=names.get(t.tax_id, ""), rate=fmt_decimal(t.rate),
                                  base=_money(t.base_minor, c), amount=_money(t.tax_minor, c)) for t in taxes],
    )


def _domain(fn):
    try:
        return fn()
    except (svc.PurchaseStateError, stock.InsufficientStock) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except (svc.PurchaseValidationError, stock.InventoryError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


def _get_credit(db: Session, credit_id: str, *, lock: bool = False) -> VendorCredit:
    vc = credit.lock_credit(db, credit_id) if lock else db.get(VendorCredit, credit_id)
    if vc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "vendor credit not found")
    return vc


@router.post("/orgs/{org_id}/bills/{bill_id}/credits", response_model=VendorCreditOut,
             status_code=status.HTTP_201_CREATED)
def create_vendor_credit(bill_id: str, body: VendorCreditIn, request: Request, response: Response,
                         idempotency_key: str | None = Header(default=None, max_length=100),
                         ctx: OrgContext = Depends(require(P.BILLS_POST)), db: Session = Depends(get_db)):
    """Record a vendor's credit against a posted bill; optionally send received goods back."""
    bill = svc.lock_bill(db, bill_id)
    if bill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "bill not found")
    if any(l.returned_quantity for l in body.lines) and not ctx.has(P.INVENTORY_WRITE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "sending goods back out of stock needs inventory.write")
    h = idempotency.request_hash({"bill": bill_id, **body.model_dump(mode="json")})
    try:
        previous = idempotency.replayed(db, "bill.vendor_credit", idempotency_key, h)
    except idempotency.IdempotencyConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    if previous:
        response.status_code = status.HTTP_200_OK
        return credit_out(db, _get_credit(db, previous))
    requests = [credit.VendorCreditRequest(l.bill_line_id, l.quantity, l.returned_quantity) for l in body.lines]
    vc = _domain(lambda: credit.create_vendor_credit(
        db, bill, issue_date=body.issue_date, reason=body.reason, vendor_reference=body.vendor_reference,
        requests=requests, user_id=ctx.user.id))
    idempotency.remember(db, "bill.vendor_credit", idempotency_key, h, vc.id)
    audit.record(db, actor_user_id=ctx.user.id, action="vendor_credit.created", target_type="vendor_credit",
                 target_id=vc.id, details={"number": vc.number, "bill": bill.number,
                                           "total": _money(vc.total_minor, vc.currency),
                                           **({"returned": vc.stock_reference} if vc.stock_reference else {})},
                 ip_address=client_ip(request))
    db.commit()
    return credit_out(db, vc)


@router.get("/orgs/{org_id}/vendor-credits", response_model=list[VendorCreditSummary])
def list_vendor_credits(bill_id: str | None = None, vendor_id: str | None = None,
                        limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
                        _: OrgContext = Depends(require(P.PURCHASES_READ)), db: Session = Depends(get_db)):
    stmt = (select(VendorCredit, VendorBill.number, Vendor.name)
            .join(VendorBill, VendorBill.id == VendorCredit.bill_id)
            .join(Vendor, Vendor.id == VendorCredit.vendor_id)
            .order_by(VendorCredit.issue_date.desc(), VendorCredit.number.desc()).limit(limit).offset(offset))
    if bill_id:
        stmt = stmt.where(VendorCredit.bill_id == bill_id)
    if vendor_id:
        stmt = stmt.where(VendorCredit.vendor_id == vendor_id)
    return [VendorCreditSummary(id=vc.id, number=vc.number, bill_id=vc.bill_id, bill_number=number or "",
                                vendor_id=vc.vendor_id, vendor_name=name, issue_date=vc.issue_date,
                                total=_money(vc.total_minor, vc.currency),
                                refundable=_money(credit.refundable(vc), vc.currency))
            for vc, number, name in db.execute(stmt)]


@router.get("/orgs/{org_id}/vendor-credits/{credit_id}", response_model=VendorCreditOut)
def read_vendor_credit(credit_id: str, _: OrgContext = Depends(require(P.PURCHASES_READ)),
                       db: Session = Depends(get_db)):
    return credit_out(db, _get_credit(db, credit_id))


def _refund_out(r: VendorRefund, currency: str) -> VendorRefundOut:
    return VendorRefundOut(id=r.id, credit_id=r.credit_id, refund_date=r.refund_date,
                           amount=_money(r.amount_minor, currency), method=r.method, reference=r.reference,
                           journal_entry_id=r.journal_entry_id, created_at=r.created_at)


@router.get("/orgs/{org_id}/vendor-credits/{credit_id}/refunds", response_model=list[VendorRefundOut])
def list_vendor_refunds(credit_id: str, _: OrgContext = Depends(require(P.PURCHASES_READ)),
                        db: Session = Depends(get_db)):
    vc = _get_credit(db, credit_id)
    rows = db.scalars(select(VendorRefund).where(VendorRefund.credit_id == vc.id).order_by(VendorRefund.refund_date))
    return [_refund_out(r, vc.currency) for r in rows]


@router.post("/orgs/{org_id}/vendor-credits/{credit_id}/refunds", response_model=VendorRefundOut,
             status_code=status.HTTP_201_CREATED)
def record_vendor_refund(credit_id: str, body: VendorRefundIn, request: Request, response: Response,
                         idempotency_key: str | None = Header(default=None, max_length=100),
                         ctx: OrgContext = Depends(require(P.PAYMENTS_WRITE)), db: Session = Depends(get_db)):
    """Record money the vendor paid back (the part of a credit that didn't reduce an open bill)."""
    vc = _get_credit(db, credit_id, lock=True)
    record, created = _domain(lambda: credit.record_refund(
        db, vc, amount_minor=to_minor(body.amount, vc.currency), refund_date=body.refund_date, method=body.method,
        reference=body.reference, user_id=ctx.user.id, idempotency_key=idempotency_key))
    if created:
        audit.record(db, actor_user_id=ctx.user.id, action="vendor_credit.refunded", target_type="vendor_credit",
                     target_id=vc.id, details={"number": vc.number, "amount": _money(record.amount_minor, vc.currency)},
                     ip_address=client_ip(request))
    else:
        response.status_code = status.HTTP_200_OK
    db.commit()
    return _refund_out(record, vc.currency)
