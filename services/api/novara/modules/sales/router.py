from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara import idempotency
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.accounting.service import next_number
from novara.modules.audit import service as audit
from novara.modules.inventory import service as stock
from novara.modules.inventory.models import Warehouse
from novara.modules.inventory.router import StockOperationOut, _replay, operation_out
from novara.modules.invoicing.lines import LineIn, resolve_lines
from novara.modules.invoicing.router import InvoiceOut, invoice_out
from novara.modules.partners.models import Customer
from novara.modules.sales import service as svc
from novara.modules.sales.models import SalesOrder, SalesOrderLine
from novara.money import fmt_decimal, from_minor

router = APIRouter(tags=["sales"])


class OrderIn(BaseModel):
    customer_id: str
    order_date: date
    valid_until: date | None = None
    warehouse_id: str | None = None
    notes: str = Field(default="", max_length=5000)
    lines: list[LineIn] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _dates(self):
        if self.valid_until is not None and self.valid_until < self.order_date:
            raise ValueError("valid_until cannot be before order_date")
        return self


class DeliveryLineIn(BaseModel):
    line_id: str
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)


class DeliveryIn(BaseModel):
    move_date: date
    # Omit to deliver everything outstanding.
    lines: list[DeliveryLineIn] | None = None


class InvoiceFromOrderIn(BaseModel):
    issue_date: date
    due_date: date | None = None


class OrderLineOut(BaseModel):
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
    qty_delivered: str
    qty_invoiced: str
    qty_to_deliver: str
    qty_to_invoice: str


class OrderOut(BaseModel):
    id: str
    number: str
    status: str
    delivery_status: str
    invoice_status: str
    customer_id: str
    customer_name: str
    order_date: date
    valid_until: date | None
    warehouse_id: str | None
    currency: str
    notes: str
    subtotal: str
    tax: str
    total: str
    confirmed_at: datetime | None
    created_at: datetime
    lines: list[OrderLineOut]


class OrderSummary(BaseModel):
    id: str
    number: str
    status: str
    customer_id: str
    customer_name: str
    order_date: date
    total: str


def _money(minor: int, currency: str) -> str:
    return str(from_minor(minor, currency))


def order_out(db: Session, order: SalesOrder) -> OrderOut:
    items = svc.progress(db, order)
    c = order.currency
    return OrderOut(
        id=order.id, number=order.number, status=order.status,
        delivery_status=svc.delivery_status(items), invoice_status=svc.invoice_status(items),
        customer_id=order.customer_id, customer_name=db.get(Customer, order.customer_id).name,
        order_date=order.order_date, valid_until=order.valid_until, warehouse_id=order.warehouse_id, currency=c,
        notes=order.notes, subtotal=_money(order.subtotal_minor, c), tax=_money(order.tax_minor, c),
        total=_money(order.total_minor, c), confirmed_at=order.confirmed_at, created_at=order.created_at,
        lines=[
            OrderLineOut(
                id=i.line.id, position=i.line.position, product_id=i.line.product_id, description=i.line.description,
                quantity=fmt_decimal(i.line.quantity), unit_price=fmt_decimal(i.line.unit_price),
                discount_percent=fmt_decimal(i.line.discount_percent), tax_id=i.line.tax_id,
                tax_rate=fmt_decimal(i.line.tax_rate), net_amount=_money(i.line.net_minor, c),
                qty_delivered=fmt_decimal(i.line.qty_delivered), qty_invoiced=fmt_decimal(i.invoiced),
                qty_to_deliver=fmt_decimal(i.to_deliver), qty_to_invoice=fmt_decimal(i.to_invoice),
            )
            for i in items
        ],
    )


