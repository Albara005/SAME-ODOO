"""Refunds through the payment provider: a credit note on an invoice paid online goes back through the same payment
(at most what that payment took less what already went back), out of Online Payments Clearing. The refund is booked
before the provider is asked and dropped if the provider refuses, so money never moves without its entry."""

from __future__ import annotations

import json
from datetime import date
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from novara.payments.providers import HyperPay, Moyasar, PayTabs, ProviderError, Stripe
from tests.test_accounting_extended import ACCT, OWNER, balances, credit, o, posted_invoice, shop  # noqa: F401
from tests.test_hyperpay import CARD, MADA, TOKEN, hyperpay, page_path, use_hyperpay  # noqa: F401 (hyperpay is a fixture)
from tests.test_online_payments import LINE, new_link


TODAY = date.today().isoformat()          # the provider's payment is dated the day it comes in


def paid_online(api, client, s, line=None):
    inv = posted_invoice(api, s, [line or LINE | {"tax_id": s["vat"]}])
    page = urlsplit(new_link(api, s, inv).json()["url"]).path
    assert client.post(page + "/pay", follow_redirects=False).status_code == 303
    return api.req("GET", ACCT, o(s, f"/invoices/{inv['id']}")).json()


def refund(api, s, note, amount, method="online", **kw):
    return api.req("POST", ACCT, o(s, f"/credit-notes/{note['id']}/refunds"),
                   json={"amount": amount, "refund_date": TODAY, "method": method}, **kw)


def test_a_credit_note_goes_back_through_the_payment(api, shop, client):
    s = shop
    assert api.req("PUT", OWNER, o(s, "/payments/settings"), json={"provider": "test"}).status_code == 200
    inv = paid_online(api, client, s)
    assert balances(api, s)["1250"] == "1150.00"
    note = credit(api, s, inv, [{"invoice_line_id": inv["lines"][0]["id"], "quantity": "1"}], date=TODAY).json()
    assert note["refundable"] == "1150.00"

    first = refund(api, s, note, "400", headers={"Idempotency-Key": "online-1"})
    assert first.status_code == 201, first.text
    assert first.json()["method"] == "online" and first.json()["reference"].startswith("test re_novaratest_")
    again = refund(api, s, note, "400", headers={"Idempotency-Key": "online-1"})
    assert (again.status_code, again.json()["id"]) == (200, first.json()["id"])     # the same refund, once
    assert balances(api, s)["1250"] == "750.00"                                     # out of the clearing account

    too_much = refund(api, s, note, "800")
    assert too_much.status_code == 422 and "750.00" in too_much.json()["detail"]
    assert refund(api, s, note, "750", method="bank").status_code == 201            # the rest by bank, as before
    actions = [a["action"] for a in api.req("GET", OWNER, o(s, "/audit-logs")).json()]
    assert "credit_note.refunded_online" in actions


def test_only_what_was_paid_online_goes_back_online(api, shop, client):
    s = shop
    assert api.req("PUT", OWNER, o(s, "/payments/settings"), json={"provider": "test"}).status_code == 200
    by_bank = posted_invoice(api, s, [LINE])
    api.req("POST", ACCT, o(s, f"/invoices/{by_bank['id']}/payments"),
            json={"amount": "1000", "payment_date": "2026-04-02", "method": "bank"})
    note = credit(api, s, by_bank, [{"invoice_line_id": by_bank["lines"][0]["id"], "quantity": "1"}]).json()
    refused = refund(api, s, note, "100")
    assert refused.status_code == 409 and "wasn't paid online" in refused.json()["detail"]
    assert api.req("GET", ACCT, o(s, f"/credit-notes/{note['id']}/refunds")).json() == []


