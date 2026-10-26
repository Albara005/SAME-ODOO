from __future__ import annotations

from novara.db import utcnow
from novara.hosting.models import HostingRequest

KINDS = ("dedicated_database", "dedicated_environment")
SIZES = ("small", "medium", "large")
REGIONS = ("me-central", "eu-central", "us-east")

# PLACEHOLDER monthly prices in USD cents. Set real prices (from actual infrastructure cost)
# before offering this to customers; the plan treats pricing as undecided.
PRICES_CENTS = {
    ("dedicated_database", "small"): 15_000, ("dedicated_database", "medium"): 40_000,
    ("dedicated_database", "large"): 90_000,
    ("dedicated_environment", "small"): 60_000, ("dedicated_environment", "medium"): 150_000,
    ("dedicated_environment", "large"): 350_000,
}
SPECS = {
    "small": "2 vCPU, 8 GB RAM, 100 GB storage, daily backups (30-day retention)",
    "medium": "4 vCPU, 16 GB RAM, 250 GB storage, daily backups (30-day retention)",
    "large": "8 vCPU, 32 GB RAM, 500 GB storage, hourly backups (35-day retention)",
}


class HostingError(ValueError):
    pass


def quote(kind: str, size: str, region: str) -> int:
    if kind not in KINDS or size not in SIZES or region not in REGIONS:
        raise HostingError(f"choose kind {KINDS}, size {SIZES}, region {REGIONS}")
    return PRICES_CENTS[(kind, size)]


def approve(req: HostingRequest, *, user_id: str, confirmed_monthly_cents: int) -> None:
    """The owner must confirm the exact quoted monthly cost - an explicit acknowledgement, not a click-through."""
    if req.status != "pending_approval":
        raise HostingError(f"request is {req.status}")
    if confirmed_monthly_cents != req.quoted_monthly_cents:
        raise HostingError("the confirmed monthly cost doesn't match the quote")
    req.status, req.approved_by, req.approved_at = "approved", user_id, utcnow()


def cancel(req: HostingRequest) -> None:
    if req.status not in ("pending_approval", "approved"):
        raise HostingError(f"a request that is {req.status} can't be cancelled here; contact support")
    req.status = "cancelled"
