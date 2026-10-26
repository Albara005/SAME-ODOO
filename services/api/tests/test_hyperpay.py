"""Paying invoices through HyperPay (COPYandPAY: mada, Visa, Mastercard). The link is NOVARA's own page, which prepares
a HyperPay checkout when the customer opens it; the result is read back from HyperPay with the organization's access
token, and only counts for the link it was made for. HyperPay's encrypted webhook is a hint checked the same way."""

from __future__ import annotations

import json
import os
import re
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tests.test_accounting_extended import ACCT, OWNER, balances, o, posted_invoice, shop  # noqa: F401 (shop is a fixture)
from tests.test_online_payments import LINE, new_link

TOKEN = "OGFjN2E0Yz" + "k" * 40
CARD, MADA = "8ac7a4c9" + "1" * 24, "8ac7a4c9" + "2" * 24
KEY = "A" * 64                                       # the webhook's AES-256 key, as hex


@pytest.fixture
def hyperpay(app):
    """A stand-in for eu-test.oppwa.com: checkouts made, and what each says now."""
    calls: list[httpx.Request] = []
    checkouts: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.host == "eu-test.oppwa.com"
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"result": {"code": "800.900.300", "description": "invalid authentication"}})
        path = request.url.path
        if request.method == "POST" and path == "/v1/checkouts":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            cid = f"{len(checkouts) + 1:02d}" + "C0FFEE" * 5 + ".uat01-vm-tx01"
            checkouts[cid] = {"form": form, "result": {"code": "000.200.000", "description": "transaction pending"}}
            return httpx.Response(200, json={"result": {"code": "000.200.100", "description": "successfully created checkout"},
                                             "id": cid, "integrity": "sha384-TESTINTEGRITY"})
        m = re.fullmatch(r"/v1/checkouts/([^/]+)/payment", path)
        if m and m.group(1) in checkouts:
            c = checkouts[m.group(1)]
            if request.url.params.get("entityId") != c["form"]["entityId"]:
                return httpx.Response(200, json={"result": {"code": "200.300.404", "description": "invalid or missing parameter"}})
            return httpx.Response(200, json={"id": "PAY" + m.group(1)[:20], "paymentType": "DB", "amount": c["form"]["amount"],
                                             "currency": c["form"]["currency"], "result": c["result"],
                                             "merchantTransactionId": c["form"]["merchantTransactionId"]})
        m = re.fullmatch(r"/v1/query/PAY(.+)", path)
        if m:
            c = next(v for k, v in checkouts.items() if k.startswith(m.group(1)))
            return httpx.Response(200, json={"id": "PAY" + m.group(1), "paymentType": "DB", "amount": c["form"]["amount"],
                                             "currency": c["form"]["currency"], "result": c["result"],
                                             "merchantTransactionId": c["form"]["merchantTransactionId"]})
        return httpx.Response(404, json={"result": {"code": "700.400.580", "description": "cannot find transaction"}})

    def pay(cid: str, code: str = "000.100.110"):
        checkouts[cid]["result"] = {"code": code, "description": "Request successfully processed"}

    app.state.payment_http = httpx.Client(transport=httpx.MockTransport(handler))
    yield {"calls": calls, "checkouts": checkouts, "pay": pay}
    app.state.payment_http.close()


def use_hyperpay(api, s, **extra):
    r = api.req("PUT", OWNER, o(s, "/payments/settings"), json={
        "provider": "hyperpay", "hyperpay_access_token": TOKEN, "hyperpay_entity_id": CARD,
        "hyperpay_mada_entity_id": MADA, "hyperpay_webhook_key": KEY} | extra)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["provider"], out["has_hyperpay_token"], out["hyperpay_entity_id"], out["hyperpay_live"]) == (
        "hyperpay", True, CARD, False)
    return "/api/v1" + out["hyperpay_webhook_url"].split("/api/v1", 1)[1]


def page_path(url: str) -> str:
    return "/api/v1" + url.split("/api/v1", 1)[1]


def test_a_hyperpay_link_is_paid_when_hyperpay_says_so(api, shop, client, hyperpay):
    put = lambda body: api.req("PUT", OWNER, o(shop, "/payments/settings"), json=body)  # noqa: E731
    assert put({"provider": "hyperpay"}).status_code == 422                       # needs the token and an entity
    assert put({"provider": "hyperpay", "hyperpay_access_token": TOKEN}).status_code == 422
    use_hyperpay(api, shop)
    inv = posted_invoice(api, shop, [LINE | {"tax_id": shop["vat"]}])
    made = new_link(api, shop, inv)
    assert made.status_code == 201, made.text
    link = made.json()
    assert link["provider"] == "hyperpay" and "/public/payments/hyperpay/hp_" in link["url"]
    assert hyperpay["calls"] == []                                                # nothing asked of HyperPay yet

    page = client.get(page_path(link["url"]))
    assert page.status_code == 200 and "مدى" in page.text and "Visa" in page.text   # choose mada or a card
    widget = client.get(page_path(link["url"]), params={"brand": "mada"})
    assert widget.status_code == 200
    sent = hyperpay["calls"][-1]
    form = {k: v[0] for k, v in parse_qs(sent.content.decode()).items()}
    assert (form["entityId"], form["amount"], form["currency"], form["paymentType"], form["merchantTransactionId"]) == (
        MADA, "1150.00", "SAR", "DB", link["id"])
    cid = next(iter(hyperpay["checkouts"]))
    assert f"paymentWidgets.js?checkoutId={cid}" in widget.text and 'integrity="sha384-TESTINTEGRITY"' in widget.text
    assert 'data-brands="MADA"' in widget.text
    csp = widget.headers["content-security-policy"]
    assert "script-src https://eu-test.oppwa.com https://*.oppwa.com" in csp and "frame-ancestors 'none'" in csp

    result = page_path(link["url"]) + "/result"
    waiting = client.get(result, params={"brand": "mada", "id": cid}, follow_redirects=False)
    assert waiting.status_code == 200 and "لم يكتمل" in waiting.text               # still pending at HyperPay
    assert api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()["status"] == "posted"
    wrong_entity = client.get(result, params={"brand": "card", "id": cid}, follow_redirects=False)
    assert wrong_entity.status_code == 200 and "لم يكتمل" in wrong_entity.text     # HyperPay doesn't know it there

    hyperpay["pay"](cid)
    paid = client.get(result, params={"brand": "mada", "id": cid}, follow_redirects=False)
    assert paid.status_code == 303 and paid.headers["location"].endswith("/public/payments/done?status=paid")
    after = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()
    assert (after["status"], after["amount_due"]) == ("paid", "0.00")
    payments = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}/payments")).json()
    assert [p["reference"] for p in payments] == [f"hyperpay PAY{cid[:20]}"]
    assert balances(api, shop)["1250"] == "1150.00"
    again = client.get(result, params={"brand": "mada", "id": cid}, follow_redirects=False)
    assert again.status_code == 303                                               # the same answer, recorded once
    assert len(api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}/payments")).json()) == 1
    assert client.get(page_path(link["url"])).status_code == 410                  # paid: the page is done


