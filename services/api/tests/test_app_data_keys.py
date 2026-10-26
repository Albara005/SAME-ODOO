"""Publishable data keys: a published site reads a fixed public view of the catalog with a key that
only someone allowed to publish (and to read that data) can create, and anyone editing the app can revoke."""

from __future__ import annotations

OWNER, DEV, WH = "owner@keys.example.com", "dev@keys.example.com", "wh@keys.example.com"


def test_catalog_key_lifecycle(api, client):
    for e in (OWNER, DEV, WH):
        api.signup(e)
    org = api.create_org(OWNER, "keys-co")
    api.add_member(OWNER, org, DEV, "developer")
    api.add_member(OWNER, org, WH, "warehouse")
    vat = api.req("POST", OWNER, f"/orgs/{org}/taxes", json={"name": "VAT", "rate": "15"}).json()["id"]
    chair = api.req("POST", OWNER, f"/orgs/{org}/products", json={"name": "Chair", "sku": "CH", "sale_price": "100",
                                                                 "default_tax_id": vat}).json()["id"]
    api.req("POST", OWNER, f"/orgs/{org}/products", json={"name": "Desk", "sku": "DK", "sale_price": "300"})
    api.req("POST", OWNER, f"/orgs/{org}/products", json={"name": "Setup", "kind": "service", "sale_price": "50"})
    api.req("POST", OWNER, f"/orgs/{org}/inventory/receipts", json={
        "move_date": "2026-02-01", "lines": [{"product_id": chair, "quantity": "2", "unit_cost": "60"}]})
    project = api.req("POST", DEV, f"/orgs/{org}/studio/projects", json={"name": "Shop", "slug": "keys-shop"}).json()["id"]
    url = f"/orgs/{org}/studio/projects/{project}/data-keys"

    assert api.req("POST", DEV, url, json={"name": "site", "scopes": ["catalog"]}).status_code == 403   # no apps.deploy
    assert api.req("POST", OWNER, url, json={"name": "site", "scopes": ["prices-and-costs"]}).status_code == 422
    created = api.req("POST", OWNER, url, json={"name": "Shop site", "scopes": ["availability"]})
    assert created.status_code == 201, created.text
    key = created.json()["key"]
    assert key.startswith("nvpk_") and created.json()["scopes"] == ["availability", "catalog"]
    listed = api.req("GET", DEV, url).json()
    assert [k["key"] for k in listed] == [None] and listed[0]["hint"] == key[:12]   # shown once only

    r = client.get("/api/v1/public/catalog", params={"key": key})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*"
    body = r.json()
    assert (body["organization"], body["currency"]) == ("Keys-Co", "SAR")
    by_name = {p["name"]: p for p in body["products"]}
    assert (by_name["Chair"]["price"], by_name["Chair"]["price_with_tax"], by_name["Chair"]["available"]) == ("100.00", "115.00", True)
    assert by_name["Desk"]["available"] is False and by_name["Setup"]["available"] is True
    assert set(by_name["Chair"]) == {"id", "name", "sku", "description", "unit", "kind", "price", "tax_rate",
                                     "price_with_tax", "available"}                    # no costs, no quantities
    assert client.get("/api/v1/public/catalog", headers={"X-Novara-Key": key}).status_code == 200
    preflight = client.options("/api/v1/public/catalog", headers={"Origin": "null", "Access-Control-Request-Method": "GET",
                                                                  "Access-Control-Request-Headers": "x-novara-key"})
    assert preflight.status_code == 204 and "X-Novara-Key" in preflight.headers["access-control-allow-headers"]
    assert api.req("GET", DEV, url).json()[0]["last_used_at"] is not None

    key_id = listed[0]["id"]
    assert api.req("DELETE", WH, f"{url}/{key_id}").status_code == 403
    assert api.req("DELETE", DEV, f"{url}/{key_id}").json()["revoked"] is True
    gone = client.get("/api/v1/public/catalog", params={"key": key})
    assert gone.status_code == 401 and gone.headers["access-control-allow-origin"] == "*"
    assert client.get("/api/v1/public/catalog", params={"key": "nvpk_guess"}).status_code == 401
    assert client.get("/api/v1/public/catalog").status_code == 401


def test_catalog_only_key_shows_no_stock_and_keys_stay_in_their_organization(api, client):
    api.signup(OWNER)
    a = api.create_org(OWNER, "keys-a")
    b = api.create_org(OWNER, "keys-b")
    api.req("POST", OWNER, f"/orgs/{a}/products", json={"name": "A product", "sale_price": "10"})
    api.req("POST", OWNER, f"/orgs/{b}/products", json={"name": "B product", "sale_price": "20"})
    project = api.req("POST", OWNER, f"/orgs/{a}/studio/projects", json={"name": "A", "slug": "keys-a-site"}).json()["id"]
    key = api.req("POST", OWNER, f"/orgs/{a}/studio/projects/{project}/data-keys",
                  json={"name": "a", "scopes": ["catalog"]}).json()["key"]
    products = client.get("/api/v1/public/catalog", params={"key": key}).json()["products"]
    assert [p["name"] for p in products] == ["A product"] and "available" not in products[0]
    assert api.req("GET", OWNER, f"/orgs/{b}/studio/projects/{project}/data-keys").status_code == 404
