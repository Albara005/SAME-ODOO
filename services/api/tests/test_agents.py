"""Phase 5 acceptance (agents): a user sets up an agent, tests it, and publishes it with
clear permissions and limits."""

from __future__ import annotations

import pytest

from novara.ai.provider import ModelReply, ScriptedProvider, Usage, text_reply, tool_reply

OWNER, SALES = "owner@agents.example.com", "sales@agents.example.com"


@pytest.fixture
def ag(api, app):
    api.signup(OWNER)
    api.signup(SALES)
    org = api.create_org(OWNER, "agents-co")
    api.add_member(OWNER, org, SALES, "sales")
    cust = api.req("POST", OWNER, f"/orgs/{org}/customers", json={"name": "Nokhba Co"}).json()["id"]
    provider = ScriptedProvider()
    app.state.ai_provider = provider
    agent = api.req("POST", OWNER, f"/orgs/{org}/agents",
                    json={"name": "مساعد المبيعات", "description": "Follow-ups"}).json()
    return {"org": org, "agent": agent["id"], "customer": cust, "ai": provider}


def a_url(s, rest=""):
    return f"/orgs/{s['org']}/agents/{s['agent']}{rest}"


def configure(api, s, **overrides):
    body = {"instructions": "Summarize overdue invoices and draft follow-ups.",
            "tools": ["search_customers", "list_invoices", "trial_balance", "create_customer"],
            "route": "assistant", "max_steps": 4, "max_cost_usd": "0.10"} | overrides
    return api.req("PUT", OWNER, a_url(s, "/draft"), json=body)


def add_test(api, s, **overrides):
    body = {"name": "uses invoices", "input": "Any overdue invoices?", "expect_tools": ["list_invoices"],
            "forbid_tools": ["create_customer"], "reply_contains": ["overdue"]} | overrides
    return api.req("POST", OWNER, a_url(s, "/tests"), json=body)


def passing_script():
    return [tool_reply("list_invoices", {"overdue_only": True}), text_reply("No overdue invoices.")]


def test_configuration_is_validated(api, ag):
    assert configure(api, ag, tools=["drop_database"]).status_code == 422
    assert configure(api, ag, max_cost_usd="5").status_code == 422
    assert configure(api, ag, max_steps=50).status_code == 422
    assert configure(api, ag, route="anything").status_code == 422
    r = configure(api, ag)
    assert r.status_code == 200 and r.json()["number"] == 1
    assert api.req("PUT", SALES, a_url(ag, "/draft"), json={}).status_code == 403   # sales can run, not manage


def test_publishing_requires_passing_tests_on_that_version(api, ag):
    configure(api, ag)
    assert api.req("POST", OWNER, a_url(ag, "/versions/1/publish")).status_code == 409   # no tests yet
    add_test(api, ag)
    ag["ai"].script = [text_reply("I don't know.")]                                      # calls no tool: fails
    runs = api.req("POST", OWNER, a_url(ag, "/versions/1/test-runs")).json()
    assert runs[0]["passed"] is False
    assert set(runs[0]["failures"]) == {"expected a call to list_invoices", "reply should contain 'overdue'"}
    assert api.req("POST", OWNER, a_url(ag, "/versions/1/publish")).status_code == 409

    # Version 1 is frozen once tested; fixing it means a new draft.
    assert configure(api, ag, instructions="Always check list_invoices first.").json()["number"] == 2
    ag["ai"].script = passing_script()
    assert api.req("POST", OWNER, a_url(ag, "/versions/2/test-runs")).json()[0]["passed"] is True
    published = api.req("POST", OWNER, a_url(ag, "/versions/2/publish")).json()
    assert published["published_version"] == 2
    assert [v["status"] for v in published["versions"]] == ["frozen", "frozen"]


def publish_v1(api, ag):
    configure(api, ag)
    add_test(api, ag)
    ag["ai"].script = passing_script()
    api.req("POST", OWNER, a_url(ag, "/versions/1/test-runs"))
    assert api.req("POST", OWNER, a_url(ag, "/versions/1/publish")).status_code == 200


def run(api, ag, user=SALES, text="hello"):
    return api.req("POST", user, a_url(ag, "/runs"), json={"input": text})


def test_runs_get_the_intersection_of_agent_tools_and_user_permissions(api, ag):
    publish_v1(api, ag)
    ag["ai"].script = [text_reply("hi"), text_reply("hi")]
    run(api, ag, SALES)
    run(api, ag, OWNER)
    sales_tools = {t["name"] for t in ag["ai"].requests[-2]["tools"]}
    owner_tools = {t["name"] for t in ag["ai"].requests[-1]["tools"]}
    assert owner_tools == {"search_customers", "list_invoices", "trial_balance", "create_customer"}
    assert sales_tools == {"search_customers", "list_invoices", "create_customer"}   # no accounting for sales
    system = ag["ai"].requests[-1]["system"]
    assert system.endswith("Summarize overdue invoices and draft follow-ups.")
    assert "Rules that always apply" in system


