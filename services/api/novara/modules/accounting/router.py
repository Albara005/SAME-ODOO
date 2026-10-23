from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara import idempotency
from novara.deps import OrgContext, client_ip, get_db, require, require_any
from novara.modules.access import permissions as P
from novara.modules.accounting.models import ACCOUNT_TYPES, Account, JournalEntry, JournalLine, Tax
from novara.modules.accounting.service import (CONTROL_ACCOUNTS, LedgerError, PeriodLocked, TrialBalanceRow, balance_sheet,
                                              close_year, ensure_chart, last_year_end, natural_balance,
                                              post_manual_entry, profit_and_loss, reverse_manual, system_account,
                                              trial_balance)
from novara.modules.audit import service as audit
from novara.money import fmt_decimal, from_minor, to_minor

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
    # Kept by documents (receivable, payable, inventory, goods received): no manual entries.
    control: bool = False


class AccountIn(BaseModel):
    code: str = Field(min_length=1, max_length=20, pattern=r"^[0-9A-Za-z.\-]+$")
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(pattern="^(" + "|".join(ACCOUNT_TYPES) + ")$")


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None


class JournalLineOut(BaseModel):
    account_id: str
    account_code: str
    account_name: str = ""
    debit: str
    credit: str
    customer_id: str | None
    vendor_id: str | None = None
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
    reversed_by_id: str | None = None
    lines: list[JournalLineOut]


class ManualLineIn(BaseModel):
    account_id: str
    debit: Decimal = Field(default=Decimal(0), ge=0, max_digits=18, decimal_places=3)
    credit: Decimal = Field(default=Decimal(0), ge=0, max_digits=18, decimal_places=3)
    label: str = Field(default="", max_length=300)


class ManualEntryIn(BaseModel):
    entry_date: date
    memo: str = Field(min_length=1, max_length=300)
    lines: list[ManualLineIn] = Field(min_length=2, max_length=100)


class ReverseIn(BaseModel):
    entry_date: date
    memo: str = Field(default="", max_length=300)


class YearEndIn(BaseModel):
    period_end: date
    # Also close the books up to the same day, so nothing can be dated into the closed year afterwards.
    lock: bool = True

    @model_validator(mode="after")
    def _past(self):
        if self.period_end >= date.today():
            raise ValueError("only a past day can end the year being closed")
        return self


class YearEndOut(BaseModel):
    last_closed: date | None
    locked_until: date | None
    entry: JournalEntryOut | None = None


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
    return AccountOut(id=a.id, code=a.code, name=a.name, type=a.type, system_key=a.system_key, active=a.active,
                      control=a.system_key in CONTROL_ACCOUNTS)


def _ledger(fn):
    try:
        return fn()
    except PeriodLocked:
        raise  # answered with 409 by the app's handler
    except LedgerError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


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
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    _: OrgContext = Depends(require(P.ACCOUNTING_READ)),
    db: Session = Depends(get_db),
):
    stmt = select(JournalEntry).order_by(JournalEntry.entry_date.desc(), JournalEntry.number.desc()).limit(limit)
    if source_type:
        stmt = stmt.where(JournalEntry.source_type == source_type)
    if source_id:
        stmt = stmt.where(JournalEntry.source_id == source_id)
    if date_from:
        stmt = stmt.where(JournalEntry.entry_date >= date_from)
    if date_to:
        stmt = stmt.where(JournalEntry.entry_date <= date_to)
    return entries_out(db, list(db.scalars(stmt)))


def entries_out(db: Session, entries: list[JournalEntry]) -> list[JournalEntryOut]:
    ids = [e.id for e in entries]
    lines = db.scalars(select(JournalLine).where(JournalLine.entry_id.in_(ids))).all()
    accounts = {a.id: a for a in db.scalars(select(Account))}
    reversed_by = dict(db.execute(select(JournalEntry.reversal_of_id, JournalEntry.id)
                                  .where(JournalEntry.reversal_of_id.in_(ids))).all())
    by_entry: dict[str, list[JournalLine]] = {}
    for l in lines:
        by_entry.setdefault(l.entry_id, []).append(l)
    return [
        JournalEntryOut(
            id=e.id, number=e.number, entry_date=e.entry_date, currency=e.currency, memo=e.memo,
            source_type=e.source_type, source_id=e.source_id, reversal_of_id=e.reversal_of_id,
            reversed_by_id=reversed_by.get(e.id),
            lines=[
                JournalLineOut(account_id=l.account_id, account_code=accounts[l.account_id].code,
                               account_name=accounts[l.account_id].name,
                               debit=str(from_minor(l.debit_minor, e.currency)),
                               credit=str(from_minor(l.credit_minor, e.currency)),
                               customer_id=l.customer_id, vendor_id=l.vendor_id, label=l.label)
                for l in sorted(by_entry.get(e.id, []),
                                key=lambda l: (l.credit_minor > 0, accounts[l.account_id].code))
            ],
        )
        for e in entries
    ]


@router.post("/orgs/{org_id}/accounting/journal-entries", response_model=JournalEntryOut,
             status_code=status.HTTP_201_CREATED)
