"""Online payments: the organization's provider settings, payment links on invoices, and the provider's calls.

    GET/PUT /orgs/{org}/payments/settings               provider and secrets (org.manage; secrets are write-only)
    GET     /orgs/{org}/payments/available              which provider is on (anyone who can read invoices)
    POST    /orgs/{org}/invoices/{id}/payment-links     a checkout link for what is due (payments.write)
    GET     /orgs/{org}/invoices/{id}/payment-links
    POST    /orgs/{org}/invoices/{id}/payment-links/{link}/cancel

    POST /api/v1/public/payments/stripe/{token}         Stripe's signed webhook calls
    POST /api/v1/public/payments/moyasar/{token}        Moyasar's webhook calls (checked against Moyasar's API)
    GET  /api/v1/public/payments/hyperpay/{session}     the customer's page: HyperPay's widget for a checkout made now
    GET  /api/v1/public/payments/hyperpay/{session}/result   where HyperPay sends the customer back (checked with HyperPay)
    POST /api/v1/public/payments/hyperpay/{token}       HyperPay's encrypted webhook calls (checked the same way)
    GET  /api/v1/public/payments/paytabs/{session}      the customer's link: a PayTabs payment page made now
    POST /api/v1/public/payments/paytabs/{session}/return    PayTabs' signed return (checked with PayTabs)
    POST /api/v1/public/payments/paytabs/{token}        PayTabs' signed callback (checked the same way)
    GET  /api/v1/public/payments/test/{session}         the test provider's checkout page (not in production)
    POST /api/v1/public/payments/test/{session}/pay
    GET  /api/v1/public/payments/done?status=paid       where the customer lands afterwards
"""

from __future__ import annotations

import hmac
import html
import json
from datetime import datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from novara import crypto
from novara.db import utcnow
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit
from novara.modules.invoicing.models import Invoice
from novara.modules.invoicing.service import lock_invoice
from novara.modules.organizations.models import Organization
from novara.modules.partners.models import Customer
from novara.modules.sales.models import WebOrder
from novara.money import from_minor, to_minor
from novara.payments import service as svc
from novara.payments.models import PaymentLink, PaymentSettings
from novara.payments.providers import (BadSignature, Expired, HyperPay, HyperPayResult, Moyasar, Paid, PayTabs,
                                       PayTabsResult, ProviderError, decrypt_hyperpay, moyasar_invoice, stripe_event,
                                       verify_paytabs_callback, verify_paytabs_return, verify_stripe)
from novara.tenancy import bind_tenant, current_tenant, system_scope

MAX_BYTES = 256 * 1024
STRIPE_KEY = r"^(sk|rk)_(test|live)_[A-Za-z0-9]{10,200}$"
STRIPE_WEBHOOK_SECRET = r"^whsec_[A-Za-z0-9+/=_-]{10,200}$"
MOYASAR_KEY = r"^sk_(test|live)_[A-Za-z0-9]{20,200}$"
HYPERPAY_TOKEN = r"^[A-Za-z0-9+/=_-]{20,300}$"
HYPERPAY_ENTITY = r"^([A-Za-z0-9]{16,64})?$"
PAYTABS_PROFILE = r"^[0-9]{1,12}$"
PAYTABS_KEY = r"^[A-Za-z0-9-]{20,100}$"
PayTabsRegion = Literal["sa", "ae", "eg", "om", "jo", "kw", "iq", "ma", "qa", "global"]
PAGE_HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                                           "frame-ancestors 'none'; base-uri 'none'",
                "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}

router = APIRouter(tags=["online payments"])
public_router = APIRouter(tags=["online payments"])


Provider = Literal["none", "stripe", "moyasar", "hyperpay", "paytabs", "test"]


class PaymentSettingsOut(BaseModel):
    provider: Provider
    stripe_key_hint: str
    has_stripe_key: bool
    has_webhook_secret: bool
    webhook_url: str
    moyasar_key_hint: str
    has_moyasar_key: bool
    has_moyasar_webhook_secret: bool
    moyasar_webhook_url: str
    hyperpay_token_hint: str
    has_hyperpay_token: bool
    hyperpay_entity_id: str
    hyperpay_mada_entity_id: str
    hyperpay_live: bool
    has_hyperpay_webhook_key: bool
    hyperpay_webhook_url: str
    paytabs_profile_id: str
    paytabs_key_hint: str
    has_paytabs_key: bool
    paytabs_region: str
    paytabs_callback_url: str           # sent with each payment page: nothing to set in PayTabs' dashboard
    test_provider_available: bool


class PaymentSettingsIn(BaseModel):
    provider: Provider
    stripe_secret_key: str | None = Field(default=None, pattern=STRIPE_KEY)
    stripe_webhook_secret: str | None = Field(default=None, pattern=STRIPE_WEBHOOK_SECRET)
    moyasar_secret_key: str | None = Field(default=None, pattern=MOYASAR_KEY)
    # The secret token typed in when adding the webhook in Moyasar's dashboard.
    moyasar_webhook_secret: str | None = Field(default=None, min_length=16, max_length=200, pattern=r"^\S+$")
    hyperpay_access_token: str | None = Field(default=None, pattern=HYPERPAY_TOKEN)
    # Entity ids aren't secret: given, they replace the stored ones ("" clears one).
    hyperpay_entity_id: str | None = Field(default=None, pattern=HYPERPAY_ENTITY)
    hyperpay_mada_entity_id: str | None = Field(default=None, pattern=HYPERPAY_ENTITY)
    hyperpay_live: bool | None = None
    # The webhook's decryption key from HyperPay's dashboard (64 hex characters).
    hyperpay_webhook_key: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{64}$")
    paytabs_profile_id: str | None = Field(default=None, pattern=PAYTABS_PROFILE)
    paytabs_server_key: str | None = Field(default=None, pattern=PAYTABS_KEY)
    paytabs_region: PayTabsRegion | None = None


