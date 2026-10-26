"""Exchange rates from the European Central Bank's daily reference rates: worked out for the organization's currency
(the riyal through its dollar peg), saved for the currencies it keeps, never over a rate set by hand."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from novara.modules.invoicing import fx_feed
from novara.worker import fetch_rates
from tests.test_accounting_extended import ACCT, SALES, o, shop  # noqa: F401 (shop is a fixture)

ECB = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <gesmes:Sender><gesmes:name>European Central Bank</gesmes:name></gesmes:Sender>
  <Cube>
    <Cube time='{day}'>
      <Cube currency='USD' rate='{usd}'/>
      <Cube currency='JPY' rate='162.50'/>
      <Cube currency='GBP' rate='{gbp}'/>
      <Cube currency='CHF' rate='0.9800'/>
    </Cube>
  </Cube>
</gesmes:Envelope>"""


@pytest.fixture
def ecb(app):
    state = {"status": 200, "body": ECB.format(day="2026-04-30", usd="1.0800", gbp="0.8600"), "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        assert str(request.url) == fx_feed.ECB_URL
        return httpx.Response(state["status"], content=state["body"].encode())

    app.state.fx_http = httpx.Client(transport=httpx.MockTransport(handler))
    yield state
    app.state.fx_http.close()


def test_the_days_rates_are_saved_for_the_currencies_kept(api, shop, ecb, app):
    feed = lambda body, user=ACCT: api.req("PUT", user, o(shop, "/exchange-rates/feed"), json=body)  # noqa: E731
    assert api.req("GET", SALES, o(shop, "/exchange-rates/feed")).json()["enabled"] is False
    assert feed({"enabled": True, "currencies": ["USD"]}, user=SALES).status_code == 403
    assert feed({"enabled": True, "currencies": ["SAR", "USD"]}).status_code == 422          # its own currency
    assert feed({"enabled": True, "currencies": []}).status_code == 422
    assert feed({"enabled": True, "currencies": ["XYZ"]}).status_code == 422
    on = feed({"enabled": True, "currencies": ["usd", "EUR", "GBP", "AED", "KWD"]})
    assert on.status_code == 200 and on.json()["currencies"] == ["AED", "EUR", "GBP", "KWD", "USD"]
    assert on.json()["pegged"]["SAR"] == "3.75"

    # Someone set that day's euro rate by hand: it stays.
    assert api.req("POST", ACCT, o(shop, "/exchange-rates"),
                   json={"currency": "EUR", "day": "2026-04-30", "rate": "4.10"}).status_code == 201
    fetched = api.req("POST", ACCT, o(shop, "/exchange-rates/feed/fetch"))
    assert fetched.status_code == 200, fetched.text
    out = fetched.json()
    # SAR per euro = 1.08 x 3.75 = 4.05; a pound is 4.05 / 0.86; a dirham 4.05 / (1.08 x 3.6725).
    assert out == {"day": "2026-04-30", "saved": {"AED": "1.021103", "GBP": "4.709302", "USD": "3.75"},
                   "kept": ["EUR"], "unavailable": ["KWD"]}
    rates = {(r["currency"], r["source"]): r["rate"] for r in api.req("GET", SALES, o(shop, "/exchange-rates")).json()}
    assert rates == {("AED", "ecb"): "1.021103", ("GBP", "ecb"): "4.709302", ("USD", "ecb"): "3.75", ("EUR", "manual"): "4.1"}
    state = api.req("GET", SALES, o(shop, "/exchange-rates/feed")).json()
    assert (state["last_day"], "KWD" in state["last_error"]) == ("2026-04-30", True)
    latest = api.req("GET", SALES, o(shop, "/exchange-rates/latest"), params={"currency": "GBP", "on": "2026-05-02"})
    assert latest.json()["rate"] == "4.709302"                       # suggested on the next invoice

    # The worker: once per published day for every organization that keeps rates.
    assert fetch_rates(app.state.sessionmaker, app.state.fx_http) == {}     # that day is saved already
    ecb["body"] = ECB.format(day="2026-05-04", usd="1.0900", gbp="0.8500")
    assert fetch_rates(app.state.sessionmaker, app.state.fx_http) == {shop["org"]: ["AED", "EUR", "GBP", "USD"]}
    day2 = {r["currency"]: r["rate"] for r in api.req("GET", SALES, o(shop, "/exchange-rates")).json()
            if r["day"] == "2026-05-04"}
    assert (day2["USD"], day2["GBP"]) == ("3.75", "4.808824")          # the peg holds; 1.09 x 3.75 / 0.85

    # A rate set by hand over the feed's replaces it.
    assert api.req("POST", ACCT, o(shop, "/exchange-rates"),
                   json={"currency": "GBP", "day": "2026-05-04", "rate": "4.8"}).json()["source"] == "manual"

    # Switched off: the worker leaves it alone.
    assert feed({"enabled": False, "currencies": ["USD"]}).status_code == 200
    ecb["body"] = ECB.format(day="2026-05-05", usd="1.1000", gbp="0.8400")
    assert fetch_rates(app.state.sessionmaker, app.state.fx_http) == {}


def test_when_the_feed_cant_be_read(api, shop, ecb):
    assert api.req("POST", ACCT, o(shop, "/exchange-rates/feed/fetch")).status_code == 409   # nothing chosen yet
    api.req("PUT", ACCT, o(shop, "/exchange-rates/feed"), json={"enabled": True, "currencies": ["USD"]})
    ecb["status"] = 503
    down = api.req("POST", ACCT, o(shop, "/exchange-rates/feed/fetch"))
    assert down.status_code == 502 and "503" in down.json()["detail"]
    assert "503" in api.req("GET", ACCT, o(shop, "/exchange-rates/feed")).json()["last_error"]
    ecb["status"], ecb["body"] = 200, "<html>maintenance</html>"
    assert api.req("POST", ACCT, o(shop, "/exchange-rates/feed/fetch")).status_code == 502


def test_reading_the_reference_rates():
    rates = fx_feed.parse_ecb(ECB.format(day="2026-04-30", usd="1.0800", gbp="0.8600").encode())
    assert rates.rate("USD", "SAR") == Decimal("3.75") and rates.rate("SAR", "USD") == Decimal("0.266667")
    assert rates.rate("EUR", "AED") == Decimal("3.966300") and rates.rate("KWD", "SAR") is None
    assert "QAR" in rates.available() and "BHD" in rates.available()
    with pytest.raises(fx_feed.FeedError, match="no US dollar"):
        fx_feed.parse_ecb(ECB.replace("currency='USD'", "currency='XXX'").format(day="2026-04-30", usd="1", gbp="1").encode())
    with pytest.raises(fx_feed.FeedError, match="no date"):
        fx_feed.parse_ecb(ECB.format(day="soon", usd="1", gbp="1").encode())
    # Entities are never expanded and nothing is fetched while reading.
    bomb = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c SYSTEM "http://169.254.169.254/latest/meta-data/">]><x>&b;&c;</x>')
    with pytest.raises(fx_feed.FeedError):
        fx_feed.parse_ecb(bomb.encode())
