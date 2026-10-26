from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.accounting.models import Tax
from novara.modules.audit import service as audit
from novara.modules.invoicing import service as svc
from novara.modules.invoicing.lines import LineIn, resolve_lines
from novara.modules.invoicing.credit import credited_so_far
from novara.modules.invoicing.models import CreditNote, Invoice, InvoiceLine, Payment
from novara.modules.invoicing.zatca import qr_payload
from novara.einvoice import service as einvoice
from novara.einvoice.schemas import EInvoiceSummary, summary as einvoice_summary
from novara.modules.organizations.models import Organization
from novara.modules.partners.models import Customer
from novara.modules.projects import service as project_hours
from novara.modules.sales.service import order_has_invoice_links, order_of_invoice
from novara.money import CURRENCY_DIGITS, fmt_decimal, from_minor, to_minor

router = APIRouter(tags=["invoices"])


class InvoiceIn(BaseModel):
    customer_id: str
    issue_date: date
    due_date: date | None = None
    notes: str = Field(default="", max_length=5000)
    lines: list[LineIn] = Field(min_length=1, max_length=500)
    # Another currency than the organization's: line prices are in it, and the rate (units of the
    # organization's currency per unit of this one) books the invoice in the ledger.
    currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")
    exchange_rate: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=8)
    # Debit notes only: why more is charged (kept as it was when left out).
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _dates(self):
        if self.due_date is not None and self.due_date < self.issue_date:
            raise ValueError("due_date cannot be before issue_date")
        return self


class DebitNoteIn(BaseModel):
    issue_date: date
    due_date: date | None = None
    reason: str = Field(min_length=1, max_length=1000)
    notes: str = Field(default="", max_length=5000)
    lines: list[LineIn] = Field(min_length=1, max_length=500)
    # The invoice is in another currency: the rate for the debit note (default: the invoice's own).
    exchange_rate: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=8)

    @model_validator(mode="after")
    def _dates(self):
        if self.due_date is not None and self.due_date < self.issue_date:
            raise ValueError("due_date cannot be before issue_date")
        if not self.reason.strip():
            raise ValueError("a debit note needs its reason")
        return self


class PaymentIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    payment_date: date
    method: str = Field(pattern="^(bank|cash)$")
    reference: str = Field(default="", max_length=120)
    # Invoices in another currency: the rate on the payment date (required for them).
    exchange_rate: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=8)


class CancelIn(BaseModel):
    cancel_date: date


class LineOut(BaseModel):
    id: str
    position: int
    product_id: str | None
    description: str
    quantity: str
    unit_price: str
    discount_percent: str
    tax_id: str | None
    tax_rate: str
    net_amount: str
    # Quantity already credited by credit notes.
    qty_credited: str = "0"


class TaxLineOut(BaseModel):
    tax_id: str
    name: str
    rate: str
    base: str
    amount: str


class CreditNoteRef(BaseModel):
    id: str
    number: str
    issue_date: date
    total: str


class DebitNoteRef(BaseModel):
    id: str
    number: str | None
    status: str
    issue_date: date
    total: str


class InvoiceOut(BaseModel):
    id: str
    number: str | None
    status: str
    kind: Literal["invoice", "debit_note"] = "invoice"
    # A debit note: the invoice it adjusts, and why.
    debit_note_of_id: str | None = None
    debit_note_of_number: str | None = None
    reason: str = ""
    debit_notes: list[DebitNoteRef] = []
    customer_id: str
    customer_name: str
    issue_date: date
    due_date: date
    currency: str
    notes: str
    subtotal: str
    tax: str
    total: str
    amount_paid: str
    amount_credited: str
    amount_due: str
    journal_entry_id: str | None
    reversal_entry_id: str | None
    posted_at: datetime | None
    created_at: datetime
    credit_notes: list[CreditNoteRef] = []
    # Base64 TLV for the e-invoice QR code: phase 2 (signed) once e-invoicing is on, else phase 1 when the
    # organization has a VAT number.
    zatca_qr: str | None = None
    einvoice: EInvoiceSummary | None = None
    # Another currency: its rate and the amounts in the organization's currency (the ledger's).
    exchange_rate: str = "1"
    base_currency: str = ""
    base_total: str | None = None
    base_due: str | None = None
    # Set when the invoice was created from a sales order (its draft is then edited through the order).
    sales_order_id: str | None = None
    sales_order_number: str | None = None
    lines: list[LineOut]
    taxes: list[TaxLineOut]


