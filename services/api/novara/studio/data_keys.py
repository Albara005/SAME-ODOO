"""Publishable data keys: how a published App Studio site reads business data.

    GET /api/v1/public/catalog?key=nvpk_...     (or the X-Novara-Key header)

returns the organization's active products: name, SKU, description, unit, price with and
without tax and, with the "availability" scope, whether each is in stock (stock left after
what confirmed orders hold). Answers carry CORS headers for any origin, because sites run in
a sandbox with an opaque origin and the data is public by the owner's choice.
"""

from __future__ import annotations

import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from novara import crypto
from novara.db import utcnow
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.accounting.models import Tax
from novara.modules.audit import service as audit
from novara.modules.catalog.models import Product
from novara.modules.inventory.models import StockLevel
from novara.modules.organizations.models import Organization
from novara.money import fmt_decimal, from_minor, to_minor
from novara.studio.models import AppDataKey, AppProject
from novara.tenancy import bind_tenant, system_scope

router = APIRouter(tags=["app data keys"])
public_router = APIRouter(tags=["public data"])

# scope -> permissions the person creating the key must hold (they decide to publish that data)
SCOPES: dict[str, tuple[str, ...]] = {
    "catalog": (P.PRODUCTS_READ,),
    "availability": (P.PRODUCTS_READ, P.INVENTORY_READ),
}
PUBLIC_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "X-Novara-Key",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Cache-Control": "public, max-age=60",
}


class DataKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(min_length=1, max_length=len(SCOPES))


class DataKeyOut(BaseModel):
    id: str
    name: str
    hint: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked: bool
    # Only in the answer that creates the key; it can't be shown again.
    key: str | None = None


def _out(k: AppDataKey, key: str | None = None) -> DataKeyOut:
    return DataKeyOut(id=k.id, name=k.name, hint=k.hint, scopes=k.scopes, created_at=k.created_at,
                      last_used_at=k.last_used_at, revoked=k.revoked_at is not None, key=key)


def _project(db: Session, project_id: str) -> AppProject:
    project = db.get(AppProject, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


@router.get("/orgs/{org_id}/studio/projects/{project_id}/data-keys", response_model=list[DataKeyOut])
def list_keys(project_id: str, _: OrgContext = Depends(require(P.APPS_READ)), db: Session = Depends(get_db)):
    project = _project(db, project_id)
    rows = db.scalars(select(AppDataKey).where(AppDataKey.project_id == project.id).order_by(AppDataKey.created_at))
    return [_out(k) for k in rows]


@router.post("/orgs/{org_id}/studio/projects/{project_id}/data-keys", response_model=DataKeyOut, status_code=status.HTTP_201_CREATED)
def create_key(project_id: str, body: DataKeyIn, request: Request, ctx: OrgContext = Depends(require(P.APPS_DEPLOY)),
               db: Session = Depends(get_db)):
    """Making data public is a production decision (apps.deploy), and you can only publish data you can read."""
    project = _project(db, project_id)
    scopes = sorted(set(body.scopes))
    for scope in scopes:
        if scope not in SCOPES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown scope '{scope}'; choose from {sorted(SCOPES)}")
        missing = [p for p in SCOPES[scope] if not ctx.has(p)]
        if missing:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"publishing '{scope}' needs {', '.join(missing)}")
    if "availability" in scopes and "catalog" not in scopes:
        scopes = sorted({*scopes, "catalog"})
    raw = "nvpk_" + secrets.token_urlsafe(24)
    key = AppDataKey(project_id=project.id, name=body.name, key_hash=crypto.token_hash(raw), hint=raw[:12],
                     scopes=scopes, created_by=ctx.user.id)
    db.add(key)
    db.flush()
    audit.record(db, actor_user_id=ctx.user.id, action="app.data_key_created", target_type="app_project",
                 target_id=project.id, details={"name": key.name, "scopes": scopes, "hint": key.hint},
                 ip_address=client_ip(request))
    db.commit()
    return _out(key, raw)


