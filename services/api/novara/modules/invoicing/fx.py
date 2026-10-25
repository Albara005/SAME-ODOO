"""Exchange rates by day, and revaluing what customers still owe in other currencies at a period's end.

An invoice in another currency is booked at its own rate (service.py) and payments clear it at that rate, with the
difference realized as they come in. At a period's end what is still owed is worth something else at that day's
rate: the difference is an unrealized gain or loss, so the balance sheet shows the receivable at today's value.

For each invoice open on the day (in its currency: total less payments and credit notes dated up to that day):

    booked    = its total in the organization's currency, less what payments and credit notes up to the day cleared
    revalued  = what is owed x the day's rate
    Dr/Cr Accounts Receivable (with the customer)      revalued - booked
        Cr/Dr Unrealized Exchange Gains and Losses (4910)

posted on the day and reversed the next day, so the books carry on from the invoice's own rate and every payment
still realizes its full difference. A revaluation can be cancelled (both entries are reversed, each on its own
date) while neither date is in closed books.

    GET/POST /orgs/{org}/exchange-rates               rates by day (POST adds or replaces a day's rate)
    DELETE   /orgs/{org}/exchange-rates/{id}
    GET      /orgs/{org}/exchange-rates/latest        the rate on or before a day, to suggest on a form
    GET      /orgs/{org}/fx-revaluations/preview      what a revaluation on a day would post (nothing is saved)
    GET/POST /orgs/{org}/fx-revaluations
    POST     /orgs/{org}/fx-revaluations/{id}/cancel
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.accounting.models import Account, JournalEntry, JournalLine
from novara.modules.accounting.service import (LedgerError, LineSpec, PeriodLocked, post_entry, reverse_entry,
                                               system_account)
from novara.modules.audit import service as audit
from novara.modules.invoicing.models import CreditNote, ExchangeRate, FxRevaluation, Invoice, Payment
from novara.modules.invoicing.service import base_currency
from novara.money import CURRENCY_DIGITS, convert, fmt_decimal, from_minor

router = APIRouter(tags=["invoicing"])


class FxError(ValueError):
    """The request can't be done as asked (HTTP 422)."""


# ---------------------------------------------------------------- rates


def rate_on(db: Session, currency: str, day: date) -> ExchangeRate | None:
    return db.scalar(select(ExchangeRate).where(ExchangeRate.currency == currency, ExchangeRate.day <= day)
                     .order_by(ExchangeRate.day.desc()).limit(1))


def set_rate(db: Session, currency: str, day: date, rate: Decimal, user_id: str) -> ExchangeRate:
    row = db.scalar(select(ExchangeRate).where(ExchangeRate.currency == currency, ExchangeRate.day == day))
    if row is None:
        row = ExchangeRate(currency=currency, day=day, rate=rate, created_by=user_id)
        db.add(row)
    else:
        row.rate, row.created_by = rate, user_id
    db.flush()
    return row


# ---------------------------------------------------------------- revaluation


def _open_lines(db: Session, as_of: date) -> list[dict]:
    """Every invoice in another currency that still had something owed at the end of the day."""
    base = base_currency(db)
    out = []
    invoices = db.scalars(select(Invoice).where(Invoice.currency != base, Invoice.status != "draft",
                                                Invoice.issue_date <= as_of, Invoice.posted_at.is_not(None))
                          .order_by(Invoice.issue_date, Invoice.number))
    for inv in invoices:
        if inv.status == "cancelled" and (inv.cancelled_at is None or inv.cancelled_at.date() <= as_of):
            continue
        paid, cleared = db.execute(
            select(func.coalesce(func.sum(Payment.amount_minor), 0),
                   func.coalesce(func.sum(Payment.base_amount_minor - Payment.fx_minor), 0))
            .where(Payment.invoice_id == inv.id, Payment.payment_date <= as_of)).one()
        credited, credit_cleared = db.execute(
            select(func.coalesce(func.sum(CreditNote.applied_minor), 0),
                   func.coalesce(func.sum(CreditNote.base_applied_minor), 0))
            .where(CreditNote.invoice_id == inv.id, CreditNote.issue_date <= as_of)).one()
        due = inv.total_minor - int(paid) - int(credited)
        if due <= 0:
            continue
        out.append({"invoice_id": inv.id, "number": inv.number, "customer_id": inv.customer_id, "currency": inv.currency,
                    "due_minor": due, "booked_minor": inv.base_total_minor - int(cleared) - int(credit_cleared)})
    return out