class InvoiceSummary(BaseModel):
    id: str
    number: str | None
    status: str
    kind: Literal["invoice", "debit_note"] = "invoice"
    customer_id: str
    customer_name: str
    issue_date: date
    due_date: date
    currency: str
    total: str
    amount_due: str


class PaymentOut(BaseModel):
    id: str
    invoice_id: str
    payment_date: date
    amount: str
    method: str
    reference: str
    journal_entry_id: str
    created_at: datetime
    exchange_rate: str = "1"
    base_amount: str | None = None
    exchange_difference: str | None = None   # gain (+) or loss (-) in the organization's currency


def _money(minor: int, currency: str) -> str:
    return str(from_minor(minor, currency))


def invoice_out(db: Session, inv: Invoice) -> InvoiceOut:
    lines = svc.lines_of(db, inv)
    customer = db.get(Customer, inv.customer_id)
    groups = svc.tax_groups(lines, inv.currency)
    tax_names = {t.id: t.name for t in db.scalars(select(Tax).where(Tax.id.in_([g.tax_id for g in groups])))}
    c = inv.currency
    order = order_of_invoice(db, inv)
    org = db.get(Organization, inv.organization_id)
    notes = db.scalars(select(CreditNote).where(CreditNote.invoice_id == inv.id).order_by(CreditNote.number)).all()
    credited = credited_so_far(db, inv) if notes else {}
    original = db.get(Invoice, inv.debit_note_of_id) if inv.debit_note_of_id else None
    debits = db.scalars(select(Invoice).where(Invoice.debit_note_of_id == inv.id)
                        .order_by(Invoice.issue_date, Invoice.created_at)).all()
    qr = None
    doc = einvoice.document_for(db, "invoice", inv.id) if inv.status != "draft" else None
    if doc is not None:
        qr = einvoice.qr_of(doc)
    elif org.vat_number and inv.status not in ("draft", "cancelled") and inv.posted_at is not None:
        qr = qr_payload(seller=org.name, vat_number=org.vat_number, issued_at=inv.posted_at,
                        total=_money(inv.total_minor, c), vat_total=_money(inv.tax_minor, c))
    return InvoiceOut(
        id=inv.id, number=inv.number, status=inv.status, kind="debit_note" if original else "invoice",
        debit_note_of_id=original.id if original else None, debit_note_of_number=original.number if original else None,
        reason=inv.reason,
        debit_notes=[DebitNoteRef(id=d.id, number=d.number, status=d.status, issue_date=d.issue_date,
                                  total=_money(d.total_minor, d.currency)) for d in debits],
        customer_id=inv.customer_id, customer_name=customer.name,
        issue_date=inv.issue_date, due_date=inv.due_date, currency=c, notes=inv.notes,
        subtotal=_money(inv.subtotal_minor, c), tax=_money(inv.tax_minor, c), total=_money(inv.total_minor, c),
        amount_paid=_money(inv.amount_paid_minor, c), amount_credited=_money(inv.amount_credited_minor, c),
        amount_due=_money(inv.amount_due_minor, c),
        credit_notes=[CreditNoteRef(id=n.id, number=n.number, issue_date=n.issue_date, total=_money(n.total_minor, c))
                      for n in notes],
        zatca_qr=qr, einvoice=einvoice_summary(doc) if doc else None,
        exchange_rate=fmt_decimal(inv.exchange_rate), base_currency=org.default_currency,
        base_total=(_money(inv.base_total_minor, org.default_currency)
                    if c != org.default_currency and inv.status != "draft" else None),
        base_due=(_money(inv.base_due_minor, org.default_currency)
                  if c != org.default_currency and inv.status != "draft" else None),
        journal_entry_id=inv.journal_entry_id, reversal_entry_id=inv.reversal_entry_id, posted_at=inv.posted_at,
        created_at=inv.created_at, sales_order_id=order.id if order else None,
        sales_order_number=order.number if order else None,
        lines=[
            LineOut(id=l.id, position=l.position, product_id=l.product_id, description=l.description,
                    quantity=fmt_decimal(l.quantity), unit_price=fmt_decimal(l.unit_price),
                    discount_percent=fmt_decimal(l.discount_percent), tax_id=l.tax_id,
                    tax_rate=fmt_decimal(l.tax_rate), net_amount=_money(l.net_minor, c),
                    qty_credited=fmt_decimal(credited.get(l.id, (Decimal(0), 0))[0]))
            for l in lines
        ],
        taxes=[
            TaxLineOut(tax_id=g.tax_id, name=tax_names.get(g.tax_id, ""), rate=fmt_decimal(g.rate),
                       base=_money(g.base_minor, c), amount=_money(g.tax_minor, c))
            for g in groups
        ],
    )


