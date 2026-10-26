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
* Orders and bills can be in another currency than the organization's; the ledger stays in its currency.
  Receipts value goods at the rate on the receipt day (stock is always in the organization's currency), a bill
  is booked at its own rate and clears what the receipts put in GRNI, the change in rate between the two being
  an exchange difference; payments are at the payment day's rate, against what they clear at the bill's.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session

from novara.db import utcnow
from novara.modules.accounting.models import Account, JournalEntry
from novara.modules.accounting.service import LineSpec, next_number, post_entry, reverse_entry, system_account
from novara.modules.catalog.models import Product
from novara.modules.inventory import service as stock
from novara.modules.inventory.models import StockLevel, StockMove, Warehouse
from novara.modules.invoicing import fx
from novara.modules.invoicing.service import PAYMENT_METHODS, base_currency, line_net_minor, tax_groups
from novara.modules.purchasing.models import BillPayment, PurchaseOrder, PurchaseOrderLine, VendorBill, VendorBillLine
from novara.money import convert, from_minor


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


def booked_rate(db: Session, bill: VendorBill) -> Decimal:
    return bill.exchange_rate if bill.currency != base_currency(db) else Decimal(1)


def rate_for(db: Session, currency: str, day: date, given: Decimal | None) -> Decimal:
    try:
        return fx.rate_for(db, currency, day, given)
    except fx.FxError as e:
        raise PurchaseValidationError(str(e)) from None


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
            user_id: str, exchange_rate: Decimal | None = None) -> stock.StockResult:
    """Receive goods. ``quantities`` maps order line id -> qty; None receives everything outstanding.

    An order in another currency is valued at the rate on the receipt day (given, else the latest kept)."""
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
    base = base_currency(db)
    rate = rate_for(db, order.currency, move_date, exchange_rate)
    memo = order.number if order.currency == base else f"{order.number} ({order.currency} at {rate.normalize():f})"
    result = stock.receive(db, warehouse=warehouse, move_date=move_date, currency=base, counterpart="grni",
                           lines=[(i.product, qty, i.line.unit_cost * rate) for i, qty in plan], memo=memo,
                           user_id=user_id, source_type="purchase_order", source_id=order.id,
                           source_line_ids=[i.line.id for i, _ in plan])
    for item, qty in plan:
        item.line.qty_received = item.line.qty_received + qty
    return result


def bill_from_order(db: Session, order: PurchaseOrder, *, bill_date: date, due_date: date | None,
                    vendor_reference: str, user_id: str, exchange_rate: Decimal | None = None) -> VendorBill:
    if order.status != "confirmed":
        raise PurchaseStateError("only confirmed orders can be billed")
    if bill_date < order.order_date:
        raise PurchaseValidationError("the bill cannot be dated before the order")
    items = [i for i in progress(db, order) if i.to_bill > 0]
    if not items:
        raise PurchaseStateError("nothing to bill: receive goods first, or everything is already billed")
    bill = VendorBill(vendor_id=order.vendor_id, vendor_reference=vendor_reference, bill_date=bill_date,
                      due_date=due_date or bill_date, currency=order.currency, created_by=user_id,
                      exchange_rate=rate_for(db, order.currency, bill_date, exchange_rate))
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


def ordered_costs(db: Session, lines: list[VendorBillLine]) -> dict[str, Decimal]:
    """Bill line id -> the purchase order's unit cost, for lines billed from an order."""
    po_ids = {l.purchase_order_line_id for l in lines if l.purchase_order_line_id}
    if not po_ids:
        return {}
    costs = dict(db.execute(select(PurchaseOrderLine.id, PurchaseOrderLine.unit_cost)
                            .where(PurchaseOrderLine.id.in_(po_ids))).all())
    return {l.id: costs[l.purchase_order_line_id] for l in lines if l.purchase_order_line_id in costs}


def set_vendor_price(db: Session, bill: VendorBill, line_id: str, unit_cost: Decimal) -> VendorBillLine:
    """The vendor's actual unit price on a line billed from a purchase order (draft bills only)."""
    if bill.status != "draft":
        raise PurchaseStateError("only a draft bill's prices can change")
    lines = bill_lines(db, bill)
    line = next((l for l in lines if l.id == line_id), None)
    if line is None or line.purchase_order_line_id is None:
        raise PurchaseValidationError("that line isn't billed from a purchase order")
    line.unit_cost = unit_cost
    recompute(bill, lines)
    return line


