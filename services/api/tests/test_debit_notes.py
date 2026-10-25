"""Debit notes: charging more on a posted invoice. Each is an invoice of its own (numbered DN-, paid and aged like
one) that refers to the invoice it adjusts, and is e-invoiced as ZATCA type 383 with that reference and its reason.
Posted invoices and vendor bills can't return to draft, in the database too."""

from __future__ import annotations

from datetime import date

import pytest
from lxml import etree
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from novara.einvoice import signing
from novara.tenancy import bind_tenant
from tests.conftest import requires_pg
from tests.test_accounting_extended import ACCT, OWNER, SALES, balances, o, posted_invoice, shop  # noqa: F401 (shop is a fixture)
from tests.test_einvoicing import ACCT as EACCT, CAC, CBC, SALES as ESALES, ei, onboard, post_invoice, stored, u, zatca  # noqa: F401

LINE = {"description": "Consulting", "quantity": "10", "unit_price": "100"}


def debit(api, s, inv, user=SALES, **body):
    payload = {"issue_date": "2026-04-10", "reason": "Two extra hours not on the invoice",
               "lines": [{"description": "Extra hours", "quantity": "2", "unit_price": "100", "tax_id": s["vat"]}]} | body
    return api.req("POST", user, o(s, f"/invoices/{inv['id']}/debit-notes"), json=payload)


def test_a_debit_note_charges_more_on_a_posted_invoice(api, shop):
    inv = posted_invoice(api, shop, [LINE | {"tax_id": shop["vat"]}])
    draft = api.req("POST", SALES, o(shop, "/invoices"), json={"customer_id": shop["customer"], "issue_date": "2026-04-01",
                                                               "lines": [LINE]}).json()
    assert debit(api, shop, draft).status_code == 409                                   # only posted invoices
    assert debit(api, shop, inv, issue_date="2026-03-01").status_code == 422            # not before the invoice
    assert debit(api, shop, inv, reason="  ").status_code == 422                        # a reason is required

    made = debit(api, shop, inv)
    assert made.status_code == 201, made.text
    note = made.json()
    assert (note["kind"], note["status"], note["number"], note["debit_note_of_number"], note["customer_id"]) == (
        "debit_note", "draft", None, inv["number"], inv["customer_id"])
    assert (note["total"], note["reason"]) == ("230.00", "Two extra hours not on the invoice")

    # A draft debit note keeps the invoice's customer and currency; its lines and reason can change.
    other = api.req("POST", SALES, o(shop, "/customers"), json={"name": "Someone else"}).json()["id"]
    put = lambda body: api.req("PUT", SALES, o(shop, f"/invoices/{note['id']}"), json=body)  # noqa: E731
    base = {"customer_id": inv["customer_id"], "issue_date": "2026-04-10",
            "lines": [{"description": "Extra hours", "quantity": "3", "unit_price": "100", "tax_id": shop["vat"]}]}
    assert put(base | {"customer_id": other}).status_code == 422
    assert put(base | {"currency": "USD", "exchange_rate": "3.75"}).status_code == 422
    changed = put(base | {"reason": "Three extra hours"})
    assert changed.status_code == 200 and (changed.json()["total"], changed.json()["reason"]) == ("345.00", "Three extra hours")

    posted = api.req("POST", ACCT, o(shop, f"/invoices/{note['id']}/post"))
    assert posted.status_code == 200, posted.text
    assert posted.json()["number"] == "DN-2026-00001"                                   # its own numbering
    assert balances(api, shop)["1300"] == "1495.00"                                      # 1,150 + 345 owed
    assert debit(api, shop, posted.json()).status_code == 422                           # not on a debit note
    original = api.req("GET", ACCT, o(shop, f"/invoices/{inv['id']}")).json()
    assert [(d["number"], d["total"]) for d in original["debit_notes"]] == [("DN-2026-00001", "345.00")]
    listed = {i["number"]: i["kind"] for i in api.req("GET", ACCT, o(shop, "/invoices")).json()}
    assert listed == {inv["number"]: "invoice", "DN-2026-00001": "debit_note", None: "invoice"}

    pay = api.req("POST", ACCT, o(shop, f"/invoices/{note['id']}/payments"),
                  json={"amount": "345", "payment_date": "2026-04-12", "method": "bank"})
    assert pay.status_code == 201
    aging = api.req("GET", ACCT, o(shop, "/reports/receivables"), params={"as_of": "2026-05-01"}).json()
    assert aging["total_due"] == "1150.00"                                                # only the invoice is still owed
    assert debit(api, shop, inv, user=ACCT).status_code == 403                            # a draft needs invoices.write


