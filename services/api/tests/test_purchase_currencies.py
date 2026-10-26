"""Quotations, sales orders, purchase orders and vendor bills in another currency. The ledger stays in the
organization's: receipts value goods at the receipt day's rate, a bill clears exactly what its receipts put in goods
received not invoiced and books the change in rate as an exchange difference, payments realize theirs, and credits,
refunds and the period-end revaluation follow the bill's rate."""

from __future__ import annotations

from tests.test_accounting_extended import ACCT, OWNER, SALES, WH, balances, o, receive, shop  # noqa: F401


def rate(api, s, currency, day, value):
    r = api.req("POST", ACCT, o(s, "/exchange-rates"), json={"currency": currency, "day": day, "rate": value})
    assert r.status_code in (200, 201), r.text


def dollar_order(api, s, qty="10", cost="100"):
    vid = api.req("POST", OWNER, o(s, "/vendors"), json={"name": "Texas Furniture"}).json()["id"]
    po = api.req("POST", OWNER, o(s, "/purchase-orders"), json={
        "vendor_id": vid, "currency": "USD", "order_date": "2026-05-01",
        "lines": [{"product_id": s["chair"], "quantity": qty, "unit_cost": cost, "tax_id": s["vat"]}]})
    assert po.status_code == 201, po.text
    url = o(s, f"/purchase-orders/{po.json()['id']}")
    assert api.req("POST", OWNER, url + "/confirm").status_code == 200
    return po.json(), url, vid


def post(api, s, bill):
    r = api.req("POST", ACCT, o(s, f"/bills/{bill['id']}/post"))
    assert r.status_code == 200, r.text
    return r.json()


def test_a_dollar_purchase_is_received_billed_and_paid_in_riyals(api, shop):
    s = shop
    po, url, _ = dollar_order(api, s)
    assert (po["currency"], po["total"]) == ("USD", "1150.00")
    assert api.req("POST", OWNER, o(s, "/purchase-orders"), json={
        "vendor_id": po["vendor_id"], "currency": "XYZ", "order_date": "2026-05-01",
        "lines": [{"description": "x", "quantity": "1", "unit_cost": "1"}]}).status_code == 422

    no_rate = api.req("POST", WH, url + "/receipts", json={"move_date": "2026-05-03"})
    assert no_rate.status_code == 422 and "exchange rate" in no_rate.json()["detail"]
    assert api.req("POST", WH, url + "/receipts", json={"move_date": "2026-05-03", "exchange_rate": "3.75"}).status_code == 201
    bal = balances(api, s)
    assert (bal["1400"], bal["2150"]) == ("3750.00", "-3750.00")                   # stock is in riyals, at the day's rate

    rate(api, s, "USD", "2026-05-04", "3.76")                                         # the bill takes the kept rate
    bill = api.req("POST", OWNER, url + "/bill", json={"bill_date": "2026-05-05", "vendor_reference": "TX-1"}).json()
    assert (bill["currency"], bill["exchange_rate"], bill["base_total"]) == ("USD", "3.76", None)   # a draft isn't booked
    bill = post(api, s, bill)
    assert (bill["total"], bill["base_total"], bill["base_due"]) == ("1150.00", "4324.00", "4324.00")
    bal = balances(api, s)
    # GRNI clears what the receipt put there; the dollar rising since makes the 10.00 more owed a loss.
    assert (bal.get("2150", "0.00"), bal["1500"], bal["2100"], bal["4900"]) == ("0.00", "564.00", "-4324.00", "10.00")

    pay = o(s, f"/bills/{bill['id']}/payments")
    assert api.req("POST", ACCT, pay, json={"amount": "1150", "payment_date": "2026-05-20", "method": "bank"}).status_code == 422
    paid = api.req("POST", ACCT, pay, json={"amount": "1150", "payment_date": "2026-05-20", "method": "bank",
                                            "exchange_rate": "3.74"})
    assert paid.status_code == 201, paid.text
    assert (paid.json()["base_amount"], paid.json()["exchange_difference"]) == ("4301.00", "23.00")  # cheaper: a gain
    bal = balances(api, s)
    assert (bal.get("2100", "0.00"), bal["1200"], bal["4900"]) == ("0.00", "-4301.00", "-13.00")
    after = api.req("GET", ACCT, o(s, f"/bills/{bill['id']}")).json()
    assert (after["status"], after["base_due"]) == ("paid", "0.00")

    listed = api.req("GET", ACCT, o(s, "/bills"), params={"q": "texas"}).json()
    assert [(b["currency"], b["total"]) for b in listed] == [("USD", "1150.00")]
    assert [p["currency"] for p in api.req("GET", ACCT, o(s, "/purchase-orders"), params={"q": po["number"]}).json()] == ["USD"]
    assert api.req("GET", ACCT, o(s, "/bills"), params={"q": "nobody"}).json() == []


