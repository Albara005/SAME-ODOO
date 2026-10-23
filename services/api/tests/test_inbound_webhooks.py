"""Inbound webhooks that start a workflow, and hourly schedules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from novara.automation import engine as E
from novara.automation import inbound
from novara.automation.events import last_slot, scan_schedules
from novara.automation.models import Notification, Task
from novara.tenancy import bind_tenant
from tests.test_automation_ai import OWNER, SALES, o, org  # noqa: F401


@pytest.fixture
def hooked(api, org):
    wf = api.req("POST", OWNER, o(org, "/workflows"), json={
        "name": "New lead from the website", "trigger": "webhook.received",
        "conditions": [{"field": "data.budget", "op": "gte", "value": 1000}],
        "actions": [{"type": "create_task", "title": "Call {{data.name}} ({{data.budget}})", "assignee": "owner"}]})
    assert wf.status_code == 201, wf.text
    hook = api.req("GET", OWNER, o(org, f"/workflows/{wf.json()['id']}/hook"))
    assert hook.status_code == 200, hook.text
    return {**org, "workflow": wf.json(), "path": hook.json()["path"]}


def call(client, path, body, **headers):
    return client.post(path, json=body, headers=headers)


def process(api, s):
    assert api.req("POST", OWNER, o(s, "/automation/process")).status_code == 200


def tasks(app, org_id) -> list[str]:
    with app.state.sessionmaker() as db:
        bind_tenant(db, org_id)
        return sorted(t.title for t in db.query(Task).all())


def test_a_call_to_the_address_starts_the_workflow(api, client, hooked):
    s = hooked
    assert s["path"].startswith("/api/v1/hooks/nvwh_")
    assert api.req("GET", SALES, o(s, f"/workflows/{s['workflow']['id']}/hook")).status_code == 403

    first = call(client, s["path"], {"name": "Hala Stores", "budget": 2500}, **{"Idempotency-Key": "lead-17"})
    assert first.status_code == 202, first.text
    again = call(client, s["path"], {"name": "Hala Stores", "budget": 2500}, **{"Idempotency-Key": "lead-17"})
    assert again.status_code == 200 and again.json()["event_id"] == first.json()["event_id"]
    assert call(client, s["path"], {"name": "Small shop", "budget": 50}).status_code == 202   # condition not met
    process(api, s)
    assert tasks(s["app"], s["id"]) == ["Call Hala Stores (2500)"]

    runs = api.req("GET", OWNER, o(s, f"/workflows/{s['workflow']['id']}/runs")).json()
    assert sorted(r["status"] for r in runs) == ["completed", "skipped"]


def test_bad_calls_are_refused(api, client, hooked):
    s = hooked
    assert call(client, "/api/v1/hooks/nvwh_not-a-real-address", {"a": 1}).status_code == 404
    assert call(client, s["path"], [1, 2, 3]).status_code == 422
    assert client.post(s["path"], content=b"not json", headers={"Content-Type": "application/json"}).status_code == 422
    assert client.post(s["path"], content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415
    big = {"text": "x" * (inbound.MAX_BYTES + 10)}
    assert call(client, s["path"], big).status_code == 413

    # A disabled workflow has no address; enabling it brings the same one back.
    wid = s["workflow"]["id"]
    api.req("PATCH", OWNER, o(s, f"/workflows/{wid}"), json={"enabled": False})
    assert call(client, s["path"], {"a": 1}).status_code == 404
    api.req("PATCH", OWNER, o(s, f"/workflows/{wid}"), json={"enabled": True})
    assert call(client, s["path"], {"a": 1}).status_code == 202

    # Rotating replaces the address at once.
    rotated = api.req("POST", OWNER, o(s, f"/workflows/{wid}/hook/rotate")).json()
    assert rotated["path"] != s["path"]
    assert call(client, s["path"], {"a": 1}).status_code == 404
    assert call(client, rotated["path"], {"a": 1}).status_code == 202

    # Only workflows started by a webhook have an address.
    other = api.req("POST", OWNER, o(s, "/workflows"), json={
        "name": "x", "trigger": "customer.created", "actions": [{"type": "notify", "title": "x"}]}).json()
    assert api.req("GET", OWNER, o(s, f"/workflows/{other['id']}/hook")).status_code == 404


def test_each_address_takes_a_limited_number_of_calls_a_minute(client, hooked, monkeypatch):
    monkeypatch.setattr(inbound, "RATE_LIMIT", 3)
    for n in range(3):
        assert call(client, hooked["path"], {"n": n}).status_code == 202
    limited = call(client, hooked["path"], {"n": 4})
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "60"


def test_the_address_secret_is_stored_encrypted(api, hooked):
    from novara.automation.models import Workflow

    token = hooked["path"].rsplit("/", 1)[1]
    with hooked["app"].state.sessionmaker() as db:
        bind_tenant(db, hooked["id"])
        wf = db.get(Workflow, hooked["workflow"]["id"])
        assert token not in (wf.hook_secret or "") and wf.hook_hash != token


# ---------------------------------------------------------------- hourly schedules


def test_hourly_slots():
    now = datetime(2026, 10, 21, 9, 40, tzinfo=timezone.utc)
    assert last_slot({"every": "hour", "minute": 15, "timezone": "UTC"}, now) == datetime(2026, 10, 21, 9, 15, tzinfo=timezone.utc)
    assert last_slot({"every": "hour", "minute": 50, "timezone": "UTC"}, now) == datetime(2026, 10, 21, 8, 50, tzinfo=timezone.utc)
    # India is UTC+5:30: minute 0 there is minute 30 in UTC.
    assert last_slot({"every": "hour", "minute": 0, "timezone": "Asia/Kolkata"}, now) == datetime(2026, 10, 21, 9, 30, tzinfo=timezone.utc)


def test_hourly_schedules_fire_once_an_hour(api, org):
    url = o(org, "/workflows")
    for bad in ({"every": "hour", "timezone": "UTC"}, {"every": "hour", "minute": 60, "timezone": "UTC"},
                {"every": "hour", "minute": True, "timezone": "UTC"}):
        assert api.req("POST", OWNER, url, json={"name": "x", "trigger": "schedule", "schedule": bad,
                                                  "actions": [{"type": "notify", "title": "x"}]}).status_code == 422, bad
    wf = api.req("POST", OWNER, url, json={
        "name": "Hourly check", "trigger": "schedule", "schedule": {"every": "hour", "minute": 5, "timezone": "Asia/Riyadh"},
        "actions": [{"type": "notify", "title": "Check at {{scheduled_for}}"}]})
    assert wf.status_code == 201, wf.text
    assert wf.json()["schedule"] == {"every": "hour", "minute": 5, "timezone": "Asia/Riyadh"}
    app = org["app"]

    def tick(now: datetime) -> int:
        with app.state.sessionmaker() as db:
            bind_tenant(db, org["id"])
            scan_schedules(db, now)
            db.commit()
            E.process(db, provider=app.state.ai_provider, now=now)
            return db.query(Notification).filter(Notification.title.startswith("Check at")).count()

    start = datetime.now(timezone.utc).replace(minute=30) + timedelta(hours=1)
    assert tick(start) == 1
    assert tick(start + timedelta(minutes=20)) == 1   # same hour
    assert tick(start + timedelta(hours=1)) == 2
