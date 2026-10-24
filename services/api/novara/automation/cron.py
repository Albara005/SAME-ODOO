"""Cron expressions for scheduled workflows.

Five fields, read in the schedule's own time zone:

    minute (0-59)   hour (0-23)   day of month (1-31)   month (1-12 or jan-dec)   day of week (0-7 or sun-sat)

Each field takes ``*``, a number or name, a range ``a-b``, a step ``*/n`` or ``a-b/n``, and comma lists of
those. As in classic cron, when both the day of month and the day of week are restricted a day matches if
either does. ``@hourly``, ``@daily``, ``@weekly`` and ``@monthly`` are accepted too. A schedule runs at
most every MIN_GAP_MINUTES minutes, so a workflow can't be turned into a busy loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

MIN_GAP_MINUTES = 5
SEARCH_DAYS = 366 * 5  # far enough to find a 29 February

ALIASES = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *"}
MONTHS = {n: i for i, n in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WEEKDAYS = {n: i for i, n in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
FIELDS = (("minute", 0, 59, {}), ("hour", 0, 23, {}), ("day of month", 1, 31, {}), ("month", 1, 12, MONTHS),
          ("day of week", 0, 7, WEEKDAYS))


class CronError(ValueError):
    pass


@dataclass(frozen=True)
class Cron:
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday
    any_day: bool
    any_weekday: bool

    def on(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        dom, dow = day.day in self.days, (day.weekday() + 1) % 7 in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return dow
        if self.any_weekday:
            return dom
        return dom or dow


def _value(token: str, low: int, high: int, names: dict[str, int], field: str) -> int:
    token = token.strip().lower()
    value = names.get(token) if token.isalpha() else (int(token) if token.isdigit() else None)  # "mon", never "monx"
    if value is None or not low <= value <= high:
        raise CronError(f"{field}: '{token}' isn't between {low} and {high}")
    return value


def _field(text: str, low: int, high: int, names: dict[str, int], field: str) -> tuple[set[int], bool]:
    values: set[int] = set()
    for item in text.split(","):
        if not item:
            raise CronError(f"{field}: empty item")
        base, _, step_text = item.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"{field}: step '{step_text}' must be a whole number above 0")
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            a, _, b = base.partition("-")
            start, end = _value(a, low, high, names, field), _value(b, low, high, names, field)
            if start > end:
                raise CronError(f"{field}: range '{base}' runs backwards")
        else:
            start = _value(base, low, high, names, field)
            end = high if step_text else start
        values.update(range(start, end + 1, step))
    return values, text == "*"


def parse(expression: str) -> Cron:
    text = " ".join(expression.split())
    text = ALIASES.get(text.lower(), text)
    parts = text.split(" ")
    if len(parts) != 5:
        raise CronError("a cron expression has five fields: minute hour day-of-month month day-of-week")
    parsed = [_field(p, low, high, names, name) for p, (name, low, high, names) in zip(parts, FIELDS)]
    (minutes, _), (hours, _), (days, any_day), (months, _), (weekdays, any_weekday) = parsed
    weekdays = {0 if d == 7 else d for d in weekdays}
    ordered = sorted(minutes)
    gaps = [b - a for a, b in zip(ordered, ordered[1:])] + [60 - ordered[-1] + ordered[0]]  # wrapping the hour
    if min(gaps) < MIN_GAP_MINUTES:
        raise CronError(f"a schedule runs at most every {MIN_GAP_MINUTES} minutes")
    # Only the day of month restricts the days (a weekday restriction would match on its own): it must exist.
    if not any_day and any_weekday and not any(_real(2024, m, d) for m in months for d in days):
        raise CronError("that day of month never happens in those months")
    return Cron(tuple(ordered), tuple(sorted(hours)), frozenset(days), frozenset(months), frozenset(weekdays),
                any_day, any_weekday)


def _real(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
        return True
    except ValueError:
        return False


def normalise(expression: str) -> str:
    return " ".join(expression.split())


def last_fire(cron: Cron, tz: ZoneInfo, now: datetime) -> datetime | None:
    """The latest time at or before ``now`` (UTC) the expression names, or None if none in five years."""
    local = now.astimezone(tz)
    day = local.date()
    for _ in range(SEARCH_DAYS):
        if cron.on(day):
            for h in reversed(cron.hours):
                if day == local.date() and h > local.hour:
                    continue
                for m in reversed(cron.minutes):
                    if day == local.date() and h == local.hour and m > local.minute:
                        continue
                    fire = datetime.combine(day, time(h, m), tz).astimezone(timezone.utc)
                    if fire <= now:  # a wall time skipped by a clock change can land just after now
                        return fire
        day -= timedelta(days=1)
    return None


def next_fires(cron: Cron, tz: ZoneInfo, after: datetime, count: int = 3) -> list[datetime]:
    """The next ``count`` times strictly after ``after``, in the schedule's time zone."""
    local = after.astimezone(tz)
    day, out = local.date(), []
    for _ in range(SEARCH_DAYS):
        if cron.on(day):
            for h in cron.hours:
                if day == local.date() and h < local.hour:
                    continue
                for m in cron.minutes:
                    if day == local.date() and (h, m) <= (local.hour, local.minute):
                        continue
                    fire = datetime.combine(day, time(h, m), tz)
                    if fire <= after or (out and fire <= out[-1]):
                        continue
                    out.append(fire)
                    if len(out) == count:
                        return out
        day += timedelta(days=1)
    return out
