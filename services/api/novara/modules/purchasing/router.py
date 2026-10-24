from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from novara import idempotency
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.accounting.models import Account, Tax
from novara.modules.accounting.service import next_number, system_account
from novara.modules.audit import service as audit
from novara.modules.catalog.models import Product
from novara.modules.inventory import service as stock
from novara.modules.inventory.models import Warehouse
from novara.modules.inventory.router import StockOperationOut, _replay, operation_out
from novara.modules.invoicing.router import AGING_BUCKETS, _bucket
from novara.modules.partners.models import Vendor
from novara.modules.partners.router import CustomerFields, CustomerUpdate
from novara.modules.purchasing import service as svc
from novara.modules.purchasing.models import (BillPayment, PurchaseOrder, PurchaseOrderLine, VendorBill, VendorBillLine,
                                              VendorCredit, VendorCreditLine)
from novara.money import fmt_decimal, from_minor, to_minor

router = APIRouter(tags=["purchases"])


def _money(minor: int, currency: str) -> str:
    return str(from_minor(minor, currency))


def _unprocessable(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, msg)


def _domain(fn):
    try:
        return fn()
    except (svc.PurchaseStateError, stock.InsufficientStock) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except (svc.PurchaseValidationError, stock.InventoryError) as e:
        raise _unprocessable(str(e))


# ---------------------------------------------------------------- vendors


class VendorIn(CustomerFields):
    name: str = Field(min_length=1, max_length=200)


class VendorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: str
    email: str
    phone: str
    tax_number: str
    commercial_registration: str
    address_line: str
    city: str
    postal_code: str
    country: str
    notes: str
    active: bool
    created_at: datetime


def _vendor(db: Session, vendor_id: str) -> Vendor:
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "vendor not found")
    return vendor


@router.get("/orgs/{org_id}/vendors", response_model=list[VendorOut])
def list_vendors(q: str | None = Query(default=None, max_length=100), include_archived: bool = False,
                 limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
                 _: OrgContext = Depends(require(P.PURCHASES_READ)), db: Session = Depends(get_db)):
    stmt = select(Vendor).order_by(Vendor.name).limit(limit).offset(offset)
    if not include_archived:
        stmt = stmt.where(Vendor.active.is_(True))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Vendor.name).like(like), func.lower(Vendor.email).like(like),
                              Vendor.tax_number.like(like), Vendor.phone.like(like)))
    return db.scalars(stmt).all()


@router.post("/orgs/{org_id}/vendors", response_model=VendorOut, status_code=status.HTTP_201_CREATED)
def create_vendor(body: VendorIn, request: Request, ctx: OrgContext = Depends(require(P.PURCHASES_WRITE)),
                  db: Session = Depends(get_db)):
    data = body.model_dump()
    data["email"] = (data["email"] or "").lower()
    vendor = Vendor(**data, created_by=ctx.user.id)
    db.add(vendor)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="vendor.created", target_type="vendor", target_id=vendor.id,
                 details={"name": vendor.name}, ip_address=client_ip(request))
    db.commit()
    return vendor


@router.get("/orgs/{org_id}/vendors/{vendor_id}", response_model=VendorOut)
def read_vendor(vendor_id: str, _: OrgContext = Depends(require(P.PURCHASES_READ)), db: Session = Depends(get_db)):
    return _vendor(db, vendor_id)


@router.patch("/orgs/{org_id}/vendors/{vendor_id}", response_model=VendorOut)
def update_vendor(vendor_id: str, body: CustomerUpdate, request: Request,
                  ctx: OrgContext = Depends(require(P.PURCHASES_WRITE)), db: Session = Depends(get_db)):
    vendor = _vendor(db, vendor_id)
    changes = body.model_dump(exclude_unset=True)
    if "email" in changes:
        changes["email"] = (changes["email"] or "").lower()
    for field, value in changes.items():
        if value is None:
            raise _unprocessable(f"{field} cannot be null")
        setattr(vendor, field, value)
    audit.record(db, actor_user_id=ctx.user.id, action="vendor.updated", target_type="vendor", target_id=vendor.id,
                 details=changes, ip_address=client_ip(request))
    db.commit()
    return vendor


# ---------------------------------------------------------------- lines


