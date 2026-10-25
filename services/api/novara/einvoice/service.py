"""E-invoicing with ZATCA (Fatoora phase 2): onboarding the organization's device, and a signed document for
every invoice and credit note posted after that.

Onboarding (someone with org.manage, with a one-time password from the Fatoora portal):
    a new key and CSR -> compliance certificate -> the six sample documents ZATCA checks (standard and
    simplified invoice, credit note and debit note) -> production certificate. The key and the
    certificates' secrets are kept encrypted for the organization.

Issuing: when an invoice is posted or a credit note created, its document is built, given the next counter
value (ICV) and the previous document's hash (PIH), signed, and stored in the same transaction, so the
chain has no gaps and follows the order of posting. It is then sent: cleared (standard: the buyer has a
Saudi VAT number) or reported (simplified). If ZATCA doesn't answer, the document stays pending and is sent
again later (the worker, or "send again"); what ZATCA says is kept with it.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from novara import crypto
from novara.db import utcnow
from novara.einvoice import client as fatoora, csr, signing, ubl
from novara.einvoice.models import EInvoiceDocument, EInvoiceSettings
from novara.modules.accounting.models import Tax
from novara.modules.invoicing.models import CreditNote, CreditNoteLine, CreditNoteTax, Invoice
from novara.modules.invoicing.service import lines_of, tax_groups
from novara.modules.organizations.models import Organization
from novara.modules.partners.models import Customer
from novara.money import from_minor, round_money
from novara.tenancy import current_tenant, system_scope

SAUDI_VAT = re.compile(r"^3\d{13}3$")
SAMPLE_BUYER = ubl.Party(name="Sample buyer", vat_number="399999999800003",
                         address=ubl.Address("King Fahd Road", "1111", "Al Olaya", "Riyadh", "12222"))


class EInvoiceError(ValueError):
    """Something the organization has to fix first (HTTP 422)."""


class EInvoiceState(RuntimeError):
    """Not possible in the current state (HTTP 409)."""


# ---------------------------------------------------------------- settings


def settings_row(db: Session) -> EInvoiceSettings:
    row = db.scalar(select(EInvoiceSettings))
    if row is None:
        row = EInvoiceSettings()
        db.add(row)
        db.flush()
    return row


def active(db: Session) -> EInvoiceSettings | None:
    row = db.scalar(select(EInvoiceSettings).where(EInvoiceSettings.status == "onboarded"))
    return row


def seller_problems(org: Organization, s: EInvoiceSettings) -> list[str]:
    """What's missing before the device can be onboarded (ZATCA's rules for the seller)."""
    problems = []
    if not SAUDI_VAT.match(org.vat_number or ""):
        problems.append("the organization's VAT number must be 15 digits, starting and ending with 3")
    if not re.fullmatch(r"\d{10}", s.crn):
        problems.append("the commercial registration number must be 10 digits")
    if not re.fullmatch(r"\d{4}", s.building_number):
        problems.append("the building number must be 4 digits")
    if not re.fullmatch(r"\d{5}", s.postal_code):
        problems.append("the postal code must be 5 digits")
    for field, label in (("street", "street"), ("district", "district"), ("city", "city")):
        if not getattr(s, field).strip():
            problems.append(f"the {label} is missing")
    return problems


def seller(org: Organization, s: EInvoiceSettings) -> ubl.Party:
    return ubl.Party(name=org.name, vat_number=org.vat_number, scheme="CRN", scheme_id=s.crn,
                     address=ubl.Address(s.street, s.building_number, s.district, s.city, s.postal_code))


def _secret(db: Session, settings, value: str | None) -> str:
    return crypto.decrypt(settings, value, context=current_tenant(db)) if value else ""


def _seal(db: Session, settings, value: str) -> str:
    return crypto.encrypt(settings, value, context=current_tenant(db))


def _credentials(db: Session, settings, s: EInvoiceSettings, which: str) -> fatoora.Credentials:
    return fatoora.Credentials(request_id=s.compliance_request_id, token=_secret(db, settings, getattr(s, f"{which}_token")),
                               secret=_secret(db, settings, getattr(s, f"{which}_secret")))


def _samples(org: Organization, s: EInvoiceSettings, today: date) -> list[ubl.Document]:
    """The six documents ZATCA checks before issuing a production certificate."""
    s15 = ubl.TaxCategory("S", Decimal("15"))
    line = [ubl.Line("Sample item", Decimal(1), Decimal(100), Decimal("100.00"), Decimal("15.00"), s15)]
    groups = [ubl.TaxGroup(s15, Decimal("100.00"), Decimal("15.00"))]
    docs, pih = [], ubl.FIRST_PIH
    now = datetime.now(timezone.utc).time().replace(microsecond=0)
    for n, (kind, type_) in enumerate([(k, t) for k in ("standard", "simplified") for t in ("invoice", "credit", "debit")], 1):
        docs.append(ubl.Document(
            number=f"SAMPLE-{n}", uuid=str(uuid.uuid4()), kind=kind, type=type_, issue_date=today, issue_time=now,
            currency="SAR", icv=n, pih=pih, seller=seller(org, s), buyer=SAMPLE_BUYER if kind == "standard" else ubl.Party("Sample buyer"),
            lines=line, groups=groups, supply_date=today, billing_reference="SAMPLE-0" if type_ != "invoice" else "",
            reason="Sample adjustment" if type_ != "invoice" else ""))
    return docs


def onboard(db: Session, settings, s: EInvoiceSettings, *, otp: str, user_id: str, http=None) -> None:
    """Get the device its certificates: compliance, the six sample checks, then production."""
    org = db.get(Organization, current_tenant(db))
    problems = seller_problems(org, s)
    if problems:
        raise EInvoiceError("; ".join(problems))
    api = fatoora.Fatoora(s.environment, http)
    key = csr.new_key()
    request = csr.request(key, environment=s.environment, common_name=f"NOVARA-{org.vat_number}", organization=org.name,
                          unit=s.branch_name or org.name, vat_number=org.vat_number, serial=f"1-NOVARA|2-1|3-{s.id}",
                          location=s.city, industry=s.industry or "Supply activities")
    try:
        compliance = api.compliance_csid(request, otp)
        for sample in _samples(org, s, date.today()):
            signed = signing.sign(sample, key, compliance.certificate)
            result = api.check(compliance, signed.invoice_hash, sample.uuid, signed.xml)
            if not result.accepted:
                raise EInvoiceError(f"ZATCA refused the sample {sample.kind} {sample.type}: " + "; ".join(result.errors)[:400])
        production = api.production_csid(compliance)
    except fatoora.FatooraRefused as e:
        raise EInvoiceError(f"ZATCA refused: {e}") from None
    except fatoora.FatooraUnavailable as e:
        raise EInvoiceState(f"{e}; try again in a few minutes") from None
    s.private_key = _seal(db, settings, csr.key_pem(key))
    s.compliance_request_id = compliance.request_id
    s.compliance_token, s.compliance_secret = _seal(db, settings, compliance.token), _seal(db, settings, compliance.secret)
    s.production_token, s.production_secret = _seal(db, settings, production.token), _seal(db, settings, production.secret)
    s.certificate_expires_at = signing.certificate(production.certificate).not_valid_after_utc
    s.status, s.onboarded_at, s.onboarded_by, s.last_error = "onboarded", utcnow(), user_id, ""
    s.icv, s.last_hash = 0, ubl.FIRST_PIH                 # a new device starts a new chain


# ---------------------------------------------------------------- documents


def kind_for(customer: Customer) -> str:
    """Standard (cleared, B2B) when the buyer has a Saudi VAT number; simplified (reported) otherwise."""
    return "standard" if SAUDI_VAT.match((customer.tax_number or "").strip()) else "simplified"


def buyer_problems(customer: Customer) -> list[str]:
    """What a standard e-invoice needs from a Saudi buyer's address (BR-KSA-63)."""
    if kind_for(customer) != "standard" or (customer.country or "SA") != "SA":
        return []
    problems = []
    if not customer.address_line.strip():
        problems.append("street")
    if not re.fullmatch(r"\d{4}", customer.building_number or ""):
        problems.append("building number (4 digits)")
    if not customer.district.strip():
        problems.append("district")
    if not customer.city.strip():
        problems.append("city")
    if not re.fullmatch(r"\d{5}", customer.postal_code or ""):
        problems.append("postal code (5 digits)")
    return problems


