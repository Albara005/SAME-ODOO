"""Platform operator commands. Not exposed through the API: these run on the operators'
side, against the database, by people with infrastructure access.

    python -m novara.ops hosting-list
    python -m novara.ops hosting-start <request_id>
    python -m novara.ops hosting-deliver <request_id> --note "db host pg-ded-12, migrated 2026-05-01"
    python -m novara.ops hosting-reject <request_id> --note "region not available"

Delivering changes the organization's hosting mode only after the operators have actually
provisioned and migrated it; this command records that, it doesn't provision anything.

On PostgreSQL set NOVARA_OPS_DATABASE_URL to an operator role (e.g. the schema owner):
the API's runtime role can't, by design, look up a row before knowing its organization.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from novara.hosting.models import HostingRequest
from novara.modules.audit import service as audit
from novara.modules.organizations.models import Organization
from novara.tenancy import bind_tenant, system_scope

TRANSITIONS = {"hosting-start": ("approved", "provisioning"), "hosting-deliver": ("provisioning", "delivered"),
               "hosting-reject": (None, "rejected")}


def transition(sessionmaker, command: str, request_id: str, note: str = "", operator: str = "operator") -> HostingRequest:
    with sessionmaker() as db:
        with system_scope(db):
            req = db.get(HostingRequest, request_id)
        if req is None:
            raise SystemExit(f"no hosting request {request_id}")
        bind_tenant(db, req.organization_id)
        required, target = TRANSITIONS[command]
        if required is not None and req.status != required:
            raise SystemExit(f"request is {req.status}; {command} needs {required}")
        if command == "hosting-reject" and req.status in ("delivered", "rejected", "cancelled"):
            raise SystemExit(f"request is already {req.status}")
        req.status, req.operator_note = target, note
        if target == "delivered":
            db.get(Organization, req.organization_id).hosting_mode = req.kind
        audit.record(db, actor_user_id=None, action=f"hosting.{target}", target_type="hosting_request",
                     target_id=req.id, details={"operator": operator, "note": note, "kind": req.kind})
        db.commit()
        return req


def main() -> None:
    import os

    from novara.db import make_engine, make_sessionmaker
    from novara.main import create_app

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["hosting-list", *TRANSITIONS])
    parser.add_argument("request_id", nargs="?")
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    app = create_app()
    ops_url = os.environ.get("NOVARA_OPS_DATABASE_URL")
    sessionmaker = make_sessionmaker(make_engine(ops_url)) if ops_url else app.state.sessionmaker
    if args.command == "hosting-list":
        with sessionmaker() as db, system_scope(db):
            for r in db.scalars(select(HostingRequest).where(HostingRequest.status.in_(("approved", "provisioning")))):
                print(f"{r.id}  {r.status:12}  org={r.organization_id}  {r.kind}/{r.size}/{r.region}  "
                      f"${r.quoted_monthly_cents / 100:,.2f}/month")
        return
    req = transition(sessionmaker, args.command, args.request_id, args.note)
    print(f"{req.id}: {req.status}")


if __name__ == "__main__":
    main()
