"""Invoices in another currency: booked in the organization's currency at the invoice's rate, paid at the payment
date's rate with the difference as an exchange gain or loss, credited and refunded without leaving cents behind."""

from __future__ import annotations

from tests.test_accounting_extended import ACCT, OWNER, SALES, balances, o, shop  # noqa: F401

LINE = {"description": "Consulting", "quantity": "10", "unit_price": "100"}     # USD 1,000 net


def usd_invoice(api, s, rate="3.75", lines=None, date="2026-04-01", tax=True):
    lines = lines or [LINE | ({"tax_id": s["vat"]} if tax else {})]
    r = api.req("POST", SALES, o(s, "/invoices"), json={"customer_id": s["customer"], "issue_date": date,
                                                        "currency": "USD", "exchange_rate": rate, "lines": lines})
    assert r.status_code == 201, r.text
    posted = api.req("POST", ACCT, o(s, f"/invoices/{r.json()['id']}/post"))
    assert posted.status_code == 200, posted.text
    return posted.json()


def pay(api, s, inv, amount, rate, date="2026-04-20", **kw):
    return api.req("POST", ACCT, o(s, f"/invoices/{inv['id']}/payments"),
                   json={"amount": amount, "payment_date": date, "method": "bank", **({"exchange_rate": rate} if rate else {}), **kw})


def test_a_dollar_invoice_is_booked_in_riyals_and_paid_with_an_exchange_difference(api, shop):
    no_rate = api.req("POST", SALES, o(shop, "/invoices"), json={"customer_id": shop["customer"], "issue_date": "2026-04-01",
                                                                  "currency": "USD", "lines": [LINE]})
    assert no_rate.status_code == 422 and "exchange rate" in no_rate.json()["detail"]
    product_price = api.req("POST", SALES, o(shop, "/invoices"), json={
        "customer_id": shop["customer"], "issue_date": "2026-04-01", "currency": "USD", "exchange_rate": "3.75",
        "lines": [{"product_id": shop["chair"], "quantity": "1"}]})
    assert product_price.status_code == 422 and "price in USD" in product_price.json()["detail"]
    assert api.req("POST", SALES, o(shop, "/invoices"), json={
        "customer_id": shop["customer"], "issue_date": "2026-04-01", "currency": "XYZ", "exchange_rate": "1",
        "lines": [LINE]}).status_code == 422

    inv = usd_invoice(api, shop)
    assert (inv["currency"], inv["total"], inv["exchange_rate"], inv["base_total"], inv["base_due"]) == (
        "USD", "1150.00", "3.75", "4312.50", "4312.50")
    bal = balances(api, shop)
    assert (bal["1300"], bal["4000"], bal["2200"]) == ("4312.50", "-3750.00", "-562.50")   # the ledger is in SAR

    assert pay(api, shop, inv, "500", None).status_code == 422                       # the payment date's rate is needed
    first = pay(api, shop, inv, "500", "3.80")                                         # the dollar went up: a gain
    assert first.status_code == 201, first.text
    assert (first.json()["base_amount"], first.json()["exchange_difference"]) == ("1900.00", "25.00")
    after = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()
    assert (after["amount_due"], after["base_due"]) == ("650.00", "2437.50")
    last = pay(api, shop, inv, "650", "3.70").json()                                   # then down: a loss
    assert (last["base_amount"], last["exchange_difference"]) == ("2405.00", "-32.50")
    bal = balances(api, shop)
    assert (bal.get("1300", "0.00"), bal["1200"], bal["4900"]) == ("0.00", "4305.00", "7.50")  # net loss of 7.50
    assert api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()["status"] == "paid"
    payments = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}/payments")).json()
    assert [p["exchange_rate"] for p in payments] == ["3.8", "3.7"]


def test_awkward_rates_leave_no_cents_behind_and_reports_count_in_riyals(api, shop):
    inv = usd_invoice(api, shop, rate="3.7513", lines=[{"description": "Odd", "quantity": "3", "unit_price": "33.33",
                                                        "tax_id": shop["vat"]}])
    assert (inv["total"], inv["base_total"]) == ("114.99", "431.36")                  # 375.09 + 56.27: each part rounded once
    for amount in ("33.33", "33.33", "48.33"):
        assert pay(api, shop, inv, amount, "3.7513").status_code == 201
    bal = balances(api, shop)
    assert bal.get("1300", "0.00") == "0.00"                                          # cleared to the cent
    assert bal.get("4900", "0.00") in ("0.00", "-0.01", "0.01")                       # only rounding, if any

    other = usd_invoice(api, shop, rate="3.75", tax=False, date="2026-04-02")
    report = api.req("GET", ACCT, o(shop, "/reports/receivables"), params={"as_of": "2026-05-01"}).json()
    assert (report["currency"], report["total_due"]) == ("SAR", "3750.00")
    sales = api.req("GET", ACCT, o(shop, "/reports/sales"), params={"date_from": "2026-04-01", "date_to": "2026-04-30"}).json()
    assert sales["net_sales"] == "4125.09"                                             # 375.09 + 3750.00, in SAR
    assert other["base_due"] == "3750.00"


def test_credit_notes_and_refunds_follow_the_invoices_rate(api, shop):
    inv = usd_invoice(api, shop, rate="3.75")
    assert pay(api, shop, inv, "1150", "3.75").status_code == 201
    note = api.req("POST", ACCT, o(shop, f"/invoices/{inv['id']}/credit-notes"), json={
        "issue_date": "2026-04-25", "reason": "Two hours not delivered",
        "lines": [{"invoice_line_id": inv["lines"][0]["id"], "quantity": "2"}]})
    assert note.status_code == 201, note.text
    note = note.json()
    assert (note["currency"], note["total"], note["refundable"]) == ("USD", "230.00", "230.00")
    bal = balances(api, shop)
    assert bal["1300"] == "-862.50"                                                    # owed back: 230 x 3.75
    refund = lambda amount: api.req("POST", ACCT, o(shop, f"/credit-notes/{note['id']}/refunds"),  # noqa: E731
                                    json={"amount": amount, "refund_date": "2026-04-26", "method": "bank"})
    assert refund("100.01").status_code == 201
    assert refund("129.99").status_code == 201
    bal = balances(api, shop)
    assert (bal.get("1300", "0.00"), bal["1200"]) == ("0.00", "3450.00")              # 4312.50 - 862.50, nothing left over
