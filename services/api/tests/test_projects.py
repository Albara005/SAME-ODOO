"""Projects: tasks, hours logged by the team, and invoicing billable hours exactly once."""

from __future__ import annotations

import pytest

OWNER, DEV, ACCT, VIEWER = (f"{n}@projects.example.com" for n in ("owner", "dev", "acct", "viewer"))


@pytest.fixture
def proj(api):
    for e in (OWNER, DEV, ACCT, VIEWER):
        api.signup(e)
    org = api.create_org(OWNER, "projects-co")
    api.add_member(OWNER, org, DEV, "member")
    api.add_member(OWNER, org, ACCT, "accountant")
    api.add_member(OWNER, org, VIEWER, "viewer")
    vat = api.req("POST", OWNER, f"/orgs/{org}/taxes", json={"name": "VAT", "rate": "15"}).json()["id"]
    customer = api.req("POST", OWNER, f"/orgs/{org}/customers", json={"name": "Hala Stores"}).json()["id"]
    p = api.req("POST", OWNER, f"/orgs/{org}/projects", json={
        "name": "Website", "customer_id": customer, "hourly_rate": "250", "tax_id": vat, "budget_hours": "40"})
    assert p.status_code == 201, p.text
    return {"org": org, "project": p.json()["id"], "customer": customer}


def u(s, path=""):
    return f"/orgs/{s['org']}/projects/{s['project']}{path}"


def log(api, s, user, hours, day="2026-03-10", **kw):
    return api.req("POST", user, u(s, "/time"), json={"entry_date": day, "hours": hours, **kw})


def test_hours_are_logged_and_invoiced_once(api, proj):
    s = proj
    assert api.req("POST", DEV, f"/orgs/{s['org']}/projects", json={"name": "x"}).status_code == 403
    task = api.req("POST", OWNER, u(s, "/tasks"), json={"title": "Home page"})
    assert task.status_code == 201, task.text
    task_id = task.json()["id"]

    assert log(api, s, DEV, "3", task_id=task_id, description="Layout").status_code == 201
    assert log(api, s, DEV, "1.5", day="2026-03-11", description="Meeting", billable=False).status_code == 201
    assert log(api, s, OWNER, "2", day="2026-03-12").status_code == 201
    assert log(api, s, VIEWER, "1").status_code == 403                                  # no timesheets.write
    assert log(api, s, DEV, "25").status_code == 422
    assert log(api, s, DEV, "1", day="2999-01-01").status_code == 422                   # not ahead of today
    other = api.req("GET", OWNER, f"/orgs/{s['org']}/members").json()
    dev_id = next(m["user_id"] for m in other if m["email"] == DEV)
    assert log(api, s, DEV, "1", user_id=dev_id).status_code == 201                       # own id is fine
    acct_id = next(m["user_id"] for m in other if m["email"] == ACCT)
    assert log(api, s, DEV, "1", user_id=acct_id).status_code == 403                     # someone else's: managers only

    summary = api.req("GET", ACCT, u(s)).json()
    assert (summary["hours"], summary["billable_hours"], summary["unbilled_hours"]) == ("7.5", "6", "6")
    assert (summary["unbilled_amount"], summary["budget_used_percent"]) == ("1500.00", "18.8")
    tasks = api.req("GET", DEV, u(s, "/tasks")).json()
    assert [(t["title"], t["hours"]) for t in tasks] == [("Home page", "3")]

    # Invoicing needs both managing the project and writing invoices.
    assert api.req("POST", ACCT, u(s, "/invoice"), json={"issue_date": "2026-03-31"}).status_code == 403
    early = api.req("POST", OWNER, u(s, "/invoice"), json={"issue_date": "2026-03-31", "up_to": "2026-03-10"})
    assert early.status_code == 201, early.text
    assert early.json()["hours"] == "4"   # 3 on the task + 1 with no task, both on the 10th
    inv = api.req("GET", OWNER, f"/orgs/{s['org']}/invoices/{early.json()['invoice_id']}").json()
    assert [(l["description"], l["quantity"], l["unit_price"]) for l in inv["lines"]] == [
        ("Website: Home page", "3", "250"), ("Website", "1", "250")]
    assert (inv["subtotal"], inv["tax"], inv["total"]) == ("1000.00", "150.00", "1150.00")
    assert api.req("GET", OWNER, u(s)).json()["unbilled_hours"] == "2"

    # Billed hours can't be removed; deleting the draft frees them again.
    entries = api.req("GET", OWNER, u(s, "/time")).json()
    billed = next(e for e in entries if e["billed"])
    assert api.req("DELETE", DEV, u(s, f"/time/{billed['id']}")).status_code == 409
    assert api.req("DELETE", OWNER, f"/orgs/{s['org']}/invoices/{inv['id']}").status_code == 204
    assert api.req("GET", OWNER, u(s)).json()["unbilled_hours"] == "6"

    rest = api.req("POST", OWNER, u(s, "/invoice"), json={"issue_date": "2026-03-31"})
    assert rest.json()["hours"] == "6"
    assert api.req("POST", OWNER, u(s, "/invoice"), json={"issue_date": "2026-03-31"}).status_code == 422  # nothing left
    # Editing that draft's lines would separate it from the hours behind it.
    assert api.req("PUT", OWNER, f"/orgs/{s['org']}/invoices/{rest.json()['invoice_id']}", json={
        "customer_id": s["customer"], "issue_date": "2026-03-31",
        "lines": [{"description": "x", "quantity": "1", "unit_price": "1"}]}).status_code == 409

    # A cancelled invoice gives its hours back.
    assert api.req("POST", ACCT, f"/orgs/{s['org']}/invoices/{rest.json()['invoice_id']}/post").status_code == 200
    assert api.req("GET", OWNER, u(s)).json()["unbilled_hours"] == "0"
    cancel = api.req("POST", ACCT, f"/orgs/{s['org']}/invoices/{rest.json()['invoice_id']}/cancel",
                     json={"cancel_date": "2026-04-01"})
    assert cancel.status_code == 200, cancel.text
    assert api.req("GET", OWNER, u(s)).json()["unbilled_hours"] == "6"


