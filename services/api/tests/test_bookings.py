"""Bookings: one calendar, opening hours and holidays, the team's bookings and requests from published sites."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from novara.modules.bookings import router as bookings

OWNER, SALES, MEMBER, VIEWER = (f"{n}@bookings.example.com" for n in ("owner", "sales", "member", "viewer"))
RIYADH = ZoneInfo("Asia/Riyadh")


def next_sunday(min_days: int = 3) -> date:
    d = datetime.now(RIYADH).date() + timedelta(days=min_days)
    while d.weekday() != 6:
        d += timedelta(days=1)
    return d


def at(day: date, hhmm: str) -> str:
    h, m = hhmm.split(":")
    return datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=RIYADH).isoformat()


@pytest.fixture
def cal(api):
    for e in (OWNER, SALES, MEMBER, VIEWER):
        api.signup(e)
    org = api.create_org(OWNER, "bookings-co")
    api.add_member(OWNER, org, SALES, "sales")
    api.add_member(OWNER, org, MEMBER, "member")
    api.add_member(OWNER, org, VIEWER, "viewer")
    # Sunday to Thursday, 09:00-13:00 and 14:00-17:00.
    hours = [{"weekday": d, "opens_at": a, "closes_at": b} for d in (6, 0, 1, 2, 3)
             for a, b in (("09:00", "13:00"), ("14:00", "17:00"))]
    r = api.req("PUT", OWNER, f"/orgs/{org}/bookings/settings", json={
        "timezone": "Asia/Riyadh", "slot_minutes": 30, "notice_hours": 2, "days_ahead": 60, "hours": hours})
    assert r.status_code == 200, r.text
    consult = api.req("POST", SALES, f"/orgs/{org}/bookings/services", json={
        "name": "Consultation", "duration_minutes": 60, "price": "200"}).json()["id"]
    return {"org": org, "consult": consult, "day": next_sunday()}


def url(s, p=""):
    return f"/orgs/{s['org']}/bookings{p}"


def free(api, s, who=MEMBER, day=None, service=None):
    r = api.req("GET", who, url(s, "/free-times"), params={"service_id": service or s["consult"],
                                                          "day": (day or s["day"]).isoformat()})
    assert r.status_code == 200, r.text
    return [t[11:16] for t in r.json()["times"]]


def test_the_team_books_on_one_calendar(api, cal):
    s, day = cal, cal["day"]
    assert api.req("GET", VIEWER, url(s, "/settings")).status_code == 403
    assert api.req("PUT", MEMBER, url(s, "/settings"), json={"timezone": "Asia/Riyadh"}).status_code == 403
    bad = api.req("PUT", OWNER, url(s, "/settings"), json={"timezone": "Asia/Riyadh", "hours": [
        {"weekday": 0, "opens_at": "09:00", "closes_at": "12:00"}, {"weekday": 0, "opens_at": "11:00", "closes_at": "15:00"}]})
    assert bad.status_code == 422                                                       # overlapping ranges
    assert api.req("PUT", OWNER, url(s, "/settings"), json={"timezone": "Mars/Base"}).status_code == 422

    # A one-hour service fits 09:00-12:00 and 14:00-16:00 every half hour: 7 + 5 times, never across the break.
    times = free(api, s)
    assert times[0] == "09:00" and "12:30" not in times and times[-1] == "16:00" and len(times) == 12

    book = lambda when, **kw: api.req("POST", SALES, url(s), json={  # noqa: E731
        "service_id": s["consult"], "starts_at": at(day, when), "name": "Hala", "phone": "+966500000001", **kw})
    first = book("10:00")
    assert first.status_code == 201, first.text
    assert (first.json()["status"], first.json()["number"][:3]) == ("confirmed", "BK-")
    assert book("10:30").status_code == 409                                             # overlaps 10:00-11:00
    assert book("12:30").status_code == 422                                             # runs into the break
    assert book("12:30", outside_hours=True).status_code == 201                         # on purpose
    assert api.req("POST", MEMBER, url(s), json={"service_id": s["consult"], "starts_at": at(day, "15:00"),
                                                 "name": "x"}).status_code == 403
    assert free(api, s) == ["09:00", "11:00", "11:30", "14:00", "14:30", "15:00", "15:30", "16:00"]

    listed = api.req("GET", MEMBER, url(s), params={"start": day.isoformat(), "end": day.isoformat()}).json()
    assert [(b["service_name"], b["starts_at"][11:16]) for b in listed] == [("Consultation", "07:00"),   # UTC
                                                                            ("Consultation", "09:30")]

    # Cancelling frees the time again; a public holiday closes the day.
    assert api.req("POST", SALES, url(s, f"/{first.json()['id']}/cancel"), json={}).json()["status"] == "cancelled"
    assert "10:00" in free(api, s)
    assert api.req("POST", SALES, url(s, f"/{first.json()['id']}/cancel"), json={}).status_code == 409
    api.req("POST", OWNER, f"/orgs/{s['org']}/hr/holidays", json={"day": (day + timedelta(days=1)).isoformat(),
                                                                  "name": "National day"})
    assert free(api, s, day=day + timedelta(days=1)) == []
    assert free(api, s, day=day - timedelta(days=1)) == []                               # Saturday: closed


def test_a_published_site_asks_and_the_team_confirms(api, client, cal):
    s, day = cal, cal["day"]
    project = api.req("POST", OWNER, f"/orgs/{s['org']}/studio/projects", json={"name": "Clinic", "slug": "clinic"}).json()["id"]
    keys = f"/orgs/{s['org']}/studio/projects/{project}/data-keys"
    assert api.req("POST", OWNER, keys, json={"name": "x", "scopes": ["bookings"]}).status_code == 201
    catalog_only = api.req("POST", OWNER, keys, json={"name": "Shop", "scopes": ["catalog"]}).json()["key"]
    key = api.req("POST", OWNER, keys, json={"name": "Bookings", "scopes": ["bookings"]}).json()["key"]
    h = {"X-Novara-Key": key}

    assert client.get("/api/v1/public/booking-services", headers={"X-Novara-Key": catalog_only}).status_code == 401
    listed = client.get("/api/v1/public/booking-services", headers=h).json()
    assert [(x["name"], x["duration_minutes"], x["price"]) for x in listed["services"]] == [("Consultation", 60, "200")]
    assert listed["timezone"] == "Asia/Riyadh"
    times = client.get("/api/v1/public/free-times", params={"service": s["consult"], "day": day.isoformat()},
                       headers=h).json()["times"]
    assert times[0] == at(day, "09:00") and len(times) == 12

    ask = lambda when, **kw: client.post("/api/v1/public/bookings", headers=h, json={  # noqa: E731
        "service_id": s["consult"], "starts_at": when, "name": "Reem", "email": "reem@example.com",
        "note": "First visit", **kw})
    sent = ask(times[2])                                                                 # 10:00
    assert sent.status_code == 201, sent.text
    assert sent.json()["status"] == "requested" and sent.json()["reference"].startswith("BK-")
    assert sent.headers["Access-Control-Allow-Origin"] == "*"
    assert ask(at(day, "10:30")).status_code == 409                                      # that hour is held
    assert ask(at(day, "14:10")).status_code == 422                                      # not one of the times
    assert ask(at(day, "20:00")).status_code == 422
    assert ask(times[5], email=None).status_code == 422                                  # no way to reply
    assert ask(times[5], website="http://spam").json()["reference"] == ""                # the honeypot
    soon = datetime.now(RIYADH) + timedelta(minutes=30)
    assert ask(soon.replace(second=0, microsecond=0).isoformat()).status_code == 422     # inside the notice period
    assert len(client.get("/api/v1/public/free-times", params={"service": s["consult"], "day": day.isoformat()},
                          headers=h).json()["times"]) == 9

    # The team sees the request, is told about it, and confirms it.
    waiting = api.req("GET", MEMBER, url(s, "/requests")).json()
    assert [(b["name"], b["source"], b["status"]) for b in waiting] == [("Reem", "Clinic", "requested")]
    assert any(n["title"] == "طلب حجز من Reem" for n in api.req("GET", SALES, f"/orgs/{s['org']}/notifications").json())
    assert api.req("POST", MEMBER, url(s, f"/{waiting[0]['id']}/confirm")).status_code == 403
    done = api.req("POST", SALES, url(s, f"/{waiting[0]['id']}/confirm"))
    assert done.status_code == 200 and done.json()["status"] == "confirmed"
    assert api.req("POST", SALES, url(s, f"/{waiting[0]['id']}/decline"), json={}).status_code == 409
    assert api.req("GET", MEMBER, url(s, "/requests")).json() == []

    # Declining another request frees its time.
    other = ask(times[-1]).json()["reference"]
    second = next(b for b in api.req("GET", SALES, url(s, "/requests")).json() if b["number"] == other)
    assert api.req("POST", SALES, url(s, f"/{second['id']}/decline"), json={"note": "Fully booked"}).json()["status"] == "declined"
    assert times[-1] in client.get("/api/v1/public/free-times", params={"service": s["consult"], "day": day.isoformat()},
                                   headers=h).json()["times"]


def test_site_requests_are_rate_limited(api, client, cal, monkeypatch):
    s = cal
    project = api.req("POST", OWNER, f"/orgs/{s['org']}/studio/projects", json={"name": "Clinic", "slug": "clinic2"}).json()["id"]
    key = api.req("POST", OWNER, f"/orgs/{s['org']}/studio/projects/{project}/data-keys",
                  json={"name": "Bookings", "scopes": ["bookings"]}).json()["key"]
    monkeypatch.setattr(bookings, "IP_LIMIT", 2)
    day = s["day"]
    codes = [client.post("/api/v1/public/bookings", headers={"X-Novara-Key": key}, json={
        "service_id": s["consult"], "starts_at": at(day, t), "name": "N", "phone": "1"}).status_code
        for t in ("09:00", "11:00", "14:00")]
    assert codes == [201, 201, 429]


def test_saving_hours_touches_only_this_organization(api, cal):
    s = cal
    other = api.create_org(OWNER, "other-bookings-co")
    assert api.req("PUT", OWNER, f"/orgs/{other}/bookings/settings", json={"timezone": "Asia/Dubai", "hours": []}).status_code == 200
    assert len(api.req("GET", MEMBER, url(s, "/settings")).json()["hours"]) == 10
    assert api.req("GET", OWNER, f"/orgs/{other}/bookings/settings").json()["timezone"] == "Asia/Dubai"
    assert api.req("GET", OWNER, url(s, "/settings")).json()["timezone"] == "Asia/Riyadh"
