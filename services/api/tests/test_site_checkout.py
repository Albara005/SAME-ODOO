"""Paying on the published site: with the "checkout" scope a site sends the order with pay=true and sends the visitor
to the payment page. When the provider confirms the money, the order becomes the customer's posted, paid invoice
(found by email or created); until then it waits as "awaiting payment"."""

from __future__ import annotations

import pytest

from tests.test_einvoicing import ei, zatca  # noqa: F401 (fixtures)
from tests.test_web_orders import OWNER, SALES, key_with, order, project  # noqa: F401 (project is a fixture)

PAY = {"name": "Hala Stores", "email": "buyer@hala.example.com", "phone": "+966500000000", "pay": True}


@pytest.fixture
def shop(api, project):
    assert api.req("PUT", OWNER, f"/orgs/{project['org']}/payments/settings", json={"provider": "test"}).status_code == 200
    k = key_with(api, project, ["catalog", "orders", "checkout"])
    assert k.status_code == 201, k.text
    return {**project, "key": k.json()["key"]}


def checkout(client, s, quantity=2, **extra):
    return order(client, s["key"], PAY | {"lines": [{"product_id": s["product"], "quantity": quantity}]} | extra)


def pay(client, url: str):
    session = url.rsplit("/", 1)[1]
    return client.post(f"/api/v1/public/payments/test/{session}/pay", follow_redirects=False)


def test_a_paid_site_order_becomes_a_posted_paid_invoice(api, client, shop):
    s, org = shop, f"/orgs/{shop['org']}"
    sent = checkout(client, s)
    assert sent.status_code == 201, sent.text
    body = sent.json()
    assert body["total"] == "230.00" and "/api/v1/public/payments/test/" in body["payment_url"]
    waiting = api.req("GET", SALES, f"{org}/web-orders", params={"status": "open"}).json()
    assert [(w["number"], w["status"]) for w in waiting] == [(body["reference"], "awaiting_payment")]
    assert api.req("GET", OWNER, f"{org}/invoices").json() == []                       # nothing posted before the money

    assert pay(client, body["payment_url"]).status_code == 303
    paid = api.req("GET", SALES, f"{org}/web-orders", params={"status": "paid"}).json()
    assert len(paid) == 1 and paid[0]["invoice_number"].startswith("INV-")
    inv = api.req("GET", OWNER, f"{org}/invoices/{paid[0]['invoice_id']}").json()
    assert (inv["status"], inv["total"], inv["amount_due"], inv["customer_name"]) == ("paid", "230.00", "0.00", "Hala Stores")
    assert [(l["description"], l["quantity"], l["unit_price"]) for l in inv["lines"]] == [("Arabic coffee", "2", "100")]
    customers = api.req("GET", OWNER, f"{org}/customers").json()
    assert [(c["name"], c["email"]) for c in customers] == [("Hala Stores", "buyer@hala.example.com")]
    tb = {l["code"]: l["balance"] for l in api.req("GET", OWNER, f"{org}/accounting/trial-balance").json()["lines"]}
    assert (tb["1250"], tb.get("1200", "0.00")) == ("230.00", "0.00")                  # in the provider's hands; nothing owed
    notes = api.req("GET", SALES, f"{org}/notifications").json()
    assert any(body["reference"] in n["title"] for n in notes)                         # sales hears of it
    assert pay(client, body["payment_url"]).status_code == 410                         # the page is done

    # A second order from the same buyer finds the customer by email.
    again = checkout(client, s, quantity=1).json()
    pay(client, again["payment_url"])
    assert len(api.req("GET", OWNER, f"{org}/customers").json()) == 1


def test_an_order_dismissed_before_the_money_comes_is_not_invoiced(api, client, shop):
    s, org = shop, f"/orgs/{shop['org']}"
    body = checkout(client, s).json()
    w = api.req("GET", SALES, f"{org}/web-orders", params={"status": "open"}).json()[0]
    assert api.req("POST", SALES, f"{org}/web-orders/{w['id']}/dismiss").status_code == 200
    assert pay(client, body["payment_url"]).status_code == 410                         # its link was cancelled
    assert api.req("GET", OWNER, f"{org}/invoices").json() == []


def test_what_checkout_needs(api, client, project):
    s = project
    plain = key_with(api, s, ["catalog", "orders"]).json()["key"]
    refused = order(client, plain, PAY | {"lines": [{"product_id": s["product"], "quantity": 1}]})
    assert refused.status_code == 403 and "payments" in refused.json()["detail"]
    assert key_with(api, s, ["catalog", "orders", "checkout"], user=SALES).status_code == 403   # needs invoices.post too
    key = key_with(api, s, ["checkout"]).json()
    assert key["scopes"] == ["catalog", "checkout", "orders"]                           # checkout takes orders
    none = order(client, key["key"], PAY | {"lines": [{"product_id": s["product"], "quantity": 1}]})
    assert none.status_code == 409 and "online payments" in none.json()["detail"]
    assert api.req("GET", SALES, f"/orgs/{s['org']}/web-orders", params={"status": "open"}).json() == []


def test_with_e_invoicing_on_the_paid_order_is_a_signed_simplified_invoice(api, client, ei, zatca, app):
    from tests.test_einvoicing import OWNER as EOWNER, onboard, stored, u

    onboard(api, ei)
    assert api.req("PUT", EOWNER, u(ei, "/payments/settings"), json={"provider": "test"}).status_code == 200
    product = api.req("POST", EOWNER, u(ei, "/products"), json={"name": "قهوة عربية", "sku": "CF", "sale_price": "100",
                                                                "default_tax_id": ei["vat"]}).json()["id"]
    project = api.req("POST", EOWNER, u(ei, "/studio/projects"), json={"name": "Coffee Shop", "slug": "coffee"}).json()["id"]
    key = api.req("POST", EOWNER, u(ei, f"/studio/projects/{project}/data-keys"),
                  json={"name": "Shop", "scopes": ["checkout"], "require_proof": False}).json()["key"]
    body = order(client, key, PAY | {"lines": [{"product_id": product, "quantity": 1}]}).json()
    assert pay(client, body["payment_url"]).status_code == 303
    paid = api.req("GET", EOWNER, u(ei, "/web-orders"), params={"status": "paid"}).json()[0]
    doc = stored(app, ei, paid["invoice_number"])
    assert (doc.kind, doc.type_code, doc.icv) == ("simplified", "388", 1)            # sent to ZATCA by the worker
