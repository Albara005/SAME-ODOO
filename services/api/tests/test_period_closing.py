"""Closing the books month by month: a checklist before, the lock after, and reopening only with a reason."""

from __future__ import annotations

from tests.test_accounting_extended import ACCT, OWNER, SALES, o, posted_invoice, shop  # noqa: F401

LINE = {"description": "Chairs", "quantity": "2", "unit_price": "500"}


def months(api, s, year_of="2026-05-15", user=ACCT):
    r = api.req("GET", user, o(s, "/accounting/periods"), params={"year_of": year_of})
    assert r.status_code == 200, r.text
    return r.json()


def by_end(out):
    return {m["end"]: m for m in out["months"]}


def test_months_are_closed_in_order_and_reopened_with_a_reason(api, shop):
    posted_invoice(api, shop, [LINE | {"tax_id": shop["vat"]}], date="2026-04-01")
    draft = api.req("POST", SALES, o(shop, "/invoices"), json={"customer_id": shop["customer"], "issue_date": "2026-03-10",
                                                               "lines": [LINE]}).json()
    out = months(api, shop)
    assert (out["fiscal_year_start"], out["fiscal_year_end"], len(out["months"])) == ("2026-01-01", "2026-12-31", 12)
    m = by_end(out)
    assert (m["2026-03-31"]["draft_invoices"], m["2026-03-31"]["status"]) == (1, "open")
    assert (m["2026-04-30"]["income"], m["2026-04-30"]["net"]) == ("1000.00", "1000.00")
    assert api.req("GET", SALES, o(shop, "/accounting/periods")).status_code == 403
    close = lambda end, user=ACCT: api.req("POST", user, o(shop, "/accounting/periods/close"), json={"period_end": end})  # noqa: E731
    assert close("2026-03-31", SALES).status_code == 403
    assert close("2026-02-15").status_code == 409                       # not a month end
    assert close("2026-12-31").status_code == 409                       # not over yet

    closed = close("2026-03-31")
    assert closed.status_code == 200, closed.text
    m = by_end(closed.json())
    assert [m[e]["status"] for e in ("2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30")] == ["closed"] * 3 + ["open"]
    assert (m["2026-03-31"]["last_action"], m["2026-03-31"]["last_action_by"]) == ("closed", ACCT)
    assert closed.json()["locked_until"] == "2026-03-31"
    assert close("2026-02-28").status_code == 409                       # already closed
    assert api.req("POST", ACCT, o(shop, f"/invoices/{draft['id']}/post")).status_code == 409   # dated in March

    reopen = lambda end, reason="Missing supplier bill for March": api.req(  # noqa: E731
        "POST", ACCT, o(shop, "/accounting/periods/reopen"), json={"period_end": end, "reason": reason})
    assert reopen("2026-02-28").status_code == 409                      # only the latest closed month
    assert reopen("2026-03-31", reason="oops").status_code == 422       # a reason is needed
    reopened = reopen("2026-03-31")
    assert reopened.status_code == 200, reopened.text
    m = by_end(reopened.json())
    assert (m["2026-03-31"]["status"], m["2026-03-31"]["last_action"], m["2026-03-31"]["last_reason"]) == (
        "open", "reopened", "Missing supplier bill for March")
    assert m["2026-02-28"]["status"] == "closed" and reopened.json()["locked_until"] == "2026-02-28"
    assert api.req("POST", ACCT, o(shop, f"/invoices/{draft['id']}/post")).status_code == 200
    actions = [e["action"] for e in api.req("GET", OWNER, o(shop, "/audit-logs")).json()]
    assert {"books.period_closed", "books.period_reopened"} <= set(actions)


def test_a_month_in_a_closed_year_stays_closed_and_years_follow_the_fiscal_start(api, shop):
    posted_invoice(api, shop, [LINE | {"tax_id": shop["vat"]}], date="2025-06-01")
    assert api.req("POST", ACCT, o(shop, "/accounting/year-end"), json={"period_end": "2025-12-31"}).status_code == 201
    m = by_end(months(api, shop, year_of="2025-07-01"))
    assert all(x["status"] == "closed" for x in m.values())
    refused = api.req("POST", ACCT, o(shop, "/accounting/periods/reopen"),
                      json={"period_end": "2025-12-31", "reason": "Wrong VAT on a June invoice"})
    assert refused.status_code == 409 and "retained earnings" in refused.json()["detail"]

    assert api.req("PATCH", OWNER, f"/orgs/{shop['org']}", json={"fiscal_year_start_month": 4}).status_code == 409
    other = api.create_org(OWNER, "april-co")
    api.req("PATCH", OWNER, f"/orgs/{other}", json={"fiscal_year_start_month": 4})
    out = api.req("GET", OWNER, f"/orgs/{other}/accounting/periods", params={"year_of": "2026-02-10"}).json()
    assert (out["fiscal_year_start"], out["fiscal_year_end"], out["months"][0]["end"]) == ("2025-04-01", "2026-03-31", "2025-04-30")
