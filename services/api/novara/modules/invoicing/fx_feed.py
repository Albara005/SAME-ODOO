"""Exchange rates fetched every working day from the European Central Bank's reference rates.

The ECB publishes, around 16:00 Central European time on its working days, how many units of about thirty currencies
one euro buys. Currencies pegged to the US dollar by their central bank are worked out from the dollar's rate at the
peg (the Saudi riyal at 3.75, the UAE dirham at 3.6725...), so an organization whose books are in riyals gets a rate
for every currency the feed has. A day's rate for the organization's currency is then

    units of the organization's currency per euro / units of the other currency per euro

rounded to six decimals, saved for the ECB's date unless someone set that day's rate by hand (theirs stays). An
organization chooses which currencies it keeps; the worker fetches the feed at most once an hour.

    GET/PUT /orgs/{org}/exchange-rates/feed          on or off, and which currencies
    POST    /orgs/{org}/exchange-rates/feed/fetch    fetch now
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from lxml import etree
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara.db import utcnow
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit
from novara.modules.invoicing.models import ExchangeRate, FxFeed
from novara.modules.invoicing.service import base_currency
from novara.money import CURRENCY_DIGITS, fmt_decimal
from novara.tenancy import system_scope

router = APIRouter(tags=["invoicing"])

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_NS = "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"
# Units per US dollar, fixed by each central bank.
USD_PEGS = {"SAR": Decimal("3.75"), "AED": Decimal("3.6725"), "QAR": Decimal("3.64"), "BHD": Decimal("0.376"),
            "OMR": Decimal("0.3845"), "JOD": Decimal("0.709")}
PLACES = Decimal("0.000001")
MAX_FEED_BYTES = 200_000


class FeedError(RuntimeError):
    """The feed couldn't be read (HTTP 502 when fetched by hand)."""


@dataclass
class Rates:
    day: date
    per_euro: dict[str, Decimal]       # units of each currency one euro buys (EUR itself is 1)

    def available(self) -> list[str]:
        return sorted(self.per_euro)

    def rate(self, currency: str, base: str) -> Decimal | None:
        """Units of base per unit of currency, or None when the feed has either one."""
        if currency not in self.per_euro or base not in self.per_euro:
            return None
        return (self.per_euro[base] / self.per_euro[currency]).quantize(PLACES, rounding=ROUND_HALF_UP)


def parse_ecb(content: bytes) -> Rates:
    """The ECB's daily reference rates XML, with the dollar pegs added."""
    if len(content) > MAX_FEED_BYTES:
        raise FeedError("the ECB's answer is too large")
    try:
        root = etree.fromstring(content, parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))
    except etree.XMLSyntaxError:
        raise FeedError("the ECB's answer isn't the reference rates") from None
    day_cube = root.find(f".//{{{ECB_NS}}}Cube[@time]")
    if day_cube is None:
        raise FeedError("the ECB's answer has no rates")
    try:
        day = date.fromisoformat(day_cube.get("time", ""))
    except ValueError:
        raise FeedError("the ECB's answer has no date") from None
    per_euro: dict[str, Decimal] = {"EUR": Decimal(1)}
    for cube in day_cube.findall(f"{{{ECB_NS}}}Cube"):
        currency, raw = cube.get("currency", ""), cube.get("rate", "")
        try:
            value = Decimal(raw)
        except InvalidOperation:
            continue
        if len(currency) == 3 and currency.isalpha() and currency.isupper() and value > 0:
            per_euro[currency] = value
    if "USD" not in per_euro:
        raise FeedError("the ECB's answer has no US dollar rate")
    for currency, peg in USD_PEGS.items():
        per_euro.setdefault(currency, per_euro["USD"] * peg)
    return Rates(day=day, per_euro=per_euro)


def fetch(http: httpx.Client | None = None) -> Rates:
    client = http or httpx.Client(timeout=15)
    try:
        r = client.get(ECB_URL, headers={"Accept": "application/xml"})
    except httpx.HTTPError as e:
        raise FeedError(f"couldn't reach the ECB ({type(e).__name__})") from None
    finally:
        if http is None:
            client.close()
    if r.status_code != 200:
        raise FeedError(f"the ECB answered {r.status_code}")
    return parse_ecb(r.content)


# ---------------------------------------------------------------- an organization's rates


def feed_of(db: Session) -> FxFeed | None:
    return db.scalar(select(FxFeed))


@dataclass
class Saved:
    day: date
    saved: dict[str, Decimal] = field(default_factory=dict)
    kept: list[str] = field(default_factory=list)        # set by hand for that day: left as it was
    unavailable: list[str] = field(default_factory=list)  # the feed has no rate for these


