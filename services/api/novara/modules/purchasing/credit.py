"""Vendor credits: a vendor's credit note against a posted bill, and goods sent back.

A credit names bill lines and quantities. Each credited line is valued exactly like the
bill line (same unit cost and tax rate); once a line, or every line of a tax rate, has
been credited in full, the last credit takes the exact remainder, like customer credit notes.

    Dr Accounts Payable (vendor)      total
        Cr Input VAT (per tax)            tax
        Cr expense account                services and direct bills (the account the bill debited)
        Cr Inventory                      goods sent back, at the average cost they leave stock at
        Cr/Dr Inventory Adjustments       goods: credited amount minus the stock value sent back
                                          (a price allowance on goods kept, or a price difference)

The total first reduces what is still due on the bill; the rest the vendor owes back, and is
recorded when the money arrives with ``record_refund``:  Dr Bank/Cash, Cr Accounts Payable.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novara.modules.accounting.models import Account, Tax
from novara.modules.accounting.service import LineSpec, next_number, post_entry, system_account
from novara.modules.catalog.models import Product
from novara.modules.inventory import service as stock
from novara.modules.inventory.models import StockMove, Warehouse
from novara.modules.invoicing.service import PAYMENT_METHODS, line_net_minor, tax_groups
from novara.modules.purchasing.models import (VendorBill, VendorBillLine, VendorCredit, VendorCreditLine,
                                              VendorCreditTax, VendorRefund)
from novara.modules.purchasing.service import PurchaseStateError, PurchaseValidationError, bill_lines, settle
from novara.money import from_minor, to_minor

CREDITABLE = ("posted", "partially_paid", "paid", "credited")


@dataclass(frozen=True)
class VendorCreditRequest:
    bill_line_id: str
    quantity: Decimal
    returned: Decimal = Decimal(0)  # units sent back to the vendor (at most ``quantity``)


def credited_so_far(db: Session, bill: VendorBill) -> dict[str, tuple[Decimal, int]]:
    """Per bill line: (quantity credited, net credited)."""
    totals: dict[str, tuple[Decimal, int]] = {}
    rows = db.execute(select(VendorCreditLine.bill_line_id, VendorCreditLine.quantity, VendorCreditLine.net_minor)
                      .join(VendorCredit, VendorCredit.id == VendorCreditLine.credit_id)
                      .where(VendorCredit.bill_id == bill.id))
    for lid, qty, net in rows:
        q, n = totals.get(lid, (Decimal(0), 0))
        totals[lid] = (q + qty, n + net)
    return totals


def _tax_credited(db: Session, bill: VendorBill) -> dict[tuple[str, Decimal], int]:
    rows = db.execute(
        select(VendorCreditTax.tax_id, VendorCreditTax.rate, func.sum(VendorCreditTax.tax_minor))
        .join(VendorCredit, VendorCredit.id == VendorCreditTax.credit_id)
        .where(VendorCredit.bill_id == bill.id)
        .group_by(VendorCreditTax.tax_id, VendorCreditTax.rate)
    )
    return {(t, Decimal(r)): int(v or 0) for t, r, v in rows}


def _receipt_warehouse(db: Session, purchase_order_line_id: str) -> Warehouse | None:
    move = db.scalar(select(StockMove).where(StockMove.source_line_id == purchase_order_line_id,
                                             StockMove.kind == "receipt").limit(1))
    return db.get(Warehouse, move.warehouse_id) if move is not None else None


def create_vendor_credit(db: Session, bill: VendorBill, *, issue_date: date, reason: str, vendor_reference: str,
                         requests: list[VendorCreditRequest], user_id: str) -> VendorCredit:
    if bill.status not in CREDITABLE:
        raise PurchaseStateError(f"only posted bills can be credited (status: {bill.status})")
    if issue_date < bill.bill_date:
        raise PurchaseValidationError("the credit cannot be dated before the bill")
    if not requests:
        raise PurchaseValidationError("name at least one line to credit")
    lines = {l.id: l for l in bill_lines(db, bill)}
    before = credited_so_far(db, bill)
    products = {p.id: p for p in db.scalars(
        select(Product).where(Product.id.in_({l.product_id for l in lines.values() if l.product_id})))}
    seen: set[str] = set()
    credit_lines: list[VendorCreditLine] = []
    for n, req in enumerate(requests, start=1):
        line = lines.get(req.bill_line_id)
        if line is None:
            raise PurchaseValidationError(f"line {n} is not on bill {bill.number}")
        if line.id in seen:
            raise PurchaseValidationError(f"'{line.description}' is listed twice")
        seen.add(line.id)
        qty_before, net_before = before.get(line.id, (Decimal(0), 0))
        open_qty = line.quantity - qty_before
        if req.quantity <= 0 or req.quantity > open_qty:
            raise PurchaseValidationError(f"'{line.description}': can credit at most {open_qty.normalize():f}")
        if req.returned < 0 or req.returned > req.quantity:
            raise PurchaseValidationError(
                f"'{line.description}': returned quantity must be between 0 and the credited quantity")
        received_goods = line.account_id is None and line.purchase_order_line_id is not None
        if req.returned and not received_goods:
            raise PurchaseValidationError(f"'{line.description}' was not received into stock, so it can't be sent back")
        net = line.net_minor - net_before if req.quantity == open_qty else min(
            line_net_minor(req.quantity, line.unit_cost, Decimal(0), bill.currency), line.net_minor - net_before)
        credit_lines.append(VendorCreditLine(
            bill_line_id=line.id, position=n, product_id=line.product_id, description=line.description,
            quantity=req.quantity, unit_cost=line.unit_cost, tax_id=line.tax_id, tax_rate=line.tax_rate,
            net_minor=net, returned_quantity=req.returned))

    # Tax per rate, with the exact remainder once every line of that rate is fully credited.
    bill_tax = {(g.tax_id, g.rate): g.tax_minor for g in tax_groups(list(lines.values()), bill.currency)}
    tax_before = _tax_credited(db, bill)
    credited_after = {lid: q for lid, (q, _) in before.items()}
    for cl in credit_lines:
        credited_after[cl.bill_line_id] = credited_after.get(cl.bill_line_id, Decimal(0)) + cl.quantity
    bases: dict[tuple[str, Decimal], int] = defaultdict(int)
    for cl in credit_lines:
        if cl.tax_id:
            bases[(cl.tax_id, cl.tax_rate)] += cl.net_minor
    taxes: list[VendorCreditTax] = []
    for key, base in sorted(bases.items()):
        remaining = bill_tax.get(key, 0) - tax_before.get(key, 0)
        group_done = all(credited_after.get(l.id, Decimal(0)) == l.quantity
                         for l in lines.values() if (l.tax_id, l.tax_rate) == key)
        amount = remaining if group_done else min(
            to_minor(from_minor(base, bill.currency) * key[1] / Decimal(100), bill.currency), remaining)
        taxes.append(VendorCreditTax(tax_id=key[0], rate=key[1], base_minor=base, tax_minor=max(amount, 0)))

    subtotal = sum(cl.net_minor for cl in credit_lines)
    tax_total = sum(t.tax_minor for t in taxes)
    total = subtotal + tax_total
    if total <= 0:
        raise PurchaseValidationError("the credit total must be greater than zero")

    number = next_number(db, f"vendor_credit:{issue_date.year}", f"VC-{issue_date.year}-")

    # Goods sent back leave stock at average cost, from the warehouse they were received into.
    stock_ref, moves = "", []
    returns: dict[str, list[tuple[Product, Decimal, str]]] = defaultdict(list)
    warehouses: dict[str, Warehouse] = {}
    for cl in credit_lines:
        if cl.returned_quantity:
            line = lines[cl.bill_line_id]
            warehouse = _receipt_warehouse(db, line.purchase_order_line_id)
            product = products.get(line.product_id or "")
            if warehouse is None or product is None:
                raise PurchaseValidationError(f"'{line.description}' was not received into stock, so it can't be sent back")
            returns[warehouse.id].append((product, cl.returned_quantity, line.id))
            warehouses[warehouse.id] = warehouse
    planned: list[tuple[str, list[StockMove]]] = []
    for wid, items in returns.items():
        planned.append(stock.send_back(db, warehouse=warehouses[wid], move_date=issue_date, lines=items,
                                       source_type="vendor_credit", source_id="", user_id=user_id))
    stock_value = -sum(m.value_minor for _, ms in planned for m in ms)

    # Credits: the account each line's cost went to; goods to inventory (stock value) and the rest of
    # their amount to inventory adjustments.
    credits: dict[str, int] = defaultdict(int)
    goods_net = 0
    for cl in credit_lines:
        line = lines[cl.bill_line_id]
        if line.account_id is None:
            goods_net += cl.net_minor
        else:
            credits[line.account_id] += cl.net_minor
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(set(credits))))}
    tax_records = {t.id: t for t in db.scalars(select(Tax).where(Tax.id.in_([t.tax_id for t in taxes])))}
    label = f"Vendor credit {number}"
    specs = [LineSpec(system_account(db, "payable"), debit_minor=total, vendor_id=bill.vendor_id,
                      label=f"{label} for {bill.number} {vendor_reference}".strip())]
    for aid, amount in sorted(credits.items(), key=lambda kv: accounts[kv[0]].code):
        specs.append(LineSpec(accounts[aid], credit_minor=amount, label=label))
    if stock_value:
        specs.append(LineSpec(system_account(db, "inventory"), credit_minor=stock_value, label=label))
    difference = goods_net - stock_value
    if difference:
        adjustment = system_account(db, "inventory_adjustment")
        specs.append(LineSpec(adjustment, credit_minor=difference, label=label) if difference > 0
                     else LineSpec(adjustment, debit_minor=-difference, label=label))
    for t in taxes:  # bills debit all their VAT to input VAT, so credits take it back from there
        specs.append(LineSpec(system_account(db, "vat_input"), credit_minor=t.tax_minor,
                              label=f"{tax_records[t.tax_id].name} on {number}"))
    entry = post_entry(db, entry_date=issue_date, currency=bill.currency, lines=specs,
                       memo=f"{label} for {bill.number}", source_type="vendor_credit", source_id=bill.id,
                       created_by=user_id)

    applied = min(total, bill.amount_due_minor)
    credit = VendorCredit(number=number, bill_id=bill.id, vendor_id=bill.vendor_id, vendor_reference=vendor_reference,
                          issue_date=issue_date, currency=bill.currency, reason=reason, subtotal_minor=subtotal,
                          tax_minor=tax_total, total_minor=total, applied_minor=applied, journal_entry_id=entry.id,
                          stock_reference=", ".join(ref for ref, _ in planned)[:40], created_by=user_id)
    db.add(credit)
    db.flush()
    for cl in credit_lines:
        cl.credit_id = credit.id
    for t in taxes:
        t.credit_id = credit.id
    db.add_all(credit_lines + taxes)
    for ref, ms in planned:
        for m in ms:
            m.source_id = credit.id
        stock.attach(db, ref, ms, entry)
    bill.amount_credited_minor += applied
    settle(bill)
    db.flush()
    return credit


def refundable(credit: VendorCredit) -> int:
    return credit.total_minor - credit.applied_minor - credit.refunded_minor


def record_refund(db: Session, credit: VendorCredit, *, amount_minor: int, refund_date: date, method: str,
                  reference: str, user_id: str, idempotency_key: str | None = None) -> tuple[VendorRefund, bool]:
    """Money the vendor paid back for the part of a credit that didn't reduce an open bill."""
    if idempotency_key:
        existing = db.scalar(select(VendorRefund).where(VendorRefund.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.credit_id != credit.id or existing.amount_minor != amount_minor:
                raise PurchaseStateError("idempotency key already used for a different refund")
            return existing, False
    if method not in PAYMENT_METHODS:
        raise PurchaseValidationError(f"unknown payment method: {method}")
    owed = refundable(credit)
    if amount_minor <= 0 or amount_minor > owed:
        raise PurchaseValidationError(
            f"refund must be between 0 and the amount the vendor owes {from_minor(owed, credit.currency)}")
    if refund_date < credit.issue_date:
        raise PurchaseValidationError("the refund cannot be dated before the credit")
    entry = post_entry(
        db, entry_date=refund_date, currency=credit.currency, created_by=user_id,
        memo=f"Vendor refund for {credit.number}", source_type="vendor_refund", source_id=credit.id,
        lines=[LineSpec(system_account(db, PAYMENT_METHODS[method]), debit_minor=amount_minor,
                        label=f"Vendor refund {credit.number} {reference}".strip()),
               LineSpec(system_account(db, "payable"), credit_minor=amount_minor, vendor_id=credit.vendor_id,
                        label=f"Vendor refund {credit.number}")],
    )
    record = VendorRefund(credit_id=credit.id, refund_date=refund_date, amount_minor=amount_minor, method=method,
                          reference=reference, journal_entry_id=entry.id, idempotency_key=idempotency_key,
                          created_by=user_id)
    db.add(record)
    credit.refunded_minor += amount_minor
    db.flush()
    return record, True


def lock_credit(db: Session, credit_id: str) -> VendorCredit | None:
    return db.scalar(select(VendorCredit).where(VendorCredit.id == credit_id).with_for_update())