class PaymentProviderOut(BaseModel):
    provider: Provider


class PaymentLinkIn(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=3)


class PaymentLinkOut(BaseModel):
    id: str
    invoice_id: str | None
    web_order_id: str | None = None         # a published site's order (paid on the site)
    provider: str
    url: str
    amount: str
    currency: str
    status: Literal["open", "paid", "unapplied", "expired", "cancelled"]
    expires_at: datetime
    paid_at: datetime | None
    payment_id: str | None
    note: str
    created_at: datetime


def _settings_out(db: Session, settings, row: PaymentSettings) -> PaymentSettingsOut:
    return PaymentSettingsOut(provider=row.provider, stripe_key_hint=row.stripe_key_hint,
                       has_stripe_key=row.stripe_secret_key is not None,
                       has_webhook_secret=row.stripe_webhook_secret is not None,
                       webhook_url=svc.webhook_url(db, settings, row),
                       moyasar_key_hint=row.moyasar_key_hint, has_moyasar_key=row.moyasar_secret_key is not None,
                       has_moyasar_webhook_secret=row.moyasar_webhook_secret is not None,
                       moyasar_webhook_url=svc.webhook_url(db, settings, row, "moyasar"),
                       hyperpay_token_hint=row.hyperpay_token_hint, has_hyperpay_token=row.hyperpay_access_token is not None,
                       hyperpay_entity_id=row.hyperpay_entity_id, hyperpay_mada_entity_id=row.hyperpay_mada_entity_id,
                       hyperpay_live=row.hyperpay_live, has_hyperpay_webhook_key=row.hyperpay_webhook_key is not None,
                       hyperpay_webhook_url=svc.webhook_url(db, settings, row, "hyperpay"),
                       paytabs_profile_id=row.paytabs_profile_id, paytabs_key_hint=row.paytabs_key_hint,
                       has_paytabs_key=row.paytabs_server_key is not None, paytabs_region=row.paytabs_region,
                       paytabs_callback_url=svc.webhook_url(db, settings, row, "paytabs"),
                       test_provider_available=svc.test_provider_allowed(settings))


def link_out(link: PaymentLink) -> PaymentLinkOut:
    return PaymentLinkOut(id=link.id, invoice_id=link.invoice_id, web_order_id=link.web_order_id, provider=link.provider, url=link.url,
                   amount=str(from_minor(link.amount_minor, link.currency)), currency=link.currency,
                   status=svc.effective_status(link), expires_at=link.expires_at, paid_at=link.paid_at,
                   payment_id=link.payment_id, note=link.note, created_at=link.created_at)


def _domain_errors(fn):
    try:
        return fn()
    except svc.PaymentsUnavailable as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except svc.PaymentsInvalid as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except ProviderError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


@router.get("/orgs/{org_id}/payments/settings", response_model=PaymentSettingsOut)
def read_settings(request: Request, _: OrgContext = Depends(require(P.ORG_MANAGE)), db: Session = Depends(get_db)):
    settings = request.app.state.settings
    row = svc.settings_row(db, settings)
    db.commit()
    return _settings_out(db, settings, row)


