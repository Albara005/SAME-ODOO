"""Vendor credits and goods sent back, manual journal entries, and closing the year into retained earnings."""

from __future__ import annotations

import pytest

from tests.test_accounting_extended import ACCT, OWNER, SALES, WH, balances, o, posted_invoice, shop  # noqa: F401


@pytest.fixture
def bought(api, shop):
    """10 chairs at 200 and a freight charge, received in full and billed: 2,357.50 owed."""
    vid = api.req("POST", OWNER, o(shop, "/vendors"), json={"name": "Gulf Supplies"}).json()["id"]
    po = api.req("POST", OWNER, o(shop, "/purchase-orders"), json={
        "vendor_id": vid, "order_date": "2026-05-01",
        "lines": [{"product_id": shop["chair"], "quantity": "10", "unit_cost": "200", "tax_id": shop["vat"]},
                  {"description": "Freight", "quantity": "1", "unit_cost": "50", "tax_id": shop["vat"]}]}).json()
    url = o(shop, f"/purchase-orders/{po['id']}")
    api.req("POST", OWNER, url + "/confirm")
    assert api.req("POST", WH, url + "/receipts", json={"move_date": "2026-05-03"}).status_code == 201
    bill = api.req("POST", OWNER, url + "/bill", json={"bill_date": "2026-05-04", "vendor_reference": "GS-881"}).json()
    bill = api.req("POST", ACCT, o(shop, f"/bills/{bill['id']}/post")).json()
    assert (bill["total"], bill["amount_due"]) == ("2357.50", "2357.50")
    return {**shop, "vendor": vid, "bill": bill, "chairs": bill["lines"][0]["id"], "freight": bill["lines"][1]["id"]}


def vendor_credit(api, s, lines, user=OWNER, date="2026-05-06", **kw):
    return api.req("POST", user, o(s, f"/bills/{s['bill']['id']}/credits"),
                   json={"issue_date": date, "reason": "Damaged in transit", "vendor_reference": "GS-CN-12",
                         "lines": lines}, **kw)


# ---------------------------------------------------------------- vendor credits


