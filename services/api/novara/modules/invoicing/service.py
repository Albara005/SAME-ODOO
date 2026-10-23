"""Invoice lifecycle: draft -> posted -> (partially_paid ->) paid, or posted -> cancelled.

Totals: each line's net amount (qty x price - discount) is rounded to the
currency's minor unit; tax is then computed per tax rate on the sum of the
line nets of that rate and rounded once per rate. This avoids the cent drift
of rounding tax on every line. The policy must be confirmed by an accountant
for each market before real-world use.

Posting writes one balanced journal entry:
    Dr Accounts Receivable        total
        Cr Sales Revenue              subtotal
        Cr Output VAT (per tax)       tax
Payments:  Dr Bank/Cash, Cr Accounts Receivable.
Cancelling a posted, unpaid invoice posts the exact reversal of its entry.
Credit notes (``credit.py``) reduce what is due without touching the invoice itself.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from novara.db import utcnow
from novara.modules.accounting.models import Account, JournalEntry, Tax
from novara.modules.accounting.service import LineSpec, next_number, post_entry, reverse_entry, system_account
from novara.modules.invoicing.models import Invoice, InvoiceLine, Payment
from novara.money import from_minor, to_minor

PAYMENT_METHODS = {"bank": "bank", "cash": "cash"}  # method -> system account key


class InvoiceStateError(RuntimeError):
    """The invoice is not in a state that allows the operation (HTTP 409)."""


class InvoiceValidationError(ValueError):
    """The request is invalid for this invoice (HTTP 422)."""


@dataclass(frozen=True)
class TaxGroup:
    tax_id: str
    rate: Decimal
    base_minor: int
    tax_minor: int


def line_net_minor(quantity: Decimal, unit_price: Decimal, discount_percent: Decimal, currency: str) -> int:
    gross = quantity * unit_price
    return to_minor(gross - gross * discount_percent / Decimal(100), currency)


def tax_groups(lines: list[InvoiceLine], currency: str) -> list[TaxGroup]:
    bases: dict[tuple[str, Decimal], int] = defaultdict(int)
    for line in lines:
        if line.tax_id:
            bases[(line.tax_id, line.tax_rate)] += line.net_minor
    return [
        TaxGroup(tax_id, rate, base, to_minor(from_minor(base, currency) * rate / Decimal(100), currency))
        for (tax_id, rate), base in sorted(bases.items())
    ]


def recompute(invoice: Invoice, lines: list[InvoiceLine]) -> list[TaxGroup]:
    for line in lines:
        line.net_minor = line_net_minor(line.quantity, line.unit_price, line.discount_percent, invoice.currency)
    groups = tax_groups(lines, invoice.currency)
    invoice.subtotal_minor = sum(l.net_minor for l in lines)
    invoice.tax_minor = sum(g.tax_minor for g in groups)
    invoice.total_minor = invoice.subtotal_minor + invoice.tax_minor
    return groups


def lines_of(db: Session, invoice: Invoice) -> list[InvoiceLine]:
    return list(db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.position)))


def lock_invoice(db: Session, invoice_id: str) -> Invoice | None:
    """Load the invoice with a row lock so concurrent post/pay/cancel requests serialize."""
    return db.scalar(select(Invoice).where(Invoice.id == invoice_id).with_for_update())


def post_invoice(db: Session, invoice: Invoice, *, user_id: str) -> JournalEntry:
    if invoice.status != "draft":
        raise InvoiceStateError(f"only draft invoices can be posted (status: {invoice.status})")
    lines = lines_of(db, invoice)
    if not lines:
        raise InvoiceValidationError("an invoice needs at least one line")
    groups = recompute(invoice, lines)
    if invoice.total_minor <= 0:
        raise InvoiceValidationError("invoice total must be greater than zero")

    year = invoice.issue_date.year
    invoice.number = next_number(db, f"invoice:{year}", f"INV-{year}-")
    taxes = {t.id: t for t in db.scalars(select(Tax).where(Tax.id.in_([g.tax_id for g in groups])))}
    tax_accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_({t.account_id for t in taxes.values()})))}

    specs = [
        LineSpec(system_account(db, "receivable"), debit_minor=invoice.total_minor,
                 customer_id=invoice.customer_id, label=f"Invoice {invoice.number}"),
        LineSpec(system_account(db, "sales"), credit_minor=invoice.subtotal_minor, label=f"Sales {invoice.number}"),
    ]
    for g in groups:
        tax = taxes[g.tax_id]
        specs.append(LineSpec(tax_accounts[tax.account_id], credit_minor=g.tax_minor,
                              label=f"{tax.name} on {invoice.number}"))
    entry = post_entry(
        db, entry_date=invoice.issue_date, currency=invoice.currency, lines=specs,
        memo=f"Invoice {invoice.number}", source_type="invoice", source_id=invoice.id, created_by=user_id,
    )
    invoice.status = "posted"
    invoice.journal_entry_id = entry.id
    invoice.posted_at = utcnow()
    invoice.posted_by = user_id
    return entry


def register_payment(
    db: Session,
    invoice: Invoice,
    *,
    amount_minor: int,
    payment_date: date,
    method: str,
    reference: str,
    user_id: str,
    idempotency_key: str | None = None,
) -> tuple[Payment, bool]:
    """Record a payment. Returns (payment, created). A repeated idempotency key returns the original payment."""
    if idempotency_key:
        existing = db.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.invoice_id != invoice.id or existing.amount_minor != amount_minor:
                raise InvoiceStateError("idempotency key already used for a different payment")
            return existing, False
    if invoice.status not in ("posted", "partially_paid"):
        raise InvoiceStateError(f"invoice cannot receive payments (status: {invoice.status})")
    if method not in PAYMENT_METHODS:
        raise InvoiceValidationError(f"unknown payment method: {method}")
    residual = invoice.amount_due_minor
    if amount_minor <= 0 or amount_minor > residual:
        raise InvoiceValidationError(
            f"payment must be between 0 and the open amount {from_minor(residual, invoice.currency)}"
        )
    if payment_date < invoice.issue_date:
        raise InvoiceValidationError("payment date cannot be before the invoice date")

    entry = post_entry(
        db, entry_date=payment_date, currency=invoice.currency, created_by=user_id,
        memo=f"Payment for {invoice.number}", source_type="payment", source_id=invoice.id,
        lines=[
            LineSpec(system_account(db, PAYMENT_METHODS[method]), debit_minor=amount_minor,
                     label=f"Payment {invoice.number} {reference}".strip()),
            LineSpec(system_account(db, "receivable"), credit_minor=amount_minor,
                     customer_id=invoice.customer_id, label=f"Payment {invoice.number}"),
        ],
    )
    payment = Payment(
        invoice_id=invoice.id, payment_date=payment_date, amount_minor=amount_minor, method=method,
        reference=reference, journal_entry_id=entry.id, idempotency_key=idempotency_key, created_by=user_id,
    )
    db.add(payment)
    invoice.amount_paid_minor += amount_minor
    settle(invoice)
    db.flush()
    return payment, True


def settle(invoice: Invoice) -> None:
    """Status after a payment or a credit: paid/credited when nothing is due, else (partially) paid."""
    if invoice.amount_due_minor == 0:
        invoice.status = "paid" if invoice.amount_paid_minor > 0 else "credited"
    else:
        invoice.status = "partially_paid" if invoice.amount_paid_minor > 0 else "posted"


def cancel_invoice(db: Session, invoice: Invoice, *, user_id: str, cancel_date: date) -> JournalEntry:
    if invoice.status != "posted":
        raise InvoiceStateError(
            "only posted invoices without payments can be cancelled" if invoice.status != "draft"
            else "draft invoices are deleted, not cancelled"
        )
    if invoice.amount_credited_minor:
        raise InvoiceStateError("invoices with credit notes cannot be cancelled; credit the rest instead")
    if cancel_date < invoice.issue_date:
        raise InvoiceValidationError("cancellation date cannot be before the invoice date")
    original = db.get(JournalEntry, invoice.journal_entry_id)
    reversal = reverse_entry(db, original, entry_date=cancel_date, memo=f"Cancellation of {invoice.number}",
                             created_by=user_id)
    invoice.status = "cancelled"
    invoice.reversal_entry_id = reversal.id
    invoice.cancelled_at = utcnow()
    return reversal


class InvoiceLocked(RuntimeError):
    pass


# Fields that may still change after posting (payment and cancellation bookkeeping).
_MUTABLE_AFTER_POST = {"status", "amount_paid_minor", "amount_credited_minor", "reversal_entry_id", "cancelled_at",
                       "number", "journal_entry_id", "posted_at", "posted_by"}


@event.listens_for(Session, "before_flush")
def _posted_invoices_are_final(session: Session, _ctx, _instances) -> None:
    for obj in session.dirty:
        if isinstance(obj, Invoice) and session.is_modified(obj):
            state = inspect(obj)
            was_status = state.attrs.status.history.deleted
            previous = was_status[0] if was_status else obj.status
            if previous == "draft":
                continue
            changed = {a.key for a in state.attrs if a.history.has_changes()}
            if changed - _MUTABLE_AFTER_POST:
                raise InvoiceLocked(f"posted invoices cannot be edited ({sorted(changed - _MUTABLE_AFTER_POST)})")
    touched = [o for o in list(session.new) + list(session.dirty) + list(session.deleted) if isinstance(o, InvoiceLine)]
    with session.no_autoflush:
        for line in touched:
            if line not in session.new and line not in session.deleted and not session.is_modified(line):
                continue
            invoice = session.get(Invoice, line.invoice_id)
            if invoice is not None and invoice not in session.deleted:
                status_hist = inspect(invoice).attrs.status.history
                original = status_hist.deleted[0] if status_hist.deleted else invoice.status
                if original != "draft":
                    raise InvoiceLocked("lines of a posted invoice cannot be changed")