def work_out(db: Session, as_of: date, given: dict[str, Decimal]) -> tuple[dict[str, Decimal], list[dict], list[str]]:
    """The rates used (given, else the latest on or before the day), each open invoice revalued, and the currencies
    with no rate."""
    base = base_currency(db)
    lines = _open_lines(db, as_of)
    rates: dict[str, Decimal] = {}
    missing: list[str] = []
    for currency in sorted({l["currency"] for l in lines}):
        if currency in given:
            rates[currency] = given[currency]
        elif (row := rate_on(db, currency, as_of)) is not None:
            rates[currency] = row.rate
        else:
            missing.append(currency)
    for l in lines:
        rate = rates.get(l["currency"])
        l["revalued_minor"] = convert(l["due_minor"], l["currency"], rate, base) if rate else None
        l["difference_minor"] = l["revalued_minor"] - l["booked_minor"] if rate else None
    return rates, lines, missing


def revalue(db: Session, as_of: date, given: dict[str, Decimal], user_id: str) -> FxRevaluation:
    if db.scalar(select(FxRevaluation.id).where(FxRevaluation.as_of == as_of, FxRevaluation.status == "posted")):
        raise FxError(f"what is owed was already revalued on {as_of.isoformat()}: cancel that one first")
    rates, lines, missing = work_out(db, as_of, given)
    if missing:
        raise FxError(f"give the rate for {', '.join(missing)} on {as_of.isoformat()}")
    lines = [l for l in lines if l["difference_minor"]]
    if not lines:
        raise FxError("nothing owed in another currency changed value: there is nothing to post")
    base = base_currency(db)
    receivable, unrealized = system_account(db, "receivable"), system_account(db, "fx_unrealized")
    specs = []
    for l in lines:
        d = l["difference_minor"]
        label = f"Revaluation of {l['number']} at {fmt_decimal(rates[l['currency']])}"
        specs.append(LineSpec(receivable, debit_minor=max(d, 0), credit_minor=max(-d, 0), customer_id=l["customer_id"],
                              label=label))
    net = sum(l["difference_minor"] for l in lines)
    specs.append(LineSpec(unrealized, debit_minor=max(-net, 0), credit_minor=max(net, 0),
                          label=f"Unrealized exchange difference on {as_of.isoformat()}"))
    doc = FxRevaluation(as_of=as_of, status="posted", rates={c: fmt_decimal(r) for c, r in rates.items()}, lines=lines,
                        difference_minor=net, created_by=user_id)
    db.add(doc)
    db.flush()
    memo = f"Revaluation of receivables in other currencies on {as_of.isoformat()}"
    entry = post_entry(db, entry_date=as_of, currency=base, lines=specs, memo=memo, source_type="fx_revaluation",
                       source_id=doc.id, created_by=user_id)
    reversal = reverse_entry(db, entry, entry_date=as_of + timedelta(days=1), memo=f"Reversal: {memo}", created_by=user_id)
    doc.entry_id, doc.reversal_entry_id = entry.id, reversal.id
    for currency, rate in given.items():
        if currency in rates:
            set_rate(db, currency, as_of, rate, user_id)
    return doc


def _mirror(db: Session, entry: JournalEntry, memo: str, user_id: str) -> JournalEntry:
    """The same lines the other way round, on the entry's own date. (Not a reversal: the revaluation entry already
    has its reversal, the next day's, and an entry is reversed only once.)"""
    lines = db.scalars(select(JournalLine).where(JournalLine.entry_id == entry.id)).all()
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_({l.account_id for l in lines})))}
    return post_entry(db, entry_date=entry.entry_date, currency=entry.currency, memo=memo, source_type=entry.source_type,
                      source_id=entry.source_id, created_by=user_id, lines=[
                          LineSpec(accounts[l.account_id], debit_minor=l.credit_minor, credit_minor=l.debit_minor,
                                   customer_id=l.customer_id, label=f"Cancelled: {l.label}"[:300]) for l in lines])


