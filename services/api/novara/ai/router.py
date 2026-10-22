from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.ai import assistant as A
from novara.ai import routing
from novara.ai import tools as T
from novara.ai.models import Conversation, ModelUsage, ToolCall
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit

router = APIRouter(tags=["assistant"])


class SettingsOut(BaseModel):
    enabled: bool
    monthly_budget_usd: str
    month_spend_usd: str
    routes: dict[str, dict[str, str]]


class SettingsUpdate(BaseModel):
    enabled: bool | None = None
    monthly_budget_usd: Decimal | None = Field(default=None, ge=0, le=1_000_000, decimal_places=2)
    routes: dict[str, dict[str, str]] | None = None


class ConversationIn(BaseModel):
    title: str = Field(default="", max_length=200)


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    deep: bool = Field(default=False, description="Use the deeper (slower, costlier) reasoning route")


class ActionOut(BaseModel):
    id: str
    name: str
    kind: str
    status: str
    arguments: dict
    result: dict | list | None
    created_at: datetime
    decided_at: datetime | None


class TurnOut(BaseModel):
    reply: str
    stop: str
    pending_actions: list[ActionOut]
    tool_calls: list[ActionOut]
    cost_usd: str


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: datetime


class TranscriptEntry(BaseModel):
    role: str
    text: str


class ConversationDetail(ConversationOut):
    messages: list[TranscriptEntry]
    actions: list[ActionOut]


class UsageRow(BaseModel):
    key: str
    requests: int
    input_tokens: int
    output_tokens: int
    cost_usd: str


class UsageOut(BaseModel):
    month: str
    total_cost_usd: str
    budget_usd: str
    by_model: list[UsageRow]
    by_user: list[UsageRow]


def _usd(micros: int) -> str:
    return str((Decimal(micros) / Decimal(1_000_000)).quantize(Decimal("0.0001")))


def _action(c: ToolCall) -> ActionOut:
    return ActionOut(id=c.id, name=c.name, kind=c.kind, status=c.status, arguments=c.arguments, result=c.result,
                     created_at=c.created_at, decided_at=c.decided_at)


def _own_conversation(db: Session, ctx: OrgContext, conversation_id: str) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != ctx.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    return conv


def _availability(fn):
    try:
        return fn()
    except A.AssistantUnavailable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e))
    except A.BudgetExceeded as e:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(e))


@router.get("/orgs/{org_id}/assistant/settings", response_model=SettingsOut)
def get_settings(ctx: OrgContext = Depends(require(P.AI_MANAGE)), db: Session = Depends(get_db)):
    s = A.settings_for(db)
    db.commit()
    return SettingsOut(enabled=s.enabled, monthly_budget_usd=_usd(s.monthly_budget_micros),
                       month_spend_usd=_usd(A.month_spend_micros(db)),
                       routes={name: {"model": r.model, "effort": r.effort}
                               for name, r in ((n, routing.resolve(n, s.routes)) for n in routing.DEFAULT_ROUTES)})


@router.patch("/orgs/{org_id}/assistant/settings", response_model=SettingsOut)
def update_settings(body: SettingsUpdate, request: Request, ctx: OrgContext = Depends(require(P.AI_MANAGE)),
                    db: Session = Depends(get_db)):
    s = A.settings_for(db)
    changes: dict = {}
    if body.enabled is not None:
        s.enabled = changes["enabled"] = body.enabled
    if body.monthly_budget_usd is not None:
        s.monthly_budget_micros = int(body.monthly_budget_usd * 1_000_000)
        changes["monthly_budget_usd"] = str(body.monthly_budget_usd)
    if body.routes is not None:
        try:
            s.routes = changes["routes"] = routing.validate_overrides(body.routes)
        except routing.RouteError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    audit.record(db, actor_user_id=ctx.user.id, action="assistant.settings_updated", target_type="assistant",
                 target_id="", details=changes, ip_address=client_ip(request))
    db.commit()
    return get_settings(ctx=ctx, db=db)


@router.post("/orgs/{org_id}/assistant/conversations", response_model=ConversationOut,
             status_code=status.HTTP_201_CREATED)
def create_conversation(body: ConversationIn, ctx: OrgContext = Depends(require(P.AI_USE)),
                        db: Session = Depends(get_db)):
    conv = Conversation(user_id=ctx.user.id, title=body.title)
    db.add(conv)
    db.commit()
    return ConversationOut(id=conv.id, title=conv.title, created_at=conv.created_at)


@router.get("/orgs/{org_id}/assistant/conversations", response_model=list[ConversationOut])
def list_conversations(ctx: OrgContext = Depends(require(P.AI_USE)), db: Session = Depends(get_db)):
    rows = db.scalars(select(Conversation).where(Conversation.user_id == ctx.user.id)
                      .order_by(Conversation.created_at.desc()))
    return [ConversationOut(id=c.id, title=c.title, created_at=c.created_at) for c in rows]