class PurchaseLineIn(BaseModel):
    product_id: str | None = None
    description: str | None = Field(default=None, min_length=1, max_length=500)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    unit_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    # Omit to use the product's default tax; send null for no tax.
    tax_id: str | None = None
    # Bills only: the expense account (default: general expenses).
    account_id: str | None = None


def _resolve_lines(db: Session, lines: list[PurchaseLineIn], *, bill: bool) -> list[dict]:
    out = []
    expense = system_account(db, "expense") if bill else None
    for i, li in enumerate(lines, start=1):
        product = None
        if li.product_id is not None:
            product = db.get(Product, li.product_id)
            if product is None or not product.active:
                raise _unprocessable(f"line {i}: unknown or archived product")
        if bill and product is not None and product.kind == "goods":
            raise _unprocessable(f"line {i}: goods are billed from their purchase order after they are received")
        description = li.description or (product.name if product else None)
        if description is None:
            raise _unprocessable(f"line {i}: description is required without a product")
        tax_id = li.tax_id if "tax_id" in li.model_fields_set else (product.default_tax_id if product else None)
        rate = Decimal(0)
        if tax_id is not None:
            tax = db.get(Tax, tax_id)
            if tax is None or not tax.active:
                raise _unprocessable(f"line {i}: unknown or inactive tax")
            rate = tax.rate
        fields = dict(position=i, product_id=li.product_id, description=description, quantity=li.quantity,
                      unit_cost=li.unit_cost, tax_id=tax_id, tax_rate=rate)
        if bill:
            account = db.get(Account, li.account_id) if li.account_id else expense
            # Expenses, or assets bought outright (equipment); never accounts the platform keeps in step itself.
            usable = account is not None and account.active and (
                account.type == "expense" or (account.type == "asset" and account.system_key is None))
            if not usable:
                raise _unprocessable(f"line {i}: choose an expense account (or an asset account you manage yourself)")
            fields["account_id"] = account.id
        out.append(fields)
    return out


# ---------------------------------------------------------------- purchase orders


class PurchaseOrderIn(BaseModel):
    vendor_id: str
    order_date: date
    expected_date: date | None = None
    warehouse_id: str | None = None
    notes: str = Field(default="", max_length=5000)
    lines: list[PurchaseLineIn] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _dates(self):
        if self.expected_date is not None and self.expected_date < self.order_date:
            raise ValueError("expected_date cannot be before order_date")
        return self


class ReceiveLineIn(BaseModel):
    line_id: str
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)


class ReceiveIn(BaseModel):
    move_date: date
    lines: list[ReceiveLineIn] | None = None  # omit to receive everything outstanding


class BillFromOrderIn(BaseModel):
    bill_date: date
    due_date: date | None = None
    vendor_reference: str = Field(default="", max_length=60)


class PurchaseOrderLineOut(BaseModel):
    id: str
    position: int
    product_id: str | None
    description: str
    quantity: str
    unit_cost: str
    tax_id: str | None
    tax_rate: str
    net_amount: str
    qty_received: str
    qty_billed: str
    qty_to_receive: str
    qty_to_bill: str


class PurchaseOrderOut(BaseModel):
    id: str
    number: str
    status: str
    receipt_status: str
    bill_status: str
    vendor_id: str
    vendor_name: str
    order_date: date
    expected_date: date | None
    warehouse_id: str | None
    currency: str
    notes: str
    subtotal: str
    tax: str
    total: str
    confirmed_at: datetime | None
    created_at: datetime
    lines: list[PurchaseOrderLineOut]


class PurchaseOrderSummary(BaseModel):
    id: str
    number: str
    status: str
    vendor_id: str
    vendor_name: str
    order_date: date
    expected_date: date | None
    total: str


