"""Paying invoices online: payment links through Stripe Checkout (or the test provider), confirmed only by the
provider's signed webhook, applied once, and reported to a person when the invoice can't take the money."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from novara.payments.models import PaymentSettings
from novara.payments.providers import (BadSignature, CheckoutRequest, Paid, ProviderError, Stripe, sign_stripe,
                                       stripe_event, verify_stripe)
from novara.tenancy import bind_tenant
from tests.test_accounting_extended import ACCT, OWNER, SALES, balances, o, posted_invoice, shop  # noqa: F401

KEY = "sk_test_" + "a1b2c3d4" * 3
WHSEC = "whsec_" + "Zm9vYmFy" * 4
LINE = {"description": "Consulting", "quantity": "1", "unit_price": "1000"}


@pytest.fixture
def stripe(app):
    """A stand-in for api.stripe.com: records each call; ``refuse`` makes the next one fail."""
    calls: list[httpx.Request] = []
    refuse: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if refuse:
            return refuse.pop()
        form = parse_qs(request.content.decode())
        if request.url.path == "/v1/checkout/sessions":
            n = sum(1 for c in calls if c.url.path == "/v1/checkout/sessions")
            return httpx.Response(200, json={"id": f"cs_test_{n}", "object": "checkout.session",
                                             "url": f"https://checkout.stripe.com/c/pay/cs_test_{n}",
                                             "expires_at": int(form["expires_at"][0])})
        if request.url.path.endswith("/expire"):
            return httpx.Response(200, json={"id": request.url.path.split("/")[-2], "status": "expired"})
        return httpx.Response(404, json={"error": {"message": "Unrecognized request URL"}})

    app.state.payment_http = httpx.Client(transport=httpx.MockTransport(handler))
    yield {"calls": calls, "refuse": refuse}
    app.state.payment_http.close()


def use_stripe(api, s):
    r = api.req("PUT", OWNER, o(s, "/payments/settings"),
                json={"provider": "stripe", "stripe_secret_key": KEY, "stripe_webhook_secret": WHSEC})
    assert r.status_code == 200, r.text
    return urlsplit(r.json()["webhook_url"]).path


def new_link(api, s, inv, **body):
    return api.req("POST", ACCT, o(s, f"/invoices/{inv['id']}/payment-links"), json=body)


def event(session: str, amount: int, kind: str = "checkout.session.completed", **extra) -> bytes:
    obj = {"id": session, "object": "checkout.session", "payment_status": "paid", "amount_total": amount,
           "currency": "sar", "payment_intent": "pi_3Px"} | extra
    return json.dumps({"id": "evt_1", "type": kind, "created": int(time.time()), "data": {"object": obj}}).encode()


def deliver(client, hook: str, body: bytes, secret: str = WHSEC, **kw):
    return client.post(hook, content=body, headers={"stripe-signature": sign_stripe(secret, body, **kw),
                                                    "content-type": "application/json"})


def test_stripe_settings_keep_their_secrets(api, shop, app):
    url = o(shop, "/payments/settings")
    assert api.req("GET", ACCT, url).status_code == 403
    first = api.req("GET", OWNER, url).json()
    assert (first["provider"], first["has_stripe_key"], first["test_provider_available"]) == ("none", False, True)
    assert first["webhook_url"].startswith("http://localhost:5173/api/v1/public/payments/stripe/nvpay_")
    put = lambda body: api.req("PUT", OWNER, url, json=body)  # noqa: E731
    assert put({"provider": "stripe"}).status_code == 422                                   # no secrets yet
    assert put({"provider": "stripe", "stripe_secret_key": "pk_test_" + "a" * 24,
                "stripe_webhook_secret": WHSEC}).status_code == 422                          # a publishable key
    assert put({"provider": "stripe", "stripe_secret_key": KEY}).status_code == 422          # no webhook secret
    done = put({"provider": "stripe", "stripe_secret_key": KEY, "stripe_webhook_secret": WHSEC})
    assert done.status_code == 200, done.text
    assert (done.json()["stripe_key_hint"], done.json()["has_webhook_secret"]) == ("sk_test_…c3d4", True)
    assert KEY not in done.text and WHSEC not in done.text
    assert done.json()["webhook_url"] == first["webhook_url"]                                # the address stays
    off = put({"provider": "none"}).json()
    assert (off["provider"], off["has_stripe_key"]) == ("none", True)                        # kept for later
    assert api.req("GET", SALES, o(shop, "/payments/available")).json() == {"provider": "none"}

    with app.state.sessionmaker() as db:
        bind_tenant(db, shop["org"])
        row = db.scalar(select(PaymentSettings))
        assert row.stripe_secret_key.startswith("v1:") and KEY not in row.stripe_secret_key
    logs = api.req("GET", OWNER, o(shop, "/audit-logs")).json()
    changed = [e for e in logs if e["action"] == "payments.settings_changed"]
    assert changed and all(KEY not in json.dumps(e) and WHSEC not in json.dumps(e) for e in changed)


def test_a_stripe_link_is_paid_once_by_the_signed_webhook(api, shop, client, stripe):
    hook = use_stripe(api, shop)
    inv = posted_invoice(api, shop, [LINE | {"tax_id": shop["vat"]}])
    assert inv["total"] == "1150.00"
    draft = api.req("POST", SALES, o(shop, "/invoices"), json={"customer_id": shop["customer"],
                                                               "issue_date": "2026-04-01", "lines": [LINE]}).json()
    assert new_link(api, shop, draft).status_code == 409
    assert api.req("POST", SALES, o(shop, f"/invoices/{inv['id']}/payment-links"), json={}).status_code == 403
    assert new_link(api, shop, inv, amount="2000").status_code == 422

    made = new_link(api, shop, inv)
    assert made.status_code == 201, made.text
    link = made.json()
    assert (link["amount"], link["currency"], link["status"], link["url"]) == (
        "1150.00", "SAR", "open", "https://checkout.stripe.com/c/pay/cs_test_1")
    sent = stripe["calls"][-1]
    form = {k: v[0] for k, v in parse_qs(sent.content.decode()).items()}
    assert sent.headers["authorization"] == f"Bearer {KEY}" and sent.headers["idempotency-key"] == f"novara-link-{link['id']}"
    assert (form["mode"], form["line_items[0][price_data][currency]"], form["line_items[0][price_data][unit_amount]"],
            form["metadata[link_id]"], form["client_reference_id"]) == ("payment", "sar", "115000", link["id"], link["id"])
    assert "INV-2026-00001" in form["line_items[0][price_data][product_data][name]"]
    assert form["success_url"].endswith("/api/v1/public/payments/done?status=paid")
    again = new_link(api, shop, inv)
    assert (again.status_code, again.json()["id"], len(stripe["calls"])) == (200, link["id"], 1)   # the same link

    body = event("cs_test_1", 115000)
    assert deliver(client, hook, body, secret="whsec_" + "x" * 32).status_code == 400            # wrong secret
    assert deliver(client, hook, body, now=int(time.time()) - 3600).status_code == 400           # too old
    assert client.post(hook, content=body).status_code == 400                                    # unsigned
    assert deliver(client, hook.replace("nvpay_", "nvpay_x"), body).status_code == 404
    paid = deliver(client, hook, body)
    assert (paid.status_code, paid.json()["result"]) == (200, "paid")
    assert deliver(client, hook, body).json()["result"] == "duplicate"                           # Stripe retries

    after = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()
    assert (after["status"], after["amount_due"]) == ("paid", "0.00")
    payments = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}/payments")).json()
    assert [(p["method"], p["amount"], p["reference"]) for p in payments] == [("online", "1150.00", "stripe pi_3Px")]
    bal = balances(api, shop)
    assert (bal["1250"], bal["1300"]) == ("1150.00", "0.00")                    # with Stripe until it pays out
    links = api.req("GET", SALES, o(shop, f"/invoices/{inv['id']}/payment-links")).json()
    assert [(l["status"], l["payment_id"]) for l in links] == [("paid", payments[0]["id"])]
    told = api.req("GET", ACCT, o(shop, "/notifications")).json()
    assert any("INV-2026-00001" in n["title"] for n in told)
    actions = [e["action"] for e in api.req("GET", OWNER, o(shop, "/audit-logs")).json()]
    assert {"invoice.payment_link_created", "invoice.paid_online"} <= set(actions)
    assert new_link(api, shop, inv).status_code == 409                          # nothing left to pay


def test_money_the_invoice_cant_take_is_reported_not_posted(api, shop, client, stripe):
    hook = use_stripe(api, shop)
    inv = posted_invoice(api, shop, [LINE])
    link = new_link(api, shop, inv).json()
    paid_by_hand = api.req("POST", ACCT, o(shop, f"/invoices/{inv['id']}/payments"),
                           json={"amount": "1000", "payment_date": "2026-04-05", "method": "bank"})
    assert paid_by_hand.status_code == 201
    late = deliver(client, hook, event("cs_test_1", 100000))
    assert late.json()["result"] == "unapplied"
    links = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}/payment-links")).json()
    assert links[0]["status"] == "unapplied" and "cannot receive payments" in links[0]["note"]
    assert [p["method"] for p in api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}/payments")).json()] == ["bank"]
    assert balances(api, shop).get("1250", "0.00") == "0.00"
    told = api.req("GET", ACCT, o(shop, "/notifications")).json()
    assert any("wasn't recorded" in n["title"] or "لم تُسجَّل" in n["title"] for n in told)
    assert deliver(client, hook, event("cs_test_1", 100000)).json()["result"] == "duplicate"

    other = posted_invoice(api, shop, [LINE], date="2026-04-02")
    part = new_link(api, shop, other, amount="400").json()
    assert part["amount"] == "400.00"
    odd = deliver(client, hook, event("cs_test_2", 40001)).json()               # not what the link asked for
    assert odd["result"] == "unapplied"
    note = api.req("GET", ACCT, o(shop, f"/invoices/{other['id']}/payment-links")).json()[0]["note"]
    assert "asked for 400.00 SAR" in note
    rest = new_link(api, shop, other).json()                                    # all that is due: 1000
    assert deliver(client, hook, event("cs_test_3", 0, kind="checkout.session.expired")).json()["result"] == "expired"
    newest = api.req("GET", ACCT, o(shop, f"/invoices/{other['id']}/payment-links")).json()[0]
    assert (newest["id"], newest["status"]) == (rest["id"], "expired")
    assert deliver(client, hook, event("cs_test_9", 100)).json()["result"] == "not ours"
    assert deliver(client, hook, event("cs_test_1", 1, kind="payment_intent.succeeded")).json()["result"] == "ignored"
    assert deliver(client, hook, event("cs_test_3", 100000, payment_status="unpaid")).json()["result"] == "ignored"


def test_cancelling_a_link_and_what_stripe_refuses(api, shop, stripe):
    use_stripe(api, shop)
    inv = posted_invoice(api, shop, [LINE])
    link = new_link(api, shop, inv).json()
    cancel = lambda: api.req("POST", ACCT, o(shop, f"/invoices/{inv['id']}/payment-links/{link['id']}/cancel"))  # noqa: E731
    out = cancel()
    assert out.status_code == 200 and out.json()["status"] == "cancelled"
    assert stripe["calls"][-1].url.path == "/v1/checkout/sessions/cs_test_1/expire"
    assert cancel().status_code == 409
    stripe["refuse"].append(httpx.Response(400, json={"error": {"message": "Amount must be at least 2.00 sar"}}))
    small = new_link(api, shop, inv, amount="1")
    assert small.status_code == 422 and "at least 2.00" in small.json()["detail"]
    stripe["refuse"].append(httpx.Response(500, json={}))
    assert new_link(api, shop, inv).status_code == 502
    assert len(api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}/payment-links")).json()) == 1   # nothing stored


def test_the_test_provider_works_the_same_way(api, shop, client, app):
    assert api.req("PUT", OWNER, o(shop, "/payments/settings"), json={"provider": "test"}).status_code == 200
    assert api.req("GET", SALES, o(shop, "/payments/available")).json() == {"provider": "test"}
    inv = posted_invoice(api, shop, [LINE | {"tax_id": shop["vat"]}])
    link = new_link(api, shop, inv).json()
    page_url = urlsplit(link["url"]).path
    assert page_url.startswith("/api/v1/public/payments/test/cs_novaratest_")
    page = client.get(page_url)
    assert page.status_code == 200 and "1150.00 SAR" in page.text and "INV-2026-00001" in page.text
    assert page.headers["content-security-policy"].startswith("default-src 'none'")
    paid = client.post(page_url + "/pay", follow_redirects=False)
    assert paid.status_code == 303 and paid.headers["location"].endswith("/api/v1/public/payments/done?status=paid")
    assert api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()["status"] == "paid"
    assert client.get(page_url).status_code == 410
    assert client.post(page_url + "/pay").status_code == 410
    assert client.get("/api/v1/public/payments/done", params={"status": "paid"}).status_code == 200
    assert client.get("/api/v1/public/payments/test/cs_novaratest_nope").status_code == 404

    app.state.settings = replace(app.state.settings, env="production")         # never in production
    assert client.get(page_url).status_code == 404
    assert api.req("PUT", OWNER, o(shop, "/payments/settings"), json={"provider": "test"}).status_code == 422
    assert api.req("GET", SALES, o(shop, "/payments/available")).json() == {"provider": "none"}


def test_signatures_and_amounts_stripe_takes():
    body = b'{"type": "x"}'
    good = sign_stripe(WHSEC, body)
    stamp = good.split(",")[0]
    assert verify_stripe(WHSEC, body, f"{stamp},v1=deadbeef,{good.split(',')[1]}") == {"type": "x"}   # one of several
    for header in ("", "t=abc,v1=00", f"{stamp},v0=00", good.replace("v1=", "v1=0")):
        with pytest.raises(BadSignature):
            verify_stripe(WHSEC, body, header)
    with pytest.raises(BadSignature):
        verify_stripe(WHSEC, b"{tampered}", good)

    gateway = Stripe(KEY, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    req = CheckoutRequest(link_id="l1", title="x", amount_minor=1005, currency="KWD", customer_email="",
                          success_url="https://a.example/s", cancel_url="https://a.example/c",
                          expires_at=datetime.now(timezone.utc) + timedelta(hours=1), metadata={})
    with pytest.raises(ProviderError, match="steps of 0.010"):
        gateway.create_checkout(req)
    with pytest.raises(ProviderError, match="can't charge in IQD"):
        gateway.create_checkout(replace(req, currency="IQD"))
    meaning = stripe_event(json.loads(event("cs_1", 500, kind="checkout.session.async_payment_succeeded")))
    assert isinstance(meaning, Paid) and (meaning.amount_minor, meaning.currency, meaning.reference) == (500, "SAR", "pi_3Px")