@router.get("/orgs/{org_id}/assistant/conversations/{conversation_id}", response_model=ConversationDetail)
def read_conversation(conversation_id: str, ctx: OrgContext = Depends(require(P.AI_USE)),
                      db: Session = Depends(get_db)):
    conv = _own_conversation(db, ctx, conversation_id)
    transcript = []
    for m in A.history(db, conv):
        texts = [b["text"] for b in m["content"] if b.get("type") == "text"]
        if m["role"] == "user" and texts:
            transcript.append(TranscriptEntry(role="user", text=texts[-1]))  # last block is what the user typed
        elif m["role"] == "assistant" and any(texts):
            transcript.append(TranscriptEntry(role="assistant", text="\n".join(texts)))
    calls = db.scalars(select(ToolCall).where(ToolCall.conversation_id == conv.id).order_by(ToolCall.created_at))
    return ConversationDetail(id=conv.id, title=conv.title, created_at=conv.created_at, messages=transcript,
                              actions=[_action(c) for c in calls])


@router.post("/orgs/{org_id}/assistant/conversations/{conversation_id}/messages", response_model=TurnOut)
def send_message(conversation_id: str, body: MessageIn, request: Request,
                 ctx: OrgContext = Depends(require(P.AI_USE)), db: Session = Depends(get_db)):
    conv = _own_conversation(db, ctx, conversation_id)
    result = _availability(lambda: A.run_turn(
        db, ctx, conv, body.content, provider=request.app.state.ai_provider,
        route_name="assistant_deep" if body.deep else "assistant", ip=client_ip(request)))
    db.commit()
    return TurnOut(reply=result.reply, stop=result.stop,
                   pending_actions=[_action(c) for c in result.tool_calls if c.status == "pending_approval"],
                   tool_calls=[_action(c) for c in result.tool_calls],
                   cost_usd=_usd(sum(u.cost_micros for u in result.usage)))


def _decide(action_id: str, approve: bool, request: Request, ctx: OrgContext, db: Session) -> ActionOut:
    call = db.get(ToolCall, action_id)
    # Only the user whose assistant proposed the action can decide on it.
    if call is None or call.user_id != ctx.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "action not found")
    permission = T.TOOLS[call.name].permission
    if approve and permission not in ctx.permissions:
        # Permissions are checked at approval time, not when the assistant proposed the action.
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"you no longer have permission '{permission}'")
    try:
        call = A.decide(db, ctx, call, approve=approve, ip=client_ip(request))
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    audit.record(db, actor_user_id=ctx.user.id, action=f"assistant.action_{call.status}", target_type="ai_action",
                 target_id=call.id, details={"tool": call.name}, ip_address=client_ip(request))
    db.commit()
    return _action(call)


@router.post("/orgs/{org_id}/assistant/actions/{action_id}/approve", response_model=ActionOut)
def approve_action(action_id: str, request: Request, ctx: OrgContext = Depends(require(P.AI_USE)),
                   db: Session = Depends(get_db)):
    """Run an action the assistant proposed. It runs with your current permissions."""
    return _decide(action_id, True, request, ctx, db)


@router.post("/orgs/{org_id}/assistant/actions/{action_id}/reject", response_model=ActionOut)
def reject_action(action_id: str, request: Request, ctx: OrgContext = Depends(require(P.AI_USE)),
                  db: Session = Depends(get_db)):
    return _decide(action_id, False, request, ctx, db)


@router.get("/orgs/{org_id}/assistant/usage", response_model=UsageOut)
def usage_report(ctx: OrgContext = Depends(require(P.AI_MANAGE)), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    rows = db.scalars(select(ModelUsage).where(ModelUsage.created_at >= start)).all()
    by: dict[str, dict[str, list[int]]] = {"model": defaultdict(lambda: [0, 0, 0, 0]),
                                           "user": defaultdict(lambda: [0, 0, 0, 0])}
    for u in rows:
        for dim, key in (("model", u.model), ("user", u.user_id or "")):
            agg = by[dim][key]
            agg[0] += 1
            agg[1] += u.input_tokens
            agg[2] += u.output_tokens
            agg[3] += u.cost_micros

    def table(dim: str) -> list[UsageRow]:
        return [UsageRow(key=k, requests=v[0], input_tokens=v[1], output_tokens=v[2], cost_usd=_usd(v[3]))
                for k, v in sorted(by[dim].items(), key=lambda kv: -kv[1][3])]

    s = A.settings_for(db)
    db.commit()
    return UsageOut(month=now.strftime("%Y-%m"), total_cost_usd=_usd(sum(u.cost_micros for u in rows)),
                    budget_usd=_usd(s.monthly_budget_micros), by_model=table("model"), by_user=table("user"))
