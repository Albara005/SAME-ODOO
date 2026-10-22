"""CSV import and export. Imports are all-or-nothing and support a dry run."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.accounting.models import Account, JournalEntry, JournalLine, Tax
from novara.modules.audit import service as audit
from novara.modules.catalog.models import Product
from novara.modules.catalog.router import ProductIn
from novara.modules.dataio.csvio import CsvError, csv_response, read_csv
from novara.modules.invoicing.models import Invoice, InvoiceLine
from novara.modules.partners.models import Customer
from novara.modules.partners.router import CustomerIn
from novara.money import fmt_decimal, from_minor

router = APIRouter(tags=["import-export"])

CUSTOMER_COLUMNS = ["id", "name", "kind", "email", "phone", "tax_number", "commercial_registration",
                    "address_line", "city", "postal_code", "country", "notes"]
PRODUCT_COLUMNS = ["sku", "name", "kind", "unit", "sale_price", "tax_name", "description"]


class RowError(BaseModel):
    row: int
    errors: list[str]


class ImportResult(BaseModel):
    dry_run: bool
    created: int
    updated: int
    errors: list[RowError]


async def _body(request: Request) -> bytes:
    limit = request.app.state.settings.max_upload_bytes
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large")
    return bytes(data)


def _rows(raw: bytes, required: set[str], allowed: list[str]) -> list[dict[str, str]]:
    try:
        return read_csv(raw, required, set(allowed))
    except CsvError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


def _messages(e: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]


def _finish(db: Session, result: ImportResult, *, ctx: OrgContext, request: Request, action: str) -> ImportResult:
    if result.errors:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.model_dump())
    if result.dry_run:
        db.rollback()
        return result
    audit.record(db, actor_user_id=ctx.user.id, action=action, target_type="import", target_id="",
                 details={"created": result.created, "updated": result.updated}, ip_address=client_ip(request))
    db.commit()
    return result


# ---------------------------------------------------------------- customers


@router.get("/orgs/{org_id}/customers/export.csv")
def export_customers(_: OrgContext = Depends(require(P.CUSTOMERS_READ)), db: Session = Depends(get_db)):
    rows = db.scalars(select(Customer).where(Customer.active.is_(True)).order_by(Customer.name))
    return csv_response("customers.csv", CUSTOMER_COLUMNS, ([getattr(c, k) for k in CUSTOMER_COLUMNS] for c in rows))


@router.post("/orgs/{org_id}/customers/import", response_model=ImportResult)
async def import_customers(request: Request, dry_run: bool = Query(default=False),
                           ctx: OrgContext = Depends(require(P.CUSTOMERS_WRITE)), db: Session = Depends(get_db)):
    """CSV with a header row. Rows with an ``id`` update that customer; others create new ones."""
    rows = _rows(await _body(request), {"name"}, CUSTOMER_COLUMNS)
    result = ImportResult(dry_run=dry_run, created=0, updated=0, errors=[])
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        customer_id = row.pop("id", "")
        data = {k: v for k, v in row.items() if v != "" or k == "name"}
        try:
            fields = CustomerIn(**data).model_dump()
        except ValidationError as e:
            result.errors.append(RowError(row=n, errors=_messages(e)))
            continue
        fields["email"] = (fields["email"] or "").lower()
        if customer_id:
            customer = db.get(Customer, customer_id)
            if customer is None:
                result.errors.append(RowError(row=n, errors=[f"id: no customer {customer_id}"]))
                continue
            for k, v in fields.items():
                if k in data:  # only columns filled in the file change the customer
                    setattr(customer, k, v)
            result.updated += 1
        else:
            db.add(Customer(**fields, created_by=ctx.user.id))
            result.created += 1
    return _finish(db, result, ctx=ctx, request=request, action="customers.imported")


# ---------------------------------------------------------------- products


@router.get("/orgs/{org_id}/products/export.csv")
def export_products(_: OrgContext = Depends(require(P.PRODUCTS_READ)), db: Session = Depends(get_db)):
    taxes = {t.id: t.name for t in db.scalars(select(Tax))}
    rows = db.scalars(select(Product).where(Product.active.is_(True)).order_by(Product.name))
    return csv_response("products.csv", PRODUCT_COLUMNS, (
        [p.sku or "", p.name, p.kind, p.unit, fmt_decimal(p.sale_price), taxes.get(p.default_tax_id, ""),
         p.description] for p in rows))


@router.post("/orgs/{org_id}/products/import", response_model=ImportResult)
async def import_products(request: Request, dry_run: bool = Query(default=False),
                          ctx: OrgContext = Depends(require(P.PRODUCTS_WRITE)), db: Session = Depends(get_db)):
    """CSV with a header row. Rows whose ``sku`` exists update that product; others create new ones.
    ``tax_name`` must match exactly one active tax (leave empty for no default tax)."""
    rows = _rows(await _body(request), {"name", "sale_price"}, PRODUCT_COLUMNS)
    taxes_by_name: dict[str, list[Tax]] = defaultdict(list)
    for t in db.scalars(select(Tax).where(Tax.active.is_(True))):
        taxes_by_name[t.name].append(t)
    by_sku = {p.sku: p for p in db.scalars(select(Product).where(Product.sku.is_not(None)))}
    seen_skus: set[str] = set()
    result = ImportResult(dry_run=dry_run, created=0, updated=0, errors=[])
    for n, row in enumerate(rows, start=2):
        errors = []
        tax_name = row.pop("tax_name", "")
        tax_id = None
        if tax_name:
            matches = taxes_by_name.get(tax_name, [])
            if len(matches) != 1:
                errors.append(f"tax_name: '{tax_name}' matches {len(matches)} active taxes")
            else:
                tax_id = matches[0].id
        data = {k: v for k, v in row.items() if v != ""}
        try:
            fields = ProductIn(**data, default_tax_id=tax_id).model_dump()
        except ValidationError as e:
            errors.extend(_messages(e))
        sku = data.get("sku")
        if sku:
            if sku in seen_skus:
                errors.append(f"sku: '{sku}' appears more than once in the file")
            seen_skus.add(sku)
        if errors:
            result.errors.append(RowError(row=n, errors=errors))
            continue
        existing = by_sku.get(sku) if sku else None
        if existing is not None:
            for k, v in fields.items():
                if k in data or k == "default_tax_id":
                    setattr(existing, k, v)
            existing.active = True
            result.updated += 1
        else:
            db.add(Product(**fields))
            result.created += 1
    return _finish(db, result, ctx=ctx, request=request, action="products.imported")


# ---------------------------------------------------------------- accounting exports


@router.get("/orgs/{org_id}/invoices/export.csv")
def export_invoices(date_from: date | None = None, date_to: date | None = None,
                    _: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    stmt = (select(Invoice, Customer).join(Customer, Customer.id == Invoice.customer_id)
            .where(Invoice.status != "draft").order_by(Invoice.issue_date, Invoice.number))
    if date_from:
        stmt = stmt.where(Invoice.issue_date >= date_from)
    if date_to:
        stmt = stmt.where(Invoice.issue_date <= date_to)
    header = ["number", "status", "issue_date", "due_date", "customer", "customer_tax_number", "currency",
              "subtotal", "tax", "total", "amount_paid", "amount_due"]

    def rows():
        for inv, cust in db.execute(stmt):
            m = lambda v: from_minor(v, inv.currency)  # noqa: E731
            due = 0 if inv.status == "cancelled" else inv.total_minor - inv.amount_paid_minor
            yield [inv.number, inv.status, inv.issue_date, inv.due_date, cust.name, cust.tax_number, inv.currency,
                   m(inv.subtotal_minor), m(inv.tax_minor), m(inv.total_minor), m(inv.amount_paid_minor), m(due)]

    return csv_response("invoices.csv", header, list(rows()))


@router.get("/orgs/{org_id}/accounting/journal.csv")
def export_journal(date_from: date | None = None, date_to: date | None = None,
                   _: OrgContext = Depends(require(P.ACCOUNTING_READ)), db: Session = Depends(get_db)):
    """General journal, one row per line, for the accountant or an external system."""
    stmt = (select(JournalEntry, JournalLine, Account)
            .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
            .join(Account, Account.id == JournalLine.account_id)
            .order_by(JournalEntry.entry_date, JournalEntry.number, JournalLine.credit_minor))
    if date_from:
        stmt = stmt.where(JournalEntry.entry_date >= date_from)
    if date_to:
        stmt = stmt.where(JournalEntry.entry_date <= date_to)
    header = ["entry", "date", "account_code", "account_name", "debit", "credit", "currency", "label", "memo"]
    return csv_response("journal.csv", header, [
        [e.number, e.entry_date, a.code, a.name, from_minor(l.debit_minor, e.currency),
         from_minor(l.credit_minor, e.currency), e.currency, l.label, e.memo]
        for e, l, a in db.execute(stmt)
    ])


# ---------------------------------------------------------------- sales report


class SalesBucket(BaseModel):
    key: str
    label: str
    net: str
    invoices: int


class SalesReportOut(BaseModel):
    date_from: date | None
    date_to: date | None
    currency: str
    net_sales: str
    tax: str
    gross_sales: str
    invoices: int
    by_month: list[SalesBucket]
    top_customers: list[SalesBucket]
    top_products: list[SalesBucket]


@router.get("/orgs/{org_id}/reports/sales", response_model=SalesReportOut)
def sales_report(date_from: date | None = None, date_to: date | None = None,
                 top: int = Query(default=10, ge=1, le=100),
                 ctx: OrgContext = Depends(require(P.INVOICES_READ)), db: Session = Depends(get_db)):
    """Sales from posted invoices (cancelled invoices excluded), net of tax."""
    currency = ctx.organization.default_currency
    cond = [Invoice.status.in_(("posted", "partially_paid", "paid"))]
    if date_from:
        cond.append(Invoice.issue_date >= date_from)
    if date_to:
        cond.append(Invoice.issue_date <= date_to)
    invoices = db.execute(select(Invoice, Customer.name).join(Customer, Customer.id == Invoice.customer_id)
                          .where(*cond)).all()
    months: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    customers: dict[str, list] = {}
    for inv, name in invoices:
        bucket = months[inv.issue_date.strftime("%Y-%m")]
        bucket[0] += inv.subtotal_minor
        bucket[1] += 1
        c = customers.setdefault(inv.customer_id, [name, 0, 0])
        c[1] += inv.subtotal_minor
        c[2] += 1
    product_rows = db.execute(
        select(InvoiceLine.product_id, Product.name, func.sum(InvoiceLine.net_minor),
               func.count(func.distinct(InvoiceLine.invoice_id)))
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .join(Product, Product.id == InvoiceLine.product_id)
        .where(*cond).group_by(InvoiceLine.product_id, Product.name)
    ).all()
    m = lambda v: str(from_minor(int(v), currency))  # noqa: E731
    net = sum(inv.subtotal_minor for inv, _ in invoices)
    tax = sum(inv.tax_minor for inv, _ in invoices)
    return SalesReportOut(
        date_from=date_from, date_to=date_to, currency=currency, net_sales=m(net), tax=m(tax), gross_sales=m(net + tax),
        invoices=len(invoices),
        by_month=[SalesBucket(key=k, label=k, net=m(v[0]), invoices=v[1]) for k, v in sorted(months.items())],
        top_customers=[SalesBucket(key=cid, label=c[0], net=m(c[1]), invoices=c[2])
                       for cid, c in sorted(customers.items(), key=lambda kv: -kv[1][1])[:top]],
        top_products=[SalesBucket(key=pid, label=name, net=m(total), invoices=count)
                      for pid, name, total, count in sorted(product_rows, key=lambda r: -int(r[2]))[:top]],
    )
