"""The UAE Wage Protection System's salary file (SIF), for a posted pay run.

Employers in the UAE pay salaries through WPS: the bank or exchange house is given a Salary Information File, one
line per employee and a control line, in the layout the Central Bank set:

    EDR,<employee's 14-digit MOHRE person number>,<9-digit routing code of their bank>,<their IBAN>,
        <pay start YYYY-MM-DD>,<pay end YYYY-MM-DD>,<days in the period>,<fixed income>,<variable income>,<days on leave>
    SCR,<13-digit employer (establishment) id>,<9-digit routing code of the employer's bank>,<file date YYYY-MM-DD>,
        <file time HHMM>,<salary month MMYYYY>,<number of EDR lines>,<total salary>,AED,<employer reference>

named <employer id><YYMMDDHHMMSS>.SIF. What each employee is paid is their net pay; the variable part is the overtime
on their payslip (up to the net), the rest is fixed. Unpaid leave days are the days on leave. Banks sometimes add
their own requirements on top of this layout: check with the bank before the first upload.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from novara.modules.payroll.models import EmployeePay, PayRun, Payslip
from novara.money import from_minor

PERSON = re.compile(r"^\d{14}$")
EMPLOYER = re.compile(r"^\d{13}$")
ROUTING = re.compile(r"^\d{9}$")


class WpsError(ValueError):
    """The file can't be made as things stand (HTTP 422): what is missing, and for whom."""


@dataclass(frozen=True)
class SalaryFile:
    name: str
    content: str


def now() -> datetime:
    """The file's date and time are the UAE's."""
    return datetime.now(ZoneInfo("Asia/Dubai"))


def _money(minor: int) -> str:
    return f"{from_minor(minor, 'AED'):.2f}"


def build(run: PayRun, slips: list[Payslip], pays: dict[str, EmployeePay], *, employer_id: str, bank_code: str,
          now: datetime, reference: str = "") -> SalaryFile:
    if run.currency != "AED":
        raise WpsError("the WPS salary file is for salaries paid in UAE dirhams (AED)")
    if not EMPLOYER.match(employer_id or "") or not ROUTING.match(bank_code or ""):
        raise WpsError("add the employer's 13-digit establishment id and its bank's 9-digit routing code in the payroll "
                       "settings")
    missing, lines, total = [], [], 0
    for s in slips:
        if s.net_minor <= 0:
            continue
        pay = pays.get(s.employee_id)
        problems = [what for what, ok in (
            ("14-digit person number", pay is not None and PERSON.match(pay.wps_person_id or "")),
            ("9-digit bank routing code", pay is not None and ROUTING.match(pay.wps_routing_code or "")),
            ("IBAN", bool(s.iban)),
        ) if not ok]
        if problems:
            missing.append(f"{s.employee_name} ({', '.join(problems)})")
            continue
        overtime = sum(l["amount_minor"] for l in s.lines if l.get("minutes"))
        variable = min(overtime, s.net_minor)
        leave = int(Decimal(s.unpaid_leave_days).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        lines.append(",".join(["EDR", pay.wps_person_id, pay.wps_routing_code, s.iban, run.period_start.isoformat(),
                               run.period_end.isoformat(), str(s.days_in_period), _money(s.net_minor - variable),
                               _money(variable), str(leave)]))
        total += s.net_minor
    if missing:
        raise WpsError("add to each employee's pay: " + "; ".join(missing))
    if not lines:
        raise WpsError("nobody is paid in this run")
    ref = re.sub(r"[^A-Za-z0-9 -]", "", reference)[:35]
    lines.append(",".join(["SCR", employer_id, bank_code, now.strftime("%Y-%m-%d"), now.strftime("%H%M"),
                           run.period_start.strftime("%m%Y"), str(len(lines)), _money(total), "AED", ref]))
    return SalaryFile(name=f"{employer_id}{now:%y%m%d%H%M%S}.SIF", content="\r\n".join(lines) + "\r\n")