@router.put("/orgs/{org_id}/payments/settings", response_model=PaymentSettingsOut)
def change_settings(body: PaymentSettingsIn, request: Request, ctx: OrgContext = Depends(require(P.ORG_MANAGE)),
                    db: Session = Depends(get_db)):
    """Choose the provider. Secrets are only ever written: leave them out to keep the ones stored."""
    settings = request.app.state.settings
    row = svc.settings_row(db, settings)
    if body.provider == "test" and not svc.test_provider_allowed(settings):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "the test provider isn't available in production")
    org = current_tenant(db)
    changed = []
    if body.stripe_secret_key:
        row.stripe_secret_key = crypto.encrypt(settings, body.stripe_secret_key, context=org)
        row.stripe_key_hint = body.stripe_secret_key[:8] + "…" + body.stripe_secret_key[-4:]
        changed.append("stripe_secret_key")
    if body.stripe_webhook_secret:
        row.stripe_webhook_secret = crypto.encrypt(settings, body.stripe_webhook_secret, context=org)
        changed.append("stripe_webhook_secret")
    if body.moyasar_secret_key:
        row.moyasar_secret_key = crypto.encrypt(settings, body.moyasar_secret_key, context=org)
        row.moyasar_key_hint = body.moyasar_secret_key[:8] + "…" + body.moyasar_secret_key[-4:]
        changed.append("moyasar_secret_key")
    if body.moyasar_webhook_secret:
        row.moyasar_webhook_secret = crypto.encrypt(settings, body.moyasar_webhook_secret, context=org)
        changed.append("moyasar_webhook_secret")
    if body.hyperpay_access_token:
        row.hyperpay_access_token = crypto.encrypt(settings, body.hyperpay_access_token, context=org)
        row.hyperpay_token_hint = body.hyperpay_access_token[:6] + "…" + body.hyperpay_access_token[-4:]
        changed.append("hyperpay_access_token")
    for field in ("hyperpay_entity_id", "hyperpay_mada_entity_id", "hyperpay_live"):
        value = getattr(body, field)
        if value is not None and value != getattr(row, field):
            setattr(row, field, value)
            changed.append(field)
    if body.hyperpay_webhook_key:
        row.hyperpay_webhook_key = crypto.encrypt(settings, body.hyperpay_webhook_key.lower(), context=org)
        changed.append("hyperpay_webhook_key")
    if body.paytabs_server_key:
        row.paytabs_server_key = crypto.encrypt(settings, body.paytabs_server_key, context=org)
        row.paytabs_key_hint = body.paytabs_server_key[:4] + "…" + body.paytabs_server_key[-4:]
        changed.append("paytabs_server_key")
    for field in ("paytabs_profile_id", "paytabs_region"):
        value = getattr(body, field)
        if value is not None and value != getattr(row, field):
            setattr(row, field, value)
            changed.append(field)
    if body.provider == "paytabs" and (row.paytabs_server_key is None or not row.paytabs_profile_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "PayTabs needs the profile id and the server key")
    if body.provider == "hyperpay" and (row.hyperpay_access_token is None
                                        or not (row.hyperpay_entity_id or row.hyperpay_mada_entity_id)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "HyperPay needs the access token and at least one entity id (cards or mada)")
    if body.provider == "stripe" and (row.stripe_secret_key is None or row.stripe_webhook_secret is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Stripe needs the secret key and the webhook's signing secret (whsec_…)")
    if body.provider == "moyasar" and (row.moyasar_secret_key is None or row.moyasar_webhook_secret is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Moyasar needs the secret key and the webhook's secret token")
    row.provider = body.provider
    row.updated_by = ctx.user.id
    audit.record(db, actor_user_id=ctx.user.id, action="payments.settings_changed", target_type="organization",
                 target_id=org, details={"provider": body.provider, "changed": changed,
                                         "stripe_key_hint": row.stripe_key_hint,
                                         "moyasar_key_hint": row.moyasar_key_hint,
                                         "hyperpay_token_hint": row.hyperpay_token_hint,
                                         "paytabs_key_hint": row.paytabs_key_hint}, ip_address=client_ip(request))
    db.commit()
    return _settings_out(db, settings, row)


@router.get("/orgs/{org_id}/payments/available", response_model=PaymentProviderOut)
def available(request: Request, _: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    row = db.scalar(select(PaymentSettings))
    name = row.provider if row is not None else "none"
    if name == "test" and not svc.test_provider_allowed(request.app.state.settings):
        name = "none"
    return PaymentProviderOut(provider=name)


def _invoice(db: Session, invoice_id: str, *, lock: bool = False) -> Invoice:
    inv = lock_invoice(db, invoice_id) if lock else db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invoice not found")
    return inv


@router.get("/orgs/{org_id}/invoices/{invoice_id}/payment-links", response_model=list[PaymentLinkOut])
def list_links(invoice_id: str, _: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    inv = _invoice(db, invoice_id)
    rows = db.scalars(select(PaymentLink).where(PaymentLink.invoice_id == inv.id).order_by(PaymentLink.created_at.desc()))
    return [link_out(l) for l in rows]


@router.post("/orgs/{org_id}/invoices/{invoice_id}/payment-links", response_model=PaymentLinkOut,
             status_code=status.HTTP_201_CREATED)
def create_link(invoice_id: str, body: PaymentLinkIn, request: Request, response: Response,
                ctx: OrgContext = Depends(require(P.PAYMENTS_WRITE)), db: Session = Depends(get_db)):
    """A checkout link for the amount (default: all that is due). An open link for the same amount is returned (200)."""
    inv = _invoice(db, invoice_id, lock=True)
    amount = None
    if body.amount is not None:
        amount = to_minor(body.amount, inv.currency)
        if from_minor(amount, inv.currency) != body.amount:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"amount has more decimals than {inv.currency} allows")
    settings = request.app.state.settings
    link, created = _domain_errors(lambda: svc.create_link(db, settings, inv, amount_minor=amount, user_id=ctx.user.id,
                                                           http=request.app.state.payment_http))
    if created:
        audit.record(db, actor_user_id=ctx.user.id, action="invoice.payment_link_created", target_type="invoice",
                     target_id=inv.id, details={"number": inv.number, "link_id": link.id, "provider": link.provider,
                                                "amount": str(from_minor(link.amount_minor, link.currency))},
                     ip_address=client_ip(request))
    else:
        response.status_code = status.HTTP_200_OK
    db.commit()
    return link_out(link)


@router.post("/orgs/{org_id}/invoices/{invoice_id}/payment-links/{link_id}/cancel", response_model=PaymentLinkOut)
def cancel_link(invoice_id: str, link_id: str, request: Request, ctx: OrgContext = Depends(require(P.PAYMENTS_WRITE)),
                db: Session = Depends(get_db)):
    link = db.scalar(select(PaymentLink).where(PaymentLink.id == link_id, PaymentLink.invoice_id == invoice_id)
                     .with_for_update())
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "payment link not found")
    _domain_errors(lambda: svc.cancel_link(db, request.app.state.settings, link, http=request.app.state.payment_http))
    audit.record(db, actor_user_id=ctx.user.id, action="invoice.payment_link_cancelled", target_type="invoice",
                 target_id=invoice_id, details={"link_id": link.id}, ip_address=client_ip(request))
    db.commit()
    return link_out(link)


# ---------------------------------------------------------------- the provider's side (no signed-in user)


def _org_by_webhook(db: Session, digest: str) -> str | None:
    if db.get_bind().dialect.name == "postgresql":
        return db.scalar(text("SELECT payment_webhook_org(:h)"), {"h": digest})
    with system_scope(db):
        return db.scalar(select(PaymentSettings.organization_id).where(PaymentSettings.webhook_hash == digest))


def _org_by_session(db: Session, provider: str, session: str) -> str | None:
    if db.get_bind().dialect.name == "postgresql":
        return db.scalar(text("SELECT payment_link_org(:p, :s)"), {"p": provider, "s": session})
    with system_scope(db):
        return db.scalar(select(PaymentLink.organization_id).where(PaymentLink.provider == provider,
                                                                   PaymentLink.session_id == session))


def _stripe_call(db: Session, settings, token: str, body: bytes, signature: str) -> JSONResponse:
    org_id = _org_by_webhook(db, crypto.token_hash(token))
    if org_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment webhook address")
    bind_tenant(db, org_id)
    row = db.scalar(select(PaymentSettings))
    whsec = svc.secret(db, settings, row.stripe_webhook_secret)
    if whsec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment webhook address")
    try:
        event = verify_stripe(whsec, body, signature)
    except BadSignature as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"rejected: {e}")
    meaning = stripe_event(event)
    if meaning is None:
        return JSONResponse({"received": True, "result": "ignored"})
    link = db.scalar(select(PaymentLink).where(PaymentLink.provider == "stripe",
                                               PaymentLink.session_id == meaning.session_id))
    if link is None:
        return JSONResponse({"received": True, "result": "not ours"})  # a checkout made outside NOVARA
    if isinstance(meaning, Expired):
        svc.expired(db, link)
        db.commit()
        return JSONResponse({"received": True, "result": "expired"})
    assert isinstance(meaning, Paid)
    result = svc.complete(db, link.id, amount_minor=meaning.amount_minor, currency=meaning.currency,
                          reference=meaning.reference, paid_at=meaning.paid_at, settings=settings)
    db.commit()
    return JSONResponse({"received": True, "result": result})


@public_router.post("/public/payments/stripe/{token}")
async def stripe_webhook(token: str, request: Request, db: Session = Depends(get_db)):
    """Stripe's webhook calls for this organization (Stripe-Signature checked against its whsec_ secret)."""
    if len(token) > 100:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment webhook address")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too large")
    body = await request.body()
    if len(body) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too large")
    return await run_in_threadpool(_stripe_call, db, request.app.state.settings, token, body,
                                   request.headers.get("stripe-signature", ""))