def _revalue(db: Session, bill: VendorBill, line: VendorBillLine, difference_minor: int, user_id: str) -> list[StockMove]:
    """Put the part of a price difference that belongs to goods still in stock into their value.

    Of a line's quantity, as much as is still on hand (all warehouses) takes its share of the difference,
    spread over the warehouses by quantity; the share of goods already sold or used stays a price
    difference. Stock value never goes below zero. Returns the revaluation moves (quantity 0)."""
    product = db.get(Product, line.product_id) if line.product_id else None
    if product is None or product.kind != "goods" or not difference_minor:
        return []
    levels = list(db.scalars(select(StockLevel).where(StockLevel.product_id == product.id, StockLevel.quantity > 0)
                             .order_by(StockLevel.warehouse_id).with_for_update()))
    on_hand = sum((level.quantity for level in levels), Decimal(0))
    if on_hand <= 0:
        return []
    share = int((Decimal(difference_minor) * min(on_hand, line.quantity) / line.quantity)
                .quantize(Decimal(1), rounding=ROUND_HALF_UP))
    moves, spread = [], 0
    for i, level in enumerate(levels):
        amount = share - spread if i == len(levels) - 1 else int(
            (Decimal(share) * level.quantity / on_hand).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        spread += amount
        amount = max(amount, -level.value_minor)
        if amount:
            level.value_minor += amount
            moves.append(StockMove(reference=bill.number, kind="revaluation", product_id=product.id,
                                   warehouse_id=level.warehouse_id, move_date=bill.bill_date, quantity=Decimal(0),
                                   value_minor=amount, source_type="vendor_bill", source_id=bill.id,
                                   source_line_id=line.id, created_by=user_id))
    return moves


def _grni_share(db: Session, bill: VendorBill, line: VendorBillLine) -> int:
    """What a bill line from a purchase order takes out of GRNI, in the organization's currency: its share of what the
    order line's receipts put there (at their own rates) not yet cleared by other bills; the last takes the rest."""
    po_line = db.get(PurchaseOrderLine, line.purchase_order_line_id)
    received = int(db.scalar(select(func.coalesce(func.sum(StockMove.value_minor), 0)).where(
        StockMove.kind == "receipt", StockMove.source_type == "purchase_order",
        StockMove.source_line_id == po_line.id)))
    others = db.execute(select(VendorBill.grni_base, VendorBillLine.quantity)
                        .join(VendorBillLine, VendorBillLine.bill_id == VendorBill.id)
                        .where(VendorBillLine.purchase_order_line_id == po_line.id, VendorBill.id != bill.id,
                               VendorBill.status.not_in(("draft", "cancelled")))).all()
    left = received - sum(int((g or {}).get(po_line.id, 0)) for g, _ in others)
    open_qty = po_line.qty_received - sum((q for _, q in others), Decimal(0))
    if open_qty <= 0 or line.quantity >= open_qty:
        return left
    return int((Decimal(left) * line.quantity / open_qty).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def post_bill(db: Session, bill: VendorBill, *, user_id: str) -> JournalEntry:
    if bill.status != "draft":
        raise PurchaseStateError(f"only draft bills can be posted (status: {bill.status})")
    lines = bill_lines(db, bill)
    if not lines:
        raise PurchaseValidationError("a bill needs at least one line")
    recompute(bill, lines)
    if bill.total_minor <= 0:
        raise PurchaseValidationError("bill total must be greater than zero")
    base = base_currency(db)
    foreign = bill.currency != base
    if foreign and bill.exchange_rate <= 0:
        raise PurchaseValidationError(f"a bill in {bill.currency} needs its exchange rate to {base}")
    rate = booked_rate(db, bill)
    to_base = lambda minor: convert(minor, bill.currency, rate, base)  # noqa: E731
    year = bill.bill_date.year
    bill.number = next_number(db, f"bill:{year}", f"BILL-{year}-")
    bill.base_subtotal_minor = to_base(bill.subtotal_minor)
    base_tax = sum(to_base(g.tax_minor) for g in tax_groups(lines, bill.currency))
    bill.base_total_minor = bill.base_subtotal_minor + base_tax
    bill.base_settled_minor = 0
    debits: dict[str, int] = defaultdict(int)
    grni, exchange = system_account(db, "grni"), system_account(db, "fx_gain_loss")
    ordered = ordered_costs(db, lines)
    revaluations: list[StockMove] = []
    cleared: dict[str, int] = {}
    for line in lines:
        if line.account_id is None and line.id in ordered:
            # Received goods clear GRNI at the order's price (what the receipt credited). A vendor charging
            # another price leaves a difference: the share for goods still in stock changes their value
            # (and average cost); the rest, for goods already gone, is a purchase price difference.
            at_order_price = line_net_minor(line.quantity, ordered[line.id], Decimal(0), bill.currency)
            if foreign:
                # In another currency the receipts put their own rates' value in GRNI: that is what clears, and
                # the bill's rate making it more or less is an exchange difference (more owed: a loss).
                cleared[line.purchase_order_line_id] = _grni_share(db, bill, line)
                debits[grni.id] += cleared[line.purchase_order_line_id]
                debits[exchange.id] += to_base(at_order_price) - cleared[line.purchase_order_line_id]
                difference = to_base(line.net_minor) - to_base(at_order_price)
            else:
                debits[grni.id] += at_order_price
                difference = line.net_minor - at_order_price
            moves = _revalue(db, bill, line, difference, user_id)
            in_stock = sum(m.value_minor for m in moves)
            revaluations += moves
            if in_stock:
                debits[system_account(db, "inventory").id] += in_stock
            if difference - in_stock:
                debits[system_account(db, "price_variance").id] += difference - in_stock
        else:
            debits[line.account_id or grni.id] += to_base(line.net_minor)
    # Lines converted one by one can round a unit away from the converted subtotal.
    debits[exchange.id] += bill.base_subtotal_minor - sum(debits.values())
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(set(debits))))}

    def label(aid: str, amount: int) -> str:
        if aid == exchange.id:
            return f"Exchange difference on {bill.number}"
        return f"Bill {bill.number}" if amount >= 0 else f"Price difference on {bill.number}"
    # A lower vendor price makes the difference a credit.
    specs = [LineSpec(accounts[aid], debit_minor=max(amount, 0), credit_minor=max(-amount, 0), label=label(aid, amount))
             for aid, amount in sorted(debits.items(), key=lambda kv: accounts[kv[0]].code) if amount]
    note = f" ({bill.currency} {from_minor(bill.total_minor, bill.currency)} at {rate.normalize():f})" if foreign else ""
    specs.append(LineSpec(system_account(db, "vat_input"), debit_minor=base_tax, label=f"VAT on {bill.number}"))
    specs.append(LineSpec(system_account(db, "payable"), credit_minor=bill.base_total_minor, vendor_id=bill.vendor_id,
                          label=f"Bill {bill.number} {bill.vendor_reference}{note}".strip()))
    entry = post_entry(db, entry_date=bill.bill_date, currency=base, lines=specs,
                       memo=f"Vendor bill {bill.number}{note}", source_type="vendor_bill", source_id=bill.id,
                       created_by=user_id)
    for m in revaluations:
        m.journal_entry_id = entry.id
    db.add_all(revaluations)
    bill.grni_base = cleared
    bill.status = "posted"
    bill.journal_entry_id = entry.id
    bill.posted_at = utcnow()
    bill.posted_by = user_id
    return entry


