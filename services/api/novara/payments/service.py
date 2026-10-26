"""Payment links for invoices, and what happens when one is paid.

A link asks for a fixed amount (by default all that is due) in the invoice's currency. When the
provider confirms the money was taken (a signed webhook, see providers.py), the payment is
registered on the invoice like any other, with method "online":

    Dr Online Payments Clearing (1250)     Cr Accounts Receivable

The clearing account holds what the provider owes the organization; when the provider pays out,
an accountant moves it to the bank with a journal entry (and books the provider's fees then).

A published site with the "checkout" scope takes payment for the order it sends (sales/web_orders.py): the link
is for the order itself, and when the money is confirmed the order becomes the customer's invoice, posted and
paid in the same transaction (and e-invoiced, when that is on). Until then nothing is posted.

A credit note on an invoice paid online can be refunded through the same provider (refund_online): back
through the link's payment, at most what that link took less what already went back through it, out of the
clearing account:

    Dr Accounts Receivable     Cr Online Payments Clearing (1250)

The refund is first booked as a trial and undone (so a closed period or an amount the note doesn't owe stops it
before any money moves), then the provider is asked, and only when it agrees is the refund written, with the
provider's id for it; if the provider refuses, nothing is kept.

Confirmations can arrive twice or late: a link is paid at most once (by its id as the payment's
idempotency key). If the invoice can't take the money any more (paid another way meanwhile,
cancelled, or the amount differs), the link becomes "unapplied", nothing is posted, and the person
who made it is told, so they can refund it at the provider or record it by hand.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novara import crypto
from novara.automation.models import Notification
from novara.db import new_id, utcnow
from novara.einvoice.service import EInvoiceError, EInvoiceState
from novara.modules.access.service import holders
from novara.modules.accounting.service import LedgerError, PeriodLocked
from novara.modules.audit import service as audit
from novara.modules.invoicing.credit import refund as refund_note
from novara.modules.invoicing.models import CreditNote, Invoice, Refund
from novara.modules.invoicing.service import InvoiceStateError, InvoiceValidationError, lock_invoice, register_payment
from novara.modules.organizations.models import Organization
from novara.modules.partners.models import Customer
from novara.modules.sales import web_orders
from novara.modules.sales.models import WebOrder
from novara.money import from_minor
from novara.payments.models import PaymentLink, PaymentSettings
from novara.payments.providers import CheckoutRequest, HyperPay, Moyasar, PayTabs, ProviderError, Stripe, TestProvider
from novara.tenancy import current_tenant

LINK_HOURS = 23  # Stripe allows at most 24
ORDER_LINK_HOURS = 2  # a visitor paying for what they just ordered


class PaymentsUnavailable(RuntimeError):
    """Online payments aren't set up, or the invoice can't be paid now (HTTP 409)."""


class PaymentsInvalid(ValueError):
    """The request is invalid (HTTP 422)."""


def test_provider_allowed(settings) -> bool:
    return settings.env != "production"


def settings_row(db: Session, settings) -> PaymentSettings:
    """The organization's payment settings, created (provider "none") with a webhook address on first use."""
    row = db.scalar(select(PaymentSettings))
    if row is None:
        raw = "nvpay_" + secrets.token_urlsafe(32)
        row = PaymentSettings(provider="none", webhook_token=crypto.encrypt(settings, raw, context=current_tenant(db)),
                              webhook_hash=crypto.token_hash(raw))
        db.add(row)
        db.flush()
    return row


def webhook_url(db: Session, settings, row: PaymentSettings, provider: str = "stripe") -> str:
    raw = crypto.decrypt(settings, row.webhook_token, context=current_tenant(db))
    return f"{settings.app_base_url}/api/v1/public/payments/{provider}/{raw}"


def secret(db: Session, settings, value: str | None) -> str | None:
    return crypto.decrypt(settings, value, context=current_tenant(db)) if value else None


