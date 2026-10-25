"""Paying invoices through Moyasar (mada, Apple Pay, cards): a hosted invoice per link, and a webhook that is only a
hint: its secret token must match, and Moyasar's own API, asked with the organization's key, decides."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from tests.test_accounting_extended import ACCT, OWNER, balances, o, posted_invoice, shop  # noqa: F401 (shop is a fixture)
from tests.test_online_payments import LINE, new_link

KEY = "sk_test_" + "MoyasarKey" * 3
TOKEN = "moyasar-webhook-token-0123456789"


@pytest.fixture
def moyasar(app):
    """A stand-in for api.moyasar.com: invoices it made, and what it says about them."""
    calls: list[httpx.Request] = []
    invoices: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/v1/invoices":
            body = json.loads(request.content)
            inv_id = f"inv_{len(invoices) + 1:04d}"
            invoices[inv_id] = {"id": inv_id, "status": "initiated", "amount": body["amount"], "currency": body["currency"],
                                "url": f"https://checkout.moyasar.com/invoices/{inv_id}", "payments": [],
                                "updated_at": "2026-04-03T09:15:00.000Z"}
            return httpx.Response(201, json=invoices[inv_id])
        parts = path.split("/")
        if len(parts) >= 4 and parts[3] in invoices:
            inv = invoices[parts[3]]
            if request.method == "PUT" and path.endswith("/cancel"):
                inv["status"] = "canceled"
            return httpx.Response(200, json=inv)
        return httpx.Response(404, json={"type": "not_found", "message": "Object not found"})

    def pay(inv_id: str, amount: int | None = None):
        inv = invoices[inv_id]
        inv["status"] = "paid"
        inv["payments"] = [{"id": "pay_7f1c", "status": "paid", "amount": amount or inv["amount"], "source": {"type": "mada"}}]
        if amount is not None:
            inv["amount"] = amount

    app.state.payment_http = httpx.Client(transport=httpx.MockTransport(handler))
    yield {"calls": calls, "invoices": invoices, "pay": pay}
    app.state.payment_http.close()


def use_moyasar(api, s):
    r = api.req("PUT", OWNER, o(s, "/payments/settings"),
                json={"provider": "moyasar", "moyasar_secret_key": KEY, "moyasar_webhook_secret": TOKEN})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["provider"], out["has_moyasar_key"], out["has_moyasar_webhook_secret"]) == ("moyasar", True, True)
    assert out["moyasar_key_hint"] == KEY[:8] + "…" + KEY[-4:]
    return "/api/v1" + out["moyasar_webhook_url"].split("/api/v1", 1)[1]


def call(client, hook, inv_id, token=TOKEN, kind="payment_paid"):
    body = {"id": "evt_1", "type": kind, "secret_token": token, "live": False,
            "data": {"id": "pay_7f1c", "status": "paid", "invoice_id": inv_id, "amount": 1, "currency": "SAR"}}
    return client.post(hook, content=json.dumps(body).encode(), headers={"content-type": "application/json"})


def test_a_moyasar_link_is_paid_when_moyasar_says_so(api, shop, client, moyasar):
    put = lambda body: api.req("PUT", OWNER, o(shop, "/payments/settings"), json=body)  # noqa: E731
    assert put({"provider": "moyasar"}).status_code == 422                       # needs the key and the token
    assert put({"provider": "moyasar", "moyasar_secret_key": "pk_test_abc"}).status_code == 422
    assert put({"provider": "moyasar", "moyasar_secret_key": KEY, "moyasar_webhook_secret": "short"}).status_code == 422
    hook = use_moyasar(api, shop)
    assert hook.startswith("/api/v1/public/payments/moyasar/nvpay_")
    assert KEY not in json.dumps(api.req("GET", OWNER, o(shop, "/audit-logs")).json())

    inv = posted_invoice(api, shop, [LINE | {"tax_id": shop["vat"]}])
    made = new_link(api, shop, inv)
    assert made.status_code == 201, made.text
    link = made.json()
    assert (link["provider"], link["url"], link["amount"]) == ("moyasar", "https://checkout.moyasar.com/invoices/inv_0001",
                                                              "1150.00")
    sent = moyasar["calls"][-1]
    body = json.loads(sent.content)
    assert sent.headers["authorization"] == "Basic " + base64.b64encode(f"{KEY}:".encode()).decode()
    assert (body["amount"], body["currency"], body["metadata"]["link_id"]) == (115000, "SAR", link["id"])
    assert body["success_url"].endswith("/public/payments/done?status=paid") and body["expired_at"].endswith("Z")
    assert "INV-2026-00001" in body["description"]

    assert call(client, hook, "inv_0001", token="not-the-token-at-all").status_code == 400
    assert client.post(hook, content=b"not json").status_code == 400
    assert call(client, hook.replace("nvpay_", "nvpay_x"), "inv_0001").status_code == 404
    # A call that says "paid" before Moyasar does: Moyasar's answer decides, nothing is recorded.
    early = call(client, hook, "inv_0001")
    assert (early.status_code, early.json()["result"]) == (200, "not paid")
    assert api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()["status"] == "posted"
    assert moyasar["calls"][-1].url.path == "/v1/invoices/inv_0001"

    moyasar["pay"]("inv_0001")
    paid = call(client, hook, "inv_0001")
    assert (paid.status_code, paid.json()["result"]) == (200, "paid")
    assert call(client, hook, "inv_0001").json()["result"] == "duplicate"           # Moyasar calls again
    after = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()
    assert (after["status"], after["amount_due"]) == ("paid", "0.00")
    payments = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}/payments")).json()
    assert [(p["method"], p["reference"]) for p in payments] == [("online", "moyasar pay_7f1c")]
    assert balances(api, shop)["1250"] == "1150.00"
    assert call(client, hook, "inv_9999").json()["result"] == "not ours"
    assert call(client, hook, "inv_0001", kind="balance_transferred").json()["result"] in ("duplicate", "ignored")


def test_a_different_amount_is_reported_and_cancelling_reaches_moyasar(api, shop, client, moyasar):
    hook = use_moyasar(api, shop)
    inv = posted_invoice(api, shop, [LINE])
    link = new_link(api, shop, inv).json()
    moyasar["pay"]("inv_0001", amount=50000)                                          # 500.00 instead of 1,000.00
    assert call(client, hook, "inv_0001").json()["result"] == "unapplied"
    links = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}/payment-links")).json()
    assert (links[0]["status"], "500.00 SAR" in links[0]["note"]) == ("unapplied", True)
    assert api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()["status"] == "posted"

    second = new_link(api, shop, inv).json()
    assert second["id"] != link["id"]
    cancelled = api.req("POST", ACCT, o(shop, f"/invoices/{inv['id']}/payment-links/{second['id']}/cancel"))
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert (moyasar["calls"][-1].method, moyasar["calls"][-1].url.path) == ("PUT", "/v1/invoices/inv_0002/cancel")
    assert call(client, hook, "inv_0002", kind="invoice_canceled").json()["result"] == "expired"


def test_what_moyasar_takes(api, shop, moyasar):
    use_moyasar(api, shop)
    usd = api.req("POST", OWNER, o(shop, "/invoices"), json={"customer_id": shop["customer"], "issue_date": "2026-04-01",
                                                               "currency": "USD", "exchange_rate": "3.75", "lines": [LINE]})
    api.req("POST", ACCT, o(shop, f"/invoices/{usd.json()['id']}/post"))
    refused = new_link(api, shop, usd.json())
    assert refused.status_code == 422 and "Moyasar can't charge in USD" in refused.json()["detail"]
    tiny = posted_invoice(api, shop, [{"description": "Pin", "quantity": "1", "unit_price": "0.50"}])
    assert "smallest payment" in new_link(api, shop, tiny).json()["detail"]