def _domain(fn):
    try:
        return fn()
    except (svc.OrderStateError, stock.InsufficientStock) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except (svc.OrderValidationError, stock.InventoryError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


def _get_order(db: Session, order_id: str, *, lock: bool = False) -> SalesOrder:
    order = svc.lock_order(db, order_id) if lock else db.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sales order not found")
    return order


def _apply(db: Session, order: SalesOrder, body: OrderIn) -> list[SalesOrderLine]:
    customer = db.get(Customer, body.customer_id)
    if customer is None or not customer.active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown or archived customer")
    if body.warehouse_id is not None:
        wh = db.get(Warehouse, body.warehouse_id)
        if wh is None or not wh.active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown or inactive warehouse")
    order.customer_id = customer.id
    order.order_date = body.order_date
    order.valid_until = body.valid_until
    order.warehouse_id = body.warehouse_id
    order.notes = body.notes
    lines = [SalesOrderLine(**fields) for fields in resolve_lines(db, body.lines)]
    svc.recompute(order, lines)
    return lines


@router.get("/orgs/{org_id}/sales-orders", response_model=list[OrderSummary])
def list_orders(status_filter: str | None = Query(default=None, alias="status"), customer_id: str | None = None,
                limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
                _: OrgContext = Depends(require(P.SALES_READ)), db: Session = Depends(get_db)):
    stmt = (select(SalesOrder, Customer.name).join(Customer, Customer.id == SalesOrder.customer_id)
            .order_by(SalesOrder.order_date.desc(), SalesOrder.number.desc()).limit(limit).offset(offset))
    if status_filter:
        stmt = stmt.where(SalesOrder.status == status_filter)
    if customer_id:
        stmt = stmt.where(SalesOrder.customer_id == customer_id)
    return [OrderSummary(id=o.id, number=o.number, status=o.status, customer_id=o.customer_id, customer_name=name,
                         order_date=o.order_date, total=_money(o.total_minor, o.currency))
            for o, name in db.execute(stmt)]


@router.post("/orgs/{org_id}/sales-orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_quotation(body: OrderIn, request: Request, ctx: OrgContext = Depends(require(P.SALES_WRITE)),
                     db: Session = Depends(get_db)):
    order = SalesOrder(currency=ctx.organization.default_currency, created_by=ctx.user.id, status="quotation")
    lines = _apply(db, order, body)
    order.number = next_number(db, f"sales_order:{body.order_date.year}", f"SO-{body.order_date.year}-")
    db.add(order)
    db.flush()
    for line in lines:
        line.order_id = order.id
    db.add_all(lines)
    audit.record(db, actor_user_id=ctx.user.id, action="sales_order.created", target_type="sales_order",
                 target_id=order.id, details={"number": order.number, "total": _money(order.total_minor, order.currency)},
                 ip_address=client_ip(request))
    db.commit()
    return order_out(db, order)


@router.get("/orgs/{org_id}/sales-orders/{order_id}", response_model=OrderOut)
def read_order(order_id: str, _: OrgContext = Depends(require(P.SALES_READ)), db: Session = Depends(get_db)):
    return order_out(db, _get_order(db, order_id))


@router.put("/orgs/{org_id}/sales-orders/{order_id}", response_model=OrderOut)
def replace_quotation(order_id: str, body: OrderIn, request: Request,
                      ctx: OrgContext = Depends(require(P.SALES_WRITE)), db: Session = Depends(get_db)):
    order = _get_order(db, order_id, lock=True)
    if order.status != "quotation":
        raise HTTPException(status.HTTP_409_CONFLICT, "only quotations can be edited")
    lines = _apply(db, order, body)
    for old in svc.lines_of(db, order):
        db.delete(old)
    db.flush()
    for line in lines:
        line.order_id = order.id
    db.add_all(lines)
    audit.record(db, actor_user_id=ctx.user.id, action="sales_order.updated", target_type="sales_order",
                 target_id=order.id, details={"total": _money(order.total_minor, order.currency)},
                 ip_address=client_ip(request))
    db.commit()
    return order_out(db, order)


@router.post("/orgs/{org_id}/sales-orders/{order_id}/confirm", response_model=OrderOut)
def confirm_order(order_id: str, request: Request, ctx: OrgContext = Depends(require(P.SALES_WRITE)),
                  db: Session = Depends(get_db)):
    order = _get_order(db, order_id, lock=True)
    _domain(lambda: svc.confirm(order, svc.lines_of(db, order)))
    audit.record(db, actor_user_id=ctx.user.id, action="sales_order.confirmed", target_type="sales_order",
                 target_id=order.id, details={"number": order.number}, ip_address=client_ip(request))
    db.commit()
    return order_out(db, order)


@router.post("/orgs/{org_id}/sales-orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: str, request: Request, ctx: OrgContext = Depends(require(P.SALES_WRITE)),
                 db: Session = Depends(get_db)):
    order = _get_order(db, order_id, lock=True)
    _domain(lambda: svc.cancel(db, order))
    audit.record(db, actor_user_id=ctx.user.id, action="sales_order.cancelled", target_type="sales_order",
                 target_id=order.id, details={"number": order.number}, ip_address=client_ip(request))
    db.commit()
    return order_out(db, order)


@router.post("/orgs/{org_id}/sales-orders/{order_id}/deliveries", response_model=StockOperationOut,
             status_code=status.HTTP_201_CREATED)
def deliver_order(order_id: str, body: DeliveryIn, request: Request, response: Response,
                  idempotency_key: str | None = Header(default=None, max_length=100),
                  ctx: OrgContext = Depends(require(P.INVENTORY_WRITE)), db: Session = Depends(get_db)):
    """Ship goods from the order's warehouse. Records cost of goods sold at average cost."""
    currency = ctx.organization.default_currency
    h = idempotency.request_hash({"order": order_id, **body.model_dump(mode="json")})
    order = _get_order(db, order_id, lock=True)
    try:
        previous = idempotency.replayed(db, "sales.delivery", idempotency_key, h)
    except idempotency.IdempotencyConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    if previous:
        response.status_code = status.HTTP_200_OK
        return _replay(db, previous, currency)
    quantities = None if body.lines is None else {}
    for l in body.lines or []:
        quantities[l.line_id] = quantities.get(l.line_id, Decimal(0)) + l.quantity
    result = _domain(lambda: svc.deliver(db, order, move_date=body.move_date, quantities=quantities,
                                         user_id=ctx.user.id))
    idempotency.remember(db, "sales.delivery", idempotency_key, h, result.reference)
    audit.record(db, actor_user_id=ctx.user.id, action="sales_order.delivered", target_type="sales_order",
                 target_id=order.id, details={"reference": result.reference, "lines": len(result.moves)},
                 ip_address=client_ip(request))
    db.commit()
    return operation_out(result, currency)


@router.post("/orgs/{org_id}/sales-orders/{order_id}/invoice", response_model=InvoiceOut,
             status_code=status.HTTP_201_CREATED)
def invoice_order(order_id: str, body: InvoiceFromOrderIn, request: Request,
                  ctx: OrgContext = Depends(require(P.INVOICES_WRITE)), db: Session = Depends(get_db)):
    """Create a draft invoice for delivered goods and ordered services not yet invoiced."""
    if body.due_date is not None and body.due_date < body.issue_date:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "due_date cannot be before issue_date")
    order = _get_order(db, order_id, lock=True)
    invoice = _domain(lambda: svc.create_invoice(db, order, issue_date=body.issue_date, due_date=body.due_date,
                                                 user_id=ctx.user.id))
    audit.record(db, actor_user_id=ctx.user.id, action="invoice.created", target_type="invoice",
                 target_id=invoice.id, details={"sales_order": order.number,
                                                "total": _money(invoice.total_minor, invoice.currency)},
                 ip_address=client_ip(request))
    db.commit()
    return invoice_out(db, invoice)
