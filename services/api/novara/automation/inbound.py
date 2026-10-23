"""Inbound webhooks: another system starts a workflow by POSTing JSON to the workflow's address.

    POST /api/v1/hooks/{token}      Content-Type: application/json; a JSON object of at most 64 KB

The token is the secret: whoever holds the address can start the workflow (with nothing but the
data they send), so it is unguessable, stored encrypted (shown again only to workflow managers)
and looked up by its SHA-256. On PostgreSQL the lookup goes through a SECURITY DEFINER function
that only answers which organization holds that hash.

Each call becomes one "webhook.received" event for that workflow, with the JSON under ``data``.
An Idempotency-Key header makes a sender's retries harmless, and a workflow takes at most
RATE_LIMIT calls a minute.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from novara import crypto
from novara.automation.models import OutboxEvent, Workflow
from novara.db import new_id, utcnow
from novara.deps import get_db
from novara.tenancy import bind_tenant, current_tenant, system_scope

TRIGGER = "webhook.received"
MAX_BYTES = 64 * 1024
RATE_LIMIT = 60  # calls per workflow per minute

public_router = APIRouter(tags=["inbound webhooks"])


def hook_token(db: Session, settings, wf: Workflow, *, rotate: bool = False) -> str:
    """The workflow's address token, created on first use (or replaced)."""
    if wf.hook_secret and not rotate:
        return crypto.decrypt(settings, wf.hook_secret, context=current_tenant(db))
    raw = "nvwh_" + secrets.token_urlsafe(32)
    wf.hook_secret = crypto.encrypt(settings, raw, context=current_tenant(db))
    wf.hook_hash = crypto.token_hash(raw)
    db.flush()
    return raw


def _workflow_for(db: Session, raw: str) -> Workflow | None:
    digest = crypto.token_hash(raw)
    if db.get_bind().dialect.name == "postgresql":
        org_id = db.scalar(text("SELECT workflow_hook_org(:h)"), {"h": digest})
    else:
        with system_scope(db):
            org_id = db.scalar(select(Workflow.organization_id).where(Workflow.hook_hash == digest))
    if org_id is None:
        return None
    bind_tenant(db, org_id)
    return db.scalar(select(Workflow).where(Workflow.hook_hash == digest, Workflow.trigger == TRIGGER,
                                            Workflow.enabled.is_(True)))


def _accept(db: Session, token: str, data: dict, key: str | None) -> JSONResponse:
    wf = _workflow_for(db, token)
    if wf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown webhook address")
    prefix = f"hook:{wf.id}:"
    dedup = prefix + (hashlib.sha256(key.encode()).hexdigest()[:32] if key else new_id())

    def duplicate() -> JSONResponse | None:
        existing = db.scalar(select(OutboxEvent.id).where(OutboxEvent.dedup_key == dedup))
        return JSONResponse({"accepted": True, "event_id": existing, "duplicate": True}) if existing else None

    if key and (seen := duplicate()):
        return seen
    now = utcnow()
    recent = db.scalar(select(func.count()).select_from(OutboxEvent).where(
        OutboxEvent.type == TRIGGER, OutboxEvent.dedup_key.like(prefix + "%"),
        OutboxEvent.created_at >= now - timedelta(minutes=1)))
    if recent >= RATE_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many calls to this address; try again in a minute",
                            headers={"Retry-After": "60"})
    event = OutboxEvent(type=TRIGGER, dedup_key=dedup, payload={
        "target_type": "workflow", "target_id": wf.id, "workflow_id": wf.id, "received_at": now.isoformat(), "data": data})
    db.add(event)
    try:
        db.commit()
    except IntegrityError:  # the same key arrived twice at once
        db.rollback()
        bind_tenant(db, wf.organization_id)
        if seen := duplicate():
            return seen
        raise
    return JSONResponse({"accepted": True, "event_id": event.id}, status_code=status.HTTP_202_ACCEPTED)


@public_router.post("/hooks/{token}", status_code=status.HTTP_202_ACCEPTED)
async def receive(token: str, request: Request, idempotency_key: str | None = Header(default=None, max_length=100),
                  db: Session = Depends(get_db)):
    """Start the workflow behind this address with the JSON object sent (202; 200 for a repeated Idempotency-Key)."""
    if len(token) > 100:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown webhook address")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"at most {MAX_BYTES // 1024} KB")
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"at most {MAX_BYTES // 1024} KB")
    if "json" not in request.headers.get("content-type", ""):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "send Content-Type: application/json")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "the body must be JSON")
    if not isinstance(data, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "the body must be a JSON object")
    return await run_in_threadpool(_accept, db, token, data, idempotency_key)