def check_buyer(db: Session, customer_id: str) -> None:
    if active(db) is None:
        return
    customer = db.get(Customer, customer_id)
    missing = buyer_problems(customer)
    if missing:
        raise EInvoiceError(f"{customer.name} has a VAT number, so this is a standard e-invoice and their address needs: "
                            + ", ".join(missing))


def _buyer(customer: Customer, kind: str) -> ubl.Party:
    if kind == "simplified":
        return ubl.Party(name=customer.name)
    return ubl.Party(name=customer.name, vat_number=customer.tax_number.strip(), address=ubl.Address(
        customer.address_line.strip(), customer.building_number, customer.district.strip(), customer.city.strip(),
        customer.postal_code, customer.country or "SA"))


def category(tax: Tax | None, rate: Decimal) -> ubl.TaxCategory:
    if tax is None or (not tax.zatca_category and rate == 0):
        code, exemption, reason = ubl.OUT_OF_SCOPE
        if tax is not None and tax.exemption_code:
            exemption, reason = tax.exemption_code, tax.exemption_reason or reason
        return ubl.TaxCategory(code, Decimal(0), exemption, reason)
    code = tax.zatca_category or ("S" if rate > 0 else "O")
    if code == "S":
        return ubl.TaxCategory("S", rate)
    return ubl.TaxCategory(code, Decimal(0), tax.exemption_code or ubl.OUT_OF_SCOPE[1],
                           tax.exemption_reason or ("Not subject to VAT" if code == "O" else ""))


