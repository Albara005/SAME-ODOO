"""Attendance and overtime: checking in and out (counted in the organization's time zone), HR's corrections, overtime
beyond the working day or on days off, approved by someone else and paid with the month's pay run."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from novara.modules.hr import attendance
from novara.modules.payroll import service as payroll
from novara.modules.payroll.models import PayItem
from tests.test_hr import EMP, MGR, OTHER, OWNER, VIEWER, hr, path  # noqa: F401 (hr is a fixture)


@pytest.fixture
def clock(monkeypatch):
    state = {"now": datetime(2026, 4, 5, 5, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(attendance, "now", lambda: state["now"])

    def at(iso: str):
        state["now"] = datetime.fromisoformat(iso)
    return at


def entry(api, s, who, check_in, check_out, user=OWNER, **kw):
    return api.req("POST", user, path(s, "/attendance"),
                   json={"employee_id": s["people"][who], "check_in": check_in, "check_out": check_out, **kw})


def month(api, s, who=OWNER, m="2026-04"):
    r = api.req("GET", who, path(s, "/overtime"), params={"month": m})
    assert r.status_code == 200, r.text
    return {row["employee_name"]: row for row in r.json()}


def approve(api, s, who, minutes, user=MGR, m="2026-04", note=""):
    return api.req("PUT", user, path(s, f"/overtime/{s['people'][who]}/{m}"), json={"minutes": minutes, "note": note})


def overtime_paid_saudi(api, s):
    r = api.req("PUT", OWNER, f"/orgs/{s['org']}/payroll/settings", json={"overtime_rule": "saudi"})
    assert r.status_code == 200 and r.json()["overtime_rule"] == "saudi", r.text


def test_attendance_counts_overtime_and_someone_else_approves_it(api, hr, clock):
    s = hr
    # Sunday 5 April 2026, 08:00 in Riyadh: in, and out at 18:30 (two and a half hours past the working day).
    first = api.req("POST", EMP, path(s, "/attendance/check-in"), json={})
    assert first.status_code == 201, first.text
    assert (first.json()["day"], first.json()["check_out"], first.json()["source"]) == ("2026-04-05", None, "self")
    assert api.req("POST", EMP, path(s, "/attendance/check-in"), json={}).status_code == 409
    assert month(api, s)["Huda"]["checked_in"] is True
    clock("2026-04-05T15:30:00+00:00")
    out = api.req("POST", EMP, path(s, "/attendance/check-out"))
    assert (out.status_code, out.json()["minutes"]) == (200, 630)
    assert api.req("POST", EMP, path(s, "/attendance/check-out")).status_code == 409

    # 01:00 on Wednesday 8 April in Riyadh is still the 7th in UTC: it counts on the 8th.
    clock("2026-04-07T22:00:00+00:00")
    assert api.req("POST", EMP, path(s, "/attendance/check-in"), json={}).json()["day"] == "2026-04-08"
    clock("2026-04-08T06:00:00+00:00")
    assert api.req("POST", EMP, path(s, "/attendance/check-out")).json()["minutes"] == 480

    # HR puts in work on Friday (the weekend) and on a public holiday: all of it is overtime.
    clock("2026-04-20T12:00:00+00:00")
    assert api.req("POST", OWNER, path(s, "/holidays"), json={"day": "2026-04-14", "name": "Holiday"}).status_code == 201
    friday = entry(api, s, EMP, "2026-04-10T09:00:00+03:00", "2026-04-10T13:00:00+03:00", note="Stock count")
    assert friday.status_code == 201 and (friday.json()["source"], friday.json()["minutes"]) == ("hr", 240)
    assert entry(api, s, EMP, "2026-04-14T10:00:00+03:00", "2026-04-14T12:00:00+03:00").status_code == 201
    assert entry(api, s, EMP, "2026-04-05T12:00:00+03:00", "2026-04-05T13:00:00+03:00").status_code == 422  # overlaps
    assert entry(api, s, EMP, "2026-04-21T09:00:00+03:00", "2026-04-21T12:00:00+03:00").status_code == 422  # future
    assert entry(api, s, EMP, "2026-04-12T12:00:00+03:00", "2026-04-12T09:00:00+03:00").status_code == 422
    assert entry(api, s, EMP, "2026-04-12T08:00:00+03:00", "2026-04-13T09:00:00+03:00").status_code == 422  # > a day
    assert entry(api, s, EMP, "2026-04-12T08:00:00+03:00", "2026-04-12T09:00:00+03:00", user=EMP).status_code == 403

    row = month(api, s)["Huda"]
    assert (row["days_present"], row["worked_minutes"], row["overtime_minutes"]) == (4, 1470, 510)  # 150 + 240 + 120
    assert (row["approved_minutes"], row["checked_in"], row["can_approve"]) == (None, False, True)
    listed = api.req("GET", MGR, path(s, "/attendance"), params={"employee_id": s["people"][EMP], "month": "2026-04"})
    assert [e["day"] for e in listed.json()] == ["2026-04-05", "2026-04-08", "2026-04-10", "2026-04-14"]
    assert api.req("GET", OTHER, path(s, "/attendance"),
                   params={"employee_id": s["people"][EMP], "month": "2026-04"}).status_code == 404
    assert api.req("GET", OTHER, path(s, "/overtime"), params={"month": "2026-04"}).status_code == 403
    assert list(month(api, s, MGR)) == ["Huda", "Majed"]            # the manager's department only

    # Approval: once payroll says how overtime is paid; by HR or the manager, never the person; up to what was worked.
    assert approve(api, s, EMP, 480).status_code == 422
    overtime_paid_saudi(api, s)
    assert approve(api, s, EMP, 540).status_code == 422             # more than the 8:30 recorded
    assert approve(api, s, EMP, 480, user=EMP).status_code == 403
    assert approve(api, s, MGR, 60, user=MGR).status_code == 403
    assert approve(api, s, EMP, 480, user=OTHER).status_code == 403
    ok = approve(api, s, EMP, 480, note="The stock count only")
    assert ok.status_code == 200 and (ok.json()["approved_minutes"], ok.json()["approval_note"]) == (480, "The stock count only")
    assert any("2026-04" in n["title"] for n in api.req("GET", EMP, f"/orgs/{s['org']}/notifications").json())
    mine = api.req("GET", EMP, path(s, "/attendance/me"), params={"month": "2026-04"}).json()
    assert (mine["worked_minutes"], mine["overtime_minutes"], mine["approved_minutes"], mine["paid"]) == (1470, 510, 480, False)
    assert len(mine["entries"]) == 4 and mine["open"] is None


def test_approved_overtime_is_paid_with_the_months_run_and_then_stands(api, hr, clock):
    s = hr
    overtime_paid_saudi(api, s)
    clock("2026-04-20T12:00:00+00:00")
    work = entry(api, s, EMP, "2026-04-10T09:00:00+03:00", "2026-04-10T17:00:00+03:00").json()   # 8 hours on Friday
    org = f"/orgs/{s['org']}"
    pay = {"iban": "", "contribution_ids": [], "items": [
        {"name": "Basic salary", "kind": "earning", "amount": "6000", "basic": True, "end_of_service": True},
        {"name": "Housing", "kind": "earning", "amount": "1500"}]}
    put = api.req("PUT", OWNER, f"{org}/payroll/employees/{s['people'][EMP]}", json=pay)
    assert put.status_code == 200 and [i["basic"] for i in put.json()["items"]] == [True, False]
    assert approve(api, s, EMP, 480).status_code == 200

    run = api.req("POST", OWNER, f"{org}/payroll/runs", json={"month": "2026-04"}).json()
    detail = api.req("GET", OWNER, f"{org}/payroll/runs/{run['id']}").json()
    slip = next(p for p in detail["payslips"] if p["employee_name"] == "Huda")
    ot = next(l for l in slip["lines"] if l["overtime_minutes"])
    # (7,500 + 3,000) / 30 / 8 hours = 43.75 an hour (the hourly wage plus half the hourly basic), for 8 hours.
    assert (ot["name"], ot["kind"], ot["amount"], ot["overtime_minutes"]) == ("Overtime", "earning", "350.00", 480)
    assert slip["gross"] == "7850.00"

    assert api.req("POST", OWNER, f"{org}/payroll/runs/{run['id']}/post").status_code == 200
    assert approve(api, s, EMP, 240).status_code == 409             # the run is posted: the month stands
    assert api.req("DELETE", MGR, path(s, f"/overtime/{s['people'][EMP]}/2026-04")).status_code == 409
    assert api.req("PATCH", OWNER, path(s, f"/attendance/{work['id']}"),
                   json={"check_out": "2026-04-10T15:00:00+03:00"}).status_code == 409
    assert api.req("DELETE", OWNER, path(s, f"/attendance/{work['id']}")).status_code == 409
    row = month(api, s)["Huda"]
    assert (row["paid"], row["can_approve"]) == (True, False)
    assert api.req("GET", EMP, path(s, "/attendance/me"), params={"month": "2026-04"}).json()["entries"][0]["locked"] is True
    # Someone without approved overtime that month can still have their hours corrected.
    other = entry(api, s, OTHER, "2026-04-12T09:00:00+03:00", "2026-04-12T17:00:00+03:00")
    assert other.status_code == 201

    cancel = api.req("POST", OWNER, f"{org}/payroll/runs/{run['id']}/cancel", json={"cancel_date": "2026-05-01"})
    assert cancel.status_code == 200
    assert api.req("DELETE", MGR, path(s, f"/overtime/{s['people'][EMP]}/2026-04")).status_code == 204
    fixed = api.req("PATCH", OWNER, path(s, f"/attendance/{work['id']}"), json={"check_out": "2026-04-10T15:00:00+03:00"})
    assert fixed.status_code == 200 and fixed.json()["minutes"] == 360


def test_forgotten_check_outs_and_who_can_check_in(api, hr, clock):
    s = hr
    assert api.req("POST", VIEWER, path(s, "/attendance/check-in"), json={}).status_code == 403
    api.signup("nobody@hr.example.com")
    api.add_member(OWNER, s["org"], "nobody@hr.example.com", "member")
    unlinked = api.req("POST", "nobody@hr.example.com", path(s, "/attendance/check-in"), json={})
    assert unlinked.status_code == 409 and "linked" in unlinked.json()["detail"]
    assert api.req("GET", "nobody@hr.example.com", path(s, "/attendance/me")).json()["employee_id"] is None

    first = api.req("POST", EMP, path(s, "/attendance/check-in"), json={"note": "Site visit"}).json()
    clock("2026-04-06T07:00:00+00:00")                               # 26 hours later
    assert "more than a day" in api.req("POST", EMP, path(s, "/attendance/check-out")).json()["detail"]
    assert "never checked out" in api.req("POST", EMP, path(s, "/attendance/check-in"), json={}).json()["detail"]
    me = api.req("GET", EMP, path(s, "/attendance/me"), params={"month": "2026-04"}).json()
    assert me["open"]["id"] == first["id"] and me["open"]["note"] == "Site visit"
    # HR puts in when they left; the person checks in again.
    fixed = api.req("PATCH", OWNER, path(s, f"/attendance/{first['id']}"), json={"check_out": "2026-04-05T16:00:00+03:00"})
    assert fixed.status_code == 200 and fixed.json()["minutes"] == 480
    assert api.req("PATCH", OWNER, path(s, f"/attendance/{first['id']}"), json={"check_out": None}).status_code == 422
    assert api.req("POST", EMP, path(s, "/attendance/check-in"), json={}).status_code == 201

    # The working day and its time zone are HR's policy.
    put = lambda body: api.req("PUT", OWNER, path(s, "/policy"), json=body)  # noqa: E731
    assert put({"timezone": "Mars/Olympus"}).status_code == 422
    assert put({"daily_minutes": 30}).status_code == 422
    changed = put({"timezone": "Asia/Dubai", "daily_minutes": 450}).json()
    assert (changed["timezone"], changed["daily_minutes"], changed["weekend"]) == ("Asia/Dubai", 450, [4, 5])
    assert put({"daily_minutes": 450}).status_code == 200
    assert api.req("PUT", EMP, path(s, "/policy"), json={"daily_minutes": 420}).status_code == 403
    actions = [a["action"] for a in api.req("GET", OWNER, f"/orgs/{s['org']}/audit-logs").json()]
    assert actions.count("hr.working_day_changed") == 1 and "attendance.changed" in actions


def _items(*specs):
    return [PayItem(name=n, kind="earning", amount_minor=a, basic=b) for n, a, b in specs]


def test_the_overtime_rate():
    items = _items(("Basic", 600000, True), ("Housing", 150000, False))
    saudi = payroll.OvertimeRule("saudi", Decimal("1.5"), 480)
    assert payroll.overtime_pay(saudi, items, 480, "Huda") == 35000                 # 43.75 an hour
    assert payroll.overtime_pay(saudi, items, 1, "Huda") == 73                      # 0.729..., rounded once
    multiplier = payroll.OvertimeRule("multiplier", Decimal("1.25"), 480)
    assert payroll.overtime_pay(multiplier, items, 480, "Huda") == 25000            # 25 x 1.25 an hour
    assert payroll.overtime_pay(payroll.OvertimeRule("saudi", Decimal("1.5"), 420), items, 420, "Huda") == 35000
    with pytest.raises(payroll.PayrollError, match="basic salary"):
        payroll.overtime_pay(saudi, _items(("Housing", 150000, False)), 60, "Huda")
    with pytest.raises(payroll.PayrollError, match="don't say how"):
        payroll.overtime_pay(payroll.OvertimeRule("none", Decimal("1.5"), 480), items, 60, "Huda")
