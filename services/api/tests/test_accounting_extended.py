"""Credit notes and refunds, returns, closed books, financial statements, e-invoice QR,
purchasing (order -> receipt -> bill -> payment) and stock reserved for confirmed orders."""

from __future__ import annotations

import pytest

from novara.modules.invoicing.zatca import decode
from tests.conftest import requires_pg

OWNER, SALES, WH, ACCT = (f"{n}@books.example.com" for n in ("owner", "sales", "wh", "acct"))


@pytest.fixture
def shop(api):
    for e in (OWNER, SALES, WH, ACCT):
        api.signup(e)
    org = api.create_org(OWNER, "books-co")
    for e, role in ((SALES, "sales"), (WH, "warehouse"), (ACCT, "accountant")):
        assert api.add_member(OWNER, org, e, role).status_code == 201
    vat = api.req("POST", ACCT, f"/orgs/{org}/taxes", json={"name": "VAT 15%", "rate": "15"}).json()["id"]
    chair = api.req("POST", OWNER, f"/orgs/{org}/products",
                    json={"name": "Office chair", "sale_price": "500", "default_tax_id": vat}).json()["id"]
    setup = api.req("POST", OWNER, f"/orgs/{org}/products",
                    json={"name": "Assembly", "kind": "service", "sale_price": "100", "default_tax_id": vat}).json()["id"]
    customer = api.req("POST", SALES, f"/orgs/{org}/customers", json={"name": "Riyadh Offices"}).json()["id"]
    return {"org": org, "vat": vat, "chair": chair, "setup": setup, "customer": customer}


def o(s, path):
    return f"/orgs/{s['org']}{path}"


def balances(api, s, **params):
    tb = api.req("GET", ACCT, o(s, "/accounting/trial-balance"), params=params).json()
    assert tb["balanced"]
    return {l["code"]: l["balance"] for l in tb["lines"]}


def posted_invoice(api, s, lines, date="2026-04-01"):
    inv = api.req("POST", SALES, o(s, "/invoices"), json={"customer_id": s["customer"], "issue_date": date,
                                                          "lines": lines})
    assert inv.status_code == 201, inv.text
    r = api.req("POST", ACCT, o(s, f"/invoices/{inv.json()['id']}/post"))
    assert r.status_code == 200, r.text
    return r.json()


def credit(api, s, inv, lines, user=ACCT, date="2026-04-10", **kw):
    return api.req("POST", user, o(s, f"/invoices/{inv['id']}/credit-notes"),
                   json={"issue_date": date, "reason": "Customer complaint", "lines": lines}, **kw)


def receive(api, s, qty, cost="200", date="2026-02-01"):
    r = api.req("POST", WH, o(s, "/inventory/receipts"), json={
        "move_date": date, "counterpart": "payable", "lines": [{"product_id": s["chair"], "quantity": qty, "unit_cost": cost}]})
    assert r.status_code == 201, r.text


def confirmed_order(api, s, chairs, date="2026-03-01"):
    r = api.req("POST", SALES, o(s, "/sales-orders"), json={
        "customer_id": s["customer"], "order_date": date,
        "lines": [{"product_id": s["chair"], "quantity": chairs}, {"product_id": s["setup"], "quantity": "1"}]})
    assert r.status_code == 201, r.text
    r = api.req("POST", SALES, o(s, f"/sales-orders/{r.json()['id']}/confirm"))
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- credit notes