def _moyasar_call(db: Session, settings, http, token: str, body: bytes) -> JSONResponse:
    org_id = _org_by_webhook(db, crypto.token_hash(token))
    if org_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment webhook address")
    bind_tenant(db, org_id)
    row = db.scalar(select(PaymentSettings))
    expected = svc.secret(db, settings, row.moyasar_webhook_secret)
    key = svc.secret(db, settings, row.moyasar_secret_key)
    if expected is None or key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment webhook address")
    try:
        event = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "rejected: the body isn't JSON")
    given = event.get("secret_token") if isinstance(event, dict) else None
    if not isinstance(given, str) or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "rejected: the secret token doesn't match")
    invoice_id = moyasar_invoice(event)
    if invoice_id is None:
        return JSONResponse({"received": True, "result": "ignored"})
    link = db.scalar(select(PaymentLink).where(PaymentLink.provider == "moyasar", PaymentLink.session_id == invoice_id))
    if link is None:
        return JSONResponse({"received": True, "result": "not ours"})  # an invoice made outside NOVARA
    try:
        meaning = Moyasar(key, http).fetch(invoice_id)                   # Moyasar's own answer decides
    except ProviderError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))         # Moyasar calls again later
    if meaning is None:
        return JSONResponse({"received": True, "result": "not paid"})
    if isinstance(meaning, Expired):
        svc.expired(db, link)
        db.commit()
        return JSONResponse({"received": True, "result": "expired"})
    result = svc.complete(db, link.id, amount_minor=meaning.amount_minor, currency=meaning.currency,
                          reference=meaning.reference, paid_at=meaning.paid_at, settings=settings)
    db.commit()
    return JSONResponse({"received": True, "result": result})


@public_router.post("/public/payments/moyasar/{token}")
async def moyasar_webhook(token: str, request: Request, db: Session = Depends(get_db)):
    """Moyasar's webhook calls for this organization: the secret token must match, then Moyasar's API is asked."""
    if len(token) > 100:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment webhook address")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too large")
    body = await request.body()
    if len(body) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too large")
    return await run_in_threadpool(_moyasar_call, db, request.app.state.settings, request.app.state.payment_http,
                                   token, body)


def _page(title: str, body: str, code: int = 200) -> HTMLResponse:
    doc = (f'<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>'
           '<style>body{font-family:system-ui,sans-serif;background:#f6f5f2;color:#1d1b16;margin:0;padding:2rem 1rem}'
           'main{max-width:28rem;margin:auto;background:#fff;border-radius:12px;padding:1.5rem;'
           'box-shadow:0 1px 3px rgba(0,0,0,.08)}h1{font-size:1.25rem}p{line-height:1.6}'
           '.amount{font-size:1.75rem;font-weight:700;direction:ltr;text-align:right}'
           'button{font:inherit;padding:.6rem 1.2rem;border-radius:8px;border:0;cursor:pointer;margin-inline-end:.5rem}'
           '.pay{background:#1f6f5c;color:#fff}.muted{color:#6b665c;font-size:.875rem}</style></head>'
           f'<body><main>{body}</main></body></html>')
    return HTMLResponse(doc, status_code=code, headers=PAGE_HEADERS)


