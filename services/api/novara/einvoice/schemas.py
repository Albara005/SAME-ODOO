"""What an invoice or credit note shows about its e-invoice."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from novara.einvoice.models import EInvoiceDocument


class EInvoiceSummary(BaseModel):
    id: str
    source_type: Literal["invoice", "credit_note"]
    source_id: str
    number: str
    kind: Literal["standard", "simplified"]
    type_code: str
    environment: Literal["sandbox", "simulation", "production"]
    icv: int
    status: Literal["pending", "reported", "cleared", "rejected"]
    warnings: list[str]
    errors: list[str]
    note: str
    attempts: int
    accepted_at: datetime | None
    created_at: datetime


def summary(doc: EInvoiceDocument) -> EInvoiceSummary:
    return EInvoiceSummary(id=doc.id, source_type=doc.source_type, source_id=doc.source_id, number=doc.number, kind=doc.kind,
                           type_code=doc.type_code, environment=doc.environment, icv=doc.icv, status=doc.status,
                           warnings=list(doc.warnings or []), errors=list(doc.errors or []), note=doc.note,
                           attempts=doc.attempts, accepted_at=doc.accepted_at, created_at=doc.created_at)