def test_a_refused_refund_books_nothing(api, shop, client, hyperpay):
    s = shop
    use_hyperpay(api, s)
    inv = posted_invoice(api, s, [LINE])
    link = new_link(api, s, inv).json()
    client.get(page_path(link["url"]), params={"brand": "mada"})
    cid = next(iter(hyperpay["checkouts"]))
    hyperpay["pay"](cid)
    client.get(page_path(link["url"]) + "/result", params={"brand": "mada", "id": cid}, follow_redirects=False)
    inv = api.req("GET", ACCT, o(s, f"/invoices/{inv['id']}")).json()
    note = credit(api, s, inv, [{"invoice_line_id": inv["lines"][0]["id"], "quantity": "1"}], date=TODAY).json()

    # HyperPay takes refunds as paymentType RF through the entity the payment came in by (mada here).
    asked: list[dict] = []
    answer = {"code": "800.100.152"}                                             # declined first

    def refunds(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.startswith("/v1/payments/"):
            asked.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
            return httpx.Response(200, json={"id": "8ac7REFUND0000001", "result": answer | {"description": "x"},
                                             "amount": asked[-1]["amount"], "currency": "SAR"})
        return previous(request)

    previous = hyperpay_handler(s)
    replace_transport(s, refunds)
    declined = refund(api, s, note, "200")
    assert declined.status_code == 502 and "HyperPay didn't refund" in declined.json()["detail"]
    assert api.req("GET", ACCT, o(s, f"/credit-notes/{note['id']}/refunds")).json() == []
    assert balances(api, s)["1250"] == "1000.00"                                    # nothing booked
    answer["code"] = "000.100.110"
    ok = refund(api, s, note, "200")
    assert ok.status_code == 201 and ok.json()["reference"] == "hyperpay 8ac7REFUND0000001"
    assert (asked[-1]["entityId"], asked[-1]["amount"], asked[-1]["paymentType"]) == (MADA, "200.00", "RF")
    assert balances(api, s)["1250"] == "800.00"


APP: dict = {}


@pytest.fixture(autouse=True)
def _remember_app(app):
    APP["app"] = app


def hyperpay_handler(s):
    return APP["app"].state.payment_http._transport.handler


def replace_transport(s, handler):
    APP["app"].state.payment_http = httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- what each provider is asked


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_how_each_provider_is_asked_to_refund():
    seen: list[httpx.Request] = []

    def stripe(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "re_1", "object": "refund", "status": "succeeded"})
    assert Stripe("sk_test_x", client_for(stripe)).refund("pi_3Px", 40000, "SAR", key="k1") == "re_1"
    form = parse_qs(seen[-1].content.decode())
    assert (seen[-1].url.path, form["payment_intent"], form["amount"]) == ("/v1/refunds", ["pi_3Px"], ["40000"])
    assert seen[-1].headers["idempotency-key"] == "novara-refund-k1"
    with pytest.raises(ProviderError, match="no payment intent"):
        Stripe("sk_test_x", client_for(stripe)).refund("cs_test_1", 100, "SAR", key="k2")

    state = {"refunded": 0}

    def moyasar(request):
        seen.append(request)
        if request.method == "POST":
            state["refunded"] += json.loads(request.content)["amount"]
        return httpx.Response(200, json={"id": "pay_1", "status": "refunded" if state["refunded"] else "paid",
                                         "refunded": state["refunded"]})
    assert Moyasar("sk_test_x", client_for(moyasar)).refund("pay_1", 40000, "SAR", key="k") == "pay_1#refund40000"
    assert seen[-1].url.path == "/v1/payments/pay_1/refund"

    def hp(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "8ac7REFUND0000009", "result": {"code": "000.100.110"}})
    assert HyperPay(TOKEN, {"card": CARD, "mada": MADA}, False, "https://x", client_for(hp)).refund(
        "8ac7PAYMENT000001", 1050, "SAR", key="k", detail="card") == "8ac7REFUND0000009"
    assert parse_qs(seen[-1].content.decode())["entityId"] == [CARD]

    def paytabs(request):
        seen.append(request)
        return httpx.Response(200, json={"tran_ref": "TST2000000099", "tran_type": "Refund",
                                         "payment_result": {"response_status": "A", "response_message": "Authorised"}})
    assert PayTabs("98765", "SK" + "X" * 30, "sa", "https://x", client_for(paytabs)).refund(
        "TST2000000001", 12345, "KWD", key="k") == "TST2000000099"
    body = json.loads(seen[-1].content)
    assert (body["tran_type"], body["tran_ref"], body["cart_amount"], body["cart_currency"]) == (
        "refund", "TST2000000001", 12.345, "KWD")
