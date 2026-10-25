"""End-of-service benefits: the market's rules worked out exactly, a provision that follows every posted pay run, and a
leaver's settlement that releases it, pays the award and unused leave, and can be cancelled but never edited."""

from __future__ import annotations

from datetime import date, timedelta
from fractions import Fraction

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from novara.modules.payroll import eosb
from novara.tenancy import bind_tenant
from tests.conftest import requires_pg
from tests.test_payroll import ACCT, HUDA, OWNER, balances, earning, pr, set_pay, url  # noqa: F401 (pr is a fixture)

WAGE = 1_000_000                      # 10,000.00 a month, in halalas


def after(start: date, days: int) -> date:
    """The last day of a service of exactly this many days."""
    return start + timedelta(days=days - 1)


def owed(rule, days, reason="termination"):
    return eosb.award(rule, WAGE, date(2000, 1, 1), after(date(2000, 1, 1), days), reason).award_minor


def test_saudi_and_uae_rules():
    y = eosb.YEAR
    assert owed("saudi", 3 * y) == 1_500_000                          # half a month a year for the first five
    assert owed("saudi", 7 * y) == 4_500_000                          # 2.5 + a full month for each year after
    assert owed("saudi", 830) == 1_136_986                            # part of a year counts: 10,000 x 830/730
    assert owed("saudi", 3 * y, "resignation") == 500_000             # a third from two to five years
    assert owed("saudi", 7 * y, "resignation") == 3_000_000           # two thirds from five to ten
    assert owed("saudi", 12 * y, "resignation") == 9_500_000          # all of it from ten
    assert owed("saudi", 2 * y - 1, "resignation") == 0               # nothing before two years
    assert owed("saudi", y, "resignation_full") == 500_000            # article 87: resigning keeps the whole award
    assert owed("saudi", 7 * y, "dismissal") == 0                     # article 80
    assert owed("uae", 3 * y) == 2_100_000                            # 21 days a year for the first five
    assert owed("uae", y - 1) == 0                                    # nothing before a year
    assert owed("uae", 30 * y) == 24_000_000                          # at most two years' wage
    assert owed("uae", 3 * y, "resignation") == 2_100_000             # resigning doesn't reduce it
    with pytest.raises(eosb.EndOfServiceError):
        owed("uae", 3 * y, "dismissal")
    with pytest.raises(eosb.EndOfServiceError):
        eosb.award("saudi", WAGE, date(2026, 1, 2), date(2026, 1, 1))
    assert eosb.leave_pay(1_250_000, Fraction(5)) == 208_333          # a day is a thirtieth of the month
    assert eosb.liability("none", WAGE, date(2020, 1, 1), date(2026, 1, 1))[1] == 0


def setup_huda(api, s, rule="saudi", hire="2024-10-01"):
    assert api.req("PUT", OWNER, url(s, "/payroll/settings"), json={"eosb_rule": rule}).json()["eosb_rule"] == rule
    huda = s["post"]("/hr/employees", {"full_name": "Huda Saleh", "hire_date": hire, "user_id": s["huda_user"]})["id"]
    out = set_pay(api, s, huda, [earning("Basic salary", "10000", True) | {"end_of_service": True},
                                 earning("Housing", "2500", True) | {"end_of_service": True},
                                 earning("Transport", "500")])
    assert [i["end_of_service"] for i in out["items"]] == [True, True, False]
    return huda


def post_month(api, s, month):
    run = api.req("POST", OWNER, url(s, "/payroll/runs"), json={"month": month})
    assert run.status_code == 201, run.text
    posted = api.req("POST", OWNER, url(s, f"/payroll/runs/{run.json()['id']}/post"))
    assert posted.status_code == 200, posted.text
    return posted.json()