def order_out(db: Session, order: PurchaseOrder) -> PurchaseOrderOut:
    items = svc.progress(db, order)
    c = order.currency
    return PurchaseOrderOut(
        id=order.id, number=order.number, status=order.status, receipt_status=svc.receipt_status(items),
        bill_status=svc.bill_status(items), vendor_id=order.vendor_id, vendor_name=db.get(Vendor, order.vendor_id).name,
        order_date=order.order_date, expected_date=order.expected_date, warehouse_id=order.warehouse_id, currency=c,
        notes=order.notes, subtotal=_money(order.subtotal_minor, c), tax=_money(order.tax_minor, c),
        total=_money(order.total_minor, c), confirmed_at=order.confirmed_at, created_at=order.created_at,
        lines=[PurchaseOrderLineOut(
            id=i.line.id, position=i.line.position, product_id=i.line.product_id, description=i.line.description,
            quantity=fmt_decimal(i.line.quantity), unit_cost=fmt_decimal(i.line.unit_cost), tax_id=i.line.tax_id,
            tax_rate=fmt_decimal(i.line.tax_rate), net_amount=_money(i.line.net_minor, c),
            qty_received=fmt_decimal(i.line.qty_received), qty_billed=fmt_decimal(i.billed),
            qty_to_receive=fmt_decimal(i.to_receive), qty_to_bill=fmt_decimal(i.to_bill)) for i in items],
    )


def _get_order(db: Session, order_id: str, *, lock: bool = False) -> PurchaseOrder:
    order = svc.lock_order(db, order_id) if lock else db.get(PurchaseOrder, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "purchase order not found")
    return order


def _apply_order(db: Session, order: PurchaseOrder, body: PurchaseOrderIn) -> list[PurchaseOrderLine]:
    vendor = db.get(Vendor, body.vendor_id)
    if vendor is None or not vendor.active:
        raise _unprocessable("unknown or archived vendor")
    if body.warehouse_id is not None:
        wh = db.get(Warehouse, body.warehouse_id)
        if wh is None or not wh.active:
            raise _unprocessable("unknown or inactive warehouse")
    order.vendor_id, order.order_date, order.expected_date = vendor.id, body.order_date, body.expected_date
    order.warehouse_id, order.notes = body.warehouse_id, body.notes
    lines = [PurchaseOrderLine(**f) for f in _resolve_lines(db, body.lines, bill=False)]
    svc.recompute(order, lines)
    return lines


@router.get("/orgs/{org_id}/purchase-orders", response_model=list[PurchaseOrderSummary])
def list_orders(status_filter: str | None = Query(default=None, alias="status"), vendor_id: str | None = None,
                limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
                _: OrgContext = Depends(require(P.PURCHASES_READ)), db: Session = Depends(get_db)):
    stmt = (select(PurchaseOrder, Vendor.name).join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
            .order_by(PurchaseOrder.order_date.desc(), PurchaseOrder.number.desc()).limit(limit).offset(offset))
    if status_filter:
        stmt = stmt.where(PurchaseOrder.status == status_filter)
    if vendor_id:
        stmt = stmt.where(PurchaseOrder.vendor_id == vendor_id)
    return [PurchaseOrderSummary(id=o.id, number=o.number, status=o.status, vendor_id=o.vendor_id, vendor_name=name,
                         order_date=o.order_date, expected_date=o.expected_date, total=_money(o.total_minor, o.currency))
            for o, name in db.execute(stmt)]


@router.post("/orgs/{org_id}/purchase-orders", response_model=PurchaseOrderOut, status_code=status.HTTP_201_CREATED)
def create_order(body: PurchaseOrderIn, request: Request, ctx: OrgContext = Depends(require(P.PURCHASES_WRITE)),
                 db: Session = Depends(get_db)):
    order = PurchaseOrder(currency=ctx.organization.default_currency, created_by=ctx.user.id, status="draft")
    lines = _apply_order(db, order, body)
    order.number = next_number(db, f"purchase_order:{body.order_date.year}", f"PO-{body.order_date.year}-")
    db.add(order)
    db.flush()
    for line in lines:
        line.order_id = order.id
    db.add_all(lines)
    audit.record(db, actor_user_id=ctx.user.id, action="purchase_order.created", target_type="purchase_order",
                 target_id=order.id, details={"number": order.number, "total": _money(order.total_minor, order.currency)},
                 ip_address=client_ip(request))
    db.commit()
    return order_out(db, order)


@router.get("/orgs/{org_id}/purchase-orders/{order_id}", response_model=PurchaseOrderOut)
def read_order(order_id: str, _: OrgContext = Depends(require(P.PURCHASES_READ)), db: Session = Depends(get_db)):
    return order_out(db, _get_order(db, order_id))


