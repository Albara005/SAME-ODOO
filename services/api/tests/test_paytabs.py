"""Paying invoices through PayTabs (hosted payment page). The link is NOVARA's page, which asks PayTabs for a payment
page when the customer opens it (the link as the cart id); the result is read back with a transaction query using the
profile's server key, and only counts for the link it was made for. The customer's return and PayTabs' callback are
signed with the server key (HMAC-SHA256), and still only hints."""

from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlencode

import httpx
import pytest

from tests.test_accounting_extended import ACCT, OWNER, balances, o, posted_invoice, shop  # noqa: F401 (shop is a fixture)
from tests.test_online_payments import LINE, new_link

PROFILE, KEY = "98765", "SJN" + "9" * 9 + "-JD" + "M" * 7 + "-6" + "K" * 7


@pytest.fixture
def paytabs(app):
    """A stand-in for secure.paytabs.sa: payment pages asked for, and what each transaction says now."""
    calls: list[httpx.Request] = []
    transactions: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.host == "secure.paytabs.sa"
        if request.headers.get("authorization") != KEY:
            return httpx.Response(401, json={"code": 1, "message": "Authentication failed. Check authentication header."})
        body = json.loads(request.content)
        assert body["profile_id"] == int(PROFILE)
        if request.url.path == "/payment/request":
            ref = f"TST2{len(transactions) + 1:011d}"
            transactions[ref] = {"request": body, "status": "P"}
            return httpx.Response(200, json={"tran_ref": ref, "cart_id": body["cart_id"],
                                             "redirect_url": f"https://secure.paytabs.sa/payment/page/{ref}"})
        if request.url.path == "/payment/query":
            t = transactions.get(body["tran_ref"])
            if t is None:
                return httpx.Response(400, json={"code": 2, "message": "Transaction not found"})
            req = t["request"]
            return httpx.Response(200, json={
                "tran_ref": body["tran_ref"], "tran_type": "Sale", "cart_id": req["cart_id"],
                "cart_currency": req["cart_currency"], "cart_amount": str(req["cart_amount"]),
                "payment_result": {"response_status": t["status"], "response_code": "000",
                                   "response_message": "Authorised" if t["status"] == "A" else "Pending"}})
        return httpx.Response(404, json={"message": "not found"})

    def pay(ref: str, status: str = "A"):
        transactions[ref]["status"] = status

    app.state.payment_http = httpx.Client(transport=httpx.MockTransport(handler))
    yield {"calls": calls, "transactions": transactions, "pay": pay}
    app.state.payment_http.close()


def use_paytabs(api, s, **extra):
    r = api.req("PUT", OWNER, o(s, "/payments/settings"), json={
        "provider": "paytabs", "paytabs_profile_id": PROFILE, "paytabs_server_key": KEY, "paytabs_region": "sa"} | extra)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["provider"], out["has_paytabs_key"], out["paytabs_profile_id"], out["paytabs_region"]) == (
        "paytabs", True, PROFILE, "sa")
    assert KEY not in r.text                                                     # the key is never shown again
    return "/api/v1" + out["paytabs_callback_url"].split("/api/v1", 1)[1]


def page_path(url: str) -> str:
    return "/api/v1" + url.split("/api/v1", 1)[1]


def signed_return(fields: dict[str, str]) -> str:
    """The form PayTabs posts back: signed over the non-empty fields, sorted and URL-encoded."""
    clean = sorted((k, v) for k, v in fields.items() if v)
    signature = hmac.new(KEY.encode(), urlencode(clean).encode(), hashlib.sha256).hexdigest()
    return urlencode(fields | {"signature": signature})


FORM = {"content-type": "application/x-www-form-urlencoded"}