def test_goods_sent_back_reduce_the_bill_stock_and_vat(api, bought):
    s = bought
    send_back = [{"bill_line_id": s["chairs"], "quantity": "2", "returned_quantity": "2"},
                 {"bill_line_id": s["freight"], "quantity": "1"}]
    # Sending goods out of stock is warehouse work too: the accountant role can credit, not move stock.
    assert vendor_credit(api, s, send_back, user=ACCT).status_code == 403
    assert vendor_credit(api, s, send_back, user=SALES).status_code == 403
    assert vendor_credit(api, s, [{"bill_line_id": s["freight"], "quantity": "1", "returned_quantity": "1"}]
                         ).status_code == 422  # a service can't go back to stock
    assert vendor_credit(api, s, [{"bill_line_id": s["chairs"], "quantity": "11"}]).status_code == 422

    vc = vendor_credit(api, s, send_back, headers={"Idempotency-Key": "vc-1"})
    assert vc.status_code == 201, vc.text
    vc = vc.json()
    assert (vc["number"], vc["subtotal"], vc["tax"], vc["total"], vc["applied"], vc["refundable"]) == (
        "VC-2026-00001", "450.00", "67.50", "517.50", "517.50", "0.00")
    assert vc["stock_reference"].startswith("VRT-2026-")
    assert vendor_credit(api, s, send_back, headers={"Idempotency-Key": "vc-1"}).status_code == 200

    bill = api.req("GET", ACCT, o(s, f"/bills/{s['bill']['id']}")).json()
    assert (bill["status"], bill["amount_credited"], bill["amount_due"]) == ("posted", "517.50", "1840.00")
    assert [(l["qty_credited"], l["qty_returned"]) for l in bill["lines"]] == [("2", "2"), ("1", "0")]
    bal = balances(api, s)
    # Stock 8 x 200, freight expense gone, VAT on 2,000 recoverable, 1,840 still owed.
    assert (bal["1400"], bal.get("6000", "0.00"), bal["1500"], bal["2100"], bal["2150"]) == (
        "1600.00", "0.00", "240.00", "-1840.00", "0.00")
    assert "5100" not in bal  # sent back at the price paid: no difference
    assert api.req("GET", WH, o(s, "/inventory/valuation")).json()["matches_ledger"] is True
    assert vendor_credit(api, s, [{"bill_line_id": s["chairs"], "quantity": "9"}]).status_code == 422  # 8 left

    # Paid in full, then a price allowance on a chair kept in stock: the vendor owes it back.
    paid = api.req("POST", ACCT, o(s, f"/bills/{s['bill']['id']}/payments"),
                   json={"amount": "1840", "payment_date": "2026-05-10", "method": "bank"})
    assert paid.status_code == 201, paid.text
    assert api.req("GET", ACCT, o(s, f"/bills/{s['bill']['id']}")).json()["status"] == "paid"
    allowance = vendor_credit(api, s, [{"bill_line_id": s["chairs"], "quantity": "1"}], date="2026-05-12").json()
    assert (allowance["total"], allowance["applied"], allowance["refundable"]) == ("230.00", "0.00", "230.00")
    assert api.req("POST", ACCT, o(s, f"/bills/{s['bill']['id']}/cancel"),
                   json={"cancel_date": "2026-05-13"}).status_code == 409
    url = o(s, f"/vendor-credits/{allowance['id']}/refunds")
    assert api.req("POST", WH, url, json={"amount": "230", "refund_date": "2026-05-14", "method": "bank"}).status_code == 403
    assert api.req("POST", ACCT, url, json={"amount": "231", "refund_date": "2026-05-14", "method": "bank"}).status_code == 422
    refund = api.req("POST", ACCT, url, json={"amount": "230", "refund_date": "2026-05-14", "method": "bank"},
                     headers={"Idempotency-Key": "vr-1"})
    assert refund.status_code == 201, refund.text
    assert api.req("POST", ACCT, url, json={"amount": "230", "refund_date": "2026-05-14", "method": "bank"},
                   headers={"Idempotency-Key": "vr-1"}).status_code == 200
    assert api.req("GET", ACCT, o(s, f"/vendor-credits/{allowance['id']}")).json()["refundable"] == "0.00"
    bal = balances(api, s)
    # The allowance lowers what the chairs cost (inventory adjustments), and VAT comes back with it.
    assert (bal["2100"], bal["5100"], bal["1500"], bal["1200"]) == ("0.00", "-200.00", "210.00", "-1610.00")
    listed = api.req("GET", ACCT, o(s, "/vendor-credits"), params={"bill_id": s["bill"]["id"]}).json()
    assert [c["number"] for c in listed] == ["VC-2026-00002", "VC-2026-00001"]


def test_crediting_a_bill_in_full_marks_it_credited(api, bought):
    s = bought
    full = vendor_credit(api, s, [{"bill_line_id": s["chairs"], "quantity": "10", "returned_quantity": "10"},
                                  {"bill_line_id": s["freight"], "quantity": "1"}])
    assert full.status_code == 201, full.text
    assert full.json()["total"] == "2357.50"
    bill = api.req("GET", ACCT, o(s, f"/bills/{s['bill']['id']}")).json()
    assert (bill["status"], bill["amount_due"]) == ("credited", "0.00")
    assert api.req("POST", ACCT, o(s, f"/bills/{s['bill']['id']}/payments"),
                   json={"amount": "1", "payment_date": "2026-05-10", "method": "bank"}).status_code == 409
    bal = balances(api, s)
    assert all(bal.get(code, "0.00") == "0.00" for code in ("1400", "1500", "2100", "2150", "6000"))


def test_goods_leave_at_average_cost_and_the_difference_is_an_adjustment(api, bought):
    s = bought
    # More chairs arrive at a higher cost: the average is now (2,000 + 2,600) / 20 = 230.
    r = api.req("POST", WH, o(s, "/inventory/receipts"), json={
        "move_date": "2026-05-05", "counterpart": "payable",
        "lines": [{"product_id": s["chair"], "quantity": "10", "unit_cost": "260"}]})
    assert r.status_code == 201, r.text
    vc = vendor_credit(api, s, [{"bill_line_id": s["chairs"], "quantity": "2", "returned_quantity": "2"}])
    assert vc.status_code == 201, vc.text
    bal = balances(api, s)
    assert (bal["1400"], bal["5100"]) == ("4140.00", "60.00")  # 460 left stock, the vendor credited 400
    assert api.req("GET", WH, o(s, "/inventory/valuation")).json()["matches_ledger"] is True


