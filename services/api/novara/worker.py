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


def main() -> None:
    from novara.main import create_app

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    while True:
        log.info("processed: %s", run_once(app.state.sessionmaker, app.state.ai_provider,
                                           webhooks=app.state.webhooks))
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