def test_a_paytabs_link_is_paid_when_paytabs_says_so(api, shop, client, paytabs):
    put = lambda body: api.req("PUT", OWNER, o(shop, "/payments/settings"), json=body)  # noqa: E731
    assert put({"provider": "paytabs"}).status_code == 422                       # needs the profile and the key
    assert put({"provider": "paytabs", "paytabs_profile_id": PROFILE}).status_code == 422
    assert put({"provider": "paytabs", "paytabs_profile_id": "12ab", "paytabs_server_key": KEY}).status_code == 422
    use_paytabs(api, shop)
    inv = posted_invoice(api, shop, [LINE | {"tax_id": shop["vat"]}])
    link = new_link(api, shop, inv).json()
    assert link["provider"] == "paytabs" and "/public/payments/paytabs/pt_" in link["url"]
    assert paytabs["calls"] == []                                                 # nothing asked of PayTabs yet

    opened = client.get(page_path(link["url"]), follow_redirects=False)
    assert opened.status_code == 303
    ref = next(iter(paytabs["transactions"]))
    assert opened.headers["location"] == f"https://secure.paytabs.sa/payment/page/{ref}"
    sent = paytabs["transactions"][ref]["request"]
    assert (sent["cart_id"], sent["cart_amount"], sent["cart_currency"], sent["tran_type"], sent["tran_class"]) == (
        link["id"], 1150.0, "SAR", "sale", "ecom")
    assert sent["return"].endswith(f"/paytabs/{link['url'].rsplit('/', 1)[1]}/return")
    assert sent["customer_details"]["name"] == "Riyadh Offices"

    back = page_path(link["url"]) + "/return"
    fields = {"tranRef": ref, "cartId": link["id"], "respStatus": "P", "respMessage": "Pending", "acquirerRRN": ""}
    forged = urlencode(fields | {"signature": "0" * 64})
    assert client.post(back, content=forged, headers=FORM).status_code == 400     # not signed with the key
    waiting = client.post(back, content=signed_return(fields), headers=FORM, follow_redirects=False)
    assert waiting.status_code == 200 and "لم يكتمل" in waiting.text            # still pending at PayTabs
    assert api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()["status"] == "posted"

    paytabs["pay"](ref)
    # The browser's own claim doesn't matter (respStatus could say anything): PayTabs is asked.
    paid = client.post(back, content=signed_return(fields | {"respStatus": "A"}), headers=FORM, follow_redirects=False)
    assert paid.status_code == 303 and paid.headers["location"].endswith("/public/payments/done?status=paid")
    after = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()
    assert (after["status"], after["amount_due"]) == ("paid", "0.00")
    payments = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}/payments")).json()
    assert [p["reference"] for p in payments] == [f"paytabs {ref}"]
    assert balances(api, shop)["1250"] == "1150.00"
    again = client.post(back, content=signed_return(fields | {"respStatus": "A"}), headers=FORM, follow_redirects=False)
    assert again.status_code == 303
    assert len(api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}/payments")).json()) == 1
    assert client.get(page_path(link["url"]), follow_redirects=False).status_code == 410


def test_a_result_only_pays_its_own_link_and_the_callback_is_checked(api, shop, client, paytabs):
    hook = use_paytabs(api, shop)
    small = posted_invoice(api, shop, [{"description": "Pin", "quantity": "1", "unit_price": "5"}])
    big = posted_invoice(api, shop, [LINE])
    a, b = new_link(api, shop, small).json(), new_link(api, shop, big).json()
    client.get(page_path(a["url"]), follow_redirects=False)
    ref = next(iter(paytabs["transactions"]))
    paytabs["pay"](ref)
    # The small link's transaction, replayed against the big link's return address, pays nothing.
    stolen = signed_return({"tranRef": ref, "cartId": b["id"], "respStatus": "A"})
    assert client.post(page_path(b["url"]) + "/return", content=stolen, headers=FORM).status_code == 400
    assert api.req("GET", ACCT, o(shop, f"/invoices/{big['id']}/payment-links")).json()[0]["status"] == "open"

    # The customer closed the page before coming back: PayTabs' callback tells, and PayTabs is asked again.
    body = json.dumps({"tran_ref": ref, "cart_id": a["id"], "cart_amount": "5.75", "cart_currency": "SAR",
                       "payment_result": {"response_status": "A"}}).encode()
    signature = hmac.new(KEY.encode(), body, hashlib.sha256).hexdigest()
    assert client.post(hook, content=body, headers={"signature": "1" * 64}).status_code == 400
    ok = client.post(hook, content=body, headers={"signature": signature})
    assert (ok.status_code, ok.json()["result"]) == (200, "paid")
    assert api.req("GET", ACCT, o(shop, f"/invoices/{small['id']}")).json()["status"] == "paid"
    assert json.loads(paytabs["calls"][-1].content)["tran_ref"] == ref
    assert client.post(hook, content=body, headers={"signature": signature}).json()["result"] == "duplicate"
    other = json.dumps({"tran_ref": "TST299", "cart_id": "someone-elses"}).encode()
    assert client.post(hook, content=other, headers={"signature": hmac.new(KEY.encode(), other, hashlib.sha256)
                                                     .hexdigest()}).json()["result"] == "not ours"


def test_what_paytabs_takes(api, shop, paytabs):
    use_paytabs(api, shop)
    try_ = api.req("POST", OWNER, o(shop, "/invoices"), json={"customer_id": shop["customer"], "issue_date": "2026-04-01",
                                                              "currency": "JPY", "exchange_rate": "0.025", "lines": [LINE]})
    api.req("POST", ACCT, o(shop, f"/invoices/{try_.json()['id']}/post"))
    refused = new_link(api, shop, try_.json())
    assert refused.status_code == 422 and "PayTabs can't charge in JPY" in refused.json()["detail"]
    assert api.req("PUT", OWNER, o(shop, "/payments/settings"),
                   json={"provider": "paytabs", "paytabs_region": "mars"}).status_code == 422
