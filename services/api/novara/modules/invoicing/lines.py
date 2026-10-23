"""Line input shared by invoices and sales orders: product defaults and tax lookup."""

from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from novara.modules.accounting.models import Tax
from novara.modules.catalog.models import Product


class LineIn(BaseModel):
    product_id: str | None = None
    description: str | None = Field(default=None, min_length=1, max_length=500)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    discount_percent: Decimal = Field(default=Decimal(0), ge=0, le=100, max_digits=7, decimal_places=4)
    # Omit to use the product's default tax; send null for no tax.
    tax_id: str | None = None


def resolve_lines(db: Session, lines: list[LineIn]) -> list[dict]:
    """Validate lines against this organization's products and taxes and fill in defaults."""
    out = []
    for i, li in enumerate(lines, start=1):
        product = None
        if li.product_id is not None:
            product = db.get(Product, li.product_id)
            if product is None or not product.active:
                raise _unprocessable(f"line {i}: unknown or archived product")
        description = li.description or (product.name if product else None)
        unit_price = li.unit_price if li.unit_price is not None else (product.sale_price if product else None)
        if description is None or unit_price is None:
            raise _unprocessable(f"line {i}: description and unit_price are required without a product")
        tax_id = li.tax_id if "tax_id" in li.model_fields_set else (product.default_tax_id if product else None)
        rate = Decimal(0)
        if tax_id is not None:
            tax = db.get(Tax, tax_id)
            if tax is None or not tax.active:
                raise _unprocessable(f"line {i}: unknown or inactive tax")
            rate = tax.rate
        out.append(dict(position=i, product_id=li.product_id, description=description, quantity=li.quantity,
                        unit_price=unit_price, discount_percent=li.discount_percent, tax_id=tax_id, tax_rate=rate))
    return out


def _unprocessable(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, msg)
