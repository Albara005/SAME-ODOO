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
from novara.modules.invoicing import credit, service as svc
from novara.modules.invoicing.models import CreditNote, CreditNoteLine, CreditNoteTax, Invoice, Refund
from novara.modules.invoicing.zatca import qr_payload
from novara.modules.organizations.models import Organization
from novara.modules.partners.models import Customer
from novara.money import fmt_decimal, from_minor, to_minor

router = APIRouter(tags=["credit notes"])


class CreditLineIn(BaseModel):
    invoice_line_id: str
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    # Units coming back into stock (goods delivered from a sales order only).
    returned_quantity: Decimal = Field(default=Decimal(0), ge=0, max_digits=18, decimal_places=4)


class CreditNoteIn(BaseModel):
    issue_date: date
    reason: str = Field(min_length=1, max_length=500)
    lines: list[CreditLineIn] = Field(min_length=1, max_length=500)


class RefundIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    refund_date: date
    method: str = Field(pattern="^(bank|cash)$")
    reference: str = Field(default="", max_length=120)


class CreditLineOut(BaseModel):
    invoice_line_id: str
    description: str
    quantity: str
    unit_price: str
    discount_percent: str
    tax_rate: str
    net_amount: str
    returned_quantity: str


class CreditTaxOut(BaseModel):
    tax_id: str
    name: str
    rate: str
    base: str
    amount: str


class CreditNoteOut(BaseModel):
    id: str
    number: str
    invoice_id: str
    invoice_number: str
    customer_id: str
    customer_name: str
    issue_date: date
    currency: str
    reason: str
    subtotal: str
    tax: str
    total: str
    applied: str
    refunded: str
    refundable: str
    stock_reference: str
    journal_entry_id: str
    zatca_qr: str | None
    created_at: datetime
    lines: list[CreditLineOut]
    taxes: list[CreditTaxOut]


class CreditNoteSummary(BaseModel):
    id: str
    number: str
    invoice_id: str
    invoice_number: str
    customer_name: str
    issue_date: date
    total: str
    refundable: str


class RefundOut(BaseModel):
    id: str
    credit_note_id: str
    refund_date: date
    amount: str
    method: str
    reference: str
    journal_entry_id: str
    created_at: datetime


def _money(minor: int, currency: str) -> str:
    return str(from_minor(minor, currency))


def _refundable(note: CreditNote) -> int:
    return note.total_minor - note.applied_minor - note.refunded_minor


def credit_note_out(db: Session, note: CreditNote) -> CreditNoteOut:
    c = note.currency
    invoice = db.get(Invoice, note.invoice_id)
    lines = db.scalars(select(CreditNoteLine).where(CreditNoteLine.credit_note_id == note.id)
                       .order_by(CreditNoteLine.position)).all()
    taxes = db.scalars(select(CreditNoteTax).where(CreditNoteTax.credit_note_id == note.id)).all()
    names = {t.id: t.name for t in db.scalars(select(Tax).where(Tax.id.in_([t.tax_id for t in taxes])))}
    org = db.get(Organization, note.organization_id)
    qr = qr_payload(seller=org.name, vat_number=org.vat_number, issued_at=note.created_at,
                    total=_money(note.total_minor, c), vat_total=_money(note.tax_minor, c)) if org.vat_number else None
    return CreditNoteOut(
        id=note.id, number=note.number, invoice_id=note.invoice_id, invoice_number=invoice.number or "",
        customer_id=note.customer_id, customer_name=db.get(Customer, note.customer_id).name,
        issue_date=note.issue_date, currency=c, reason=note.reason, subtotal=_money(note.subtotal_minor, c),
        tax=_money(note.tax_minor, c), total=_money(note.total_minor, c), applied=_money(note.applied_minor, c),
        refunded=_money(note.refunded_minor, c), refundable=_money(_refundable(note), c),
        stock_reference=note.stock_reference, journal_entry_id=note.journal_entry_id, zatca_qr=qr,
        created_at=note.created_at,
        lines=[CreditLineOut(invoice_line_id=l.invoice_line_id, description=l.description,
                             quantity=fmt_decimal(l.quantity), unit_price=fmt_decimal(l.unit_price),
                             discount_percent=fmt_decimal(l.discount_percent), tax_rate=fmt_decimal(l.tax_rate),
                             net_amount=_money(l.net_minor, c), returned_quantity=fmt_decimal(l.returned_quantity))
               for l in lines],
        taxes=[CreditTaxOut(tax_id=t.tax_id, name=names.get(t.tax_id, ""), rate=fmt_decimal(t.rate),
                            base=_money(t.base_minor, c), amount=_money(t.tax_minor, c)) for t in taxes],
    )


def _domain(fn):
    try:
        return fn()
    except (svc.InvoiceStateError, stock.InsufficientStock) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except (svc.InvoiceValidationError, stock.InventoryError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


def _get_note(db: Session, note_id: str, *, lock: bool = False) -> CreditNote:
    note = credit.lock_credit_note(db, note_id) if lock else db.get(CreditNote, note_id)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "credit note not found")
    return note


