"""Stock operations. Every change is a StockMove plus, when value moves, a journal entry:

    receipt      Dr Inventory              Cr payable / equity / bank / cash / goods received not invoiced
    delivery     Dr Cost of Goods Sold     Cr Inventory
    return       Dr Inventory              Cr Cost of Goods Sold   (goods a customer sent back)
    adjustment   Dr/Cr Inventory           Cr/Dr Inventory Adjustments

Costing is weighted average per warehouse. A delivery takes value
proportionally (qty / on-hand x stock value); delivering the last units takes
the exact remaining value, so empty stock always has zero value and the
Inventory account always equals the sum of stock values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from novara.db import new_id

from novara.modules.accounting.models import JournalEntry
from novara.modules.accounting.service import LineSpec, next_number, post_entry, system_account
from novara.modules.catalog.models import Product
from novara.modules.inventory.models import StockLevel, StockMove, Warehouse
from novara.money import to_minor
from novara.tenancy import current_tenant

RECEIPT_COUNTERPARTS = ("payable", "equity", "bank", "cash")
# Purchase order receipts credit "goods received not invoiced" until the vendor's bill arrives.
_ALL_COUNTERPARTS = RECEIPT_COUNTERPARTS + ("grni",)


class InventoryError(ValueError):
    pass


class InsufficientStock(RuntimeError):
    pass


class StockMoveImmutable(RuntimeError):
    pass


@dataclass(frozen=True)
class StockResult:
    reference: str
    moves: list[StockMove]
    entry: JournalEntry | None


def default_warehouse(db: Session) -> Warehouse:
    wh = db.scalar(select(Warehouse).where(Warehouse.active.is_(True)).order_by(Warehouse.created_at).limit(1))
    if wh is None:
        wh = Warehouse(code="WH", name="Main warehouse")
        db.add(wh)
        db.flush()
    return wh


def stock_level(db: Session, product_id: str, warehouse_id: str) -> StockLevel:
    """The stock level row, locked for update; created if it doesn't exist yet."""
    stmt = select(StockLevel).where(StockLevel.product_id == product_id, StockLevel.warehouse_id == warehouse_id)
    level = db.scalar(stmt.with_for_update())
    if level is not None:
        return level
    if db.get_bind().dialect.name == "postgresql":
        # Safe when two requests create the same level at once: one insert wins, both then lock it.
        db.execute(pg_insert(StockLevel).values(
            id=new_id(), organization_id=current_tenant(db), product_id=product_id, warehouse_id=warehouse_id,
            quantity=Decimal(0), value_minor=0,
        ).on_conflict_do_nothing(constraint="uq_stock_level"))
        return db.scalar(stmt.with_for_update())
    level = StockLevel(product_id=product_id, warehouse_id=warehouse_id, quantity=Decimal(0), value_minor=0)
    db.add(level)
    db.flush()
    return level


def _stockable(product: Product) -> None:
    if product.kind != "goods":
        raise InventoryError(f"'{product.name}' is a service and has no stock")


def _move(db: Session, level: StockLevel, *, reference: str, kind: str, qty: Decimal, value_minor: int,
          move_date: date, user_id: str | None, source_type: str = "", source_id: str = "",
          source_line_id: str | None = None) -> StockMove:
    level.quantity = level.quantity + qty
    level.value_minor = level.value_minor + value_minor
    # Added to the session by _save, once the journal entry (if any) exists.
    return StockMove(reference=reference, kind=kind, product_id=level.product_id, warehouse_id=level.warehouse_id,
                     move_date=move_date, quantity=qty, value_minor=value_minor, source_type=source_type,
                     source_id=source_id, source_line_id=source_line_id, created_by=user_id)