def test_goods_already_sold_cannot_be_sent_back(api, bought):
    s = bought
    adjust = api.req("POST", WH, o(s, "/inventory/adjustments"), json={
        "move_date": "2026-05-05", "lines": [{"product_id": s["chair"], "counted_quantity": "1"}]})
    assert adjust.status_code == 201, adjust.text
    r = vendor_credit(api, s, [{"bill_line_id": s["chairs"], "quantity": "2", "returned_quantity": "2"}])
    assert r.status_code == 409 and "only 1" in r.text


# ---------------------------------------------------------------- manual journal entries


def accounts(api, s):
    return {a["code"]: a for a in api.req("GET", ACCT, o(s, "/accounting/accounts")).json()}


def manual(api, s, lines, user=ACCT, date="2026-03-31", memo="Rent accrual for March", **kw):
    return api.req("POST", user, o(s, "/accounting/journal-entries"),
                   json={"entry_date": date, "memo": memo, "lines": lines}, **kw)


def test_manual_entries_balance_avoid_control_accounts_and_are_reversed(api, shop):
    accrued = api.req("POST", ACCT, o(shop, "/accounting/accounts"),
                      json={"code": "2300", "name": "Accrued expenses", "type": "liability"})
    assert accrued.status_code == 201, accrued.text
    assert api.req("POST", ACCT, o(shop, "/accounting/accounts"),
                   json={"code": "2300", "name": "Again", "type": "liability"}).status_code == 409
    assert api.req("POST", SALES, o(shop, "/accounting/accounts"),
                   json={"code": "2400", "name": "No", "type": "liability"}).status_code == 403
    acc = accounts(api, shop)
    assert (acc["1300"]["control"], acc["6000"]["control"]) == (True, False)
    rent = [{"account_id": acc["6000"]["id"], "debit": "1200", "label": "March rent"},
            {"account_id": acc["2300"]["id"], "credit": "1200"}]

    assert manual(api, shop, rent, user=SALES).status_code == 403
    assert manual(api, shop, [rent[0], {"account_id": acc["2300"]["id"], "credit": "1100"}]).status_code == 422
    assert manual(api, shop, [rent[0], {"account_id": acc["1300"]["id"], "credit": "1200"}]).status_code == 422
    assert manual(api, shop, [{"account_id": acc["6000"]["id"], "debit": "5", "credit": "5"},
                              rent[1]]).status_code == 422

    entry = manual(api, shop, rent, headers={"Idempotency-Key": "je-1"})
    assert entry.status_code == 201, entry.text
    entry = entry.json()
    assert (entry["source_type"], entry["number"]) == ("manual", "JE-2026-000001")
    assert [(l["account_code"], l["account_name"], l["debit"], l["credit"]) for l in entry["lines"]] == [
        ("6000", "General Expenses", "1200.00", "0.00"), ("2300", "Accrued expenses", "0.00", "1200.00")]
    assert manual(api, shop, rent, headers={"Idempotency-Key": "je-1"}).status_code == 200
    assert (balances(api, shop)["6000"], balances(api, shop)["2300"]) == ("1200.00", "-1200.00")

    # An account with a balance can't be archived; a platform account never can.
    url = o(shop, f"/accounting/accounts/{acc['2300']['id']}")
    assert api.req("PATCH", ACCT, url, json={"active": False}).status_code == 409
    assert api.req("PATCH", ACCT, o(shop, f"/accounting/accounts/{acc['6000']['id']}"),
                   json={"active": False}).status_code == 409

    rev_url = o(shop, f"/accounting/journal-entries/{entry['id']}/reverse")
    assert api.req("POST", ACCT, rev_url, json={"entry_date": "2026-03-30"}).status_code == 422  # before the entry
    reversal = api.req("POST", ACCT, rev_url, json={"entry_date": "2026-04-01"})
    assert reversal.status_code == 201, reversal.text
    assert reversal.json()["reversal_of_id"] == entry["id"]
    assert api.req("POST", ACCT, rev_url, json={"entry_date": "2026-04-01"}).status_code == 422  # only once
    journal = api.req("GET", ACCT, o(shop, "/accounting/journal-entries"), params={"source_type": "manual"}).json()
    assert {e["id"]: e["reversed_by_id"] for e in journal}[entry["id"]] == reversal.json()["id"]
    assert balances(api, shop).get("2300", "0.00") == "0.00"
    assert api.req("PATCH", ACCT, url, json={"active": False}).json()["active"] is False
    assert manual(api, shop, rent, date="2026-04-02").status_code == 422  # archived account

    # Document entries are reversed through their documents.
    inv = posted_invoice(api, shop, [{"product_id": shop["setup"], "quantity": "1"}])
    doc_entry = api.req("GET", ACCT, o(shop, "/accounting/journal-entries"),
                        params={"source_type": "invoice", "source_id": inv["id"]}).json()[0]
    assert api.req("POST", ACCT, o(shop, f"/accounting/journal-entries/{doc_entry['id']}/reverse"),
                   json={"entry_date": "2026-04-05"}).status_code == 422

    # Closed books refuse manual entries too.
    assert api.req("PUT", ACCT, o(shop, "/accounting/lock"), json={"locked_until": "2026-04-30"}).status_code == 200
    late = manual(api, shop, [rent[0], {"account_id": acc["1200"]["id"], "credit": "1200"}], date="2026-04-15")
    assert late.status_code == 409


