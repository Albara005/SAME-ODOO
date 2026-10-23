from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara import idempotency
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.accounting.models import JournalEntry, JournalLine
from novara.modules.accounting.service import system_account
from novara.modules.audit import service as audit
from novara.modules.catalog.models import Product
from novara.modules.inventory import service as svc
from novara.modules.inventory.models import StockLevel, StockMove, Warehouse
from novara.modules.sales.reservations import reserved_totals
from novara.money import fmt_decimal, from_minor

router = APIRouter(tags=["inventory"])

Qty = dict(gt=0, max_digits=18, decimal_places=4)


class WarehouseIn(BaseModel):
    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,19}$")
    name: str = Field(min_length=1, max_length=200)


class WarehouseOut(BaseModel):
    id: str
    code: str
    name: str
    active: bool


class ReceiptLine(BaseModel):
    product_id: str
    quantity: Decimal = Field(**Qty)
    unit_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=6)


class ReceiptIn(BaseModel):
    warehouse_id: str | None = None
    move_date: date
    counterpart: str = Field(default="payable", pattern="^(payable|equity|bank|cash)$")
    memo: str = Field(default="", max_length=200)
    lines: list[ReceiptLine] = Field(min_length=1, max_length=500)


class AdjustmentLine(BaseModel):
    product_id: str
    counted_quantity: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)


class AdjustmentIn(BaseModel):
    warehouse_id: str | None = None
    move_date: date
    memo: str = Field(default="", max_length=200)
    lines: list[AdjustmentLine] = Field(min_length=1, max_length=500)


class MoveOut(BaseModel):
    id: str
    reference: str
    kind: str
    product_id: str
    warehouse_id: str
    move_date: date
    quantity: str
    value: str
    source_type: str
    source_id: str
    journal_entry_id: str | None


class StockOperationOut(BaseModel):
    reference: str
    journal_entry_id: str | None
    moves: list[MoveOut]


class LevelOut(BaseModel):
    product_id: str
    product_name: str
    sku: str | None
    warehouse_id: str
    warehouse_code: str
    quantity: str
    # Held for confirmed sales orders not delivered yet; available = quantity - reserved (can go negative).
    reserved: str = "0"
    available: str = "0"
    value: str
    average_cost: str


class ValuationOut(BaseModel):
    currency: str
    stock_value: str
    ledger_inventory_balance: str
    matches_ledger: bool


def _wh_out(w: Warehouse) -> WarehouseOut:
    return WarehouseOut(id=w.id, code=w.code, name=w.name, active=w.active)


def move_out(m: StockMove, currency: str) -> MoveOut:
    return MoveOut(id=m.id, reference=m.reference, kind=m.kind, product_id=m.product_id, warehouse_id=m.warehouse_id,
                   move_date=m.move_date, quantity=fmt_decimal(m.quantity), value=str(from_minor(m.value_minor, currency)),
                   source_type=m.source_type, source_id=m.source_id, journal_entry_id=m.journal_entry_id)


def operation_out(result: svc.StockResult, currency: str) -> StockOperationOut:
    return StockOperationOut(reference=result.reference, journal_entry_id=result.entry.id if result.entry else None,
                             moves=[move_out(m, currency) for m in result.moves])


def _replay(db: Session, reference: str, currency: str) -> StockOperationOut:
    moves = db.scalars(select(StockMove).where(StockMove.reference == reference)).all()
    return StockOperationOut(reference=reference, journal_entry_id=moves[0].journal_entry_id if moves else None,
                             moves=[move_out(m, currency) for m in moves])


def _warehouse(db: Session, warehouse_id: str | None) -> Warehouse:
    if warehouse_id is None:
        return svc.default_warehouse(db)
    wh = db.get(Warehouse, warehouse_id)
    if wh is None or not wh.active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown or inactive warehouse")
    return wh


def _products(db: Session, ids: list[str]) -> dict[str, Product]:
    found = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids)))}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown products: {missing}")
    return found


def _run(fn):
    try:
        return fn()
    except svc.InsufficientStock as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except svc.InventoryError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


@router.get("/orgs/{org_id}/warehouses", response_model=list[WarehouseOut])
def list_warehouses(_: OrgContext = Depends(require(P.INVENTORY_READ)), db: Session = Depends(get_db)):
    svc.default_warehouse(db)
    db.commit()
    return [_wh_out(w) for w in db.scalars(select(Warehouse).order_by(Warehouse.code))]


@router.post("/orgs/{org_id}/warehouses", response_model=WarehouseOut, status_code=status.HTTP_201_CREATED)
def create_warehouse(body: WarehouseIn, request: Request, ctx: OrgContext = Depends(require(P.INVENTORY_WRITE)),
                     db: Session = Depends(get_db)):
    wh = Warehouse(code=body.code, name=body.name)
    db.add(wh)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "warehouse code already exists")
    audit.record(db, actor_user_id=ctx.user.id, action="warehouse.created", target_type="warehouse",
                 target_id=wh.id, details={"code": wh.code}, ip_address=client_ip(request))
    db.commit()
    return _wh_out(wh)


@router.post("/orgs/{org_id}/inventory/receipts", response_model=StockOperationOut,
             status_code=status.HTTP_201_CREATED)