def provider(db: Session, settings, row: PaymentSettings, http=None, name: str | None = None):
    name = name or row.provider
    if name == "stripe":
        key = secret(db, settings, row.stripe_secret_key)
        if key is None:
            raise PaymentsUnavailable("Stripe isn't set up: add the secret key in the organization's settings")
        return Stripe(key, http)
    if name == "moyasar":
        key = secret(db, settings, row.moyasar_secret_key)
        if key is None:
            raise PaymentsUnavailable("Moyasar isn't set up: add the secret key in the organization's settings")
        return Moyasar(key, http)
    if name == "hyperpay":
        token = secret(db, settings, row.hyperpay_access_token)
        if token is None or not (row.hyperpay_entity_id or row.hyperpay_mada_entity_id):
            raise PaymentsUnavailable("HyperPay isn't set up: add the access token and an entity id in the organization's settings")
        return HyperPay(token, {"card": row.hyperpay_entity_id, "mada": row.hyperpay_mada_entity_id}, row.hyperpay_live,
                        settings.app_base_url, http)
    if name == "paytabs":
        key = secret(db, settings, row.paytabs_server_key)
        if key is None or not row.paytabs_profile_id:
            raise PaymentsUnavailable("PayTabs isn't set up: add the profile id and the server key in the organization's settings")
        return PayTabs(row.paytabs_profile_id, key, row.paytabs_region, settings.app_base_url, http)
    if name == "test" and test_provider_allowed(settings):
        return TestProvider(settings.app_base_url)
    raise PaymentsUnavailable("online payments aren't set up for this organization")


def effective_status(link: PaymentLink, now: datetime | None = None) -> str:
    expires = link.expires_at if link.expires_at.tzinfo else link.expires_at.replace(tzinfo=timezone.utc)
    if link.status == "open" and expires <= (now or utcnow()):
        return "expired"
    return link.status


def create_link(db: Session, settings, inv: Invoice, *, amount_minor: int | None, user_id: str,
                http=None) -> tuple[PaymentLink, bool]:
    """A link for amount_minor (default: all that is due). An open link for the same amount is reused."""
    row = settings_row(db, settings)
    if row.provider == "none":
        raise PaymentsUnavailable("online payments aren't set up for this organization")
    if inv.status not in ("posted", "partially_paid"):
        raise PaymentsUnavailable(f"only posted invoices with something due can be paid online (status: {inv.status})")
    due = inv.amount_due_minor
    amount = due if amount_minor is None else amount_minor
    if amount <= 0 or amount > due:
        raise PaymentsInvalid(f"the amount must be more than 0 and at most what is due ({from_minor(due, inv.currency)})")
    for link in db.scalars(select(PaymentLink).where(PaymentLink.invoice_id == inv.id, PaymentLink.status == "open",
                                                     PaymentLink.provider == row.provider,
                                                     PaymentLink.amount_minor == amount)):
        if effective_status(link) == "open":
            return link, False
    gateway = provider(db, settings, row, http)
    org = db.get(Organization, inv.organization_id)
    customer = db.get(Customer, inv.customer_id)
    link_id = new_id()
    base = f"{settings.app_base_url}/api/v1/public/payments/done"
    try:
        checkout = gateway.create_checkout(CheckoutRequest(
            link_id=link_id, title=f"{org.name} · {inv.number}"[:250], amount_minor=amount, currency=inv.currency,
            customer_email=customer.email if customer else "", success_url=f"{base}?status=paid",
            cancel_url=f"{base}?status=cancelled", expires_at=utcnow() + timedelta(hours=LINK_HOURS),
            metadata={"invoice_id": inv.id, "invoice_number": inv.number or "", "organization_id": inv.organization_id}))
    except ProviderError as e:
        if e.invalid:
            raise PaymentsInvalid(str(e)) from None
        raise
    link = PaymentLink(id=link_id, invoice_id=inv.id, provider=gateway.name, session_id=checkout.session_id,
                       url=checkout.url, amount_minor=amount, currency=inv.currency, status="open",
                       expires_at=checkout.expires_at, created_by=user_id)
    db.add(link)
    db.flush()
    return link, True