def _test_link(db: Session, settings, session: str) -> PaymentLink:
    if not svc.test_provider_allowed(settings) or len(session) > 100:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    org_id = _org_by_session(db, "test", session)
    if org_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    bind_tenant(db, org_id)
    return db.scalar(select(PaymentLink).where(PaymentLink.provider == "test", PaymentLink.session_id == session))


@public_router.get("/public/payments/test/{session}", response_class=HTMLResponse)
def test_checkout(session: str, request: Request, db: Session = Depends(get_db)):
    """The test provider's checkout page: what a customer would see at the provider, with a pretend card."""
    link = _test_link(db, request.app.state.settings, session)
    org = db.get(Organization, link.organization_id)
    money = f"{from_minor(link.amount_minor, link.currency)} {link.currency}"
    state = svc.effective_status(link)
    if state != "open":
        return _page("الدفع", f"<h1>هذا الرابط لم يعد متاحاً</h1><p class='muted'>This link is {state}.</p>", 410)
    return _page("الدفع", (
        f"<p class='muted'>مزوّد دفع تجريبي · Test payment provider</p><h1>{html.escape(org.name)}</h1>"
        f"<p>⁦{html.escape(svc.subject_number(db, link))}⁩</p><p class='amount'>{html.escape(money)}</p>"
        f"<form method='post' action='{html.escape(session)}/pay'>"
        "<button class='pay' type='submit'>ادفع (تجريبي) · Pay (test)</button></form>"
        "<p class='muted'>لا تُسحب أي أموال حقيقية. No real money is taken.</p>"))


@public_router.post("/public/payments/test/{session}/pay")
def test_pay(session: str, request: Request, db: Session = Depends(get_db)):
    """Pretend the provider took the money, then confirm it the way a provider's webhook would."""
    link = _test_link(db, request.app.state.settings, session)
    if svc.effective_status(link) != "open":
        raise HTTPException(status.HTTP_410_GONE, "this link is no longer open")
    svc.complete(db, link.id, amount_minor=link.amount_minor, currency=link.currency, reference=session[-12:],
                 paid_at=utcnow(), settings=request.app.state.settings)
    db.commit()
    return RedirectResponse(f"{request.app.state.settings.app_base_url}/api/v1/public/payments/done?status=paid",
                            status_code=status.HTTP_303_SEE_OTHER)


@public_router.get("/public/payments/done", response_class=HTMLResponse)
def done(state: Literal["paid", "cancelled"] = Query(alias="status")):
    """Where the customer lands after the provider's page. It confirms nothing: only the provider's webhook does."""
    if state == "paid":
        return _page("شكراً لك", "<h1>شكراً لك، وصلتنا دفعتك</h1><p>سيصلك الإيصال من مزوّد الدفع، "
                                 "وستظهر الدفعة على الفاتورة خلال لحظات.</p>"
                                 "<p class='muted'>Thank you, your payment was received. It will show on the invoice "
                                 "within moments.</p>")
    return _page("لم يكتمل الدفع", "<h1>لم يكتمل الدفع</h1><p>لم يُسحب أي مبلغ. يمكنك فتح الرابط مرة أخرى متى شئت.</p>"
                                    "<p class='muted'>The payment wasn't completed and nothing was charged. You can "
                                    "open the link again.</p>")


# ---------------------------------------------------------------- HyperPay (COPYandPAY)

BRAND_LABELS = {"mada": ("مدى", "mada", "MADA"), "card": ("فيزا أو ماستركارد", "Visa or Mastercard", "VISA MASTER")}


def _hyperpay_link(db: Session, session: str) -> PaymentLink:
    if not session.startswith("hp_") or len(session) > 100:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    org_id = _org_by_session(db, "hyperpay", session)
    if org_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    bind_tenant(db, org_id)
    return db.scalar(select(PaymentLink).where(PaymentLink.provider == "hyperpay", PaymentLink.session_id == session))


def _hyperpay(db: Session, settings, http) -> HyperPay:
    try:
        gateway = svc.provider(db, settings, svc.settings_row(db, settings), http, name="hyperpay")
    except svc.PaymentsUnavailable:
        raise HTTPException(status.HTTP_410_GONE, "online payments are no longer taken this way") from None
    assert isinstance(gateway, HyperPay)
    return gateway


def _widget_headers(host: str) -> dict[str, str]:
    """HyperPay's widget runs its own scripts and frames, and 3-D Secure goes through the card's bank."""
    hosts = f"{host} https://*.oppwa.com"
    return {"Content-Security-Policy": f"default-src 'none'; script-src {hosts} 'unsafe-inline'; style-src {hosts} "
                                       f"'unsafe-inline'; img-src https: data:; font-src {hosts}; connect-src {hosts}; "
                                       "frame-src https:; form-action 'self' https:; frame-ancestors 'none'; base-uri 'none'",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}