@router.put("/orgs/{org_id}/purchase-orders/{order_id}", response_model=PurchaseOrderOut)
def replace_order(order_id: str, body: PurchaseOrderIn, request: Request,
                  ctx: OrgContext = Depends(require(P.PURCHASES_WRITE)), db: Session = Depends(get_db)):
    order = _get_order(db, order_id, lock=True)
    if order.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "only draft orders can be edited")
    lines = _apply_order(db, order, body)
    for old in svc.order_lines(db, order):
        db.delete(old)
    db.flush()
    for line in lines:
        line.order_id = order.id
    db.add_all(lines)
    audit.record(db, actor_user_id=ctx.user.id, action="purchase_order.updated", target_type="purchase_order",
                 target_id=order.id, details={"total": _money(order.total_minor, order.currency)},
                 ip_address=client_ip(request))
    db.commit()
    return order_out(db, order)


@router.post("/orgs/{org_id}/purchase-orders/{order_id}/confirm", response_model=PurchaseOrderOut)
def confirm_order(order_id: str, request: Request, ctx: OrgContext = Depends(require(P.PURCHASES_WRITE)),
                  db: Session = Depends(get_db)):
    order = _get_order(db, order_id, lock=True)
    _domain(lambda: svc.confirm(order, svc.order_lines(db, order)))
    audit.record(db, actor_user_id=ctx.user.id, action="purchase_order.confirmed", target_type="purchase_order",
                 target_id=order.id, details={"number": order.number}, ip_address=client_ip(request))
    db.commit()
    return order_out(db, order)


@router.post("/orgs/{org_id}/purchase-orders/{order_id}/cancel", response_model=PurchaseOrderOut)
def cancel_order(order_id: str, request: Request, ctx: OrgContext = Depends(require(P.PURCHASES_WRITE)),
                 db: Session = Depends(get_db)):
    order = _get_order(db, order_id, lock=True)
    _domain(lambda: svc.cancel(db, order))
    audit.record(db, actor_user_id=ctx.user.id, action="purchase_order.cancelled", target_type="purchase_order",
                 target_id=order.id, details={"number": order.number}, ip_address=client_ip(request))
    db.commit()
    return order_out(db, order)


@router.post("/orgs/{org_id}/purchase-orders/{order_id}/receipts", response_model=StockOperationOut,
             status_code=status.HTTP_201_CREATED)
def receive_order(order_id: str, body: ReceiveIn, request: Request, response: Response,
                  idempotency_key: str | None = Header(default=None, max_length=100),
                  ctx: OrgContext = Depends(require(P.INVENTORY_WRITE)), db: Session = Depends(get_db)):
    """Receive goods into the order's warehouse at the order's unit cost."""
    currency = ctx.organization.default_currency
    h = idempotency.request_hash({"order": order_id, **body.model_dump(mode="json")})
    order = _get_order(db, order_id, lock=True)
    try:
        previous = idempotency.replayed(db, "purchase.receipt", idempotency_key, h)
    except idempotency.IdempotencyConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    if previous:
        response.status_code = status.HTTP_200_OK
        return _replay(db, previous, currency)
    quantities = None if body.lines is None else {}
    for l in body.lines or []:
        quantities[l.line_id] = quantities.get(l.line_id, Decimal(0)) + l.quantity
    result = _domain(lambda: svc.receive(db, order, move_date=body.move_date, quantities=quantities,
                                         user_id=ctx.user.id))
    idempotency.remember(db, "purchase.receipt", idempotency_key, h, result.reference)
    audit.record(db, actor_user_id=ctx.user.id, action="purchase_order.received", target_type="purchase_order",
                 target_id=order.id, details={"reference": result.reference, "lines": len(result.moves)},
                 ip_address=client_ip(request))
    db.commit()
    return operation_out(result, currency)


# ---------------------------------------------------------------- vendor bills


class BillIn(BaseModel):
    vendor_id: str
    vendor_reference: str = Field(default="", max_length=60)
    bill_date: date
    due_date: date | None = None
    notes: str = Field(default="", max_length=5000)
    lines: list[PurchaseLineIn] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _dates(self):
        if self.due_date is not None and self.due_date < self.bill_date:
            raise ValueError("due_date cannot be before bill_date")
        return self


class BillPaymentIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    payment_date: date
    method: str = Field(pattern="^(bank|cash)$")
    reference: str = Field(default="", max_length=120)


class BillCancelIn(BaseModel):
    cancel_date: date


class BillLineOut(BaseModel):
    id: str
    position: int
    product_id: str | None
    description: str
    quantity: str
    unit_cost: str
    tax_id: str | None
    tax_rate: str
    net_amount: str
    account_id: str | None
    purchase_order_line_id: str | None
    ordered_unit_cost: str | None = None  # the purchase order's price, when billed from one
    qty_credited: str = "0"  # by vendor credits
    qty_returned: str = "0"  # sent back to the vendor


class BillOut(BaseModel):
    id: str
    number: str | None
    vendor_reference: str
    status: str
    vendor_id: str
    vendor_name: str
    bill_date: date
    due_date: date
    currency: str
    notes: str
    subtotal: str
    tax: str
    total: str
    amount_paid: str
    amount_credited: str = "0"
    amount_due: str
    journal_entry_id: str | None
    reversal_entry_id: str | None
    purchase_order_id: str | None
    purchase_order_number: str | None
    posted_at: datetime | None
    created_at: datetime
    lines: list[BillLineOut]


class BillSummary(BaseModel):
    id: str
    number: str | None
    vendor_reference: str
    status: str
    vendor_id: str
    vendor_name: str
    bill_date: date
    due_date: date
    total: str
    amount_due: str


class BillPaymentOut(BaseModel):
    id: str
    bill_id: str
    payment_date: date
    amount: str
    method: str
    reference: str
    journal_entry_id: str
    created_at: datetime


def _order_of_bill(db: Session, bill: VendorBill) -> PurchaseOrder | None:
    return db.scalar(select(PurchaseOrder).join(PurchaseOrderLine, PurchaseOrderLine.order_id == PurchaseOrder.id)
                     .join(VendorBillLine, VendorBillLine.purchase_order_line_id == PurchaseOrderLine.id)
                     .where(VendorBillLine.bill_id == bill.id).limit(1))


def bill_out(db: Session, bill: VendorBill) -> BillOut:
    c = bill.currency
    order = _order_of_bill(db, bill)
    credited: dict[str, tuple[Decimal, Decimal]] = {}
    if bill.amount_credited_minor or bill.status in ("paid", "credited"):
        rows = db.execute(select(VendorCreditLine.bill_line_id, VendorCreditLine.quantity,
                                 VendorCreditLine.returned_quantity)
                          .join(VendorCredit, VendorCredit.id == VendorCreditLine.credit_id)
                          .where(VendorCredit.bill_id == bill.id))
        for lid, qty, returned in rows:
            q, r = credited.get(lid, (Decimal(0), Decimal(0)))
            credited[lid] = (q + qty, r + returned)
    lines = svc.bill_lines(db, bill)
    ordered = svc.ordered_costs(db, lines)
    return BillOut(
        id=bill.id, number=bill.number, vendor_reference=bill.vendor_reference, status=bill.status,
        vendor_id=bill.vendor_id, vendor_name=db.get(Vendor, bill.vendor_id).name, bill_date=bill.bill_date,
        due_date=bill.due_date, currency=c, notes=bill.notes, subtotal=_money(bill.subtotal_minor, c),
        tax=_money(bill.tax_minor, c), total=_money(bill.total_minor, c), amount_paid=_money(bill.amount_paid_minor, c),
        amount_credited=_money(bill.amount_credited_minor, c), amount_due=_money(bill.amount_due_minor, c), journal_entry_id=bill.journal_entry_id,
        reversal_entry_id=bill.reversal_entry_id, purchase_order_id=order.id if order else None,
        purchase_order_number=order.number if order else None, posted_at=bill.posted_at, created_at=bill.created_at,
        lines=[BillLineOut(id=l.id, position=l.position, product_id=l.product_id, description=l.description,
                           quantity=fmt_decimal(l.quantity), unit_cost=fmt_decimal(l.unit_cost), tax_id=l.tax_id,
                           tax_rate=fmt_decimal(l.tax_rate), net_amount=_money(l.net_minor, c),
                           account_id=l.account_id, purchase_order_line_id=l.purchase_order_line_id,
                           ordered_unit_cost=fmt_decimal(ordered[l.id]) if l.id in ordered else None,
                           qty_credited=fmt_decimal(credited.get(l.id, (Decimal(0), Decimal(0)))[0]),
                           qty_returned=fmt_decimal(credited.get(l.id, (Decimal(0), Decimal(0)))[1]))
               for l in lines],
    )