def apply(db: Session, feed: FxFeed, rates: Rates) -> Saved:
    """Save the day's rates for the currencies the organization keeps."""
    base = base_currency(db)
    out = Saved(day=rates.day)
    if base not in rates.per_euro:
        feed.last_error = f"the feed has no rate for {base}, the organization's currency"
        out.unavailable = list(feed.currencies)
        return out
    for currency in feed.currencies:
        value = rates.rate(currency, base)
        if value is None:
            out.unavailable.append(currency)
            continue
        row = db.scalar(select(ExchangeRate).where(ExchangeRate.currency == currency, ExchangeRate.day == rates.day))
        if row is not None and row.source == "manual":
            out.kept.append(currency)
            continue
        if row is None:
            db.add(ExchangeRate(currency=currency, day=rates.day, rate=value, source="ecb"))
        else:
            row.rate = value
        out.saved[currency] = value
    feed.last_day, feed.last_run_at = rates.day, utcnow()
    feed.last_error = f"no rate in the feed for {', '.join(out.unavailable)}" if out.unavailable else ""
    db.flush()
    return out


def organizations_with_feed(db: Session) -> list[str]:
    if db.get_bind().dialect.name == "postgresql":
        return list(db.execute(text("SELECT app_orgs_with_fx_feed()")).scalars())
    with system_scope(db):
        return list(db.scalars(select(FxFeed.organization_id).where(FxFeed.enabled.is_(True))))


# ---------------------------------------------------------------- API


class FeedIn(BaseModel):
    enabled: bool
    currencies: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("currencies")
    @classmethod
    def _codes(cls, v: list[str]) -> list[str]:
        codes = sorted({c.strip().upper() for c in v})
        bad = [c for c in codes if c not in CURRENCY_DIGITS]
        if bad:
            raise ValueError(f"unknown currency: {', '.join(bad)}")
        return codes


class FeedOut(BaseModel):
    enabled: bool
    currencies: list[str]
    last_day: date | None
    last_run_at: datetime | None
    last_error: str
    source: str
    pegged: dict[str, str]              # currencies worked out from the US dollar at their peg


class FetchOut(BaseModel):
    day: date
    saved: dict[str, str]
    kept: list[str]
    unavailable: list[str]


def _feed_out(feed: FxFeed | None) -> FeedOut:
    return FeedOut(enabled=bool(feed and feed.enabled), currencies=list(feed.currencies) if feed else [],
                   last_day=feed.last_day if feed else None, last_run_at=feed.last_run_at if feed else None,
                   last_error=feed.last_error if feed else "", source="European Central Bank reference rates",
                   pegged={c: fmt_decimal(p) for c, p in USD_PEGS.items()})


@router.get("/orgs/{org_id}/exchange-rates/feed", response_model=FeedOut)
def get_feed(_: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    return _feed_out(feed_of(db))


@router.put("/orgs/{org_id}/exchange-rates/feed", response_model=FeedOut)
def put_feed(body: FeedIn, request: Request, ctx: OrgContext = Depends(require(P.ACCOUNTING_JOURNAL)),
             db: Session = Depends(get_db)):
    base = base_currency(db)
    if base in body.currencies:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{base} is the organization's own currency")
    if body.enabled and not body.currencies:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "choose the currencies to keep rates for")
    feed = feed_of(db)
    if feed is None:
        feed = FxFeed(enabled=body.enabled, currencies=body.currencies, last_error="")
        db.add(feed)
    else:
        feed.enabled, feed.currencies = body.enabled, body.currencies
    audit.record(db, actor_user_id=ctx.user.id, action="exchange_rate.feed_changed", target_type="organization",
                 target_id=ctx.organization.id, details={"enabled": body.enabled, "currencies": body.currencies},
                 ip_address=client_ip(request))
    try:
        db.commit()
    except IntegrityError:                          # two first saves at once
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "the feed was just changed; try again")
    return _feed_out(feed)


@router.post("/orgs/{org_id}/exchange-rates/feed/fetch", response_model=FetchOut)
def fetch_now(request: Request, ctx: OrgContext = Depends(require(P.ACCOUNTING_JOURNAL)), db: Session = Depends(get_db)):
    feed = feed_of(db)
    if feed is None or not feed.currencies:
        raise HTTPException(status.HTTP_409_CONFLICT, "choose the currencies to keep rates for first")
    try:
        rates = fetch(request.app.state.fx_http)
    except FeedError as e:
        feed.last_error = str(e)[:300]
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    out = apply(db, feed, rates)
    audit.record(db, actor_user_id=ctx.user.id, action="exchange_rate.fetched", target_type="organization",
                 target_id=ctx.organization.id, details={"day": rates.day.isoformat(),
                                                         "saved": {c: fmt_decimal(v) for c, v in out.saved.items()}},
                 ip_address=client_ip(request))
    db.commit()
    return FetchOut(day=out.day, saved={c: fmt_decimal(v) for c, v in out.saved.items()}, kept=out.kept,
                    unavailable=out.unavailable)