def _customer_fields(db: Session, link: PaymentLink) -> dict[str, str]:
    """What HyperPay asks of the buyer for 3-D Secure, as far as the customer's record (or the site order) has it."""
    if link.web_order_id:
        w = db.get(WebOrder, link.web_order_id)
        return {"customer.email": w.email, "customer.givenName": w.customer_name[:48], "billing.country": "SA"}
    c = db.get(Customer, db.get(Invoice, link.invoice_id).customer_id)
    if c is None:
        return {}
    return {"customer.email": c.email or "", "customer.givenName": (c.name or "")[:48], "billing.street1": c.address_line or "",
            "billing.city": c.city or "", "billing.postcode": c.postal_code or "", "billing.country": c.country or "SA"}


@public_router.get("/public/payments/hyperpay/{session}", response_class=HTMLResponse)
def hyperpay_checkout(session: str, request: Request, brand: Literal["mada", "card"] | None = None,
                      db: Session = Depends(get_db)):
    """The customer's page: choose mada or another card, then HyperPay's widget for a checkout prepared now."""
    link = _hyperpay_link(db, session)
    org = db.get(Organization, link.organization_id)
    money = f"{from_minor(link.amount_minor, link.currency)} {link.currency}"
    state = svc.effective_status(link)
    if state != "open":
        return _page("الدفع", f"<h1>هذا الرابط لم يعد متاحاً</h1><p class='muted'>This link is {html.escape(state)}.</p>", 410)
    settings = request.app.state.settings
    gateway = _hyperpay(db, settings, request.app.state.payment_http)
    head = (f"<h1>{html.escape(org.name)}</h1><p>⁦{html.escape(svc.subject_number(db, link))}⁩</p>"
            f"<p class='amount'>{html.escape(money)}</p>")
    brands = gateway.brands()
    if brand is not None and brand not in brands:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if brand is None and len(brands) > 1:
        choices = "".join(f"<p><a class='pay' href='{html.escape(session)}?brand={b}'>{BRAND_LABELS[b][0]} · "
                          f"{BRAND_LABELS[b][1]}</a></p>" for b in brands)
        return _page("الدفع", head + "<p>اختر طريقة الدفع · Choose how to pay</p>" + choices)
    brand = brand or brands[0]
    try:
        checkout = gateway.prepare(brand=brand, link_id=link.id, amount_minor=link.amount_minor, currency=link.currency,
                                   customer=_customer_fields(db, link))
    except ProviderError as e:
        return _page("الدفع", head + f"<p>تعذّر بدء الدفع، حاول بعد قليل.</p><p class='muted'>{html.escape(str(e))}</p>", 502)
    result = f"{settings.app_base_url}/api/v1/public/payments/hyperpay/{session}/result?brand={brand}"
    integrity = f' integrity="{html.escape(checkout.integrity)}" crossorigin="anonymous"' if checkout.integrity else ""
    widget = (f'<script src="{gateway.host}/v1/paymentWidgets.js?checkoutId={html.escape(checkout.checkout_id)}"{integrity}>'
              f'</script><form action="{html.escape(result)}" class="paymentWidgets" '
              f'data-brands="{BRAND_LABELS[brand][2]}"></form>')
    page = _page("الدفع", head + widget)
    page.headers.update(_widget_headers(gateway.host))
    return page


def _not_completed(session: str, outcome: HyperPayResult | None = None, code: int = 200) -> HTMLResponse:
    detail = f"<p class='muted'>{html.escape(outcome.description)} ({html.escape(outcome.code)})</p>" if outcome else ""
    return _page("لم يكتمل الدفع", "<h1>لم يكتمل الدفع</h1><p>لم يُسحب أي مبلغ. يمكنك المحاولة مرة أخرى.</p>"
                                    f"<p><a class='pay' href='../{html.escape(session)}'>حاول مرة أخرى · Try again</a></p>"
                                    "<p class='muted'>The payment wasn't completed and nothing was charged.</p>" + detail, code)


def _record(db: Session, settings, link: PaymentLink, outcome: HyperPayResult, brand: str) -> str:
    """What HyperPay said about a payment for this link: recorded when taken, noted when held for review."""
    if outcome.state == "paid":
        if link.status == "open":
            link.provider_detail = brand        # a refund goes back through the same entity
        if outcome.amount_minor is None:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "HyperPay's answer had no amount")
        result = svc.complete(db, link.id, amount_minor=outcome.amount_minor, currency=outcome.currency,
                              reference=outcome.payment_id, paid_at=utcnow(), settings=settings)
        db.commit()
        return result
    if outcome.state == "review" and link.status == "open":
        link.note = f"HyperPay is reviewing the payment ({outcome.code}); it is recorded once HyperPay accepts it"[:300]
        db.commit()
    return outcome.state


def _hyperpay_back(db: Session, settings, http, session: str, brand: str, checkout_id: str):
    link = _hyperpay_link(db, session)
    try:
        outcome = _hyperpay(db, settings, http).checkout_result(checkout_id, brand)
    except ProviderError as e:
        if e.invalid:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
        return _page("الدفع", "<h1>لم نتأكد من الدفع بعد</h1><p>إن سُحب المبلغ فسيظهر على الفاتورة خلال لحظات.</p>"
                              f"<p class='muted'>{html.escape(str(e))}</p>", 502)
    if outcome.state in ("pending", "failed"):
        return _not_completed(session, outcome)
    if outcome.merchant_reference != link.id:                # a checkout made for another link
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "that payment wasn't made for this link")
    if _record(db, settings, link, outcome, brand) == "review":
        return _page("الدفع قيد المراجعة", "<h1>دفعتك قيد المراجعة</h1><p>ستظهر على الفاتورة حين يقبلها مزوّد الدفع.</p>"
                                           "<p class='muted'>Your payment is being reviewed by the provider.</p>")
    return RedirectResponse(f"{settings.app_base_url}/api/v1/public/payments/done?status=paid",
                            status_code=status.HTTP_303_SEE_OTHER)


