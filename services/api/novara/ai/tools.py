"""Tools the assistant can use, each tied to a permission.

Safety model (enforced here, not by the prompt):
* The model is only offered tools the *current user* holds the permission for.
* Every call is checked again on execution; a tool the user can't use is refused.
* Read tools run with the user's own organization context, so tenant isolation and
  permissions apply exactly as in the API.
* Write tools never run on the model's request. They become a pending action that the
  user approves or rejects in the app; the approval re-checks the permission.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from novara.deps import OrgContext
from novara.modules.access import permissions as P

# read: runs at once. edit: runs at once on a draft that has its own review step (e.g. an app's
# working copy). write: changes business data, so it waits for the user's approval.
Kind = Literal["read", "edit", "write"]


@dataclass(frozen=True)
class ToolContext:
    db: Session
    org: OrgContext
    ip: str = ""
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    permission: str
    kind: Kind
    args: type[BaseModel]
    run: Callable[[ToolContext, BaseModel], object]
    summary: Callable[[BaseModel], str] = lambda a: ""

    def definition(self) -> dict:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": schema}


class ToolRefused(Exception):
    """The tool can't run for this user or with these arguments; the message goes back to the model."""


def _dump(value) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value


# ---------------------------------------------------------------- read tools


class SearchArgs(BaseModel):
    query: str = Field(default="", max_length=100, description="Text to search for; empty lists everything")
    limit: int = Field(default=10, ge=1, le=50)


def _search_customers(t: ToolContext, a: SearchArgs):
    from novara.modules.partners.router import list_customers

    rows = list_customers(q=a.query or None, include_archived=False, limit=a.limit, offset=0, _=t.org, db=t.db)
    return [{"id": c.id, "name": c.name, "email": c.email, "phone": c.phone, "tax_number": c.tax_number,
             "city": c.city, "notes": c.notes} for c in rows]


def _search_products(t: ToolContext, a: SearchArgs):
    from novara.modules.catalog.router import list_products

    return _dump(list_products(q=a.query or None, include_archived=False, limit=a.limit, offset=0, _=t.org, db=t.db))


class InvoiceQuery(BaseModel):
    status: Literal["draft", "posted", "partially_paid", "paid", "cancelled"] | None = None
    customer_id: str | None = None
    overdue_only: bool = Field(default=False, description="Only posted invoices past their due date with money owed")
    limit: int = Field(default=20, ge=1, le=100)


def _list_invoices(t: ToolContext, a: InvoiceQuery):
    from novara.modules.invoicing.router import list_invoices

    today = date.today()
    rows = list_invoices(status_filter=a.status, customer_id=a.customer_id, limit=500 if a.overdue_only else a.limit,
                         offset=0, _=t.org, db=t.db)
    out = []
    for inv in rows:
        overdue_days = (today - inv.due_date).days
        if a.overdue_only and not (Decimal(inv.amount_due) > 0 and overdue_days > 0):
            continue
        out.append(inv.model_dump(mode="json") | {"days_overdue": max(overdue_days, 0)})
    return out[: a.limit]


class InvoiceRef(BaseModel):
    invoice_id: str


def _get_invoice(t: ToolContext, a: InvoiceRef):
    from novara.modules.invoicing.router import read_invoice

    return _dump(read_invoice(a.invoice_id, _=t.org, db=t.db))


class AsOf(BaseModel):
    as_of: date | None = Field(default=None, description="Report date (YYYY-MM-DD); default today")


def _receivables(t: ToolContext, a: AsOf):
    from novara.modules.invoicing.router import receivables_aging

    return _dump(receivables_aging(as_of=a.as_of, ctx=t.org, db=t.db))


def _trial_balance(t: ToolContext, a: AsOf):
    from novara.modules.accounting.router import get_trial_balance

    return _dump(get_trial_balance(as_of=a.as_of, ctx=t.org, db=t.db))


class Period(BaseModel):
    date_from: date | None = None
    date_to: date | None = None


def _sales_summary(t: ToolContext, a: Period):
    from novara.modules.dataio.router import sales_report

    return _dump(sales_report(date_from=a.date_from, date_to=a.date_to, top=10, ctx=t.org, db=t.db))


class StockQuery(BaseModel):
    product_id: str | None = None


def _stock(t: ToolContext, a: StockQuery):
    from novara.modules.inventory.router import stock_levels

    return _dump(stock_levels(product_id=a.product_id, warehouse_id=None, ctx=t.org, db=t.db))


class OrderQuery(BaseModel):
    status: Literal["quotation", "confirmed", "cancelled"] | None = None
    customer_id: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


def _orders(t: ToolContext, a: OrderQuery):
    from novara.modules.sales.router import list_orders

    return _dump(list_orders(status_filter=a.status, customer_id=a.customer_id, limit=a.limit, offset=0,
                             _=t.org, db=t.db))


# ---------------------------------------------------------------- write tools (approval required)


