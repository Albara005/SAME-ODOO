"""Live updates (server-sent events): the user's counts arrive as events and follow their access."""

from __future__ import annotations

import json

from novara.automation.live import snapshot
from novara.modules.organizations.models import Organization
from novara.tenancy import bind_tenant
from tests.test_automation import ACCT, OWNER, SALES, create_wf, post_invoice, process, wf  # noqa: F401


def events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def live(api, user, org):
    r = api.req("GET", user, f"/orgs/{org}/live?once=true")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return events(r.text)


def test_counts_follow_notifications_tasks_and_approvals(api, wf):
    org = wf["org"]
    assert live(api, OWNER, org) == [("counts", {"notifications": 0, "approvals": 0, "tasks": 0, "latest_notification": None})]
    create_wf(api, wf, trigger="invoice.posted", actions=[
        {"type": "create_task", "title": "Call about {{number}}", "assignee": "owner"},
        {"type": "notify", "to": "owner", "title": "Posted {{number}}"},
        {"type": "require_approval", "permission": "payments.write", "message": "Chase {{number}}?"}])
    post_invoice(api, wf, "100")
    process(api, wf)

    [(name, data)] = live(api, OWNER, org)
    assert name == "counts"
    assert (data["notifications"], data["approvals"], data["tasks"]) == (1, 1, 1)
    assert data["latest_notification"]["title"] == "Posted INV-2026-00001"
    # Approvals depend on the viewer's permissions: sales can't approve payments.write steps.
    assert live(api, SALES, org)[0][1]["approvals"] == 0
    assert live(api, ACCT, org)[0][1]["approvals"] == 1


def test_stream_requires_membership_and_a_live_session(api, wf, app):
    api.signup("stranger@wf.example.com")
    assert api.req("GET", "stranger@wf.example.com", f"/orgs/{wf['org']}/live?once=true").status_code == 404
    me = api.req("GET", OWNER, "/me").json()
    sessions = api.req("GET", OWNER, "/me/sessions").json()
    sid = sessions[0]["id"]
    maker = app.state.sessionmaker
    assert snapshot(maker, wf["org"], me["id"], sid) is not None
    with maker() as db:
        bind_tenant(db, wf["org"])
        db.get(Organization, wf["org"]).require_mfa = True
        db.commit()
    assert snapshot(maker, wf["org"], me["id"], sid) is None   # this password-only sign-in no longer qualifies
    with maker() as db:
        bind_tenant(db, wf["org"])
        db.get(Organization, wf["org"]).require_mfa = False
        db.commit()
    assert snapshot(maker, wf["org"], me["id"], sid) is not None
    assert api.req("POST", OWNER, "/auth/logout").status_code in (200, 204)
    assert snapshot(maker, wf["org"], me["id"], sid) is None   # a revoked login ends an open stream
