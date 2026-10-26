"""Work schedules and reduced hours: someone on a schedule has their overtime counted against its working week (and
their leave in its working days); a reduced-hours period such as Ramadan shortens the working day of the people it
applies to; the hourly overtime rate follows the person's usual working day."""

from __future__ import annotations

import pytest

from tests.test_attendance import approve, clock, entry, month, overtime_paid_saudi  # noqa: F401 (clock is a fixture)
from tests.test_hr import EMP, OTHER, OWNER, hr, path  # noqa: F401 (hr is a fixture)

# Saturday to Wednesday, nine hours; Thursday and Friday off (Monday first).
SHIFT_B = [540, 540, 540, 0, 0, 540, 540]
RAMADAN = {"name": "Ramadan 1447", "start_date": "2026-02-18", "end_date": "2026-03-19", "daily_minutes": 360}


@pytest.fixture
def shift(api, hr, clock):
    s = hr
    clock("2026-03-25T12:00:00+00:00")
    r = api.req("POST", OWNER, path(s, "/schedules"), json={"name": "Shift B", "minutes": SHIFT_B})
    assert r.status_code == 201, r.text
    assert (r.json()["weekly_minutes"], r.json()["employees"]) == (2700, 0)
    moved = api.req("PATCH", OWNER, path(s, f"/employees/{s['people'][EMP]}"), json={"schedule_id": r.json()["id"]})
    assert moved.status_code == 200 and moved.json()["schedule_id"] == r.json()["id"]
    return {**s, "shift": r.json()["id"]}


def test_overtime_follows_the_schedule_and_ramadan_hours(api, shift, clock):
    s = shift
    assert api.req("POST", OWNER, path(s, "/reduced-hours"), json=RAMADAN).status_code == 201
    work = [
        (EMP, "2026-03-12T09:00:00+03:00", "2026-03-12T13:00:00+03:00"),   # Thursday: her day off, all overtime (4:00)
        (EMP, "2026-03-14T09:00:00+03:00", "2026-03-14T18:00:00+03:00"),   # Saturday in Ramadan: 9:00 - 6:00 = 3:00
        (EMP, "2026-03-22T08:00:00+03:00", "2026-03-22T18:00:00+03:00"),   # Sunday after Ramadan: 10:00 - 9:00 = 1:00
        (OTHER, "2026-03-09T08:00:00+03:00", "2026-03-09T17:00:00+03:00"),  # Monday, the policy's day, in Ramadan
    ]
    for who, start, end in work:
        assert entry(api, s, who, start, end).status_code == 201
    rows = month(api, s, m="2026-03")
    assert (rows["Huda"]["worked_minutes"], rows["Huda"]["overtime_minutes"]) == (1380, 480)
    assert rows["Omar"]["overtime_minutes"] == 180                      # 9:00 - Ramadan's 6:00
    # Ramadan hours don't apply to everyone.
    not_him = api.req("PATCH", OWNER, path(s, f"/employees/{s['people'][OTHER]}"), json={"reduced_hours": False})
    assert not_him.status_code == 200 and not_him.json()["reduced_hours"] is False
    assert month(api, s, m="2026-03")["Omar"]["overtime_minutes"] == 60    # 9:00 - the policy's 8:00

    # Her own page says what today's working day is.
    clock("2026-03-15T06:00:00+00:00")                                     # Sunday in Ramadan
    me = api.req("GET", EMP, path(s, "/attendance/me")).json()
    assert (me["schedule_name"], me["today_minutes"], me["reduced_hours"]) == ("Shift B", 360, "Ramadan 1447")
    clock("2026-03-26T06:00:00+00:00")                                     # Thursday: off
    me = api.req("GET", EMP, path(s, "/attendance/me")).json()
    assert (me["today_minutes"], me["reduced_hours"]) == (0, None)

    # The hourly rate is her usual nine-hour day: (7,500 + 3,000) / 30 / 9 = 38.888... an hour, for 8 hours.
    overtime_paid_saudi(api, s)
    org = f"/orgs/{s['org']}"
    pay = {"iban": "", "contribution_ids": [], "items": [
        {"name": "Basic salary", "kind": "earning", "amount": "6000", "basic": True},
        {"name": "Housing", "kind": "earning", "amount": "1500"}]}
    assert api.req("PUT", OWNER, f"{org}/payroll/employees/{s['people'][EMP]}", json=pay).status_code == 200
    assert approve(api, s, EMP, 480, m="2026-03").status_code == 200
    run = api.req("POST", OWNER, f"{org}/payroll/runs", json={"month": "2026-03"}).json()
    slip = next(p for p in api.req("GET", OWNER, f"{org}/payroll/runs/{run['id']}").json()["payslips"]
                if p["employee_name"] == "Huda")
    assert next(l["amount"] for l in slip["lines"] if l["overtime_minutes"]) == "311.11"

    # Without the period, Saturday's nine hours are a normal day.
    period = api.req("GET", EMP, path(s, "/reduced-hours"), params={"year": 2026}).json()[0]
    assert api.req("DELETE", OWNER, path(s, f"/reduced-hours/{period['id']}")).status_code == 204
    assert month(api, s, m="2026-03")["Huda"]["overtime_minutes"] == 300
    actions = [a["action"] for a in api.req("GET", OWNER, f"{org}/audit-logs").json()]
    assert {"hr.schedule_added", "hr.reduced_hours_added", "hr.reduced_hours_removed"} <= set(actions)


