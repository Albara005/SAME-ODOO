from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from novara.automation.models import Notification
from novara.db import utcnow
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.access.service import holders
from novara.modules.accounting.service import next_number
from novara.modules.audit import service as audit
from novara.modules.bookings import service as svc
from novara.modules.bookings.models import BookableService, Booking, OpeningHours
from novara.modules.organizations.models import Organization
from novara.modules.partners.models import Customer
from novara.money import fmt_decimal
from novara.studio.models import AppDataKey, AppProject

router = APIRouter(tags=["bookings"])

KEY_LIMIT = 30  # site requests per key per hour
IP_LIMIT = 5    # per address per ten minutes
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# ---------------------------------------------------------------- schemas


class OpeningHoursIn(BaseModel):
    weekday: int = Field(ge=0, le=6)
    opens_at: str
    closes_at: str

    @field_validator("opens_at", "closes_at")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not HHMM.match(v):
            raise ValueError("use HH:MM, 24-hour")
        return v


class BookingSettingsIn(BaseModel):
    timezone: str = Field(min_length=1, max_length=64)
    slot_minutes: int = Field(default=30)
    notice_hours: int = Field(default=2, ge=0, le=720)
    days_ahead: int = Field(default=60, gt=0, le=366)
    hours: list[OpeningHoursIn] = Field(default_factory=list, max_length=50)

    @field_validator("slot_minutes")
    @classmethod
    def _slot(cls, v: int) -> int:
        if v not in (15, 30, 60):
            raise ValueError("bookings start every 15, 30 or 60 minutes")
        return v


class BookingSettingsOut(BaseModel):
    timezone: str
    slot_minutes: int
    notice_hours: int
    days_ahead: int
    hours: list[OpeningHoursIn]


class BookableServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    duration_minutes: int = Field(gt=0, le=720)
    price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)


class BookableServiceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    duration_minutes: int | None = Field(default=None, gt=0, le=720)
    price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    active: bool | None = None


class BookableServiceOut(BaseModel):
    id: str
    name: str
    description: str
    duration_minutes: int
    price: str | None
    active: bool


class BookingIn(BaseModel):
    service_id: str
    starts_at: datetime
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=1000)
    customer_id: str | None = None
    outside_hours: bool = False  # the team may book outside opening hours on purpose

    @field_validator("starts_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("give the time with its offset, e.g. 2026-11-02T09:00:00+03:00")
        return v


class BookingDecisionIn(BaseModel):
    note: str = Field(default="", max_length=500)


class BookingOut(BaseModel):
    id: str
    number: str
    service_id: str
    service_name: str
    starts_at: datetime
    ends_at: datetime
    status: str
    name: str
    email: str
    phone: str
    note: str
    customer_id: str | None
    source: str
    decision_note: str
    created_at: datetime


class FreeTimesOut(BaseModel):
    timezone: str
    times: list[datetime]


class PublicBookingIn(BaseModel):
    service_id: str = Field(max_length=36)
    starts_at: datetime
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=1000)
    website: str = Field(default="", max_length=200)  # left empty by people, filled in by bots

    @field_validator("starts_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("give the time with its offset, as the free times list it")
        return v


class RateLimited(RuntimeError):
    pass


# ---------------------------------------------------------------- helpers


def _service_out(x: BookableService) -> BookableServiceOut:
    return BookableServiceOut(id=x.id, name=x.name, description=x.description, duration_minutes=x.duration_minutes,
                      price=fmt_decimal(x.price) if x.price is not None else None, active=x.active)


def _out(b: Booking, names: dict[str, str]) -> BookingOut:
    return BookingOut(id=b.id, number=b.number, service_id=b.service_id, service_name=names.get(b.service_id, ""),
                      starts_at=svc.utc(b.starts_at), ends_at=svc.utc(b.ends_at), status=b.status, name=b.name,
                      email=b.email, phone=b.phone, note=b.note, customer_id=b.customer_id, source=b.source,
                      decision_note=b.decision_note, created_at=b.created_at)


def _names(db: Session) -> dict[str, str]:
    return {x.id: x.name for x in db.scalars(select(BookableService))}


def _service(db: Session, service_id: str) -> BookableService:
    x = db.get(BookableService, service_id)
    if x is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "service not found")
    return x


def _settings_out(db: Session) -> BookingSettingsOut:
    s = svc.current(db)
    hours = [OpeningHoursIn(weekday=h.weekday, opens_at=h.opens_at, closes_at=h.closes_at)
             for h in db.scalars(select(OpeningHours).order_by(OpeningHours.weekday, OpeningHours.opens_at))]
    return BookingSettingsOut(timezone=s.timezone, slot_minutes=s.slot_minutes, notice_hours=s.notice_hours,
                       days_ahead=s.days_ahead, hours=hours)