def test_receipts_at_different_rates_clear_to_the_cent_whatever_order_bills_are_posted_in(api, shop):
    s = shop
    _, url, _ = dollar_order(api, s, qty="3", cost="33.33")
    first = api.req("POST", WH, url + "/receipts", json={"move_date": "2026-05-02", "exchange_rate": "3.7513",
                                                        "lines": [{"line_id": _line(api, url), "quantity": "1"}]})
    assert first.status_code == 201, first.text
    early = api.req("POST", OWNER, url + "/bill", json={"bill_date": "2026-05-03", "exchange_rate": "3.76"}).json()
    assert api.req("POST", WH, url + "/receipts", json={"move_date": "2026-05-04", "exchange_rate": "3.80"}).status_code == 201
    late = api.req("POST", OWNER, url + "/bill", json={"bill_date": "2026-05-05", "exchange_rate": "3.79"}).json()
    assert balances(api, s)["2150"] == "-378.34"                  # 125.03 + 253.31, each receipt at its own rate
    post(api, s, late)                                             # the later bill first: it takes its share...
    post(api, s, early)                                            # ...and the earlier one exactly what is left
    bal = balances(api, s)
    assert bal.get("2150", "0.00") == "0.00"
    assert bal["2100"] == "-434.66"                                # (125.32 + 18.80) at 3.76 + (252.64 + 37.90) at 3.79
    assert api.req("GET", WH, o(s, "/inventory/valuation")).json()["matches_ledger"] is True


def _line(api, url):
    return api.req("GET", OWNER, url).json()["lines"][0]["id"]