def create_order_link(db: Session, settings, order: WebOrder, http=None) -> PaymentLink:
    """The payment page for an order a site just sent: its whole total, in the organization's currency."""
    row = settings_row(db, settings)
    if row.provider == "none":
        raise PaymentsUnavailable("online payments aren't set up for this shop")
    gateway = provider(db, settings, row, http)
    org = db.get(Organization, order.organization_id)
    link_id = new_id()
    base = f"{settings.app_base_url}/api/v1/public/payments/done"
    try:
        checkout = gateway.create_checkout(CheckoutRequest(
            link_id=link_id, title=f"{org.name} · {order.number}"[:250], amount_minor=order.total_minor,
            currency=order.currency, customer_email=order.email, success_url=f"{base}?status=paid",
            cancel_url=f"{base}?status=cancelled", expires_at=utcnow() + timedelta(hours=ORDER_LINK_HOURS),
            metadata={"web_order_id": order.id, "web_order_number": order.number, "organization_id": order.organization_id}))
    except ProviderError as e:
        if e.invalid:
            raise PaymentsInvalid(str(e)) from None
        raise
    link = PaymentLink(id=link_id, web_order_id=order.id, provider=gateway.name, session_id=checkout.session_id,
                       url=checkout.url, amount_minor=order.total_minor, currency=order.currency, status="open",
                       expires_at=checkout.expires_at)
    db.add(link)
    db.flush()
    return link


def cancel_order_links(db: Session, settings, order: WebOrder, http=None) -> None:
    """The order was taken over or dismissed: its open links close here (and at the provider, if it answers). Money
    that still arrives is not recorded, and sales is told."""
    for link in db.scalars(select(PaymentLink).where(PaymentLink.web_order_id == order.id, PaymentLink.status == "open")
                           .with_for_update()):
        try:
            provider(db, settings, settings_row(db, settings), http, name=link.provider).expire(link.session_id)
        except (ProviderError, PaymentsUnavailable):
            pass
        link.status = "cancelled"


def subject_number(db: Session, link: PaymentLink) -> str:
    """What the customer is paying for: the invoice's number, or the site order's."""
    if link.web_order_id:
        return db.get(WebOrder, link.web_order_id).number
    return db.get(Invoice, link.invoice_id).number or ""


def cancel_link(db: Session, settings, link: PaymentLink, http=None) -> None:
    if effective_status(link) != "open":
        raise PaymentsUnavailable(f"only open links can be cancelled (this one is {effective_status(link)})")
    row = settings_row(db, settings)
    try:
        provider(db, settings, row, http, name=link.provider).expire(link.session_id)
    except ProviderError as e:
        raise PaymentsUnavailable(f"{e} (if the customer has just paid, the payment will show here shortly)") from None
    link.status = "cancelled"


def _say(org: Organization, ar: str, en: str) -> str:
    return ar if (org.default_locale or "ar").startswith("ar") else en


def _tell(db: Session, link: PaymentLink, title: str, body: str) -> None:
    if link.created_by:
        db.add(Notification(user_id=link.created_by, title=title[:300], body=body,
                            idempotency_key=f"paylink:{link.id}:{link.status}"))


