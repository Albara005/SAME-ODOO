"""Background worker: runs workflow automation for every organization that has workflows.

    python -m novara.worker            # loop forever (every 30 s)
    python -m novara.worker --once     # one pass, e.g. from cron

Each organization is processed in its own session bound to that organization, so tenant
isolation (and PostgreSQL row-level security) applies to the worker too.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from novara.automation import engine
from novara.automation.events import scan_overdue, scan_schedules
from novara.automation.models import Workflow
from novara.db import utcnow
from novara.einvoice import service as einvoice
from novara.tenancy import bind_tenant, system_scope

log = logging.getLogger("novara.worker")


def organizations_with_workflows(db: Session) -> list[str]:
    if db.get_bind().dialect.name == "postgresql":
        # SECURITY DEFINER function (migration 0009): returns only organization ids.
        return list(db.execute(text("SELECT app_orgs_with_enabled_workflows()")).scalars())
    with system_scope(db):
        return list(db.scalars(select(Workflow.organization_id).where(Workflow.enabled.is_(True)).distinct()))


def run_once(sessionmaker, provider, today: date | None = None, webhooks=None) -> dict[str, dict]:
    with sessionmaker() as db:
        org_ids = organizations_with_workflows(db)
    results = {}
    for org_id in org_ids:
        with sessionmaker() as db:
            bind_tenant(db, org_id)
            try:
                emitted = scan_overdue(db, today or date.today())
                scheduled = scan_schedules(db, utcnow())
                db.commit()
                results[org_id] = {"overdue_events": emitted, "scheduled_events": scheduled,
                                   **engine.process(db, provider=provider, webhooks=webhooks)}
            except Exception:  # noqa: BLE001 - one organization's failure must not stop the others
                log.exception("automation failed for organization %s", org_id)
                db.rollback()
    return results


def send_einvoices(sessionmaker, settings, http=None) -> dict[str, int]:
    """Send e-invoices still waiting for ZATCA (it didn't answer when they were issued)."""
    with sessionmaker() as db:
        org_ids = einvoice.organizations_with_pending(db)
    sent = {}
    for org_id in org_ids:
        with sessionmaker() as db:
            bind_tenant(db, org_id)
            try:
                sent[org_id] = einvoice.send_pending(db, settings, http)
                db.commit()
            except Exception:  # noqa: BLE001 - one organization's failure must not stop the others
                log.exception("sending e-invoices failed for organization %s", org_id)
                db.rollback()
    return sent


def remind_certificates(sessionmaker) -> dict[str, int]:
    """Tell organizations whose e-invoicing certificate expires soon (or has); each reminder goes once."""
    with sessionmaker() as db:
        org_ids = einvoice.organizations_onboarded(db)
    added = {}
    for org_id in org_ids:
        with sessionmaker() as db:
            bind_tenant(db, org_id)
            try:
                n = einvoice.remind_expiry(db)
                db.commit()
                if n:
                    added[org_id] = n
            except Exception:  # noqa: BLE001 - one organization's failure must not stop the others
                log.exception("certificate reminders failed for organization %s", org_id)
                db.rollback()
    return added


FX_FEED_EVERY = 3600.0       # seconds between fetches of the ECB's rates


def fetch_rates(sessionmaker, http=None) -> dict[str, list[str]]:
    """Save the day's ECB rates for every organization that keeps them (the feed is fetched once for all)."""
    from novara.modules.invoicing import fx_feed

    with sessionmaker() as db:
        org_ids = fx_feed.organizations_with_feed(db)
    if not org_ids:
        return {}
    try:
        rates = fx_feed.fetch(http)
    except fx_feed.FeedError as e:
        log.warning("exchange-rate feed: %s", e)
        return {}
    saved = {}
    for org_id in org_ids:
        with sessionmaker() as db:
            bind_tenant(db, org_id)
            try:
                feed = fx_feed.feed_of(db)
                if feed is not None and feed.enabled and feed.last_day != rates.day:
                    saved[org_id] = sorted(fx_feed.apply(db, feed, rates).saved)
                db.commit()
            except Exception:  # noqa: BLE001 - one organization's failure must not stop the others
                log.exception("exchange-rate feed failed for organization %s", org_id)
                db.rollback()
    return saved


def main() -> None:
    from novara.main import create_app

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    last_fx = 0.0
    while True:
        log.info("processed: %s", run_once(app.state.sessionmaker, app.state.ai_provider,
                                           webhooks=app.state.webhooks))
        log.info("e-invoices sent: %s", send_einvoices(app.state.sessionmaker, app.state.settings))
        reminded = remind_certificates(app.state.sessionmaker)
        if reminded:
            log.info("certificate reminders: %s", reminded)
        if time.monotonic() - last_fx >= FX_FEED_EVERY:
            last_fx = time.monotonic()
            log.info("exchange rates saved: %s", fetch_rates(app.state.sessionmaker))
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