def test_leave_days_follow_the_persons_schedule(api, shift):
    s = shift
    days = lambda who, **p: api.req("GET", who, path(s, "/leave-days"),  # noqa: E731
                                    params={"start": "2026-03-28", "end": "2026-03-29", **p})
    assert days(EMP).json()["days"] == 2                                   # Saturday and Sunday are her working days
    assert days(OTHER).json()["days"] == 1                                 # Saturday is the weekend for the rest
    assert days(OWNER, employee_id=s["people"][EMP]).json()["days"] == 2
    asked = api.req("POST", EMP, path(s, "/leave-requests"), json={
        "leave_type_id": s["annual"], "start_date": "2026-04-02", "end_date": "2026-04-04"})  # Thursday to Saturday
    assert asked.status_code == 201 and asked.json()["days"] == 1
    off = api.req("POST", EMP, path(s, "/leave-requests"), json={
        "leave_type_id": s["annual"], "start_date": "2026-04-09", "end_date": "2026-04-10"})  # Thursday and Friday
    assert off.status_code == 422 and "days off" in off.json()["detail"]


def test_what_a_schedule_and_a_period_take(api, shift):
    s = shift
    add = lambda body: api.req("POST", OWNER, path(s, "/schedules"), json=body)  # noqa: E731
    assert add({"name": "Short", "minutes": [480] * 6}).status_code == 422
    assert add({"name": "Nothing", "minutes": [0] * 7}).status_code == 422
    assert add({"name": "Tiny", "minutes": [30, 0, 0, 0, 0, 0, 0]}).status_code == 422
    assert add({"name": "Shift B", "minutes": [480] * 7}).status_code == 409
    assert api.req("POST", EMP, path(s, "/schedules"), json={"name": "Mine", "minutes": [480] * 7}).status_code == 403
    assert api.req("PATCH", OWNER, path(s, f"/employees/{s['people'][OTHER]}"),
                   json={"schedule_id": "no-such-schedule"}).status_code == 422
    listed = api.req("GET", OWNER, path(s, "/schedules")).json()
    assert [(x["name"], x["employees"]) for x in listed] == [("Shift B", 1)]
    changed = api.req("PUT", OWNER, path(s, f"/schedules/{s['shift']}"), json={"name": "Shift B", "minutes": [480] * 7})
    assert changed.status_code == 200 and changed.json()["weekly_minutes"] == 3360

    assert api.req("DELETE", OWNER, path(s, f"/schedules/{s['shift']}")).status_code == 409   # Huda works on it
    assert api.req("PATCH", OWNER, path(s, f"/employees/{s['people'][EMP]}"), json={"schedule_id": None}).status_code == 200
    assert api.req("DELETE", OWNER, path(s, f"/schedules/{s['shift']}")).status_code == 204

    period = lambda body, who=OWNER: api.req("POST", who, path(s, "/reduced-hours"), json=RAMADAN | body)  # noqa: E731
    assert period({}).status_code == 201
    assert period({"name": "Again", "start_date": "2026-03-19", "end_date": "2026-03-25"}).status_code == 409
    assert period({"start_date": "2026-03-25", "end_date": "2026-03-20"}).status_code == 422
    assert period({"start_date": "2026-05-01", "end_date": "2026-09-01"}).status_code == 422
    assert period({"daily_minutes": 30}).status_code == 422
    assert period({"start_date": "2027-02-08", "end_date": "2027-03-09"}, who=EMP).status_code == 403