def test_the_provision_follows_every_posted_run(api, pr):
    plain = post_month_without_rules(api, pr)
    assert plain["end_of_service_change"] == "0.00"                   # no rules chosen: nothing is provisioned

    setup_huda(api, pr)
    sep = post_month(api, pr, "2026-09")
    (slip,) = [p for p in sep["payslips"] if p["employee_name"] == "Huda Saleh"]
    assert (slip["service_days"], slip["end_of_service_accrued"]) == (730, "12500.00")   # two years: one month
    assert sep["end_of_service_change"] == "12500.00"
    bal = balances(api, pr)
    assert (bal["2270"], bal["6120"]) == ("-12500.00", "12500.00")

    oct_ = post_month(api, pr, "2026-10")
    assert oct_["end_of_service_change"] == "530.82"                  # 12,500 x 761/730 - 12,500
    assert balances(api, pr)["2270"] == "-13030.82"

    cancel = api.req("POST", OWNER, url(pr, f"/payroll/runs/{oct_['id']}/cancel"), json={"cancel_date": "2026-11-02"})
    assert cancel.status_code == 200, cancel.text
    assert balances(api, pr)["2270"] == "-12500.00"
    assert post_month(api, pr, "2026-10")["end_of_service_change"] == "530.82"   # again from September's

    # A raise grows the provision for the service already done, too.
    huda = next(e["employee_id"] for e in api.req("GET", OWNER, url(pr, "/payroll/employees")).json()
                if e["employee_name"] == "Huda Saleh")
    set_pay(api, pr, huda, [earning("Basic salary", "12000", True) | {"end_of_service": True},
                            earning("Housing", "2500", True) | {"end_of_service": True}])
    nov = post_month(api, pr, "2026-11")
    assert nov["end_of_service_change"] == "2680.82"                  # 14,500 x 791/730 - 13,030.82
    mine = api.req("GET", HUDA, url(pr, "/payroll/my-payslips")).json()[0]
    assert (mine["month"], mine["end_of_service_accrued"]) == ("2026-11", "15711.64")


def post_month_without_rules(api, s):
    other = s["post"]("/hr/employees", {"full_name": "Sami Nasser", "hire_date": "2020-01-01"})["id"]
    set_pay(api, s, other, [earning("Basic salary", "9000") | {"end_of_service": True}])
    run = post_month(api, s, "2026-08")
    api.req("PATCH", OWNER, url(s, f"/hr/employees/{other}"), json={"end_date": "2026-08-31"})
    return run


def test_a_resignation_is_settled_and_paid(api, pr):
    huda = setup_huda(api, pr)
    post_month(api, pr, "2026-09")
    post_month(api, pr, "2026-10")
    body = {"employee_id": huda, "last_day": "2026-11-15", "reason": "resignation", "leave_days": "5"}
    preview = api.req("POST", OWNER, url(pr, "/payroll/end-of-service/preview"), json=body)
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert (p["service_days"], p["service_years"], p["share"], p["months"]) == (776, "2.13", "1/3", "1.0630")
    assert (p["full_award"], p["award"], p["leave_pay"], p["provision"], p["total"]) == (
        "13287.67", "4429.22", "2083.33", "13030.82", "6512.55")

    doc = api.req("POST", OWNER, url(pr, "/payroll/end-of-service"), json=body | {"notes": "Moving abroad"})
    assert doc.status_code == 201, doc.text
    doc = doc.json()
    assert (doc["status"], doc["total"]) == ("draft", "6512.55")
    assert api.req("POST", OWNER, url(pr, "/payroll/end-of-service"), json=body).status_code == 409   # one per person

    posted = api.req("POST", OWNER, url(pr, f"/payroll/end-of-service/{doc['id']}/post"))
    assert posted.status_code == 200, posted.text
    bal = balances(api, pr)
    assert (bal.get("2270", "0.00"), bal["6120"]) == ("0.00", "4429.22")      # the provision released, only the award kept
    record = api.req("GET", OWNER, url(pr, f"/hr/employees/{huda}")).json()
    assert record["end_date"] == "2026-11-15"

    nov = post_month(api, pr, "2026-11")                               # half a month's pay, nothing more provisioned
    (slip,) = nov["payslips"]
    assert (slip["days_paid"], nov["end_of_service_change"]) == ("15", "0.00")

    assert api.req("POST", OWNER, url(pr, f"/payroll/end-of-service/{doc['id']}/pay"),
                   json={"payment_date": "2026-11-14", "method": "bank"}).status_code == 422   # before the last day
    paid = api.req("POST", OWNER, url(pr, f"/payroll/end-of-service/{doc['id']}/pay"),
                   json={"payment_date": "2026-11-20", "method": "bank"})
    assert paid.status_code == 200 and paid.json()["status"] == "paid"
    assert api.req("POST", OWNER, url(pr, f"/payroll/end-of-service/{doc['id']}/cancel"),
                   json={"cancel_date": "2026-11-21"}).status_code == 409
    assert api.req("DELETE", OWNER, url(pr, f"/payroll/end-of-service/{doc['id']}")).status_code == 409