def _net_price(unit_price: Decimal, discount_percent: Decimal) -> Decimal:
    return (unit_price * (Decimal(100) - discount_percent) / Decimal(100)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _lines(rows, taxes: dict[str, Tax], currency: str) -> list[ubl.Line]:
    out = []
    for l in rows:
        net = from_minor(l.net_minor, currency)
        tax = taxes.get(l.tax_id) if l.tax_id else None
        cat = category(tax, l.tax_rate if tax else Decimal(0))
        out.append(ubl.Line(name=l.description, quantity=l.quantity, price=_net_price(l.unit_price, l.discount_percent),
                            net=net, tax=round_money(net * cat.percent / 100, currency), category=cat))
    return out


def _groups(per_tax: list[tuple[str | None, Decimal, int, int]], taxes: dict[str, Tax], currency: str) -> list[ubl.TaxGroup]:
    """One subtotal per category and rate (taxes with the same category and rate are added up)."""
    merged: dict[tuple, list] = {}
    for tax_id, rate, base, tax in per_tax:
        cat = category(taxes.get(tax_id) if tax_id else None, rate if tax_id else Decimal(0))
        key = (cat.code, cat.percent, cat.exemption_code)
        entry = merged.setdefault(key, [cat, 0, 0])
        entry[1] += base
        entry[2] += tax
    return [ubl.TaxGroup(cat, from_minor(base, currency), from_minor(tax, currency)) for cat, base, tax in merged.values()]


def _utc(moment: datetime | None) -> datetime:
    """A stored time in UTC (SQLite hands them back without a zone)."""
    if moment is None:
        return utcnow()
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _taxes(db: Session, ids) -> dict[str, Tax]:
    ids = {i for i in ids if i}
    return {t.id: t for t in db.scalars(select(Tax).where(Tax.id.in_(ids)))} if ids else {}


def invoice_document(db: Session, inv: Invoice) -> ubl.Document:
    org = db.get(Organization, inv.organization_id)
    customer = db.get(Customer, inv.customer_id)
    lines = lines_of(db, inv)
    taxes = _taxes(db, (l.tax_id for l in lines))
    kind = kind_for(customer)
    per_tax = [(g.tax_id, g.rate, g.base_minor, g.tax_minor) for g in tax_groups(lines, inv.currency)]
    untaxed = sum(l.net_minor for l in lines if not l.tax_id)
    if untaxed:
        per_tax.append((None, Decimal(0), untaxed, 0))
    posted = _utc(inv.posted_at)
    return ubl.Document(number=inv.number, uuid="", kind=kind, type="invoice", issue_date=inv.issue_date,
                        issue_time=posted.time().replace(microsecond=0, tzinfo=None), currency=inv.currency, icv=0, pih="",
                        seller=seller(org, active(db)), buyer=_buyer(customer, kind),
                        lines=_lines(lines, taxes, inv.currency), groups=_groups(per_tax, taxes, inv.currency),
                        supply_date=inv.issue_date, payment_means="30" if kind == "standard" else "10")


def credit_document(db: Session, note: CreditNote) -> ubl.Document:
    org = db.get(Organization, note.organization_id)
    customer = db.get(Customer, note.customer_id)
    invoice = db.get(Invoice, note.invoice_id)
    lines = list(db.scalars(select(CreditNoteLine).where(CreditNoteLine.credit_note_id == note.id).order_by(CreditNoteLine.position)))
    taxes = _taxes(db, (l.tax_id for l in lines))
    kind = kind_for(customer)
    per_tax = [(t.tax_id, t.rate, t.base_minor, t.tax_minor)
               for t in db.scalars(select(CreditNoteTax).where(CreditNoteTax.credit_note_id == note.id))]
    untaxed = sum(l.net_minor for l in lines if not l.tax_id)
    if untaxed:
        per_tax.append((None, Decimal(0), untaxed, 0))
    created = _utc(note.created_at)
    return ubl.Document(number=note.number, uuid="", kind=kind, type="credit", issue_date=note.issue_date,
                        issue_time=created.time().replace(microsecond=0, tzinfo=None), currency=note.currency, icv=0, pih="",
                        seller=seller(org, active(db)), buyer=_buyer(customer, kind),
                        lines=_lines(lines, taxes, note.currency), groups=_groups(per_tax, taxes, note.currency),
                        supply_date=note.issue_date, billing_reference=invoice.number,
                        reason=note.reason or "Credit note", payment_means="30" if kind == "standard" else "10")


def issue(db: Session, settings, source_type: str, source_id: str, doc: ubl.Document) -> EInvoiceDocument:
    """Sign the document as the device's next one (locks the chain until the transaction ends) and store it."""
    s = db.scalar(select(EInvoiceSettings).where(EInvoiceSettings.status == "onboarded").with_for_update())
    if s is None:
        raise EInvoiceState("e-invoicing isn't set up")
    if doc.currency != "SAR":
        raise EInvoiceError("ZATCA e-invoices are issued in SAR")
    s.icv += 1
    doc.icv, doc.pih, doc.uuid = s.icv, s.last_hash, str(uuid.uuid4())
    key = csr.load_key(_secret(db, settings, s.private_key))
    signed = signing.sign(doc, key, _credentials(db, settings, s, "production").certificate)
    s.last_hash = signed.invoice_hash
    record = EInvoiceDocument(source_type=source_type, source_id=source_id, number=doc.number, kind=doc.kind,
                              type_code=ubl.TYPE_CODES[doc.type], environment=s.environment, icv=doc.icv, uuid=doc.uuid,
                              invoice_hash=signed.invoice_hash, xml=signed.xml.decode(), qr=signed.qr, status="pending")
    db.add(record)
    db.flush()
    return record


def issue_for_invoice(db: Session, settings, inv: Invoice) -> EInvoiceDocument | None:
    return issue(db, settings, "invoice", inv.id, invoice_document(db, inv)) if active(db) else None


def issue_for_credit_note(db: Session, settings, note: CreditNote) -> EInvoiceDocument | None:
    return issue(db, settings, "credit_note", note.id, credit_document(db, note)) if active(db) else None


def submit(db: Session, settings, doc: EInvoiceDocument, http=None) -> EInvoiceDocument:
    """Send a pending document: clearance for standard, reporting for simplified."""
    if doc.status in ("reported", "cleared"):
        return doc
    s = db.scalar(select(EInvoiceSettings))
    api = fatoora.Fatoora(doc.environment, http)
    creds = _credentials(db, settings, s, "production")
    doc.attempts += 1
    doc.last_attempt_at = utcnow()
    try:
        send = api.clear if doc.kind == "standard" else api.report
        result = send(creds, doc.invoice_hash, doc.uuid, doc.xml.encode())
    except (fatoora.FatooraUnavailable, fatoora.FatooraRefused) as e:
        doc.note = f"{e}; it will be sent again"[:300]
        return doc
    doc.warnings, doc.errors, doc.note = result.warnings, result.errors, ""
    if result.accepted:
        doc.status = "cleared" if doc.kind == "standard" else "reported"
        doc.accepted_at = utcnow()
        if result.cleared_xml:
            doc.cleared_xml = result.cleared_xml.decode()
    else:
        doc.status = "rejected"
    return doc


def qr_of(doc: EInvoiceDocument) -> str:
    """The QR code to print: ZATCA's own on a cleared document, ours otherwise."""
    if doc.cleared_xml:
        found = re.search(r"<cbc:ID>QR</cbc:ID>\s*<cac:Attachment>\s*<cbc:EmbeddedDocumentBinaryObject[^>]*>([^<]+)<",
                          doc.cleared_xml)
        if found:
            return found.group(1).strip()
    return doc.qr


def document_for(db: Session, source_type: str, source_id: str) -> EInvoiceDocument | None:
    return db.scalar(select(EInvoiceDocument).where(EInvoiceDocument.source_type == source_type,
                                                    EInvoiceDocument.source_id == source_id))


def organizations_with_pending(db: Session) -> list[str]:
    if db.get_bind().dialect.name == "postgresql":
        return list(db.execute(text("SELECT app_orgs_with_pending_einvoices()")).scalars())
    with system_scope(db):
        return list(db.scalars(select(EInvoiceDocument.organization_id).where(EInvoiceDocument.status == "pending").distinct()))


def send_pending(db: Session, settings, http=None, limit: int = 50) -> int:
    """Send the bound organization's pending documents, oldest first (in chain order)."""
    sent = 0
    for doc in db.scalars(select(EInvoiceDocument).where(EInvoiceDocument.status == "pending")
                          .order_by(EInvoiceDocument.icv).limit(limit)):
        submit(db, settings, doc, http)
        sent += 1
        if doc.status == "pending":      # ZATCA is unavailable: stop, keep the order
            break
    return sent
