"""Stock reserved by confirmed sales orders.

What a confirmed order still has to ship is held for it, oldest confirmation first:
an order can only ship what is left after every order confirmed before it takes its
share. Nothing is stored; reservations follow from the open order lines and the stock
levels, so cancelling or delivering an order frees its share immediately.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.modules.inventory import service as stock
from novara.modules.sales.models import SalesOrder, SalesOrderLine


def _open_lines(db: Session, product_ids: set[str] | None = None):
    """(order id, confirmed at, warehouse id, product id, line id, open quantity) of confirmed orders."""
    default_wh = None
    stmt = (select(SalesOrder.id, SalesOrder.confirmed_at, SalesOrder.warehouse_id, SalesOrderLine.product_id,
                   SalesOrderLine.id, SalesOrderLine.quantity, SalesOrderLine.qty_delivered)
            .join(SalesOrderLine, SalesOrderLine.order_id == SalesOrder.id)
            .where(SalesOrder.status == "confirmed", SalesOrderLine.product_id.is_not(None)))
    if product_ids is not None:
        stmt = stmt.where(SalesOrderLine.product_id.in_(product_ids))
    for order_id, confirmed_at, wh, product_id, line_id, qty, delivered in db.execute(stmt):
        if qty > delivered:
            if wh is None:
                default_wh = default_wh or stock.default_warehouse(db).id
            yield order_id, confirmed_at, wh or default_wh, product_id, line_id, qty - delivered


def _key(confirmed_at: datetime | None, order_id: str) -> tuple:
    if confirmed_at is None:
        return (datetime.max, order_id)
    if confirmed_at.tzinfo is not None:
        confirmed_at = confirmed_at.astimezone(timezone.utc).replace(tzinfo=None)
    return (confirmed_at, order_id)


def demand_before(db: Session, order: SalesOrder, product_id: str, warehouse_id: str) -> Decimal:
    """Open quantity of ``product_id`` that orders confirmed before ``order`` still need from the warehouse."""
    mine = _key(order.confirmed_at, order.id)
    return sum((q for oid, at, wh, pid, _, q in _open_lines(db, {product_id})
                if wh == warehouse_id and pid == product_id and _key(at, oid) < mine), Decimal(0))


def reserved_totals(db: Session) -> dict[tuple[str, str], Decimal]:
    """Open demand per (product, warehouse), all confirmed orders."""
    totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for _, _, wh, pid, _, q in _open_lines(db):
        totals[(pid, wh)] += q
    return totals


def reserved_for_lines(db: Session, order: SalesOrder, on_hand: dict[tuple[str, str], Decimal]) -> dict[str, Decimal]:
    """How much of each open line of ``order`` the stock covers, after earlier orders take theirs."""
    rows = sorted(_open_lines(db), key=lambda r: (_key(r[1], r[0]), r[4]))
    left = dict(on_hand)
    out: dict[str, Decimal] = {}
    for oid, _, wh, pid, line_id, q in rows:
        available = max(left.get((pid, wh), Decimal(0)), Decimal(0))
        take = min(q, available)
        left[(pid, wh)] = available - take
        if oid == order.id:
            out[line_id] = take
    return out
