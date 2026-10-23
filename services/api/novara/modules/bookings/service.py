"""Free times and the rules for holding one."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara.modules.bookings.models import BookableService, Booking, BookingSettings, OpeningHours
from novara.modules.hr.models import Holiday

LIVE = ("requested", "confirmed")  # bookings that hold their time


class BookingError(ValueError):
    """The time can't be booked (HTTP 422 by the router)."""


class SlotTaken(BookingError):
    """Someone else holds that time (HTTP 409)."""


def utc(dt: datetime) -> datetime:
    """SQLite hands datetimes back without a zone; they are UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise BookingError(f"unknown time zone '{name}'")


DEFAULTS = {"timezone": "Asia/Riyadh", "slot_minutes": 30, "notice_hours": 2, "days_ahead": 60}


def current(db: Session) -> BookingSettings:
    """The settings to read by. Reading never writes: an organization without a row gets the defaults."""
    return db.scalar(select(BookingSettings)) or BookingSettings(**DEFAULTS)


def locked(db: Session) -> BookingSettings:
    """The settings row, created if needed and locked, so bookings (and changes to the rules) go one at a time."""
    stmt = select(BookingSettings).with_for_update()
    s = db.scalar(stmt)
    if s is None:
        try:
            with db.begin_nested():  # two first bookings at once: one inserts, the other finds it
                db.add(BookingSettings(**DEFAULTS))
        except IntegrityError:
            pass
        s = db.scalar(stmt)
    return s


def hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def open_ranges(db: Session, s: BookingSettings, day: date) -> list[tuple[datetime, datetime]]:
    """The day's opening hours as UTC ranges; a public holiday is closed."""
    if db.scalar(select(Holiday.id).where(Holiday.day == day)):
        return []
    tz = zone(s.timezone)
    hours = db.scalars(select(OpeningHours).where(OpeningHours.weekday == day.weekday()).order_by(OpeningHours.opens_at))
    return [(datetime.combine(day, hhmm(h.opens_at), tz).astimezone(timezone.utc),
             datetime.combine(day, hhmm(h.closes_at), tz).astimezone(timezone.utc)) for h in hours]


def overlapping(db: Session, start: datetime, end: datetime, skip: str | None = None) -> Booking | None:
    stmt = select(Booking).where(Booking.status.in_(LIVE), Booking.starts_at < end, Booking.ends_at > start)
    if skip:
        stmt = stmt.where(Booking.id != skip)
    return db.scalar(stmt.order_by(Booking.starts_at).limit(1))


def free_times(db: Session, s: BookingSettings, service: BookableService, day: date, now: datetime, *,
               public: bool) -> list[datetime]:
    """Start times on that day where the service fits inside opening hours and nothing else is booked.
    Sites also keep to the notice period and how far ahead bookings open."""
    earliest = now + timedelta(hours=s.notice_hours) if public else now
    latest = now + timedelta(days=s.days_ahead) if public else None
    length, step = timedelta(minutes=service.duration_minutes), timedelta(minutes=s.slot_minutes)
    ranges = open_ranges(db, s, day)
    if not ranges:
        return []
    lo, hi = ranges[0][0], ranges[-1][1]
    taken = [(utc(b.starts_at), utc(b.ends_at)) for b in db.scalars(
        select(Booking).where(Booking.status.in_(LIVE), Booking.starts_at < hi, Booking.ends_at > lo))]
    out = []
    for a, b in ranges:
        t = a
        while t + length <= b:
            if t >= earliest and (latest is None or t <= latest) and not any(x < t + length and y > t for x, y in taken):
                out.append(t)
            t += step
    return out


def check(db: Session, s: BookingSettings, service: BookableService, start: datetime, now: datetime, *,
          public: bool, outside_hours: bool = False, skip: str | None = None) -> datetime:
    """The booking's end, or BookingError/SlotTaken. Sites may only take a free time on the grid;
    the team may also book outside opening hours when they say so."""
    if not service.active:
        raise BookingError("that service can't be booked any more")
    start = utc(start)
    end = start + timedelta(minutes=service.duration_minutes)
    if start < now:
        raise BookingError("that time has passed")
    if public:
        local_day = start.astimezone(zone(s.timezone)).date()
        if start not in free_times(db, s, service, local_day, now, public=True):
            if overlapping(db, start, end, skip):
                raise SlotTaken("that time was just taken; pick another")
            raise BookingError("that time can't be booked; pick one of the free times")
        return end
    if not outside_hours:
        local_day = start.astimezone(zone(s.timezone)).date()
        if not any(a <= start and end <= b for a, b in open_ranges(db, s, local_day)):
            raise BookingError("that time is outside opening hours")
    if overlapping(db, start, end, skip):
        raise SlotTaken("another booking holds that time")
    return end
