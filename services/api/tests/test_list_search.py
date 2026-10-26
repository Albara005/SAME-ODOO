"""Finding invoices and sales orders by part of their number or of the customer's name (the web app's search)."""

from __future__ import annotations

from tests.test_accounting_extended import ACCT, SALES, o, posted_invoice, shop  # noqa: F401 (shop is a fixture)


def test_invoices_and_orders_by_number_or_customer(api, shop):
    s = shop
    other = api.req("POST", SALES, o(s, "/customers"), json={"name": "مطاعم البيت الشامي"}).json()["id"]
    line = [{"product_id": s["chair"], "quantity": "1"}]
    first = posted_invoice(api, s, line)
    api.req("POST", SALES, o(s, "/invoices"), json={"customer_id": other, "issue_date": "2026-04-02", "lines": line})
    find = lambda what, q: [x.get("number") for x in api.req("GET", ACCT, o(s, what), params={"q": q}).json()]  # noqa: E731
    assert find("/invoices", "00001") == [first["number"]]
    assert find("/invoices", "riyadh") == [first["number"]]                  # the customer, whatever the case
    assert find("/invoices", "الشامي") == [None]                             # a draft has no number yet
    assert find("/invoices", "nothing like it") == []

    for customer in (s["customer"], other):
        api.req("POST", SALES, o(s, "/sales-orders"), json={"customer_id": customer, "order_date": "2026-04-01", "lines": line})
    assert find("/sales-orders", "SO-2026-00002") == ["SO-2026-00002"]
    assert len(find("/sales-orders", "البيت")) == 1
