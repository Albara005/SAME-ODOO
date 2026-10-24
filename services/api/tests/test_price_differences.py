"""A vendor bills received goods at another price than the order's: GRNI still clears, the difference is booked."""

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


def test_the_vendors_price_is_billed_and_the_difference_is_booked(api, shop):
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
    # The receipt credited GRNI 2,000 at the order's price; the bill clears exactly that, and the
    # extra 100 the vendor charged is a purchase price difference.
    assert (bal.get("2150", "0.00"), bal["5150"], bal["2100"], bal["1500"]) == ("0.00", "100.00", "-2415.00", "315.00")
    assert api.req("PUT", OWNER, price, json={"unit_cost": "200"}).status_code == 409        # posted: final

    # Cancelling the bill takes the difference back out with everything else.
    cancel = api.req("POST", ACCT, o(shop, f"/bills/{bill['id']}/cancel"), json={"cancel_date": "2026-05-05"})
    assert cancel.status_code == 200, cancel.text
    bal = balances(api, shop)
    assert (bal.get("5150", "0.00"), bal["2150"]) == ("0.00", "-2000.00")


def test_a_lower_price_is_a_credit_and_only_order_lines_take_a_vendor_price(api, shop):
    bill = ordered_and_received(api, shop, cost="200", qty="3")
    line = bill["lines"][0]
    price = o(shop, f"/bills/{bill['id']}/lines/{line['id']}/price")
    assert api.req("PUT", OWNER, price, json={"unit_cost": "-1"}).status_code == 422
    assert api.req("PUT", OWNER, price, json={"unit_cost": "190.5"}).json()["subtotal"] == "571.50"
    assert api.req("POST", ACCT, o(shop, f"/bills/{bill['id']}/post")).status_code == 200
    bal = balances(api, shop)
    assert (bal.get("2150", "0.00"), bal["5150"]) == ("0.00", "-28.50")                        # 3 x 9.50 cheaper

    vid = api.req("GET", OWNER, o(shop, "/vendors")).json()[0]["id"]
    direct = api.req("POST", OWNER, o(shop, "/bills"), json={
        "vendor_id": vid, "bill_date": "2026-05-04",
        "lines": [{"description": "Cleaning", "quantity": "1", "unit_cost": "80"}]}).json()
    assert api.req("PUT", OWNER, o(shop, f"/bills/{direct['id']}/lines/{direct['lines'][0]['id']}/price"),
                   json={"unit_cost": "90"}).status_code == 422                                # edit the draft instead
