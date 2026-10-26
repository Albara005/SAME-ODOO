from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.agents import service as svc
from novara.agents.models import Agent, AgentRun, AgentTestCase, AgentVersion
from novara.ai import assistant as A
from novara.ai.router import _usd as usd
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit

router = APIRouter(tags=["agents"])


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    disabled_tools: list[str] | None = None
    monthly_budget_usd: Decimal | None = Field(default=None, ge=0, le=10_000, decimal_places=2)


class DraftIn(BaseModel):
    instructions: str = Field(default="", max_length=20_000)
    tools: list[str] = Field(default_factory=list, max_length=30)
    route: str = "assistant"
    max_steps: int = 6
    max_cost_usd: Decimal = Field(default=Decimal("0.20"), gt=0, decimal_places=4)
    # Files the agent can look things up in; omit to keep the draft's current ones.
    knowledge: list[str] | None = Field(default=None, max_length=svc.MAX_KNOWLEDGE_FILES)


class TestCaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    input: str = Field(min_length=1, max_length=8000)
    expect_tools: list[str] = Field(default_factory=list)
    forbid_tools: list[str] = Field(default_factory=list)
    reply_contains: list[str] = Field(default_factory=list)


class RunIn(BaseModel):
    input: str = Field(min_length=1, max_length=8000)


class KnowledgeFileOut(BaseModel):
    file_id: str
    filename: str            # empty if the file was deleted since
    text_status: str         # as on the file, or "deleted"


class VersionOut(BaseModel):
    number: int
    status: str
    instructions: str
    tools: list[str]
    route: str
    max_steps: int
    max_cost_usd: str
    knowledge: list[KnowledgeFileOut]
    created_at: datetime


class AgentOut(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    published_version: int | None
    disabled_tools: list[str]
    monthly_budget_usd: str
    month_spend_usd: str
    versions: list[VersionOut]
    available_tools: list[dict]


class TestCaseOut(TestCaseIn):
    id: str


class RunOut(BaseModel):
    id: str
    version: int
    trigger: str
    input: str
    reply: str
    stop: str
    tool_calls: list[dict]
    cost_usd: str
    passed: bool | None
    failures: list[str]
    created_at: datetime


def _version_out(db: Session, v: AgentVersion) -> VersionOut:
    from novara.modules.files.models import StoredFile

    files = {f.id: f for f in db.scalars(select(StoredFile).where(StoredFile.id.in_(v.knowledge)))} if v.knowledge else {}
    knowledge = [KnowledgeFileOut(file_id=i, filename=files[i].filename if i in files else "",
                                  text_status=files[i].text_status if i in files else "deleted") for i in v.knowledge]
    return VersionOut(number=v.number, status=v.status, instructions=v.instructions, tools=v.tools, route=v.route,
                      max_steps=v.max_steps, max_cost_usd=usd(v.max_cost_micros), knowledge=knowledge,
                      created_at=v.created_at)


def _agent_out(db: Session, a: Agent) -> AgentOut:
    from novara.ai.tools import TOOLS

    return AgentOut(id=a.id, name=a.name, description=a.description, enabled=a.enabled,
                    published_version=a.published_version, disabled_tools=a.disabled_tools,
                    monthly_budget_usd=usd(a.monthly_budget_micros), month_spend_usd=usd(svc.month_spend(db, a)),
                    versions=[_version_out(db, v) for v in svc.versions(db, a)],
                    available_tools=[{"name": t.name, "kind": t.kind, "permission": t.permission,
                                      "description": t.description} for t in TOOLS.values()])


def _run_out(r: AgentRun) -> RunOut:
    return RunOut(id=r.id, version=r.version_number, trigger=r.trigger, input=r.input, reply=r.reply, stop=r.stop,
                  tool_calls=r.tool_calls, cost_usd=usd(r.cost_micros), passed=r.passed, failures=r.failures,
                  created_at=r.created_at)


def _agent(db: Session, agent_id: str) -> Agent:
    a = db.get(Agent, agent_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    return a


def _version(db: Session, agent: Agent, number: int) -> AgentVersion:
    v = svc.get_version(db, agent, number)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"version {number} not found")
    return v


def _errors(fn):
    try:
        return fn()
    except svc.AgentError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except svc.AgentStateError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except (svc.AgentDisabled, svc.AgentKnowledgeForbidden) as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e))
    except A.AssistantUnavailable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e))
    except A.BudgetExceeded as e:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(e))


