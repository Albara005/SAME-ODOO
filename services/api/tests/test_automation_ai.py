"""Per-member AI allowance, approved notes, file search, streamed replies, scheduled workflows,
and signed webhooks that can only reach the public internet."""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
from datetime import datetime, timedelta, timezone

import pytest

from novara.ai.provider import ScriptedProvider, text_reply, tool_reply
from novara.automation import engine as E
from novara.automation import webhooks as W
from novara.automation.events import last_slot, scan_schedules
from novara.automation.models import Notification
from novara.tenancy import bind_tenant

OWNER, SALES = "owner@auto.example.com", "sales@auto.example.com"


@pytest.fixture
def org(api, app):
    for e in (OWNER, SALES):
        api.signup(e)
    org_id = api.create_org(OWNER, "auto-co")
    api.add_member(OWNER, org_id, SALES, "sales")
    app.state.ai_provider = ScriptedProvider()
    return {"id": org_id, "ai": app.state.ai_provider, "app": app}


def o(s, path):
    return f"/orgs/{s['id']}{path}"


def conversation(api, s, user=SALES):
    return api.req("POST", user, o(s, "/assistant/conversations"), json={"title": "t"}).json()["id"]


def say(api, s, conv, text, user=SALES):
    return api.req("POST", user, o(s, f"/assistant/conversations/{conv}/messages"), json={"content": text})


def preface(request: dict) -> str:
    return request["messages"][-1]["content"][0]["text"]


# ---------------------------------------------------------------- AI allowance per member


def test_each_member_has_their_own_monthly_allowance(api, org):
    url = o(org, "/assistant/settings")
    assert api.req("PATCH", OWNER, url, json={"user_monthly_budget_usd": "0.05"}).json()["user_monthly_budget_usd"] == "0.0500"
    org["ai"].script = [text_reply("hi", input_tokens=10_000, output_tokens=1_000)]   # $0.06
    assert say(api, org, conversation(api, org), "hello").status_code == 200
    blocked = say(api, org, conversation(api, org), "again")
    assert blocked.status_code == 402 and "your AI allowance" in blocked.text
    org["ai"].script = [text_reply("owner is fine")]
    assert say(api, org, conversation(api, org, OWNER), "hello", user=OWNER).status_code == 200
    assert api.req("PATCH", OWNER, url, json={"user_monthly_budget_usd": None}).json()["user_monthly_budget_usd"] is None
    org["ai"].script = [text_reply("back")]
    assert say(api, org, conversation(api, org), "again").status_code == 200


# ---------------------------------------------------------------- approved notes


def test_notes_are_saved_only_after_approval_and_stay_private(api, org):
    conv = conversation(api, org)
    org["ai"].script = [tool_reply("remember", {"note": "Invoices go to finance@client.example"}),
                        text_reply("I'll remember that once you approve it.")]
    out = say(api, org, conv, "remember: invoices go to finance@client.example").json()
    action = out["pending_actions"][0]
    assert action["name"] == "remember" and api.req("GET", SALES, o(org, "/assistant/memories")).json() == []
    assert api.req("POST", SALES, o(org, f"/assistant/actions/{action['id']}/approve")).json()["status"] == "approved"
    notes = api.req("GET", SALES, o(org, "/assistant/memories")).json()
    assert [n["text"] for n in notes] == ["Invoices go to finance@client.example"]

    org["ai"].script = [text_reply("ok"), text_reply("ok")]
    say(api, org, conversation(api, org), "who gets invoices?")
    assert "Invoices go to finance@client.example" in preface(org["ai"].requests[-1])
    say(api, org, conversation(api, org, OWNER), "who gets invoices?", user=OWNER)
    assert "finance@client.example" not in preface(org["ai"].requests[-1])   # another member's assistant
    assert api.req("GET", OWNER, o(org, "/assistant/memories")).json() == []
    assert api.req("DELETE", OWNER, o(org, f"/assistant/memories/{notes[0]['id']}")).status_code == 404
    assert api.req("DELETE", SALES, o(org, f"/assistant/memories/{notes[0]['id']}")).status_code == 204
    assert api.req("GET", SALES, o(org, "/assistant/memories")).json() == []


# ---------------------------------------------------------------- file search


def test_search_files_finds_names_and_text_inside(api, org):
    api.req("POST", OWNER, o(org, "/files"), params={"filename": "contracts.txt"},
            content="Lease with Al Noor. Renewal due in March 2027.".encode(), headers={"Content-Type": "text/plain"})
    api.req("POST", OWNER, o(org, "/files"), params={"filename": "renewal-checklist.pdf"}, content=b"%PDF-1.4 binary",
            headers={"Content-Type": "application/pdf"})
    api.req("POST", OWNER, o(org, "/files"), params={"filename": "other.txt"}, content=b"nothing here",
            headers={"Content-Type": "text/plain"})
    org["ai"].script = [tool_reply("search_files", {"query": "renewal"}), text_reply("Found two files.")]
    out = say(api, org, conversation(api, org), "where is the renewal?").json()
    assert [(c["name"], c["status"]) for c in out["tool_calls"]] == [("search_files", "executed")]
    result = json.loads(org["ai"].requests[-1]["messages"][-1]["content"][0]["content"])["data"]
    by_name = {f["filename"]: f for f in result}
    assert set(by_name) == {"contracts.txt", "renewal-checklist.pdf"}
    assert "Renewal due in March 2027" in by_name["contracts.txt"]["text"]
    assert by_name["renewal-checklist.pdf"]["match"] == "file name"   # not a readable PDF: found by its name