# ---------------------------------------------------------------- closing the year


def test_year_end_close_moves_the_result_into_retained_earnings(api, shop):
    # This organization's fiscal year runs April to March.
    assert api.req("PATCH", OWNER, f"/orgs/{shop['org']}", json={"fiscal_year_start_month": 4}).status_code == 200
    assert api.req("POST", ACCT, o(shop, "/accounting/year-end"),
                   json={"period_end": "2026-03-31"}).status_code == 422  # nothing to close yet
    posted_invoice(api, shop, [{"product_id": shop["setup"], "quantity": "10"}], date="2026-02-01")  # 1,000 income
    acc = accounts(api, shop)
    assert manual(api, shop, [{"account_id": acc["6000"]["id"], "debit": "300"},
                              {"account_id": acc["1200"]["id"], "credit": "300"}], date="2026-03-15").status_code == 201

    assert api.req("POST", SALES, o(shop, "/accounting/year-end"), json={"period_end": "2026-03-31"}).status_code == 403
    assert api.req("POST", ACCT, o(shop, "/accounting/year-end"), json={"period_end": "2999-12-31"}).status_code == 422
    closed = api.req("POST", ACCT, o(shop, "/accounting/year-end"), json={"period_end": "2026-03-31"})
    assert closed.status_code == 201, closed.text
    closed = closed.json()
    assert (closed["last_closed"], closed["locked_until"]) == ("2026-03-31", "2026-03-31")
    assert sorted((l["account_code"], l["debit"], l["credit"]) for l in closed["entry"]["lines"]) == [
        ("3100", "0.00", "700.00"), ("4000", "1000.00", "0.00"), ("6000", "0.00", "300.00")]

    bal = balances(api, shop)
    assert (bal["3100"], bal.get("4000", "0.00"), bal.get("6000", "0.00")) == ("-700.00", "0.00", "0.00")
    # The profit and loss of the closed period still shows what happened in it.
    pnl = api.req("GET", ACCT, o(shop, "/accounting/profit-and-loss"),
                  params={"date_from": "2026-01-01", "date_to": "2026-03-31"}).json()
    assert (pnl["total_income"], pnl["total_expenses"], pnl["net_profit"]) == ("1000.00", "300.00", "700.00")
    bs = api.req("GET", ACCT, o(shop, "/accounting/balance-sheet"), params={"as_of": "2026-03-31"}).json()
    assert ({l["code"]: l["amount"] for l in bs["equity"]}, bs["current_earnings"], bs["balanced"]) == (
        {"3100": "700.00"}, "0.00", True)

    assert api.req("POST", ACCT, o(shop, "/accounting/year-end"), json={"period_end": "2026-03-31"}).status_code == 422
    assert api.req("POST", ACCT, o(shop, "/accounting/year-end"), json={"period_end": "2026-02-28"}).status_code == 422
    assert api.req("GET", SALES, o(shop, "/accounting/year-end")).status_code == 403

    # Reopening the year: the books must be reopened first, then the closing entry is reversed.
    rev_url = o(shop, f"/accounting/journal-entries/{closed['entry']['id']}/reverse")
    assert api.req("POST", ACCT, rev_url, json={"entry_date": "2026-03-31"}).status_code == 409
    assert api.req("PUT", ACCT, o(shop, "/accounting/lock"), json={"locked_until": None}).status_code == 200
    assert api.req("POST", ACCT, rev_url, json={"entry_date": "2026-03-31"}).status_code == 201
    assert api.req("GET", ACCT, o(shop, "/accounting/year-end")).json()["last_closed"] is None
    bal = balances(api, shop)
    assert (bal.get("3100", "0.00"), bal["4000"]) == ("0.00", "-1000.00")
    # Closing it again works, without locking this time.
    again = api.req("POST", ACCT, o(shop, "/accounting/year-end"), json={"period_end": "2026-03-31", "lock": False})
    assert again.status_code == 201, again.text
    assert again.json()["locked_until"] is None