def test_crediting_in_parts_ends_exactly_at_the_invoice(api, shop):
    inv = posted_invoice(api, shop, [{"product_id": shop["chair"], "quantity": "3"},
                                     {"product_id": shop["setup"], "quantity": "1"}])
    assert (inv["total"], inv["tax"]) == ("1840.00", "240.00")
    chairs, setup = inv["lines"][0]["id"], inv["lines"][1]["id"]

    assert credit(api, shop, inv, [{"invoice_line_id": chairs, "quantity": "1"}], user=SALES).status_code == 403
    first = credit(api, shop, inv, [{"invoice_line_id": chairs, "quantity": "1"}])
    assert first.status_code == 201, first.text
    cn = first.json()
    assert (cn["number"], cn["subtotal"], cn["tax"], cn["total"], cn["applied"], cn["refundable"]) == (
        "CN-2026-00001", "500.00", "75.00", "575.00", "575.00", "0.00")
    after = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()
    assert (after["status"], after["amount_credited"], after["amount_due"]) == ("posted", "575.00", "1265.00")
    assert [c["number"] for c in after["credit_notes"]] == ["CN-2026-00001"]

    too_many = credit(api, shop, inv, [{"invoice_line_id": chairs, "quantity": "3"}])
    assert too_many.status_code == 422 and "at most 2" in too_many.text
    rest = credit(api, shop, inv, [{"invoice_line_id": chairs, "quantity": "2"}, {"invoice_line_id": setup, "quantity": "1"}])
    assert (rest.json()["subtotal"], rest.json()["tax"], rest.json()["total"]) == ("1100.00", "165.00", "1265.00")
    after = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()
    assert (after["status"], after["amount_due"]) == ("credited", "0.00")
    assert credit(api, shop, inv, [{"invoice_line_id": setup, "quantity": "1"}]).status_code == 422
    bal = balances(api, shop)
    assert (bal["1300"], bal["2200"], bal["4000"]) == ("0.00", "0.00", "0.00")
    assert api.req("POST", ACCT, o(shop, f"/invoices/{inv['id']}/cancel"),
                   json={"cancel_date": "2026-04-20"}).status_code == 409


def test_rounding_never_drifts(api, shop):
    inv = posted_invoice(api, shop, [{"description": "Washer", "quantity": "3", "unit_price": "0.10",
                                      "tax_id": shop["vat"]}])
    assert (inv["subtotal"], inv["tax"]) == ("0.30", "0.05")
    line = inv["lines"][0]["id"]
    totals = [credit(api, shop, inv, [{"invoice_line_id": line, "quantity": "1"}]).json()["total"] for _ in range(3)]
    assert totals == ["0.12", "0.12", "0.11"]  # the last one takes the exact remainder
    assert api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()["amount_due"] == "0.00"


def test_credit_after_payment_is_refunded(api, shop):
    inv = posted_invoice(api, shop, [{"product_id": shop["chair"], "quantity": "1"}])
    pay = api.req("POST", ACCT, o(shop, f"/invoices/{inv['id']}/payments"),
                  json={"amount": "200", "payment_date": "2026-04-02", "method": "bank"})
    assert pay.status_code == 201, pay.text
    cn = credit(api, shop, inv, [{"invoice_line_id": inv["lines"][0]["id"], "quantity": "1"}]).json()
    assert (cn["applied"], cn["refundable"]) == ("375.00", "200.00")   # 575 credited, 375 was still due
    assert api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()["status"] == "paid"
    url = o(shop, f"/credit-notes/{cn['id']}/refunds")
    assert api.req("POST", ACCT, url, json={"amount": "250", "refund_date": "2026-04-11", "method": "bank"}).status_code == 422
    first = api.req("POST", ACCT, url, json={"amount": "200", "refund_date": "2026-04-11", "method": "bank"},
                    headers={"Idempotency-Key": "refund-1"})
    again = api.req("POST", ACCT, url, json={"amount": "200", "refund_date": "2026-04-11", "method": "bank"},
                    headers={"Idempotency-Key": "refund-1"})
    assert (first.status_code, again.status_code) == (201, 200)
    assert api.req("GET", ACCT, o(shop, f"/credit-notes/{cn['id']}")).json()["refundable"] == "0.00"
    bal = balances(api, shop)
    assert (bal["1300"], bal["1200"], bal["4000"]) == ("0.00", "0.00", "0.00")
    assert [r["amount"] for r in api.req("GET", ACCT, url).json()] == ["200.00"]