def _outgoing_value(level: StockLevel, qty: Decimal) -> int:
    if qty == level.quantity:
        return level.value_minor
    return int((Decimal(level.value_minor) * qty / level.quantity).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _save(db: Session, reference: str, moves: list[StockMove], entry: JournalEntry | None) -> StockResult:
    for m in moves:
        m.journal_entry_id = entry.id if entry is not None else None
    db.add_all(moves)
    db.flush()
    return StockResult(reference, moves, entry)


def receive(db: Session, *, warehouse: Warehouse, move_date: date, currency: str, counterpart: str,
            lines: list[tuple[Product, Decimal, Decimal]], memo: str, user_id: str,
            source_type: str = "receipt", source_id: str = "", source_line_ids: list[str] | None = None) -> StockResult:
    """Add stock. ``lines`` are (product, quantity, unit cost)."""
    if counterpart not in _ALL_COUNTERPARTS:
        raise InventoryError(f"counterpart must be one of {RECEIPT_COUNTERPARTS}")
    reference = next_number(db, f"receipt:{move_date.year}", f"REC-{move_date.year}-")
    moves = []
    for n, (product, qty, unit_cost) in enumerate(lines):
        _stockable(product)
        if qty <= 0 or unit_cost < 0:
            raise InventoryError("receipts need a positive quantity and a non-negative cost")
        level = stock_level(db, product.id, warehouse.id)
        moves.append(_move(db, level, reference=reference, kind="receipt", qty=qty,
                           value_minor=to_minor(qty * unit_cost, currency), move_date=move_date, user_id=user_id,
                           source_type=source_type, source_id=source_id,
                           source_line_id=source_line_ids[n] if source_line_ids else None))
    total = sum(m.value_minor for m in moves)
    entry = None
    if total:
        entry = post_entry(db, entry_date=move_date, currency=currency, created_by=user_id,
                           memo=f"Stock receipt {reference} {memo}".strip(), source_type="stock", source_id=reference,
                           lines=[LineSpec(system_account(db, "inventory"), debit_minor=total, label=reference),
                                  LineSpec(system_account(db, counterpart), credit_minor=total, label=reference)])
    return _save(db, reference, moves, entry)


def deliver(db: Session, *, warehouse: Warehouse, move_date: date, currency: str,
            lines: list[tuple[Product, Decimal, str | None]], source_type: str, source_id: str,
            user_id: str) -> StockResult:
    """Remove stock at average cost. ``lines`` are (product, quantity, source line id)."""
    reference = next_number(db, f"delivery:{move_date.year}", f"DEL-{move_date.year}-")
    moves = []
    for product, qty, source_line_id in lines:
        _stockable(product)
        if qty <= 0:
            raise InventoryError("delivered quantity must be positive")
        level = stock_level(db, product.id, warehouse.id)
        if level.quantity < qty:
            raise InsufficientStock(f"only {level.quantity.normalize():f} of '{product.name}' in {warehouse.code}")
        value = _outgoing_value(level, qty)
        moves.append(_move(db, level, reference=reference, kind="delivery", qty=-qty, value_minor=-value,
                           move_date=move_date, user_id=user_id, source_type=source_type, source_id=source_id,
                           source_line_id=source_line_id))
    total = -sum(m.value_minor for m in moves)
    entry = None
    if total:
        entry = post_entry(db, entry_date=move_date, currency=currency, created_by=user_id,
                           memo=f"Delivery {reference}", source_type="stock", source_id=reference,
                           lines=[LineSpec(system_account(db, "cogs"), debit_minor=total, label=reference),
                                  LineSpec(system_account(db, "inventory"), credit_minor=total, label=reference)])
    return _save(db, reference, moves, entry)


def return_in(db: Session, *, warehouse: Warehouse, move_date: date, currency: str,
              lines: list[tuple[Product, Decimal, int, str | None]], source_type: str, source_id: str,
              user_id: str) -> StockResult:
    """Take back goods a customer returned. ``lines`` are (product, quantity, value, source line id)."""
    reference = next_number(db, f"return:{move_date.year}", f"RET-{move_date.year}-")
    moves = []
    for product, qty, value, source_line_id in lines:
        _stockable(product)
        if qty <= 0 or value < 0:
            raise InventoryError("returns need a positive quantity and a non-negative value")
        level = stock_level(db, product.id, warehouse.id)
        moves.append(_move(db, level, reference=reference, kind="return", qty=qty, value_minor=value,
                           move_date=move_date, user_id=user_id, source_type=source_type, source_id=source_id,
                           source_line_id=source_line_id))
    total = sum(m.value_minor for m in moves)
    entry = None
    if total:
        entry = post_entry(db, entry_date=move_date, currency=currency, created_by=user_id,
                           memo=f"Customer return {reference}", source_type="stock", source_id=reference,
                           lines=[LineSpec(system_account(db, "inventory"), debit_minor=total, label=reference),
                                  LineSpec(system_account(db, "cogs"), credit_minor=total, label=reference)])
    return _save(db, reference, moves, entry)


def adjust(db: Session, *, warehouse: Warehouse, move_date: date, currency: str,
           lines: list[tuple[Product, Decimal, Decimal | None]], memo: str, user_id: str) -> StockResult:
    """Set counted quantities. ``lines`` are (product, counted quantity, unit cost for increases from zero)."""
    reference = next_number(db, f"adjustment:{move_date.year}", f"ADJ-{move_date.year}-")
    moves = []
    for product, counted, unit_cost in lines:
        _stockable(product)
        if counted < 0:
            raise InventoryError("counted quantity cannot be negative")
        level = stock_level(db, product.id, warehouse.id)
        delta = counted - level.quantity
        if delta == 0:
            continue
        if delta < 0:
            value = -_outgoing_value(level, -delta)
        elif unit_cost is None and level.quantity > 0:  # found more: value at current average cost
            value = int((Decimal(level.value_minor) * delta / level.quantity).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        elif unit_cost is not None:
            value = to_minor(delta * unit_cost, currency)
        else:
            raise InventoryError(f"'{product.name}' has no stock to take a cost from; give unit_cost")
        moves.append(_move(db, level, reference=reference, kind="adjustment", qty=delta, value_minor=value,
                           move_date=move_date, user_id=user_id, source_type="adjustment"))
    if not moves:
        raise InventoryError("counted quantities match the current stock; nothing to adjust")
    gain = sum(m.value_minor for m in moves if m.value_minor > 0)
    loss = -sum(m.value_minor for m in moves if m.value_minor < 0)
    entry = None
    if gain or loss:
        inventory, adjustment = system_account(db, "inventory"), system_account(db, "inventory_adjustment")
        entry = post_entry(db, entry_date=move_date, currency=currency, created_by=user_id,
                           memo=f"Stock adjustment {reference} {memo}".strip(), source_type="stock",
                           source_id=reference,
                           lines=[LineSpec(inventory, debit_minor=gain, label=reference),
                                  LineSpec(adjustment, credit_minor=gain, label=reference),
                                  LineSpec(adjustment, debit_minor=loss, label=reference),
                                  LineSpec(inventory, credit_minor=loss, label=reference)])
    return _save(db, reference, moves, entry)


@event.listens_for(Session, "before_flush")
def _stock_moves_are_append_only(session: Session, _ctx, _instances) -> None:
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, StockMove) and (obj in session.deleted or session.is_modified(obj)):
            raise StockMoveImmutable("stock moves cannot be modified or deleted")