def _get_bill(db: Session, bill_id: str, *, lock: bool = False) -> VendorBill:
    bill = svc.lock_bill(db, bill_id) if lock else db.get(VendorBill, bill_id)
    if bill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "bill not found")
    return bill


def _apply_bill(db: Session, bill: VendorBill, body: BillIn) -> list[VendorBillLine]:
    vendor = db.get(Vendor, body.vendor_id)
    if vendor is None or not vendor.active:
        raise _unprocessable("unknown or archived vendor")
    bill.vendor_id, bill.vendor_reference, bill.bill_date = vendor.id, body.vendor_reference, body.bill_date
    bill.due_date, bill.notes = body.due_date or body.bill_date, body.notes
    lines = [VendorBillLine(**f) for f in _resolve_lines(db, body.lines, bill=True)]
    svc.recompute(bill, lines)
    return lines


@router.post("/orgs/{org_id}/purchase-orders/{order_id}/bill", response_model=BillOut,
             status_code=status.HTTP_201_CREATED)
def bill_order(order_id: str, body: BillFromOrderIn, request: Request,
               ctx: OrgContext = Depends(require(P.PURCHASES_WRITE)), db: Session = Depends(get_db)):
    """A draft bill for received goods and ordered services not billed yet, at the order's prices."""
    if body.due_date is not None and body.due_date < body.bill_date:
        raise _unprocessable("due_date cannot be before bill_date")
    order = _get_order(db, order_id, lock=True)
    bill = _domain(lambda: svc.bill_from_order(db, order, bill_date=body.bill_date, due_date=body.due_date,
                                               vendor_reference=body.vendor_reference, user_id=ctx.user.id))
    audit.record(db, actor_user_id=ctx.user.id, action="bill.created", target_type="vendor_bill", target_id=bill.id,
                 details={"purchase_order": order.number, "total": _money(bill.total_minor, bill.currency)},
                 ip_address=client_ip(request))
    db.commit()
    return bill_out(db, bill)


@router.get("/orgs/{org_id}/bills", response_model=list[BillSummary])
def list_bills(status_filter: str | None = Query(default=None, alias="status"), vendor_id: str | None = None,
               limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
               _: OrgContext = Depends(require(P.PURCHASES_READ)), db: Session = Depends(get_db)):
    stmt = (select(VendorBill, Vendor.name).join(Vendor, Vendor.id == VendorBill.vendor_id)
            .order_by(VendorBill.bill_date.desc(), VendorBill.created_at.desc()).limit(limit).offset(offset))
    if status_filter:
        stmt = stmt.where(VendorBill.status == status_filter)
    if vendor_id:
        stmt = stmt.where(VendorBill.vendor_id == vendor_id)
    return [BillSummary(id=b.id, number=b.number, vendor_reference=b.vendor_reference, status=b.status,
                        vendor_id=b.vendor_id, vendor_name=name, bill_date=b.bill_date, due_date=b.due_date,
                        total=_money(b.total_minor, b.currency), amount_due=_money(b.amount_due_minor, b.currency))
            for b, name in db.execute(stmt)]


@router.post("/orgs/{org_id}/bills", response_model=BillOut, status_code=status.HTTP_201_CREATED)
def create_bill(body: BillIn, request: Request, ctx: OrgContext = Depends(require(P.PURCHASES_WRITE)),
                db: Session = Depends(get_db)):
    """A bill without a purchase order: services and expenses (rent, utilities...)."""
    bill = VendorBill(currency=ctx.organization.default_currency, created_by=ctx.user.id)
    lines = _apply_bill(db, bill, body)
    db.add(bill)
    db.flush()
    for line in lines:
        line.bill_id = bill.id
    db.add_all(lines)
    audit.record(db, actor_user_id=ctx.user.id, action="bill.created", target_type="vendor_bill", target_id=bill.id,
                 details={"total": _money(bill.total_minor, bill.currency)}, ip_address=client_ip(request))
    db.commit()
    return bill_out(db, bill)