@router.post("/orgs/{org_id}/invoices/{invoice_id}/credit-notes", response_model=CreditNoteOut,
             status_code=status.HTTP_201_CREATED)
def create_credit_note(invoice_id: str, body: CreditNoteIn, request: Request, response: Response,
                       idempotency_key: str | None = Header(default=None, max_length=100),
                       ctx: OrgContext = Depends(require(P.INVOICES_CREDIT)), db: Session = Depends(get_db)):
    """Credit some or all of a posted invoice; optionally take returned goods back into stock."""
    invoice = svc.lock_invoice(db, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invoice not found")
    if any(l.returned_quantity for l in body.lines) and not ctx.has(P.INVENTORY_WRITE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "taking goods back into stock needs inventory.write")
    h = idempotency.request_hash({"invoice": invoice_id, **body.model_dump(mode="json")})
    try:
        previous = idempotency.replayed(db, "invoice.credit_note", idempotency_key, h)
    except idempotency.IdempotencyConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    if previous:
        response.status_code = status.HTTP_200_OK
        return credit_note_out(db, _get_note(db, previous))
    requests = [credit.CreditRequest(l.invoice_line_id, l.quantity, l.returned_quantity) for l in body.lines]
    note = _domain(lambda: credit.create_credit_note(db, invoice, issue_date=body.issue_date, reason=body.reason,
                                                     requests=requests, user_id=ctx.user.id))
    idempotency.remember(db, "invoice.credit_note", idempotency_key, h, note.id)
    audit.record(db, actor_user_id=ctx.user.id, action="credit_note.created", target_type="credit_note",
                 target_id=note.id, details={"number": note.number, "invoice": invoice.number,
                                             "total": _money(note.total_minor, note.currency),
                                             **({"returned": note.stock_reference} if note.stock_reference else {})},
                 ip_address=client_ip(request))
    db.commit()
    return credit_note_out(db, note)


@router.get("/orgs/{org_id}/credit-notes", response_model=list[CreditNoteSummary])
def list_credit_notes(invoice_id: str | None = None, customer_id: str | None = None,
                      limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
                      _: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    stmt = (select(CreditNote, Invoice.number, Customer.name)
            .join(Invoice, Invoice.id == CreditNote.invoice_id).join(Customer, Customer.id == CreditNote.customer_id)
            .order_by(CreditNote.issue_date.desc(), CreditNote.number.desc()).limit(limit).offset(offset))
    if invoice_id:
        stmt = stmt.where(CreditNote.invoice_id == invoice_id)
    if customer_id:
        stmt = stmt.where(CreditNote.customer_id == customer_id)
    return [CreditNoteSummary(id=n.id, number=n.number, invoice_id=n.invoice_id, invoice_number=inv_no or "",
                              customer_name=name, issue_date=n.issue_date, total=_money(n.total_minor, n.currency),
                              refundable=_money(_refundable(n), n.currency))
            for n, inv_no, name in db.execute(stmt)]


@router.get("/orgs/{org_id}/credit-notes/{note_id}", response_model=CreditNoteOut)
def read_credit_note(note_id: str, _: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    return credit_note_out(db, _get_note(db, note_id))


def _refund_out(r: Refund, currency: str) -> RefundOut:
    return RefundOut(id=r.id, credit_note_id=r.credit_note_id, refund_date=r.refund_date,
                     amount=_money(r.amount_minor, currency), method=r.method, reference=r.reference,
                     journal_entry_id=r.journal_entry_id, created_at=r.created_at)


@router.get("/orgs/{org_id}/credit-notes/{note_id}/refunds", response_model=list[RefundOut])
def list_refunds(note_id: str, _: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    note = _get_note(db, note_id)
    rows = db.scalars(select(Refund).where(Refund.credit_note_id == note.id).order_by(Refund.refund_date))
    return [_refund_out(r, note.currency) for r in rows]


@router.post("/orgs/{org_id}/credit-notes/{note_id}/refunds", response_model=RefundOut,
             status_code=status.HTTP_201_CREATED)
def refund(note_id: str, body: RefundIn, request: Request, response: Response,
           idempotency_key: str | None = Header(default=None, max_length=100),
           ctx: OrgContext = Depends(require(P.PAYMENTS_WRITE)), db: Session = Depends(get_db)):
    """Pay back what a credit note owes the customer (the part that didn't reduce an open invoice)."""
    note = _get_note(db, note_id, lock=True)
    record, created = _domain(lambda: credit.refund(
        db, note, amount_minor=to_minor(body.amount, note.currency), refund_date=body.refund_date,
        method=body.method, reference=body.reference, user_id=ctx.user.id, idempotency_key=idempotency_key))
    if created:
        audit.record(db, actor_user_id=ctx.user.id, action="credit_note.refunded", target_type="credit_note",
                     target_id=note.id, details={"number": note.number,
                                                 "amount": _money(record.amount_minor, note.currency)},
                     ip_address=client_ip(request))
        db.commit()
    else:
        response.status_code = status.HTTP_200_OK
    return _refund_out(record, note.currency)
