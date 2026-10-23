"""Phase 2 acceptance: a company completes a full sales cycle, and its effect on
stock, the invoice and the ledger is verified.

    quotation -> confirm -> deliver (stock out, COGS) -> invoice -> post -> payment
"""

from __future__ import annotations

import pytest

OWNER, SALES, WH, ACCT = (f"{n}@cycle.example.com" for n in ("owner", "sales", "wh", "acct"))


@pytest.fixture
def shop(api):
    for e in (OWNER, SALES, WH, ACCT):
        api.signup(e)
    org = api.create_org(OWNER, "cycle-co")
    for e, role in ((SALES, "sales"), (WH, "warehouse"), (ACCT, "accountant")):
        assert api.add_member(OWNER, org, e, role).status_code == 201
    vat = api.req("POST", ACCT, f"/orgs/{org}/taxes", json={"name": "VAT 15%", "rate": "15"}).json()["id"]
    chair = api.req("POST", OWNER, f"/orgs/{org}/products",
                    json={"name": "Office chair", "sku": "CH-1", "sale_price": "500", "default_tax_id": vat}).json()["id"]
    setup = api.req("POST", OWNER, f"/orgs/{org}/products",
                    json={"name": "Assembly", "kind": "service", "sale_price": "100", "default_tax_id": vat}).json()["id"]
    customer = api.req("POST", SALES, f"/orgs/{org}/customers", json={"name": "Riyadh Offices"}).json()["id"]
    return {"org": org, "vat": vat, "chair": chair, "setup": setup, "customer": customer}


def receive(api, s, qty, cost, date="2026-02-01", key=None):
    r = api.req("POST", WH, f"/orgs/{s['org']}/inventory/receipts",
                json={"move_date": date, "counterpart": "payable",
                      "lines": [{"product_id": s["chair"], "quantity": qty, "unit_cost": cost}]},
                headers={"Idempotency-Key": key} if key else {})
    return r


def level(api, s, product):
    rows = api.req("GET", WH, f"/orgs/{s['org']}/inventory/levels", params={"product_id": product}).json()
    return rows[0] if rows else None


def balances(api, s):
    tb = api.req("GET", ACCT, f"/orgs/{s['org']}/accounting/trial-balance").json()
    assert tb["balanced"]
    return {l["code"]: l["balance"] for l in tb["lines"]}


