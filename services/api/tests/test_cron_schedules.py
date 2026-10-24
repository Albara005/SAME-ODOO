"""Cron schedules: the parser, the times they name in a time zone, and firing once per time."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from novara.automation import cron
from novara.automation import engine as E
from novara.automation.events import last_slot, scan_schedules
from novara.automation.models import Notification
from novara.tenancy import bind_tenant
from tests.test_automation_ai import OWNER, SALES, o, org  # noqa: F401

RIYADH = ZoneInfo("Asia/Riyadh")


@pytest.mark.parametrize("expression, reason", [
    ("* * * * *", "every 5 minutes"),
    ("0,3 * * * *", "every 5 minutes"),
    ("0 9 31 2 *", "never happens"),
    ("60 * * * *", "between 0 and 59"),
    ("0 24 * * *", "between 0 and 23"),
    ("0 9 * * mon-sun-x", "isn't between"),
    ("0 9 * *", "five fields"),
    ("5-1 * * * *", "backwards"),
    ("*/0 * * * *", "above 0"),
    ("0 9 * jan,,feb *", "empty item"),
])
def test_bad_expressions_say_why(expression, reason):
    with pytest.raises(cron.CronError, match=reason):
        cron.parse(expression)


def test_what_an_expression_names():
    work = cron.parse("*/15  8-17 * * SUN-thu")                      # spacing and case don't matter
    assert work.minutes == (0, 15, 30, 45) and work.hours == tuple(range(8, 18))
    assert work.on(date(2026, 10, 25)) and not work.on(date(2026, 10, 23))   # Sunday yes, Friday no
    assert cron.parse("@daily") == cron.parse("0 0 * * *")
    assert cron.parse("0 9 * * 7").weekdays == frozenset({0})        # 7 is Sunday too
    # Day of month and day of week both restricted: either matches (classic cron).
    either = cron.parse("0 9 1 * mon")
    assert either.on(date(2026, 10, 1)) and either.on(date(2026, 10, 26)) and not either.on(date(2026, 10, 27))

    saturday_night = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)   # 23:00 in Riyadh
    assert cron.last_fire(work, RIYADH, saturday_night) == datetime(2026, 10, 22, 14, 45, tzinfo=timezone.utc)
    assert [t.isoformat() for t in cron.next_fires(work, RIYADH, saturday_night, 2)] == [
        "2026-10-25T08:00:00+03:00", "2026-10-25T08:15:00+03:00"]
    leap = cron.parse("0 9 29 2 *")
    assert cron.last_fire(leap, RIYADH, saturday_night).astimezone(RIYADH).date() == date(2024, 2, 29)
    # London skips 01:00-02:00 on 29 March 2026: 01:30 that day doesn't exist, so it is never "in the future".
    london, nightly = ZoneInfo("Europe/London"), cron.parse("30 1 * * *")
    just_after_jump = datetime(2026, 3, 29, 1, 10, tzinfo=timezone.utc)                  # 02:10 BST
    assert cron.last_fire(nightly, london, just_after_jump) <= just_after_jump
    assert all(t > just_after_jump for t in cron.next_fires(nightly, london, just_after_jump, 3))
    # India is UTC+5:30: 09:00 there is 03:30 UTC.
    assert last_slot({"every": "cron", "cron": "0 9 * * *", "timezone": "Asia/Kolkata"},
                     datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc)) == datetime(2026, 10, 21, 3, 30, tzinfo=timezone.utc)


def test_cron_workflows_fire_once_per_time(api, org):
    url = o(org, "/workflows")
    body = lambda schedule: {"name": "Every 20 minutes", "trigger": "schedule", "schedule": schedule,  # noqa: E731
                             "actions": [{"type": "notify", "title": "Tick {{scheduled_for}}"}]}
    refused = api.req("POST", OWNER, url, json=body({"every": "cron", "cron": "* * * * *", "timezone": "UTC"}))
    assert refused.status_code == 422 and "5 minutes" in refused.json()["detail"]
    assert api.req("POST", OWNER, url, json=body({"every": "cron", "timezone": "UTC"})).status_code == 422
    wf = api.req("POST", OWNER, url, json=body({"every": "cron", "cron": " */20   *  * * * ", "timezone": "UTC"}))
    assert wf.status_code == 201, wf.text
    assert wf.json()["schedule"] == {"every": "cron", "cron": "*/20 * * * *", "timezone": "UTC"}

    preview = api.req("POST", OWNER, o(org, "/workflows/schedule-preview"),
                      json={"schedule": {"every": "cron", "cron": "0 9 * * sun-thu", "timezone": "Asia/Riyadh"}})
    assert preview.status_code == 200, preview.text
    runs = preview.json()["next_runs"]
    assert len(runs) == 3 and all(r.endswith("09:00:00+03:00") for r in runs)
    assert api.req("POST", SALES, o(org, "/workflows/schedule-preview"),
                   json={"schedule": {"every": "day", "time": "09:00", "timezone": "UTC"}}).status_code == 403
    daily = api.req("POST", OWNER, o(org, "/workflows/schedule-preview"),
                    json={"schedule": {"every": "day", "time": "09:00", "timezone": "UTC"}}).json()["next_runs"]
    assert len(daily) == 3 and all(r[11:16] == "09:00" for r in daily)

    app = org["app"]

    def tick(now: datetime) -> int:
        with app.state.sessionmaker() as db:
            bind_tenant(db, org["id"])
            scan_schedules(db, now)
            db.commit()
            E.process(db, provider=app.state.ai_provider, now=now)
            return db.query(Notification).filter(Notification.title.startswith("Tick")).count()

    start = datetime.now(timezone.utc).replace(minute=5, second=0, microsecond=0) + timedelta(hours=1)
    assert tick(start) == 1                                 # :00 of that hour
    assert tick(start + timedelta(minutes=10)) == 1         # still :00 (no new slot until :20)
    assert tick(start + timedelta(minutes=16)) == 2         # :20
    assert tick(start + timedelta(minutes=95)) == 3         # down for a while: only the latest missed slot fires