def cancel(db: Session, doc: FxRevaluation, user_id: str) -> None:
    """Undo both entries, each on its own date (refused if either date is in closed books)."""
    if doc.status != "posted":
        raise FxError("this revaluation is already cancelled")
    memo = f"Cancellation of the revaluation on {doc.as_of.isoformat()}"
    doc.cancel_entry_id = _mirror(db, db.get(JournalEntry, doc.entry_id), memo, user_id).id
    doc.cancel_reversal_entry_id = _mirror(db, db.get(JournalEntry, doc.reversal_entry_id), memo, user_id).id
    doc.status = "cancelled"


# ---------------------------------------------------------------- API


class RateIn(BaseModel):
    currency: str = Field(pattern="^[A-Z]{3}$")
    day: date
    rate: Decimal = Field(gt=0, max_digits=18, decimal_places=8)


class RateOut(BaseModel):
    id: str
    currency: str
    day: date
    rate: str


class RevaluationIn(BaseModel):
    as_of: date
    rates: dict[str, Decimal] = Field(default_factory=dict, max_length=20)


class RevaluationLineOut(BaseModel):
    invoice_id: str
    number: str | None
    currency: str
    due: str
    booked: str
    revalued: str | None
    difference: str | None


class RevaluationPreviewOut(BaseModel):
    as_of: date
    base_currency: str
    rates: dict[str, str]
    missing: list[str]                  # currencies with open invoices and no rate on or before the day
    lines: list[RevaluationLineOut]
    difference: str | None              # the net unrealized gain (negative: a loss), once every rate is known


class RevaluationOut(BaseModel):
    id: str
    as_of: date
    status: Literal["posted", "cancelled"]
    base_currency: str
    rates: dict[str, str]
    lines: list[RevaluationLineOut]
    difference: str
    entry_id: str | None
    reversal_entry_id: str | None
    created_at: datetime


def _line_out(l: dict, base: str) -> RevaluationLineOut:
    m = lambda minor, c: str(from_minor(minor, c)) if minor is not None else None  # noqa: E731
    return RevaluationLineOut(invoice_id=l["invoice_id"], number=l["number"], currency=l["currency"],
                              due=m(l["due_minor"], l["currency"]), booked=m(l["booked_minor"], base),
                              revalued=m(l["revalued_minor"], base), difference=m(l["difference_minor"], base))


def _doc_out(db: Session, d: FxRevaluation) -> RevaluationOut:
    base = base_currency(db)
    return RevaluationOut(id=d.id, as_of=d.as_of, status=d.status, base_currency=base, rates=d.rates,
                          lines=[_line_out(l, base) for l in d.lines], difference=str(from_minor(d.difference_minor, base)),
                          entry_id=d.entry_id, reversal_entry_id=d.reversal_entry_id, created_at=d.created_at)


def _given(db: Session, rates: dict[str, Decimal]) -> dict[str, Decimal]:
    base = base_currency(db)
    for currency in rates:
        if currency not in CURRENCY_DIGITS or currency == base:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"not a rate this organization needs: {currency}")
    return dict(rates)


