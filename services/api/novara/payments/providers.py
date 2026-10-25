"""Payment providers: open a checkout page for an amount, and read back what the provider says happened.

Stripe Checkout (https://docs.stripe.com/api/checkout/sessions): the API is called with the
organization's secret key; Stripe then calls the organization's webhook address with
``checkout.session.completed`` (or ``...async_payment_succeeded``) once the money is taken, signed as

    Stripe-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>" with the endpoint's whsec_ secret>

A link is only ever marked paid from that signed call, never from the customer's browser coming
back to the success page (anyone can open that).

Moyasar (https://docs.moyasar.com/api/invoices), the common gateway in Saudi Arabia: an invoice
gives a hosted page that takes mada, Apple Pay, STC Pay and cards. Its webhook calls carry the secret
token the organization set in Moyasar's dashboard; even then the call is only a hint: the invoice is
read back from Moyasar's API with the organization's secret key, and that answer decides.

The test provider is the same flow without a real provider, for development and tests: its
checkout page is served by NOVARA itself and refused in production.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

STRIPE_API = "https://api.stripe.com/v1"
MOYASAR_API = "https://api.moyasar.com/v1"
MOYASAR_CURRENCIES = {"SAR"}  # mada, Apple Pay and STC Pay settle in riyals
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
SIGNATURE_TOLERANCE = 300  # seconds either side of now, as Stripe's own libraries allow
# Stripe takes these in the same minor unit as ISO 4217. Its three-decimal currencies must end in 0.
STRIPE_CURRENCIES = {"SAR", "AED", "QAR", "EGP", "MAD", "USD", "EUR", "GBP", "TRY", "JPY",
                     "KWD", "BHD", "OMR", "JOD", "TND"}
STRIPE_THREE_DECIMAL = {"KWD", "BHD", "OMR", "JOD", "TND"}


class ProviderError(RuntimeError):
    """The provider refused or didn't answer (HTTP 502), or can't take this amount (HTTP 422 via .invalid)."""

    def __init__(self, message: str, *, invalid: bool = False):
        super().__init__(message)
        self.invalid = invalid


class BadSignature(ValueError):
    pass


@dataclass(frozen=True)
class Checkout:
    session_id: str
    url: str
    expires_at: datetime


@dataclass(frozen=True)
class Paid:
    session_id: str
    amount_minor: int
    currency: str
    reference: str
    paid_at: datetime


@dataclass(frozen=True)
class Expired:
    session_id: str


@dataclass(frozen=True)
class CheckoutRequest:
    link_id: str
    title: str
    amount_minor: int
    currency: str
    customer_email: str
    success_url: str
    cancel_url: str
    expires_at: datetime
    metadata: dict[str, str]


class Stripe:
    name = "stripe"

    def __init__(self, secret_key: str, http: httpx.Client | None = None):
        self._key = secret_key
        self._http = http

    def _post(self, path: str, data: dict[str, str], *, idempotency_key: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._key}", "Stripe-Version": "2024-06-20"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        client = self._http or httpx.Client(timeout=TIMEOUT)
        try:
            r = client.post(f"{STRIPE_API}{path}", data=data, headers=headers)
        except httpx.HTTPError as e:
            raise ProviderError(f"Stripe didn't answer ({type(e).__name__}); try again") from None
        finally:
            if self._http is None:
                client.close()
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400:
            message = (body.get("error") or {}).get("message") or f"HTTP {r.status_code}"
            raise ProviderError(f"Stripe refused: {message}"[:300], invalid=400 <= r.status_code < 500 and r.status_code != 401)
        return body

    def create_checkout(self, req: CheckoutRequest) -> Checkout:
        if req.currency not in STRIPE_CURRENCIES:
            raise ProviderError(f"Stripe can't charge in {req.currency}", invalid=True)
        if req.currency in STRIPE_THREE_DECIMAL and req.amount_minor % 10:
            raise ProviderError(f"Stripe only charges {req.currency} in steps of 0.010", invalid=True)
        data = {
            "mode": "payment",
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": req.currency.lower(),
            "line_items[0][price_data][unit_amount]": str(req.amount_minor),
            "line_items[0][price_data][product_data][name]": req.title,
            "success_url": req.success_url,
            "cancel_url": req.cancel_url,
            "client_reference_id": req.link_id,
            "expires_at": str(int(req.expires_at.timestamp())),
        }
        if req.customer_email:
            data["customer_email"] = req.customer_email
        for k, v in (req.metadata | {"link_id": req.link_id}).items():
            data[f"metadata[{k}]"] = v
            data[f"payment_intent_data[metadata][{k}]"] = v
        body = self._post("/checkout/sessions", data, idempotency_key=f"novara-link-{req.link_id}")
        if not body.get("id") or not body.get("url"):
            raise ProviderError("Stripe's answer had no checkout address")
        expires = datetime.fromtimestamp(int(body.get("expires_at") or req.expires_at.timestamp()), timezone.utc)
        return Checkout(session_id=str(body["id"]), url=str(body["url"]), expires_at=expires)

    def expire(self, session_id: str) -> None:
        self._post(f"/checkout/sessions/{session_id}/expire", {})


def verify_stripe(secret: str, body: bytes, header: str, now: float | None = None) -> dict:
    """The event in a webhook call, if its Stripe-Signature is valid and recent; BadSignature otherwise."""
    stamp, signatures = "", []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            stamp = value
        elif key == "v1":
            signatures.append(value)
    if not stamp.isdigit() or not signatures:
        raise BadSignature("missing or malformed Stripe-Signature")
    if abs((time.time() if now is None else now) - int(stamp)) > SIGNATURE_TOLERANCE:
        raise BadSignature("the signature is too old")
    want = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(want, s) for s in signatures):
        raise BadSignature("the signature doesn't match")
    try:
        event = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise BadSignature("the body isn't JSON") from None
    if not isinstance(event, dict):
        raise BadSignature("the body isn't an event")
    return event


def sign_stripe(secret: str, body: bytes, now: int | None = None) -> str:
    """A Stripe-Signature header for body (what Stripe sends; used by tests and the development tools)."""
    stamp = int(time.time()) if now is None else now
    return f"t={stamp},v1=" + hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()


def stripe_event(event: dict) -> Paid | Expired | None:
    """What a Stripe event means for a payment link (None: nothing to do)."""
    kind = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}
    if not isinstance(obj, dict) or obj.get("object") != "checkout.session" or not obj.get("id"):
        return None
    if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        if obj.get("payment_status") != "paid":
            return None  # e.g. a bank debit still on its way: async_payment_succeeded follows
        try:
            amount = int(obj["amount_total"])
            currency = str(obj["currency"]).upper()
        except (KeyError, TypeError, ValueError):
            return None
        created = event.get("created")
        paid_at = datetime.fromtimestamp(created, timezone.utc) if isinstance(created, int) else datetime.now(timezone.utc)
        return Paid(session_id=str(obj["id"]), amount_minor=amount, currency=currency,
                    reference=str(obj.get("payment_intent") or obj["id"]), paid_at=paid_at)
    if kind == "checkout.session.expired":
        return Expired(session_id=str(obj["id"]))
    return None


class Moyasar:
    name = "moyasar"

    def __init__(self, secret_key: str, http: httpx.Client | None = None):
        self._key = secret_key
        self._http = http

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        client = self._http or httpx.Client(timeout=TIMEOUT)
        try:
            r = client.request(method, f"{MOYASAR_API}{path}", json=body, auth=(self._key, ""))
        except httpx.HTTPError as e:
            raise ProviderError(f"Moyasar didn't answer ({type(e).__name__}); try again") from None
        finally:
            if self._http is None:
                client.close()
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            message = data.get("message") or f"HTTP {r.status_code}"
            if isinstance(data.get("errors"), dict):
                message += ": " + "; ".join(f"{k} {', '.join(map(str, v)) if isinstance(v, list) else v}"
                                            for k, v in data["errors"].items())
            raise ProviderError(f"Moyasar refused: {message}"[:300], invalid=400 <= r.status_code < 500 and r.status_code != 401)
        return data if isinstance(data, dict) else {}

    def create_checkout(self, req: CheckoutRequest) -> Checkout:
        if req.currency not in MOYASAR_CURRENCIES:
            raise ProviderError(f"Moyasar can't charge in {req.currency}", invalid=True)
        if req.amount_minor < 100:
            raise ProviderError("Moyasar's smallest payment is 1.00 SAR", invalid=True)
        body = self._call("POST", "/invoices", {
            "amount": req.amount_minor, "currency": req.currency, "description": req.title,
            "success_url": req.success_url, "back_url": req.cancel_url,
            "expired_at": req.expires_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "metadata": req.metadata | {"link_id": req.link_id},
        })
        if not body.get("id") or not body.get("url"):
            raise ProviderError("Moyasar's answer had no invoice address")
        return Checkout(session_id=str(body["id"]), url=str(body["url"]), expires_at=req.expires_at)

    def expire(self, session_id: str) -> None:
        self._call("PUT", f"/invoices/{session_id}/cancel")

    def fetch(self, session_id: str) -> Paid | Expired | None:
        """What Moyasar itself says about the invoice (None: not paid, nor over, yet)."""
        inv = self._call("GET", f"/invoices/{session_id}")
        state = inv.get("status")
        if state == "paid":
            try:
                amount, currency = int(inv["amount"]), str(inv["currency"]).upper()
            except (KeyError, TypeError, ValueError):
                raise ProviderError("Moyasar's answer had no amount") from None
            paid = [p for p in inv.get("payments") or [] if isinstance(p, dict) and p.get("status") == "paid"]
            reference = str(paid[-1].get("id")) if paid else str(inv.get("id") or session_id)
            return Paid(session_id=session_id, amount_minor=amount, currency=currency, reference=reference,
                        paid_at=_moyasar_time(inv.get("updated_at")))
        if state in ("expired", "canceled", "cancelled"):
            return Expired(session_id=session_id)
        return None


def _moyasar_time(value) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def moyasar_invoice(event: dict) -> str | None:
    """The Moyasar invoice a webhook call is about (a payment names its invoice; an invoice event is the invoice)."""
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    if data.get("invoice_id"):
        return str(data["invoice_id"])
    if str(event.get("type", "")).startswith("invoice") and data.get("id"):
        return str(data["id"])
    return None


class TestProvider:
    """Checkout pages served by NOVARA itself (development and tests only)."""

    name = "test"

    def __init__(self, base_url: str):
        self._base = base_url

    def create_checkout(self, req: CheckoutRequest) -> Checkout:
        session = "cs_novaratest_" + secrets.token_urlsafe(24)
        return Checkout(session_id=session, url=f"{self._base}/api/v1/public/payments/test/{session}",
                        expires_at=req.expires_at)

    def expire(self, session_id: str) -> None:
        return None