@public_router.get("/public/payments/hyperpay/{session}/result", response_class=HTMLResponse)
def hyperpay_result(session: str, request: Request, brand: Literal["mada", "card"], checkout_id: str = Query(alias="id"),
                    db: Session = Depends(get_db)):
    """Where HyperPay sends the customer back. Nothing here is believed: HyperPay is asked what happened."""
    return _hyperpay_back(db, request.app.state.settings, request.app.state.payment_http, session, brand, checkout_id)


def _hyperpay_call(db: Session, settings, http, token: str, body: bytes, iv: str, tag: str) -> JSONResponse:
    org_id = _org_by_webhook(db, crypto.token_hash(token))
    if org_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment webhook address")
    bind_tenant(db, org_id)
    row = db.scalar(select(PaymentSettings))
    key = svc.secret(db, settings, row.hyperpay_webhook_key)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment webhook address")
    try:
        event = decrypt_hyperpay(key, iv, tag, body.decode("ascii"))
    except (BadSignature, UnicodeDecodeError) as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"rejected: {e}")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if str(event.get("type", "")).upper() != "PAYMENT" or not payload.get("id"):
        return JSONResponse({"received": True, "result": "ignored"})
    link = db.scalar(select(PaymentLink).where(PaymentLink.id == str(payload.get("merchantTransactionId", "")),
                                               PaymentLink.provider == "hyperpay"))
    if link is None:
        return JSONResponse({"received": True, "result": "not ours"})   # a payment made outside NOVARA
    brand = "mada" if str(payload.get("paymentBrand", "")).upper() == "MADA" else "card"
    try:
        outcome = _hyperpay(db, settings, http).payment(str(payload["id"]), brand)   # HyperPay's own answer decides
    except ProviderError as e:
        if e.invalid:
            return JSONResponse({"received": True, "result": "ignored"})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))         # HyperPay calls again later
    if outcome.merchant_reference != link.id:
        return JSONResponse({"received": True, "result": "not ours"})
    return JSONResponse({"received": True, "result": _record(db, settings, link, outcome, brand)})


@public_router.post("/public/payments/hyperpay/{token}")
async def hyperpay_webhook(token: str, request: Request, db: Session = Depends(get_db)):
    """HyperPay's webhook calls for this organization: decrypted with its key, then HyperPay's API is asked."""
    if len(token) > 100:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment webhook address")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too large")
    body = await request.body()
    if len(body) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too large")
    return await run_in_threadpool(_hyperpay_call, db, request.app.state.settings, request.app.state.payment_http, token,
                                   body, request.headers.get("x-initialization-vector", ""),
                                   request.headers.get("x-authentication-tag", ""))


# ---------------------------------------------------------------- PayTabs (hosted payment page)


def _paytabs_link(db: Session, session: str) -> PaymentLink:
    if not session.startswith("pt_") or len(session) > 100:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    org_id = _org_by_session(db, "paytabs", session)
    if org_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    bind_tenant(db, org_id)
    return db.scalar(select(PaymentLink).where(PaymentLink.provider == "paytabs", PaymentLink.session_id == session))


def _paytabs(db: Session, settings, http) -> PayTabs:
    try:
        gateway = svc.provider(db, settings, svc.settings_row(db, settings), http, name="paytabs")
    except svc.PaymentsUnavailable:
        raise HTTPException(status.HTTP_410_GONE, "online payments are no longer taken this way") from None
    assert isinstance(gateway, PayTabs)
    return gateway


def _paytabs_customer(db: Session, link: PaymentLink) -> dict[str, str]:
    """Fills PayTabs' page in advance, as far as the customer's record (or the site order) has it."""
    if link.web_order_id:
        w = db.get(WebOrder, link.web_order_id)
        return {"name": w.customer_name[:128], "email": w.email, "phone": w.phone, "country": "SA"}
    c = db.get(Customer, db.get(Invoice, link.invoice_id).customer_id)
    if c is None:
        return {}
    return {"name": (c.name or "")[:128], "email": c.email or "", "phone": c.phone or "", "street1": c.address_line or "",
            "city": c.city or "", "zip": c.postal_code or "", "country": c.country or "SA"}


@public_router.get("/public/payments/paytabs/{session}", response_class=HTMLResponse)
def paytabs_checkout(session: str, request: Request, db: Session = Depends(get_db)):
    """The customer's link: PayTabs is asked for a payment page for the amount now, and the customer goes there."""
    link = _paytabs_link(db, session)
    state = svc.effective_status(link)
    if state != "open":
        return _page("الدفع", f"<h1>هذا الرابط لم يعد متاحاً</h1><p class='muted'>This link is {html.escape(state)}.</p>", 410)
    settings = request.app.state.settings
    org = db.get(Organization, link.organization_id)
    gateway = _paytabs(db, settings, request.app.state.payment_http)
    row = svc.settings_row(db, settings)
    base = f"{settings.app_base_url}/api/v1/public/payments/paytabs"
    try:
        page = gateway.prepare(link_id=link.id, title=f"{org.name} · {svc.subject_number(db, link)}",
                               amount_minor=link.amount_minor, currency=link.currency,
                               customer=_paytabs_customer(db, link), return_url=f"{base}/{session}/return",
                               callback_url=svc.webhook_url(db, settings, row, "paytabs"),
                               lang="ar" if org.default_locale == "ar" else "en")
    except ProviderError as e:
        return _page("الدفع", f"<h1>{html.escape(org.name)}</h1><p>تعذّر بدء الدفع، حاول بعد قليل.</p>"
                              f"<p class='muted'>{html.escape(str(e))}</p>", 502)
    return RedirectResponse(page.redirect_url, status_code=status.HTTP_303_SEE_OTHER)


