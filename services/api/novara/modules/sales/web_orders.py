"""Order requests from published App Studio sites.

A site holding a data key with the "orders" scope can send what a visitor wants to buy:

    POST /api/v1/public/orders     (X-Novara-Key header)
    {"name": "...", "email": "...", "phone": "...", "note": "...", "lines": [{"product_id": "...", "quantity": 2}]}

Nothing is reserved, posted or promised: the request waits in Sales until a person turns it
into a quotation (finding or creating the customer) or dismisses it. With the "checkout" scope a site can send
"pay": true instead: the request waits for the money (payments/service.py makes the payment page), and when the
provider confirms it the request becomes the customer's invoice, posted and paid (invoice_paid_order). Prices are always taken
from the catalog on the server, never from the site. Protection against abuse: the key's
scope, a hidden "website" field people leave empty (bots fill it in; such requests are
answered as if accepted and dropped), at most KEY_LIMIT requests an hour per key and
IP_LIMIT per address in ten minutes, and size limits on every field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novara.db import utcnow
from novara.einvoice import service as einvoice
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.accounting.models import Tax
from novara.modules.accounting.service import next_number
from novara.modules.audit import service as audit
from novara.modules.catalog.models import Product
from novara.modules.invoicing.lines import LineIn
from novara.modules.invoicing.models import Invoice, InvoiceLine
from novara.modules.invoicing.service import InvoiceValidationError, line_net_minor, post_invoice, recompute, tax_groups
from novara.modules.organizations.models import Organization
from novara.modules.partners.models import Customer
from novara.modules.sales.models import WebOrder
from novara.modules.sales.router import OrderIn, add_quotation
from novara.money import fmt_decimal, from_minor
from novara.studio.models import AppDataKey, AppProject

KEY_LIMIT = 30  # requests per key per hour
IP_LIMIT = 5    # requests per address per ten minutes

router = APIRouter(tags=["web orders"])


class WebOrderInvalid(ValueError):
    pass


class RateLimited(RuntimeError):
    pass


class PublicOrderLine(BaseModel):
    product_id: str = Field(max_length=36)
    quantity: Decimal = Field(gt=0, le=10000, max_digits=9, decimal_places=4)


class PublicOrderIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=2000)
    lines: list[PublicOrderLine] = Field(min_length=1, max_length=50)
    # Left empty by people (the site hides it); bots fill every field in.
    website: str = Field(default="", max_length=200)


@dataclass
class _Line:
    tax_id: str | None
    tax_rate: Decimal
    net_minor: int


def receive(db: Session, key: AppDataKey, body: PublicOrderIn, *, ip: str, awaiting_payment: bool = False) -> WebOrder:
    """Record a request from a site (the session is bound to the key's organization)."""
    if not (body.email or body.phone.strip()):
        raise WebOrderInvalid("give an email address or a phone number so the shop can reply")
    now = utcnow()
    per_key = db.scalar(select(func.count()).select_from(WebOrder).where(
        WebOrder.key_id == key.id, WebOrder.created_at >= now - timedelta(hours=1)))
    per_ip = db.scalar(select(func.count()).select_from(WebOrder).where(
        WebOrder.ip_address == ip, WebOrder.created_at >= now - timedelta(minutes=10))) if ip else 0
    if per_key >= KEY_LIMIT or per_ip >= IP_LIMIT:
        raise RateLimited("too many order requests; try again later")
    ids = [l.product_id for l in body.lines]
    if len(set(ids)) != len(ids):
        raise WebOrderInvalid("each product can appear once; change its quantity instead")
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids), Product.active.is_(True)))}
    rates = {t.id: t.rate for t in db.scalars(select(Tax))}
    currency = db.get(Organization, key.organization_id).default_currency
    lines, calc = [], []
    for n, req in enumerate(body.lines, start=1):
        product = products.get(req.product_id)
        if product is None:
            raise WebOrderInvalid(f"line {n}: that product isn't available")
        rate = rates.get(product.default_tax_id, Decimal(0)) if product.default_tax_id else Decimal(0)
        net = line_net_minor(req.quantity, product.sale_price, Decimal(0), currency)
        calc.append(_Line(product.default_tax_id, rate, net))
        lines.append({"product_id": product.id, "name": product.name, "sku": product.sku or "",
                      "quantity": fmt_decimal(req.quantity), "unit_price": fmt_decimal(product.sale_price),
                      "tax_id": product.default_tax_id, "tax_rate": fmt_decimal(rate)})
    subtotal = sum(c.net_minor for c in calc)
    tax = sum(g.tax_minor for g in tax_groups(calc, currency))
    year = now.year
    order = WebOrder(number=next_number(db, f"web_order:{year}", f"WEB-{year}-"), project_id=key.project_id,
                     key_id=key.id, customer_name=body.name.strip(), email=str(body.email or ""),
                     phone=body.phone.strip(), note=body.note.strip(), lines=lines, currency=currency,
                     subtotal_minor=subtotal, tax_minor=tax, total_minor=subtotal + tax, ip_address=ip,
                     status="awaiting_payment" if awaiting_payment else "new")
    db.add(order)
    db.flush()
    project = db.get(AppProject, key.project_id)
    audit.record(db, actor_user_id=None, action="web_order.received", target_type="web_order", target_id=order.id,
                 details={"number": order.number, "site": project.name if project else "",
                          "customer_name": order.customer_name, "total": str(from_minor(order.total_minor, currency))},
                 ip_address=ip)
    return order


