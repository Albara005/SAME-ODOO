from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.ai import assistant as A
from novara.ai.models import Conversation
from novara.ai.router import _usd as usd
from novara.ai.tools import Tool, ToolContext, ToolRefused
from novara.creative import color as C
from novara.creative import logo as L
from novara.creative import service as svc
from novara.creative.models import BrandKit, BrandLogo
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit

router = APIRouter(tags=["creative-studio"])

HexColor = Field(pattern=r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


class PaletteIn(BaseModel):
    primary: str = HexColor
    secondary: str | None = Field(default=None, pattern=r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


class KitIn(PaletteIn):
    name: str = Field(min_length=1, max_length=120)
    tagline: str = Field(default="", max_length=120)
    font_pair: str = Field(default="kufi", pattern="^(kufi|plex|naskh|system)$")


class KitUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    tagline: str | None = Field(default=None, max_length=120)
    primary: str | None = Field(default=None, pattern=r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")
    secondary: str | None = Field(default=None, pattern=r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")
    font_pair: str | None = Field(default=None, pattern="^(kufi|plex|naskh|system)$")


class LogoOut(BaseModel):
    id: str
    spec: dict
    source: str
    note: str
    primary: bool
    created_at: datetime


class KitOut(BaseModel):
    id: str
    name: str
    tagline: str
    primary_color: str
    secondary_color: str | None
    font_pair: str
    tokens: dict
    checks_pass: bool
    notes: list[str]
    adjustments: list[dict]
    logos: list[LogoOut]


class ConceptIn(BaseModel):
    brief: str = Field(min_length=1, max_length=4000)


def _logo_out(l: BrandLogo) -> LogoOut:
    return LogoOut(id=l.id, spec=l.spec, source=l.source, note=l.note, primary=l.primary, created_at=l.created_at)


def _logos(db: Session, kit: BrandKit) -> list[BrandLogo]:
    return list(db.scalars(select(BrandLogo).where(BrandLogo.brand_kit_id == kit.id).order_by(BrandLogo.created_at)))


def _adjustments(k: BrandKit) -> list[dict]:
    if "adjustments" in k.system:
        return k.system["adjustments"]
    # kits saved before adjustments existed: derive them again from the colours
    return svc.system_for(k.primary_color, k.secondary_color)["adjustments"]


def _kit_out(db: Session, k: BrandKit) -> KitOut:
    return KitOut(id=k.id, name=k.name, tagline=k.tagline, primary_color=k.primary_color,
                  secondary_color=k.secondary_color, font_pair=k.font_pair, tokens=k.system["modes"],
                  checks_pass=k.system["all_checks_pass"], notes=k.system["notes"],
                  adjustments=_adjustments(k),
                  logos=[_logo_out(l) for l in _logos(db, k)])


def _kit(db: Session, kit_id: str) -> BrandKit:
    k = db.get(BrandKit, kit_id)
    if k is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand kit not found")
    return k


def _logo(db: Session, kit: BrandKit, logo_id: str) -> BrandLogo:
    l = db.get(BrandLogo, logo_id)
    if l is None or l.brand_kit_id != kit.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "logo not found")
    return l


def _norm(hex_color: str | None) -> str | None:
    return C.to_hex(C.parse_hex(hex_color)) if hex_color else None


@router.post("/orgs/{org_id}/creative/palette")
def preview_palette(body: PaletteIn, _: OrgContext = Depends(require(P.BRAND_READ))):
    """Preview the full colour system for a brand colour without saving anything."""
    system = C.build_system(body.primary, body.secondary)
    return system | {"css": C.to_css(system)}


@router.post("/orgs/{org_id}/creative/brand-kits", response_model=KitOut, status_code=status.HTTP_201_CREATED)
def create_kit(body: KitIn, request: Request, ctx: OrgContext = Depends(require(P.BRAND_MANAGE)),
               db: Session = Depends(get_db)):
    k = BrandKit(name=body.name, tagline=body.tagline, primary_color=_norm(body.primary),
                 secondary_color=_norm(body.secondary), font_pair=body.font_pair,
                 system=svc.system_for(body.primary, body.secondary), created_by=ctx.user.id)
    db.add(k)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="brand.created", target_type="brand_kit", target_id=k.id,
                 details={"name": k.name, "primary": k.primary_color}, ip_address=client_ip(request))
    db.commit()
    return _kit_out(db, k)


@router.get("/orgs/{org_id}/creative/brand-kits", response_model=list[KitOut])
def list_kits(_: OrgContext = Depends(require(P.BRAND_READ)), db: Session = Depends(get_db)):
    return [_kit_out(db, k) for k in db.scalars(select(BrandKit).order_by(BrandKit.name))]


@router.get("/orgs/{org_id}/creative/brand-kits/{kit_id}", response_model=KitOut)
def read_kit(kit_id: str, _: OrgContext = Depends(require(P.BRAND_READ)), db: Session = Depends(get_db)):
    return _kit_out(db, _kit(db, kit_id))


@router.patch("/orgs/{org_id}/creative/brand-kits/{kit_id}", response_model=KitOut)
def update_kit(kit_id: str, body: KitUpdate, request: Request, ctx: OrgContext = Depends(require(P.BRAND_MANAGE)),
               db: Session = Depends(get_db)):
    """Changing a colour regenerates every derived token (and every logo, which is drawn from them)."""
    k = _kit(db, kit_id)
    changes = body.model_dump(exclude_unset=True)
    for field in ("name", "tagline", "font_pair"):
        if changes.get(field) is not None:
            setattr(k, field, changes[field])
    if "primary" in changes or "secondary" in changes:
        k.primary_color = _norm(changes.get("primary")) or k.primary_color
        if "secondary" in changes:
            k.secondary_color = _norm(changes["secondary"])
        k.system = svc.system_for(k.primary_color, k.secondary_color)
    audit.record(db, actor_user_id=ctx.user.id, action="brand.updated", target_type="brand_kit", target_id=k.id,
                 details=changes, ip_address=client_ip(request))
    db.commit()
    return _kit_out(db, k)


@router.get("/orgs/{org_id}/creative/brand-kits/{kit_id}/tokens.css")
def kit_css(kit_id: str, _: OrgContext = Depends(require(P.BRAND_READ)), db: Session = Depends(get_db)) -> Response:
    return Response(svc.css(_kit(db, kit_id)), media_type="text/css; charset=utf-8")


@router.get("/orgs/{org_id}/creative/brand-kits/{kit_id}/tokens.json")
def kit_tokens(kit_id: str, _: OrgContext = Depends(require(P.BRAND_READ)), db: Session = Depends(get_db)):
    return svc.tokens_json(_kit(db, kit_id))


@router.post("/orgs/{org_id}/creative/brand-kits/{kit_id}/logos", response_model=LogoOut,
             status_code=status.HTTP_201_CREATED)
def add_logo(kit_id: str, spec: L.LogoSpec, ctx: OrgContext = Depends(require(P.BRAND_MANAGE)),
             db: Session = Depends(get_db)):
    k = _kit(db, kit_id)
    logo = _store_logo(db, k, spec, source="user")
    db.commit()
    return _logo_out(logo)


def _store_logo(db: Session, kit: BrandKit, spec: L.LogoSpec, *, source: str, note: str = "") -> BrandLogo:
    try:
        L.render(spec, svc.logo_colors(kit, "full"))  # refuse specs that can't be drawn (e.g. missing glyphs)
    except L.LogoError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    first = not _logos(db, kit)
    logo = BrandLogo(brand_kit_id=kit.id, spec=spec.model_dump(), source=source, note=note, primary=first)
    db.add(logo)
    db.flush()
    return logo


@router.post("/orgs/{org_id}/creative/brand-kits/{kit_id}/logos/{logo_id}/primary", response_model=KitOut)
def choose_logo(kit_id: str, logo_id: str, ctx: OrgContext = Depends(require(P.BRAND_MANAGE)),
                db: Session = Depends(get_db)):
    k = _kit(db, kit_id)
    chosen = _logo(db, k, logo_id)
    for l in _logos(db, k):
        l.primary = l.id == chosen.id
    db.commit()
    return _kit_out(db, k)


@router.get("/orgs/{org_id}/creative/brand-kits/{kit_id}/logos/{logo_id}.{fmt}")
def logo_file(kit_id: str, logo_id: str, fmt: str, variant: str = Query(default="full", pattern="^(full|icon|mono|inverse)$"),
              width: int = Query(default=1024, ge=16, le=4096), _: OrgContext = Depends(require(P.BRAND_READ)),
              db: Session = Depends(get_db)) -> Response:
    k = _kit(db, kit_id)
    svg = svc.render_logo(k, _logo(db, k, logo_id), variant)
    if fmt == "svg":
        return Response(svg, media_type="image/svg+xml; charset=utf-8",
                        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
    if fmt == "png":
        try:
            return Response(L.png(svg, width), media_type="image/png")
        except L.LogoError as e:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(e))
    raise HTTPException(status.HTTP_404_NOT_FOUND, "use .svg or .png")


@router.get("/orgs/{org_id}/creative/brand-kits/{kit_id}/export.zip")
def export_kit(kit_id: str, _: OrgContext = Depends(require(P.BRAND_READ)), db: Session = Depends(get_db)) -> Response:
    k = _kit(db, kit_id)
    return Response(svc.export_zip(k, _logos(db, k)), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="brand-kit.zip"'})


@router.post("/orgs/{org_id}/creative/brand-kits/{kit_id}/apply/{project_id}")
def apply_to_app(kit_id: str, project_id: str, request: Request, ctx: OrgContext = Depends(require(P.BRAND_READ)),
                 db: Session = Depends(get_db)):
    """Put the brand's tokens into an App Studio project's styles.css (working copy; the user cuts a version)."""
    from novara.studio.files import FileError
    from novara.studio.models import AppProject
    from novara.studio.service import ProjectFiles

    if not ctx.has(P.APPS_WRITE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission: {P.APPS_WRITE}")
    k = _kit(db, kit_id)
    project = db.get(AppProject, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    pf = ProjectFiles(request.app.state.storage, project)
    try:
        existing = pf.read("styles.css").decode("utf-8") if "styles.css" in project.working_files else ""
        pf.write("styles.css", svc.apply_to_stylesheet(existing, k).encode("utf-8"))
    except FileError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    audit.record(db, actor_user_id=ctx.user.id, action="brand.applied_to_app", target_type="app_project",
                 target_id=project.id, details={"brand_kit": k.id}, ip_address=client_ip(request))
    db.commit()
    return {"project_id": project.id, "file": "styles.css", "applied": True}


# ---------------------------------------------------------------- design agent

DESIGN_SYSTEM = """You are the design agent in NOVARA Creative Studio. You help the user find a visual identity: \
colours and logo concepts for their brand.

How you work:
- Understand the brand: what it does, who it serves, the feeling it should give. Ask nothing; make sensible \
choices and explain them briefly.
- Use preview_palette to try colours; it reports accessibility (WCAG contrast) - prefer colours whose checks pass, \
and say when the platform had to adjust a colour for readability.
- Use propose_logo to create 2-4 distinct concepts (different layouts and symbols). The platform draws them; \
you choose from its vocabulary. Never write SVG or code.
- Keep names exactly as the user gave them. Arabic names read right to left; the platform handles that.
- Reply in the user's language with one line per concept: what it is and why it suits the brand.
"""


class PaletteArgs(BaseModel):
    primary: str = Field(description="#RRGGBB")
    secondary: str | None = None


def _preview(t: ToolContext, a: PaletteArgs):
    try:
        s = C.build_system(a.primary, a.secondary)
    except C.ColorError as e:
        raise ToolRefused(str(e))
    light = s["modes"]["light"]
    return {"all_checks_pass": s["all_checks_pass"], "notes": s["notes"],
            "button": [light["color-primary"], light["color-on-primary"]], "link": light["color-link"],
            "scale": s["scales"]["primary"]}


class ProposeArgs(L.LogoSpec):
    rationale: str = Field(default="", max_length=400, description="One sentence: why this concept suits the brand")


def _propose(t: ToolContext, a: ProposeArgs):
    kit = t.extra["kit"]
    spec = L.LogoSpec(**a.model_dump(exclude={"rationale"}))
    try:
        L.render(spec, svc.logo_colors(kit, "full"))
    except L.LogoError as e:
        raise ToolRefused(str(e))
    first = not _logos(t.db, kit)
    logo = BrandLogo(brand_kit_id=kit.id, spec=spec.model_dump(), source="assistant", note=a.rationale, primary=first)
    t.db.add(logo)
    t.db.flush()
    return {"logo_id": logo.id, "stored": True}


DESIGN_TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("preview_palette", "Build and check a colour system for a brand colour (nothing is saved).",
         P.BRAND_MANAGE, "read", PaletteArgs, _preview),
    Tool("propose_logo", "Draw a logo concept from a spec and add it to the brand kit as a concept.",
         P.BRAND_MANAGE, "edit", ProposeArgs, _propose),
]}


@router.post("/orgs/{org_id}/creative/brand-kits/{kit_id}/concepts")
def design_concepts(kit_id: str, body: ConceptIn, request: Request, ctx: OrgContext = Depends(require(P.BRAND_MANAGE)),
                    db: Session = Depends(get_db)):
    """Ask the design agent for logo concepts. They are added to the kit; nothing else changes."""
    if not ctx.has(P.AI_USE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission: {P.AI_USE}")
    k = _kit(db, kit_id)
    before = {l.id for l in _logos(db, k)}
    conv = Conversation(user_id=ctx.user.id, title=f"brand:{k.id}")
    db.add(conv)
    db.flush()
    note = (f"Brand: {k.name}" + (f" - {k.tagline}" if k.tagline else "") +
            f". Current colours: primary {k.primary_color}" + (f", secondary {k.secondary_color}" if k.secondary_color else ""))
    try:
        result = A.run_turn(db, ctx, conv, body.brief, provider=request.app.state.ai_provider, ip=client_ip(request),
                            registry=DESIGN_TOOLS, system=DESIGN_SYSTEM, context_note=note, extra={"kit": k})
    except A.AssistantUnavailable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e))
    except A.BudgetExceeded as e:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(e))
    new = [l for l in _logos(db, k) if l.id not in before]
    db.commit()
    return {"reply": result.reply, "stop": result.stop, "concepts": [_logo_out(l) for l in new],
            "cost_usd": usd(sum(u.cost_micros for u in result.usage))}