def test_a_year_closes_on_the_last_day_of_its_fiscal_year(api, shop):
    from datetime import date

    from novara.modules.accounting.service import fiscal_year_of, next_year_end

    assert fiscal_year_of(1, date(2026, 5, 4)) == (date(2026, 1, 1), date(2026, 12, 31))
    assert fiscal_year_of(7, date(2026, 5, 4)) == (date(2025, 7, 1), date(2026, 6, 30))
    assert fiscal_year_of(7, date(2026, 7, 1)) == (date(2026, 7, 1), date(2027, 6, 30))
    assert fiscal_year_of(3, date(2024, 2, 29)) == (date(2023, 3, 1), date(2024, 2, 29))      # a leap-year end
    assert next_year_end(7, None, date(2026, 10, 24)) == date(2026, 6, 30)
    assert next_year_end(7, date(2026, 6, 30), date(2026, 10, 24)) is None                    # this year isn't over
    assert next_year_end(1, date(2024, 12, 31), date(2026, 10, 24)) == date(2025, 12, 31)

    org = f"/orgs/{shop['org']}"
    assert api.req("PATCH", OWNER, org, json={"fiscal_year_start_month": 13}).status_code == 422
    assert api.req("PATCH", OWNER, org, json={"fiscal_year_start_month": 7}).json()["fiscal_year_start_month"] == 7
    info = api.req("GET", ACCT, o(shop, "/accounting/year-end")).json()
    assert info["fiscal_year_start_month"] == 7 and info["current_year_start"][5:] == "07-01"
    assert info["current_year_end"][5:] == "06-30" and info["next_to_close"][5:] == "06-30"

    posted_invoice(api, shop, [{"product_id": shop["setup"], "quantity": "2"}], date="2025-03-01")
    wrong = api.req("POST", ACCT, o(shop, "/accounting/year-end"), json={"period_end": "2025-12-31"})
    assert wrong.status_code == 422 and "2025-06-30" in wrong.json()["detail"]
    closed = api.req("POST", ACCT, o(shop, "/accounting/year-end"), json={"period_end": "2025-06-30"})
    assert closed.status_code == 201, closed.text
    assert closed.json()["next_to_close"] in (None, "2026-06-30")
    # The boundary can't move while a closed year stands on it.
    assert api.req("PATCH", OWNER, org, json={"fiscal_year_start_month": 1}).status_code == 409
    assert api.req("PATCH", OWNER, org, json={"fiscal_year_start_month": 7}).status_code == 200   # unchanged is fine
