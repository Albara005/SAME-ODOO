"""Leave allowances earned monthly or given upfront, prorated for part of a year, carried over; half days."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from novara.modules.hr import service as svc
from novara.modules.hr.models import Employee, LeaveType

OWNER = "owner@leave-rules.example.com"


@pytest.fixture
def lr(api):
    api.signup(OWNER)
    org = api.create_org(OWNER, "leave-rules-co")

    def post(p, body):
        r = api.req("POST", OWNER, f"/orgs/{org}/hr{p}", json=body)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    return {"org": org, "post": post}


def working(d: date) -> date:
    """The first working day from d (the default weekend is Friday and Saturday)."""
    while d.weekday() in (4, 5):
        d += timedelta(days=1)
    return d


def ask(api, s, employee, kind, start, end=None, **kw):
    return api.req("POST", OWNER, f"/orgs/{s['org']}/hr/leave-requests", json={
        "employee_id": employee, "leave_type_id": kind, "start_date": start.isoformat(),
        "end_date": (end or start).isoformat(), **kw})


def approve(api, s, request_id):
    r = api.req("POST", OWNER, f"/orgs/{s['org']}/hr/leave-requests/{request_id}/approve", json={})
    assert r.status_code == 200, r.text


def balance(api, s, employee, year, kind_name):
    rows = api.req("GET", OWNER, f"/orgs/{s['org']}/hr/employees/{employee}/balances", params={"year": year}).json()
    return next(b for b in rows if b["leave_type"] == kind_name)


def test_what_is_earned_and_given():
    monthly = LeaveType(name="Annual", annual_days=24, accrual="monthly", prorate=False, carry_over_days=0)
    upfront = LeaveType(name="Upfront", annual_days=30, accrual="upfront", prorate=True, carry_over_days=0)
    whole = LeaveType(name="Whole", annual_days=30, accrual="upfront", prorate=False, carry_over_days=0)
    since_jan = Employee(full_name="A", hire_date=date(2027, 1, 1))
    since_july = Employee(full_name="B", hire_date=date(2027, 7, 1))
    leaving = Employee(full_name="C", hire_date=date(2020, 1, 1), end_date=date(2027, 3, 31))
    assert svc.earned(monthly, since_jan, 2027, date(2027, 2, 10)) == Decimal("4")     # 24 x 59/365, to the half day
    assert svc.earned(monthly, since_jan, 2027, date(2027, 12, 1)) == Decimal("24")
    assert svc.earned(monthly, since_jan, 2027, date(2026, 12, 31)) == Decimal("0")   # next year: nothing yet
    assert svc.allowance(upfront, since_july, 2027) == Decimal("15")                    # 30 x 184/365
    assert svc.allowance(upfront, leaving, 2027) == Decimal("7.5")                      # 30 x 90/365
    assert svc.allowance(whole, since_july, 2027) == Decimal("30")                      # not prorated
    assert svc.earned(monthly, since_july, 2027, date(2027, 7, 15)) == Decimal("2")     # July's 31 days
    assert [svc.half_days(Decimal(x)) for x in ("3.24", "3.25", "3.74", "3.75")] == [Decimal("3"), Decimal("3.5"),
                                                                                      Decimal("3.5"), Decimal("4")]


def test_monthly_accrual_and_proration_limit_requests(api, lr):
    post = lr["post"]
    monthly = post("/leave-types", {"name": "Annual", "annual_days": 24, "accrual": "monthly"})
    prorated = post("/leave-types", {"name": "Study", "annual_days": 30, "prorate": True})
    jan = post("/employees", {"full_name": "Huda", "hire_date": "2027-01-01"})
    july = post("/employees", {"full_name": "Omar", "hire_date": "2027-07-01"})

    feb = working(date(2027, 2, 7))
    five = feb
    for _ in range(4):
        five = working(five + timedelta(days=1))
    refused = ask(api, lr, jan, monthly, feb, five)                   # 5 days in February: 4 earned by its end
    assert refused.status_code == 422 and "earned by the end of February 2027" in refused.json()["detail"]
    four = feb
    for _ in range(3):
        four = working(four + timedelta(days=1))
    assert ask(api, lr, jan, monthly, feb, four).status_code == 201
    assert ask(api, lr, jan, monthly, working(date(2027, 2, 24))).status_code == 422   # February is used up

    start = working(date(2027, 8, 1))
    end = start
    for _ in range(15):                                               # 16 working days
        end = working(end + timedelta(days=1))
    over = ask(api, lr, july, prorated, start, end)
    assert over.status_code == 422 and "only 15 day(s) of Study left in 2027" in over.json()["detail"]
    b = balance(api, lr, july, 2027, "Study")
    assert (b["allowance"], b["earned"], b["remaining"], b["accrual"]) == (15, 15, 15, "upfront")


def test_unused_days_carry_over_up_to_the_limit(api, lr):
    post = lr["post"]
    annual = post("/leave-types", {"name": "Annual", "annual_days": 21, "carry_over_days": 5})
    huda = post("/employees", {"full_name": "Huda", "hire_date": "2025-01-01"})

    def take(start: date, days: int):
        end = start = working(start)
        for _ in range(days - 1):
            end = working(end + timedelta(days=1))
        r = ask(api, lr, huda, annual, start, end)
        assert r.status_code == 201, r.text
        approve(api, lr, r.json()["id"])

    take(date(2025, 3, 2), 10)            # 11 left in 2025: 5 carry into 2026
    b = balance(api, lr, huda, 2026, "Annual")
    assert (b["allowance"], b["carried"], b["remaining"]) == (21, 5, 26)
    take(date(2026, 3, 1), 20)            # 21 + 5 - 20 = 6 left: again only 5 carry
    b = balance(api, lr, huda, 2027, "Annual")
    assert (b["allowance"], b["carried"], b["remaining"]) == (21, 5, 26)
    take(date(2027, 1, 3), 26)            # all 26 can be used in 2027
    assert ask(api, lr, huda, annual, working(date(2027, 6, 1))).status_code == 422


def test_half_days(api, lr):
    post = lr["post"]
    annual = post("/leave-types", {"name": "Annual", "annual_days": 21})
    huda = post("/employees", {"full_name": "Huda", "hire_date": "2025-01-01"})
    day = working(date(2027, 4, 4))
    morning = ask(api, lr, huda, annual, day, half_day="am")
    assert morning.status_code == 201 and (morning.json()["days"], morning.json()["half_day"]) == (0.5, "am")
    assert ask(api, lr, huda, annual, day, half_day="am").status_code == 422        # that morning is taken
    assert ask(api, lr, huda, annual, day).status_code == 422                        # so is the whole day
    afternoon = ask(api, lr, huda, annual, day, half_day="pm")
    assert afternoon.status_code == 201
    two_days = ask(api, lr, huda, annual, day + timedelta(days=1), working(day + timedelta(days=2)), half_day="am")
    assert two_days.status_code == 422 and "single day" in two_days.json()["detail"]
    weekend = day
    while weekend.weekday() != 4:
        weekend += timedelta(days=1)
    assert ask(api, lr, huda, annual, weekend, half_day="pm").status_code == 422
    approve(api, lr, morning.json()["id"])
    b = balance(api, lr, huda, 2027, "Annual")
    assert (b["approved"], b["pending"], b["remaining"]) == (0.5, 0.5, 20)