# ---------------------------------------------------------------- streamed replies


def events(response) -> list[tuple[str, dict]]:
    out = []
    for block in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_reply_streams_while_it_is_written(api, org, client):
    conv = conversation(api, org)
    org["ai"].script = [tool_reply("search_customers", {"query": ""}), text_reply("You have no customers yet, sorry.")]
    r = client.post(f"/api/v1{o(org, f'/assistant/conversations/{conv}/messages/stream')}",
                    json={"content": "list customers"}, headers=api.h(SALES))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    got = events(r)
    kinds = [k for k, _ in got]
    assert kinds[0] == "tool" and got[0][1] == {"name": "search_customers", "status": "executed"}
    assert kinds[-1] == "done" and kinds.count("delta") > 1
    assert "".join(d["text"] for k, d in got if k == "delta") == "You have no customers yet, sorry."
    assert got[-1][1]["reply"] == "You have no customers yet, sorry."
    transcript = api.req("GET", SALES, o(org, f"/assistant/conversations/{conv}")).json()["messages"]
    assert transcript[-1] == {"role": "assistant", "text": "You have no customers yet, sorry."}

    api.req("PATCH", OWNER, o(org, "/assistant/settings"), json={"enabled": False})
    off = client.post(f"/api/v1{o(org, f'/assistant/conversations/{conv}/messages/stream')}",
                      json={"content": "hi"}, headers=api.h(SALES))
    assert off.status_code == 403   # refused before any streaming starts


# ---------------------------------------------------------------- scheduled workflows


def test_schedule_slots():
    now = datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc)   # Wednesday 09:00 in Riyadh (UTC+3)
    riyadh = {"timezone": "Asia/Riyadh", "time": "08:30"}
    assert last_slot({"every": "day", **riyadh}, now) == datetime(2026, 10, 21, 5, 30, tzinfo=timezone.utc)
    assert last_slot({"every": "day", **riyadh, "time": "10:00"}, now) == datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc)
    assert last_slot({"every": "week", **riyadh, "weekday": 0}, now) == datetime(2026, 10, 19, 5, 30, tzinfo=timezone.utc)
    assert last_slot({"every": "week", **riyadh, "weekday": 3}, now) == datetime(2026, 10, 15, 5, 30, tzinfo=timezone.utc)
    assert last_slot({"every": "month", **riyadh, "day": 1}, now) == datetime(2026, 10, 1, 5, 30, tzinfo=timezone.utc)
    assert last_slot({"every": "month", **riyadh, "day": 25}, now) == datetime(2026, 9, 25, 5, 30, tzinfo=timezone.utc)


def test_schedules_are_validated(api, org):
    url = o(org, "/workflows")
    base = {"name": "daily", "trigger": "schedule", "actions": [{"type": "notify", "title": "x"}]}
    assert api.req("POST", OWNER, url, json=base).status_code == 422                       # no schedule
    for bad in ({"every": "hour", "time": "08:00", "timezone": "UTC"},
                {"every": "day", "time": "8am", "timezone": "UTC"},
                {"every": "day", "time": "08:00", "timezone": "Mars/Base"},
                {"every": "week", "time": "08:00", "timezone": "UTC"},
                {"every": "month", "time": "08:00", "timezone": "UTC", "day": 31}):
        assert api.req("POST", OWNER, url, json=base | {"schedule": bad}).status_code == 422, bad
    wrong = {"name": "x", "trigger": "customer.created", "schedule": {"every": "day", "time": "08:00", "timezone": "UTC"},
             "actions": [{"type": "notify", "title": "x"}]}
    assert api.req("POST", OWNER, url, json=wrong).status_code == 422


