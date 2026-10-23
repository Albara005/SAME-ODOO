"""Purchasing: purchase order -> receipts -> vendor bill -> payments.

* Receiving goods from an order adds stock at the order's unit cost:
      Dr Inventory, Cr Goods Received Not Invoiced
* The vendor's bill clears that for received goods and expenses everything else:
      Dr Goods Received Not Invoiced (goods from the order) / Dr expense account (services, direct bills)
      Dr Input VAT (tax)
          Cr Accounts Payable (vendor)       total
* Paying the bill:  Dr Accounts Payable (vendor), Cr Bank/Cash.
* Goods are billed by received quantity, services by ordered quantity. Billed quantity
  is derived from bill lines (cancelled bills excluded), like invoiced quantity on sales.
* Bill lines from an order keep the order's unit cost, so the clearing account
  empties exactly; a different price means editing the order first.
* Vendor credits (goods sent back, allowances) are in ``credit.py``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from novara.db import utcnow
from novara.modules.accounting.models import Account, JournalEntry
from novara.modules.accounting.service import LineSpec, next_number, post_entry, reverse_entry, system_account
from novara.modules.catalog.models import Product
from novara.modules.inventory import service as stock
from novara.modules.inventory.models import Warehouse
from novara.modules.invoicing.service import PAYMENT_METHODS, line_net_minor, tax_groups
from novara.modules.purchasing.models import BillPayment, PurchaseOrder, PurchaseOrderLine, VendorBill, VendorBillLine
from novara.money import from_minor


class PurchaseStateError(RuntimeError):
    """Not allowed in the document's current state (HTTP 409)."""


class PurchaseValidationError(ValueError):
    """Invalid request for this document (HTTP 422)."""


def recompute(doc: PurchaseOrder | VendorBill, lines: list[PurchaseOrderLine] | list[VendorBillLine]) -> None:
    for line in lines:
        line.net_minor = line_net_minor(line.quantity, line.unit_cost, Decimal(0), doc.currency)
    doc.subtotal_minor = sum(l.net_minor for l in lines)
    doc.tax_minor = sum(g.tax_minor for g in tax_groups(lines, doc.currency))
    doc.total_minor = doc.subtotal_minor + doc.tax_minor


def order_lines(db: Session, order: PurchaseOrder) -> list[PurchaseOrderLine]:
    return list(db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.order_id == order.id)
                           .order_by(PurchaseOrderLine.position)))


def bill_lines(db: Session, bill: VendorBill) -> list[VendorBillLine]:
    return list(db.scalars(select(VendorBillLine).where(VendorBillLine.bill_id == bill.id)
                           .order_by(VendorBillLine.position)))


def lock_order(db: Session, order_id: str) -> PurchaseOrder | None:
    return db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == order_id).with_for_update())


def lock_bill(db: Session, bill_id: str) -> VendorBill | None:
    return db.scalar(select(VendorBill).where(VendorBill.id == bill_id).with_for_update())


@dataclass(frozen=True)
class LineProgress:
    line: PurchaseOrderLine
    product: Product | None
    billed: Decimal

    @property
    def is_goods(self) -> bool:
        return self.product is not None and self.product.kind == "goods"

    @property
    def to_receive(self) -> Decimal:
        return self.line.quantity - self.line.qty_received if self.is_goods else Decimal(0)

    @property
    def to_bill(self) -> Decimal:
        basis = self.line.qty_received if self.is_goods else self.line.quantity
        return max(basis - self.billed, Decimal(0))


def progress(db: Session, order: PurchaseOrder) -> list[LineProgress]:
    lines = order_lines(db, order)
    billed: dict[str, Decimal] = defaultdict(Decimal)
    rows = db.execute(
        select(VendorBillLine.purchase_order_line_id, VendorBillLine.quantity)
        .join(VendorBill, VendorBill.id == VendorBillLine.bill_id)
        .where(VendorBillLine.purchase_order_line_id.in_([l.id for l in lines]), VendorBill.status != "cancelled")
    )
    for line_id, qty in rows:
        billed[line_id] += qty
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_({l.product_id for l in lines if l.product_id})))}
    return [LineProgress(l, products.get(l.product_id), billed[l.id]) for l in lines]


def receipt_status(items: list[LineProgress]) -> str:
    goods = [i for i in items if i.is_goods]
    if not goods:
        return "not_applicable"
    if sum(i.line.qty_received for i in goods) == 0:
        return "pending"
    return "received" if all(i.to_receive == 0 for i in goods) else "partial"


def bill_status(items: list[LineProgress]) -> str:
    if sum(i.billed for i in items) == 0:
        return "pending" if any(i.to_bill > 0 for i in items) else "nothing_to_bill"
    return "billed" if all(i.billed >= i.line.quantity for i in items) else "partial"


def confirm(order: PurchaseOrder, lines: list[PurchaseOrderLine]) -> None:
    if order.status != "draft":
        raise PurchaseStateError(f"only draft orders can be confirmed (status: {order.status})")
    if not lines:
        raise PurchaseValidationError("an order needs at least one line")
    order.status = "confirmed"
    order.confirmed_at = utcnow()