def _customer_for(db: Session, w: WebOrder, user_id: str | None) -> tuple[Customer, bool]:
    """The customer with the request's email, or a new one."""
    if w.email:
        found = db.scalar(select(Customer).where(func.lower(Customer.email) == w.email.lower(),
                                                 Customer.active.is_(True)).limit(1))
        if found is not None:
            return found, False
    customer = Customer(name=w.customer_name, kind="individual", email=w.email, phone=w.phone,
                        notes=f"From site order request {w.number}", created_by=user_id)
    db.add(customer)
    db.flush()
    return customer, True


def invoice_paid_order(db: Session, w: WebOrder, *, paid_at: datetime, settings) -> Invoice:
    """A paid site order becomes the customer's invoice, at the prices and taxes the order was taken at, posted on the
    day of the payment (and e-invoiced as a simplified invoice when e-invoicing is on). The caller records the
    payment; anything refused raises, and the caller undoes it all."""
    customer, created = _customer_for(db, w, None)
    if created:
        audit.record(db, actor_user_id=None, action="customer.created", target_type="customer", target_id=customer.id,
                     details={"name": customer.name, "via": w.number})
    inv = Invoice(customer_id=customer.id, issue_date=paid_at.date(), due_date=paid_at.date(), currency=w.currency,
                  notes="\n".join(x for x in (w.number, w.note) if x))
    lines = [InvoiceLine(position=n, product_id=l["product_id"], description=l["name"], quantity=Decimal(l["quantity"]),
                         unit_price=Decimal(l["unit_price"]), discount_percent=Decimal(0), tax_id=l.get("tax_id"),
                         tax_rate=Decimal(l["tax_rate"]) if l.get("tax_id") else Decimal(0))
             for n, l in enumerate(w.lines, start=1)]
    recompute(inv, lines)
    if inv.total_minor != w.total_minor:
        raise InvoiceValidationError(f"the order's total doesn't add up any more ({from_minor(inv.total_minor, w.currency)})")
    db.add(inv)
    db.flush()
    for line in lines:
        line.invoice_id = inv.id
    db.add_all(lines)
    db.flush()
    post_invoice(db, inv, user_id=None)
    doc = einvoice.issue_for_invoice(db, settings, inv)          # sent to ZATCA by the worker
    audit.record(db, actor_user_id=None, action="invoice.posted", target_type="invoice", target_id=inv.id,
                 details={"number": inv.number, "via": w.number, "total": str(from_minor(inv.total_minor, w.currency)),
                          "einvoice": doc.number if doc else None})
    w.status, w.invoice_id, w.customer_id, w.handled_at = "paid", inv.id, customer.id, utcnow()
    return inv


# ---------------------------------------------------------------- in Sales


class WebOrderLineOut(BaseModel):
    product_id: str
    name: str
    sku: str
    quantity: str
    unit_price: str
    tax_rate: str


class WebOrderOut(BaseModel):
    id: str
    number: str
    status: str
    project_id: str
    project_name: str
    customer_name: str
    email: str
    phone: str
    note: str
    lines: list[WebOrderLineOut]
    currency: str
    subtotal: str
    tax: str
    total: str
    sales_order_id: str | None
    invoice_id: str | None          # paid on the site: the invoice it became
    invoice_number: str | None
    created_at: datetime


class ConvertedOut(BaseModel):
    sales_order_id: str
    customer_id: str