def complete(db: Session, link_id: str, *, amount_minor: int, currency: str, reference: str,
             paid_at: datetime, settings=None) -> str:
    """Apply a confirmed payment to the link's invoice: "paid", "unapplied", or "duplicate" if already handled."""
    link = db.scalar(select(PaymentLink).where(PaymentLink.id == link_id).with_for_update())
    if link.status in ("paid", "unapplied"):
        return "duplicate"
    if link.web_order_id:
        return _complete_order(db, link, amount_minor=amount_minor, currency=currency, reference=reference,
                               paid_at=paid_at, settings=settings)
    inv = lock_invoice(db, link.invoice_id)
    org = db.get(Organization, inv.organization_id)
    link.paid_at = paid_at
    link.provider_reference = reference[:255]
    money = f"{from_minor(amount_minor, currency)} {currency}"
    problem = None
    if (amount_minor, currency) != (link.amount_minor, link.currency):
        problem = f"the provider took {money}, but the link asked for {from_minor(link.amount_minor, link.currency)} {link.currency}"
    elif link.status == "cancelled":
        problem = "the link had been cancelled"
    else:
        try:
            with db.begin_nested():
                payment, _ = register_payment(
                    db, inv, amount_minor=amount_minor, payment_date=paid_at.date(), method="online",
                    reference=f"{link.provider} {reference}"[:120], user_id=link.created_by,
                    idempotency_key=f"link:{link.id}")
        except (InvoiceStateError, InvoiceValidationError, LedgerError) as e:
            problem = str(e)
    details = {"number": inv.number, "amount": str(from_minor(amount_minor, currency)), "currency": currency,
               "provider": link.provider, "link_id": link.id, "reference": reference[:255]}
    if problem is None:
        link.status = "paid"
        link.payment_id = payment.id
        audit.record(db, actor_user_id=None, action="invoice.paid_online", target_type="invoice", target_id=inv.id,
                     details=details | {"payment_id": payment.id})
        _tell(db, link, _say(org, f"دُفعت الفاتورة ⁦{inv.number}⁩ إلكترونياً: ⁦{money}⁩",
                             f"Invoice {inv.number} was paid online: {money}"), "")
        if inv.amount_due_minor == 0:
            for other in db.scalars(select(PaymentLink).where(PaymentLink.invoice_id == inv.id,
                                                              PaymentLink.status == "open", PaymentLink.id != link.id)):
                other.status = "cancelled"   # nothing left to pay; the provider's page expires on its own
        return "paid"
    link.status = "unapplied"
    link.note = problem[:300]
    audit.record(db, actor_user_id=None, action="invoice.online_payment_unapplied", target_type="invoice",
                 target_id=inv.id, details=details | {"problem": problem[:300]})
    _tell(db, link, _say(org, f"دفعة إلكترونية لم تُسجَّل على الفاتورة ⁦{inv.number}⁩",
                         f"An online payment for invoice {inv.number} wasn't recorded"),
          _say(org, f"استلم مزوّد الدفع ⁦{money}⁩ لكن لم تُسجَّل: ⁦{problem}⁩. "
                    "ردّ المبلغ من لوحة المزوّد أو سجّله يدوياً.",
               f"The provider took {money}, but it wasn't recorded: {problem}. "
               "Refund it from the provider's dashboard or record it by hand."))
    return "unapplied"


def _tell_sales(db: Session, key: str, title: str, body: str) -> None:
    for uid in holders(db, "sales.write"):
        db.add(Notification(user_id=uid, title=title[:300], body=body, idempotency_key=f"{key}:{uid}"[:120]))


def _complete_order(db: Session, link: PaymentLink, *, amount_minor: int, currency: str, reference: str,
                    paid_at: datetime, settings) -> str:
    """The money for a site's order: the order becomes the customer's invoice, posted and paid; or, if that can't be
    done (the order was taken over meanwhile, a different amount...), the link is "unapplied" and sales is told."""
    order = db.scalar(select(WebOrder).where(WebOrder.id == link.web_order_id).with_for_update())
    org = db.get(Organization, link.organization_id)
    link.paid_at, link.provider_reference = paid_at, reference[:255]
    money = f"{from_minor(amount_minor, currency)} {currency}"
    problem, inv, payment = None, None, None
    if (amount_minor, currency) != (link.amount_minor, link.currency):
        problem = f"the provider took {money}, but the order is {from_minor(link.amount_minor, link.currency)} {link.currency}"
    elif link.status == "cancelled" or order.status != "awaiting_payment":
        problem = f"the order request was {order.status.replace('_', ' ')} meanwhile"
    else:
        try:
            with db.begin_nested():
                inv = web_orders.invoice_paid_order(db, order, paid_at=paid_at, settings=settings)
                payment, _ = register_payment(
                    db, inv, amount_minor=amount_minor, payment_date=inv.issue_date, method="online",
                    reference=f"{link.provider} {reference}"[:120], user_id=None, idempotency_key=f"link:{link.id}")
        except (InvoiceStateError, InvoiceValidationError, LedgerError, PeriodLocked, EInvoiceError, EInvoiceState) as e:
            problem, inv = str(e), None
    details = {"number": order.number, "amount": str(from_minor(amount_minor, currency)), "currency": currency,
               "provider": link.provider, "link_id": link.id, "reference": reference[:255]}
    if problem is None:
        link.status, link.payment_id = "paid", payment.id
        audit.record(db, actor_user_id=None, action="web_order.paid_online", target_type="web_order", target_id=order.id,
                     details=details | {"invoice": inv.number, "payment_id": payment.id})
        _tell_sales(db, f"weborder:{order.id}:paid",
                    _say(org, f"دُفع طلب الموقع ⁦{order.number}⁩: الفاتورة ⁦{inv.number}⁩", f"Site order {order.number} was paid: invoice {inv.number}"),
                    _say(org, f"⁦{money}⁩ من {order.customer_name}. جهّز الطلب وسلّمه.",
                         f"{money} from {order.customer_name}. Prepare the order and deliver it."))
        return "paid"
    link.status, link.note = "unapplied", problem[:300]
    audit.record(db, actor_user_id=None, action="web_order.online_payment_unapplied", target_type="web_order",
                 target_id=order.id, details=details | {"problem": problem[:300]})
    _tell_sales(db, f"weborder:{order.id}:unapplied:{link.id}",
                _say(org, f"دفعة لطلب الموقع ⁦{order.number}⁩ لم تُسجَّل", f"A payment for site order {order.number} wasn't recorded"),
                _say(org, f"استلم مزوّد الدفع ⁦{money}⁩ لكن لم تُسجَّل: ⁦{problem}⁩. ردّ المبلغ من لوحة المزوّد أو سجّله يدوياً.",
                     f"The provider took {money}, but it wasn't recorded: {problem}. Refund it from the provider's dashboard "
                     "or record it by hand."))
    return "unapplied"