@router.get("/orgs/{org_id}/bills/{bill_id}", response_model=BillOut)
def read_bill(bill_id: str, _: OrgContext = Depends(require(P.PURCHASES_READ)), db: Session = Depends(get_db)):
    return bill_out(db, _get_bill(db, bill_id))


@router.put("/orgs/{org_id}/bills/{bill_id}", response_model=BillOut)
def replace_bill(bill_id: str, body: BillIn, request: Request, ctx: OrgContext = Depends(require(P.PURCHASES_WRITE)),
                 db: Session = Depends(get_db)):
    bill = _get_bill(db, bill_id, lock=True)
    if bill.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "only draft bills can be edited")
    if _order_of_bill(db, bill) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "bills from a purchase order follow the order; delete and bill again")
    lines = _apply_bill(db, bill, body)
    for old in svc.bill_lines(db, bill):
        db.delete(old)
    db.flush()
    for line in lines:
        line.bill_id = bill.id
    db.add_all(lines)
    audit.record(db, actor_user_id=ctx.user.id, action="bill.updated", target_type="vendor_bill", target_id=bill.id,
                 details={"total": _money(bill.total_minor, bill.currency)}, ip_address=client_ip(request))
    db.commit()
    return bill_out(db, bill)


class VendorPriceIn(BaseModel):
    unit_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=6)


@router.put("/orgs/{org_id}/bills/{bill_id}/lines/{line_id}/price", response_model=BillOut)
def set_vendor_price(bill_id: str, line_id: str, body: VendorPriceIn, request: Request,
                     ctx: OrgContext = Depends(require(P.PURCHASES_WRITE)), db: Session = Depends(get_db)):
    """The vendor's actual unit price on a line billed from a purchase order. When the bill is posted,
    the difference from the order's price goes to purchase price differences (5150)."""
    bill = _get_bill(db, bill_id, lock=True)
    line = _domain(lambda: svc.set_vendor_price(db, bill, line_id, body.unit_cost))
    audit.record(db, actor_user_id=ctx.user.id, action="bill.price_changed", target_type="vendor_bill",
                 target_id=bill.id, details={"line": line.description, "unit_cost": fmt_decimal(body.unit_cost)},
                 ip_address=client_ip(request))
    db.commit()
    return bill_out(db, bill)


@router.delete("/orgs/{org_id}/bills/{bill_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_bill(bill_id: str, request: Request, ctx: OrgContext = Depends(require(P.PURCHASES_WRITE)),
                db: Session = Depends(get_db)):
    bill = _get_bill(db, bill_id, lock=True)
    if bill.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "posted bills cannot be deleted; cancel them instead")
    for line in svc.bill_lines(db, bill):
        db.delete(line)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="bill.deleted", target_type="vendor_bill", target_id=bill.id,
                 details={}, ip_address=client_ip(request))
    db.delete(bill)
    db.commit()


@router.post("/orgs/{org_id}/bills/{bill_id}/post", response_model=BillOut)
def post_bill(bill_id: str, request: Request, ctx: OrgContext = Depends(require(P.BILLS_POST)),
              db: Session = Depends(get_db)):
    bill = _get_bill(db, bill_id, lock=True)
    entry = _domain(lambda: svc.post_bill(db, bill, user_id=ctx.user.id))
    audit.record(db, actor_user_id=ctx.user.id, action="bill.posted", target_type="vendor_bill", target_id=bill.id,
                 details={"number": bill.number, "total": _money(bill.total_minor, bill.currency),
                          "journal_entry": entry.number}, ip_address=client_ip(request))
    db.commit()
    return bill_out(db, bill)


@router.post("/orgs/{org_id}/bills/{bill_id}/cancel", response_model=BillOut)
def cancel_bill(bill_id: str, body: BillCancelIn, request: Request, ctx: OrgContext = Depends(require(P.BILLS_POST)),
                db: Session = Depends(get_db)):
    bill = _get_bill(db, bill_id, lock=True)
    reversal = _domain(lambda: svc.cancel_bill(db, bill, user_id=ctx.user.id, cancel_date=body.cancel_date))
    audit.record(db, actor_user_id=ctx.user.id, action="bill.cancelled", target_type="vendor_bill", target_id=bill.id,
                 details={"number": bill.number, "reversal_entry": reversal.number}, ip_address=client_ip(request))
    db.commit()
    return bill_out(db, bill)