def cancel(db: Session, order: PurchaseOrder) -> None:
    if order.status == "cancelled":
        raise PurchaseStateError("order is already cancelled")
    items = progress(db, order)
    if any(i.line.qty_received > 0 or i.billed > 0 for i in items):
        raise PurchaseStateError("orders with receipts or bills cannot be cancelled")
    order.status = "cancelled"
    order.cancelled_at = utcnow()


def receive(db: Session, order: PurchaseOrder, *, move_date: date, quantities: dict[str, Decimal] | None,
            user_id: str) -> stock.StockResult:
    """Receive goods. ``quantities`` maps order line id -> qty; None receives everything outstanding."""
    if order.status != "confirmed":
        raise PurchaseStateError("only confirmed orders can be received")
    if move_date < order.order_date:
        raise PurchaseValidationError("the receipt cannot be dated before the order")
    items = {i.line.id: i for i in progress(db, order)}
    if quantities is None:
        quantities = {lid: i.to_receive for lid, i in items.items() if i.to_receive > 0}
    if not quantities:
        raise PurchaseStateError("nothing left to receive")
    plan = []
    for line_id, qty in quantities.items():
        item = items.get(line_id)
        if item is None:
            raise PurchaseValidationError(f"line {line_id} is not on this order")
        if not item.is_goods:
            raise PurchaseValidationError(f"'{item.line.description}' is not a stocked product")
        if qty <= 0 or qty > item.to_receive:
            raise PurchaseValidationError(f"'{item.line.description}': can receive at most {item.to_receive.normalize():f}")
        plan.append((item, qty))
    warehouse = db.get(Warehouse, order.warehouse_id) if order.warehouse_id else stock.default_warehouse(db)
    result = stock.receive(db, warehouse=warehouse, move_date=move_date, currency=order.currency, counterpart="grni",
                           lines=[(i.product, qty, i.line.unit_cost) for i, qty in plan], memo=order.number,
                           user_id=user_id, source_type="purchase_order", source_id=order.id,
                           source_line_ids=[i.line.id for i, _ in plan])
    for item, qty in plan:
        item.line.qty_received = item.line.qty_received + qty
    return result


def bill_from_order(db: Session, order: PurchaseOrder, *, bill_date: date, due_date: date | None,
                    vendor_reference: str, user_id: str) -> VendorBill:
    if order.status != "confirmed":
        raise PurchaseStateError("only confirmed orders can be billed")
    if bill_date < order.order_date:
        raise PurchaseValidationError("the bill cannot be dated before the order")
    items = [i for i in progress(db, order) if i.to_bill > 0]
    if not items:
        raise PurchaseStateError("nothing to bill: receive goods first, or everything is already billed")
    bill = VendorBill(vendor_id=order.vendor_id, vendor_reference=vendor_reference, bill_date=bill_date,
                      due_date=due_date or bill_date, currency=order.currency, created_by=user_id)
    db.add(bill)
    db.flush()
    expense = system_account(db, "expense")
    lines = [
        VendorBillLine(bill_id=bill.id, position=n, product_id=i.line.product_id, description=i.line.description,
                       quantity=i.to_bill, unit_cost=i.line.unit_cost, tax_id=i.line.tax_id, tax_rate=i.line.tax_rate,
                       account_id=None if i.is_goods else expense.id, purchase_order_line_id=i.line.id)
        for n, i in enumerate(items, start=1)
    ]
    recompute(bill, lines)
    db.add_all(lines)
    db.flush()
    return bill


def post_bill(db: Session, bill: VendorBill, *, user_id: str) -> JournalEntry:
    if bill.status != "draft":
        raise PurchaseStateError(f"only draft bills can be posted (status: {bill.status})")
    lines = bill_lines(db, bill)
    if not lines:
        raise PurchaseValidationError("a bill needs at least one line")
    recompute(bill, lines)
    if bill.total_minor <= 0:
        raise PurchaseValidationError("bill total must be greater than zero")
    year = bill.bill_date.year
    bill.number = next_number(db, f"bill:{year}", f"BILL-{year}-")
    debits: dict[str, int] = defaultdict(int)
    grni = system_account(db, "grni")
    for line in lines:
        debits[line.account_id or grni.id] += line.net_minor
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(set(debits))))}
    specs = [LineSpec(accounts[aid], debit_minor=amount, label=f"Bill {bill.number}")
             for aid, amount in sorted(debits.items(), key=lambda kv: accounts[kv[0]].code)]
    specs.append(LineSpec(system_account(db, "vat_input"), debit_minor=bill.tax_minor, label=f"VAT on {bill.number}"))
    specs.append(LineSpec(system_account(db, "payable"), credit_minor=bill.total_minor, vendor_id=bill.vendor_id,
                          label=f"Bill {bill.number} {bill.vendor_reference}".strip()))
    entry = post_entry(db, entry_date=bill.bill_date, currency=bill.currency, lines=specs,
                       memo=f"Vendor bill {bill.number}", source_type="vendor_bill", source_id=bill.id,
                       created_by=user_id)
    bill.status = "posted"
    bill.journal_entry_id = entry.id
    bill.posted_at = utcnow()
    bill.posted_by = user_id
    return entry


