from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit
from novara.modules.invoicing.models import Invoice
from novara.modules.partners.models import Customer

router = APIRouter(tags=["customers"])


class CustomerFields(BaseModel):
    kind: str = Field(default="company", pattern="^(company|individual)$")
    email: EmailStr | None = None
    phone: str = Field(default="", max_length=40)
    tax_number: str = Field(default="", max_length=40)
    commercial_registration: str = Field(default="", max_length=40)
    address_line: str = Field(default="", max_length=300)
    city: str = Field(default="", max_length=100)
    postal_code: str = Field(default="", max_length=20)
    country: str = Field(default="", pattern="^([A-Z]{2})?$")
    notes: str = Field(default="", max_length=5000)


class CustomerIn(CustomerFields):
    name: str = Field(min_length=1, max_length=200)


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: str | None = Field(default=None, pattern="^(company|individual)$")
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    tax_number: str | None = Field(default=None, max_length=40)
    commercial_registration: str | None = Field(default=None, max_length=40)
    address_line: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, pattern="^([A-Z]{2})?$")
    notes: str | None = Field(default=None, max_length=5000)
    active: bool | None = None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: str
    email: str
    phone: str
    tax_number: str
    commercial_registration: str
    address_line: str
    city: str
    postal_code: str
    country: str
    notes: str
    active: bool
    created_at: datetime


def get_customer(db: Session, customer_id: str) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
    return customer


@router.get("/orgs/{org_id}/customers", response_model=list[CustomerOut])
def list_customers(
    q: str | None = Query(default=None, max_length=100),
    include_archived: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: OrgContext = Depends(require(P.CUSTOMERS_READ)),
    db: Session = Depends(get_db),
):
    stmt = select(Customer).order_by(Customer.name).limit(limit).offset(offset)
    if not include_archived:
        stmt = stmt.where(Customer.active.is_(True))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Customer.name).like(like), func.lower(Customer.email).like(like),
                              Customer.tax_number.like(like), Customer.phone.like(like)))
    return db.scalars(stmt).all()


@router.post("/orgs/{org_id}/customers", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(body: CustomerIn, request: Request, ctx: OrgContext = Depends(require(P.CUSTOMERS_WRITE)),
                    db: Session = Depends(get_db)):
    customer = add_customer(db, ctx, body, ip=client_ip(request))
    db.commit()
    return customer


def add_customer(db: Session, ctx: OrgContext, body: CustomerIn, *, ip: str = "", via: str | None = None) -> Customer:
    """Create a customer and its audit entry in the current transaction (no commit)."""
    data = body.model_dump()
    data["email"] = (data["email"] or "").lower()
    customer = Customer(**data, created_by=ctx.user.id)
    db.add(customer)
    db.flush()
    details = {"name": customer.name} | ({"via": via} if via else {})
    audit.record(db, actor_user_id=ctx.user.id, action="customer.created", target_type="customer",
                 target_id=customer.id, details=details, ip_address=ip)
    return customer


@router.get("/orgs/{org_id}/customers/{customer_id}", response_model=CustomerOut)
def read_customer(customer_id: str, _: OrgContext = Depends(require(P.CUSTOMERS_READ)), db: Session = Depends(get_db)):
    return get_customer(db, customer_id)


@router.patch("/orgs/{org_id}/customers/{customer_id}", response_model=CustomerOut)
def update_customer(customer_id: str, body: CustomerUpdate, request: Request,
                    ctx: OrgContext = Depends(require(P.CUSTOMERS_WRITE)), db: Session = Depends(get_db)):
    customer = get_customer(db, customer_id)
    changes = body.model_dump(exclude_unset=True)
    if "email" in changes:
        changes["email"] = (changes["email"] or "").lower()
    for field, value in changes.items():
        if value is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} cannot be null")
        setattr(customer, field, value)
    audit.record(db, actor_user_id=ctx.user.id, action="customer.updated", target_type="customer",
                 target_id=customer.id, details=changes, ip_address=client_ip(request))
    db.commit()
    return customer


@router.delete("/orgs/{org_id}/customers/{customer_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_customer(customer_id: str, request: Request, ctx: OrgContext = Depends(require(P.CUSTOMERS_WRITE)),
                    db: Session = Depends(get_db)):
    """Delete a customer with no invoices; customers with history are archived instead."""
    customer = get_customer(db, customer_id)
    has_history = db.scalar(select(func.count()).select_from(Invoice).where(Invoice.customer_id == customer.id))
    if has_history:
        customer.active = False
        action = "customer.archived"
    else:
        db.delete(customer)
        action = "customer.deleted"
    audit.record(db, actor_user_id=ctx.user.id, action=action, target_type="customer", target_id=customer_id,
                 details={"name": customer.name}, ip_address=client_ip(request))
    db.commit()