def _say(org: Organization, ar: str, en: str) -> str:
    return ar if org.default_locale == "ar" else en


def _local(b: Booking, tz_name: str) -> str:
    return svc.utc(b.starts_at).astimezone(svc.zone(tz_name)).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- settings and services


@router.get("/orgs/{org_id}/bookings/settings", response_model=BookingSettingsOut)
def get_settings(_: OrgContext = Depends(require(P.BOOKINGS_READ)), db: Session = Depends(get_db)):
    return _settings_out(db)


@router.put("/orgs/{org_id}/bookings/settings", response_model=BookingSettingsOut)
def put_settings(body: BookingSettingsIn, request: Request, ctx: OrgContext = Depends(require(P.BOOKINGS_MANAGE)),
                 db: Session = Depends(get_db)):
    """The booking time zone, how often bookings may start, the notice and window for sites, and the
    weekly opening hours (replaced as a whole). Bookings already made keep their times."""
    try:
        svc.zone(body.timezone)
    except svc.BookingError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    by_day: dict[int, list[OpeningHoursIn]] = {}
    for h in body.hours:
        if h.opens_at >= h.closes_at:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "each range must close after it opens")
        by_day.setdefault(h.weekday, []).append(h)
    for ranges in by_day.values():
        ranges.sort(key=lambda h: h.opens_at)
        if any(a.closes_at > b.opens_at for a, b in zip(ranges, ranges[1:])):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "opening ranges on the same day overlap")
    s = svc.locked(db)
    s.timezone, s.slot_minutes, s.notice_hours, s.days_ahead = (body.timezone, body.slot_minutes, body.notice_hours,
                                                                 body.days_ahead)
    db.execute(delete(OpeningHours))
    db.add_all(OpeningHours(weekday=h.weekday, opens_at=h.opens_at, closes_at=h.closes_at) for h in body.hours)
    audit.record(db, actor_user_id=ctx.user.id, action="booking.settings_changed", target_type="organization",
                 target_id=ctx.organization.id, details={"timezone": body.timezone, "ranges": len(body.hours)},
                 ip_address=client_ip(request))
    db.commit()
    return _settings_out(db)


@router.get("/orgs/{org_id}/bookings/services", response_model=list[BookableServiceOut])
def list_services(_: OrgContext = Depends(require(P.BOOKINGS_READ)), db: Session = Depends(get_db)):
    return [_service_out(x) for x in db.scalars(select(BookableService).order_by(BookableService.active.desc(),
                                                                                 BookableService.name))]


@router.post("/orgs/{org_id}/bookings/services", response_model=BookableServiceOut, status_code=status.HTTP_201_CREATED)
def create_service(body: BookableServiceIn, request: Request, ctx: OrgContext = Depends(require(P.BOOKINGS_MANAGE)),
                   db: Session = Depends(get_db)):
    if db.scalar(select(BookableService.id).where(BookableService.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "a service with that name exists")
    x = BookableService(**body.model_dump())
    db.add(x)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="booking.service_created", target_type="bookable_service",
                 target_id=x.id, details={"name": x.name, "minutes": x.duration_minutes}, ip_address=client_ip(request))
    db.commit()
    return _service_out(x)


