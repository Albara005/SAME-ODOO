"""Customers, products and taxes."""

from __future__ import annotations

import pytest

OWNER, SALES, ACCT, VIEWER = "owner@acme.example.com", "sales@acme.example.com", "acct@acme.example.com", "viewer@acme.example.com"


@pytest.fixture
def org(api):
    for e in (OWNER, SALES, ACCT, VIEWER):
        api.signup(e)
    org_id = api.create_org(OWNER, "acme")
    for e, role in ((SALES, "sales"), (ACCT, "accountant"), (VIEWER, "viewer")):
        assert api.add_member(OWNER, org_id, e, role).status_code == 201
    return org_id


def test_customer_crud_and_search(api, org):
    r = api.req("POST", SALES, f"/orgs/{org}/customers",
                json={"name": "شركة النخبة", "email": "Info@Nokhba.SA", "tax_number": "300000000000003", "country": "SA"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["email"] == "info@nokhba.sa"
    api.req("POST", SALES, f"/orgs/{org}/customers", json={"name": "Globex", "kind": "company"})

    found = api.req("GET", SALES, f"/orgs/{org}/customers", params={"q": "النخبة"}).json()
    assert [c["id"] for c in found] == [cid]
    assert len(api.req("GET", SALES, f"/orgs/{org}/customers", params={"q": "3000000"}).json()) == 1

    r = api.req("PATCH", SALES, f"/orgs/{org}/customers/{cid}", json={"city": "الرياض"})
    assert r.json()["city"] == "الرياض"
    assert api.req("PATCH", SALES, f"/orgs/{org}/customers/{cid}", json={"name": None}).status_code == 422

    assert api.req("DELETE", SALES, f"/orgs/{org}/customers/{cid}").status_code == 204
    assert api.req("GET", SALES, f"/orgs/{org}/customers/{cid}").status_code == 404


def test_customer_permissions(api, org):
    assert api.req("GET", VIEWER, f"/orgs/{org}/customers").status_code == 403
    assert api.req("POST", ACCT, f"/orgs/{org}/customers", json={"name": "x"}).status_code == 403
    assert api.req("GET", ACCT, f"/orgs/{org}/customers").status_code == 200


def test_customers_are_isolated_between_organizations(api, org):
    api.signup("other@globex.example.com")
    other = api.create_org("other@globex.example.com", "globex")
    cid = api.req("POST", OWNER, f"/orgs/{org}/customers", json={"name": "Acme customer"}).json()["id"]
    assert api.req("GET", "other@globex.example.com", f"/orgs/{other}/customers").json() == []
    assert api.req("GET", "other@globex.example.com", f"/orgs/{other}/customers/{cid}").status_code == 404
    assert api.req("PATCH", "other@globex.example.com", f"/orgs/{other}/customers/{cid}", json={"name": "x"}).status_code == 404


def test_product_crud_sku_and_price(api, org):
    vat = api.req("POST", ACCT, f"/orgs/{org}/taxes", json={"name": "VAT 15%", "rate": "15"}).json()
    r = api.req("POST", OWNER, f"/orgs/{org}/products",
                json={"name": "Laptop", "sku": "LP-1", "sale_price": "3499.99", "default_tax_id": vat["id"]})
    assert r.status_code == 201, r.text
    assert r.json()["sale_price"] == "3499.99"
    assert api.req("POST", OWNER, f"/orgs/{org}/products", json={"name": "Dup", "sku": "LP-1", "sale_price": 1}).status_code == 409
    assert api.req("POST", OWNER, f"/orgs/{org}/products", json={"name": "Bad", "sale_price": -1}).status_code == 422
    # SKUs are unique per organization, not globally.
    api.signup("other@globex.example.com")
    other = api.create_org("other@globex.example.com", "globex")
    assert api.req("POST", "other@globex.example.com", f"/orgs/{other}/products",
                   json={"name": "Laptop", "sku": "LP-1", "sale_price": 1}).status_code == 201
    # Another organization's tax can't be referenced.
    assert api.req("POST", "other@globex.example.com", f"/orgs/{other}/products",
                   json={"name": "X", "sale_price": 1, "default_tax_id": vat["id"]}).status_code == 422
    assert api.req("POST", SALES, f"/orgs/{org}/products", json={"name": "Nope", "sale_price": 1}).status_code == 403


def test_tax_rate_is_fixed_after_creation(api, org):
    tax = api.req("POST", ACCT, f"/orgs/{org}/taxes", json={"name": "VAT", "rate": "15"}).json()
    r = api.req("PATCH", ACCT, f"/orgs/{org}/taxes/{tax['id']}", json={"name": "VAT standard", "rate": "5"})
    assert r.status_code == 200
    assert r.json()["rate"] == "15"
    assert r.json()["name"] == "VAT standard"
    assert api.req("POST", ACCT, f"/orgs/{org}/taxes", json={"name": "Bad", "rate": "101"}).status_code == 422


def test_new_organization_has_chart_of_accounts(api, org):
    accounts = api.req("GET", ACCT, f"/orgs/{org}/accounting/accounts").json()
    keys = {a["system_key"] for a in accounts if a["system_key"]}
    assert {"receivable", "sales", "vat_output", "bank", "cash"} <= keys
    assert api.req("GET", SALES, f"/orgs/{org}/accounting/accounts").status_code == 403


def test_unsupported_currency_rejected(api, org):
    r = api.req("POST", OWNER, "/orgs", json={"name": "Bad", "slug": "bad-cur", "default_currency": "XYZ"})
    assert r.status_code == 422
