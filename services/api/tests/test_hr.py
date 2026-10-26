"""People: employee records, departments, and leave counted in working days and decided by someone else."""

from __future__ import annotations

import pytest

OWNER, MGR, EMP, OTHER, VIEWER = (f"{n}@hr.example.com" for n in ("owner", "mgr", "emp", "other", "viewer"))


@pytest.fixture
def hr(api):
    for e in (OWNER, MGR, EMP, OTHER, VIEWER):
        api.signup(e)
    org = api.create_org(OWNER, "hr-co")
    for e in (MGR, EMP, OTHER):
        api.add_member(OWNER, org, e, "member")
    api.add_member(OWNER, org, VIEWER, "viewer")
    members = {m["email"]: m["user_id"] for m in api.req("GET", OWNER, f"/orgs/{org}/members").json()}

    def post(path, body, who=OWNER):
        r = api.req("POST", who, f"/orgs/{org}/hr{path}", json=body)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    design = post("/departments", {"name": "Design", "manager_user_id": members[MGR]})
    people = {
        EMP: post("/employees", {"full_name": "Huda", "job_title": "Designer", "department_id": design,
                                 "user_id": members[EMP], "hire_date": "2025-01-01"}),
        MGR: post("/employees", {"full_name": "Majed", "department_id": design, "user_id": members[MGR]}),
        OTHER: post("/employees", {"full_name": "Omar", "user_id": members[OTHER]}),
    }
    annual = post("/leave-types", {"name": "Annual", "annual_days": 21})
    unpaid = post("/leave-types", {"name": "Unpaid", "paid": False})
    post("/holidays", {"day": "2027-03-09", "name": "Founding day"})
    return {"org": org, "members": members, "people": people, "design": design, "annual": annual, "unpaid": unpaid}


def path(s, p=""):
    return f"/orgs/{s['org']}/hr{p}"


def ask(api, s, who, start, end, kind="annual", **kw):
    return api.req("POST", who, path(s, "/leave-requests"),
                   json={"leave_type_id": s[kind], "start_date": start, "end_date": end, **kw})


def remaining(api, s, who, kind="Annual"):
    bal = api.req("GET", who, path(s, "/me"), params={"year": 2027}).json()["balances"]
    return next(b for b in bal if b["leave_type"] == kind)


