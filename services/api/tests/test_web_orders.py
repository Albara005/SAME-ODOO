"""Order requests from published sites: received with a data key, turned into quotations by a person."""

from __future__ import annotations

import pytest

from novara.modules.sales import web_orders

OWNER, SALES, VIEWER = "owner@weborders.example.com", "sales@weborders.example.com", "viewer@weborders.example.com"


@pytest.fixture
def project(api):
    for e in (OWNER, SALES, VIEWER):
        api.signup(e)
    org = api.create_org(OWNER, "web-orders-co")
    api.add_member(OWNER, org, SALES, "sales")
    api.add_member(OWNER, org, VIEWER, "viewer")
    vat = api.req("POST", OWNER, f"/orgs/{org}/taxes", json={"name": "VAT", "rate": "15"}).json()["id"]
    coffee = api.req("POST", OWNER, f"/orgs/{org}/products", json={
        "name": "Arabic coffee", "sku": "CF", "sale_price": "100", "default_tax_id": vat}).json()["id"]
    project = api.req("POST", OWNER, f"/orgs/{org}/studio/projects", json={"name": "Coffee Shop", "slug": "coffee"}).json()["id"]
    return {"org": org, "product": coffee, "project": project}


def key_with(api, s, scopes, user=OWNER):
    return api.req("POST", user, f"/orgs/{s['org']}/studio/projects/{s['project']}/data-keys",
                   json={"name": "Shop", "scopes": scopes, "require_proof": False})  # proofs: test_proof_of_work.py


def order(client, key, body=None, **extra):
    headers = {"X-Novara-Key": key}
    payload = body or {"name": "Hala Stores", "email": "buyer@hala.example.com", "phone": "+966500000000",
                       "note": "Deliver to the Olaya branch", "lines": [{"product_id": extra.get("product"), "quantity": 2}]}
    return client.post("/api/v1/public/orders", json=payload, headers=headers)


@pytest.fixture
def keyed(api, project):
    k = key_with(api, project, ["catalog", "orders"])
    assert k.status_code == 201, k.text
    return {**project, "key": k.json()["key"]}


def test_a_site_sends_an_order_request_and_sales_makes_it_a_quotation(api, client, keyed):
    s = keyed
    sent = order(client, s["key"], product=s["product"])
    assert sent.status_code == 201, sent.text
    body = sent.json()
    assert body["reference"].startswith("WEB-") and body["total"] == "230.00"  # 2 x 100 + 15% VAT, from the catalog
    assert sent.headers["Access-Control-Allow-Origin"] == "*"

    listed = api.req("GET", SALES, f"/orgs/{s['org']}/web-orders", params={"status": "new"}).json()
    assert [(w["number"], w["customer_name"], w["total"], w["project_name"]) for w in listed] == [
        (body["reference"], "Hala Stores", "230.00", "Coffee Shop")]
    assert listed[0]["lines"][0]["name"] == "Arabic coffee"

    converted = api.req("POST", SALES, f"/orgs/{s['org']}/web-orders/{listed[0]['id']}/convert")
    assert converted.status_code == 200, converted.text
    so = api.req("GET", SALES, f"/orgs/{s['org']}/sales-orders/{converted.json()['sales_order_id']}").json()
    assert (so["status"], so["customer_name"], so["total"]) == ("quotation", "Hala Stores", "230.00")
    assert body["reference"] in so["notes"]
    assert api.req("POST", SALES, f"/orgs/{s['org']}/web-orders/{listed[0]['id']}/convert").status_code == 409

    # The same buyer again: the existing customer is used.
    again = order(client, s["key"], product=s["product"]).json()
    pending = api.req("GET", SALES, f"/orgs/{s['org']}/web-orders", params={"status": "new"}).json()
    assert [w["number"] for w in pending] == [again["reference"]]
    second = api.req("POST", SALES, f"/orgs/{s['org']}/web-orders/{pending[0]['id']}/convert").json()
    assert second["customer_id"] == converted.json()["customer_id"]


def test_only_keys_allowed_to_take_orders_can_send_them(api, client, project):
    s = project
    catalog_only = key_with(api, s, ["catalog"]).json()["key"]
    assert order(client, catalog_only, product=s["product"]).status_code == 401
    assert order(client, "nvpk_nope", product=s["product"]).status_code == 401
    # Taking orders is a sales decision: the key's creator needs sales.write.
    assert "orders" in key_with(api, s, ["orders"]).json()["scopes"]
    assert "catalog" in key_with(api, s, ["orders"]).json()["scopes"]   # orders come with the catalog


def test_bad_or_abusive_requests_are_refused(api, client, keyed, monkeypatch):
    s = keyed
    no_contact = {"name": "X", "lines": [{"product_id": s["product"], "quantity": 1}]}
    assert order(client, s["key"], body=no_contact).status_code == 422
    unknown = {"name": "X", "email": "x@example.com", "lines": [{"product_id": "nope", "quantity": 1}]}
    assert order(client, s["key"], body=unknown).status_code == 422
    twice = {"name": "X", "email": "x@example.com", "lines": [{"product_id": s["product"], "quantity": 1}] * 2}
    assert order(client, s["key"], body=twice).status_code == 422
    huge = {"name": "X", "email": "x@example.com", "lines": [{"product_id": s["product"], "quantity": 100000}]}
    bad = order(client, s["key"], body=huge)
    assert bad.status_code == 422 and bad.headers["Access-Control-Allow-Origin"] == "*"

    # A bot fills the hidden field: answered as if accepted, and dropped.
    bot = {"name": "Bot", "email": "bot@example.com", "website": "http://spam.example",
           "lines": [{"product_id": s["product"], "quantity": 1}]}
    assert order(client, s["key"], body=bot).status_code == 201
    assert api.req("GET", SALES, f"/orgs/{s['org']}/web-orders").json() == []

    monkeypatch.setattr(web_orders, "IP_LIMIT", 2)
    for _ in range(2):
        assert order(client, s["key"], product=s["product"]).status_code == 201
    limited = order(client, s["key"], product=s["product"])
    assert limited.status_code == 429 and limited.headers["Access-Control-Allow-Origin"] == "*"

    pending = api.req("GET", SALES, f"/orgs/{s['org']}/web-orders").json()
    dismissed = api.req("POST", SALES, f"/orgs/{s['org']}/web-orders/{pending[0]['id']}/dismiss")
    assert dismissed.json()["status"] == "dismissed"
    assert api.req("GET", VIEWER, f"/orgs/{s['org']}/web-orders").status_code == 403

    preflight = client.options("/api/v1/public/orders")
    assert "POST" in preflight.headers["Access-Control-Allow-Methods"]
    assert "Content-Type" in preflight.headers["Access-Control-Allow-Headers"]
