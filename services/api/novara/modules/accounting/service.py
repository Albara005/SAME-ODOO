"""Ledger operations. All journal entries are created through ``post_entry``.

Rules enforced here (and again by the database on PostgreSQL):
* every entry balances: total debit == total credit, at least two lines;
* each line is either a debit or a credit, never both, never negative;
* entries and lines are never updated or deleted; mistakes are fixed with
  ``reverse_entry``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from novara.modules.accounting.models import Account, JournalEntry, JournalLine, NumberSequence

# A small, country-neutral starting chart. Organizations can add accounts later;
# system_key marks the ones the platform posts to automatically.
DEFAULT_CHART: list[tuple[str, str, str, str | None]] = [
    ("1100", "Cash", "asset", "cash"),
    ("1200", "Bank", "asset", "bank"),
    ("1300", "Accounts Receivable", "asset", "receivable"),
    ("1400", "Inventory", "asset", "inventory"),
    ("2100", "Accounts Payable", "liability", "payable"),
    ("2200", "Output VAT Payable", "liability", "vat_output"),
    ("3000", "Owner's Equity", "equity", None),
    ("4000", "Sales Revenue", "income", "sales"),
    ("5000", "Cost of Goods Sold", "expense", "cogs"),
    ("6000", "General Expenses", "expense", None),
]


class LedgerError(ValueError):
    pass


class LedgerImmutable(RuntimeError):
    pass


@dataclass(frozen=True)
class LineSpec:
    account: Account
    debit_minor: int = 0
    credit_minor: int = 0
    customer_id: str | None = None
    label: str = ""


def ensure_chart(db: Session) -> dict[str, Account]:
    """Create the default chart of accounts for the bound organization if it has none. Idempotent."""
    accounts = list(db.scalars(select(Account)))
    codes = {a.code for a in accounts}
    keys = {a.system_key for a in accounts if a.system_key}
    for code, name, type_, key in DEFAULT_CHART:
        if code in codes or (key is not None and key in keys):
            continue
        account = Account(code=code, name=name, type=type_, system_key=key)
        db.add(account)
        accounts.append(account)
    db.flush()
    return {a.system_key: a for a in accounts if a.system_key}


def system_account(db: Session, key: str) -> Account:
    account = db.scalar(select(Account).where(Account.system_key == key))
    if account is None:
        account = ensure_chart(db)[key]
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
                     customer_id=l.customer_id, label=f"Reversal: {l.label}"[:300])
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


def trial_balance(db: Session, *, as_of: date | None = None) -> list[TrialBalanceRow]:
    stmt = (
        select(JournalLine.account_id, func.sum(JournalLine.debit_minor), func.sum(JournalLine.credit_minor))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .group_by(JournalLine.account_id)
    )
    if as_of is not None:
        stmt = stmt.where(JournalEntry.entry_date <= as_of)
    sums = {acc: (int(d or 0), int(c or 0)) for acc, d, c in db.execute(stmt)}
    accounts = db.scalars(select(Account).order_by(Account.code)).all()
    return [TrialBalanceRow(a, *sums[a.id]) for a in accounts if a.id in sums]


@event.listens_for(Session, "before_flush")
def _ledger_is_append_only(session: Session, _ctx, _instances) -> None:
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, (JournalEntry, JournalLine)) and (obj in session.deleted or session.is_modified(obj)):
            raise LedgerImmutable("journal entries cannot be modified or deleted; post a reversal instead")