def pay_bill(db: Session, bill: VendorBill, *, amount_minor: int, payment_date: date, method: str, reference: str,
             user_id: str, idempotency_key: str | None = None,
             exchange_rate: Decimal | None = None) -> tuple[BillPayment, bool]:
    """Pay a bill. For a bill in another currency, exchange_rate is the rate on the payment date (required)."""
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
    base = base_currency(db)
    foreign = bill.currency != base
    if foreign and (exchange_rate is None or exchange_rate <= 0):
        raise PurchaseValidationError(f"a payment in {bill.currency} needs the exchange rate to {base} on the payment date")
    rate = exchange_rate if foreign else Decimal(1)
    paid = convert(amount_minor, bill.currency, rate, base)
    cleared = (bill.base_due_minor if amount_minor == bill.amount_due_minor
               else min(convert(amount_minor, bill.currency, booked_rate(db, bill), base), bill.base_due_minor))
    difference = paid - cleared                  # paying more than was booked is a loss
    lines = [LineSpec(system_account(db, "payable"), debit_minor=cleared, vendor_id=bill.vendor_id,
                      label=f"Payment {bill.number}"),
             LineSpec(system_account(db, PAYMENT_METHODS[method]), credit_minor=paid,
                      label=f"Payment {bill.number} {reference}".strip())]
    if difference:
        lines.append(LineSpec(system_account(db, "fx_gain_loss"), debit_minor=max(difference, 0),
                              credit_minor=max(-difference, 0),
                              label=f"Exchange {'loss' if difference > 0 else 'gain'} on {bill.number}"))
    entry = post_entry(
        db, entry_date=payment_date, currency=base, created_by=user_id,
        memo=f"Payment of {bill.number}", source_type="bill_payment", source_id=bill.id, lines=lines,
    )
    payment = BillPayment(bill_id=bill.id, payment_date=payment_date, amount_minor=amount_minor, method=method,
                          reference=reference, journal_entry_id=entry.id, idempotency_key=idempotency_key,
                          created_by=user_id, exchange_rate=rate, base_amount_minor=paid, fx_minor=difference)
    db.add(payment)
    bill.amount_paid_minor += amount_minor
    bill.base_settled_minor += cleared
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
    _undo_revaluations(db, bill, cancel_date, user_id)
    bill.status = "cancelled"
    bill.reversal_entry_id = reversal.id
    bill.cancelled_at = utcnow()
    return reversal


