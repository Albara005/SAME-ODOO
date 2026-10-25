"""Payroll: each employee's pay, contributions on it, a month worked out (part months and unpaid leave), posted to
the ledger, paid, and the payslips their owners can see."""

from __future__ import annotations

import csv
import io

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from novara.modules.accounting.models import Account
from novara.modules.accounting.service import system_account
from novara.modules.payroll.router import iban_ok
from novara.tenancy import bind_tenant
from tests.conftest import requires_pg

OWNER, ACCT, HUDA, PAY = (f"{n}@payroll.example.com" for n in ("owner", "acct", "huda", "pay"))
SA_IBAN = "SA0380000000608010167519"


@pytest.fixture
def pr(api):
    for e in (OWNER, ACCT, HUDA, PAY):
        api.signup(e)
    org = api.create_org(OWNER, "payroll-co")
    assert api.add_member(OWNER, org, ACCT, "accountant").status_code == 201
    assert api.add_member(OWNER, org, HUDA, "member").status_code == 201
    huda_user = next(m["user_id"] for m in api.req("GET", OWNER, f"/orgs/{org}/members").json() if m["email"] == HUDA)

    def post(path, body, user=OWNER):
        r = api.req("POST", user, f"/orgs/{org}{path}", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    return {"org": org, "post": post, "huda_user": huda_user}


def url(s, path):
    return f"/orgs/{s['org']}{path}"


def balances(api, s):
    tb = api.req("GET", OWNER, url(s, "/accounting/trial-balance")).json()
    assert tb["balanced"]
    return {l["code"]: l["balance"] for l in tb["lines"]}


def set_pay(api, s, employee, items, contributions=(), iban=""):
    r = api.req("PUT", OWNER, url(s, f"/payroll/employees/{employee}"),
                json={"iban": iban, "contribution_ids": list(contributions), "items": items})
    assert r.status_code == 200, r.text
    return r.json()


def earning(name, amount, contributory=False):
    return {"name": name, "kind": "earning", "amount": amount, "contributory": contributory}


def test_a_month_is_worked_out_posted_and_paid(api, pr):
    post = pr["post"]
    gosi = post("/payroll/contributions", {"name": "GOSI", "employee_rate": "9.75", "employer_rate": "11.75",
                                          "base_cap": "45000"})
    assert (gosi["employee_rate"], gosi["base_cap"]) == ("9.75", "45000.00")
    huda = post("/hr/employees", {"full_name": "Huda Saleh", "hire_date": "2025-01-01", "user_id": pr["huda_user"]})["id"]
    omar = post("/hr/employees", {"full_name": "Omar Faris", "hire_date": "2026-09-16"})["id"]
    sami = post("/hr/employees", {"full_name": "Sami Nasser", "hire_date": "2024-03-01"})["id"]
    post("/hr/employees", {"full_name": "Zaid (no pay yet)", "hire_date": "2026-01-01"})
    left = post("/hr/employees", {"full_name": "Left In August", "hire_date": "2024-01-01"})["id"]
    api.req("PATCH", OWNER, url(pr, f"/hr/employees/{left}"), json={"end_date": "2026-08-31"})

    out = set_pay(api, pr, huda, [earning("Basic salary", "10000", True), earning("Housing", "2500", True),
                                  earning("Transport", "500"),
                                  {"name": "Advance repayment", "kind": "deduction", "amount": "1000"}],
                  [gosi["id"]], iban="sa03 8000 0000 6080 1016 7519")
    assert (out["monthly_earnings"], out["monthly_deductions"], out["iban"]) == ("13000.00", "1000.00", SA_IBAN)
    set_pay(api, pr, omar, [earning("Basic salary", "6000")])
    set_pay(api, pr, sami, [earning("Basic salary", "9000")])
    set_pay(api, pr, left, [earning("Basic salary", "7000")])
    unpaid = post("/hr/leave-types", {"name": "Unpaid", "paid": False})["id"]
    leave = post("/hr/leave-requests", {"employee_id": sami, "leave_type_id": unpaid,
                                        "start_date": "2026-09-06", "end_date": "2026-09-07"})
    api.req("POST", OWNER, url(pr, f"/hr/leave-requests/{leave['id']}/approve"), json={})

    run = api.req("POST", OWNER, url(pr, "/payroll/runs"), json={"month": "2026-09"})
    assert run.status_code == 201, run.text
    run = run.json()
    assert run["without_pay"] == ["Zaid (no pay yet)"]
    slips = {p["employee_name"]: p for p in run["payslips"]}
    assert set(slips) == {"Huda Saleh", "Omar Faris", "Sami Nasser"}           # Left In August isn't in September
    h = slips["Huda Saleh"]
    assert (h["gross"], h["deductions"], h["employee_contributions"], h["employer_contributions"], h["net"]) == (
        "13000.00", "1000.00", "1218.75", "1468.75", "10781.25")               # 9.75% / 11.75% of 12,500
    assert {(l["name"], l["amount"], l["employer_amount"]) for l in h["lines"]} >= {("GOSI", "1218.75", "1468.75")}
    assert (slips["Omar Faris"]["days_paid"], slips["Omar Faris"]["net"]) == ("15", "3000.00")      # from the 16th
    assert (slips["Sami Nasser"]["unpaid_leave_days"], slips["Sami Nasser"]["net"]) == ("2", "8400.00")
    assert (run["gross"], run["net"], run["status"]) == ("24400.00", "22181.25", "draft")
    assert api.req("GET", HUDA, url(pr, "/payroll/my-payslips")).json() == []                  # not until posted
    assert api.req("POST", OWNER, url(pr, "/payroll/runs"), json={"month": "2026-09"}).status_code == 409

    posted = api.req("POST", OWNER, url(pr, f"/payroll/runs/{run['id']}/post"))
    assert posted.status_code == 200, posted.text
    bal = balances(api, pr)
    assert (bal["6100"], bal["6110"], bal["2250"], bal["2260"], bal["1350"]) == (
        "24400.00", "1468.75", "-22181.25", "-2687.50", "-1000.00")
    assert api.req("DELETE", OWNER, url(pr, f"/payroll/runs/{run['id']}")).status_code == 409
    assert api.req("POST", OWNER, url(pr, f"/payroll/runs/{run['id']}/recalculate")).status_code == 409

    bank = api.req("GET", OWNER, url(pr, f"/payroll/runs/{run['id']}/bank-file.csv"))
    rows = list(csv.reader(io.StringIO(bank.content.decode("utf-8-sig"))))
    assert rows[0] == ["employee", "iban", "amount", "currency", "reference"]
    assert ["Huda Saleh", SA_IBAN, "10781.25", "SAR", "Salary 2026-09"] in rows

    mine = api.req("GET", HUDA, url(pr, "/payroll/my-payslips")).json()
    assert [(p["month"], p["net"]) for p in mine] == [("2026-09", "10781.25")]
    assert api.req("GET", HUDA, url(pr, "/payroll/runs")).status_code == 403
    assert api.req("GET", HUDA, url(pr, f"/payroll/employees/{huda}")).status_code == 403

    paid = api.req("POST", OWNER, url(pr, f"/payroll/runs/{run['id']}/pay"),
                   json={"payment_date": "2026-09-28", "method": "bank"})
    assert paid.status_code == 200 and paid.json()["status"] == "paid"
    bal = balances(api, pr)
    assert (bal.get("2250", "0.00"), bal["1200"]) == ("0.00", "-22181.25")
    assert api.req("POST", OWNER, url(pr, f"/payroll/runs/{run['id']}/cancel"),
                   json={"cancel_date": "2026-10-01"}).status_code == 409                    # paid: final
    actions = [e["action"] for e in api.req("GET", OWNER, url(pr, "/audit-logs")).json()]
    assert {"payroll.pay_changed", "payroll.run_created", "payroll.run_posted", "payroll.run_paid"} <= set(actions)
    changed = next(e for e in api.req("GET", OWNER, url(pr, "/audit-logs")).json() if e["action"] == "payroll.pay_changed")
    assert "10000" not in str(changed["details"])                                            # no salaries in the log


def test_drafts_change_with_pay_and_posted_runs_are_cancelled_not_edited(api, pr):
    post = pr["post"]
    huda = post("/hr/employees", {"full_name": "Huda Saleh", "hire_date": "2025-01-01"})["id"]
    set_pay(api, pr, huda, [earning("Basic salary", "8000")])
    run = api.req("POST", OWNER, url(pr, "/payroll/runs"), json={"month": "2026-10"}).json()
    assert run["net"] == "8000.00"
    set_pay(api, pr, huda, [earning("Basic salary", "8500")])
    assert api.req("POST", OWNER, url(pr, f"/payroll/runs/{run['id']}/recalculate")).json()["net"] == "8500.00"
    set_pay(api, pr, huda, [earning("Basic salary", "1000"), {"name": "Loan", "kind": "deduction", "amount": "1500"}])
    refused = api.req("POST", OWNER, url(pr, f"/payroll/runs/{run['id']}/post"))
    assert refused.status_code == 422 and "more than their pay" in refused.json()["detail"]
    set_pay(api, pr, huda, [earning("Basic salary", "9000")])
    assert api.req("POST", ACCT, url(pr, f"/payroll/runs/{run['id']}/post")).status_code == 403   # payroll.manage
    posted = api.req("POST", OWNER, url(pr, f"/payroll/runs/{run['id']}/post")).json()
    assert posted["net"] == "9000.00"                                          # posting takes the pay as it is now

    early = api.req("POST", OWNER, url(pr, f"/payroll/runs/{run['id']}/cancel"), json={"cancel_date": "2026-10-15"})
    assert early.status_code == 422
    cancelled = api.req("POST", OWNER, url(pr, f"/payroll/runs/{run['id']}/cancel"), json={"cancel_date": "2026-10-31"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert balances(api, pr).get("6100", "0.00") == "0.00"
    again = api.req("POST", OWNER, url(pr, "/payroll/runs"), json={"month": "2026-10"})
    assert again.status_code == 201                                            # a cancelled run frees the month
    assert api.req("DELETE", OWNER, url(pr, f"/payroll/runs/{again.json()['id']}")).status_code == 204

    # Paying needs payments.write too: a payroll-only role can't move money.
    api.req("POST", OWNER, url(pr, "/roles"), json={"key": "payroll", "name": "Payroll", "permissions": ["payroll.manage"]})
    assert api.add_member(OWNER, pr["org"], PAY, "payroll").status_code == 201
    nov = api.req("POST", PAY, url(pr, "/payroll/runs"), json={"month": "2026-11"}).json()
    assert api.req("POST", PAY, url(pr, f"/payroll/runs/{nov['id']}/post")).status_code == 200
    assert api.req("POST", PAY, url(pr, f"/payroll/runs/{nov['id']}/pay"),
                   json={"payment_date": "2026-11-27", "method": "bank"}).status_code == 403

    bad = api.req("PUT", OWNER, url(pr, f"/payroll/employees/{huda}"),
                  json={"iban": "SA0380000000608010167518", "items": []})
    assert bad.status_code == 422                                              # a wrong check digit
    assert api.req("PUT", OWNER, url(pr, f"/payroll/employees/{huda}"),
                   json={"contribution_ids": ["nope"], "items": []}).status_code == 422
    assert api.req("POST", OWNER, url(pr, "/payroll/runs"), json={"month": "2026-13"}).status_code == 422


def test_ibans_and_the_chart_never_takes_over_an_organizations_own_account(api, pr, app):
    assert iban_ok(SA_IBAN) and iban_ok("GB82WEST12345698765432")
    assert not iban_ok("GB82WEST12345698765433") and not iban_ok("SA03") and not iban_ok("sa0380000000608010167519")
    # An organization from before payroll that numbered its own account 6100.
    with app.state.sessionmaker() as db:
        bind_tenant(db, pr["org"])
        salaries = db.scalar(select(Account).where(Account.system_key == "salaries"))
        salaries.system_key, salaries.name = None, "Office rent"
        db.commit()
        account = system_account(db, "salaries")
        db.commit()
        assert (account.code, account.name) == ("6101", "Salaries and Wages")
        assert db.scalar(select(Account).where(Account.code == "6100")).name == "Office rent"


@requires_pg
def test_the_database_keeps_posted_runs_final(api, pr, app):
    huda = pr["post"]("/hr/employees", {"full_name": "Huda Saleh", "hire_date": "2025-01-01"})["id"]
    set_pay(api, pr, huda, [earning("Basic salary", "8000")])
    run = api.req("POST", OWNER, url(pr, "/payroll/runs"), json={"month": "2026-10"}).json()
    api.req("POST", OWNER, url(pr, f"/payroll/runs/{run['id']}/post"))

    def raw(sql):
        with app.state.sessionmaker() as db:
            bind_tenant(db, pr["org"])
            db.execute(text(sql), {"id": run["id"]})
            db.commit()

    for sql in ("DELETE FROM pay_runs WHERE id = :id",
                "UPDATE pay_runs SET net_minor = 1 WHERE id = :id",
                "UPDATE pay_runs SET status = 'draft' WHERE id = :id",
                "UPDATE payslips SET net_minor = 1 WHERE run_id = :id",
                "DELETE FROM payslips WHERE run_id = :id"):
        with pytest.raises(DBAPIError, match="posted pay run|permission denied"):
            raw(sql)
