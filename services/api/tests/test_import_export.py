from __future__ import annotations

import csv
import io

import pytest

from novara.modules.dataio.csvio import safe_cell

OWNER = "owner@io.example.com"


@pytest.fixture
def org(api):
    api.signup(OWNER)
    return api.create_org(OWNER, "io-co")


def post_csv(api, path, text, **params):
    return api.req("POST", OWNER, path, content=text.encode("utf-8"), params=params,
                   headers={"Content-Type": "text/csv"})


def parse(resp):
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.content.startswith("﻿".encode())  # BOM so Excel shows Arabic correctly
    return list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))


def test_formula_cells_are_neutralised():
    assert safe_cell("=HYPERLINK(\"http://evil\")") == "'=HYPERLINK(\"http://evil\")"
    assert safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert safe_cell("-150.00") == "-150.00"
    assert safe_cell("شركة") == "شركة"


def test_customer_import_dry_run_then_commit(api, org):
    text = "name,email,city,country\nمؤسسة الأفق,info@ufuq.sa,جدة,SA\nGlobex,sales@globex.com,Dubai,AE\n"
    dry = post_csv(api, f"/orgs/{org}/customers/import", text, dry_run="true")
    assert dry.status_code == 200, dry.text
    assert (dry.json()["created"], dry.json()["dry_run"]) == (2, True)
    assert api.req("GET", OWNER, f"/orgs/{org}/customers").json() == []

    done = post_csv(api, f"/orgs/{org}/customers/import", text)
    assert done.json()["created"] == 2
    exported = parse(api.req("GET", OWNER, f"/orgs/{org}/customers/export.csv"))
    assert {r["name"] for r in exported} == {"مؤسسة الأفق", "Globex"}

    # Re-import with ids updates in place.
    ufuq = next(r for r in exported if r["city"] == "جدة")
    upd = post_csv(api, f"/orgs/{org}/customers/import", f"id,name,city\n{ufuq['id']},مؤسسة الأفق,الرياض\n")
    assert (upd.json()["created"], upd.json()["updated"]) == (0, 1)
    assert api.req("GET", OWNER, f"/orgs/{org}/customers/{ufuq['id']}").json()["city"] == "الرياض"
    assert api.req("GET", OWNER, f"/orgs/{org}/customers/{ufuq['id']}").json()["email"] == "info@ufuq.sa"


def test_import_is_all_or_nothing_with_row_errors(api, org):
    text = "name,email,country\nGood Co,good@example.com,SA\n,missing@example.com,SA\nBad,not-an-email,SAU\n"
    r = post_csv(api, f"/orgs/{org}/customers/import", text)
    assert r.status_code == 422
    errors = {e["row"]: e["errors"] for e in r.json()["detail"]["errors"]}
    assert set(errors) == {3, 4}
    assert api.req("GET", OWNER, f"/orgs/{org}/customers").json() == []


def test_bad_files_rejected(api, org):
    assert post_csv(api, f"/orgs/{org}/customers/import", "email\nx@example.com\n").status_code == 422
    assert post_csv(api, f"/orgs/{org}/customers/import", "name,password\nx,y\n").status_code == 422
    r = api.req("POST", OWNER, f"/orgs/{org}/customers/import", content="name\n\xff".encode("latin-1"))
    assert r.status_code == 422


def test_product_import_upserts_by_sku_and_resolves_tax(api, org):
    api.req("POST", OWNER, f"/orgs/{org}/taxes", json={"name": "VAT 15%", "rate": "15"})
    text = "sku,name,sale_price,tax_name,kind\nA-1,قلم,2.5,VAT 15%,goods\nS-1,Delivery,30,,service\n"
    assert post_csv(api, f"/orgs/{org}/products/import", text).json()["created"] == 2
    r = post_csv(api, f"/orgs/{org}/products/import", "sku,name,sale_price\nA-1,قلم أزرق,3\n")
    assert (r.json()["created"], r.json()["updated"]) == (0, 1)
    products = {p["sku"]: p for p in api.req("GET", OWNER, f"/orgs/{org}/products").json()}
    assert (products["A-1"]["name"], products["A-1"]["sale_price"]) == ("قلم أزرق", "3")
    bad = post_csv(api, f"/orgs/{org}/products/import", "sku,name,sale_price,tax_name\nB,x,1,VAT 99%\nB,y,1,\n")
    assert bad.status_code == 422
    assert {e["row"] for e in bad.json()["detail"]["errors"]} == {2, 3}
    exported = parse(api.req("GET", OWNER, f"/orgs/{org}/products/export.csv"))
    assert {r["sku"]: r["tax_name"] for r in exported} == {"A-1": "", "S-1": ""}


def test_invoice_journal_export_and_sales_report(api, org):
    cust = api.req("POST", OWNER, f"/orgs/{org}/customers", json={"name": "=cmd|' /C calc'!A0"}).json()["id"]
    prod = api.req("POST", OWNER, f"/orgs/{org}/products", json={"name": "Widget", "sale_price": "100"}).json()["id"]
    for day, qty in (("2026-01-15", "1"), ("2026-02-10", "3")):
        inv = api.req("POST", OWNER, f"/orgs/{org}/invoices", json={
            "customer_id": cust, "issue_date": day, "lines": [{"product_id": prod, "quantity": qty}]}).json()
        api.req("POST", OWNER, f"/orgs/{org}/invoices/{inv['id']}/post")

    rows = parse(api.req("GET", OWNER, f"/orgs/{org}/invoices/export.csv"))
    assert [r["number"] for r in rows] == ["INV-2026-00001", "INV-2026-00002"]
    assert rows[0]["customer"].startswith("'=")  # formula neutralised
    journal = parse(api.req("GET", OWNER, f"/orgs/{org}/accounting/journal.csv", params={"date_from": "2026-02-01"}))
    assert {(r["account_code"], r["debit"], r["credit"]) for r in journal} == {("1300", "300.00", "0.00"),
                                                                              ("4000", "0.00", "300.00")}

    rep = api.req("GET", OWNER, f"/orgs/{org}/reports/sales").json()
    assert (rep["net_sales"], rep["invoices"]) == ("400.00", 2)
    assert [(b["key"], b["net"]) for b in rep["by_month"]] == [("2026-01", "100.00"), ("2026-02", "300.00")]
    assert rep["top_products"][0]["label"] == "Widget"
    feb = api.req("GET", OWNER, f"/orgs/{org}/reports/sales", params={"date_from": "2026-02-01"}).json()
    assert feb["net_sales"] == "300.00"
