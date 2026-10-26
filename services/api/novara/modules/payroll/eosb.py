"""End-of-service benefits: what an employee is owed when they leave, under the rules of the organization's market.

    saudi  Saudi Labor Law, articles 84 and 85: half a month's wage for each of the first five years of service and a
           full month for each year after, in proportion for part of a year. Someone who resigns gets nothing before
           two years of service, a third of it from two to five years, two thirds from five to ten and all of it from
           ten. Article 87 keeps the whole award for some resignations (force majeure; a woman leaving within six
           months of marrying or three months of giving birth), and article 80 dismissals get none.
    uae    UAE Labour Law (Federal Decree-Law 33 of 2021, article 51): 21 days' wage for each of the first five years
           and 30 days for each year after, nothing before one year of service, at most two years' wage. Resigning
           doesn't reduce it.
    none   no end-of-service benefit is accrued or settled.

The wage is the monthly total of the employee's earnings marked as counting for end of service (in Saudi Arabia
usually the basic salary and housing allowance; in the UAE the basic salary). Service runs from the hire date to the
last day, both included, counted in days over 365. Amounts are worked out as exact fractions and rounded once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from fractions import Fraction

RULES = ("saudi", "uae", "none")
REASONS = {
    "saudi": ("termination", "contract_end", "resignation", "resignation_full", "dismissal"),
    "uae": ("termination", "contract_end", "resignation"),
    "none": (),
}
YEAR = 365


class EndOfServiceError(ValueError):
    pass


def service_days(hire: date, last: date) -> int:
    if last < hire:
        raise EndOfServiceError("the last day is before the hire date")
    return (last - hire).days + 1


def months_owed(rule: str, days: int) -> Fraction:
    """Months of wage the full award is worth after this much service."""
    years = Fraction(days, YEAR)
    if rule == "saudi":
        return min(years, 5) * Fraction(1, 2) + max(years - 5, 0)
    if rule == "uae":
        if years < 1:
            return Fraction(0)
        return min(min(years, 5) * Fraction(21, 30) + max(years - 5, 0), Fraction(24))
    return Fraction(0)


def share(rule: str, reason: str, days: int) -> Fraction:
    """The part of the full award the reason for leaving allows."""
    if reason not in REASONS.get(rule, ()):
        raise EndOfServiceError(f"'{reason}' isn't a reason for leaving under the {rule} rules")
    if reason == "dismissal":
        return Fraction(0)
    if rule == "saudi" and reason == "resignation":
        years = Fraction(days, YEAR)
        return Fraction(0) if years < 2 else Fraction(1, 3) if years < 5 else Fraction(2, 3) if years < 10 else Fraction(1)
    return Fraction(1)


def round_half_up(x: Fraction) -> int:
    return int(x + Fraction(1, 2)) if x >= 0 else -int(-x + Fraction(1, 2))


@dataclass(frozen=True)
class Award:
    days: int
    months: Fraction            # months of wage the full award is worth
    share: Fraction             # the part the reason for leaving allows
    full_minor: int             # the full award (what the provision holds)
    award_minor: int            # what is owed

    @property
    def years(self) -> Fraction:
        return Fraction(self.days, YEAR)


def award(rule: str, wage_minor: int, hire: date, last: date, reason: str = "termination") -> Award:
    days = service_days(hire, last)
    months = months_owed(rule, days)
    part = share(rule, reason, days) if rule != "none" else Fraction(0)
    return Award(days=days, months=months, share=part, full_minor=round_half_up(wage_minor * months),
                 award_minor=round_half_up(wage_minor * months * part))


def liability(rule: str, wage_minor: int, hire: date | None, on: date) -> tuple[int, int]:
    """(days of service, the full award) as of a date: what the provision should hold for the employee."""
    if rule == "none" or hire is None or on < hire or wage_minor <= 0:
        return (service_days(hire, on) if hire and on >= hire else 0), 0
    a = award(rule, wage_minor, hire, on)
    return a.days, a.full_minor


def leave_pay(wage_minor: int, days: Fraction) -> int:
    """Unused leave paid out at a day's wage (the monthly wage over 30)."""
    return round_half_up(wage_minor * days / 30)