def test_a_dollar_bill_credited_refunded_and_revalued(api, shop):
    s = shop
    vid = api.req("POST", OWNER, o(s, "/vendors"), json={"name": "Boston Advisors"}).json()["id"]
    body = {"vendor_id": vid, "currency": "USD", "bill_date": "2026-04-01",
            "lines": [{"description": "Advice", "quantity": "10", "unit_cost": "100", "tax_id": s["vat"]}]}
    missing = api.req("POST", OWNER, o(s, "/bills"), json=body)
    assert missing.status_code == 422 and "exchange rate" in missing.json()["detail"]
    bill = post(api, s, api.req("POST", OWNER, o(s, "/bills"), json=body | {"exchange_rate": "3.75"}).json())
    assert (bill["base_total"], balances(api, s)["2100"]) == ("4312.50", "-4312.50")

    # At the month's end the dollar is worth more: owing the same dollars is a loss until it is paid.
    rate(api, s, "USD", "2026-04-30", "3.80")
    preview = api.req("GET", ACCT, o(s, "/fx-revaluations/preview"), params={"as_of": "2026-04-30"}).json()
    assert [(l["kind"], l["bill_id"], l["due"], l["booked"], l["revalued"], l["difference"]) for l in preview["lines"]] == [
        ("payable", bill["id"], "1150.00", "4312.50", "4370.00", "-57.50")]
    made = api.req("POST", ACCT, o(s, "/fx-revaluations"), json={"as_of": "2026-04-30"})
    assert made.status_code == 201, made.text
    on_the_day = balances(api, s, as_of="2026-04-30")
    assert (on_the_day["2100"], on_the_day["4910"]) == ("-4370.00", "57.50")
    assert balances(api, s)["2100"] == "-4312.50"                   # reversed the next day
    cancelled = api.req("POST", ACCT, o(s, f"/fx-revaluations/{made.json()['id']}/cancel"))
    assert cancelled.status_code == 200, cancelled.text
    assert balances(api, s, as_of="2026-04-30")["2100"] == "-4312.50"

    # A credit for four of the ten, at the bill's rate, then the rest paid at it: nothing is left in payables.
    vc = api.req("POST", OWNER, o(s, f"/bills/{bill['id']}/credits"), json={
        "issue_date": "2026-05-02", "reason": "Fewer hours", "lines": [{"bill_line_id": bill["lines"][0]["id"], "quantity": "4"}]})
    assert vc.status_code == 201, vc.text
    after = api.req("GET", ACCT, o(s, f"/bills/{bill['id']}")).json()
    assert (after["amount_due"], after["base_due"]) == ("690.00", "2587.50")
    paid = api.req("POST", ACCT, o(s, f"/bills/{bill['id']}/payments"), json={
        "amount": "690", "payment_date": "2026-05-03", "method": "bank", "exchange_rate": "3.75"})
    assert paid.status_code == 201 and paid.json()["exchange_difference"] == "0.00"
    assert balances(api, s).get("2100", "0.00") == "0.00"

    # Paid in full, then an allowance: the vendor pays it back, at the bill's rate.
    allowance = api.req("POST", OWNER, o(s, f"/bills/{bill['id']}/credits"), json={
        "issue_date": "2026-05-04", "reason": "Discount", "lines": [{"bill_line_id": bill["lines"][0]["id"], "quantity": "1"}]})
    assert allowance.status_code == 201 and allowance.json()["refundable"] == "115.00"
    assert balances(api, s)["2100"] == "431.25"                     # the vendor owes us 115 dollars
    refund = api.req("POST", ACCT, o(s, f"/vendor-credits/{allowance.json()['id']}/refunds"),
                     json={"amount": "115", "refund_date": "2026-05-05", "method": "bank"})
    assert refund.status_code == 201, refund.text
    bal = balances(api, s)
    assert bal.get("2100", "0.00") == "0.00"
    aging = api.req("GET", ACCT, o(s, "/reports/payables"), params={"as_of": "2026-05-05"}).json()
    assert (aging["currency"], aging["total_due"]) == ("SAR", "0.00")


def test_a_dollar_quotation_becomes_a_dollar_invoice(api, shop):
    s = shop
    receive(api, s, "5")                                            # chairs in stock at 200 riyals
    lines = [{"product_id": s["chair"], "quantity": "1"}]
    no_price = api.req("POST", SALES, o(s, "/sales-orders"), json={
        "customer_id": s["customer"], "currency": "USD", "order_date": "2026-03-01", "lines": lines})
    assert no_price.status_code == 422 and "price in USD" in no_price.json()["detail"]
    order = api.req("POST", SALES, o(s, "/sales-orders"), json={
        "customer_id": s["customer"], "currency": "USD", "order_date": "2026-03-01",
        "lines": [lines[0] | {"unit_price": "150"}]})
    assert order.status_code == 201, order.text
    assert (order.json()["currency"], order.json()["total"]) == ("USD", "172.50")
    url = o(s, f"/sales-orders/{order.json()['id']}")
    assert api.req("POST", SALES, url + "/confirm").status_code == 200
    assert api.req("POST", WH, url + "/deliveries", json={"move_date": "2026-03-02"}).status_code == 201
    assert balances(api, s)["1400"] == "800.00"                      # the chair left stock at its riyal cost

    missing = api.req("POST", SALES, url + "/invoice", json={"issue_date": "2026-03-03"})
    assert missing.status_code == 422 and "exchange rate" in missing.json()["detail"]
    inv = api.req("POST", SALES, url + "/invoice", json={"issue_date": "2026-03-03", "exchange_rate": "3.75"})
    assert inv.status_code == 201, inv.text
    assert (inv.json()["currency"], inv.json()["exchange_rate"], inv.json()["total"]) == ("USD", "3.75", "172.50")
    posted = api.req("POST", ACCT, o(s, f"/invoices/{inv.json()['id']}/post")).json()
    assert posted["base_total"] == "646.88"                          # 562.50 + 84.38
    assert [o_["currency"] for o_ in api.req("GET", SALES, o(s, "/sales-orders")).json()] == ["USD"]