def test_each_scheduled_workflow_fires_once_per_time(api, org):
    url = o(org, "/workflows")
    daily = api.req("POST", OWNER, url, json={
        "name": "Morning summary", "trigger": "schedule",
        "schedule": {"every": "day", "time": "08:00", "timezone": "Asia/Riyadh"},
        "actions": [{"type": "notify", "title": "Good morning ({{date}})"}]}).json()
    weekly = api.req("POST", OWNER, url, json={
        "name": "Weekly", "trigger": "schedule",
        "schedule": {"every": "week", "time": "08:00", "timezone": "Asia/Riyadh", "weekday": 6},
        "actions": [{"type": "notify", "title": "Week ahead"}]}).json()
    assert daily["schedule"]["every"] == "day"
    app = org["app"]

    def tick(now: datetime) -> list[str]:
        with app.state.sessionmaker() as db:
            bind_tenant(db, org["id"])
            scan_schedules(db, now)
            db.commit()
            E.process(db, provider=app.state.ai_provider, now=now)
            return sorted(n.title for n in db.query(Notification).all())

    soon = datetime.now(timezone.utc) + timedelta(days=1, hours=1)
    first = tick(soon)
    assert sum(t.startswith("Good morning") for t in first) == 1
    assert tick(soon + timedelta(minutes=5)) == first           # same time slot: nothing new
    later = tick(soon + timedelta(days=1))
    assert sum(t.startswith("Good morning") for t in later) == 2
    assert later.count("Week ahead") <= 2                       # only the weekly workflow sends this
    runs = api.req("GET", OWNER, o(org, f"/workflows/{weekly['id']}/runs")).json()
    assert all(r["workflow_id"] == weekly["id"] for r in runs)


# ---------------------------------------------------------------- webhooks


@pytest.mark.parametrize("url", [
    "http://hooks.example.com/x", "https://localhost/x", "https://127.0.0.1/x", "https://10.1.2.3/x",
    "https://169.254.169.254/latest/meta-data", "https://[::1]/x", "https://[::ffff:127.0.0.1]/x",
    "https://hooks.example.com:22/x", "https://user:pass@hooks.example.com/x", "https://db.internal/x",
    "https://printer.local/x", "ftp://hooks.example.com/x",
])
def test_unsafe_destinations_are_refused(url):
    with pytest.raises(W.UnsafeDestination):
        W.check_url(url)


def test_names_resolving_to_private_addresses_are_refused(monkeypatch):
    def fake(addresses):
        return lambda host, port, type=0: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", fake(["93.184.215.14", "10.0.0.5"]))
    with pytest.raises(W.UnsafeDestination):
        W.resolve_public("hooks.example.com", 443)   # one private answer is enough to refuse
    monkeypatch.setattr(socket, "getaddrinfo", fake(["93.184.215.14"]))
    assert W.resolve_public("hooks.example.com", 443) == "93.184.215.14"
    assert W.check_url("https://hooks.example.com:8443/novara?x=1").hostname == "hooks.example.com"


def test_webhook_deliveries_are_signed_and_retried(api, org):
    sent: list[tuple[str, bytes, dict]] = []
    answers = [500, 200]

    def transport(url, body, headers):
        sent.append((url, body, headers))
        return answers.pop(0)

    org["app"].state.webhooks.transport = transport
    url = o(org, "/workflows")
    assert api.req("POST", OWNER, url, json={"name": "x", "trigger": "customer.created",
                                             "actions": [{"type": "webhook", "url": "http://hooks.example.com"}]}
                   ).status_code == 422
    wf = api.req("POST", OWNER, url, json={"name": "To CRM", "trigger": "customer.created",
                                           "actions": [{"type": "webhook", "url": "https://hooks.example.com/novara"}]}).json()
    api.req("POST", OWNER, o(org, "/customers"), json={"name": "Gulf Trading"})
    api.req("POST", OWNER, o(org, "/automation/process"))
    run = api.req("GET", OWNER, o(org, f"/workflows/{wf['id']}/runs")).json()[0]
    assert run["status"] == "retrying" and "HTTP 500" in run["error"]

    # The retry is due later; process it as the worker would then.
    app = org["app"]
    with app.state.sessionmaker() as db:
        bind_tenant(db, org["id"])
        E.process(db, provider=app.state.ai_provider, now=datetime.now(timezone.utc) + timedelta(minutes=5),
                  webhooks=app.state.webhooks)
    run = api.req("GET", OWNER, o(org, f"/workflows/{wf['id']}/runs")).json()[0]
    assert run["status"] == "completed" and run["steps"][0]["result"] == {"status": 200}

    (url1, body1, h1), (_, body2, h2) = sent
    assert url1 == "https://hooks.example.com/novara"
    assert h1["Idempotency-Key"] == h2["Idempotency-Key"] == f"wf:{run['id']}:0"   # receivers can drop the repeat
    secret = api.req("GET", OWNER, o(org, "/automation/webhook-secret")).json()["secret"]
    expected = hmac.new(secret.encode(), f"{h2['X-Novara-Timestamp']}.".encode() + body2, hashlib.sha256).hexdigest()
    assert h2["X-Novara-Signature"] == f"v1={expected}"
    payload = json.loads(body2)
    assert (payload["event"], payload["data"]["name"], payload["workflow"]["name"]) == ("customer.created", "Gulf Trading", "To CRM")

    rotated = api.req("POST", OWNER, o(org, "/automation/webhook-secret/rotate")).json()["secret"]
    assert rotated != secret and rotated.startswith("whsec_")
    assert api.req("GET", SALES, o(org, "/automation/webhook-secret")).status_code == 403
