"""Inbound webhooks: another system starts a workflow by POSTing JSON to the workflow's address.

    POST /api/v1/hooks/{token}      Content-Type: application/json; a JSON object of at most 64 KB

The token is the secret: whoever holds the address can start the workflow (with nothing but the
data they send), so it is unguessable, stored encrypted (shown again only to workflow managers)
and looked up by its SHA-256. On PostgreSQL the lookup goes through a SECURITY DEFINER function
that only answers which organization holds that hash.

Each call becomes one "webhook.received" event for that workflow, with the JSON under ``data``.
An Idempotency-Key header makes a sender's retries harmless, and a workflow takes at most
RATE_LIMIT calls a minute.

A workflow can also require signed calls. With a signing secret set, a call must carry either

    X-Novara-Timestamp: <unix seconds>
    X-Novara-Signature: v1=<hex HMAC-SHA256 of "<timestamp>.<body>">     (as NOVARA signs its own)

with the timestamp within SIGNATURE_WINDOW seconds of now, or the common body-only form

    X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the body>

which has no timestamp, so a captured call could be replayed: senders that can should use the
first form (or send an Idempotency-Key). Anything else is refused with 401 before it counts
against the rate limit.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
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
SIGNATURE_WINDOW = 300  # seconds a timestamped signature stays valid, either side of now

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


def signing_secret(db: Session, settings, wf: Workflow) -> str | None:
    if not wf.hook_signing_secret:
        return None
    return crypto.decrypt(settings, wf.hook_signing_secret, context=current_tenant(db))


def new_signing_secret(db: Session, settings, wf: Workflow) -> str:
    raw = "whsec_" + secrets.token_urlsafe(32)
    wf.hook_signing_secret = crypto.encrypt(settings, raw, context=current_tenant(db))
    db.flush()
    return raw


def signed(secret: str, body: bytes, headers, now: float | None = None) -> bool:
    """Whether the call carries a valid signature in either accepted form (compared in constant time)."""
    key = secret.encode()
    stamp, sig = headers.get("x-novara-timestamp", ""), headers.get("x-novara-signature", "")
    if stamp and sig:
        if not stamp.isdigit() or abs((time.time() if now is None else now) - int(stamp)) > SIGNATURE_WINDOW:
            return False
        want = "v1=" + hmac.new(key, f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(want, part.strip()) for part in sig.split(","))
    hub = headers.get("x-hub-signature-256", "")
    if hub:
        return hmac.compare_digest("sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest(), hub.strip())
    return False


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


def _accept(db: Session, settings, token: str, data: dict, key: str | None, body: bytes, headers) -> JSONResponse:
    wf = _workflow_for(db, token)
    if wf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown webhook address")
    secret = signing_secret(db, settings, wf)
    if secret is not None and not signed(secret, body, headers):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "this address only takes signed calls; the signature is "
                                                          "missing, wrong or too old")
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
    return await run_in_threadpool(_accept, db, request.app.state.settings, token, data, idempotency_key, raw,
                                   request.headers)