def payment_out(p: Payment, currency: str, base: str | None = None) -> PaymentOut:
    foreign = base is not None and base != currency
    return PaymentOut(id=p.id, invoice_id=p.invoice_id, payment_date=p.payment_date,
                      amount=_money(p.amount_minor, currency), method=p.method, reference=p.reference,
                      journal_entry_id=p.journal_entry_id, created_at=p.created_at,
                      exchange_rate=fmt_decimal(p.exchange_rate),
                      base_amount=_money(p.base_amount_minor, base) if foreign else None,
                      exchange_difference=_money(p.fx_minor, base) if foreign else None)


def _unprocessable(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, msg)


def _build_lines(db: Session, body: InvoiceIn) -> list[InvoiceLine]:
    return [InvoiceLine(**fields) for fields in resolve_lines(db, body.lines)]


def _apply_header(db: Session, inv: Invoice, body: InvoiceIn) -> None:
    customer = db.get(Customer, body.customer_id)
    if customer is None or not customer.active:
        raise _unprocessable("unknown or archived customer")
    inv.customer_id = customer.id
    inv.issue_date = body.issue_date
    inv.due_date = body.due_date or body.issue_date
    inv.notes = body.notes
    base = svc.base_currency(db)
    currency = body.currency or base
    if currency not in CURRENCY_DIGITS:
        raise _unprocessable(f"unsupported currency: {currency}")
    if currency != base:
        if body.exchange_rate is None:
            raise _unprocessable(f"an invoice in {currency} needs the exchange rate to {base}")
        if any(l.unit_price is None for l in body.lines):
            raise _unprocessable(f"give each line's price in {currency} (product prices are in {base})")
    inv.currency = currency
    inv.exchange_rate = body.exchange_rate if currency != base else Decimal(1)


def _get_invoice(db: Session, invoice_id: str, *, lock: bool = False) -> Invoice:
    inv = svc.lock_invoice(db, invoice_id) if lock else db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invoice not found")
    return inv


def _domain_errors(fn):
    try:
        return fn()
    except svc.InvoiceStateError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except (svc.InvoiceValidationError, einvoice.EInvoiceError) as e:
        raise _unprocessable(str(e))
    except einvoice.EInvoiceState as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.get("/orgs/{org_id}/invoices", response_model=list[InvoiceSummary])
def list_invoices(
    status_filter: str | None = Query(default=None, alias="status"),
    customer_id: str | None = None,
    q: str | None = Query(default=None, max_length=100),   # part of the number or the customer's name
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: OrgContext = Depends(require(P.INVOICES_READ)),
    db: Session = Depends(get_db),
):
    stmt = (select(Invoice, Customer.name).join(Customer, Customer.id == Invoice.customer_id)
            .order_by(Invoice.issue_date.desc(), Invoice.created_at.desc()).limit(limit).offset(offset))
    if status_filter:
        stmt = stmt.where(Invoice.status == status_filter)
    if customer_id:
        stmt = stmt.where(Invoice.customer_id == customer_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Invoice.number).like(like), func.lower(Customer.name).like(like)))
    out = []
    for inv, name in db.execute(stmt):
        due = inv.amount_due_minor
        out.append(InvoiceSummary(id=inv.id, number=inv.number, status=inv.status,
                                  kind="debit_note" if inv.debit_note_of_id else "invoice", customer_id=inv.customer_id,
                                  customer_name=name, issue_date=inv.issue_date, due_date=inv.due_date,
                                  currency=inv.currency, total=_money(inv.total_minor, inv.currency),
                                  amount_due=_money(due, inv.currency)))
    return out