def _undo_revaluations(db: Session, bill: VendorBill, cancel_date: date, user_id: str) -> None:
    """Take a cancelled bill's revaluations back out of the stock that is still there.

    The reversal entry takes the whole revaluation out of the inventory account; stock sold since
    carried part of it into cost of sales, so only what remains in stock value can come back out.
    The part that can't is moved from inventory to price differences, keeping the ledger equal to
    the stock's value."""
    done = list(db.scalars(select(StockMove).where(StockMove.kind == "revaluation", StockMove.source_type == "vendor_bill",
                                                   StockMove.source_id == bill.id, StockMove.value_minor != 0)))
    if not done:
        return
    undone, stuck = [], 0
    for m in done:
        level = stock.stock_level(db, m.product_id, m.warehouse_id)
        back = -m.value_minor
        if back < 0:
            back = max(back, -level.value_minor)          # can't take out more value than is left
        elif level.quantity <= 0:
            back = 0                                       # no stock left to carry the value back
        if back:
            level.value_minor += back
            undone.append(StockMove(reference=f"{bill.number}-X", kind="revaluation", product_id=m.product_id,
                                    warehouse_id=m.warehouse_id, move_date=cancel_date, quantity=Decimal(0),
                                    value_minor=back, source_type="vendor_bill_cancel", source_id=bill.id,
                                    source_line_id=m.source_line_id, created_by=user_id))
        stuck += -m.value_minor - back
    entry = None
    if stuck:
        inventory, variance = system_account(db, "inventory"), system_account(db, "price_variance")
        label = f"Revaluation of goods already sold, {bill.number}"
        lines = ([LineSpec(inventory, debit_minor=-stuck, label=label), LineSpec(variance, credit_minor=-stuck, label=label)]
                 if stuck < 0 else
                 [LineSpec(variance, debit_minor=stuck, label=label), LineSpec(inventory, credit_minor=stuck, label=label)])
        entry = post_entry(db, entry_date=cancel_date, currency=base_currency(db), lines=lines, created_by=user_id,
                           memo=f"Stock revaluation on cancelling {bill.number}", source_type="vendor_bill",
                           source_id=bill.id)
    for m in undone:
        m.journal_entry_id = entry.id if entry is not None else None
    db.add_all(undone)


class BillLocked(RuntimeError):
    pass


_MUTABLE_AFTER_POST = {"status", "amount_paid_minor", "amount_credited_minor", "reversal_entry_id", "cancelled_at",
                       "number", "journal_entry_id", "posted_at", "posted_by", "base_settled_minor"}


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
