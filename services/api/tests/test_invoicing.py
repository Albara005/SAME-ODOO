"""Invoice lifecycle and its effect on the ledger."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from novara.modules.accounting.models import JournalEntry, JournalLine
from novara.modules.accounting.service import LedgerError, LedgerImmutable, LineSpec, post_entry, system_account
from novara.modules.invoicing.models import InvoiceLine
from novara.modules.invoicing.service import InvoiceLocked
from novara.tenancy import bind_tenant

OWNER, SALES, ACCT = "owner@acme.example.com", "sales@acme.example.com", "acct@acme.example.com"


@pytest.fixture
def books(api):
    for e in (OWNER, SALES, ACCT):
        api.signup(e)
    org = api.create_org(OWNER, "acme")
    api.add_member(OWNER, org, SALES, "sales")
    api.add_member(OWNER, org, ACCT, "accountant")
    vat = api.req("POST", ACCT, f"/orgs/{org}/taxes", json={"name": "VAT 15%", "rate": "15"}).json()["id"]
    customer = api.req("POST", SALES, f"/orgs/{org}/customers", json={"name": "Nokhba Co"}).json()["id"]
    product = api.req("POST", OWNER, f"/orgs/{org}/products",
                      json={"name": "Consulting hour", "kind": "service", "sale_price": "250", "default_tax_id": vat}).json()["id"]
    return {"org": org, "vat": vat, "customer": customer, "product": product}


def draft(api, b, lines=None, issue="2026-03-01", due="2026-03-31", as_user=SALES):
    body = {"customer_id": b["customer"], "issue_date": issue, "due_date": due,
            "lines": lines or [{"product_id": b["product"], "quantity": "4"}]}
    r = api.req("POST", as_user, f"/orgs/{b['org']}/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def post(api, b, inv_id, as_user=ACCT):
    return api.req("POST", as_user, f"/orgs/{b['org']}/invoices/{inv_id}/post")


def trial(api, b, **params):
    tb = api.req("GET", ACCT, f"/orgs/{b['org']}/accounting/trial-balance", params=params).json()
    return tb, {l["code"]: l["balance"] for l in tb["lines"]}


def test_draft_uses_product_defaults_and_computes_totals(api, books):
    inv = draft(api, books)
    assert inv["status"] == "draft"
    assert inv["number"] is None
    line = inv["lines"][0]
    assert (line["description"], line["unit_price"], line["tax_rate"]) == ("Consulting hour", "250", "15")
    assert (inv["subtotal"], inv["tax"], inv["total"]) == ("1000.00", "150.00", "1150.00")
    assert inv["amount_due"] == "0.00"  # nothing is owed until posted


def test_tax_is_rounded_once_per_rate_not_per_line(api, books):
    # Three lines of 0.10 at 15%: per-line rounding would give 0.06, the correct total is 0.05.
    lines = [{"description": f"item {i}", "quantity": "1", "unit_price": "0.10", "tax_id": books["vat"]} for i in range(3)]
    inv = draft(api, books, lines)
    assert (inv["subtotal"], inv["tax"], inv["total"]) == ("0.30", "0.05", "0.35")


def test_discount_and_explicit_no_tax(api, books):
    lines = [{"product_id": books["product"], "quantity": "2", "discount_percent": "10", "tax_id": None}]
    inv = draft(api, books, lines)
    assert (inv["subtotal"], inv["tax"], inv["total"]) == ("450.00", "0.00", "450.00")


def test_invalid_lines_rejected(api, books):
    org = books["org"]
    base = {"customer_id": books["customer"], "issue_date": "2026-03-01"}
    bad = [
        [{"description": "no price", "quantity": "1"}],
        [{"description": "x", "quantity": "0", "unit_price": "1"}],
        [{"description": "x", "quantity": "1", "unit_price": "1", "discount_percent": "120"}],
        [{"description": "x", "quantity": "1", "unit_price": "1", "tax_id": "nope"}],
        [],
    ]
    for lines in bad:
        assert api.req("POST", SALES, f"/orgs/{org}/invoices", json={**base, "lines": lines}).status_code == 422, lines
    r = api.req("POST", SALES, f"/orgs/{org}/invoices",
                json={**base, "due_date": "2026-02-01", "lines": [{"product_id": books["product"], "quantity": 1}]})
    assert r.status_code == 422


def test_posting_creates_balanced_journal_entry(api, books):
    inv = draft(api, books)
    r = post(api, books, inv["id"])
    assert r.status_code == 200, r.text
    posted = r.json()
    assert posted["status"] == "posted"
    assert posted["number"] == "INV-2026-00001"
    assert posted["amount_due"] == "1150.00"

    entries = api.req("GET", ACCT, f"/orgs/{books['org']}/accounting/journal-entries",
                      params={"source_type": "invoice", "source_id": inv["id"]}).json()
    assert len(entries) == 1
    lines = {(l["account_code"], l["debit"], l["credit"]) for l in entries[0]["lines"]}
    assert lines == {("1300", "1150.00", "0.00"), ("4000", "0.00", "1000.00"), ("2200", "0.00", "150.00")}

    tb, bal = trial(api, books)
    assert tb["balanced"] is True
    assert bal == {"1300": "1150.00", "2200": "-150.00", "4000": "-1000.00"}


def test_posting_requires_permission_and_happens_once(api, books):
    inv = draft(api, books)
    assert post(api, books, inv["id"], as_user=SALES).status_code == 403
    assert post(api, books, inv["id"]).status_code == 200
    assert post(api, books, inv["id"]).status_code == 409
    entries = api.req("GET", ACCT, f"/orgs/{books['org']}/accounting/journal-entries").json()
    assert len(entries) == 1


def test_posted_invoice_is_final(api, books):
    inv = draft(api, books)
    post(api, books, inv["id"])
    org = books["org"]
    body = {"customer_id": books["customer"], "issue_date": "2026-03-01",
            "lines": [{"product_id": books["product"], "quantity": "1"}]}
    assert api.req("PUT", SALES, f"/orgs/{org}/invoices/{inv['id']}", json=body).status_code == 409
    assert api.req("DELETE", SALES, f"/orgs/{org}/invoices/{inv['id']}").status_code == 409


def test_posted_invoice_is_final_even_below_the_api(api, app, books):
    inv = draft(api, books)
    post(api, books, inv["id"])
    with app.state.sessionmaker() as db:
        bind_tenant(db, books["org"])
        line = db.scalars(select(InvoiceLine)).first()
        line.quantity = 1000
        with pytest.raises(InvoiceLocked):
            db.flush()


def test_drafts_can_be_edited_and_deleted_without_consuming_numbers(api, books):
    org = books["org"]
    first = draft(api, books)
    body = {"customer_id": books["customer"], "issue_date": "2026-03-02",
            "lines": [{"product_id": books["product"], "quantity": "1"}, {"description": "Travel", "quantity": "1", "unit_price": "99.5"}]}
    edited = api.req("PUT", SALES, f"/orgs/{org}/invoices/{first['id']}", json=body).json()
    assert len(edited["lines"]) == 2
    # 250 (+15% VAT = 37.50) + 99.50 travel without tax
    assert (edited["subtotal"], edited["tax"], edited["total"]) == ("349.50", "37.50", "387.00")
    assert api.req("DELETE", SALES, f"/orgs/{org}/invoices/{first['id']}").status_code == 204

    second = draft(api, books)
    third = draft(api, books)
    assert post(api, books, third["id"]).json()["number"] == "INV-2026-00001"
    assert post(api, books, second["id"]).json()["number"] == "INV-2026-00002"


def test_numbering_restarts_each_year(api, books):
    a = draft(api, books, issue="2026-12-31", due="2026-12-31")
    b = draft(api, books, issue="2027-01-01", due="2027-01-01")
    assert post(api, books, a["id"]).json()["number"] == "INV-2026-00001"
    assert post(api, books, b["id"]).json()["number"] == "INV-2027-00001"


def test_partial_and_full_payment(api, books):
    org = books["org"]
    inv = draft(api, books)
    post(api, books, inv["id"])
    pay = lambda amount, key=None: api.req(  # noqa: E731
        "POST", ACCT, f"/orgs/{org}/invoices/{inv['id']}/payments",
        json={"amount": amount, "payment_date": "2026-03-10", "method": "bank"},
        headers={"Idempotency-Key": key} if key else {},
    )
    assert pay("2000").status_code == 422          # more than owed
    assert pay("0.001").status_code == 422         # more decimals than SAR has
    assert pay("150", key="p-1").status_code == 201
    state = api.req("GET", ACCT, f"/orgs/{org}/invoices/{inv['id']}").json()
    assert (state["status"], state["amount_paid"], state["amount_due"]) == ("partially_paid", "150.00", "1000.00")

    assert pay("1000", key="p-2").status_code == 201
    state = api.req("GET", ACCT, f"/orgs/{org}/invoices/{inv['id']}").json()
    assert (state["status"], state["amount_due"]) == ("paid", "0.00")
    assert pay("1").status_code == 409             # paid invoices take no more payments

    _, bal = trial(api, books)
    assert bal["1200"] == "1150.00"                # bank
    assert bal["1300"] == "0.00"                   # receivable cleared


def test_retried_payment_with_same_idempotency_key_is_not_charged_twice(api, books):
    org = books["org"]
    inv = draft(api, books)
    post(api, books, inv["id"])
    body = {"amount": "500", "payment_date": "2026-03-10", "method": "cash"}
    url = f"/orgs/{org}/invoices/{inv['id']}/payments"
    first = api.req("POST", ACCT, url, json=body, headers={"Idempotency-Key": "abc"})
    retry = api.req("POST", ACCT, url, json=body, headers={"Idempotency-Key": "abc"})
    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]
    assert len(api.req("GET", ACCT, url).json()) == 1
    assert api.req("GET", ACCT, f"/orgs/{org}/invoices/{inv['id']}").json()["amount_paid"] == "500.00"
    reused = api.req("POST", ACCT, url, json={**body, "amount": "10"}, headers={"Idempotency-Key": "abc"})
    assert reused.status_code == 409


def test_cancel_posts_reversal_and_clears_balances(api, books):
    org = books["org"]
    inv = draft(api, books)
    post(api, books, inv["id"])
    url = f"/orgs/{org}/invoices/{inv['id']}/cancel"
    assert api.req("POST", SALES, url, json={"cancel_date": "2026-03-05"}).status_code == 403
    r = api.req("POST", ACCT, url, json={"cancel_date": "2026-03-05"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    assert r.json()["number"] == "INV-2026-00001"  # the number is never reused
    tb, bal = trial(api, books)
    assert tb["balanced"] and set(bal.values()) == {"0.00"}
    # The original entry still exists: history is reversed, never erased.
    assert len(api.req("GET", ACCT, f"/orgs/{org}/accounting/journal-entries").json()) == 2
    # Trial balance as of before the cancellation still shows the invoice.
    _, before = trial(api, books, as_of="2026-03-04")
    assert before["1300"] == "1150.00"


def test_paid_invoice_cannot_be_cancelled(api, books):
    org = books["org"]
    inv = draft(api, books)
    post(api, books, inv["id"])
    api.req("POST", ACCT, f"/orgs/{org}/invoices/{inv['id']}/payments",
            json={"amount": "100", "payment_date": "2026-03-10", "method": "bank"})
    r = api.req("POST", ACCT, f"/orgs/{org}/invoices/{inv['id']}/cancel", json={"cancel_date": "2026-03-11"})
    assert r.status_code == 409


def test_receivables_aging(api, books):
    org = books["org"]
    a = draft(api, books, issue="2026-01-01", due="2026-01-31")   # 1150, 60 days overdue on 2026-04-01
    b = draft(api, books, issue="2026-03-25", due="2026-04-24")   # 1150, not yet due
    for inv in (a, b):
        post(api, books, inv["id"])
    api.req("POST", ACCT, f"/orgs/{org}/invoices/{a['id']}/payments",
            json={"amount": "150", "payment_date": "2026-02-01", "method": "bank"})
    rep = api.req("GET", SALES, f"/orgs/{org}/reports/receivables", params={"as_of": "2026-04-01"}).json()
    assert rep["total_due"] == "2150.00"
    assert rep["totals"]["31_60"] == "1000.00"
    assert rep["totals"]["current"] == "1150.00"
    assert rep["customers"][0]["invoices"] == 2


def test_cross_organization_references_are_refused(api, books):
    api.signup("other@globex.example.com")
    other = api.create_org("other@globex.example.com", "globex")
    body = {"customer_id": books["customer"], "issue_date": "2026-03-01",
            "lines": [{"description": "x", "quantity": "1", "unit_price": "1"}]}
    assert api.req("POST", "other@globex.example.com", f"/orgs/{other}/invoices", json=body).status_code == 422
    inv = draft(api, books)
    assert api.req("GET", "other@globex.example.com", f"/orgs/{other}/invoices/{inv['id']}").status_code == 404
    assert api.req("POST", "other@globex.example.com", f"/orgs/{other}/invoices/{inv['id']}/post").status_code == 404


def test_three_decimal_currency(api):
    api.signup("kw@example.com")
    org = api.req("POST", "kw@example.com", "/orgs",
                  json={"name": "Kuwait Co", "slug": "kw-co", "default_currency": "KWD"}).json()["id"]
    cust = api.req("POST", "kw@example.com", f"/orgs/{org}/customers", json={"name": "C"}).json()["id"]
    tax = api.req("POST", "kw@example.com", f"/orgs/{org}/taxes", json={"name": "VAT 5%", "rate": "5"}).json()["id"]
    inv = api.req("POST", "kw@example.com", f"/orgs/{org}/invoices", json={
        "customer_id": cust, "issue_date": "2026-03-01",
        "lines": [{"description": "Item", "quantity": "3", "unit_price": "1.333", "tax_id": tax}],
    }).json()
    assert (inv["currency"], inv["subtotal"], inv["tax"], inv["total"]) == ("KWD", "3.999", "0.200", "4.199")


def test_currency_is_locked_once_ledger_has_entries(api, books):
    org = books["org"]
    assert api.req("PATCH", OWNER, f"/orgs/{org}", json={"default_currency": "USD"}).status_code == 200
    assert api.req("PATCH", OWNER, f"/orgs/{org}", json={"default_currency": "SAR"}).status_code == 200
    post(api, books, draft(api, books)["id"])
    assert api.req("PATCH", OWNER, f"/orgs/{org}", json={"default_currency": "USD"}).status_code == 409


def test_ledger_rejects_unbalanced_entries_and_edits(app, api, books):
    from datetime import date

    with app.state.sessionmaker() as db:
        bind_tenant(db, books["org"])
        cash, bank = system_account(db, "cash"), system_account(db, "bank")
        with pytest.raises(LedgerError):
            post_entry(db, entry_date=date(2026, 1, 1), currency="SAR",
                       lines=[LineSpec(cash, debit_minor=100), LineSpec(bank, credit_minor=99)])
        with pytest.raises(LedgerError):
            post_entry(db, entry_date=date(2026, 1, 1), currency="SAR", lines=[LineSpec(cash, debit_minor=100)])
        entry = post_entry(db, entry_date=date(2026, 1, 1), currency="SAR",
                           lines=[LineSpec(cash, debit_minor=100), LineSpec(bank, credit_minor=100)])
        db.commit()
        line = db.scalars(select(JournalLine).where(JournalLine.entry_id == entry.id)).first()
        line.debit_minor = 5
        with pytest.raises(LedgerImmutable):
            db.flush()
        db.rollback()
        db.delete(db.get(JournalEntry, entry.id))
        with pytest.raises(LedgerImmutable):
            db.flush()