def test_a_result_only_pays_its_own_link_and_the_webhook_is_checked(api, shop, client, hyperpay):
    hook = use_hyperpay(api, shop)
    small = posted_invoice(api, shop, [{"description": "Pin", "quantity": "1", "unit_price": "5"}])
    big = posted_invoice(api, shop, [LINE])
    a, b = new_link(api, shop, small).json(), new_link(api, shop, big).json()
    client.get(page_path(a["url"]), params={"brand": "card"})
    cid = next(iter(hyperpay["checkouts"]))
    hyperpay["pay"](cid)
    # The small link's checkout, replayed against the big link's result address, pays nothing.
    stolen = client.get(page_path(b["url"]) + "/result", params={"brand": "card", "id": cid}, follow_redirects=False)
    assert stolen.status_code == 400
    links = api.req("GET", ACCT, o(shop, f"/invoices/{big['id']}/payment-links")).json()
    assert links[0]["status"] == "open"

    # The customer closed the page before coming back: HyperPay's webhook tells, and HyperPay is asked again.
    event = {"type": "PAYMENT", "payload": {"id": "PAY" + cid[:20], "paymentBrand": "VISA", "amount": "5.75",
                                            "currency": "SAR", "result": {"code": "000.100.110"},
                                            "merchantTransactionId": a["id"]}}
    iv = os.urandom(12)
    sealed = AESGCM(bytes.fromhex(KEY)).encrypt(iv, json.dumps(event).encode(), None)
    body, tag = sealed[:-16].hex().encode(), sealed[-16:].hex()
    bad = client.post(hook, content=body, headers={"x-initialization-vector": iv.hex(), "x-authentication-tag": "00" * 16})
    assert bad.status_code == 400
    ok = client.post(hook, content=body, headers={"x-initialization-vector": iv.hex(), "x-authentication-tag": tag})
    assert (ok.status_code, ok.json()["result"]) == (200, "paid")
    assert api.req("GET", ACCT, o(shop, f"/invoices/{small['id']}")).json()["status"] == "paid"
    assert hyperpay["calls"][-1].url.path == f"/v1/query/PAY{cid[:20]}"
    assert client.post(hook, content=body, headers={"x-initialization-vector": iv.hex(),
                                                    "x-authentication-tag": tag}).json()["result"] == "duplicate"


def test_what_hyperpay_takes(api, shop, client, hyperpay):
    use_hyperpay(api, shop, hyperpay_mada_entity_id="")
    usd = api.req("POST", OWNER, o(shop, "/invoices"), json={"customer_id": shop["customer"], "issue_date": "2026-04-01",
                                                               "currency": "USD", "exchange_rate": "3.75", "lines": [LINE]})
    api.req("POST", ACCT, o(shop, f"/invoices/{usd.json()['id']}/post"))
    refused = new_link(api, shop, usd.json())
    assert refused.status_code == 422 and "HyperPay can't charge in USD" in refused.json()["detail"]
    # Cards only: the page goes straight to the widget, and asking for mada finds nothing.
    sar = posted_invoice(api, shop, [LINE])
    link = new_link(api, shop, sar).json()
    widget = client.get(page_path(link["url"]))
    assert widget.status_code == 200 and 'data-brands="VISA MASTER"' in widget.text
    assert client.get(page_path(link["url"]), params={"brand": "mada"}).status_code == 404
    # Another provider for new links: a HyperPay link already sent can still be paid until it expires.
    assert api.req("PUT", OWNER, o(shop, "/payments/settings"), json={"provider": "test"}).status_code == 200
    assert client.get(page_path(link["url"])).status_code == 200
    # HyperPay with no entity left is refused (and nothing is changed).
    assert api.req("PUT", OWNER, o(shop, "/payments/settings"),
                   json={"provider": "hyperpay", "hyperpay_entity_id": "", "hyperpay_mada_entity_id": ""}).status_code == 422
    assert api.req("GET", OWNER, o(shop, "/payments/settings")).json()["hyperpay_entity_id"] == CARD