class NewCustomer(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["company", "individual"] = "company"
    email: str | None = None
    phone: str = ""
    tax_number: str = ""
    city: str = ""
    country: str = Field(default="", description="ISO 3166-1 alpha-2, e.g. SA")
    notes: str = ""


def _create_customer(t: ToolContext, a: NewCustomer):
    from novara.modules.partners.router import CustomerIn, add_customer

    c = add_customer(t.db, t.org, CustomerIn(**a.model_dump()), ip=t.ip, via="assistant")
    return {"customer_id": c.id, "name": c.name}


class LineArgs(BaseModel):
    product_id: str | None = None
    description: str | None = None
    quantity: str = Field(description="Decimal as a string, e.g. '2' or '1.5'")
    unit_price: str | None = Field(default=None, description="Omit to use the product price")


class NewInvoice(BaseModel):
    customer_id: str
    issue_date: date
    due_date: date | None = None
    lines: list[LineArgs] = Field(min_length=1, max_length=100)


def _line_inputs(lines: list[LineArgs]) -> list[dict]:
    return [{k: v for k, v in l.model_dump().items() if v is not None} for l in lines]


def _create_invoice(t: ToolContext, a: NewInvoice):
    from novara.modules.invoicing.router import InvoiceIn, add_draft_invoice

    body = InvoiceIn(customer_id=a.customer_id, issue_date=a.issue_date, due_date=a.due_date,
                     lines=_line_inputs(a.lines))
    inv = add_draft_invoice(t.db, t.org, body, ip=t.ip, via="assistant")
    return {"invoice_id": inv.id, "status": inv.status, "total_minor": inv.total_minor, "currency": inv.currency}


class NewQuotation(BaseModel):
    customer_id: str
    order_date: date
    lines: list[LineArgs] = Field(min_length=1, max_length=100)


def _create_quotation(t: ToolContext, a: NewQuotation):
    from novara.modules.sales.router import OrderIn, add_quotation

    order = add_quotation(t.db, t.org, OrderIn(customer_id=a.customer_id, order_date=a.order_date,
                                               lines=_line_inputs(a.lines)), ip=t.ip, via="assistant")
    return {"sales_order_id": order.id, "number": order.number, "status": order.status}


class NewPayment(BaseModel):
    invoice_id: str
    amount: str = Field(description="Decimal as a string, e.g. '1150.00'")
    payment_date: date
    method: Literal["bank", "cash"]
    reference: str = ""


def _register_payment(t: ToolContext, a: NewPayment):
    from novara.modules.invoicing.router import PaymentIn, add_payment_record

    body = PaymentIn(amount=a.amount, payment_date=a.payment_date, method=a.method, reference=a.reference)
    payment, _, inv = add_payment_record(t.db, t.org, a.invoice_id, body, ip=t.ip, via="assistant")
    return {"payment_id": payment.id, "invoice_status": inv.status}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("search_customers", "Find customers by name, email, phone or tax number.", P.CUSTOMERS_READ, "read",
         SearchArgs, _search_customers),
    Tool("search_products", "Find products and services by name or SKU, with prices.", P.PRODUCTS_READ, "read",
         SearchArgs, _search_products),
    Tool("list_invoices", "List invoices, optionally only overdue ones, with totals and amounts due.",
         P.INVOICES_READ, "read", InvoiceQuery, _list_invoices),
    Tool("get_invoice", "Full details of one invoice: lines, taxes, payments status.", P.INVOICES_READ, "read",
         InvoiceRef, _get_invoice),
    Tool("receivables_aging", "Money customers owe, grouped by days overdue.", P.INVOICES_READ, "read",
         AsOf, _receivables),
    Tool("sales_summary", "Net sales by month, top customers and top products for a period.", P.INVOICES_READ,
         "read", Period, _sales_summary),
    Tool("trial_balance", "Balance of every ledger account.", P.ACCOUNTING_READ, "read", AsOf, _trial_balance),
    Tool("stock_levels", "Quantity, value and average cost of stock per product and warehouse.", P.INVENTORY_READ,
         "read", StockQuery, _stock),
    Tool("list_sales_orders", "Quotations and sales orders.", P.SALES_READ, "read", OrderQuery, _orders),
    Tool("create_customer", "Create a customer. Requires the user's approval in the app before it happens.",
         P.CUSTOMERS_WRITE, "write", NewCustomer, _create_customer, lambda a: f"Create customer '{a.name}'"),
    Tool("create_draft_invoice", "Create a DRAFT invoice (not posted). Requires the user's approval in the app.",
         P.INVOICES_WRITE, "write", NewInvoice, _create_invoice,
         lambda a: f"Create a draft invoice with {len(a.lines)} line(s) dated {a.issue_date}"),
    Tool("create_quotation", "Create a quotation. Requires the user's approval in the app.", P.SALES_WRITE, "write",
         NewQuotation, _create_quotation, lambda a: f"Create a quotation with {len(a.lines)} line(s)"),
    Tool("register_payment", "Record a customer payment against a posted invoice. Requires the user's approval.",
         P.PAYMENTS_WRITE, "write", NewPayment, _register_payment,
         lambda a: f"Record a {a.method} payment of {a.amount} on {a.payment_date}"),
]}


def tools_for(permissions: set[str], registry: dict[str, Tool] | None = None) -> list[Tool]:
    """Tools the user may use, in a fixed order so the prompt-cache prefix stays stable."""
    registry = TOOLS if registry is None else registry
    return [registry[name] for name in sorted(registry) if registry[name].permission in permissions]


def parse_args(tool: Tool, arguments: dict) -> BaseModel:
    try:
        return tool.args(**(arguments or {}))
    except ValidationError as e:
        raise ToolRefused("invalid arguments: " + "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()))


def execute(tool: Tool, ctx: ToolContext, args: BaseModel) -> object:
    """Run a tool with the user's own context. HTTP-style errors from the business layer
    become a refusal the model can read and react to."""
    if tool.permission not in ctx.org.permissions:
        raise ToolRefused(f"the user does not have permission '{tool.permission}'")
    try:
        return tool.run(ctx, args)
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, str) else json.dumps(e.detail, default=str)
        raise ToolRefused(detail)