@router.post("/orgs/{org_id}/invoices", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def create_invoice(body: InvoiceIn, request: Request, ctx: OrgContext = Depends(require(P.INVOICES_WRITE)),
                   db: Session = Depends(get_db)):
    inv = add_draft_invoice(db, ctx, body, ip=client_ip(request))
    db.commit()
    return invoice_out(db, inv)


def add_draft_invoice(db: Session, ctx: OrgContext, body: InvoiceIn, *, ip: str = "", via: str | None = None) -> Invoice:
    """Create a draft invoice and its audit entry in the current transaction (no commit)."""
    inv = Invoice(currency=ctx.organization.default_currency, created_by=ctx.user.id)
    _apply_header(db, inv, body)
    lines = _build_lines(db, body)
    svc.recompute(inv, lines)
    db.add(inv)
    db.flush()
    for line in lines:
        line.invoice_id = inv.id
    db.add_all(lines)
    details = {"customer_id": inv.customer_id, "total": _money(inv.total_minor, inv.currency)}
    audit.record(db, actor_user_id=ctx.user.id, action="invoice.created", target_type="invoice", target_id=inv.id,
                 details=details | ({"via": via} if via else {}), ip_address=ip)
    return inv


@router.post("/orgs/{org_id}/invoices/{invoice_id}/debit-notes", response_model=InvoiceOut,
             status_code=status.HTTP_201_CREATED)
def create_debit_note(invoice_id: str, body: DebitNoteIn, request: Request,
                      ctx: OrgContext = Depends(require(P.INVOICES_WRITE)), db: Session = Depends(get_db)):
    """A draft debit note charging more on a posted invoice: the same customer and currency, lines of its own."""
    original = _get_invoice(db, invoice_id)
    if original.status in ("draft", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"only a posted invoice can have a debit note (status: {original.status})")
    if original.debit_note_of_id:
        raise _unprocessable("a debit note adjusts an invoice, not another debit note")
    if body.issue_date < original.issue_date:
        raise _unprocessable("a debit note can't be dated before the invoice it adjusts")
    foreign = original.currency != svc.base_currency(db)
    header = InvoiceIn(customer_id=original.customer_id, issue_date=body.issue_date, due_date=body.due_date,
                       notes=body.notes, lines=body.lines, currency=original.currency,
                       exchange_rate=(body.exchange_rate or original.exchange_rate) if foreign else None)
    inv = add_draft_invoice(db, ctx, header, ip=client_ip(request))
    inv.debit_note_of_id, inv.reason = original.id, body.reason.strip()
    audit.record(db, actor_user_id=ctx.user.id, action="invoice.debit_note_created", target_type="invoice",
                 target_id=original.id, details={"number": original.number, "debit_note_id": inv.id,
                                                 "total": _money(inv.total_minor, inv.currency)},
                 ip_address=client_ip(request))
    db.commit()
    return invoice_out(db, inv)


@router.get("/orgs/{org_id}/invoices/{invoice_id}", response_model=InvoiceOut)
def read_invoice(invoice_id: str, _: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    return invoice_out(db, _get_invoice(db, invoice_id))


@router.put("/orgs/{org_id}/invoices/{invoice_id}", response_model=InvoiceOut)
def replace_draft(invoice_id: str, body: InvoiceIn, request: Request,
                  ctx: OrgContext = Depends(require(P.INVOICES_WRITE)), db: Session = Depends(get_db)):
    """Replace a draft's header and lines. Posted invoices are final."""
    inv = _get_invoice(db, invoice_id, lock=True)
    if inv.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "only draft invoices can be edited")
    if order_has_invoice_links(db, inv):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "this draft was created from a sales order; change the order or delete the draft and re-invoice")
    if project_hours.has_hours(db, inv.id):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "this draft bills a project's hours; delete it and invoice the hours again")
    if inv.debit_note_of_id:
        if body.customer_id != inv.customer_id or (body.currency or svc.base_currency(db)) != inv.currency:
            raise _unprocessable("a debit note keeps the customer and currency of the invoice it adjusts")
        if body.reason is not None:
            if not body.reason.strip():
                raise _unprocessable("a debit note needs its reason")
            inv.reason = body.reason.strip()
    _apply_header(db, inv, body)
    new_lines = _build_lines(db, body)
    for old in svc.lines_of(db, inv):
        db.delete(old)
    db.flush()
    for line in new_lines:
        line.invoice_id = inv.id
    db.add_all(new_lines)
    svc.recompute(inv, new_lines)
    audit.record(db, actor_user_id=ctx.user.id, action="invoice.updated", target_type="invoice", target_id=inv.id,
                 details={"total": _money(inv.total_minor, inv.currency)}, ip_address=client_ip(request))
    db.commit()
    return invoice_out(db, inv)


