"""The first-run setup: a new organization says what it does and what it wants to run, and gets its menu, its
VAT, sample data to explore with and, for a shop, an online store on its catalog that takes orders at once."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from tests.test_proof_of_work import solved

OWNER, CLERK = "owner@setup.example.com", "clerk@setup.example.com"


def new_org(api, slug="nukhba"):
    for e in (OWNER, CLERK):
        if e not in api.tokens:
            api.signup(e)
    return api.create_org(OWNER, slug)


def setup(api, org, **body):
    return api.req("POST", OWNER, f"/orgs/{org}/setup", json={"business_type": "store", "apps": [], **body})


def test_a_shop_is_set_up_with_its_store_online(api, client):
    org = new_org(api)
    fresh = api.req("GET", OWNER, f"/orgs/{org}").json()
    assert (fresh["setup_completed_at"], fresh["apps"]) == (None, None)
    catalog = api.req("GET", OWNER, f"/orgs/{org}/setup").json()
    store_apps = next(t["apps"] for t in catalog["business_types"] if t["key"] == "store")
    assert "inventory" in store_apps and catalog["completed"] is False
    assert {"code": "SA", "currency": "SAR", "vat_rate": "15"} in catalog["countries"]

    assert setup(api, org, business_type="farm", apps=store_apps).status_code == 422
    assert setup(api, org, apps=["invoices", "spaceships"]).status_code == 422
    done = setup(api, org, apps=store_apps, country="SA", vat_number="300000000000003", store=True,
                 store_name="متجر النخبة")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["created"] == {"taxes": 1, "products": 4, "customers": 2, "services": 0, "projects": 0, "stock": 4}
    assert body["organization"]["apps"][:3] == ["customers", "products", "sales"]           # in menu order
    assert (body["organization"]["business_type"], body["organization"]["vat_number"]) == ("store", "300000000000003")
    assert body["organization"]["setup_completed_at"] and body["notes"] == []
    assert api.req("GET", OWNER, f"/orgs/{org}").json()["setup_completed_at"]               # the wizard is done
    assert setup(api, org, apps=store_apps).status_code == 409                              # once

    # The store is published and wired to the catalog with a public key that can't do more than that.
    page = client.get(urlsplit(body["store_url"]).path)
    assert page.status_code == 200 and "متجر النخبة" in page.text
    config = client.get(urlsplit(body["store_url"]).path + "config.js").text
    key = re.search(r'key: "(nvpk_[\w-]+)"', config).group(1)
    assert 'api: "http://testserver"' in config
    products = client.get("/api/v1/public/catalog", headers={"X-Novara-Key": key}).json()["products"]
    shirt = next(p for p in products if p["name"] == "قميص قطني")
    assert (shirt["price"], shirt["price_with_tax"]) == ("79.00", "90.85")                  # with the 15% VAT

    # A visitor orders; it waits in Sales.
    unproven = client.post("/api/v1/public/orders", headers={"X-Novara-Key": key},
                           json={"name": "زائر", "phone": "+966500000001", "lines": [{"product_id": shirt["id"], "quantity": 2}]})
    assert unproven.status_code == 403
    ordered = client.post("/api/v1/public/orders", headers={"X-Novara-Key": key},
                          json={"name": "زائر", "phone": "+966500000001", "proof": solved(client, key),
                                "lines": [{"product_id": shirt["id"], "quantity": 2}]})
    assert ordered.status_code == 201, ordered.text
    assert ordered.json()["total"] == "181.70"
    assert client.get("/api/v1/public/catalog", headers={"X-Novara-Key": key}).status_code == 200
    projects = api.req("GET", OWNER, f"/orgs/{org}/studio/projects").json()
    assert [(p["name"], p["template"]) for p in projects] == [("متجر النخبة", "store")]
    balances = {l["code"]: l["balance"] for l in
                api.req("GET", OWNER, f"/orgs/{org}/accounting/trial-balance").json()["lines"]}
    assert balances["1400"] == "13290.00"                       # 25 of each at 60% of their 886 in prices


def test_other_businesses_get_what_they_run(api):
    org = new_org(api, "salon")
    done = setup(api, org, business_type="appointments", apps=["customers", "bookings", "invoices"], country="AE")
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["organization"]["default_currency"], body["created"]["services"]) == ("AED", 3)
    services = api.req("GET", OWNER, f"/orgs/{org}/bookings/services").json()
    assert sorted(s["name"] for s in services) == ["جلسة عناية", "قص شعر", "كشف عام"]
    taxes = api.req("GET", OWNER, f"/orgs/{org}/taxes").json()
    assert [t["rate"] for t in taxes] == ["5"]

    org = new_org(api, "consult")
    done = setup(api, org, business_type="services", apps=["customers", "invoices", "projects"], country="KW",
                 sample_data=True)
    assert done.json()["created"] | {} == {"taxes": 0, "products": 3, "customers": 2, "services": 0, "projects": 1,
                                           "stock": 0}                                     # no VAT in Kuwait
    assert [p["name"] for p in api.req("GET", OWNER, f"/orgs/{org}/projects").json()] == ["مشروع تجريبي"]

    org = new_org(api, "bare")
    done = setup(api, org, business_type="other", apps=["invoices"], sample_data=False)
    assert done.json()["created"] == {"taxes": 1}
    assert api.req("GET", OWNER, f"/orgs/{org}/products").json() == []


def test_skipping_changing_the_apps_and_who_may(api):
    org = new_org(api, "later")
    assert api.add_member(OWNER, org, CLERK, "sales").status_code == 201
    assert api.req("POST", CLERK, f"/orgs/{org}/setup", json={"business_type": "store", "apps": ["sales"]}).status_code == 403
    assert api.req("POST", CLERK, f"/orgs/{org}/setup/skip").status_code == 403
    skipped = api.req("POST", OWNER, f"/orgs/{org}/setup/skip").json()
    assert skipped["setup_completed_at"] and skipped["apps"] is None                        # every app on the menu
    assert setup(api, org, apps=["sales"]).status_code == 409

    assert api.req("PATCH", OWNER, f"/orgs/{org}", json={"apps": ["invoices", "ufo"]}).status_code == 422
    changed = api.req("PATCH", OWNER, f"/orgs/{org}", json={"apps": ["payroll", "customers", "invoices"]})
    assert changed.status_code == 200 and changed.json()["apps"] == ["customers", "invoices", "payroll"]
    # The menu is only a menu: what is left out still works for those allowed.
    assert api.req("GET", OWNER, f"/orgs/{org}/products").status_code == 200