def test_a_debit_note_on_a_foreign_invoice_uses_its_rate(api, shop):
    usd = api.req("POST", SALES, o(shop, "/invoices"), json={"customer_id": shop["customer"], "issue_date": "2026-04-01",
                                                               "currency": "USD", "exchange_rate": "3.75", "lines": [LINE]}).json()
    api.req("POST", ACCT, o(shop, f"/invoices/{usd['id']}/post"))
    note = debit(api, shop, usd, lines=[{"description": "Late fee", "quantity": "1", "unit_price": "20"}]).json()
    assert (note["currency"], note["exchange_rate"]) == ("USD", "3.75")
    posted = api.req("POST", ACCT, o(shop, f"/invoices/{note['id']}/post")).json()
    assert posted["base_total"] == "75.00"
    other = debit(api, shop, usd, exchange_rate="3.8", lines=[{"description": "Fee", "quantity": "1", "unit_price": "10"}])
    assert other.json()["exchange_rate"] == "3.8"


def test_a_debit_note_is_e_invoiced_as_type_383(api, ei, zatca, app):
    onboard(api, ei)
    inv = post_invoice(api, ei, ei["cash"]).json()
    note = api.req("POST", ESALES, u(ei, f"/invoices/{inv['id']}/debit-notes"), json={
        "issue_date": date.today().isoformat(), "reason": "Price corrected",
        "lines": [{"description": "Price difference", "quantity": "1", "unit_price": "10", "tax_id": ei["vat"]}]}).json()
    posted = api.req("POST", EACCT, u(ei, f"/invoices/{note['id']}/post")).json()
    assert (posted["einvoice"]["type_code"], posted["einvoice"]["status"], posted["einvoice"]["icv"]) == ("383", "reported", 2)
    doc = stored(app, ei, posted["number"])
    assert signing.verify(doc.xml.encode())
    root = etree.fromstring(doc.xml.encode())
    assert root.findtext(f"{{{CBC}}}InvoiceTypeCode") == "383"
    assert root.findtext(f".//{{{CAC}}}BillingReference/{{{CAC}}}InvoiceDocumentReference/{{{CBC}}}ID") == inv["number"]
    assert root.findtext(f".//{{{CAC}}}PaymentMeans/{{{CBC}}}InstructionNote") == "Price corrected"
    assert root.findtext(f".//{{{CAC}}}LegalMonetaryTotal/{{{CBC}}}PayableAmount") == "11.50"


@requires_pg
def test_posted_documents_never_return_to_draft(api, shop, app):
    inv = posted_invoice(api, shop, [LINE])
    note = debit(api, shop, inv).json()
    api.req("POST", ACCT, o(shop, f"/invoices/{note['id']}/post"))

    def raw(sql, **params):
        with app.state.sessionmaker() as db:
            bind_tenant(db, shop["org"])
            db.execute(text(sql), params)
            db.commit()

    with pytest.raises(DBAPIError, match="cannot go back to draft"):
        raw("UPDATE invoices SET status = 'draft' WHERE id = :id", id=inv["id"])
    with pytest.raises(DBAPIError, match="cannot be edited"):
        raw("UPDATE invoices SET reason = 'changed' WHERE id = :id", id=note["id"])
    vendor = api.req("POST", OWNER, o(shop, "/vendors"), json={"name": "Supplier"}).json()["id"]
    bill = api.req("POST", OWNER, o(shop, "/bills"), json={"vendor_id": vendor, "bill_date": "2026-04-01", "lines": [
        {"description": "Paper", "quantity": "1", "unit_cost": "100", "tax_id": None}]}).json()
    assert api.req("POST", OWNER, o(shop, f"/bills/{bill['id']}/post")).status_code == 200
    with pytest.raises(DBAPIError, match="cannot go back to draft"):
        raw("UPDATE vendor_bills SET status = 'draft' WHERE id = :id", id=bill["id"])