def test_returned_goods_go_back_to_stock_at_their_cost(api, shop):
    receive(api, shop, "10")
    order = confirmed_order(api, shop, "2")
    assert api.req("POST", WH, o(shop, f"/sales-orders/{order['id']}/deliveries"),
                   json={"move_date": "2026-03-02"}).status_code == 201
    draft = api.req("POST", SALES, o(shop, f"/sales-orders/{order['id']}/invoice"), json={"issue_date": "2026-03-03"})
    inv = api.req("POST", ACCT, o(shop, f"/invoices/{draft.json()['id']}/post")).json()
    chair_line = next(l for l in inv["lines"] if l["product_id"] == shop["chair"])
    setup_line = next(l for l in inv["lines"] if l["product_id"] == shop["setup"])

    returned = [{"invoice_line_id": chair_line["id"], "quantity": "1", "returned_quantity": "1"}]
    assert credit(api, shop, inv, returned).status_code == 403          # accountants don't move stock
    assert credit(api, shop, inv, [{"invoice_line_id": setup_line["id"], "quantity": "1", "returned_quantity": "1"}],
                  user=OWNER).status_code == 422                        # a service can't come back to stock
    cn = credit(api, shop, inv, returned, user=OWNER)
    assert cn.status_code == 201, cn.text
    assert cn.json()["stock_reference"].startswith("RET-2026-")
    lv = api.req("GET", WH, o(shop, "/inventory/levels"), params={"product_id": shop["chair"]}).json()[0]
    assert (lv["quantity"], lv["value"]) == ("9", "1800.00")
    bal = balances(api, shop)
    assert (bal["1400"], bal["5000"]) == ("1800.00", "200.00")
    assert api.req("GET", WH, o(shop, "/inventory/valuation")).json()["matches_ledger"] is True


def test_direct_invoice_goods_cannot_be_returned_to_stock(api, shop):
    inv = posted_invoice(api, shop, [{"product_id": shop["chair"], "quantity": "1"}])
    r = credit(api, shop, inv, [{"invoice_line_id": inv["lines"][0]["id"], "quantity": "1", "returned_quantity": "1"}],
               user=OWNER)
    assert r.status_code == 422 and "not delivered from stock" in r.text


# ---------------------------------------------------------------- e-invoice QR


def test_invoice_carries_the_zatca_qr_once_a_vat_number_is_set(api, shop):
    inv = posted_invoice(api, shop, [{"product_id": shop["chair"], "quantity": "1"}])
    assert inv["zatca_qr"] is None
    assert api.req("PATCH", OWNER, o(shop, ""), json={"vat_number": "300000000000003"}).status_code == 200
    inv = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()
    fields = decode(inv["zatca_qr"])
    assert (fields[1], fields[2], fields[4], fields[5]) == ("Books-Co", "300000000000003", "575.00", "75.00")
    assert fields[3].endswith("Z") and fields[3].startswith("20")
    cn = credit(api, shop, inv, [{"invoice_line_id": inv["lines"][0]["id"], "quantity": "1"}]).json()
    assert decode(cn["zatca_qr"])[4] == "575.00"


# ---------------------------------------------------------------- closed books


def test_closed_books_refuse_every_posting_on_or_before_the_date(api, shop):
    url = o(shop, "/accounting/lock")
    assert api.req("PUT", SALES, url, json={"locked_until": "2026-03-31"}).status_code == 403
    assert api.req("PUT", ACCT, url, json={"locked_until": "2999-01-01"}).status_code == 422
    assert api.req("PUT", ACCT, url, json={"locked_until": "2026-03-31"}).json() == {"locked_until": "2026-03-31"}
    assert api.req("GET", OWNER, o(shop, "")).json()["books_locked_until"] == "2026-03-31"

    draft = api.req("POST", SALES, o(shop, "/invoices"), json={
        "customer_id": shop["customer"], "issue_date": "2026-03-15", "lines": [{"product_id": shop["chair"], "quantity": "1"}]})
    r = api.req("POST", ACCT, o(shop, f"/invoices/{draft.json()['id']}/post"))
    assert r.status_code == 409 and "closed up to 2026-03-31" in r.text
    assert api.req("GET", ACCT, o(shop, f"/invoices/{draft.json()['id']}")).json()["status"] == "draft"

    inv = posted_invoice(api, shop, [{"product_id": shop["chair"], "quantity": "1"}], date="2026-04-01")
    pay = api.req("POST", ACCT, o(shop, f"/invoices/{inv['id']}/payments"),
                  json={"amount": "100", "payment_date": "2026-03-31", "method": "bank"})
    assert pay.status_code == 422  # before the invoice date anyway
    r = api.req("POST", WH, o(shop, "/inventory/receipts"), json={
        "move_date": "2026-03-01", "lines": [{"product_id": shop["chair"], "quantity": "1", "unit_cost": "1"}]})
    assert r.status_code == 409

    assert api.req("PUT", ACCT, url, json={"locked_until": None}).json() == {"locked_until": None}
    assert api.req("POST", ACCT, o(shop, f"/invoices/{draft.json()['id']}/post")).status_code == 200
    actions = [a["action"] for a in api.req("GET", OWNER, o(shop, "/audit-logs")).json()]
    assert "books.closed" in actions and "books.reopened" in actions