def test_agent_cannot_use_tools_outside_its_allowlist(api, ag):
    publish_v1(api, ag)
    ag["ai"].script = [tool_reply("register_payment", {"invoice_id": "x", "amount": "1", "payment_date": "2026-01-01",
                                                       "method": "bank"}), text_reply("Couldn't do that.")]
    out = run(api, ag, OWNER).json()                       # even the owner, who may record payments
    assert out["tool_calls"][0]["name"] == "register_payment" and out["tool_calls"][0]["status"] == "denied"


def test_agent_writes_wait_for_the_users_approval(api, ag):
    publish_v1(api, ag)
    ag["ai"].script = [tool_reply("create_customer", {"name": "From Agent"}), text_reply("Pending approval.")]
    out = run(api, ag, SALES).json()
    call = out["tool_calls"][0]
    assert call["status"] == "pending_approval"
    assert "From Agent" not in [c["name"] for c in api.req("GET", OWNER, f"/orgs/{ag['org']}/customers").json()]
    assert api.req("POST", SALES, f"/orgs/{ag['org']}/assistant/actions/{call['id']}/approve").status_code == 200
    assert "From Agent" in [c["name"] for c in api.req("GET", OWNER, f"/orgs/{ag['org']}/customers").json()]


def test_stop_switch_tool_revocation_and_budgets(api, ag):
    publish_v1(api, ag)
    api.req("PATCH", OWNER, a_url(ag), json={"disabled_tools": ["list_invoices"]})
    ag["ai"].script = [text_reply("hi")]
    run(api, ag, OWNER)
    assert "list_invoices" not in {t["name"] for t in ag["ai"].requests[-1]["tools"]}   # revoked at once

    api.req("PATCH", OWNER, a_url(ag), json={"enabled": False})
    assert run(api, ag).status_code == 403
    api.req("PATCH", OWNER, a_url(ag), json={"enabled": True})

    # Per-run cost cap ($0.10): a run that spends more stops before the next model call.
    expensive = ModelReply(content=[{"type": "tool_use", "id": "t1", "name": "search_customers", "input": {}}],
                           stop_reason="tool_use", usage=Usage(30_000, 1_000), model="claude-opus-5-5")
    ag["ai"].script = [expensive, text_reply("never reached")]
    out = run(api, ag, OWNER).json()
    assert out["stop"] == "limit"

    api.req("PATCH", OWNER, a_url(ag), json={"monthly_budget_usd": "0.01"})
    assert run(api, ag).status_code == 403                                          # monthly budget used up


def test_step_limit_comes_from_the_version(api, ag):
    publish_v1(api, ag)  # max_steps 4
    ag["ai"].script = [tool_reply("search_customers", {}, tool_use_id=f"t{i}") for i in range(10)]
    out = run(api, ag, OWNER).json()
    assert out["stop"] == "max_steps"
    assert len(ag["ai"].requests) == 2 + 4  # the passing test run (2 calls) + this run, capped at 4


def test_rollback_by_publishing_an_older_version(api, ag):
    publish_v1(api, ag)
    configure(api, ag, instructions="v2 instructions")
    ag["ai"].script = passing_script()
    api.req("POST", OWNER, a_url(ag, "/versions/2/test-runs"))
    api.req("POST", OWNER, a_url(ag, "/versions/2/publish"))
    assert api.req("POST", OWNER, a_url(ag, "/versions/1/publish")).json()["published_version"] == 1
    ag["ai"].script = [text_reply("hi")]
    run(api, ag, OWNER)
    assert ag["ai"].requests[-1]["system"].endswith("Summarize overdue invoices and draft follow-ups.")


def test_run_log_and_failure_event(api, ag):
    publish_v1(api, ag)
    ag["ai"].script = []  # provider error -> failed run
    out = run(api, ag, SALES).json()
    assert out["stop"] == "provider_error"
    mine = api.req("GET", SALES, a_url(ag, "/runs")).json()
    assert [r["trigger"] for r in mine] == ["manual"]
    everyone = api.req("GET", OWNER, a_url(ag, "/runs")).json()
    assert {r["trigger"] for r in everyone} == {"manual", "test"}
    actions = [l["action"] for l in api.req("GET", OWNER, f"/orgs/{ag['org']}/audit-logs").json()]
    assert "agent.run_failed" in actions and "agent.published" in actions