@router.patch("/orgs/{org_id}/bookings/services/{service_id}", response_model=BookableServiceOut)
def update_service(service_id: str, body: BookableServiceUpdate, request: Request,
                   ctx: OrgContext = Depends(require(P.BOOKINGS_MANAGE)), db: Session = Depends(get_db)):
    """A new length applies to new bookings; existing ones keep their times."""
    x = db.get(BookableService, service_id)
    if x is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "service not found")
    changes = body.model_dump(exclude_unset=True)
    for required in ("name", "description", "duration_minutes", "active"):
        if required in changes and changes[required] is None:
            del changes[required]
    if changes.get("name") and db.scalar(select(BookableService.id).where(BookableService.name == changes["name"],
                                                                           BookableService.id != x.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "a service with that name exists")
    for k, v in changes.items():
        setattr(x, k, v)
    audit.record(db, actor_user_id=ctx.user.id, action="booking.service_updated", target_type="bookable_service",
                 target_id=x.id, details={k: (fmt_decimal(v) if isinstance(v, Decimal) else v) for k, v in changes.items()},
                 ip_address=client_ip(request))
    db.commit()
    return _service_out(x)


# ---------------------------------------------------------------- the calendar


@router.get("/orgs/{org_id}/bookings", response_model=list[BookingOut])
def list_bookings(start: date | None = None, end: date | None = None,
                  status_filter: str | None = Query(default=None, alias="status",
                                                    pattern="^(requested|confirmed|declined|cancelled)$"),
                  _: OrgContext = Depends(require(P.BOOKINGS_READ)), db: Session = Depends(get_db)):
    """Bookings starting between two days (inclusive, in the booking time zone); this week by default."""
    s = svc.current(db)
    tz = svc.zone(s.timezone)
    start = start or datetime.now(tz).date()
    end = end or start + timedelta(days=6)
    if end < start or (end - start).days > 92:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "pick a range of up to three months")
    lo = datetime.combine(start, datetime.min.time(), tz).astimezone(timezone.utc)
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tz).astimezone(timezone.utc)
    stmt = select(Booking).where(Booking.starts_at >= lo, Booking.starts_at < hi)
    if status_filter:
        stmt = stmt.where(Booking.status == status_filter)
    names = _names(db)
    return [_out(b, names) for b in db.scalars(stmt.order_by(Booking.starts_at).limit(1000))]


@router.get("/orgs/{org_id}/bookings/requests", response_model=list[BookingOut])
def waiting_requests(_: OrgContext = Depends(require(P.BOOKINGS_READ)), db: Session = Depends(get_db)):
    """Requests from sites still waiting for someone, soonest first."""
    names = _names(db)
    return [_out(b, names) for b in db.scalars(select(Booking).where(Booking.status == "requested")
                                                .order_by(Booking.starts_at).limit(500))]


@router.get("/orgs/{org_id}/bookings/free-times", response_model=FreeTimesOut)
def staff_free_times(service_id: str, day: date, _: OrgContext = Depends(require(P.BOOKINGS_READ)),
                     db: Session = Depends(get_db)):
    s = svc.current(db)
    times = svc.free_times(db, s, _service(db, service_id), day, utcnow(), public=False)
    tz = svc.zone(s.timezone)
    return FreeTimesOut(timezone=s.timezone, times=[t.astimezone(tz) for t in times])


@router.post("/orgs/{org_id}/bookings", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
def book(body: BookingIn, request: Request, ctx: OrgContext = Depends(require(P.BOOKINGS_MANAGE)),
         db: Session = Depends(get_db)):
    """The team books someone in: confirmed at once."""
    x = _service(db, body.service_id)
    if body.customer_id and db.get(Customer, body.customer_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "customer not found")
    s = svc.locked(db)
    try:
        end = svc.check(db, s, x, body.starts_at, utcnow(), public=False, outside_hours=body.outside_hours)
    except svc.SlotTaken as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except svc.BookingError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    year = svc.utc(body.starts_at).year
    b = Booking(number=next_number(db, f"booking:{year}", f"BK-{year}-"), service_id=x.id,
                starts_at=svc.utc(body.starts_at), ends_at=end, status="confirmed", name=body.name.strip(),
                email=str(body.email or ""), phone=body.phone.strip(), note=body.note.strip(),
                customer_id=body.customer_id, created_by=ctx.user.id, decided_by=ctx.user.id, decided_at=utcnow())
    db.add(b)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="booking.confirmed", target_type="booking", target_id=b.id,
                 details={"number": b.number, "service": x.name, "starts_at": _local(b, s.timezone), "name": b.name},
                 ip_address=client_ip(request))
    db.commit()
    return _out(b, {x.id: x.name})


def _pending(db: Session, booking_id: str, allowed: tuple[str, ...]) -> Booking:
    svc.locked(db)
    b = db.get(Booking, booking_id)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "booking not found")
    db.refresh(b)
    if b.status not in allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, f"this booking is already {b.status}")
    return b


@router.post("/orgs/{org_id}/bookings/{booking_id}/confirm", response_model=BookingOut)
def confirm(booking_id: str, request: Request, ctx: OrgContext = Depends(require(P.BOOKINGS_MANAGE)),
            db: Session = Depends(get_db)):
    b = _pending(db, booking_id, ("requested",))
    b.status, b.decided_by, b.decided_at = "confirmed", ctx.user.id, utcnow()
    names = _names(db)
    audit.record(db, actor_user_id=ctx.user.id, action="booking.confirmed", target_type="booking", target_id=b.id,
                 details={"number": b.number, "service": names.get(b.service_id, ""),
                          "starts_at": _local(b, svc.current(db).timezone), "name": b.name},
                 ip_address=client_ip(request))
    db.commit()
    return _out(b, names)