def test_closed_and_internal_projects(api, proj):
    s = proj
    internal = api.req("POST", OWNER, f"/orgs/{s['org']}/projects", json={"name": "Internal tools"}).json()
    assert internal["unbilled_amount"] is None
    url = f"/orgs/{s['org']}/projects/{internal['id']}"
    assert api.req("POST", DEV, url + "/time", json={"entry_date": "2026-03-10", "hours": "2"}).status_code == 201
    assert api.req("POST", OWNER, url + "/invoice", json={"issue_date": "2026-03-31"}).status_code == 422

    kept = log(api, s, DEV, "2").json()["id"]
    assert api.req("PATCH", OWNER, u(s), json={"status": "closed"}).json()["status"] == "closed"
    assert log(api, s, DEV, "1").status_code == 409
    assert api.req("DELETE", DEV, u(s, f"/time/{kept}")).status_code == 409                # a closed project's hours stay
    assert [p["name"] for p in api.req("GET", DEV, f"/orgs/{s['org']}/projects", params={"status_filter": "active"}).json()] == [
        "Internal tools"]
    assert api.req("GET", VIEWER, f"/orgs/{s['org']}/projects").status_code == 403
    # Project tasks are ordinary tasks: assigned people see them in their own list.
    dev_id = next(m["user_id"] for m in api.req("GET", OWNER, f"/orgs/{s['org']}/members").json() if m["email"] == DEV)
    api.req("POST", OWNER, u(s, "/tasks"), json={"title": "Write copy", "assignee_user_id": dev_id})
    assert "Write copy" in [t["title"] for t in api.req("GET", DEV, f"/orgs/{s['org']}/tasks").json()]
