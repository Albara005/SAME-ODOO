"""Sales cycle: quotation -> confirmed order -> deliveries -> invoices.

* Goods are invoiced by delivered quantity; services (and lines without a
  product) by ordered quantity.
* Invoiced quantity is derived from invoice lines linked to the order line,
  excluding cancelled invoices, so deleting a draft or cancelling an invoice
  makes the quantity invoiceable again without any bookkeeping to undo.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novara.db import utcnow
from novara.modules.catalog.models import Product
from novara.modules.inventory import service as stock
from novara.modules.inventory.models import Warehouse
from novara.modules.invoicing.models import Invoice, InvoiceLine
from novara.modules.invoicing.service import line_net_minor, recompute as recompute_invoice, tax_groups
from novara.modules.sales import reservations
from novara.modules.sales.models import SalesOrder, SalesOrderLine


class OrderStateError(RuntimeError):
    """Operation not allowed in the order's current state (HTTP 409)."""


class OrderValidationError(ValueError):
    """Invalid request for this order (HTTP 422)."""


@dataclass(frozen=True)
class LineProgress:
    line: SalesOrderLine
    product: Product | None
    invoiced: Decimal

    @property
    def is_goods(self) -> bool:
        return self.product is not None and self.product.kind == "goods"

    @property
    def to_deliver(self) -> Decimal:
        return self.line.quantity - self.line.qty_delivered if self.is_goods else Decimal(0)

    @property
    def to_invoice(self) -> Decimal:
        basis = self.line.qty_delivered if self.is_goods else self.line.quantity
        return max(basis - self.invoiced, Decimal(0))


def recompute(order: SalesOrder, lines: list[SalesOrderLine]) -> None:
    for line in lines:
        line.net_minor = line_net_minor(line.quantity, line.unit_price, line.discount_percent, order.currency)
    order.subtotal_minor = sum(l.net_minor for l in lines)
    order.tax_minor = sum(g.tax_minor for g in tax_groups(lines, order.currency))
    order.total_minor = order.subtotal_minor + order.tax_minor


def lines_of(db: Session, order: SalesOrder) -> list[SalesOrderLine]:
    return list(db.scalars(select(SalesOrderLine).where(SalesOrderLine.order_id == order.id)
                           .order_by(SalesOrderLine.position)))


def lock_order(db: Session, order_id: str) -> SalesOrder | None:
    return db.scalar(select(SalesOrder).where(SalesOrder.id == order_id).with_for_update())


def progress(db: Session, order: SalesOrder) -> list[LineProgress]:
    lines = lines_of(db, order)
    invoiced: dict[str, Decimal] = defaultdict(Decimal)
    rows = db.execute(
        select(InvoiceLine.sales_order_line_id, InvoiceLine.quantity)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(InvoiceLine.sales_order_line_id.in_([l.id for l in lines]), Invoice.status != "cancelled")
    )
    for line_id, qty in rows:
        invoiced[line_id] += qty
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_({l.product_id for l in lines if l.product_id})))}
    return [LineProgress(l, products.get(l.product_id), invoiced[l.id]) for l in lines]


def delivery_status(items: list[LineProgress]) -> str:
    goods = [i for i in items if i.is_goods]
    if not goods:
        return "not_applicable"
    delivered = sum(i.line.qty_delivered for i in goods)
    if delivered == 0:
        return "pending"
    return "delivered" if all(i.to_deliver == 0 for i in goods) else "partial"


def invoice_status(items: list[LineProgress]) -> str:
    invoiced = sum(i.invoiced for i in items)
    if invoiced == 0:
        return "pending" if any(i.to_invoice > 0 for i in items) else "nothing_to_invoice"
    fully = all(i.invoiced >= i.line.quantity for i in items)
    return "invoiced" if fully else "partial"


def confirm(order: SalesOrder, lines: list[SalesOrderLine]) -> None:
    if order.status != "quotation":
        raise OrderStateError(f"only quotations can be confirmed (status: {order.status})")
    if not lines:
        raise OrderValidationError("an order needs at least one line")
    order.status = "confirmed"
    order.confirmed_at = utcnow()