def test_leave_is_requested_and_decided_by_someone_else(api, hr):
    s = hr
    # Sunday 7 to Saturday 13 March 2027: Friday and Saturday are the weekend, Tuesday 9th is a holiday.
    assert api.req("GET", EMP, path(s, "/leave-days"), params={"start": "2027-03-07", "end": "2027-03-13"}).json() == {
        "days": 4}
    r = ask(api, s, EMP, "2027-03-07", "2027-03-13", reason="Family trip")
    assert r.status_code == 201, r.text
    req = r.json()
    assert (req["days"], req["status"], req["mine"], req["can_decide"]) == (4, "pending", True, False)
    note = api.req("GET", MGR, f"/orgs/{s['org']}/notifications").json()
    assert any("Huda" in n["title"] for n in note)                     # the department's manager hears of it

    assert ask(api, s, EMP, "2027-03-10", "2027-03-11").status_code == 422          # overlaps
    assert ask(api, s, EMP, "2027-12-30", "2028-01-02").status_code == 422          # crosses the year
    assert ask(api, s, EMP, "2027-03-12", "2027-03-13", kind="unpaid").status_code == 422  # only weekend days
    over = ask(api, s, EMP, "2027-04-04", "2027-05-06")
    assert over.status_code == 422 and "17 day" in over.json()["detail"]             # 21 - 4 held by the request
    assert remaining(api, s, EMP)["remaining"] == 17

    # Only HR or the department's manager decides; never the person themselves.
    assert api.req("POST", EMP, path(s, f"/leave-requests/{req['id']}/approve"), json={}).status_code == 403
    assert api.req("POST", OTHER, path(s, f"/leave-requests/{req['id']}/approve"), json={}).status_code == 403
    assert api.req("GET", OTHER, path(s, "/leave-requests")).json() == []
    seen = api.req("GET", MGR, path(s, "/leave-requests")).json()
    assert [(x["employee_name"], x["can_decide"]) for x in seen] == [("Huda", True)]
    ok = api.req("POST", MGR, path(s, f"/leave-requests/{req['id']}/approve"), json={"note": "Enjoy"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert api.req("POST", MGR, path(s, f"/leave-requests/{req['id']}/reject"), json={}).status_code == 409
    assert any(n["title"] == "وُوفق على إجازتك" for n in api.req("GET", EMP, f"/orgs/{s['org']}/notifications").json())
    assert (remaining(api, s, EMP)["approved"], remaining(api, s, EMP)["remaining"]) == (4, 17)

    # Everyone sees who is away, but not the kind of leave or the reason.
    away = api.req("GET", OTHER, path(s, "/away"), params={"start": "2027-03-01", "end": "2027-03-31"}).json()
    assert away == [{"employee_id": s["people"][EMP], "employee_name": "Huda", "department_name": "Design",
                     "start_date": "2027-03-07", "end_date": "2027-03-13"}]

    # The department's manager can't decide their own leave: it goes to HR.
    own = ask(api, s, MGR, "2027-06-06", "2027-06-07").json()
    assert api.req("POST", MGR, path(s, f"/leave-requests/{own['id']}/approve"), json={}).status_code == 403
    assert any("Majed" in n["title"] for n in api.req("GET", OWNER, f"/orgs/{s['org']}/notifications").json())
    assert api.req("POST", OWNER, path(s, f"/leave-requests/{own['id']}/reject"),
                   json={"note": "Launch week"}).json()["status"] == "rejected"

    # Approved leave that hasn't started can be given back.
    assert api.req("POST", EMP, path(s, f"/leave-requests/{req['id']}/cancel")).json()["status"] == "cancelled"
    assert remaining(api, s, EMP)["remaining"] == 21
    assert api.req("POST", OTHER, path(s, f"/leave-requests/{req['id']}/cancel")).status_code == 404

    # A different weekend changes how new requests are counted (Saturday and Sunday off).
    assert api.req("PUT", OWNER, path(s, "/policy"), json={"weekend": [6, 5]}).json()["weekend"] == [5, 6]
    assert api.req("GET", EMP, path(s, "/leave-days"), params={"start": "2027-03-07", "end": "2027-03-13"}).json() == {
        "days": 4}   # Monday, Wednesday, Thursday and Friday


def test_people_records(api, hr):
    s = hr
    assert api.req("GET", VIEWER, path(s, "/employees")).status_code == 403
    assert api.req("GET", VIEWER, path(s, "/me")).status_code == 403
    assert api.req("POST", EMP, path(s, "/employees"), json={"full_name": "x"}).status_code == 403
    staff = api.req("GET", OTHER, path(s, "/employees")).json()
    assert [(p["full_name"], p["department_name"]) for p in staff] == [("Huda", "Design"), ("Majed", "Design"),
                                                                        ("Omar", None)]
    assert api.req("POST", OWNER, path(s, "/employees"),
                   json={"full_name": "Twin", "user_id": s["members"][EMP]}).status_code == 409   # already linked
    assert api.req("POST", OWNER, path(s, "/departments"), json={"name": "Design"}).status_code == 409
    depts = api.req("GET", OTHER, path(s, "/departments")).json()
    assert [(d["name"], d["employees"]) for d in depts] == [("Design", 2)]

    # Balances: the person, their department's manager, or HR.
    emp = s["people"][EMP]
    assert api.req("GET", OTHER, path(s, f"/employees/{emp}/balances")).status_code == 403
    assert api.req("GET", MGR, path(s, f"/employees/{emp}/balances")).status_code == 200

    # HR files leave for someone; leave before the hire date isn't possible.
    assert ask(api, s, OWNER, "2024-06-02", "2024-06-03", employee_id=emp).status_code == 422
    assert ask(api, s, OTHER, "2027-06-06", "2027-06-07", employee_id=emp).status_code == 403
    later = ask(api, s, OWNER, "2027-08-01", "2027-08-02", employee_id=emp)
    assert later.status_code == 201, later.text

    # Leaving: leave after the last day is withdrawn and can't be asked for; the notice period still counts.
    leaving = api.req("PATCH", OWNER, path(s, f"/employees/{emp}"), json={"end_date": "2027-07-31", "full_name": None})
    assert (leaving.json()["status"], leaving.json()["full_name"]) == ("active", "Huda")    # still here until then
    reqs = api.req("GET", OWNER, path(s, "/leave-requests")).json()
    assert [r["status"] for r in reqs if r["id"] == later.json()["id"]] == ["cancelled"]
    assert ask(api, s, EMP, "2027-08-08", "2027-08-09").status_code == 422
    assert ask(api, s, EMP, "2027-05-02", "2027-05-03").status_code == 201
    # Once the last day has passed, the record stays among former employees.
    old = api.req("POST", OWNER, path(s, "/employees"), json={"full_name": "Former", "hire_date": "2020-01-01"}).json()
    assert api.req("PATCH", OWNER, path(s, f"/employees/{old['id']}"), json={"end_date": "2021-06-30"}).json()["status"] == "left"
    assert [p["full_name"] for p in api.req("GET", OWNER, path(s, "/employees"), params={"status_filter": "left"}).json()] == [
        "Former"]
    assert "Former" not in [p["full_name"] for p in api.req("GET", OWNER, path(s, "/employees")).json()]

    # A member without an employee record is told to ask HR.
    api.req("PATCH", OWNER, path(s, f"/employees/{s['people'][OTHER]}"), json={"user_id": None})
    assert ask(api, s, OTHER, "2027-05-02", "2027-05-03").status_code == 409
    assert api.req("GET", OTHER, path(s, "/me")).json()["employee"] is None