@router.delete("/orgs/{org_id}/studio/projects/{project_id}/data-keys/{key_id}", response_model=DataKeyOut)
def revoke_key(project_id: str, key_id: str, request: Request, ctx: OrgContext = Depends(require(P.APPS_WRITE)),
               db: Session = Depends(get_db)):
    """Anyone who can edit the app can switch a key off at once."""
    project = _project(db, project_id)
    key = db.get(AppDataKey, key_id)
    if key is None or key.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "key not found")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        audit.record(db, actor_user_id=ctx.user.id, action="app.data_key_revoked", target_type="app_project",
                     target_id=project.id, details={"name": key.name, "hint": key.hint}, ip_address=client_ip(request))
        db.commit()
    return _out(key)


# ---------------------------------------------------------------- public read


def _key_for(db: Session, raw: str) -> AppDataKey | None:
    """Find the key and bind the session to its organization. On PostgreSQL the lookup goes through a
    SECURITY DEFINER function that only answers which organization holds a given hash."""
    digest = crypto.token_hash(raw)
    if db.get_bind().dialect.name == "postgresql":
        org_id = db.scalar(text("SELECT app_data_key_org(:h)"), {"h": digest})
    else:
        with system_scope(db):
            org_id = db.scalar(select(AppDataKey.organization_id).where(AppDataKey.key_hash == digest))
    if org_id is None:
        return None
    bind_tenant(db, org_id)
    return db.scalar(select(AppDataKey).where(AppDataKey.key_hash == digest, AppDataKey.revoked_at.is_(None)))


def _available(db: Session) -> dict[str, bool]:
    from novara.modules.sales.reservations import reserved_totals

    on_hand: dict[str, Decimal] = defaultdict(Decimal)
    for level in db.scalars(select(StockLevel)):
        on_hand[level.product_id] += level.quantity
    for (product_id, _), qty in reserved_totals(db).items():
        on_hand[product_id] -= qty
    return {pid: qty > 0 for pid, qty in on_hand.items()}


@public_router.options("/public/catalog")
def catalog_preflight() -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=PUBLIC_HEADERS)


@public_router.get("/public/catalog")
def public_catalog(key: str | None = Query(default=None, max_length=100),
                   x_novara_key: str | None = Header(default=None, max_length=100), db: Session = Depends(get_db)):
    raw = x_novara_key or key
    data_key = _key_for(db, raw) if raw else None
    if data_key is None or "catalog" not in data_key.scopes:
        return JSONResponse({"detail": "invalid or revoked key"}, status_code=status.HTTP_401_UNAUTHORIZED,
                            headers={**PUBLIC_HEADERS, "Cache-Control": "no-store"})
    org = db.get(Organization, data_key.organization_id)
    c = org.default_currency
    rates = {t.id: t.rate for t in db.scalars(select(Tax))}
    stock = _available(db) if "availability" in data_key.scopes else None
    products = []
    for p in db.scalars(select(Product).where(Product.active.is_(True)).order_by(Product.name)):
        rate = rates.get(p.default_tax_id, Decimal(0)) if p.default_tax_id else Decimal(0)
        net = to_minor(p.sale_price, c)
        item = {"id": p.id, "name": p.name, "sku": p.sku, "description": p.description, "unit": p.unit,
                "kind": p.kind, "price": str(from_minor(net, c)), "tax_rate": fmt_decimal(rate),
                "price_with_tax": str(from_minor(net + to_minor(from_minor(net, c) * rate / 100, c), c))}
        if stock is not None:
            item["available"] = p.kind == "service" or stock.get(p.id, False)
        products.append(item)
    now = utcnow()
    last = data_key.last_used_at
    if last is None or now - (last if last.tzinfo else last.replace(tzinfo=timezone.utc)) > timedelta(minutes=1):
        data_key.last_used_at = now  # at most one write a minute, however busy the site is
        db.commit()
    return JSONResponse({"organization": org.name, "currency": c, "products": products}, headers=PUBLIC_HEADERS)