@requires_pg
def test_the_database_refuses_closed_periods_too(api, shop, monkeypatch):
    from sqlalchemy.exc import DBAPIError

    from novara.modules.accounting import service as ledger

    api.req("PUT", ACCT, o(shop, "/accounting/lock"), json={"locked_until": "2026-03-31"})
    monkeypatch.setattr(ledger, "check_open", lambda db, entry_date: None)  # as if the API forgot to check
    draft = api.req("POST", SALES, o(shop, "/invoices"), json={
        "customer_id": shop["customer"], "issue_date": "2026-03-15", "lines": [{"product_id": shop["chair"], "quantity": "1"}]})
    with pytest.raises(DBAPIError, match="books are closed"):
        api.req("POST", ACCT, o(shop, f"/invoices/{draft.json()['id']}/post"))


# ---------------------------------------------------------------- financial statements


def test_profit_and_loss_and_balance_sheet(api, shop):
    receive(api, shop, "10")
    order = confirmed_order(api, shop, "2")
    api.req("POST", WH, o(shop, f"/sales-orders/{order['id']}/deliveries"), json={"move_date": "2026-03-02"})
    draft = api.req("POST", SALES, o(shop, f"/sales-orders/{order['id']}/invoice"), json={"issue_date": "2026-03-03"})
    inv = api.req("POST", ACCT, o(shop, f"/invoices/{draft.json()['id']}/post")).json()
    api.req("POST", ACCT, o(shop, f"/invoices/{inv['id']}/payments"),
            json={"amount": inv["total"], "payment_date": "2026-03-10", "method": "bank"})

    pnl = api.req("GET", ACCT, o(shop, "/accounting/profit-and-loss")).json()
    assert [(l["code"], l["amount"]) for l in pnl["income"]] == [("4000", "1100.00")]
    assert [(l["code"], l["amount"]) for l in pnl["expenses"]] == [("5000", "400.00")]
    assert pnl["net_profit"] == "700.00"
    later = api.req("GET", ACCT, o(shop, "/accounting/profit-and-loss"),
                    params={"date_from": "2026-04-01", "date_to": "2026-04-30"}).json()
    assert (later["income"], later["net_profit"]) == ([], "0.00")
    assert api.req("GET", ACCT, o(shop, "/accounting/profit-and-loss"),
                   params={"date_from": "2026-04-02", "date_to": "2026-04-01"}).status_code == 422

    bs = api.req("GET", ACCT, o(shop, "/accounting/balance-sheet")).json()
    assert {l["code"]: l["amount"] for l in bs["assets"]} == {"1200": "1265.00", "1400": "1600.00"}
    assert {l["code"]: l["amount"] for l in bs["liabilities"]} == {"2100": "2000.00", "2200": "165.00"}
    assert (bs["current_earnings"], bs["total_assets"], bs["total_liabilities_and_equity"], bs["balanced"]) == (
        "700.00", "2865.00", "2865.00", True)
    early = api.req("GET", ACCT, o(shop, "/accounting/balance-sheet"), params={"as_of": "2026-02-15"}).json()
    assert (early["total_assets"], early["current_earnings"]) == ("2000.00", "0.00")
    assert api.req("GET", SALES, o(shop, "/accounting/balance-sheet")).status_code == 403


# ---------------------------------------------------------------- purchasing


