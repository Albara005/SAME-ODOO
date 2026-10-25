"""UBL 2.1 XML for ZATCA's e-invoicing (Fatoora) phase 2: tax invoices, credit notes and debit notes.

Standard tax invoices (B2B: the buyer has a VAT number) are cleared by ZATCA before they count;
simplified ones (B2C) are reported within 24 hours. Both carry a counter (ICV) and the hash of the
previous document (PIH), so the documents of one device (EGS unit) form a chain that can't be edited
or reordered without it showing.

The XML here is unsigned; signing.py adds the signature, the QR code and the cac:Signature block.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal

from lxml import etree

INVOICE_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
EXT = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
NSMAP = {None: INVOICE_NS, "cac": CAC, "cbc": CBC, "ext": EXT}

# The PIH of a device's first document: base64 of the hex SHA-256 of "0".
FIRST_PIH = base64.b64encode(hashlib.sha256(b"0").hexdigest().encode()).decode()
TYPE_CODES = {"invoice": "388", "credit": "381", "debit": "383"}
SUBTYPES = {"standard": "0100000", "simplified": "0200000"}
# A zero-rated, exempt or out-of-scope line needs a reason; out of scope is the generic one.
OUT_OF_SCOPE = ("O", "VATEX-SA-OOS", "Not subject to VAT")


@dataclass(frozen=True)
class Address:
    street: str
    building_number: str
    district: str
    city: str
    postal_code: str
    country: str = "SA"


@dataclass(frozen=True)
class Party:
    name: str
    vat_number: str = ""
    address: Address | None = None
    scheme: str = ""        # seller: CRN (commercial registration); a buyer without VAT: NAT, IQA, CRN... or none
    scheme_id: str = ""


@dataclass(frozen=True)
class TaxCategory:
    code: str               # S standard, Z zero rated, E exempt, O out of scope
    percent: Decimal
    exemption_code: str = ""
    exemption_reason: str = ""


@dataclass(frozen=True)
class Line:
    name: str
    quantity: Decimal
    price: Decimal           # the net unit price (after any discount)
    net: Decimal             # quantity x price as invoiced, rounded to the currency
    tax: Decimal             # VAT on this line, rounded
    category: TaxCategory


@dataclass(frozen=True)
class TaxGroup:
    category: TaxCategory
    taxable: Decimal
    tax: Decimal


@dataclass
class Document:
    number: str
    uuid: str
    kind: str                # standard | simplified
    type: str                # invoice | credit | debit
    issue_date: date
    issue_time: time
    currency: str
    icv: int
    pih: str
    seller: Party
    buyer: Party
    lines: list[Line]
    groups: list[TaxGroup]
    supply_date: date | None = None
    billing_reference: str = ""      # credit and debit notes: the invoice they adjust
    reason: str = ""                 # credit and debit notes: why (KSA-10)
    payment_means: str = "10"        # UN/ECE 4461: 10 cash, 30 credit transfer, 42 bank account, 48 card, 1 not defined
    notes: list[str] = field(default_factory=list)

    @property
    def line_total(self) -> Decimal:
        return sum((l.net for l in self.lines), Decimal(0))

    @property
    def tax_total(self) -> Decimal:
        return sum((g.tax for g in self.groups), Decimal(0))

    @property
    def total(self) -> Decimal:
        return self.line_total + self.tax_total


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _qty(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _price(value: Decimal) -> str:
    """At least two decimals, more only when the price has them (up to six, as stored)."""
    text = f"{value.normalize():f}"
    whole, _, frac = text.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def _el(parent, ns: str, tag: str, text: str | None = None, **attrs) -> etree._Element:
    el = etree.SubElement(parent, f"{{{ns}}}{tag}", attrs)
    if text is not None:
        el.text = text
    return el


def _address(parent, a: Address) -> None:
    pa = _el(parent, CAC, "PostalAddress")
    if a.street:
        _el(pa, CBC, "StreetName", a.street)
    if a.building_number:
        _el(pa, CBC, "BuildingNumber", a.building_number)
    if a.district:
        _el(pa, CBC, "CitySubdivisionName", a.district)
    if a.city:
        _el(pa, CBC, "CityName", a.city)
    if a.postal_code:
        _el(pa, CBC, "PostalZone", a.postal_code)
    _el(_el(pa, CAC, "Country"), CBC, "IdentificationCode", a.country or "SA")


def _party(parent, tag: str, p: Party) -> None:
    party = _el(_el(parent, CAC, tag), CAC, "Party")
    if p.scheme and p.scheme_id:
        _el(_el(party, CAC, "PartyIdentification"), CBC, "ID", p.scheme_id, schemeID=p.scheme)
    if p.address is not None:
        _address(party, p.address)
    if p.vat_number:
        pts = _el(party, CAC, "PartyTaxScheme")
        _el(pts, CBC, "CompanyID", p.vat_number)
        _el(_el(pts, CAC, "TaxScheme"), CBC, "ID", "VAT")
    _el(_el(party, CAC, "PartyLegalEntity"), CBC, "RegistrationName", p.name)


def _category(parent, tag: str, c: TaxCategory, *, scheme_attrs: bool) -> None:
    cat = _el(parent, CAC, tag)
    if scheme_attrs:
        _el(cat, CBC, "ID", c.code, schemeID="UN/ECE 5305", schemeAgencyID="6")
    else:
        _el(cat, CBC, "ID", c.code)
    _el(cat, CBC, "Percent", f"{c.percent:.2f}")
    if c.code != "S" and scheme_attrs:
        if c.exemption_code:
            _el(cat, CBC, "TaxExemptionReasonCode", c.exemption_code)
        if c.exemption_reason:
            _el(cat, CBC, "TaxExemptionReason", c.exemption_reason)
    scheme = _el(cat, CAC, "TaxScheme")
    if scheme_attrs:
        _el(scheme, CBC, "ID", "VAT", schemeID="UN/ECE 5153", schemeAgencyID="6")
    else:
        _el(scheme, CBC, "ID", "VAT")


def build(doc: Document) -> etree._Element:
    """The document as an unsigned UBL tree (signing.sign() completes it)."""
    c = doc.currency
    root = etree.Element(f"{{{INVOICE_NS}}}Invoice", nsmap=NSMAP)
    _el(root, CBC, "ProfileID", "reporting:1.0")
    _el(root, CBC, "ID", doc.number)
    _el(root, CBC, "UUID", doc.uuid)
    _el(root, CBC, "IssueDate", doc.issue_date.isoformat())
    _el(root, CBC, "IssueTime", doc.issue_time.strftime("%H:%M:%S"))
    _el(root, CBC, "InvoiceTypeCode", TYPE_CODES[doc.type], name=SUBTYPES[doc.kind])
    for note in doc.notes:
        _el(root, CBC, "Note", note[:1000], languageID="ar")
    _el(root, CBC, "DocumentCurrencyCode", c)
    _el(root, CBC, "TaxCurrencyCode", c)
    if doc.billing_reference:
        _el(_el(_el(root, CAC, "BillingReference"), CAC, "InvoiceDocumentReference"), CBC, "ID", doc.billing_reference)
    icv = _el(root, CAC, "AdditionalDocumentReference")
    _el(icv, CBC, "ID", "ICV")
    _el(icv, CBC, "UUID", str(doc.icv))
    pih = _el(root, CAC, "AdditionalDocumentReference")
    _el(pih, CBC, "ID", "PIH")
    _el(_el(pih, CAC, "Attachment"), CBC, "EmbeddedDocumentBinaryObject", doc.pih, mimeCode="text/plain")
    _party(root, "AccountingSupplierParty", doc.seller)
    _party(root, "AccountingCustomerParty", doc.buyer)
    if doc.supply_date is not None:
        _el(_el(root, CAC, "Delivery"), CBC, "ActualDeliveryDate", doc.supply_date.isoformat())
    means = _el(root, CAC, "PaymentMeans")
    _el(means, CBC, "PaymentMeansCode", doc.payment_means)
    if doc.reason:
        _el(means, CBC, "InstructionNote", doc.reason[:1000])
    # Two TaxTotal blocks: the first just the amount in the tax currency, the second with the breakdown.
    _el(_el(root, CAC, "TaxTotal"), CBC, "TaxAmount", _money(doc.tax_total), currencyID=c)
    breakdown = _el(root, CAC, "TaxTotal")
    _el(breakdown, CBC, "TaxAmount", _money(doc.tax_total), currencyID=c)
    for g in doc.groups:
        sub = _el(breakdown, CAC, "TaxSubtotal")
        _el(sub, CBC, "TaxableAmount", _money(g.taxable), currencyID=c)
        _el(sub, CBC, "TaxAmount", _money(g.tax), currencyID=c)
        _category(sub, "TaxCategory", g.category, scheme_attrs=True)
    totals = _el(root, CAC, "LegalMonetaryTotal")
    _el(totals, CBC, "LineExtensionAmount", _money(doc.line_total), currencyID=c)
    _el(totals, CBC, "TaxExclusiveAmount", _money(doc.line_total), currencyID=c)
    _el(totals, CBC, "TaxInclusiveAmount", _money(doc.total), currencyID=c)
    _el(totals, CBC, "AllowanceTotalAmount", "0.00", currencyID=c)
    _el(totals, CBC, "PrepaidAmount", "0.00", currencyID=c)
    _el(totals, CBC, "PayableAmount", _money(doc.total), currencyID=c)
    for n, line in enumerate(doc.lines, start=1):
        il = _el(root, CAC, "InvoiceLine")
        _el(il, CBC, "ID", str(n))
        _el(il, CBC, "InvoicedQuantity", _qty(line.quantity), unitCode="PCE")
        _el(il, CBC, "LineExtensionAmount", _money(line.net), currencyID=c)
        lt = _el(il, CAC, "TaxTotal")
        _el(lt, CBC, "TaxAmount", _money(line.tax), currencyID=c)
        _el(lt, CBC, "RoundingAmount", _money(line.net + line.tax), currencyID=c)
        item = _el(il, CAC, "Item")
        _el(item, CBC, "Name", line.name[:1000])
        _category(item, "ClassifiedTaxCategory", line.category, scheme_attrs=False)
        _el(_el(il, CAC, "Price"), CBC, "PriceAmount", _price(line.price), currencyID=c)
    return root
