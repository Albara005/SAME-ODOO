from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.deps import OrgContext, client_ip, get_db, require, require_any
from novara.modules.access import permissions as P
from novara.modules.accounting.models import Account, JournalEntry, JournalLine, Tax
from novara.modules.accounting.service import (TrialBalanceRow, balance_sheet, ensure_chart, natural_balance,
                                              profit_and_loss, system_account, trial_balance)
from novara.modules.audit import service as audit
from novara.money import fmt_decimal, from_minor

router = APIRouter(tags=["accounting"])


class TaxIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rate: Decimal = Field(ge=0, le=100, max_digits=7, decimal_places=4)
    account_id: str | None = Field(default=None, description="Defaults to the output VAT account")


class TaxUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    active: bool | None = None


class TaxOut(BaseModel):
    id: str
    name: str
    rate: str
    account_id: str
    active: bool


class AccountOut(BaseModel):
    id: str
    code: str
    name: str
    type: str
    system_key: str | None
    active: bool


class JournalLineOut(BaseModel):
    account_id: str
    account_code: str
    debit: str
    credit: str
    customer_id: str | None
    label: str


class JournalEntryOut(BaseModel):
    id: str
    number: str
    entry_date: date
    currency: str
    memo: str
    source_type: str
    source_id: str
    reversal_of_id: str | None
    lines: list[JournalLineOut]


class TrialBalanceLine(BaseModel):
    account_id: str
    code: str
    name: str
    type: str
    debit: str
    credit: str
    balance: str


class TrialBalanceOut(BaseModel):
    currency: str
    as_of: date | None
    lines: list[TrialBalanceLine]
    total_debit: str
    total_credit: str
    balanced: bool


def tax_out(t: Tax) -> TaxOut:
    return TaxOut(id=t.id, name=t.name, rate=fmt_decimal(t.rate), account_id=t.account_id, active=t.active)


def account_out(a: Account) -> AccountOut:
    return AccountOut(id=a.id, code=a.code, name=a.name, type=a.type, system_key=a.system_key, active=a.active)


@router.get("/orgs/{org_id}/taxes", response_model=list[TaxOut])
def list_taxes(include_inactive: bool = False,
               _: OrgContext = Depends(require_any(P.PRODUCTS_READ, P.INVOICES_READ, P.TAXES_MANAGE)),
               db: Session = Depends(get_db)):
    stmt = select(Tax).order_by(Tax.name)
    if not include_inactive:
        stmt = stmt.where(Tax.active.is_(True))
    return [tax_out(t) for t in db.scalars(stmt)]


@router.post("/orgs/{org_id}/taxes", response_model=TaxOut, status_code=status.HTTP_201_CREATED)
def create_tax(body: TaxIn, request: Request, ctx: OrgContext = Depends(require(P.TAXES_MANAGE)),
               db: Session = Depends(get_db)):
    if body.account_id is None:
        account = system_account(db, "vat_output")
    else:
        account = db.get(Account, body.account_id)
        if account is None or account.type != "liability":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "tax account must be a liability account")
    tax = Tax(name=body.name, rate=body.rate, account_id=account.id)
    db.add(tax)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="tax.created", target_type="tax", target_id=tax.id,
                 details={"name": tax.name, "rate": str(body.rate)}, ip_address=client_ip(request))
    db.commit()
    return tax_out(tax)


@router.patch("/orgs/{org_id}/taxes/{tax_id}", response_model=TaxOut)
def update_tax(tax_id: str, body: TaxUpdate, request: Request, ctx: OrgContext = Depends(require(P.TAXES_MANAGE)),
               db: Session = Depends(get_db)):
    """Rename or (de)activate. Rates are fixed: create a new tax when a rate changes."""
    tax = db.get(Tax, tax_id)
    if tax is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tax not found")
    changes = body.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(tax, field, value)
    audit.record(db, actor_user_id=ctx.user.id, action="tax.updated", target_type="tax", target_id=tax.id,
                 details=changes, ip_address=client_ip(request))
    db.commit()
    return tax_out(tax)


@router.get("/orgs/{org_id}/accounting/accounts", response_model=list[AccountOut])
def list_accounts(_: OrgContext = Depends(require(P.ACCOUNTING_READ)), db: Session = Depends(get_db)):
    ensure_chart(db)
    db.commit()
    return [account_out(a) for a in db.scalars(select(Account).order_by(Account.code))]


@router.get("/orgs/{org_id}/accounting/journal-entries", response_model=list[JournalEntryOut])
def list_journal_entries(
    source_type: str | None = None,
    source_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    _: OrgContext = Depends(require(P.ACCOUNTING_READ)),
    db: Session = Depends(get_db),
):
    stmt = select(JournalEntry).order_by(JournalEntry.entry_date.desc(), JournalEntry.number.desc()).limit(limit)
    if source_type:
        stmt = stmt.where(JournalEntry.source_type == source_type)
    if source_id:
        stmt = stmt.where(JournalEntry.source_id == source_id)
    entries = db.scalars(stmt).all()
    lines = db.scalars(select(JournalLine).where(JournalLine.entry_id.in_([e.id for e in entries]))).all()
    codes = {a.id: a.code for a in db.scalars(select(Account))}
    by_entry: dict[str, list[JournalLine]] = {}
    for l in lines:
        by_entry.setdefault(l.entry_id, []).append(l)
    return [
        JournalEntryOut(
            id=e.id, number=e.number, entry_date=e.entry_date, currency=e.currency, memo=e.memo,
            source_type=e.source_type, source_id=e.source_id, reversal_of_id=e.reversal_of_id,
            lines=[
                JournalLineOut(account_id=l.account_id, account_code=codes[l.account_id],
                               debit=str(from_minor(l.debit_minor, e.currency)),
                               credit=str(from_minor(l.credit_minor, e.currency)),
                               customer_id=l.customer_id, label=l.label)
                for l in sorted(by_entry.get(e.id, []), key=lambda l: (l.credit_minor > 0, codes[l.account_id]))
            ],
        )
        for e in entries
    ]