def test_purchase_order_to_paid_bill(api, shop):
    vendor = api.req("POST", OWNER, o(shop, "/vendors"), json={"name": "Gulf Supplies", "tax_number": "310000000000003"})
    assert vendor.status_code == 201, vendor.text
    vid = vendor.json()["id"]
    assert api.req("POST", SALES, o(shop, "/vendors"), json={"name": "Nope"}).status_code == 403
    po = api.req("POST", OWNER, o(shop, "/purchase-orders"), json={
        "vendor_id": vid, "order_date": "2026-05-01",
        "lines": [{"product_id": shop["chair"], "quantity": "10", "unit_cost": "200"},
                  {"description": "Freight", "quantity": "1", "unit_cost": "50", "tax_id": shop["vat"]}]})
    assert po.status_code == 201, po.text
    po = po.json()
    assert (po["number"], po["status"], po["subtotal"], po["tax"], po["total"]) == (
        "PO-2026-00001", "draft", "2050.00", "307.50", "2357.50")
    url = o(shop, f"/purchase-orders/{po['id']}")
    assert api.req("POST", WH, url + "/receipts", json={"move_date": "2026-05-02"}).status_code == 409  # not confirmed
    assert api.req("POST", OWNER, url + "/confirm").json()["status"] == "confirmed"

    chair_line = po["lines"][0]["id"]
    rec = api.req("POST", WH, url + "/receipts", json={"move_date": "2026-05-03",
                                                       "lines": [{"line_id": chair_line, "quantity": "6"}]},
                  headers={"Idempotency-Key": "po-rec-1"})
    assert rec.status_code == 201, rec.text
    assert api.req("POST", WH, url + "/receipts", json={"move_date": "2026-05-03",
                                                        "lines": [{"line_id": chair_line, "quantity": "6"}]},
                   headers={"Idempotency-Key": "po-rec-1"}).status_code == 200
    assert balances(api, shop)["2150"] == "-1200.00"   # received, not billed yet

    bill = api.req("POST", OWNER, url + "/bill", json={"bill_date": "2026-05-04", "vendor_reference": "GS-881"})
    assert bill.status_code == 201, bill.text
    bill = bill.json()
    assert [(l["description"], l["quantity"]) for l in bill["lines"]] == [("Office chair", "6"), ("Freight", "1")]
    assert (bill["status"], bill["total"], bill["purchase_order_number"]) == ("draft", "1437.50", "PO-2026-00001")
    assert api.req("POST", SALES, o(shop, f"/bills/{bill['id']}/post")).status_code == 403
    posted = api.req("POST", ACCT, o(shop, f"/bills/{bill['id']}/post"))
    assert posted.status_code == 200, posted.text
    assert posted.json()["number"] == "BILL-2026-00001"
    bal = balances(api, shop)
    assert (bal["2150"], bal["6000"], bal["1500"], bal["2100"]) == ("0.00", "50.00", "187.50", "-1437.50")

    pay = api.req("POST", ACCT, o(shop, f"/bills/{bill['id']}/payments"),
                  json={"amount": "1000", "payment_date": "2026-05-10", "method": "bank"}, headers={"Idempotency-Key": "bp-1"})
    assert pay.status_code == 201, pay.text
    assert api.req("POST", ACCT, o(shop, f"/bills/{bill['id']}/payments"),
                   json={"amount": "1000", "payment_date": "2026-05-10", "method": "bank"},
                   headers={"Idempotency-Key": "bp-1"}).status_code == 200
    payables = api.req("GET", ACCT, o(shop, "/reports/payables"), params={"as_of": "2026-05-31"}).json()
    assert [(v["vendor_name"], v["total_due"]) for v in payables["vendors"]] == [("Gulf Supplies", "437.50")]

    # The rest arrives and is billed; the clearing account ends at zero and stock matches the ledger.
    api.req("POST", WH, url + "/receipts", json={"move_date": "2026-05-12"})
    second = api.req("POST", OWNER, url + "/bill", json={"bill_date": "2026-05-12"}).json()
    assert [(l["description"], l["quantity"]) for l in second["lines"]] == [("Office chair", "4")]
    api.req("POST", ACCT, o(shop, f"/bills/{second['id']}/post"))
    order = api.req("GET", OWNER, url).json()
    assert (order["receipt_status"], order["bill_status"]) == ("received", "billed")
    assert api.req("POST", OWNER, url + "/bill", json={"bill_date": "2026-05-13"}).status_code == 409
    assert balances(api, shop)["2150"] == "0.00"
    assert api.req("GET", WH, o(shop, "/inventory/valuation")).json()["matches_ledger"] is True
    assert api.req("POST", OWNER, url + "/cancel").status_code == 409