def _audit(db, ctx, request, action, agent, **details):
    audit.record(db, actor_user_id=ctx.user.id, action=action, target_type="agent", target_id=agent.id,
                 details=details, ip_address=client_ip(request))


@router.post("/orgs/{org_id}/agents", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
def create_agent(body: AgentIn, request: Request, ctx: OrgContext = Depends(require(P.AGENTS_MANAGE)),
                 db: Session = Depends(get_db)):
    agent = svc.create_agent(db, name=body.name, description=body.description, user_id=ctx.user.id)
    _audit(db, ctx, request, "agent.created", agent, name=agent.name)
    db.commit()
    return _agent_out(db, agent)


@router.get("/orgs/{org_id}/agents", response_model=list[AgentOut])
def list_agents(_: OrgContext = Depends(require(P.AGENTS_RUN)), db: Session = Depends(get_db)):
    return [_agent_out(db, a) for a in db.scalars(select(Agent).order_by(Agent.name))]


@router.get("/orgs/{org_id}/agents/{agent_id}", response_model=AgentOut)
def read_agent(agent_id: str, _: OrgContext = Depends(require(P.AGENTS_RUN)), db: Session = Depends(get_db)):
    return _agent_out(db, _agent(db, agent_id))


@router.patch("/orgs/{org_id}/agents/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: str, body: AgentUpdate, request: Request,
                 ctx: OrgContext = Depends(require(P.AGENTS_MANAGE)), db: Session = Depends(get_db)):
    """Rename, switch on/off instantly, revoke tools instantly, or set the monthly budget."""
    a = _agent(db, agent_id)
    changes = body.model_dump(exclude_none=True)
    if "disabled_tools" in changes:
        from novara.ai.tools import TOOLS

        if unknown := sorted(set(changes["disabled_tools"]) - set(TOOLS)):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown tools: {unknown}")
    if "monthly_budget_usd" in changes:
        a.monthly_budget_micros = int(changes.pop("monthly_budget_usd") * 1_000_000)
        changes["monthly_budget_micros"] = a.monthly_budget_micros
    for k, v in changes.items():
        setattr(a, k, v)
    _audit(db, ctx, request, "agent.updated", a, **{k: v for k, v in changes.items()})
    db.commit()
    return _agent_out(db, a)


@router.put("/orgs/{org_id}/agents/{agent_id}/draft", response_model=VersionOut)
def edit_draft(agent_id: str, body: DraftIn, request: Request, ctx: OrgContext = Depends(require(P.AGENTS_MANAGE)),
               db: Session = Depends(get_db)):
    """Edit the draft version (a tested or published version is copied into a new draft)."""
    a = _agent(db, agent_id)
    cost = int(body.max_cost_usd * 1_000_000)
    _errors(lambda: svc.validate_config(tools=body.tools, route=body.route, max_steps=body.max_steps,
                                        max_cost_micros=cost))
    knowledge = _errors(lambda: svc.validate_knowledge(db, ctx, body.knowledge)) if body.knowledge is not None else None
    draft = svc.editable_draft(db, a, ctx.user.id)
    draft.instructions, draft.tools, draft.route = body.instructions, sorted(set(body.tools)), body.route
    draft.max_steps, draft.max_cost_micros = body.max_steps, cost
    if knowledge is not None:
        draft.knowledge = knowledge
    _audit(db, ctx, request, "agent.draft_updated", a, version=draft.number, tools=draft.tools,
           knowledge=len(draft.knowledge))
    db.commit()
    return _version_out(db, draft)


@router.post("/orgs/{org_id}/agents/{agent_id}/tests", response_model=TestCaseOut,
             status_code=status.HTTP_201_CREATED)
def add_test(agent_id: str, body: TestCaseIn, ctx: OrgContext = Depends(require(P.AGENTS_MANAGE)),
             db: Session = Depends(get_db)):
    a = _agent(db, agent_id)
    case = AgentTestCase(agent_id=a.id, **body.model_dump())
    db.add(case)
    db.commit()
    return TestCaseOut(id=case.id, **body.model_dump())


@router.get("/orgs/{org_id}/agents/{agent_id}/tests", response_model=list[TestCaseOut])
def list_tests(agent_id: str, _: OrgContext = Depends(require(P.AGENTS_MANAGE)), db: Session = Depends(get_db)):
    a = _agent(db, agent_id)
    rows = db.scalars(select(AgentTestCase).where(AgentTestCase.agent_id == a.id).order_by(AgentTestCase.created_at))
    return [TestCaseOut(id=c.id, name=c.name, input=c.input, expect_tools=c.expect_tools, forbid_tools=c.forbid_tools,
                        reply_contains=c.reply_contains) for c in rows]


@router.post("/orgs/{org_id}/agents/{agent_id}/versions/{number}/test-runs", response_model=list[RunOut])
def test_version(agent_id: str, number: int, request: Request, ctx: OrgContext = Depends(require(P.AGENTS_MANAGE)),
                 db: Session = Depends(get_db)):
    """Run every test case against this version, as you (your permissions apply). Freezes the version."""
    a = _agent(db, agent_id)
    v = _version(db, a, number)
    runs = _errors(lambda: svc.run_tests(db, ctx, a, v, provider=request.app.state.ai_provider, ip=client_ip(request),
                                         storage=request.app.state.storage))
    _audit(db, ctx, request, "agent.tested", a, version=number, passed=sum(1 for r in runs if r.passed),
           total=len(runs))
    db.commit()
    return [_run_out(r) for r in runs]


@router.post("/orgs/{org_id}/agents/{agent_id}/versions/{number}/publish", response_model=AgentOut)
def publish(agent_id: str, number: int, request: Request, ctx: OrgContext = Depends(require(P.AGENTS_MANAGE)),
            db: Session = Depends(get_db)):
    """Publish a version whose tests all passed. Publishing an older version is a rollback."""
    a = _agent(db, agent_id)
    _errors(lambda: svc.publish(db, a, _version(db, a, number)))
    _audit(db, ctx, request, "agent.published", a, version=number)
    db.commit()
    return _agent_out(db, a)


@router.post("/orgs/{org_id}/agents/{agent_id}/runs", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def run_agent(agent_id: str, body: RunIn, request: Request, ctx: OrgContext = Depends(require(P.AGENTS_RUN)),
              db: Session = Depends(get_db)):
    """Run the published version with your own permissions. Changes it proposes wait for your approval."""
    a = _agent(db, agent_id)
    if a.published_version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "this agent has no published version")
    r = _errors(lambda: svc.run(db, ctx, a, _version(db, a, a.published_version), body.input,
                                provider=request.app.state.ai_provider, trigger="manual", ip=client_ip(request),
                                storage=request.app.state.storage))
    db.commit()
    return _run_out(r)


@router.get("/orgs/{org_id}/agents/{agent_id}/runs", response_model=list[RunOut])
def list_runs(agent_id: str, ctx: OrgContext = Depends(require(P.AGENTS_RUN)), db: Session = Depends(get_db)):
    """Managers see every run; others see their own."""
    a = _agent(db, agent_id)
    stmt = select(AgentRun).where(AgentRun.agent_id == a.id).order_by(AgentRun.created_at.desc()).limit(200)
    if not ctx.has(P.AGENTS_MANAGE):
        stmt = stmt.where(AgentRun.user_id == ctx.user.id)
    return [_run_out(r) for r in db.scalars(stmt)]
