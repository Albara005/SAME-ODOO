"""Private hosting (plan 11.2): request -> cost shown -> explicit owner approval -> operators
provision and record delivery. Nothing is provisioned automatically."""

from __future__ import annotations

import pytest

from novara import ops

OWNER, ADMIN = "owner@host.example.com", "admin@host.example.com"


@pytest.fixture
def org(api):
    api.signup(OWNER)
    api.signup(ADMIN)
    org = api.create_org(OWNER, "host-co")
    api.add_member(OWNER, org, ADMIN, "admin")
    return org


def test_full_request_flow_requires_owner_cost_confirmation(api, ops_sessionmaker, org):
    quote = api.req("POST", ADMIN, f"/orgs/{org}/hosting/quote",
                    json={"kind": "dedicated_database", "size": "medium", "region": "me-central"}).json()
    assert quote["monthly_usd"] == "400.00" and "vCPU" in quote["specification"]

    req = api.req("POST", ADMIN, f"/orgs/{org}/hosting/requests", json={
        "kind": "dedicated_database", "size": "medium", "region": "me-central", "reason": "regulatory"}).json()
    assert (req["status"], req["monthly_usd"]) == ("pending_approval", "400.00")
    again = api.req("POST", ADMIN, f"/orgs/{org}/hosting/requests",
                    json={"kind": "dedicated_database", "size": "small", "region": "me-central"})
    assert again.status_code == 409                                           # one open request at a time

    approve = f"/orgs/{org}/hosting/requests/{req['id']}/approve"
    assert api.req("POST", ADMIN, approve, json={"confirm_monthly_usd": "400.00"}).status_code == 403  # owners only
    assert api.req("POST", OWNER, approve, json={"confirm_monthly_usd": "40.00"}).status_code == 422   # must match
    ok = api.req("POST", OWNER, approve, json={"confirm_monthly_usd": "400"}).json()
    assert ok["status"] == "approved" and ok["approved_by"]

    # The API never changes the hosting mode; operators do, after real provisioning.
    assert api.req("GET", OWNER, f"/orgs/{org}/hosting").json()["hosting_mode"] == "shared"
    with pytest.raises(SystemExit):
        ops.transition(ops_sessionmaker, "hosting-deliver", req["id"])     # must be provisioning first
    ops.transition(ops_sessionmaker, "hosting-start", req["id"], note="ticket OPS-12")
    ops.transition(ops_sessionmaker, "hosting-deliver", req["id"], note="pg-ded-07")
    assert api.req("GET", OWNER, f"/orgs/{org}/hosting").json()["hosting_mode"] == "dedicated_database"
    final = api.req("GET", OWNER, f"/orgs/{org}/hosting/requests").json()[0]
    assert (final["status"], final["operator_note"]) == ("delivered", "pg-ded-07")
    actions = [l["action"] for l in api.req("GET", OWNER, f"/orgs/{org}/audit-logs").json()]
    assert {"hosting.requested", "hosting.approved", "hosting.provisioning", "hosting.delivered"} <= set(actions)


def test_invalid_options_and_cancellation(api, org):
    bad = api.req("POST", ADMIN, f"/orgs/{org}/hosting/requests",
                  json={"kind": "bare_metal", "size": "huge", "region": "moon"})
    assert bad.status_code == 422
    req = api.req("POST", ADMIN, f"/orgs/{org}/hosting/requests",
                  json={"kind": "dedicated_environment", "size": "small", "region": "eu-central"}).json()
    assert api.req("POST", ADMIN, f"/orgs/{org}/hosting/requests/{req['id']}/cancel").json()["status"] == "cancelled"
    assert api.req("POST", OWNER, f"/orgs/{org}/hosting/requests/{req['id']}/approve",
                   json={"confirm_monthly_usd": "600"}).status_code == 422