def quotation(api, s, chairs="10", date="2026-03-01"):
    r = api.req("POST", SALES, f"/orgs/{s['org']}/sales-orders", json={
        "customer_id": s["customer"], "order_date": date,
        "lines": [{"product_id": s["chair"], "quantity": chairs}, {"product_id": s["setup"], "quantity": "1"}],
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_receipts_use_weighted_average_cost(api, shop):
    assert receive(api, shop, "10", "200").status_code == 201
    assert receive(api, shop, "10", "230").status_code == 201
    lv = level(api, shop, shop["chair"])
    assert (lv["quantity"], lv["value"], lv["average_cost"]) == ("20", "4300.00", "215")
    bal = balances(api, shop)
    assert (bal["1400"], bal["2100"]) == ("4300.00", "-4300.00")


def test_retried_receipt_is_not_received_twice(api, shop):
    first = receive(api, shop, "5", "100", key="rcv-1")
    again = receive(api, shop, "5", "100", key="rcv-1")
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["reference"] == again.json()["reference"]
    assert level(api, shop, shop["chair"])["quantity"] == "5"
    assert receive(api, shop, "6", "100", key="rcv-1").status_code == 409


def test_services_have_no_stock(api, shop):
    r = api.req("POST", WH, f"/orgs/{shop['org']}/inventory/receipts", json={
        "move_date": "2026-02-01", "lines": [{"product_id": shop["setup"], "quantity": "1", "unit_cost": "1"}]})
    assert r.status_code == 422


def test_full_sales_cycle(api, shop):
    org = shop["org"]
    receive(api, shop, "10", "200")                                   # stock: 10 @ 200 = 2000
    receive(api, shop, "10", "230")                                   # stock: 20, value 4300, avg 215

    q = quotation(api, shop, chairs="12")
    assert (q["number"], q["status"]) == ("SO-2026-00001", "quotation")
    assert (q["subtotal"], q["tax"], q["total"]) == ("6100.00", "915.00", "7015.00")
    assert level(api, shop, shop["chair"])["quantity"] == "20"         # quotations don't touch stock

    assert api.req("POST", WH, f"/orgs/{org}/sales-orders/{q['id']}/confirm").status_code == 403
    so = api.req("POST", SALES, f"/orgs/{org}/sales-orders/{q['id']}/confirm").json()
    assert (so["status"], so["delivery_status"], so["invoice_status"]) == ("confirmed", "pending", "pending")

    # Partial delivery: 8 of 12 chairs.
    chair_line = next(l for l in so["lines"] if l["product_id"] == shop["chair"])
    d1 = api.req("POST", WH, f"/orgs/{org}/sales-orders/{so['id']}/deliveries",
                 json={"move_date": "2026-03-02", "lines": [{"line_id": chair_line["id"], "quantity": "8"}]})
    assert d1.status_code == 201, d1.text
    assert d1.json()["moves"][0]["value"] == "-1720.00"                # 8 x 215
    assert level(api, shop, shop["chair"])["quantity"] == "12"

    # First invoice: the 8 delivered chairs plus the assembly service.
    inv = api.req("POST", SALES, f"/orgs/{org}/sales-orders/{so['id']}/invoice", json={"issue_date": "2026-03-02"})
    assert inv.status_code == 201, inv.text
    inv = inv.json()
    assert [(l["description"], l["quantity"]) for l in inv["lines"]] == [("Office chair", "8"), ("Assembly", "1")]
    assert inv["total"] == "4715.00"                                   # (4000 + 100) * 1.15
    assert (inv["sales_order_id"], inv["sales_order_number"]) == (so["id"], so["number"])
    # Nothing more can be invoiced until more is delivered.
    again = api.req("POST", SALES, f"/orgs/{org}/sales-orders/{so['id']}/invoice", json={"issue_date": "2026-03-02"})
    assert again.status_code == 409

    # Deliver the rest (no lines = everything outstanding); more than in stock is refused first.
    big = quotation(api, shop, chairs="50")
    api.req("POST", SALES, f"/orgs/{org}/sales-orders/{big['id']}/confirm")
    short = api.req("POST", WH, f"/orgs/{org}/sales-orders/{big['id']}/deliveries", json={"move_date": "2026-03-03"})
    assert short.status_code == 409
    d2 = api.req("POST", WH, f"/orgs/{org}/sales-orders/{so['id']}/deliveries", json={"move_date": "2026-03-03"})
    assert d2.status_code == 201
    assert d2.json()["moves"][0]["value"] == "-860.00"                 # 4 x 215
    inv2 = api.req("POST", SALES, f"/orgs/{org}/sales-orders/{so['id']}/invoice",
                   json={"issue_date": "2026-03-03"}).json()
    assert [(l["description"], l["quantity"]) for l in inv2["lines"]] == [("Office chair", "4")]

    so = api.req("GET", SALES, f"/orgs/{org}/sales-orders/{so['id']}").json()
    assert (so["delivery_status"], so["invoice_status"]) == ("delivered", "invoiced")

    # Post both invoices and collect payment for the first.
    for i in (inv, inv2):
        assert api.req("POST", ACCT, f"/orgs/{org}/invoices/{i['id']}/post").status_code == 200
    pay = api.req("POST", ACCT, f"/orgs/{org}/invoices/{inv['id']}/payments",
                  json={"amount": "4715", "payment_date": "2026-03-10", "method": "bank"})
    assert pay.status_code == 201

    # Verify the ledger end to end.
    bal = balances(api, shop)
    assert bal["1400"] == "1720.00"     # inventory: 4300 - 1720 - 860 = 8 chairs left at 215
    assert bal["5000"] == "2580.00"     # COGS: 12 chairs x 215
    assert bal["4000"] == "-6100.00"    # revenue: 12 x 500 + 100
    assert bal["2200"] == "-915.00"     # VAT
    assert bal["1300"] == "2300.00"     # receivable: second invoice 4 x 500 x 1.15
    assert bal["1200"] == "4715.00"     # bank
    assert bal["2100"] == "-4300.00"    # payable to supplier

    val = api.req("GET", ACCT, f"/orgs/{org}/inventory/valuation").json()
    assert val == {"currency": "SAR", "stock_value": "1720.00", "ledger_inventory_balance": "1720.00",
                   "matches_ledger": True}


def test_last_units_leave_with_exact_remaining_value(api, shop):
    org = shop["org"]
    # 3 units for 100 total: average 33.333...; selling 1, 1, 1 must empty the value exactly.
    api.req("POST", WH, f"/orgs/{org}/inventory/receipts", json={
        "move_date": "2026-02-01", "lines": [{"product_id": shop["chair"], "quantity": "3", "unit_cost": "33.333334"}]})
    assert level(api, shop, shop["chair"])["value"] == "100.00"
    so = quotation(api, shop, chairs="3")
    api.req("POST", SALES, f"/orgs/{org}/sales-orders/{so['id']}/confirm")
    line = next(l for l in so["lines"] if l["product_id"] == shop["chair"])
    values = []
    for day in ("02", "03", "04"):
        r = api.req("POST", WH, f"/orgs/{org}/sales-orders/{so['id']}/deliveries",
                    json={"move_date": f"2026-03-{day}", "lines": [{"line_id": line["id"], "quantity": "1"}]})
        values.append(r.json()["moves"][0]["value"])
    assert values == ["-33.33", "-33.34", "-33.33"]
    lv = level(api, shop, shop["chair"])
    assert (lv["quantity"], lv["value"]) == ("0", "0.00")
    assert api.req("GET", ACCT, f"/orgs/{org}/inventory/valuation").json()["matches_ledger"] is True


def test_retried_delivery_is_not_shipped_twice(api, shop):
    org = shop["org"]
    receive(api, shop, "10", "200")
    so = quotation(api, shop, chairs="4")
    api.req("POST", SALES, f"/orgs/{org}/sales-orders/{so['id']}/confirm")
    url = f"/orgs/{org}/sales-orders/{so['id']}/deliveries"
    body = {"move_date": "2026-03-02"}
    first = api.req("POST", WH, url, json=body, headers={"Idempotency-Key": "d-1"})
    retry = api.req("POST", WH, url, json=body, headers={"Idempotency-Key": "d-1"})
    assert (first.status_code, retry.status_code) == (201, 200)
    assert level(api, shop, shop["chair"])["quantity"] == "6"


def test_over_delivery_and_wrong_lines_rejected(api, shop):
    org = shop["org"]
    receive(api, shop, "10", "200")
    so = quotation(api, shop, chairs="2")
    url = f"/orgs/{org}/sales-orders/{so['id']}/deliveries"
    assert api.req("POST", WH, url, json={"move_date": "2026-03-02"}).status_code == 409   # not confirmed yet
    api.req("POST", SALES, f"/orgs/{org}/sales-orders/{so['id']}/confirm")
    chair = next(l for l in so["lines"] if l["product_id"] == shop["chair"])
    service = next(l for l in so["lines"] if l["product_id"] == shop["setup"])
    for lines in ([{"line_id": chair["id"], "quantity": "3"}], [{"line_id": service["id"], "quantity": "1"}],
                  [{"line_id": "nope", "quantity": "1"}]):
        assert api.req("POST", WH, url, json={"move_date": "2026-03-02", "lines": lines}).status_code == 422, lines


def test_cancelled_invoice_frees_quantities_for_reinvoicing(api, shop):
    org = shop["org"]
    receive(api, shop, "10", "200")
    so = quotation(api, shop, chairs="2")
    api.req("POST", SALES, f"/orgs/{org}/sales-orders/{so['id']}/confirm")
    api.req("POST", WH, f"/orgs/{org}/sales-orders/{so['id']}/deliveries", json={"move_date": "2026-03-02"})
    inv = api.req("POST", SALES, f"/orgs/{org}/sales-orders/{so['id']}/invoice", json={"issue_date": "2026-03-02"}).json()
    # Drafts created from an order can't be freely edited (that would break the link to the order).
    put = api.req("PUT", SALES, f"/orgs/{org}/invoices/{inv['id']}", json={
        "customer_id": shop["customer"], "issue_date": "2026-03-02", "lines": [{"description": "x", "quantity": 1, "unit_price": 1}]})
    assert put.status_code == 409
    api.req("POST", ACCT, f"/orgs/{org}/invoices/{inv['id']}/post")
    api.req("POST", ACCT, f"/orgs/{org}/invoices/{inv['id']}/cancel", json={"cancel_date": "2026-03-05"})
    again = api.req("POST", SALES, f"/orgs/{org}/sales-orders/{so['id']}/invoice", json={"issue_date": "2026-03-06"})
    assert again.status_code == 201
    assert again.json()["total"] == "1265.00"


def test_quotation_edit_and_cancel_rules(api, shop):
    org = shop["org"]
    q = quotation(api, shop)
    body = {"customer_id": shop["customer"], "order_date": "2026-03-01",
            "lines": [{"product_id": shop["setup"], "quantity": "3"}]}
    r = api.req("PUT", SALES, f"/orgs/{org}/sales-orders/{q['id']}", json=body)
    assert r.status_code == 200 and r.json()["total"] == "345.00"
    api.req("POST", SALES, f"/orgs/{org}/sales-orders/{q['id']}/confirm")
    assert api.req("PUT", SALES, f"/orgs/{org}/sales-orders/{q['id']}", json=body).status_code == 409
    api.req("POST", SALES, f"/orgs/{org}/sales-orders/{q['id']}/invoice", json={"issue_date": "2026-03-01"})
    assert api.req("POST", SALES, f"/orgs/{org}/sales-orders/{q['id']}/cancel").status_code == 409
    other = quotation(api, shop)
    assert api.req("POST", SALES, f"/orgs/{org}/sales-orders/{other['id']}/cancel").json()["status"] == "cancelled"


def test_stock_count_adjustment(api, shop):
    org = shop["org"]
    receive(api, shop, "10", "200")
    url = f"/orgs/{org}/inventory/adjustments"
    lost = api.req("POST", WH, url, json={"move_date": "2026-02-10",
                                          "lines": [{"product_id": shop["chair"], "counted_quantity": "9"}]})
    assert lost.status_code == 201, lost.text
    assert lost.json()["moves"][0]["value"] == "-200.00"
    bal = balances(api, shop)
    assert (bal["1400"], bal["5100"]) == ("1800.00", "200.00")
    same = api.req("POST", WH, url, json={"move_date": "2026-02-10",
                                          "lines": [{"product_id": shop["chair"], "counted_quantity": "9"}]})
    assert same.status_code == 422
    assert api.req("GET", ACCT, f"/orgs/{org}/inventory/valuation").json()["matches_ledger"] is True


def test_inventory_permissions(api, shop):
    org = shop["org"]
    assert receive(api, shop, "1", "1").status_code == 201
    r = api.req("POST", SALES, f"/orgs/{org}/inventory/receipts", json={
        "move_date": "2026-02-01", "lines": [{"product_id": shop["chair"], "quantity": "1", "unit_cost": "1"}]})
    assert r.status_code == 403
    assert api.req("GET", SALES, f"/orgs/{org}/inventory/levels").status_code == 200