@router.delete("/orgs/{org_id}/invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_draft(invoice_id: str, request: Request, ctx: OrgContext = Depends(require(P.INVOICES_WRITE)),
                 db: Session = Depends(get_db)):
    inv = _get_invoice(db, invoice_id, lock=True)
    if inv.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "posted invoices cannot be deleted; cancel them instead")
    project_hours.release_invoice(db, inv.id)  # its hours become billable again
    for line in svc.lines_of(db, inv):
        db.delete(line)
    db.flush()  # lines first: there is no ORM relationship to order the deletes
    audit.record(db, actor_user_id=ctx.user.id, action="invoice.deleted", target_type="invoice", target_id=inv.id,
                 details={}, ip_address=client_ip(request))
    db.delete(inv)
    db.commit()


@router.post("/orgs/{org_id}/invoices/{invoice_id}/post", response_model=InvoiceOut)
def post_invoice(invoice_id: str, request: Request, ctx: OrgContext = Depends(require(P.INVOICES_POST)),
                 db: Session = Depends(get_db)):
    inv = _get_invoice(db, invoice_id, lock=True)
    _domain_errors(lambda: einvoice.check_buyer(db, inv.customer_id))
    entry = _domain_errors(lambda: svc.post_invoice(db, inv, user_id=ctx.user.id))
    # E-invoicing on: the signed document is part of posting (same transaction), then it goes to ZATCA.
    doc = _domain_errors(lambda: einvoice.issue_for_invoice(db, request.app.state.settings, inv))
    audit.record(db, actor_user_id=ctx.user.id, action="invoice.posted", target_type="invoice", target_id=inv.id,
                 details={"number": inv.number, "journal_entry": entry.number,
                          "total": _money(inv.total_minor, inv.currency)}, ip_address=client_ip(request))
    db.commit()
    if doc is not None:
        einvoice.submit(db, request.app.state.settings, doc, http=request.app.state.einvoice_http)
        db.commit()
    return invoice_out(db, inv)


@router.post("/orgs/{org_id}/invoices/{invoice_id}/payments", response_model=PaymentOut,
             status_code=status.HTTP_201_CREATED)
def add_payment(
    invoice_id: str,
    body: PaymentIn,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, max_length=100),
    ctx: OrgContext = Depends(require(P.PAYMENTS_WRITE)),
    db: Session = Depends(get_db),
):
    """Register a customer payment. Send an Idempotency-Key header so a retried request can't pay twice."""
    payment, created, inv = add_payment_record(db, ctx, invoice_id, body, ip=client_ip(request),
                                               idempotency_key=idempotency_key)
    if created:
        try:
            db.commit()
        except IntegrityError:
            # A concurrent request with the same Idempotency-Key won the race; return its payment.
            db.rollback()
            if not idempotency_key:
                raise
            payment = db.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
            if payment is None or payment.invoice_id != invoice_id:
                raise HTTPException(status.HTTP_409_CONFLICT, "idempotency key already used for a different payment")
            response.status_code = status.HTTP_200_OK
    else:
        response.status_code = status.HTTP_200_OK
    return payment_out(payment, inv.currency, svc.base_currency(db))


def add_payment_record(db: Session, ctx: OrgContext, invoice_id: str, body: PaymentIn, *, ip: str = "",
                       idempotency_key: str | None = None, via: str | None = None) -> tuple[Payment, bool, Invoice]:
    """Register a payment and its audit entry in the current transaction (no commit)."""
    inv = _get_invoice(db, invoice_id, lock=True)
    amount_minor = to_minor(body.amount, inv.currency)
    if from_minor(amount_minor, inv.currency) != body.amount:
        raise _unprocessable(f"amount has more decimals than {inv.currency} allows")
    payment, created = _domain_errors(lambda: svc.register_payment(
        db, inv, amount_minor=amount_minor, payment_date=body.payment_date, method=body.method,
        reference=body.reference, user_id=ctx.user.id, idempotency_key=idempotency_key,
        exchange_rate=body.exchange_rate,
    ))
    if created:
        details = {"payment_id": payment.id, "amount": str(body.amount), "method": body.method}
        audit.record(db, actor_user_id=ctx.user.id, action="invoice.payment_registered", target_type="invoice",
                     target_id=inv.id, details=details | ({"via": via} if via else {}), ip_address=ip)
    return payment, created, inv