def create_manual_entry(body: ManualEntryIn, request: Request, response: Response,
                        idempotency_key: str | None = Header(default=None, max_length=100),
                        ctx: OrgContext = Depends(require(P.ACCOUNTING_JOURNAL)), db: Session = Depends(get_db)):
    """A manual journal entry: accruals, depreciation, owner contributions, corrections between accounts.
    It must balance; accounts kept by documents (receivable, payable, inventory) are refused."""
    c = ctx.organization.default_currency
    h = idempotency.request_hash(body.model_dump(mode="json"))
    try:
        previous = idempotency.replayed(db, "journal.manual", idempotency_key, h)
    except idempotency.IdempotencyConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    if previous:
        response.status_code = status.HTTP_200_OK
        return entries_out(db, [db.get(JournalEntry, previous)])[0]
    lines = [(l.account_id, to_minor(l.debit, c), to_minor(l.credit, c), l.label) for l in body.lines]
    entry = _ledger(lambda: post_manual_entry(db, entry_date=body.entry_date, currency=c, memo=body.memo,
                                              lines=lines, created_by=ctx.user.id))
    idempotency.remember(db, "journal.manual", idempotency_key, h, entry.id)
    audit.record(db, actor_user_id=ctx.user.id, action="journal.posted", target_type="journal_entry",
                 target_id=entry.id, details={"number": entry.number, "memo": entry.memo,
                                              "amount": str(from_minor(sum(l[1] for l in lines), c))},
                 ip_address=client_ip(request))
    db.commit()
    return entries_out(db, [entry])[0]


@router.post("/orgs/{org_id}/accounting/journal-entries/{entry_id}/reverse", response_model=JournalEntryOut,
             status_code=status.HTTP_201_CREATED)
def reverse_journal_entry(entry_id: str, body: ReverseIn, request: Request,
                          ctx: OrgContext = Depends(require(P.ACCOUNTING_JOURNAL)), db: Session = Depends(get_db)):
    """Undo a manual entry or a year-end close with a mirror entry (entries are never edited or deleted)."""
    entry = db.get(JournalEntry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "journal entry not found")
    if entry.source_type == "year_end_close" and not ctx.has(P.ACCOUNTING_CLOSE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "reopening a closed year needs accounting.close")
    try:
        reversal = _ledger(lambda: reverse_manual(db, entry, entry_date=body.entry_date,
                                                  memo=body.memo or f"Reversal of {entry.number}",
                                                  created_by=ctx.user.id))
    except IntegrityError:  # another request reversed it a moment ago
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "this entry has already been reversed")
    audit.record(db, actor_user_id=ctx.user.id, action="journal.reversed", target_type="journal_entry",
                 target_id=entry.id, details={"number": entry.number, "reversal": reversal.number},
                 ip_address=client_ip(request))
    db.commit()
    return entries_out(db, [reversal])[0]


@router.post("/orgs/{org_id}/accounting/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(body: AccountIn, request: Request, ctx: OrgContext = Depends(require(P.ACCOUNTING_JOURNAL)),
                   db: Session = Depends(get_db)):
    ensure_chart(db)
    if db.scalar(select(Account.id).where(Account.code == body.code)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"account code {body.code} is already used")
    account = Account(code=body.code, name=body.name, type=body.type)
    db.add(account)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="account.created", target_type="account",
                 target_id=account.id, details={"code": account.code, "name": account.name, "type": account.type},
                 ip_address=client_ip(request))
    db.commit()
    return account_out(account)


@router.patch("/orgs/{org_id}/accounting/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: str, body: AccountUpdate, request: Request,
                   ctx: OrgContext = Depends(require(P.ACCOUNTING_JOURNAL)), db: Session = Depends(get_db)):
    """Rename or archive an account. Codes and types are fixed once created: reports depend on them."""
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")
    if body.active is False and account.system_key is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "the platform posts to this account; it can't be archived")
    if body.active is False:
        balance = db.scalar(select(func.coalesce(func.sum(JournalLine.debit_minor - JournalLine.credit_minor), 0))
                            .where(JournalLine.account_id == account.id))
        if balance:
            raise HTTPException(status.HTTP_409_CONFLICT, "move the balance to another account before archiving")
    changes = body.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(account, field, value)
    audit.record(db, actor_user_id=ctx.user.id, action="account.updated", target_type="account",
                 target_id=account.id, details={"code": account.code, **changes}, ip_address=client_ip(request))
    db.commit()
    return account_out(account)


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


# ---------------------------------------------------------------- closing the year


@router.get("/orgs/{org_id}/accounting/year-end", response_model=YearEndOut)
def get_year_end(ctx: OrgContext = Depends(require(P.ACCOUNTING_READ)), db: Session = Depends(get_db)):
    return YearEndOut(last_closed=last_year_end(db), locked_until=ctx.organization.books_locked_until)


@router.post("/orgs/{org_id}/accounting/year-end", response_model=YearEndOut, status_code=status.HTTP_201_CREATED)
def post_year_end(body: YearEndIn, request: Request, ctx: OrgContext = Depends(require(P.ACCOUNTING_CLOSE)),
                  db: Session = Depends(get_db)):
    """Close the year: income and expenses go to zero and the result moves into retained earnings.
    By default the books are closed up to the same day, so the closed year can't change afterwards."""
    org = ctx.organization
    entry = _ledger(lambda: close_year(db, period_end=body.period_end, currency=org.default_currency,
                                       created_by=ctx.user.id))
    if body.lock and (org.books_locked_until is None or org.books_locked_until < body.period_end):
        org.books_locked_until = body.period_end
    audit.record(db, actor_user_id=ctx.user.id, action="books.year_closed", target_type="organization",
                 target_id=org.id, details={"period_end": body.period_end.isoformat(), "entry": entry.number,
                                            "locked": body.lock}, ip_address=client_ip(request))
    db.commit()
    return YearEndOut(last_closed=last_year_end(db), locked_until=org.books_locked_until,
                      entry=entries_out(db, [entry])[0])