def expired(db: Session, link: PaymentLink) -> None:
    if link.status == "open":
        link.status = "expired"


# ---------------------------------------------------------------- refunds through the provider


def refundable_links(db: Session, invoice_id: str) -> list[tuple[PaymentLink, int]]:
    """The invoice's paid links, newest first, with what can still go back through each."""
    links = list(db.scalars(select(PaymentLink).where(PaymentLink.invoice_id == invoice_id, PaymentLink.status == "paid")
                            .order_by(PaymentLink.paid_at.desc())))
    out = []
    for link in links:
        back = db.scalar(select(func.coalesce(func.sum(Refund.amount_minor), 0)).where(Refund.payment_link_id == link.id))
        out.append((link, link.amount_minor - int(back or 0)))
    return out


def refund_online(db: Session, settings, note: CreditNote, *, amount_minor: int, refund_date, user_id: str,
                  idempotency_key: str | None, http=None) -> tuple[Refund, bool]:
    """Pay a credit note back through the provider the invoice was paid with (see the module's docstring)."""
    if idempotency_key:
        existing = db.scalar(select(Refund).where(Refund.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.credit_note_id != note.id or existing.amount_minor != amount_minor:
                raise PaymentsInvalid("idempotency key already used for a different refund")
            return existing, False
    candidates = refundable_links(db, note.invoice_id)
    if not candidates:
        raise PaymentsUnavailable("the invoice wasn't paid online: refund by bank transfer or cash")
    link = next((l for l, room in candidates if room >= amount_minor and l.currency == note.currency), None)
    if link is None:
        most = max(room for _, room in candidates)
        raise PaymentsInvalid(f"at most {from_minor(max(most, 0), note.currency)} {note.currency} can go back through "
                              "one online payment")
    gateway = provider(db, settings, settings_row(db, settings), http, name=link.provider)
    booking = dict(amount_minor=amount_minor, refund_date=refund_date, method="online", user_id=user_id,
                   idempotency_key=idempotency_key, online=True, payment_link_id=link.id)
    # Booked once as a trial (the amount the note owes, the date, closed periods...), undone, so the provider is only
    # asked for a refund the books will take; refunds are never changed once written.
    trial = db.begin_nested()
    try:
        refund_note(db, note, reference="", **booking)
    finally:
        trial.rollback()
    reference = gateway.refund(link.provider_reference, amount_minor, link.currency,
                               key=idempotency_key or secrets.token_hex(12), detail=link.provider_detail)
    record, _ = refund_note(db, note, reference=f"{link.provider} {reference}"[:120], provider_reference=reference,
                            **booking)
    audit.record(db, actor_user_id=user_id, action="credit_note.refunded_online", target_type="credit_note",
                 target_id=note.id, details={"number": note.number, "amount": str(from_minor(amount_minor, note.currency)),
                                             "currency": note.currency, "provider": link.provider, "reference": reference})
    return record, True
