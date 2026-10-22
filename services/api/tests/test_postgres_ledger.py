"""Database-level accounting guarantees on PostgreSQL, exercised with raw SQL so
the application-level checks are not involved."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from novara.tenancy import bind_tenant, system_scope
from tests.conftest import requires_pg

pytestmark = requires_pg

OWNER = "owner@pg.example.com"


@pytest.fixture
def posted(api):
    api.signup(OWNER)
    org = api.create_org(OWNER, "pg-co")
    cust = api.req("POST", OWNER, f"/orgs/{org}/customers", json={"name": "C"}).json()["id"]
    inv = api.req("POST", OWNER, f"/orgs/{org}/invoices", json={
        "customer_id": cust, "issue_date": "2026-03-01",
        "lines": [{"description": "x", "quantity": "1", "unit_price": "100"}],
    }).json()
    assert api.req("POST", OWNER, f"/orgs/{org}/invoices/{inv['id']}/post").status_code == 200
    return org, inv["id"]


def _raw(app, org, sql, params=None):
    """Run one or more statements (split on ';') in a single transaction, bypassing the ORM."""
    with app.state.sessionmaker() as db:
        bind_tenant(db, org)
        with system_scope(db):
            for stmt in filter(str.strip, sql.split(";")):
                db.execute(text(stmt), params or {})
            db.commit()


def test_unbalanced_entry_is_rejected_at_commit(app, posted):
    org, _ = posted
    with app.state.sessionmaker() as db:
        bind_tenant(db, org)
        accounts = db.execute(text("SELECT id FROM accounts ORDER BY code LIMIT 2")).scalars().all()
    sql = """
        INSERT INTO journal_entries (id, organization_id, number, entry_date, currency, memo, source_type, source_id, created_at)
        VALUES ('je-x', :org, 'JE-X', '2026-01-01', 'SAR', '', '', '', now());
        INSERT INTO journal_lines (id, organization_id, entry_id, account_id, debit_minor, credit_minor, label)
        VALUES ('l1', :org, 'je-x', :a, 100, 0, ''), ('l2', :org, 'je-x', :b, 0, 99, '');
    """
    with pytest.raises(DBAPIError, match="unbalanced"):
        _raw(app, org, sql, {"org": org, "a": accounts[0], "b": accounts[1]})


def test_entry_without_lines_is_rejected(app, posted):
    org, _ = posted
    sql = """INSERT INTO journal_entries (id, organization_id, number, entry_date, currency, memo, source_type, source_id, created_at)
             VALUES ('je-y', :org, 'JE-Y', '2026-01-01', 'SAR', '', '', '', now())"""
    with pytest.raises(DBAPIError, match="unbalanced"):
        _raw(app, org, sql, {"org": org})


def test_ledger_rows_cannot_be_changed_by_the_api_role(app, posted):
    org, _ = posted
    for sql in ("UPDATE journal_lines SET debit_minor = debit_minor + 1",
                "DELETE FROM journal_entries", "DELETE FROM payments"):
        with pytest.raises(DBAPIError, match="permission denied"):
            _raw(app, org, sql)


def test_posted_invoice_cannot_be_edited_in_sql(app, posted):
    org, inv = posted
    with pytest.raises(DBAPIError, match="cannot be edited"):
        _raw(app, org, "UPDATE invoices SET total_minor = 1 WHERE id = :i", {"i": inv})
    with pytest.raises(DBAPIError, match="cannot be changed"):
        _raw(app, org, "UPDATE invoice_lines SET quantity = 5 WHERE invoice_id = :i", {"i": inv})
    with pytest.raises(DBAPIError, match="cannot be deleted"):
        _raw(app, org, "DELETE FROM invoices WHERE id = :i", {"i": inv})


def test_rows_cannot_reference_another_organizations_records(api, app, posted):
    org, _ = posted
    api.signup("b@pg.example.com")
    other = api.create_org("b@pg.example.com", "pg-other")
    foreign_customer = api.req("POST", "b@pg.example.com", f"/orgs/{other}/customers", json={"name": "B"}).json()["id"]
    sql = """INSERT INTO invoices (id, organization_id, customer_id, status, issue_date, due_date, currency, notes,
                                   subtotal_minor, tax_minor, total_minor, amount_paid_minor, created_at)
             VALUES ('inv-x', :org, :c, 'draft', '2026-01-01', '2026-01-01', 'SAR', '', 0, 0, 0, 0, now())"""
    with pytest.raises(DBAPIError, match="foreign key"):
        _raw(app, org, sql, {"org": org, "c": foreign_customer})
