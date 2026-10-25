"""Exchange rates by day, and revaluing what customers still owe in other currencies at a period's end: the unrealized
difference is posted on the day and reversed the next, so later payments still realize theirs in full."""

from __future__ import annotations

from tests.test_accounting_extended import ACCT, OWNER, SALES, balances, o, shop  # noqa: F401 (shop is a fixture)
from tests.test_multi_currency import LINE, pay, usd_invoice


def rate(api, s, currency, day, value, user=ACCT):
    return api.req("POST", user, o(s, "/exchange-rates"), json={"currency": currency, "day": day, "rate": value})


def preview(api, s, day):
    r = api.req("GET", ACCT, o(s, "/fx-revaluations/preview"), params={"as_of": day})
    assert r.status_code == 200, r.text
    return r.json()


def revalue(api, s, day, rates=None, user=ACCT):
    return api.req("POST", user, o(s, "/fx-revaluations"), json={"as_of": day, "rates": rates or {}})


def test_what_is_owed_in_dollars_is_revalued_at_the_months_end(api, shop):
    inv = usd_invoice(api, shop)                                                   # 1,150 USD at 3.75 = 4,312.50
    assert pay(api, shop, inv, "500", "3.80").status_code == 201                    # 2026-04-20: 650 left, 2,437.50 booked
    assert rate(api, shop, "USD", "2026-04-30", "3.76").status_code == 201
    assert rate(api, shop, "SAR", "2026-04-30", "1").status_code == 422             # the organization's own currency
    assert rate(api, shop, "USD", "2026-04-30", "3.76", user=SALES).status_code == 403
    latest = api.req("GET", SALES, o(shop, "/exchange-rates/latest"), params={"currency": "USD", "on": "2026-05-15"})
    assert (latest.status_code, latest.json()["rate"], latest.json()["day"]) == (200, "3.76", "2026-04-30")

    p = preview(api, shop, "2026-04-30")
    assert (p["rates"], p["missing"], p["difference"]) == ({"USD": "3.76"}, [], "6.50")
    (line,) = p["lines"]
    assert (line["number"], line["due"], line["booked"], line["revalued"], line["difference"]) == (
        inv["number"], "650.00", "2437.50", "2444.00", "6.50")

    made = revalue(api, shop, "2026-04-30")
    assert made.status_code == 201, made.text
    assert (made.json()["status"], made.json()["difference"]) == ("posted", "6.50")
    at_end = balances(api, shop, as_of="2026-04-30")
    assert (at_end["1300"], at_end["4910"]) == ("2444.00", "-6.50")                 # at the month's value
    after = balances(api, shop, as_of="2026-05-01")
    assert (after["1300"], after.get("4910", "0.00")) == ("2437.50", "0.00")        # reversed the next day
    assert revalue(api, shop, "2026-04-30").status_code == 422                     # once per day

    # The last payment still realizes its whole difference against the invoice's own rate.
    last = pay(api, shop, inv, "650", "3.70", date="2026-05-10").json()
    assert last["exchange_difference"] == "-32.50"

    cancelled = api.req("POST", ACCT, o(shop, f"/fx-revaluations/{made.json()['id']}/cancel"))
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert balances(api, shop, as_of="2026-04-30")["1300"] == "2437.50"
    assert api.req("POST", ACCT, o(shop, f"/fx-revaluations/{made.json()['id']}/cancel")).status_code == 409
    listed = api.req("GET", ACCT, o(shop, "/fx-revaluations")).json()
    assert [r["status"] for r in listed] == ["cancelled"]


def test_the_day_decides_what_was_owed_and_which_rate_applies(api, shop):
    inv = usd_invoice(api, shop)
    pay(api, shop, inv, "500", "3.80")                                              # on 2026-04-20
    eur = api.req("POST", SALES, o(shop, "/invoices"), json={"customer_id": shop["customer"], "issue_date": "2026-04-05",
                                                               "currency": "EUR", "exchange_rate": "4.00", "lines": [LINE]})
    api.req("POST", ACCT, o(shop, f"/invoices/{eur.json()['id']}/post"))           # 1,000 EUR = 4,000.00

    before = preview(api, shop, "2026-04-15")                                       # before the payment
    assert (before["missing"], before["difference"]) == (["EUR", "USD"], None)
    assert {l["currency"]: l["due"] for l in before["lines"]} == {"USD": "1150.00", "EUR": "1000.00"}
    refused = revalue(api, shop, "2026-04-15")
    assert refused.status_code == 422 and "give the rate for EUR, USD" in refused.json()["detail"]

    made = revalue(api, shop, "2026-04-15", {"USD": "3.70", "EUR": "4.10"})
    assert made.status_code == 201, made.text
    diffs = {l["currency"]: l["difference"] for l in made.json()["lines"]}
    assert diffs == {"USD": "-57.50", "EUR": "100.00"}                              # 1,150 x 3.70 - 4,312.50; 4,100 - 4,000
    assert made.json()["difference"] == "42.50"
    days = {(r["currency"], r["day"]): r["rate"] for r in api.req("GET", ACCT, o(shop, "/exchange-rates")).json()}
    assert days[("USD", "2026-04-15")] == "3.7" and days[("EUR", "2026-04-15")] == "4.1"   # kept for next time

    # Nothing before the invoices existed; nothing to post when every rate is the booked one.
    assert preview(api, shop, "2026-03-31")["lines"] == []
    same = revalue(api, shop, "2026-04-02", {"USD": "3.75"})
    assert same.status_code == 422 and "nothing to post" in same.json()["detail"]


def test_closed_books_and_permissions(api, shop):
    usd_invoice(api, shop)
    assert api.req("PUT", ACCT, o(shop, "/accounting/lock"), json={"locked_until": "2026-04-30"}).status_code == 200
    locked = revalue(api, shop, "2026-04-30", {"USD": "3.80"})
    assert locked.status_code == 409 and "closed up to 2026-04-30" in locked.text
    assert revalue(api, shop, "2026-05-31", {"USD": "3.80"}, user=SALES).status_code == 403
    assert api.req("GET", SALES, o(shop, "/fx-revaluations/preview"), params={"as_of": "2026-05-31"}).status_code == 403
    assert revalue(api, shop, "2026-05-31", {"USD": "3.80"}, user=OWNER).status_code == 201
