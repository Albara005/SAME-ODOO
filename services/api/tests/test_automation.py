"""Phase 5 acceptance (automation): reliable workflows with triggers, conditions, actions,
approvals, retries, failure handling, idempotency and an execution record."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from novara.ai.provider import ScriptedProvider, text_reply
from novara.automation import engine as E
from novara.automation.models import OutboxEvent, WorkflowRun
from novara.tenancy import bind_tenant
from novara.worker import run_once

OWNER, ACCT, SALES = "owner@wf.example.com", "acct@wf.example.com", "sales@wf.example.com"


@pytest.fixture
def wf(api, app):
    for e in (OWNER, ACCT, SALES):
        api.signup(e)
    org = api.create_org(OWNER, "wf-co")
    api.add_member(OWNER, org, ACCT, "accountant")
    api.add_member(OWNER, org, SALES, "sales")
    cust = api.req("POST", OWNER, f"/orgs/{org}/customers", json={"name": "Nokhba Co"}).json()["id"]
    app.state.ai_provider = ScriptedProvider()
    ids = {e: api.req("GET", e, "/me").json()["id"] for e in (OWNER, ACCT, SALES)}
    return {"org": org, "customer": cust, "ids": ids, "app": app, "ai": app.state.ai_provider}


def create_wf(api, s, **body):
    r = api.req("POST", OWNER, f"/orgs/{s['org']}/workflows", json={"name": "wf"} | body)
    assert r.status_code == 201, r.text
    return r.json()


def post_invoice(api, s, amount, issue="2026-03-01", due="2026-03-31"):
    inv = api.req("POST", OWNER, f"/orgs/{s['org']}/invoices", json={
        "customer_id": s["customer"], "issue_date": issue, "due_date": due,
        "lines": [{"description": "Work", "quantity": "1", "unit_price": amount}]}).json()
    assert api.req("POST", OWNER, f"/orgs/{s['org']}/invoices/{inv['id']}/post").status_code == 200
    return inv["id"]


def process(api, s):
    r = api.req("POST", OWNER, f"/orgs/{s['org']}/automation/process")
    assert r.status_code == 200, r.text
    return r.json()


def runs(api, s, workflow_id):
    return api.req("GET", OWNER, f"/orgs/{s['org']}/workflows/{workflow_id}/runs").json()


def test_conditions_and_template_rendering():
    p = {"total": "1500.00", "number": "INV-1", "customer": {"name": "نخبة"}}
    assert E.condition_holds({"field": "total", "op": "gte", "value": "1000"}, p)
    assert not E.condition_holds({"field": "total", "op": "lt", "value": 1000}, p)
    assert E.condition_holds({"field": "customer.name", "op": "contains", "value": "نخب"}, p)
    assert E.condition_holds({"field": "number", "op": "in", "value": ["INV-1", "INV-2"]}, p)
    assert not E.condition_holds({"field": "missing", "op": "eq", "value": "x"}, p)
    assert E.condition_holds({"field": "total", "op": "eq", "value": "1500"}, p)  # numeric equality
    assert E.render("Follow up {{number}} for {{customer.name}}{{nope}}", p) == "Follow up INV-1 for نخبة"
    assert E.render("{{__class__}}", p) == ""


def test_invalid_workflows_are_rejected(api, wf):
    org = wf["org"]
    bad = [
        {"trigger": "invoice.exploded", "actions": [{"type": "notify", "title": "x"}]},
        {"trigger": "invoice.posted", "conditions": [{"field": "total", "op": "~="}], "actions": [{"type": "notify", "title": "x"}]},
        {"trigger": "invoice.posted", "actions": [{"type": "shell", "cmd": "rm -rf /"}]},
        {"trigger": "invoice.posted", "actions": [{"type": "notify"}]},
        {"trigger": "invoice.posted", "actions": [{"type": "require_approval", "permission": "god.mode"}]},
        {"trigger": "agent.run_failed", "actions": [{"type": "run_agent", "agent_id": "x", "input": "y"}]},
        {"trigger": "invoice.posted", "actions": [{"type": "run_agent", "agent_id": "nope", "input": "y"}]},
    ]
    for body in bad:
        r = api.req("POST", OWNER, f"/orgs/{org}/workflows", json={"name": "bad"} | body)
        assert r.status_code == 422, body
    assert api.req("POST", SALES, f"/orgs/{org}/workflows", json={"name": "x"}).status_code == 403


def test_trigger_condition_actions_run_exactly_once(api, wf):
    w = create_wf(api, wf, trigger="invoice.posted",
                  conditions=[{"field": "total", "op": "gte", "value": "1000"}],
                  actions=[{"type": "create_task", "title": "Check large invoice {{number}}", "assignee": "role:accountant",
                            "due_in_days": 2},
                           {"type": "notify", "to": "owner", "title": "Large invoice {{number}}: {{total}}"}])
    post_invoice(api, wf, "2500")
    post_invoice(api, wf, "400")
    stats = process(api, wf)
    assert stats["runs_created"] == 2
    statuses = sorted(r["status"] for r in runs(api, wf, w["id"]))
    assert statuses == ["completed", "skipped"]

    tasks = api.req("GET", ACCT, f"/orgs/{wf['org']}/tasks").json()
    assert [t["title"] for t in tasks] == ["Check large invoice INV-2026-00001"]
    assert tasks[0]["related_type"] == "invoice" and tasks[0]["due_date"] == str(date.today() + timedelta(days=2))
    notes = api.req("GET", OWNER, f"/orgs/{wf['org']}/notifications").json()
    assert [n["title"] for n in notes] == ["Large invoice INV-2026-00001: 2500.00"]

    # Processing again (or a worker crash and restart) never repeats work.
    assert process(api, wf)["runs_created"] == 0
    assert len(api.req("GET", ACCT, f"/orgs/{wf['org']}/tasks").json()) == 1
    assert len(api.req("GET", OWNER, f"/orgs/{wf['org']}/notifications").json()) == 1


def test_events_are_only_recorded_when_a_workflow_listens(api, wf):
    post_invoice(api, wf, "100")
    with wf["app"].state.sessionmaker() as db:
        bind_tenant(db, wf["org"])
        assert db.scalars(select(OutboxEvent)).all() == []


def test_edits_do_not_change_runs_already_created(api, wf):
    w = create_wf(api, wf, trigger="invoice.posted", actions=[{"type": "notify", "title": "old"}])
    post_invoice(api, wf, "100")
    with wf["app"].state.sessionmaker() as db:
        bind_tenant(db, wf["org"])
        E.match_events(db)
        db.commit()
    api.req("PATCH", OWNER, f"/orgs/{wf['org']}/workflows/{w['id']}", json={"actions": [{"type": "notify", "title": "new"}]})
    process(api, wf)
    assert [n["title"] for n in api.req("GET", OWNER, f"/orgs/{wf['org']}/notifications").json()] == ["old"]


def test_approval_step_pauses_until_an_allowed_user_approves(api, wf):
    w = create_wf(api, wf, trigger="invoice.posted", actions=[
        {"type": "require_approval", "permission": "payments.write", "message": "Approve follow-up for {{number}}?"},
        {"type": "notify", "to": "actor", "title": "Follow-up approved for {{number}}"}])
    post_invoice(api, wf, "100")
    process(api, wf)
    run = runs(api, wf, w["id"])[0]
    assert run["status"] == "waiting_approval"
    assert run["steps"][0]["result"]["message"] == "Approve follow-up for INV-2026-00001?"
    assert [r["id"] for r in api.req("GET", ACCT, f"/orgs/{wf['org']}/approvals").json()] == [run["id"]]
    assert api.req("GET", SALES, f"/orgs/{wf['org']}/approvals").json() == []
    assert api.req("POST", SALES, f"/orgs/{wf['org']}/workflow-runs/{run['id']}/approve").status_code == 403
    done = api.req("POST", ACCT, f"/orgs/{wf['org']}/workflow-runs/{run['id']}/approve").json()
    assert done["status"] == "completed"
    assert done["steps"][0]["result"]["decided_by"] == wf["ids"][ACCT]
    assert [n["title"] for n in api.req("GET", OWNER, f"/orgs/{wf['org']}/notifications").json()] == [
        "Follow-up approved for INV-2026-00001"]


def test_rejected_approval_stops_the_run(api, wf):
    w = create_wf(api, wf, trigger="invoice.posted", actions=[
        {"type": "require_approval", "permission": "invoices.post"}, {"type": "notify", "title": "x"}])
    post_invoice(api, wf, "100")
    process(api, wf)
    run = runs(api, wf, w["id"])[0]
    assert api.req("POST", OWNER, f"/orgs/{wf['org']}/workflow-runs/{run['id']}/reject").json()["status"] == "rejected"
    assert api.req("GET", OWNER, f"/orgs/{wf['org']}/notifications").json() == []


def publish_agent(api, s):
    agent = api.req("POST", OWNER, f"/orgs/{s['org']}/agents", json={"name": "Welcomer"}).json()["id"]
    base = f"/orgs/{s['org']}/agents/{agent}"
    api.req("PUT", OWNER, f"{base}/draft", json={"instructions": "Write a welcome note.", "tools": ["search_customers"]})
    api.req("POST", OWNER, f"{base}/tests", json={"name": "t", "input": "hi", "reply_contains": ["welcome"]})
    s["ai"].script = [text_reply("welcome!")]
    api.req("POST", OWNER, f"{base}/versions/1/test-runs")
    assert api.req("POST", OWNER, f"{base}/versions/1/publish").status_code == 200
    return agent


def test_run_agent_action_runs_as_the_workflow_owner(api, wf):
    agent = publish_agent(api, wf)
    w = create_wf(api, wf, trigger="customer.created",
                  actions=[{"type": "run_agent", "agent_id": agent, "input": "Welcome {{name}}"}])
    api.req("POST", SALES, f"/orgs/{wf['org']}/customers", json={"name": "New Co"})
    wf["ai"].script = [text_reply("Welcome aboard, New Co!")]
    process(api, wf)
    run = runs(api, wf, w["id"])[0]
    assert run["status"] == "completed"
    assert run["steps"][0]["result"]["reply"] == "Welcome aboard, New Co!"
    agent_runs = api.req("GET", OWNER, f"/orgs/{wf['org']}/agents/{agent}/runs").json()
    assert agent_runs[0]["trigger"] == "workflow" and agent_runs[0]["input"] == "Welcome New Co"


def test_failures_retry_with_backoff_then_fail_and_notify_the_owner(api, wf):
    agent = publish_agent(api, wf)
    w = create_wf(api, wf, trigger="customer.created",
                  actions=[{"type": "notify", "title": "first step"},
                           {"type": "run_agent", "agent_id": agent, "input": "x"}])
    api.req("PATCH", OWNER, f"/orgs/{wf['org']}/agents/{agent}", json={"enabled": False})   # make the action fail
    api.req("POST", OWNER, f"/orgs/{wf['org']}/customers", json={"name": "C"})
    app = wf["app"]
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    for minutes, expected in ((0, "retrying"), (0, "retrying"), (1, "retrying"), (3, "failed")):
        with app.state.sessionmaker() as db:
            bind_tenant(db, wf["org"])
            E.process(db, provider=wf["ai"], now=t0 + timedelta(minutes=minutes))
            run = db.scalars(select(WorkflowRun)).one()
            assert run.status == expected, (minutes, run.status, run.attempts)
    assert run.attempts == 3 and "switched off" in run.error
    titles = [n["title"] for n in api.req("GET", OWNER, f"/orgs/{wf['org']}/notifications").json()]
    # The first step's notification was rolled back with each failed attempt: no duplicates, no half runs.
    assert titles == ["Workflow 'wf' failed"]


def test_overdue_scan_emits_once_per_invoice(api, wf):
    w = create_wf(api, wf, trigger="invoice.overdue",
                  conditions=[{"field": "days_overdue", "op": "gt", "value": 7}],
                  actions=[{"type": "create_task", "title": "Chase {{customer_name}} for {{number}} ({{amount_due}})",
                            "assignee": "role:accountant"}])
    post_invoice(api, wf, "300", issue="2026-01-01", due="2026-01-15")
    post_invoice(api, wf, "200", issue=str(date.today()), due=str(date.today() + timedelta(days=30)))
    process(api, wf)
    process(api, wf)
    tasks = api.req("GET", ACCT, f"/orgs/{wf['org']}/tasks").json()
    assert [t["title"] for t in tasks] == ["Chase Nokhba Co for INV-2026-00001 (300.00)"]
    assert len(runs(api, wf, w["id"])) == 1


def test_worker_processes_each_organization_in_isolation(api, wf):
    create_wf(api, wf, trigger="customer.created", actions=[{"type": "notify", "title": "new customer {{name}}"}])
    api.signup("other@wf.example.com")
    other = api.create_org("other@wf.example.com", "wf-other")
    api.req("POST", "other@wf.example.com", f"/orgs/{other}/workflows",
            json={"name": "o", "trigger": "customer.created", "actions": [{"type": "notify", "title": "other {{name}}"}]})
    api.req("POST", OWNER, f"/orgs/{wf['org']}/customers", json={"name": "A1"})
    api.req("POST", "other@wf.example.com", f"/orgs/{other}/customers", json={"name": "B1"})
    results = run_once(wf["app"].state.sessionmaker, wf["ai"])
    assert set(results) == {wf["org"], other}
    assert [n["title"] for n in api.req("GET", OWNER, f"/orgs/{wf['org']}/notifications").json()] == ["new customer A1"]
    assert [n["title"] for n in api.req("GET", "other@wf.example.com", f"/orgs/{other}/notifications").json()] == [
        "other B1"]


def test_tasks_visibility_and_completion(api, wf):
    create_wf(api, wf, trigger="customer.created", actions=[{"type": "create_task", "title": "Call {{name}}",
                                                             "assignee": "actor"}])
    api.req("POST", SALES, f"/orgs/{wf['org']}/customers", json={"name": "Lead Co"})
    process(api, wf)
    mine = api.req("GET", SALES, f"/orgs/{wf['org']}/tasks").json()
    assert [t["title"] for t in mine] == ["Call Lead Co"]
    assert api.req("GET", ACCT, f"/orgs/{wf['org']}/tasks").json() == []
    assert len(api.req("GET", OWNER, f"/orgs/{wf['org']}/tasks", params={"all_tasks": True}).json()) == 1
    assert api.req("PATCH", ACCT, f"/orgs/{wf['org']}/tasks/{mine[0]['id']}", json={"status": "done"}).status_code == 404
    assert api.req("PATCH", SALES, f"/orgs/{wf['org']}/tasks/{mine[0]['id']}", json={"status": "done"}).json()["status"] == "done"
