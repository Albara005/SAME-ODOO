"""Ledger operations. All journal entries are created through ``post_entry``.

Rules enforced here (and again by the database on PostgreSQL):
* every entry balances: total debit == total credit, at least two lines;
* each line is either a debit or a credit, never both, never negative;
* entries and lines are never updated or deleted; mistakes are fixed with
  ``reverse_entry``;
* nothing is dated on or before the organization's lock date (closed books).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from novara.modules.accounting.models import Account, JournalEntry, JournalLine, NumberSequence
from novara.modules.organizations.models import Organization
from novara.tenancy import current_tenant

# A small, country-neutral starting chart. Organizations can add accounts later;
# system_key marks the ones the platform posts to automatically.
DEFAULT_CHART: list[tuple[str, str, str, str | None]] = [
    ("1100", "Cash", "asset", "cash"),
    ("1200", "Bank", "asset", "bank"),
    # Paid through an online payment link, not yet paid out by the provider (then moved to the bank).
    ("1250", "Online Payments Clearing", "asset", "online_clearing"),
    ("1300", "Accounts Receivable", "asset", "receivable"),
    ("1400", "Inventory", "asset", "inventory"),
    ("1500", "Input VAT Recoverable", "asset", "vat_input"),
    ("2100", "Accounts Payable", "liability", "payable"),
    ("2150", "Goods Received Not Invoiced", "liability", "grni"),
    ("2200", "Output VAT Payable", "liability", "vat_output"),
    ("3000", "Owner's Equity", "equity", "equity"),
    ("3100", "Retained Earnings", "equity", "retained_earnings"),
    ("4000", "Sales Revenue", "income", "sales"),
    ("5000", "Cost of Goods Sold", "expense", "cogs"),
    ("5100", "Inventory Adjustments", "expense", "inventory_adjustment"),
    # A vendor billing received goods at another price than the order's: the difference lands here.
    ("5150", "Purchase Price Differences", "expense", "price_variance"),
    ("6000", "General Expenses", "expense", "expense"),
]


class LedgerError(ValueError):
    pass


class LedgerImmutable(RuntimeError):
    pass


class PeriodLocked(LedgerError):
    """The date falls in closed books (HTTP 409)."""


# Accounts with a subledger behind them (customer and vendor balances, stock value, goods awaiting a
# bill). Only documents post to them, so the subledgers always agree with the ledger.
CONTROL_ACCOUNTS = ("receivable", "payable", "inventory", "grni")
YEAR_END = "year_end_close"


@dataclass(frozen=True)
class LineSpec:
    account: Account
    debit_minor: int = 0
    credit_minor: int = 0
    customer_id: str | None = None
    vendor_id: str | None = None
    label: str = ""


def lock_date(db: Session) -> date | None:
    org = db.get(Organization, current_tenant(db))
    return org.books_locked_until if org is not None else None


def check_open(db: Session, entry_date: date) -> None:
    locked = lock_date(db)
    if locked is not None and entry_date <= locked:
        raise PeriodLocked(f"the books are closed up to {locked.isoformat()}; use a later date")


def ensure_chart(db: Session) -> dict[str, Account]:
    """Create the default chart of accounts for the bound organization if it has none. Idempotent."""
    accounts = list(db.scalars(select(Account)))
    codes = {a.code for a in accounts}
    keys = {a.system_key for a in accounts if a.system_key}
    by_code = {a.code: a for a in accounts}
    for code, name, type_, key in DEFAULT_CHART:
        if key is not None and key in keys:
            continue
        if code in codes:
            # Account exists from an older chart version; give it its system role if it has none yet.
            if key is not None and by_code[code].system_key is None and by_code[code].type == type_:
                by_code[code].system_key = key
                keys.add(key)
            continue
        account = Account(code=code, name=name, type=type_, system_key=key)
        db.add(account)
        accounts.append(account)
    db.flush()
    return {a.system_key: a for a in accounts if a.system_key}


def system_account(db: Session, key: str) -> Account:
    account = db.scalar(select(Account).where(Account.system_key == key))
    if account is None:
        accounts = ensure_chart(db)
        if key not in accounts:
            raise LedgerError(f"no account is configured for '{key}'")
        account = accounts[key]
    return account


def next_number(db: Session, key: str, prefix: str, width: int = 5) -> str:
    """Next gap-free number for ``key``. Locks the sequence row until the transaction ends."""
    seq = db.scalar(select(NumberSequence).where(NumberSequence.key == key).with_for_update())
    if seq is None:
        seq = NumberSequence(key=key, next_value=1)
        db.add(seq)
        db.flush()
    value = seq.next_value
    seq.next_value = value + 1
    return f"{prefix}{value:0{width}d}"


def post_entry(
    db: Session,
    *,
    entry_date: date,
    currency: str,
    lines: list[LineSpec],
    memo: str = "",
    source_type: str = "",
    source_id: str = "",
    created_by: str | None = None,
    reversal_of_id: str | None = None,
) -> JournalEntry:
    lines = [l for l in lines if l.debit_minor or l.credit_minor]
    if len(lines) < 2:
        raise LedgerError("a journal entry needs at least two non-zero lines")
    for l in lines:
        if l.debit_minor < 0 or l.credit_minor < 0 or (l.debit_minor and l.credit_minor):
            raise LedgerError("each line must be a positive debit or a positive credit")
    debit = sum(l.debit_minor for l in lines)
    credit = sum(l.credit_minor for l in lines)
    if debit != credit:
        raise LedgerError(f"unbalanced entry: debit {debit} != credit {credit}")
    check_open(db, entry_date)

    entry = JournalEntry(
        number=next_number(db, f"journal:{entry_date.year}", f"JE-{entry_date.year}-", 6),
        entry_date=entry_date,
        currency=currency,
        memo=memo,
        source_type=source_type,
        source_id=source_id,
        created_by=created_by,
        reversal_of_id=reversal_of_id,
    )
    db.add(entry)
    db.flush()
    db.add_all(
        JournalLine(
            entry_id=entry.id,
            account_id=l.account.id,
            debit_minor=l.debit_minor,
            credit_minor=l.credit_minor,
            customer_id=l.customer_id,
            vendor_id=l.vendor_id,
            label=l.label,
        )
        for l in lines
    )
    db.flush()
    return entry


def reverse_entry(db: Session, entry: JournalEntry, *, entry_date: date, memo: str,
                  created_by: str | None = None) -> JournalEntry:
    originals = db.scalars(select(JournalLine).where(JournalLine.entry_id == entry.id)).all()
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_({l.account_id for l in originals})))}
    return post_entry(
        db,
        entry_date=entry_date,
        currency=entry.currency,
        memo=memo,
        source_type=entry.source_type,
        source_id=entry.source_id,
        created_by=created_by,
        reversal_of_id=entry.id,
        lines=[
            LineSpec(accounts[l.account_id], debit_minor=l.credit_minor, credit_minor=l.debit_minor,
                     customer_id=l.customer_id, vendor_id=l.vendor_id, label=f"Reversal: {l.label}"[:300])
            for l in originals
        ],
    )


@dataclass(frozen=True)
class TrialBalanceRow:
    account: Account
    debit_minor: int
    credit_minor: int

    @property
    def balance_minor(self) -> int:
        return self.debit_minor - self.credit_minor


def trial_balance(db: Session, *, as_of: date | None = None, date_from: date | None = None,
                  exclude_sources: tuple[str, ...] = ()) -> list[TrialBalanceRow]:
    stmt = (
        select(JournalLine.account_id, func.sum(JournalLine.debit_minor), func.sum(JournalLine.credit_minor))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .group_by(JournalLine.account_id)
    )
    if exclude_sources:
        stmt = stmt.where(JournalEntry.source_type.not_in(exclude_sources))
    if as_of is not None:
        stmt = stmt.where(JournalEntry.entry_date <= as_of)
    if date_from is not None:
        stmt = stmt.where(JournalEntry.entry_date >= date_from)
    sums = {acc: (int(d or 0), int(c or 0)) for acc, d, c in db.execute(stmt)}
    accounts = db.scalars(select(Account).order_by(Account.code)).all()
    return [TrialBalanceRow(a, *sums[a.id]) for a in accounts if a.id in sums]


# Which side increases each account type: assets and expenses grow with debits, the rest with credits.
DEBIT_NORMAL = {"asset", "expense"}


def natural_balance(row: TrialBalanceRow) -> int:
    return row.balance_minor if row.account.type in DEBIT_NORMAL else -row.balance_minor


@dataclass(frozen=True)
class ProfitAndLoss:
    income: list[TrialBalanceRow]
    expenses: list[TrialBalanceRow]

    @property
    def total_income(self) -> int:
        return sum(natural_balance(r) for r in self.income)

    @property
    def total_expenses(self) -> int:
        return sum(natural_balance(r) for r in self.expenses)

    @property
    def net_profit(self) -> int:
        return self.total_income - self.total_expenses


def profit_and_loss(db: Session, *, date_from: date | None, date_to: date | None) -> ProfitAndLoss:
    # Year-end closing entries move the result into retained earnings; they are not income or expenses.
    rows = trial_balance(db, as_of=date_to, date_from=date_from, exclude_sources=(YEAR_END,))
    return ProfitAndLoss(income=[r for r in rows if r.account.type == "income"],
                         expenses=[r for r in rows if r.account.type == "expense"])


@dataclass(frozen=True)
class BalanceSheet:
    assets: list[TrialBalanceRow]
    liabilities: list[TrialBalanceRow]
    equity: list[TrialBalanceRow]
    # Income minus expenses up to the date: not closed into equity by an entry, so shown on its own line.
    earnings_minor: int

    @property
    def total_assets(self) -> int:
        return sum(natural_balance(r) for r in self.assets)

    @property
    def total_liabilities(self) -> int:
        return sum(natural_balance(r) for r in self.liabilities)

    @property
    def total_equity(self) -> int:
        return sum(natural_balance(r) for r in self.equity) + self.earnings_minor


def balance_sheet(db: Session, *, as_of: date | None) -> BalanceSheet:
    rows = trial_balance(db, as_of=as_of)
    pnl = ProfitAndLoss(income=[r for r in rows if r.account.type == "income"],
                        expenses=[r for r in rows if r.account.type == "expense"])
    return BalanceSheet(assets=[r for r in rows if r.account.type == "asset"],
                        liabilities=[r for r in rows if r.account.type == "liability"],
                        equity=[r for r in rows if r.account.type == "equity"], earnings_minor=pnl.net_profit)


# ---------------------------------------------------------------- manual entries and closing the year


def post_manual_entry(db: Session, *, entry_date: date, currency: str, memo: str,
                      lines: list[tuple[str, int, int, str]], created_by: str) -> JournalEntry:
    """An accountant's own entry. ``lines`` are (account id, debit, credit, label)."""
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_({l[0] for l in lines})))}
    specs = []
    for n, (account_id, debit, credit, label) in enumerate(lines, start=1):
        account = accounts.get(account_id)
        if account is None:
            raise LedgerError(f"line {n}: unknown account")
        if not account.active:
            raise LedgerError(f"line {n}: account {account.code} is archived")
        if account.system_key in CONTROL_ACCOUNTS:
            raise LedgerError(f"line {n}: {account.code} {account.name} is kept by documents "
                              "(invoices, bills, payments, stock); post to it through them")
        if (debit > 0) == (credit > 0):
            raise LedgerError(f"line {n}: enter either a debit or a credit")
        specs.append(LineSpec(account, debit_minor=debit, credit_minor=credit, label=label))
    return post_entry(db, entry_date=entry_date, currency=currency, lines=specs, memo=memo,
                      source_type="manual", created_by=created_by)


