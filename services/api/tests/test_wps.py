"""The UAE Wage Protection System's salary file (SIF) for a posted pay run: one EDR line per employee paid and the
SCR control line, refused (with what is missing, for whom) until the employer and each employee have their WPS ids."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

import pytest

from novara.modules.payroll import wps
from tests.test_payroll import OWNER, earning, pr, set_pay, url  # noqa: F401 (pr is a fixture)

AE_IBAN = "AE070331234567890123456"


def test_a_posted_runs_salary_file(api, pr):
    s = pr
    assert api.req("PATCH", OWNER, f"/orgs/{s['org']}", json={"default_currency": "AED"}).status_code == 200
    huda = s["post"]("/hr/employees", {"full_name": "Huda Saleh", "hire_date": "2025-01-01"})["id"]
    omar = s["post"]("/hr/employees", {"full_name": "Omar Ali", "hire_date": "2026-09-16"})["id"]   # half the month
    set_pay(api, s, huda, [earning("Basic", "10000"), earning("Housing", "2500")], iban=AE_IBAN)
    set_pay(api, s, omar, [earning("Basic", "6000")], iban=AE_IBAN)
    run = api.req("POST", OWNER, url(s, "/payroll/runs"), json={"month": "2026-09"}).json()
    sif = lambda: api.req("GET", OWNER, url(s, f"/payroll/runs/{run['id']}/wps.sif"))  # noqa: E731
    assert sif().status_code == 409                                                   # a draft: post it first
    assert api.req("POST", OWNER, url(s, f"/payroll/runs/{run['id']}/post")).status_code == 200

    missing = sif()
    assert missing.status_code == 422 and "establishment id" in missing.json()["detail"]
    settings = {"wps_employer_id": "1234567890123", "wps_bank_code": "803320101"}
    assert api.req("PUT", OWNER, url(s, "/payroll/settings"), json=settings | {"wps_bank_code": "8033"}).status_code == 422
    assert api.req("PUT", OWNER, url(s, "/payroll/settings"), json=settings).json()["wps_employer_id"] == "1234567890123"
    who = sif()
    assert who.status_code == 422
    assert "Huda Saleh (14-digit person number, 9-digit bank routing code)" in who.json()["detail"]

    for emp, person in ((huda, "10012345678901"), (omar, "10012345678902")):
        r = api.req("PUT", OWNER, url(s, f"/payroll/employees/{emp}"), json={
            "iban": AE_IBAN, "contribution_ids": [], "wps_person_id": person, "wps_routing_code": "803320101",
            "items": [earning("Basic", "10000" if emp == huda else "6000")] + ([earning("Housing", "2500")] if emp == huda else [])})
        assert r.status_code == 200 and r.json()["wps_person_id"] == person
    bad = api.req("PUT", OWNER, url(s, f"/payroll/employees/{huda}"), json={
        "iban": AE_IBAN, "contribution_ids": [], "wps_person_id": "123", "items": [earning("Basic", "10000")]})
    assert bad.status_code == 422

    made = sif()
    assert made.status_code == 200, made.text
    assert re.fullmatch(r'attachment; filename="1234567890123\d{12}\.SIF"', made.headers["content-disposition"])
    rows = made.text.strip().split("\r\n")
    assert rows[0] == f"EDR,10012345678901,803320101,{AE_IBAN},2026-09-01,2026-09-30,30,12500.00,0.00,0"
    assert rows[1] == f"EDR,10012345678902,803320101,{AE_IBAN},2026-09-01,2026-09-30,30,3000.00,0.00,0"   # 15 of 30 days
    scr = rows[2].split(",")
    assert scr[:3] == ["SCR", "1234567890123", "803320101"] and scr[5:] == ["092026", "2", "15500.00", "AED", "Salary 2026-09"]


def test_the_file_by_itself():
    run = NS(currency="SAR", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30))
    with pytest.raises(wps.WpsError, match="dirhams"):
        wps.build(run, [], {}, employer_id="1234567890123", bank_code="803320101", now=datetime.now(timezone.utc))
    run.currency = "AED"
    slip = NS(employee_id="e1", employee_name="Huda", net_minor=1_050_000, iban=AE_IBAN, days_in_period=30,
              unpaid_leave_days="2", lines=[{"name": "Basic", "amount_minor": 1_000_000},
                                            {"name": "Overtime", "amount_minor": 50_000, "minutes": 480}])
    pay = NS(wps_person_id="10012345678901", wps_routing_code="803320101")
    f = wps.build(run, [slip], {"e1": pay}, employer_id="1234567890123", bank_code="803320101",
                  now=datetime(2026, 10, 1, 9, 5, 7, tzinfo=timezone.utc), reference="Salary 2026-09")
    assert f.name == "1234567890123261001090507.SIF"
    assert f.content == (f"EDR,10012345678901,803320101,{AE_IBAN},2026-09-01,2026-09-30,30,10000.00,500.00,2\r\n"
                         "SCR,1234567890123,803320101,2026-10-01,0905,092026,1,10500.00,AED,Salary 2026-09\r\n")