def test_a_posted_settlement_is_cancelled_with_a_reversal(api, pr):
    huda = setup_huda(api, pr)
    post_month(api, pr, "2026-09")
    body = {"employee_id": huda, "last_day": "2026-09-30", "reason": "termination"}
    doc = api.req("POST", OWNER, url(pr, "/payroll/end-of-service"), json=body).json()
    posted = api.req("POST", OWNER, url(pr, f"/payroll/end-of-service/{doc['id']}/post")).json()
    assert (posted["award"], posted["provision"], posted["total"]) == ("12500.00", "12500.00", "12500.00")
    assert balances(api, pr).get("2270", "0.00") == "0.00"
    cancelled = api.req("POST", OWNER, url(pr, f"/payroll/end-of-service/{doc['id']}/cancel"), json={"cancel_date": "2026-10-01"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert balances(api, pr)["2270"] == "-12500.00"                    # the provision is back
    again = api.req("POST", OWNER, url(pr, "/payroll/end-of-service"), json=body)
    assert again.status_code == 201                                     # a cancelled one doesn't block a new one
    assert api.req("DELETE", OWNER, url(pr, f"/payroll/end-of-service/{again.json()['id']}")).status_code == 204


def test_what_a_settlement_needs(api, pr):
    huda = pr["post"]("/hr/employees", {"full_name": "Huda Saleh", "hire_date": "2024-10-01"})["id"]
    body = {"employee_id": huda, "last_day": "2026-09-30", "reason": "termination"}
    preview = lambda b=body, user=OWNER: api.req("POST", user, url(pr, "/payroll/end-of-service/preview"), json=b)  # noqa: E731
    assert "choose the end-of-service rules" in preview().json()["detail"]
    api.req("PUT", OWNER, url(pr, "/payroll/settings"), json={"eosb_rule": "uae"})
    assert "none of Huda Saleh's earnings count" in preview().json()["detail"]
    set_pay(api, pr, huda, [earning("Basic salary", "10000") | {"end_of_service": True}])
    assert preview().status_code == 200
    assert "isn't a reason for leaving under the uae rules" in preview(body | {"reason": "dismissal"}).json()["detail"]
    assert preview(body | {"leave_days": "1.25"}).status_code == 422
    assert preview(body | {"last_day": "2024-09-01"}).status_code == 422
    nohire = pr["post"]("/hr/employees", {"full_name": "No Date"})["id"]
    assert "no hire date" in preview(body | {"employee_id": nohire}).json()["detail"]
    api.req("PATCH", OWNER, url(pr, f"/hr/employees/{huda}"), json={"end_date": "2026-09-29"})
    assert "last day on their record is 2026-09-29" in preview().json()["detail"]

    assert preview(user=HUDA).status_code == 403                       # payroll.manage only
    assert preview(user=ACCT).status_code == 403
    settings = api.req("GET", OWNER, url(pr, "/payroll/settings")).json()
    assert settings == {"eosb_rule": "uae", "reasons": ["termination", "contract_end", "resignation"]}
    assert api.req("PUT", OWNER, url(pr, "/payroll/settings"), json={"eosb_rule": "egypt"}).status_code == 422


@requires_pg
def test_the_database_keeps_posted_settlements_final(api, pr, app):
    huda = setup_huda(api, pr)
    doc = api.req("POST", OWNER, url(pr, "/payroll/end-of-service"),
                  json={"employee_id": huda, "last_day": "2026-09-30", "reason": "termination"}).json()
    api.req("POST", OWNER, url(pr, f"/payroll/end-of-service/{doc['id']}/post"))

    def raw(sql):
        with app.state.sessionmaker() as db:
            bind_tenant(db, pr["org"])
            db.execute(text(sql), {"id": doc["id"]})
            db.commit()

    for sql in ("DELETE FROM end_of_service WHERE id = :id",
                "UPDATE end_of_service SET award_minor = 1 WHERE id = :id",
                "UPDATE end_of_service SET status = 'draft' WHERE id = :id"):
        with pytest.raises(DBAPIError, match="end-of-service settlement"):
            raw(sql)
    raw("UPDATE end_of_service SET status = 'paid' WHERE id = :id")
    with pytest.raises(DBAPIError, match="paid end-of-service settlement cannot change status"):
        raw("UPDATE end_of_service SET status = 'cancelled' WHERE id = :id")
