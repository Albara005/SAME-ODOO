from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.accounting.models import Tax
from novara.modules.audit import service as audit
from novara.modules.catalog.models import Product
from novara.modules.invoicing.models import InvoiceLine
from novara.money import fmt_decimal

router = APIRouter(tags=["products"])

Price = Field(ge=0, max_digits=18, decimal_places=6)


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    kind: str = Field(default="goods", pattern="^(goods|service)$")
    unit: str = Field(default="unit", min_length=1, max_length=20)
    sale_price: Decimal = Price
    default_tax_id: str | None = None
    description: str = Field(default="", max_length=5000)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    kind: str | None = Field(default=None, pattern="^(goods|service)$")
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    sale_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    default_tax_id: str | None = None
    description: str | None = Field(default=None, max_length=5000)
    active: bool | None = None


class ProductOut(BaseModel):
    id: str
    name: str
    sku: str | None
    kind: str
    unit: str
    sale_price: str
    default_tax_id: str | None
    description: str
    active: bool
    created_at: datetime


def product_out(p: Product) -> ProductOut:
    return ProductOut(id=p.id, name=p.name, sku=p.sku, kind=p.kind, unit=p.unit, sale_price=fmt_decimal(p.sale_price),
                      default_tax_id=p.default_tax_id, description=p.description, active=p.active,
                      created_at=p.created_at)


def get_product(db: Session, product_id: str) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    return product


def _check_tax(db: Session, tax_id: str | None) -> None:
    if tax_id is not None:
        tax = db.get(Tax, tax_id)
        if tax is None or not tax.active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown or inactive tax")


def _commit_unique_sku(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "SKU already used by another product")


@router.get("/orgs/{org_id}/products", response_model=list[ProductOut])
def list_products(
    q: str | None = Query(default=None, max_length=100),
    include_archived: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: OrgContext = Depends(require(P.PRODUCTS_READ)),
    db: Session = Depends(get_db),
):
    stmt = select(Product).order_by(Product.name).limit(limit).offset(offset)
    if not include_archived:
        stmt = stmt.where(Product.active.is_(True))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Product.name).like(like), func.lower(Product.sku).like(like)))
    return [product_out(p) for p in db.scalars(stmt)]


@router.post("/orgs/{org_id}/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(body: ProductIn, request: Request, ctx: OrgContext = Depends(require(P.PRODUCTS_WRITE)),
                   db: Session = Depends(get_db)):
    _check_tax(db, body.default_tax_id)
    product = Product(**body.model_dump())
    db.add(product)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "SKU already used by another product")
    audit.record(db, actor_user_id=ctx.user.id, action="product.created", target_type="product",
                 target_id=product.id, details={"name": product.name, "sku": product.sku},
                 ip_address=client_ip(request))
    _commit_unique_sku(db)
    return product_out(product)


@router.get("/orgs/{org_id}/products/{product_id}", response_model=ProductOut)
def read_product(product_id: str, _: OrgContext = Depends(require(P.PRODUCTS_READ)), db: Session = Depends(get_db)):
    return product_out(get_product(db, product_id))


@router.patch("/orgs/{org_id}/products/{product_id}", response_model=ProductOut)
def update_product(product_id: str, body: ProductUpdate, request: Request,
                   ctx: OrgContext = Depends(require(P.PRODUCTS_WRITE)), db: Session = Depends(get_db)):
    product = get_product(db, product_id)
    changes = body.model_dump(exclude_unset=True)
    if "default_tax_id" in changes:
        _check_tax(db, changes["default_tax_id"])
    for field, value in changes.items():
        if value is None and field not in ("default_tax_id", "sku"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} cannot be null")
        setattr(product, field, value)
    audit.record(db, actor_user_id=ctx.user.id, action="product.updated", target_type="product",
                 target_id=product.id, details={k: str(v) if isinstance(v, Decimal) else v for k, v in changes.items()},
                 ip_address=client_ip(request))
    _commit_unique_sku(db)
    return product_out(product)


@router.delete("/orgs/{org_id}/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_product(product_id: str, request: Request, ctx: OrgContext = Depends(require(P.PRODUCTS_WRITE)),
                   db: Session = Depends(get_db)):
    """Delete a product never invoiced; otherwise archive it."""
    product = get_product(db, product_id)
    used = db.scalar(select(func.count()).select_from(InvoiceLine).where(InvoiceLine.product_id == product.id))
    if used:
        product.active = False
        action = "product.archived"
    else:
        db.delete(product)
        action = "product.deleted"
    audit.record(db, actor_user_id=ctx.user.id, action=action, target_type="product", target_id=product_id,
                 details={"name": product.name}, ip_address=client_ip(request))
    db.commit()