def receive_stock(body: ReceiptIn, request: Request, response: Response,
                  idempotency_key: str | None = Header(default=None, max_length=100),
                  ctx: OrgContext = Depends(require(P.INVENTORY_WRITE)), db: Session = Depends(get_db)):
    currency = ctx.organization.default_currency
    h = idempotency.request_hash(body.model_dump(mode="json"))
    try:
        previous = idempotency.replayed(db, "stock.receipt", idempotency_key, h)
    except idempotency.IdempotencyConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    if previous:
        response.status_code = status.HTTP_200_OK
        return _replay(db, previous, currency)
    wh = _warehouse(db, body.warehouse_id)
    products = _products(db, [l.product_id for l in body.lines])
    result = _run(lambda: svc.receive(
        db, warehouse=wh, move_date=body.move_date, currency=currency, counterpart=body.counterpart, memo=body.memo,
        lines=[(products[l.product_id], l.quantity, l.unit_cost) for l in body.lines], user_id=ctx.user.id))
    idempotency.remember(db, "stock.receipt", idempotency_key, h, result.reference)
    audit.record(db, actor_user_id=ctx.user.id, action="stock.received", target_type="stock",
                 target_id=result.reference, details={"lines": len(body.lines), "warehouse": wh.code},
                 ip_address=client_ip(request))
    db.commit()
    return operation_out(result, currency)


@router.post("/orgs/{org_id}/inventory/adjustments", response_model=StockOperationOut,
             status_code=status.HTTP_201_CREATED)
def adjust_stock(body: AdjustmentIn, request: Request, ctx: OrgContext = Depends(require(P.INVENTORY_WRITE)),
                 db: Session = Depends(get_db)):
    currency = ctx.organization.default_currency
    wh = _warehouse(db, body.warehouse_id)
    products = _products(db, [l.product_id for l in body.lines])
    result = _run(lambda: svc.adjust(
        db, warehouse=wh, move_date=body.move_date, currency=currency, memo=body.memo, user_id=ctx.user.id,
        lines=[(products[l.product_id], l.counted_quantity, l.unit_cost) for l in body.lines]))
    audit.record(db, actor_user_id=ctx.user.id, action="stock.adjusted", target_type="stock",
                 target_id=result.reference, details={"lines": len(result.moves), "warehouse": wh.code},
                 ip_address=client_ip(request))
    db.commit()
    return operation_out(result, currency)


@router.get("/orgs/{org_id}/inventory/levels", response_model=list[LevelOut])
def stock_levels(product_id: str | None = None, warehouse_id: str | None = None,
                 ctx: OrgContext = Depends(require(P.INVENTORY_READ)), db: Session = Depends(get_db)):
    currency = ctx.organization.default_currency
    stmt = (select(StockLevel, Product, Warehouse)
            .join(Product, Product.id == StockLevel.product_id)
            .join(Warehouse, Warehouse.id == StockLevel.warehouse_id)
            .order_by(Product.name, Warehouse.code))
    if product_id:
        stmt = stmt.where(StockLevel.product_id == product_id)
    if warehouse_id:
        stmt = stmt.where(StockLevel.warehouse_id == warehouse_id)
    out = []
    reserved = reserved_totals(db)
    for level, product, wh in db.execute(stmt):
        avg = (from_minor(level.value_minor, currency) / level.quantity) if level.quantity else Decimal(0)
        held = reserved.get((product.id, wh.id), Decimal(0))
        out.append(LevelOut(product_id=product.id, product_name=product.name, sku=product.sku, warehouse_id=wh.id,
                            warehouse_code=wh.code, quantity=fmt_decimal(level.quantity),
                            reserved=fmt_decimal(held), available=fmt_decimal(level.quantity - held),
                            value=str(from_minor(level.value_minor, currency)),
                            average_cost=fmt_decimal(avg.quantize(Decimal("0.000001")))))
    return out


@router.get("/orgs/{org_id}/inventory/moves", response_model=list[MoveOut])
def stock_moves(product_id: str | None = None, reference: str | None = None,
                limit: int = Query(default=100, ge=1, le=1000),
                ctx: OrgContext = Depends(require(P.INVENTORY_READ)), db: Session = Depends(get_db)):
    stmt = select(StockMove).order_by(StockMove.move_date.desc(), StockMove.created_at.desc()).limit(limit)
    if product_id:
        stmt = stmt.where(StockMove.product_id == product_id)
    if reference:
        stmt = stmt.where(StockMove.reference == reference)
    return [move_out(m, ctx.organization.default_currency) for m in db.scalars(stmt)]


@router.get("/orgs/{org_id}/inventory/valuation", response_model=ValuationOut)
def valuation(ctx: OrgContext = Depends(require(P.INVENTORY_READ)), db: Session = Depends(get_db)):
    """Stock value from stock levels, checked against the Inventory account in the ledger."""
    currency = ctx.organization.default_currency
    stock_value = int(db.scalar(select(func.coalesce(func.sum(StockLevel.value_minor), 0))))
    inventory = system_account(db, "inventory")
    debit, credit = db.execute(
        select(func.coalesce(func.sum(JournalLine.debit_minor), 0), func.coalesce(func.sum(JournalLine.credit_minor), 0))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalLine.account_id == inventory.id)
    ).one()
    ledger = int(debit) - int(credit)
    db.commit()
    return ValuationOut(currency=currency, stock_value=str(from_minor(stock_value, currency)),
                        ledger_inventory_balance=str(from_minor(ledger, currency)),
                        matches_ledger=stock_value == ledger)