def deliver(db: Session, order: SalesOrder, *, move_date: date, quantities: dict[str, Decimal] | None,
            user_id: str) -> stock.StockResult:
    """Ship goods. ``quantities`` maps order line id -> qty; None ships everything outstanding."""
    if order.status != "confirmed":
        raise OrderStateError("only confirmed orders can be delivered")
    if move_date < order.order_date:
        raise OrderValidationError("delivery date cannot be before the order date")
    items = {i.line.id: i for i in progress(db, order)}
    if quantities is None:
        quantities = {lid: i.to_deliver for lid, i in items.items() if i.to_deliver > 0}
    if not quantities:
        raise OrderStateError("nothing left to deliver")
    plan = []
    for line_id, qty in quantities.items():
        item = items.get(line_id)
        if item is None:
            raise OrderValidationError(f"line {line_id} is not on this order")
        if not item.is_goods:
            raise OrderValidationError(f"'{item.line.description}' is not a stocked product")
        if qty <= 0 or qty > item.to_deliver:
            raise OrderValidationError(f"'{item.line.description}': can deliver at most {item.to_deliver.normalize():f}")
        plan.append((item, qty))
    warehouse = db.get(Warehouse, order.warehouse_id) if order.warehouse_id else stock.default_warehouse(db)
    for item, qty in plan:
        # Stock held for orders confirmed earlier is theirs (see reservations.py).
        level = stock.stock_level(db, item.product.id, warehouse.id)
        free = level.quantity - reservations.demand_before(db, order, item.product.id, warehouse.id)
        if qty > free:
            raise stock.InsufficientStock(
                f"only {max(free, Decimal(0)).normalize():f} of '{item.product.name}' is free for this order "
                f"in {warehouse.code}; the rest is reserved for orders confirmed earlier")
    result = stock.deliver(db, warehouse=warehouse, move_date=move_date, currency=order.currency,
                           lines=[(i.product, qty, i.line.id) for i, qty in plan],
                           source_type="sales_order", source_id=order.id, user_id=user_id)
    for item, qty in plan:
        item.line.qty_delivered = item.line.qty_delivered + qty
    return result


def create_invoice(db: Session, order: SalesOrder, *, issue_date: date, due_date: date | None,
                   user_id: str) -> Invoice:
    if order.status != "confirmed":
        raise OrderStateError("only confirmed orders can be invoiced")
    if issue_date < order.order_date:
        raise OrderValidationError("invoice date cannot be before the order date")
    items = [i for i in progress(db, order) if i.to_invoice > 0]
    if not items:
        raise OrderStateError("nothing to invoice: deliver goods first, or everything is already invoiced")
    invoice = Invoice(customer_id=order.customer_id, issue_date=issue_date, due_date=due_date or issue_date,
                      currency=order.currency, notes="", created_by=user_id)
    db.add(invoice)
    db.flush()
    lines = [
        InvoiceLine(invoice_id=invoice.id, position=n, product_id=i.line.product_id, description=i.line.description,
                    quantity=i.to_invoice, unit_price=i.line.unit_price, discount_percent=i.line.discount_percent,
                    tax_id=i.line.tax_id, tax_rate=i.line.tax_rate, sales_order_line_id=i.line.id)
        for n, i in enumerate(items, start=1)
    ]
    recompute_invoice(invoice, lines)
    db.add_all(lines)
    db.flush()
    return invoice


def cancel(db: Session, order: SalesOrder) -> None:
    if order.status == "cancelled":
        raise OrderStateError("order is already cancelled")
    items = progress(db, order)
    if any(i.line.qty_delivered > 0 for i in items) or any(i.invoiced > 0 for i in items):
        raise OrderStateError("orders with deliveries or invoices cannot be cancelled")
    order.status = "cancelled"
    order.cancelled_at = utcnow()


def order_has_invoice_links(db: Session, invoice: Invoice) -> bool:
    return bool(db.scalar(select(func.count()).select_from(InvoiceLine).where(
        InvoiceLine.invoice_id == invoice.id, InvoiceLine.sales_order_line_id.is_not(None))))


def order_of_invoice(db: Session, invoice: Invoice) -> SalesOrder | None:
    """The sales order an invoice was created from, if any."""
    return db.scalar(select(SalesOrder).join(SalesOrderLine, SalesOrderLine.order_id == SalesOrder.id)
                     .join(InvoiceLine, InvoiceLine.sales_order_line_id == SalesOrderLine.id)
                     .where(InvoiceLine.invoice_id == invoice.id).limit(1))