def test_direct_bills_expense_services_and_can_be_cancelled(api, shop):
    vid = api.req("POST", OWNER, o(shop, "/vendors"), json={"name": "Landlord"}).json()["id"]
    goods = api.req("POST", OWNER, o(shop, "/bills"), json={
        "vendor_id": vid, "bill_date": "2026-06-01", "lines": [{"product_id": shop["chair"], "quantity": "1", "unit_cost": "1"}]})
    assert goods.status_code == 422 and "purchase order" in goods.text
    inventory = next(a for a in api.req("GET", ACCT, o(shop, "/accounting/accounts")).json() if a["code"] == "1400")
    wrong = api.req("POST", OWNER, o(shop, "/bills"), json={
        "vendor_id": vid, "bill_date": "2026-06-01",
        "lines": [{"description": "x", "quantity": "1", "unit_cost": "1", "account_id": inventory["id"]}]})
    assert wrong.status_code == 422
    bill = api.req("POST", OWNER, o(shop, "/bills"), json={
        "vendor_id": vid, "bill_date": "2026-06-01", "due_date": "2026-06-30", "vendor_reference": "RENT-06",
        "lines": [{"description": "Office rent June", "quantity": "1", "unit_cost": "3000"}]}).json()
    api.req("POST", ACCT, o(shop, f"/bills/{bill['id']}/post"))
    pnl = api.req("GET", ACCT, o(shop, "/accounting/profit-and-loss")).json()
    assert [(l["code"], l["amount"]) for l in pnl["expenses"]] == [("6000", "3000.00")]
    assert api.req("PUT", OWNER, o(shop, f"/bills/{bill['id']}"), json={
        "vendor_id": vid, "bill_date": "2026-06-01", "lines": [{"description": "x", "quantity": "1", "unit_cost": "1"}]}
    ).status_code == 409
    cancelled = api.req("POST", ACCT, o(shop, f"/bills/{bill['id']}/cancel"), json={"cancel_date": "2026-06-02"})
    assert cancelled.json()["status"] == "cancelled"
    assert balances(api, shop).get("6000", "0.00") == "0.00"


# ---------------------------------------------------------------- reservations


def test_stock_is_held_for_orders_confirmed_first(api, shop):
    receive(api, shop, "5")
    first = confirmed_order(api, shop, "4")
    second = confirmed_order(api, shop, "3")
    lv = api.req("GET", WH, o(shop, "/inventory/levels"), params={"product_id": shop["chair"]}).json()[0]
    assert (lv["quantity"], lv["reserved"], lv["available"]) == ("5", "7", "-2")
    reserved = lambda order: next(l["qty_reserved"] for l in api.req("GET", SALES, o(shop, f"/sales-orders/{order['id']}")).json()["lines"]
                                  if l["product_id"] == shop["chair"])  # noqa: E731
    assert (reserved(first), reserved(second)) == ("4", "1")

    chair_line = next(l["id"] for l in second["lines"] if l["product_id"] == shop["chair"])
    late = api.req("POST", WH, o(shop, f"/sales-orders/{second['id']}/deliveries"),
                   json={"move_date": "2026-03-05", "lines": [{"line_id": chair_line, "quantity": "3"}]})
    assert late.status_code == 409 and "only 1" in late.text
    assert api.req("POST", WH, o(shop, f"/sales-orders/{second['id']}/deliveries"),
                   json={"move_date": "2026-03-05", "lines": [{"line_id": chair_line, "quantity": "1"}]}).status_code == 201
    assert api.req("POST", WH, o(shop, f"/sales-orders/{first['id']}/deliveries"),
                   json={"move_date": "2026-03-05"}).status_code == 201
    lv = api.req("GET", WH, o(shop, "/inventory/levels"), params={"product_id": shop["chair"]}).json()[0]
    assert (lv["quantity"], lv["reserved"], lv["available"]) == ("0", "2", "-2")