@router.get("/orgs/{org_id}/accounting/trial-balance", response_model=TrialBalanceOut)
def get_trial_balance(as_of: date | None = None, ctx: OrgContext = Depends(require(P.ACCOUNTING_READ)),
                      db: Session = Depends(get_db)):
    currency = ctx.organization.default_currency
    rows = trial_balance(db, as_of=as_of)
    total_debit = sum(r.debit_minor for r in rows)
    total_credit = sum(r.credit_minor for r in rows)
    m = lambda v: str(from_minor(v, currency))  # noqa: E731
    return TrialBalanceOut(
        currency=currency,
        as_of=as_of,
        lines=[
            TrialBalanceLine(account_id=r.account.id, code=r.account.code, name=r.account.name, type=r.account.type,
                             debit=m(r.debit_minor), credit=m(r.credit_minor), balance=m(r.balance_minor))
            for r in rows
        ],
        total_debit=m(total_debit),
        total_credit=m(total_credit),
        balanced=total_debit == total_credit,
    )


# ---------------------------------------------------------------- financial statements


class StatementLine(BaseModel):
    account_id: str
    code: str
    name: str
    amount: str


class ProfitAndLossOut(BaseModel):
    currency: str
    date_from: date | None
    date_to: date | None
    income: list[StatementLine]
    expenses: list[StatementLine]
    total_income: str
    total_expenses: str
    net_profit: str


class BalanceSheetOut(BaseModel):
    currency: str
    as_of: date | None
    assets: list[StatementLine]
    liabilities: list[StatementLine]
    equity: list[StatementLine]
    current_earnings: str
    total_assets: str
    total_liabilities: str
    total_equity: str
    total_liabilities_and_equity: str
    balanced: bool


def _lines(rows: list[TrialBalanceRow], currency: str) -> list[StatementLine]:
    return [StatementLine(account_id=r.account.id, code=r.account.code, name=r.account.name,
                          amount=str(from_minor(natural_balance(r), currency)))
            for r in rows if natural_balance(r)]


@router.get("/orgs/{org_id}/accounting/profit-and-loss", response_model=ProfitAndLossOut)
def get_profit_and_loss(date_from: date | None = None, date_to: date | None = None,
                        ctx: OrgContext = Depends(require(P.ACCOUNTING_READ)), db: Session = Depends(get_db)):
    if date_from and date_to and date_to < date_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "date_to cannot be before date_from")
    c = ctx.organization.default_currency
    pnl = profit_and_loss(db, date_from=date_from, date_to=date_to)
    m = lambda v: str(from_minor(v, c))  # noqa: E731
    return ProfitAndLossOut(currency=c, date_from=date_from, date_to=date_to, income=_lines(pnl.income, c),
                            expenses=_lines(pnl.expenses, c), total_income=m(pnl.total_income),
                            total_expenses=m(pnl.total_expenses), net_profit=m(pnl.net_profit))


@router.get("/orgs/{org_id}/accounting/balance-sheet", response_model=BalanceSheetOut)
def get_balance_sheet(as_of: date | None = None, ctx: OrgContext = Depends(require(P.ACCOUNTING_READ)),
                      db: Session = Depends(get_db)):
    c = ctx.organization.default_currency
    bs = balance_sheet(db, as_of=as_of)
    m = lambda v: str(from_minor(v, c))  # noqa: E731
    return BalanceSheetOut(
        currency=c, as_of=as_of, assets=_lines(bs.assets, c), liabilities=_lines(bs.liabilities, c),
        equity=_lines(bs.equity, c), current_earnings=m(bs.earnings_minor), total_assets=m(bs.total_assets),
        total_liabilities=m(bs.total_liabilities), total_equity=m(bs.total_equity),
        total_liabilities_and_equity=m(bs.total_liabilities + bs.total_equity),
        balanced=bs.total_assets == bs.total_liabilities + bs.total_equity,
    )


# ---------------------------------------------------------------- closing the books


class LockIn(BaseModel):
    # Null reopens every period.
    locked_until: date | None


class LockOut(BaseModel):
    locked_until: date | None


@router.get("/orgs/{org_id}/accounting/lock", response_model=LockOut)
def get_lock(ctx: OrgContext = Depends(require(P.ACCOUNTING_READ))):
    return LockOut(locked_until=ctx.organization.books_locked_until)


@router.put("/orgs/{org_id}/accounting/lock", response_model=LockOut)
def set_lock(body: LockIn, request: Request, ctx: OrgContext = Depends(require(P.ACCOUNTING_CLOSE)),
             db: Session = Depends(get_db)):
    """Close the books up to a date: no invoice, payment, stock move or other entry can be dated on or before it."""
    if body.locked_until is not None and body.locked_until >= date.today():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "only past days can be closed")
    previous = ctx.organization.books_locked_until
    ctx.organization.books_locked_until = body.locked_until
    reopened = previous is not None and (body.locked_until is None or body.locked_until < previous)
    audit.record(db, actor_user_id=ctx.user.id, action="books.reopened" if reopened else "books.closed",
                 target_type="organization", target_id=ctx.organization.id,
                 details={"from": previous.isoformat() if previous else None,
                          "to": body.locked_until.isoformat() if body.locked_until else None},
                 ip_address=client_ip(request))
    db.commit()
    return LockOut(locked_until=ctx.organization.books_locked_until)