def _out(db: Session, w: WebOrder, names: dict[str, str] | None = None) -> WebOrderOut:
    c = w.currency
    if names is None:
        project = db.get(AppProject, w.project_id)
        names = {w.project_id: project.name if project else ""}
    return WebOrderOut(id=w.id, number=w.number, status=w.status, project_id=w.project_id,
                       project_name=names.get(w.project_id, ""), customer_name=w.customer_name, email=w.email,
                       phone=w.phone, note=w.note, lines=[WebOrderLineOut(**l) for l in w.lines], currency=c,
                       subtotal=str(from_minor(w.subtotal_minor, c)), tax=str(from_minor(w.tax_minor, c)),
                       total=str(from_minor(w.total_minor, c)), sales_order_id=w.sales_order_id,
                       invoice_id=w.invoice_id,
                       invoice_number=db.get(Invoice, w.invoice_id).number if w.invoice_id else None,
                       created_at=w.created_at)


def _locked(db: Session, web_order_id: str) -> WebOrder:
    w = db.scalar(select(WebOrder).where(WebOrder.id == web_order_id).with_for_update())
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order request not found")
    if w.status not in ("new", "awaiting_payment"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"this request was already {w.status.replace('_', ' ')}")
    return w


def _stop_payment(db: Session, request: Request, w: WebOrder) -> None:
    """Taking over (or dismissing) a request still waiting for its money closes its payment page."""
    if w.status == "awaiting_payment":
        from novara.payments import service as payments   # payments imports this module

        payments.cancel_order_links(db, request.app.state.settings, w, request.app.state.payment_http)


@router.get("/orgs/{org_id}/web-orders", response_model=list[WebOrderOut])
def list_web_orders(status_filter: str | None = Query(default=None, alias="status",
                                                    pattern="^(open|new|awaiting_payment|paid|converted|dismissed)$"),
                    _: OrgContext = Depends(require(P.SALES_READ)), db: Session = Depends(get_db)):
    """By status; "open" is what still waits for someone: new requests and those waiting for their payment."""
    stmt = select(WebOrder).order_by(WebOrder.created_at.desc()).limit(200)
    if status_filter == "open":
        stmt = stmt.where(WebOrder.status.in_(("new", "awaiting_payment")))
    elif status_filter:
        stmt = stmt.where(WebOrder.status == status_filter)
    rows = list(db.scalars(stmt))
    names = {p.id: p.name for p in db.scalars(select(AppProject).where(AppProject.id.in_({w.project_id for w in rows})))}
    return [_out(db, w, names) for w in rows]


@router.post("/orgs/{org_id}/web-orders/{web_order_id}/convert", response_model=ConvertedOut)
def convert_web_order(web_order_id: str, request: Request, ctx: OrgContext = Depends(require(P.SALES_WRITE)),
                      db: Session = Depends(get_db)):
    """Make a quotation from the request: the customer is found by email, or created."""
    if not ctx.has(P.CUSTOMERS_WRITE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing permission: customers.write")
    w = _locked(db, web_order_id)
    _stop_payment(db, request, w)
    customer, created = _customer_for(db, w, ctx.user.id)
    if created:
        audit.record(db, actor_user_id=ctx.user.id, action="customer.created", target_type="customer",
                     target_id=customer.id, details={"name": customer.name, "via": w.number},
                     ip_address=client_ip(request))
    body = OrderIn(customer_id=customer.id, order_date=date.today(),
                   notes="\n".join(x for x in (f"{w.number}", w.note) if x),
                   lines=[LineIn(product_id=l["product_id"], quantity=Decimal(l["quantity"])) for l in w.lines])
    order = add_quotation(db, ctx, body, ip=client_ip(request), via=w.number)
    w.status, w.sales_order_id, w.customer_id = "converted", order.id, customer.id
    w.handled_by, w.handled_at = ctx.user.id, utcnow()
    audit.record(db, actor_user_id=ctx.user.id, action="web_order.converted", target_type="web_order", target_id=w.id,
                 details={"number": w.number, "sales_order": order.number}, ip_address=client_ip(request))
    db.commit()
    return ConvertedOut(sales_order_id=order.id, customer_id=customer.id)


@router.post("/orgs/{org_id}/web-orders/{web_order_id}/dismiss", response_model=WebOrderOut)
def dismiss_web_order(web_order_id: str, request: Request, ctx: OrgContext = Depends(require(P.SALES_WRITE)),
                      db: Session = Depends(get_db)):
    w = _locked(db, web_order_id)
    _stop_payment(db, request, w)
    w.status, w.handled_by, w.handled_at = "dismissed", ctx.user.id, utcnow()
    audit.record(db, actor_user_id=ctx.user.id, action="web_order.dismissed", target_type="web_order", target_id=w.id,
                 details={"number": w.number}, ip_address=client_ip(request))
    db.commit()
    return _out(db, w)