REVERSIBLE_SOURCES = ("manual", YEAR_END)


def reverse_manual(db: Session, entry: JournalEntry, *, entry_date: date, memo: str,
                   created_by: str) -> JournalEntry:
    """Reverse an accountant's own entry or a year-end close. Document entries are reversed by
    cancelling or crediting the document, so the document and the ledger never disagree."""
    if entry.source_type not in REVERSIBLE_SOURCES:
        raise LedgerError("this entry belongs to a document; cancel or credit the document instead")
    if entry.reversal_of_id is not None:
        raise LedgerError("this entry is itself a reversal")
    if db.scalar(select(JournalEntry.id).where(JournalEntry.reversal_of_id == entry.id)) is not None:
        raise LedgerError("this entry has already been reversed")
    if entry_date < entry.entry_date:
        raise LedgerError("the reversal cannot be dated before the entry")
    return reverse_entry(db, entry, entry_date=entry_date, memo=memo, created_by=created_by)


def last_year_end(db: Session) -> date | None:
    """The date of the latest year-end close still in force (not reversed)."""
    reversed_ids = select(JournalEntry.reversal_of_id).where(JournalEntry.reversal_of_id.is_not(None))
    return db.scalar(select(func.max(JournalEntry.entry_date)).where(
        JournalEntry.source_type == YEAR_END, JournalEntry.reversal_of_id.is_(None),
        JournalEntry.id.not_in(reversed_ids)))


