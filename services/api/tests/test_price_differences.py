"""A vendor bills received goods at another price than the order's: GRNI still clears at the order's price,
goods still in stock take the difference into their value, and only the share of goods already gone is a
purchase price difference."""

from __future__ import annotations

from tests.test_accounting_extended import ACCT, OWNER, SALES, WH, balances, o, shop  # noqa: F401


def ordered_and_received(api, s, cost="200", qty="10"):
    vid = api.req("POST", OWNER, o(s, "/vendors"), json={"name": "Gulf Supplies"}).json()["id"]
    po = api.req("POST", OWNER, o(s, "/purchase-orders"), json={
        "vendor_id": vid, "order_date": "2026-05-01",
        "lines": [{"product_id": s["chair"], "quantity": qty, "unit_cost": cost, "tax_id": s["vat"]}]}).json()
    url = o(s, f"/purchase-orders/{po['id']}")
    api.req("POST", OWNER, url + "/confirm")
    assert api.req("POST", WH, url + "/receipts", json={"move_date": "2026-05-03"}).status_code == 201
    bill = api.req("POST", OWNER, url + "/bill", json={"bill_date": "2026-05-04", "vendor_reference": "GS-881"}).json()
    return bill


def stock(api, s):
    level = next(l for l in api.req("GET", OWNER, o(s, "/inventory/levels")).json() if l["product_id"] == s["chair"])
    return level["quantity"], level["value"], level["average_cost"]


def count(api, s, qty, date):
    r = api.req("POST", WH, o(s, "/inventory/adjustments"), json={
        "move_date": date, "lines": [{"product_id": s["chair"], "counted_quantity": qty}]})
    assert r.status_code == 201, r.text


def test_goods_still_in_stock_take_the_vendors_price(api, shop):
    bill = ordered_and_received(api, shop)
    line = bill["lines"][0]
    assert (line["unit_cost"], line["ordered_unit_cost"]) == ("200", "200")
    price = o(shop, f"/bills/{bill['id']}/lines/{line['id']}/price")
    assert api.req("PUT", SALES, price, json={"unit_cost": "210"}).status_code == 403
    changed = api.req("PUT", OWNER, price, json={"unit_cost": "210"})
    assert changed.status_code == 200, changed.text
    changed = changed.json()
    assert (changed["subtotal"], changed["tax"], changed["total"]) == ("2100.00", "315.00", "2415.00")
    assert (changed["lines"][0]["unit_cost"], changed["lines"][0]["ordered_unit_cost"]) == ("210", "200")

    posted = api.req("POST", ACCT, o(shop, f"/bills/{bill['id']}/post"))
    assert posted.status_code == 200, posted.text
    bal = balances(api, shop)
    # GRNI clears at the order's 2,000; all ten chairs are still here, so the extra 100 is their cost.
    assert (bal.get("2150", "0.00"), bal["1400"], bal.get("5150", "0.00"), bal["2100"]) == ("0.00", "2100.00", "0.00", "-2415.00")
    assert stock(api, shop) == ("10", "2100.00", "210")
    moves = api.req("GET", OWNER, o(shop, "/inventory/moves")).json()
    assert any(m["kind"] == "revaluation" and m["value"] == "100.00" for m in moves)
    assert api.req("PUT", OWNER, price, json={"unit_cost": "200"}).status_code == 409        # posted: final

    # Cancelling the bill takes the revaluation back out with everything else.
    cancel = api.req("POST", ACCT, o(shop, f"/bills/{bill['id']}/cancel"), json={"cancel_date": "2026-05-05"})
    assert cancel.status_code == 200, cancel.text
    bal = balances(api, shop)
    assert (bal["1400"], bal.get("5150", "0.00"), bal["2150"]) == ("2000.00", "0.00", "-2000.00")
    assert stock(api, shop) == ("10", "2000.00", "200")


def test_a_lower_price_lowers_the_stocks_cost_and_only_order_lines_take_a_vendor_price(api, shop):
    bill = ordered_and_received(api, shop, cost="200", qty="3")
    line = bill["lines"][0]
    price = o(shop, f"/bills/{bill['id']}/lines/{line['id']}/price")
    assert api.req("PUT", OWNER, price, json={"unit_cost": "-1"}).status_code == 422
    assert api.req("PUT", OWNER, price, json={"unit_cost": "190.5"}).json()["subtotal"] == "571.50"
    assert api.req("POST", ACCT, o(shop, f"/bills/{bill['id']}/post")).status_code == 200
    bal = balances(api, shop)
    assert (bal.get("2150", "0.00"), bal["1400"], bal.get("5150", "0.00")) == ("0.00", "571.50", "0.00")   # 3 x 9.50 cheaper
    assert stock(api, shop) == ("3", "571.50", "190.5")

    vid = api.req("GET", OWNER, o(shop, "/vendors")).json()[0]["id"]
    direct = api.req("POST", OWNER, o(shop, "/bills"), json={
        "vendor_id": vid, "bill_date": "2026-05-04",
        "lines": [{"description": "Cleaning", "quantity": "1", "unit_cost": "80"}]}).json()
    assert api.req("PUT", OWNER, o(shop, f"/bills/{direct['id']}/lines/{direct['lines'][0]['id']}/price"),
                   json={"unit_cost": "90"}).status_code == 422                                # edit the draft instead


def test_goods_already_gone_keep_their_share_as_a_price_difference(api, shop):
    bill = ordered_and_received(api, shop)                 # 10 chairs at 200
    count(api, shop, "4", "2026-05-03")                    # 6 are gone before the bill arrives (at 200 each)
    line = bill["lines"][0]
    api.req("PUT", OWNER, o(shop, f"/bills/{bill['id']}/lines/{line['id']}/price"), json={"unit_cost": "210"})
    assert api.req("POST", ACCT, o(shop, f"/bills/{bill['id']}/post")).status_code == 200
    bal = balances(api, shop)
    # Of the 100 extra, the 4 chairs left take 40 (now 210 each); the 6 gone leave 60 as a price difference.
    assert (bal["1400"], bal["5150"], bal.get("2150", "0.00")) == ("840.00", "60.00", "0.00")
    assert stock(api, shop) == ("4", "840.00", "210")

    # The last four go too (at 210), then the bill is cancelled: their 40 can't come back out of stock,
    # so it moves to price differences and the ledger still equals the stock's value (zero).
    count(api, shop, "0", "2026-05-06")
    cancel = api.req("POST", ACCT, o(shop, f"/bills/{bill['id']}/cancel"), json={"cancel_date": "2026-05-07"})
    assert cancel.status_code == 200, cancel.text
    bal = balances(api, shop)
    assert (bal.get("1400", "0.00"), bal["5150"], bal["2150"]) == ("0.00", "-40.00", "-2000.00")
    assert stock(api, shop) == ("0", "0.00", "0")