def _paytabs_record(db: Session, settings, link: PaymentLink, outcome: PayTabsResult) -> str:
    """What PayTabs said about a transaction for this link: recorded when authorised, noted when on hold."""
    if outcome.state == "paid":
        if outcome.amount_minor is None:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "PayTabs' answer had no amount")
        result = svc.complete(db, link.id, amount_minor=outcome.amount_minor, currency=outcome.currency,
                              reference=outcome.tran_ref, paid_at=utcnow(), settings=settings)
        db.commit()
        return result
    if outcome.state == "review" and link.status == "open":
        link.note = f"PayTabs is holding the payment ({outcome.message}); it is recorded once PayTabs releases it"[:300]
        db.commit()
    return outcome.state


def _paytabs_back(db: Session, settings, http, session: str, form: dict[str, str]):
    link = _paytabs_link(db, session)
    gateway = _paytabs(db, settings, http)
    try:
        verify_paytabs_return(svc.secret(db, settings, svc.settings_row(db, settings).paytabs_server_key) or "", form)
    except BadSignature as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"rejected: {e}")
    if form.get("cartId") != link.id:                            # a transaction made for another link
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "that payment wasn't made for this link")
    try:
        outcome = gateway.query(form.get("tranRef", ""))         # PayTabs' own answer decides
    except ProviderError as e:
        if e.invalid:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
        return _page("الدفع", "<h1>لم نتأكد من الدفع بعد</h1><p>إن سُحب المبلغ فسيظهر على الفاتورة خلال لحظات.</p>"
                              f"<p class='muted'>{html.escape(str(e))}</p>", 502)
    if outcome.cart_id != link.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "that payment wasn't made for this link")
    state = _paytabs_record(db, settings, link, outcome)
    if state in ("pending", "failed"):
        return _page("لم يكتمل الدفع", "<h1>لم يكتمل الدفع</h1><p>لم يُسحب أي مبلغ. يمكنك المحاولة مرة أخرى.</p>"
                                        f"<p><a class='pay' href='../{html.escape(session)}'>حاول مرة أخرى · Try again</a></p>"
                                        "<p class='muted'>The payment wasn't completed and nothing was charged.</p>"
                                        f"<p class='muted'>{html.escape(outcome.message)}</p>")
    if state == "review":
        return _page("الدفع قيد المراجعة", "<h1>دفعتك قيد المراجعة</h1><p>ستظهر على الفاتورة حين يقبلها مزوّد الدفع.</p>"
                                           "<p class='muted'>Your payment is being reviewed by the provider.</p>")
    return RedirectResponse(f"{settings.app_base_url}/api/v1/public/payments/done?status=paid",
                            status_code=status.HTTP_303_SEE_OTHER)


@public_router.post("/public/payments/paytabs/{session}/return", response_class=HTMLResponse)
async def paytabs_return(session: str, request: Request, db: Session = Depends(get_db)):
    """Where PayTabs sends the customer back (a signed form). Even signed, PayTabs is asked what happened."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too large")
    body = await request.body()
    if len(body) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too large")
    try:
        form = dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))   # an urlencoded form
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not a form") from None
    return await run_in_threadpool(_paytabs_back, db, request.app.state.settings, request.app.state.payment_http,
                                   session, form)


def _paytabs_call(db: Session, settings, http, token: str, body: bytes, signature: str) -> JSONResponse:
    org_id = _org_by_webhook(db, crypto.token_hash(token))
    if org_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment webhook address")
    bind_tenant(db, org_id)
    row = db.scalar(select(PaymentSettings))
    key = svc.secret(db, settings, row.paytabs_server_key)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment webhook address")
    try:
        event = verify_paytabs_callback(key, body, signature)
    except BadSignature as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"rejected: {e}")
    link = db.scalar(select(PaymentLink).where(PaymentLink.id == str(event.get("cart_id", "")),
                                               PaymentLink.provider == "paytabs"))
    if link is None or not event.get("tran_ref"):
        return JSONResponse({"received": True, "result": "not ours"})   # a payment made outside NOVARA
    try:
        outcome = _paytabs(db, settings, http).query(str(event["tran_ref"]))
    except ProviderError as e:
        if e.invalid:
            return JSONResponse({"received": True, "result": "ignored"})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))         # PayTabs calls again later
    if outcome.cart_id != link.id:
        return JSONResponse({"received": True, "result": "not ours"})
    return JSONResponse({"received": True, "result": _paytabs_record(db, settings, link, outcome)})


@public_router.post("/public/payments/paytabs/{token}")
async def paytabs_webhook(token: str, request: Request, db: Session = Depends(get_db)):
    """PayTabs' callback for a transaction of this organization: its signature checked, then PayTabs is asked."""
    if len(token) > 100:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment webhook address")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too large")
    body = await request.body()
    if len(body) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too large")
    return await run_in_threadpool(_paytabs_call, db, request.app.state.settings, request.app.state.payment_http, token,
                                   body, request.headers.get("signature", ""))
