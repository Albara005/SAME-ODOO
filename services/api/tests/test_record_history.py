"""A record's history: what happened to it and who did it, for whoever may read the record (the IP addresses the
audit log keeps are left out)."""

from __future__ import annotations

from tests.test_accounting_extended import ACCT, OWNER, SALES, WH, o, posted_invoice, shop  # noqa: F401 (shop is a fixture)


def test_an_invoices_history_for_its_readers(api, shop):
    s = shop
    inv = posted_invoice(api, s, [{"product_id": s["chair"], "quantity": "1"}])
    api.req("POST", ACCT, o(s, f"/invoices/{inv['id']}/payments"), json={"amount": "575", "payment_date": "2026-04-05", "method": "bank"})
    history = api.req("GET", SALES, o(s, f"/history/invoice/{inv['id']}"))
    assert history.status_code == 200, history.text
    rows = history.json()
    assert [r["action"] for r in rows][:3] == ["invoice.payment_registered", "invoice.posted", "invoice.created"]
    assert rows[0]["actor_name"] == ACCT and "ip_address" not in rows[0]
    assert api.req("GET", WH, o(s, f"/history/invoice/{inv['id']}")).status_code == 403   # can't read invoices
    assert api.req("GET", OWNER, o(s, f"/history/membership/{inv['id']}")).status_code == 404
    assert api.req("GET", OWNER, o(s, "/history/invoice/someone-elses")).json() == []
