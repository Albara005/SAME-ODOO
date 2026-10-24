"""Live updates for the signed-in user over server-sent events (SSE).

The stream sends the user's counts (unread notifications, approvals they can give, open tasks)
whenever they change, plus the newest unread notification. Each check opens its own short
database session, re-checks the login session and the membership, and is bound to the
organization and user like any request, so row-level security applies.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timezone

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from novara.automation.models import Notification, Task
from novara.automation.router import approvable_runs
from novara.db import utcnow
from novara.deps import AuthContext, OrgContext, current_auth, member_context, org_context, two_step
from novara.modules.identity.models import User, UserSession
from novara.tenancy import bind_user

router = APIRouter(tags=["live"])

HEARTBEAT_SECONDS = 25


def snapshot(maker, org_id: str, user_id: str, session_id: str) -> dict | None:
    """The user's current counts, or None when they may no longer see this organization."""
    with maker() as db:
        bind_user(db, user_id)
        login = db.get(UserSession, session_id)
        if login is None or login.revoked_at is not None:
            return None
        expires = login.expires_at if login.expires_at.tzinfo else login.expires_at.replace(tzinfo=timezone.utc)
        user = db.get(User, user_id)
        if expires <= utcnow() or user is None:
            return None
        ctx = member_context(db, org_id, user)
        if ctx is None or (ctx.organization.require_mfa and not two_step(AuthContext(user=user, session=login))):
            return None
        return counts(db, ctx)


def counts(db, ctx: OrgContext) -> dict:
    unread = select(Notification).where(Notification.user_id == ctx.user.id, Notification.read_at.is_(None))
    latest = db.scalar(unread.order_by(Notification.created_at.desc()).limit(1))
    return {
        "notifications": db.scalar(select(func.count()).select_from(unread.subquery())),
        "approvals": len(approvable_runs(db, ctx)),
        "tasks": db.scalar(select(func.count()).select_from(Task)
                           .where(Task.assignee_user_id == ctx.user.id, Task.status == "open")),
        "latest_notification": {"id": latest.id, "title": latest.title} if latest else None,
    }


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/orgs/{org_id}/live")
async def live(org_id: str, request: Request, once: bool = False, ctx: OrgContext = Depends(org_context),
               auth: AuthContext = Depends(current_auth)):
    """Server-sent events: `counts` whenever the user's counts change, `end` when access is gone.
    The stream closes after a few minutes; clients reconnect (``once=true`` sends one event and closes)."""
    settings = request.app.state.settings
    maker = request.app.state.sessionmaker
    args = (maker, ctx.organization.id, ctx.user.id, auth.session.id)

    async def stream():
        last: dict | None = None
        started = last_sent = time.monotonic()
        while True:
            snap = await run_in_threadpool(snapshot, *args)
            if snap is None:
                yield _event("end", {"reason": "access"})
                return
            now = time.monotonic()
            if snap != last:
                yield _event("counts", snap)
                last, last_sent = snap, now
            elif now - last_sent >= HEARTBEAT_SECONDS:
                yield ": ping\n\n"
                last_sent = now
            if once or now - started >= settings.live_max_seconds or await request.is_disconnected():
                return
            await asyncio.sleep(settings.live_interval_seconds)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
