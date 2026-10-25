"""Online payments: the organization's provider settings, payment links on invoices, and the provider's calls.

    GET/PUT /orgs/{org}/payments/settings               provider and secrets (org.manage; secrets are write-only)
    GET     /orgs/{org}/payments/available              which provider is on (anyone who can read invoices)
    POST    /orgs/{org}/invoices/{id}/payment-links     a checkout link for what is due (payments.write)
    GET     /orgs/{org}/invoices/{id}/payment-links
    POST    /orgs/{org}/invoices/{id}/payment-links/{link}/cancel

    POST /api/v1/public/payments/stripe/{token}         Stripe's signed webhook calls
    GET  /api/v1/public/payments/test/{session}         the test provider's checkout page (not in production)
    POST /api/v1/public/payments/test/{session}/pay
    GET  /api/v1/public/payments/done?status=paid       where the customer lands afterwards
"""

from __future__ import annotations

import html
from datetime import datetime
from decimal import Decimal
from typing import Literal

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
from novara.money import from_minor, to_minor
from novara.payments import service as svc
from novara.payments.models import PaymentLink, PaymentSettings
from novara.payments.providers import BadSignature, Expired, Paid, ProviderError, stripe_event, verify_stripe
from novara.tenancy import bind_tenant, current_tenant, system_scope

MAX_BYTES = 256 * 1024
STRIPE_KEY = r"^(sk|rk)_(test|live)_[A-Za-z0-9]{10,200}$"
STRIPE_WEBHOOK_SECRET = r"^whsec_[A-Za-z0-9+/=_-]{10,200}$"
PAGE_HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                                           "frame-ancestors 'none'; base-uri 'none'",
                "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}

router = APIRouter(tags=["online payments"])
public_router = APIRouter(tags=["online payments"])


class PaymentSettingsOut(BaseModel):
    provider: Literal["none", "stripe", "test"]
    stripe_key_hint: str
    has_stripe_key: bool
    has_webhook_secret: bool
    webhook_url: str
    test_provider_available: bool


class PaymentSettingsIn(BaseModel):
    provider: Literal["none", "stripe", "test"]
    stripe_secret_key: str | None = Field(default=None, pattern=STRIPE_KEY)
    stripe_webhook_secret: str | None = Field(default=None, pattern=STRIPE_WEBHOOK_SECRET)


class PaymentProviderOut(BaseModel):
    provider: Literal["none", "stripe", "test"]


class PaymentLinkIn(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=3)


class PaymentLinkOut(BaseModel):
    id: str
    invoice_id: str
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
                       test_provider_available=svc.test_provider_allowed(settings))


def link_out(link: PaymentLink) -> PaymentLinkOut:
    return PaymentLinkOut(id=link.id, invoice_id=link.invoice_id, provider=link.provider, url=link.url,
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
    if body.provider == "stripe" and (row.stripe_secret_key is None or row.stripe_webhook_secret is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Stripe needs the secret key and the webhook's signing secret (whsec_…)")
    row.provider = body.provider
    row.updated_by = ctx.user.id
    audit.record(db, actor_user_id=ctx.user.id, action="payments.settings_changed", target_type="organization",
                 target_id=org, details={"provider": body.provider, "changed": changed,
                                         "stripe_key_hint": row.stripe_key_hint}, ip_address=client_ip(request))
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
                          reference=meaning.reference, paid_at=meaning.paid_at)
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
    inv = db.get(Invoice, link.invoice_id)
    org = db.get(Organization, link.organization_id)
    money = f"{from_minor(link.amount_minor, link.currency)} {link.currency}"
    state = svc.effective_status(link)
    if state != "open":
        return _page("الدفع", f"<h1>هذا الرابط لم يعد متاحاً</h1><p class='muted'>This link is {state}.</p>", 410)
    return _page("الدفع", (
        f"<p class='muted'>مزوّد دفع تجريبي · Test payment provider</p><h1>{html.escape(org.name)}</h1>"
        f"<p>الفاتورة ⁦{html.escape(inv.number or '')}⁩</p><p class='amount'>{html.escape(money)}</p>"
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
                 paid_at=utcnow())
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