def pay_bill(db: Session, bill: VendorBill, *, amount_minor: int, payment_date: date, method: str, reference: str,
             user_id: str, idempotency_key: str | None = None) -> tuple[BillPayment, bool]:
    if idempotency_key:
        existing = db.scalar(select(BillPayment).where(BillPayment.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.bill_id != bill.id or existing.amount_minor != amount_minor:
                raise PurchaseStateError("idempotency key already used for a different payment")
            return existing, False
    if bill.status not in ("posted", "partially_paid"):
        raise PurchaseStateError(f"bill cannot be paid (status: {bill.status})")
    if method not in PAYMENT_METHODS:
        raise PurchaseValidationError(f"unknown payment method: {method}")
    if amount_minor <= 0 or amount_minor > bill.amount_due_minor:
        raise PurchaseValidationError(
            f"payment must be between 0 and the open amount {from_minor(bill.amount_due_minor, bill.currency)}")
    if payment_date < bill.bill_date:
        raise PurchaseValidationError("payment date cannot be before the bill date")
    entry = post_entry(
        db, entry_date=payment_date, currency=bill.currency, created_by=user_id,
        memo=f"Payment of {bill.number}", source_type="bill_payment", source_id=bill.id,
        lines=[LineSpec(system_account(db, "payable"), debit_minor=amount_minor, vendor_id=bill.vendor_id,
                        label=f"Payment {bill.number}"),
               LineSpec(system_account(db, PAYMENT_METHODS[method]), credit_minor=amount_minor,
                        label=f"Payment {bill.number} {reference}".strip())],
    )
    payment = BillPayment(bill_id=bill.id, payment_date=payment_date, amount_minor=amount_minor, method=method,
                          reference=reference, journal_entry_id=entry.id, idempotency_key=idempotency_key,
                          created_by=user_id)
    db.add(payment)
    bill.amount_paid_minor += amount_minor
    settle(bill)
    db.flush()
    return payment, True


def settle(bill: VendorBill) -> None:
    """Status after a payment or a vendor credit: paid/credited when nothing is due, else (partially) paid."""
    if bill.amount_due_minor == 0:
        bill.status = "paid" if bill.amount_paid_minor > 0 else "credited"
    else:
        bill.status = "partially_paid" if bill.amount_paid_minor > 0 else "posted"


def cancel_bill(db: Session, bill: VendorBill, *, user_id: str, cancel_date: date) -> JournalEntry:
    if bill.status != "posted":
        raise PurchaseStateError("only posted bills without payments can be cancelled" if bill.status != "draft"
                                 else "draft bills are deleted, not cancelled")
    if bill.amount_credited_minor:
        raise PurchaseStateError("bills with vendor credits cannot be cancelled; credit the rest instead")
    if cancel_date < bill.bill_date:
        raise PurchaseValidationError("cancellation date cannot be before the bill date")
    reversal = reverse_entry(db, db.get(JournalEntry, bill.journal_entry_id), entry_date=cancel_date,
                             memo=f"Cancellation of {bill.number}", created_by=user_id)
    bill.status = "cancelled"
    bill.reversal_entry_id = reversal.id
    bill.cancelled_at = utcnow()
    return reversal


class BillLocked(RuntimeError):
    pass


_MUTABLE_AFTER_POST = {"status", "amount_paid_minor", "amount_credited_minor", "reversal_entry_id", "cancelled_at",
                       "number", "journal_entry_id", "posted_at", "posted_by"}


@event.listens_for(Session, "before_flush")
def _posted_bills_are_final(session: Session, _ctx, _instances) -> None:
    for obj in session.dirty:
        if isinstance(obj, VendorBill) and session.is_modified(obj):
            history = inspect(obj).attrs.status.history
            if (history.deleted[0] if history.deleted else obj.status) == "draft":
                continue
            changed = {a.key for a in inspect(obj).attrs if a.history.has_changes()}
            if changed - _MUTABLE_AFTER_POST:
                raise BillLocked(f"posted bills cannot be edited ({sorted(changed - _MUTABLE_AFTER_POST)})")
    with session.no_autoflush:
        for line in [o for o in list(session.new) + list(session.dirty) + list(session.deleted)
                     if isinstance(o, VendorBillLine)]:
            if line not in session.new and line not in session.deleted and not session.is_modified(line):
                continue
            bill = session.get(VendorBill, line.bill_id)
            if bill is not None and bill not in session.deleted:
                history = inspect(bill).attrs.status.history
                if (history.deleted[0] if history.deleted else bill.status) != "draft":
                    raise BillLocked("lines of a posted bill cannot be changed")