@router.post("/orgs/{org_id}/bookings/{booking_id}/decline", response_model=BookingOut)
def decline(booking_id: str, body: BookingDecisionIn, request: Request, ctx: OrgContext = Depends(require(P.BOOKINGS_MANAGE)),
            db: Session = Depends(get_db)):
    """Turn down a site's request; its time is free again."""
    b = _pending(db, booking_id, ("requested",))
    b.status, b.decided_by, b.decided_at, b.decision_note = "declined", ctx.user.id, utcnow(), body.note
    audit.record(db, actor_user_id=ctx.user.id, action="booking.declined", target_type="booking", target_id=b.id,
                 details={"number": b.number}, ip_address=client_ip(request))
    db.commit()
    return _out(b, _names(db))


@router.post("/orgs/{org_id}/bookings/{booking_id}/cancel", response_model=BookingOut)
def cancel(booking_id: str, body: BookingDecisionIn, request: Request, ctx: OrgContext = Depends(require(P.BOOKINGS_MANAGE)),
           db: Session = Depends(get_db)):
    """Cancel a request or a confirmed booking; its time is free again."""
    b = _pending(db, booking_id, svc.LIVE)
    was = b.status
    b.status, b.decision_note = "cancelled", body.note or b.decision_note
    audit.record(db, actor_user_id=ctx.user.id, action="booking.cancelled", target_type="booking", target_id=b.id,
                 details={"number": b.number, "was": was}, ip_address=client_ip(request))
    db.commit()
    return _out(b, _names(db))


# ---------------------------------------------------------------- requests from published sites


def public_services(db: Session) -> list[dict]:
    return [{"id": x.id, "name": x.name, "description": x.description, "duration_minutes": x.duration_minutes,
             "price": fmt_decimal(x.price) if x.price is not None else None}
            for x in db.scalars(select(BookableService).where(BookableService.active.is_(True))
                                .order_by(BookableService.name))]


def public_free_times(db: Session, service_id: str, day: date) -> tuple[str, list[datetime]]:
    s = svc.current(db)
    x = db.get(BookableService, service_id)
    if x is None or not x.active:
        raise svc.BookingError("that service can't be booked")
    tz = svc.zone(s.timezone)
    return s.timezone, [t.astimezone(tz) for t in svc.free_times(db, s, x, day, utcnow(), public=True)]


def receive(db: Session, key: AppDataKey, body: PublicBookingIn, *, ip: str) -> Booking:
    """Hold a free time for a site's visitor until the team confirms or declines it."""
    if not (body.email or body.phone.strip()):
        raise svc.BookingError("give an email address or a phone number so we can confirm")
    now = utcnow()
    per_key = db.scalar(select(func.count()).select_from(Booking).where(
        Booking.key_id == key.id, Booking.created_at >= now - timedelta(hours=1)))
    per_ip = db.scalar(select(func.count()).select_from(Booking).where(
        Booking.ip_address == ip, Booking.created_at >= now - timedelta(minutes=10))) if ip else 0
    if per_key >= KEY_LIMIT or per_ip >= IP_LIMIT:
        raise RateLimited("too many booking requests; try again later")
    x = db.get(BookableService, body.service_id)
    if x is None:
        raise svc.BookingError("that service can't be booked")
    s = svc.locked(db)
    end = svc.check(db, s, x, body.starts_at, now, public=True)
    project = db.get(AppProject, key.project_id)
    start = svc.utc(body.starts_at)
    b = Booking(number=next_number(db, f"booking:{start.year}", f"BK-{start.year}-"), service_id=x.id,
                starts_at=start, ends_at=end, status="requested", name=body.name.strip(), email=str(body.email or ""),
                phone=body.phone.strip(), note=body.note.strip(), source=(project.name if project else "")[:120],
                key_id=key.id, ip_address=ip)
    db.add(b)
    db.flush()
    when = _local(b, s.timezone)
    audit.record(db, actor_user_id=None, action="booking.requested", target_type="booking", target_id=b.id,
                 details={"number": b.number, "service": x.name, "starts_at": when, "name": b.name, "site": b.source},
                 ip_address=ip)
    org = db.get(Organization, key.organization_id)
    for uid in holders(db, P.BOOKINGS_MANAGE):
        db.add(Notification(user_id=uid, title=_say(org, f"طلب حجز من {b.name}", f"Booking request from {b.name}")[:300],
                            body=f"{x.name}: {when}", idempotency_key=f"booking:{b.id}:{uid}"))
    return b
