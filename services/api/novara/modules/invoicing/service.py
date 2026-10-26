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
Payments:  Dr Bank/Cash, Cr Accounts Receivable. A payment through an online payment link
(novara/payments) goes to Online Payments Clearing instead, until the provider pays it out.
Cancelling a posted, unpaid invoice posts the exact reversal of its entry.
Credit notes (``credit.py``) reduce what is due without touching the invoice itself.

An invoice can be in another currency than the organization's, at an exchange rate. The ledger stays in
the organization's currency: posting converts each amount at the invoice's rate (base_* fields). A payment
is converted at its own date's rate; the receivable clears at the invoice's rate (the last payment clears
exactly what is left), and the difference is an exchange gain or loss:
    Dr Bank (at the payment's rate)   Cr Receivable (at the invoice's rate)   Dr/Cr Exchange Gains and Losses
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
from novara.modules.organizations.models import Organization
from novara.money import convert, from_minor, to_minor
from novara.tenancy import current_tenant

PAYMENT_METHODS = {"bank": "bank", "cash": "cash"}  # method -> system account key
# Customers can also pay online; only a payment link records those (refunds and vendor payments can't).
RECEIPT_METHODS = PAYMENT_METHODS | {"online": "online_clearing"}


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


def base_currency(db: Session) -> str:
    """The organization's currency: the one its books are kept in."""
    return db.get(Organization, current_tenant(db)).default_currency


def booked_rate(db: Session, invoice: Invoice) -> Decimal:
    return invoice.exchange_rate if invoice.currency != base_currency(db) else Decimal(1)


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

    base = base_currency(db)
    if invoice.currency != base and invoice.exchange_rate <= 0:
        raise InvoiceValidationError(f"an invoice in {invoice.currency} needs its exchange rate to {base}")
    rate = booked_rate(db, invoice)
    year = invoice.issue_date.year
    if invoice.debit_note_of_id:
        original = db.get(Invoice, invoice.debit_note_of_id)
        if original.status in ("draft", "cancelled"):
            raise InvoiceStateError(f"the invoice this debit note adjusts is {original.status}")
        if not invoice.reason.strip():
            raise InvoiceValidationError("a debit note needs its reason")
        invoice.number = next_number(db, f"debit_note:{year}", f"DN-{year}-")
    else:
        invoice.number = next_number(db, f"invoice:{year}", f"INV-{year}-")
    kind = "Debit note" if invoice.debit_note_of_id else "Invoice"
    taxes = {t.id: t for t in db.scalars(select(Tax).where(Tax.id.in_([g.tax_id for g in groups])))}
    tax_accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_({t.account_id for t in taxes.values()})))}

    to_base = lambda minor: convert(minor, invoice.currency, rate, base)  # noqa: E731
    invoice.base_subtotal_minor = to_base(invoice.subtotal_minor)
    base_taxes = [(g, to_base(g.tax_minor)) for g in groups]
    invoice.base_total_minor = invoice.base_subtotal_minor + sum(t for _, t in base_taxes)
    invoice.base_settled_minor = 0
    note = (f" ({invoice.currency} {from_minor(invoice.total_minor, invoice.currency)} at {rate.normalize():f})"
            if invoice.currency != base else "")
    specs = [
        LineSpec(system_account(db, "receivable"), debit_minor=invoice.base_total_minor,
                 customer_id=invoice.customer_id, label=f"{kind} {invoice.number}{note}"),
        LineSpec(system_account(db, "sales"), credit_minor=invoice.base_subtotal_minor, label=f"Sales {invoice.number}"),
    ]
    for g, base_tax in base_taxes:
        tax = taxes[g.tax_id]
        specs.append(LineSpec(tax_accounts[tax.account_id], credit_minor=base_tax,
                              label=f"{tax.name} on {invoice.number}"))
    entry = post_entry(
        db, entry_date=invoice.issue_date, currency=base, lines=specs,
        memo=f"{kind} {invoice.number}{note}", source_type="invoice", source_id=invoice.id, created_by=user_id,
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
    exchange_rate: Decimal | None = None,
) -> tuple[Payment, bool]:
    """Record a payment. Returns (payment, created). A repeated idempotency key returns the original payment.

    For an invoice in another currency, exchange_rate is the rate on the payment date (required)."""
    if idempotency_key:
        existing = db.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.invoice_id != invoice.id or existing.amount_minor != amount_minor:
                raise InvoiceStateError("idempotency key already used for a different payment")
            return existing, False
    if invoice.status not in ("posted", "partially_paid"):
        raise InvoiceStateError(f"invoice cannot receive payments (status: {invoice.status})")
    if method not in RECEIPT_METHODS:
        raise InvoiceValidationError(f"unknown payment method: {method}")
    residual = invoice.amount_due_minor
    if amount_minor <= 0 or amount_minor > residual:
        raise InvoiceValidationError(
            f"payment must be between 0 and the open amount {from_minor(residual, invoice.currency)}"
        )
    if payment_date < invoice.issue_date:
        raise InvoiceValidationError("payment date cannot be before the invoice date")
    base = base_currency(db)
    foreign = invoice.currency != base
    if foreign and (exchange_rate is None or exchange_rate <= 0):
        raise InvoiceValidationError(f"a payment in {invoice.currency} needs the exchange rate to {base} on the payment date")
    rate = exchange_rate if foreign else Decimal(1)
    received = convert(amount_minor, invoice.currency, rate, base)
    cleared = (invoice.base_due_minor if amount_minor == residual
               else min(convert(amount_minor, invoice.currency, booked_rate(db, invoice), base), invoice.base_due_minor))
    fx = received - cleared
    lines = [
        LineSpec(system_account(db, RECEIPT_METHODS[method]), debit_minor=received,
                 label=f"Payment {invoice.number} {reference}".strip()),
        LineSpec(system_account(db, "receivable"), credit_minor=cleared,
                 customer_id=invoice.customer_id, label=f"Payment {invoice.number}"),
    ]
    if fx:
        lines.append(LineSpec(system_account(db, "fx_gain_loss"), credit_minor=max(fx, 0), debit_minor=max(-fx, 0),
                              label=f"Exchange {'gain' if fx > 0 else 'loss'} on {invoice.number}"))
    entry = post_entry(
        db, entry_date=payment_date, currency=base, created_by=user_id,
        memo=f"Payment for {invoice.number}", source_type="payment", source_id=invoice.id, lines=lines,
    )
    payment = Payment(
        invoice_id=invoice.id, payment_date=payment_date, amount_minor=amount_minor, method=method,
        reference=reference, journal_entry_id=entry.id, idempotency_key=idempotency_key, created_by=user_id,
        exchange_rate=rate, base_amount_minor=received, fx_minor=fx,
    )
    db.add(payment)
    invoice.base_settled_minor += cleared
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
                       "number", "journal_entry_id", "posted_at", "posted_by", "base_settled_minor"}


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