@router.get("/orgs/{org_id}/exchange-rates", response_model=list[RateOut])
def list_rates(currency: str | None = Query(default=None, pattern="^[A-Z]{3}$"),
               _: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    q = select(ExchangeRate).order_by(ExchangeRate.day.desc(), ExchangeRate.currency).limit(200)
    if currency:
        q = q.where(ExchangeRate.currency == currency)
    return [RateOut(id=r.id, currency=r.currency, day=r.day, rate=fmt_decimal(r.rate)) for r in db.scalars(q)]


@router.get("/orgs/{org_id}/exchange-rates/latest", response_model=RateOut | None)
def latest_rate(currency: str = Query(pattern="^[A-Z]{3}$"), on: date | None = None,
                _: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    r = rate_on(db, currency, on or date.today())
    return RateOut(id=r.id, currency=r.currency, day=r.day, rate=fmt_decimal(r.rate)) if r else None


@router.post("/orgs/{org_id}/exchange-rates", response_model=RateOut, status_code=status.HTTP_201_CREATED)
def add_rate(body: RateIn, request: Request, ctx: OrgContext = Depends(require(P.ACCOUNTING_JOURNAL)),
             db: Session = Depends(get_db)):
    _given(db, {body.currency: body.rate})
    r = set_rate(db, body.currency, body.day, body.rate, ctx.user.id)
    audit.record(db, actor_user_id=ctx.user.id, action="exchange_rate.set", target_type="exchange_rate", target_id=r.id,
                 details={"currency": body.currency, "day": body.day.isoformat(), "rate": fmt_decimal(body.rate)},
                 ip_address=client_ip(request))
    try:
        db.commit()
    except IntegrityError:                          # the same day at the same moment
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "that day's rate was just set; try again")
    return RateOut(id=r.id, currency=r.currency, day=r.day, rate=fmt_decimal(r.rate))


@router.delete("/orgs/{org_id}/exchange-rates/{rate_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_rate(rate_id: str, request: Request, ctx: OrgContext = Depends(require(P.ACCOUNTING_JOURNAL)),
                db: Session = Depends(get_db)):
    r = db.get(ExchangeRate, rate_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rate not found")
    db.delete(r)
    audit.record(db, actor_user_id=ctx.user.id, action="exchange_rate.deleted", target_type="exchange_rate", target_id=rate_id,
                 details={"currency": r.currency, "day": r.day.isoformat()}, ip_address=client_ip(request))
    db.commit()


@router.get("/orgs/{org_id}/fx-revaluations/preview", response_model=RevaluationPreviewOut)
def preview(as_of: date, _: OrgContext = Depends(require(P.ACCOUNTING_READ)), db: Session = Depends(get_db)):
    base = base_currency(db)
    rates, lines, missing = work_out(db, as_of, {})
    net = None if missing else sum(l["difference_minor"] for l in lines)
    return RevaluationPreviewOut(as_of=as_of, base_currency=base, rates={c: fmt_decimal(r) for c, r in rates.items()},
                                 missing=missing, lines=[_line_out(l, base) for l in lines],
                                 difference=str(from_minor(net, base)) if net is not None else None)


@router.get("/orgs/{org_id}/fx-revaluations", response_model=list[RevaluationOut])
def list_revaluations(_: OrgContext = Depends(require(P.ACCOUNTING_READ)), db: Session = Depends(get_db)):
    rows = db.scalars(select(FxRevaluation).order_by(FxRevaluation.as_of.desc(), FxRevaluation.created_at.desc()))
    return [_doc_out(db, d) for d in rows]


@router.post("/orgs/{org_id}/fx-revaluations", response_model=RevaluationOut, status_code=status.HTTP_201_CREATED)
def create_revaluation(body: RevaluationIn, request: Request, ctx: OrgContext = Depends(require(P.ACCOUNTING_JOURNAL)),
                       db: Session = Depends(get_db)):
    try:
        doc = revalue(db, body.as_of, _given(db, body.rates), ctx.user.id)
    except PeriodLocked:
        raise                                       # closed books: the app answers 409
    except (FxError, LedgerError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    audit.record(db, actor_user_id=ctx.user.id, action="fx.revalued", target_type="fx_revaluation", target_id=doc.id,
                 details={"as_of": body.as_of.isoformat(), "rates": doc.rates,
                          "difference": str(from_minor(doc.difference_minor, base_currency(db)))},
                 ip_address=client_ip(request))
    try:
        db.commit()
    except IntegrityError:                          # two at once for the same day
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"what is owed was already revalued on {body.as_of.isoformat()}")
    return _doc_out(db, doc)


@router.post("/orgs/{org_id}/fx-revaluations/{doc_id}/cancel", response_model=RevaluationOut)
def cancel_revaluation(doc_id: str, request: Request, ctx: OrgContext = Depends(require(P.ACCOUNTING_JOURNAL)),
                       db: Session = Depends(get_db)):
    doc = db.scalar(select(FxRevaluation).where(FxRevaluation.id == doc_id).with_for_update())
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "revaluation not found")
    try:
        cancel(db, doc, ctx.user.id)
    except FxError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    audit.record(db, actor_user_id=ctx.user.id, action="fx.revaluation_cancelled", target_type="fx_revaluation",
                 target_id=doc.id, details={"as_of": doc.as_of.isoformat()}, ip_address=client_ip(request))
    db.commit()
    return _doc_out(db, doc)