@router.get("/orgs/{org_id}/bills/{bill_id}/payments", response_model=list[BillPaymentOut])
def list_bill_payments(bill_id: str, _: OrgContext = Depends(require(P.PURCHASES_READ)), db: Session = Depends(get_db)):
    bill = _get_bill(db, bill_id)
    rows = db.scalars(select(BillPayment).where(BillPayment.bill_id == bill.id).order_by(BillPayment.payment_date))
    return [_payment_out(p, bill.currency) for p in rows]


def _payment_out(p: BillPayment, currency: str) -> BillPaymentOut:
    return BillPaymentOut(id=p.id, bill_id=p.bill_id, payment_date=p.payment_date,
                          amount=_money(p.amount_minor, currency), method=p.method, reference=p.reference,
                          journal_entry_id=p.journal_entry_id, created_at=p.created_at)


@router.post("/orgs/{org_id}/bills/{bill_id}/payments", response_model=BillPaymentOut,
             status_code=status.HTTP_201_CREATED)
def pay_bill(bill_id: str, body: BillPaymentIn, request: Request, response: Response,
             idempotency_key: str | None = Header(default=None, max_length=100),
             ctx: OrgContext = Depends(require(P.PAYMENTS_WRITE)), db: Session = Depends(get_db)):
    bill = _get_bill(db, bill_id, lock=True)
    payment, created = _domain(lambda: svc.pay_bill(
        db, bill, amount_minor=to_minor(body.amount, bill.currency), payment_date=body.payment_date,
        method=body.method, reference=body.reference, user_id=ctx.user.id, idempotency_key=idempotency_key))
    if created:
        audit.record(db, actor_user_id=ctx.user.id, action="bill.payment_registered", target_type="vendor_bill",
                     target_id=bill.id, details={"number": bill.number, "amount": _money(payment.amount_minor, bill.currency)},
                     ip_address=client_ip(request))
        db.commit()
    else:
        response.status_code = status.HTTP_200_OK
    return _payment_out(payment, bill.currency)


# ---------------------------------------------------------------- payables


class PayablesRow(BaseModel):
    vendor_id: str
    vendor_name: str
    bills: int
    buckets: dict[str, str]
    total_due: str


class PayablesOut(BaseModel):
    as_of: date
    currency: str
    vendors: list[PayablesRow]
    totals: dict[str, str]
    total_due: str


@router.get("/orgs/{org_id}/reports/payables", response_model=PayablesOut)
def payables_aging(as_of: date | None = None, ctx: OrgContext = Depends(require(P.PURCHASES_READ)),
                   db: Session = Depends(get_db)):
    """What is owed to each vendor, by how many days past the due date."""
    as_of = as_of or date.today()
    currency = ctx.organization.default_currency
    rows = db.execute(select(VendorBill, Vendor.name).join(Vendor, Vendor.id == VendorBill.vendor_id)
                      .where(VendorBill.status.in_(("posted", "partially_paid")), VendorBill.bill_date <= as_of)).all()
    per_vendor: dict[str, dict] = {}
    totals: dict[str, int] = defaultdict(int)
    for bill, name in rows:
        bucket = _bucket((as_of - bill.due_date).days)
        v = per_vendor.setdefault(bill.vendor_id, {"name": name, "buckets": defaultdict(int), "count": 0})
        v["buckets"][bucket] += bill.amount_due_minor
        v["count"] += 1
        totals[bucket] += bill.amount_due_minor
    names = [b[0] for b in AGING_BUCKETS]
    ordered = sorted(per_vendor.items(), key=lambda kv: -sum(kv[1]["buckets"].values()))
    return PayablesOut(
        as_of=as_of, currency=currency,
        vendors=[PayablesRow(vendor_id=vid, vendor_name=v["name"], bills=v["count"],
                             buckets={n: _money(v["buckets"][n], currency) for n in names},
                             total_due=_money(sum(v["buckets"].values()), currency)) for vid, v in ordered],
        totals={n: _money(totals[n], currency) for n in names},
        total_due=_money(sum(totals.values()), currency),
    )