def fiscal_year_of(start_month: int, day: date) -> tuple[date, date]:
    """First and last day of the fiscal year that contains ``day`` (it starts on the 1st of ``start_month``)."""
    year = day.year if day.month >= start_month else day.year - 1
    return date(year, start_month, 1), date(year + 1, start_month, 1) - timedelta(days=1)


def next_year_end(start_month: int, last_closed: date | None, today: date) -> date | None:
    """The next fiscal year end to close: the one after the last close, or the year before this one.
    None while that year hasn't ended yet."""
    if last_closed is not None:
        candidate = fiscal_year_of(start_month, last_closed + timedelta(days=1))[1]
    else:
        candidate = fiscal_year_of(start_month, today)[0] - timedelta(days=1)
    return candidate if candidate < today else None


def close_year(db: Session, *, period_end: date, currency: str, created_by: str, fiscal_start: int = 1) -> JournalEntry:
    """Move the result of the period into retained earnings: every income and expense account
    goes to zero as of ``period_end``, and the difference (profit or loss) lands in retained earnings."""
    if fiscal_year_of(fiscal_start, period_end)[1] != period_end:
        end = fiscal_year_of(fiscal_start, period_end)[0] - timedelta(days=1)
        raise LedgerError(f"a year is closed on the last day of a fiscal year (e.g. {end.isoformat()}); "
                          "the fiscal year start is set in the organization's settings")
    previous = last_year_end(db)
    if previous is not None and period_end <= previous:
        raise LedgerError(f"the year is already closed up to {previous.isoformat()}")
    rows = [r for r in trial_balance(db, as_of=period_end)
            if r.account.type in ("income", "expense") and r.balance_minor]
    if not rows:
        raise LedgerError("nothing to close: no income or expenses in the period")
    label = f"Year-end close {period_end.isoformat()}"
    specs = [LineSpec(r.account, credit_minor=r.balance_minor, label=label) if r.balance_minor > 0
             else LineSpec(r.account, debit_minor=-r.balance_minor, label=label) for r in rows]
    result = -sum(r.balance_minor for r in rows)  # credits exceed debits: a profit
    retained = system_account(db, "retained_earnings")
    specs.append(LineSpec(retained, credit_minor=result, label=label) if result > 0
                 else LineSpec(retained, debit_minor=-result, label=label))
    return post_entry(db, entry_date=period_end, currency=currency, lines=specs, memo=label,
                      source_type=YEAR_END, source_id=period_end.isoformat(), created_by=created_by)


@event.listens_for(Session, "before_flush")
def _ledger_is_append_only(session: Session, _ctx, _instances) -> None:
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, (JournalEntry, JournalLine)) and (obj in session.deleted or session.is_modified(obj)):
            raise LedgerImmutable("journal entries cannot be modified or deleted; post a reversal instead")
