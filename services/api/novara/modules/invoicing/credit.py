"""Credit notes and refunds against posted invoices.

A credit note names invoice lines and quantities. Each credited line is valued
exactly like the invoice line (same price, discount and tax rate); when a line,
or every line of a tax rate, has been credited in full, the last credit note
takes the exact remainder, so crediting an invoice in pieces never drifts by a
cent from crediting it at once.

    Dr Sales Revenue          subtotal
    Dr Output VAT (per tax)   tax
        Cr Accounts Receivable    total

The total first reduces what is still due on the invoice; the rest is owed to
the customer and paid out with ``refund``:  Dr Accounts Receivable, Cr Bank/Cash.

Goods delivered from stock (invoice lines that came from a sales order) can come
back to the warehouse they left, valued at the cost they left at:
    Dr Inventory, Cr Cost of Goods Sold.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novara.modules.accounting.models import Account, Tax
from novara.modules.accounting.service import LineSpec, next_number, post_entry, system_account
from novara.modules.catalog.models import Product
from novara.modules.inventory import service as stock
from novara.modules.inventory.models import StockMove, Warehouse
from novara.modules.invoicing.models import CreditNote, CreditNoteLine, CreditNoteTax, Invoice, InvoiceLine, Refund
from novara.modules.invoicing.service import (PAYMENT_METHODS, InvoiceStateError, InvoiceValidationError,
                                              line_net_minor, lines_of, settle, tax_groups)
from novara.money import from_minor, to_minor

CREDITABLE = ("posted", "partially_paid", "paid", "credited")


@dataclass(frozen=True)
class CreditRequest:
    invoice_line_id: str
    quantity: Decimal
    returned: Decimal = Decimal(0)  # units coming back into stock (at most ``quantity``)


def credited_so_far(db: Session, invoice: Invoice) -> dict[str, tuple[Decimal, int]]:
    """Per invoice line: (quantity credited, net credited)."""
    totals: dict[str, tuple[Decimal, int]] = {}
    rows = db.execute(select(CreditNoteLine.invoice_line_id, CreditNoteLine.quantity, CreditNoteLine.net_minor)
                      .join(CreditNote, CreditNote.id == CreditNoteLine.credit_note_id)
                      .where(CreditNote.invoice_id == invoice.id))
    for lid, qty, net in rows:
        q, n = totals.get(lid, (Decimal(0), 0))
        totals[lid] = (q + qty, n + net)
    return totals


def _tax_credited(db: Session, invoice: Invoice) -> dict[tuple[str, Decimal], int]:
    rows = db.execute(
        select(CreditNoteTax.tax_id, CreditNoteTax.rate, func.sum(CreditNoteTax.tax_minor))
        .join(CreditNote, CreditNote.id == CreditNoteTax.credit_note_id)
        .where(CreditNote.invoice_id == invoice.id)
        .group_by(CreditNoteTax.tax_id, CreditNoteTax.rate)
    )
    return {(t, Decimal(r)): int(v or 0) for t, r, v in rows}


def _delivery_cost(db: Session, sales_order_line_id: str) -> tuple[Warehouse, Decimal] | None:
    """Where the goods of a sales order line left from, and their average unit cost then."""
    moves = db.scalars(select(StockMove).where(StockMove.source_line_id == sales_order_line_id,
                                               StockMove.kind == "delivery")).all()
    if not moves:
        return None
    qty = -sum((m.quantity for m in moves), Decimal(0))
    value = -sum(m.value_minor for m in moves)
    return db.get(Warehouse, moves[0].warehouse_id), Decimal(value) / qty


def create_credit_note(db: Session, invoice: Invoice, *, issue_date: date, reason: str,
                       requests: list[CreditRequest], user_id: str) -> CreditNote:
    if invoice.status not in CREDITABLE:
        raise InvoiceStateError(f"only posted invoices can be credited (status: {invoice.status})")
    if issue_date < invoice.issue_date:
        raise InvoiceValidationError("the credit note cannot be dated before the invoice")
    if not requests:
        raise InvoiceValidationError("name at least one line to credit")
    lines = {l.id: l for l in lines_of(db, invoice)}
    before = credited_so_far(db, invoice)
    seen: set[str] = set()
    credit_lines: list[CreditNoteLine] = []
    returns: list[tuple[InvoiceLine, Decimal]] = []
    for n, req in enumerate(requests, start=1):
        line = lines.get(req.invoice_line_id)
        if line is None:
            raise InvoiceValidationError(f"line {n} is not on invoice {invoice.number}")
        if line.id in seen:
            raise InvoiceValidationError(f"'{line.description}' is listed twice")
        seen.add(line.id)
        qty_before, net_before = before.get(line.id, (Decimal(0), 0))
        open_qty = line.quantity - qty_before
        if req.quantity <= 0 or req.quantity > open_qty:
            raise InvoiceValidationError(f"'{line.description}': can credit at most {open_qty.normalize():f}")
        if req.returned < 0 or req.returned > req.quantity:
            raise InvoiceValidationError(f"'{line.description}': returned quantity must be between 0 and the credited quantity")
        if req.quantity == open_qty:
            net = line.net_minor - net_before  # the exact remainder
        else:
            net = min(line_net_minor(req.quantity, line.unit_price, line.discount_percent, invoice.currency),
                      line.net_minor - net_before)
        if req.returned:
            returns.append((line, req.returned))
        credit_lines.append(CreditNoteLine(
            invoice_line_id=line.id, position=n, product_id=line.product_id, description=line.description,
            quantity=req.quantity, unit_price=line.unit_price, discount_percent=line.discount_percent,
            tax_id=line.tax_id, tax_rate=line.tax_rate, net_minor=net, returned_quantity=req.returned))

    # Tax per rate, with the exact remainder once every line of that rate is fully credited.
    invoice_tax = {(g.tax_id, g.rate): g.tax_minor for g in tax_groups(list(lines.values()), invoice.currency)}
    tax_before = _tax_credited(db, invoice)
    credited_after = {lid: q for lid, (q, _) in before.items()}
    for cl in credit_lines:
        credited_after[cl.invoice_line_id] = credited_after.get(cl.invoice_line_id, Decimal(0)) + cl.quantity
    bases: dict[tuple[str, Decimal], int] = defaultdict(int)
    for cl in credit_lines:
        if cl.tax_id:
            bases[(cl.tax_id, cl.tax_rate)] += cl.net_minor
    taxes: list[CreditNoteTax] = []
    for key, base in sorted(bases.items()):
        remaining = invoice_tax.get(key, 0) - tax_before.get(key, 0)
        group_done = all(credited_after.get(l.id, Decimal(0)) == l.quantity
                         for l in lines.values() if (l.tax_id, l.tax_rate) == key)
        amount = remaining if group_done else min(
            to_minor(from_minor(base, invoice.currency) * key[1] / Decimal(100), invoice.currency), remaining)
        taxes.append(CreditNoteTax(tax_id=key[0], rate=key[1], base_minor=base, tax_minor=max(amount, 0)))

    subtotal = sum(cl.net_minor for cl in credit_lines)
    tax_total = sum(t.tax_minor for t in taxes)
    total = subtotal + tax_total
    if total <= 0:
        raise InvoiceValidationError("the credit note total must be greater than zero")

    number = next_number(db, f"credit_note:{issue_date.year}", f"CN-{issue_date.year}-")
    tax_records = {t.id: t for t in db.scalars(select(Tax).where(Tax.id.in_([t.tax_id for t in taxes])))}
    tax_accounts = {a.id: a for a in db.scalars(
        select(Account).where(Account.id.in_({t.account_id for t in tax_records.values()})))}
    specs = [LineSpec(system_account(db, "sales"), debit_minor=subtotal, label=f"Credit note {number}")]
    for t in taxes:
        record = tax_records[t.tax_id]
        specs.append(LineSpec(tax_accounts[record.account_id], debit_minor=t.tax_minor,
                              label=f"{record.name} on {number}"))
    specs.append(LineSpec(system_account(db, "receivable"), credit_minor=total, customer_id=invoice.customer_id,
                          label=f"Credit note {number} for {invoice.number}"))
    entry = post_entry(db, entry_date=issue_date, currency=invoice.currency, lines=specs,
                       memo=f"Credit note {number} for {invoice.number}", source_type="credit_note",
                       source_id=invoice.id, created_by=user_id)

    applied = min(total, invoice.amount_due_minor)
    note = CreditNote(number=number, invoice_id=invoice.id, customer_id=invoice.customer_id, issue_date=issue_date,
                      currency=invoice.currency, reason=reason, subtotal_minor=subtotal, tax_minor=tax_total,
                      total_minor=total, applied_minor=applied, journal_entry_id=entry.id, created_by=user_id)
    db.add(note)
    db.flush()
    for cl in credit_lines:
        cl.credit_note_id = note.id
    for t in taxes:
        t.credit_note_id = note.id
    db.add_all(credit_lines + taxes)
    invoice.amount_credited_minor += applied
    settle(invoice)
    if returns:
        note.stock_reference = _return_goods(db, invoice, note, returns, user_id=user_id)
    db.flush()
    return note


def _return_goods(db: Session, invoice: Invoice, note: CreditNote, returns: list[tuple[InvoiceLine, Decimal]],
                  *, user_id: str) -> str:
    """Returned units never exceed credited units, which never exceed what was invoiced (and so delivered)."""
    plan: dict[str, list[tuple[Product, Decimal, int, str]]] = defaultdict(list)
    warehouses: dict[str, Warehouse] = {}
    for line, qty in returns:
        product = db.get(Product, line.product_id) if line.product_id else None
        origin = _delivery_cost(db, line.sales_order_line_id) if line.sales_order_line_id else None
        if product is None or product.kind != "goods" or origin is None:
            raise InvoiceValidationError(f"'{line.description}' was not delivered from stock, so it can't be returned to it")
        warehouse, unit_cost = origin
        value = int((unit_cost * qty).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        plan[warehouse.id].append((product, qty, value, line.id))
        warehouses[warehouse.id] = warehouse
    references = []
    for wid, items in plan.items():
        result = stock.return_in(db, warehouse=warehouses[wid], move_date=note.issue_date, currency=invoice.currency,
                                 lines=items, source_type="credit_note", source_id=note.id, user_id=user_id)
        references.append(result.reference)
    return ", ".join(references)[:40]


def refund(db: Session, note: CreditNote, *, amount_minor: int, refund_date: date, method: str, reference: str,
           user_id: str, idempotency_key: str | None = None) -> tuple[Refund, bool]:
    if idempotency_key:
        existing = db.scalar(select(Refund).where(Refund.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.credit_note_id != note.id or existing.amount_minor != amount_minor:
                raise InvoiceStateError("idempotency key already used for a different refund")
            return existing, False
    if method not in PAYMENT_METHODS:
        raise InvoiceValidationError(f"unknown payment method: {method}")
    owed = note.total_minor - note.applied_minor - note.refunded_minor
    if amount_minor <= 0 or amount_minor > owed:
        raise InvoiceValidationError(f"refund must be between 0 and the amount owed {from_minor(owed, note.currency)}")
    if refund_date < note.issue_date:
        raise InvoiceValidationError("the refund cannot be dated before the credit note")
    entry = post_entry(
        db, entry_date=refund_date, currency=note.currency, created_by=user_id,
        memo=f"Refund for {note.number}", source_type="refund", source_id=note.id,
        lines=[LineSpec(system_account(db, "receivable"), debit_minor=amount_minor, customer_id=note.customer_id,
                        label=f"Refund {note.number}"),
               LineSpec(system_account(db, PAYMENT_METHODS[method]), credit_minor=amount_minor,
                        label=f"Refund {note.number} {reference}".strip())],
    )
    record = Refund(credit_note_id=note.id, refund_date=refund_date, amount_minor=amount_minor, method=method,
                    reference=reference, journal_entry_id=entry.id, idempotency_key=idempotency_key,
                    created_by=user_id)
    db.add(record)
    note.refunded_minor += amount_minor
    db.flush()
    return record, True


def lock_credit_note(db: Session, note_id: str) -> CreditNote | None:
    return db.scalar(select(CreditNote).where(CreditNote.id == note_id).with_for_update())