@router.get("/orgs/{org_id}/invoices/{invoice_id}/payments", response_model=list[PaymentOut])
def list_payments(invoice_id: str, _: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    inv = _get_invoice(db, invoice_id)
    rows = db.scalars(select(Payment).where(Payment.invoice_id == inv.id).order_by(Payment.payment_date, Payment.created_at))
    base = svc.base_currency(db)
    return [payment_out(p, inv.currency, base) for p in rows]


@router.post("/orgs/{org_id}/invoices/{invoice_id}/cancel", response_model=InvoiceOut)
def cancel_invoice(invoice_id: str, body: CancelIn, request: Request,
                   ctx: OrgContext = Depends(require(P.INVOICES_CANCEL)), db: Session = Depends(get_db)):
    inv = _get_invoice(db, invoice_id, lock=True)
    reversal = _domain_errors(lambda: svc.cancel_invoice(db, inv, user_id=ctx.user.id, cancel_date=body.cancel_date))
    audit.record(db, actor_user_id=ctx.user.id, action="invoice.cancelled", target_type="invoice", target_id=inv.id,
                 details={"number": inv.number, "reversal_entry": reversal.number}, ip_address=client_ip(request))
    db.commit()
    return invoice_out(db, inv)


# ---------------------------------------------------------------- reports

AGING_BUCKETS = (("current", None, 0), ("1_30", 1, 30), ("31_60", 31, 60), ("61_90", 61, 90), ("over_90", 91, None))


class AgingRow(BaseModel):
    customer_id: str
    customer_name: str
    buckets: dict[str, str]
    total_due: str
    invoices: int


class ReceivablesOut(BaseModel):
    as_of: date
    currency: str
    customers: list[AgingRow]
    totals: dict[str, str]
    total_due: str


def _bucket(days_overdue: int) -> str:
    for name, low, high in AGING_BUCKETS:
        if (low is None or days_overdue >= low) and (high is None or days_overdue <= high):
            return name
    raise AssertionError(days_overdue)


@router.get("/orgs/{org_id}/reports/receivables", response_model=ReceivablesOut)
def receivables_aging(as_of: date | None = None, ctx: OrgContext = Depends(require(P.INVOICES_READ)),
                      db: Session = Depends(get_db)):
    """Open customer balances grouped by how many days past the due date they are."""
    as_of = as_of or date.today()
    currency = ctx.organization.default_currency
    rows = db.execute(
        select(Invoice, Customer.name).join(Customer, Customer.id == Invoice.customer_id)
        .where(Invoice.status.in_(("posted", "partially_paid")), Invoice.issue_date <= as_of)
    ).all()
    per_customer: dict[str, dict] = {}
    totals: dict[str, int] = defaultdict(int)
    for inv, name in rows:
        due = inv.base_due_minor            # in the organization's currency, whatever the invoice's
        bucket = _bucket((as_of - inv.due_date).days)
        c = per_customer.setdefault(inv.customer_id, {"name": name, "buckets": defaultdict(int), "count": 0})
        c["buckets"][bucket] += due
        c["count"] += 1
        totals[bucket] += due
    names = [b[0] for b in AGING_BUCKETS]
    customers = sorted(per_customer.items(), key=lambda kv: -sum(kv[1]["buckets"].values()))
    return ReceivablesOut(
        as_of=as_of,
        currency=currency,
        customers=[
            AgingRow(customer_id=cid, customer_name=c["name"], invoices=c["count"],
                     buckets={n: _money(c["buckets"][n], currency) for n in names},
                     total_due=_money(sum(c["buckets"].values()), currency))
            for cid, c in customers
        ],
        totals={n: _money(totals[n], currency) for n in names},
        total_due=_money(sum(totals.values()), currency),
    )
