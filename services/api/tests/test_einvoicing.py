"""ZATCA e-invoicing phase 2: onboarding the device, a signed and chained document for every invoice and credit
note, reporting and clearance, and what happens when ZATCA is down or says no. ZATCA here is a stand-in that
issues real certificates for the device's key and checks each document's hash and ECDSA signature."""

from __future__ import annotations

from datetime import date

import httpx
import pytest
from lxml import etree
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from novara.einvoice import signing
from novara.einvoice.models import EInvoiceDocument, EInvoiceSettings
from novara.tenancy import bind_tenant
from novara.worker import send_einvoices
from tests.conftest import requires_pg
from tests.fake_zatca import FakeZatca

OWNER, SALES, ACCT = (f"{n}@einvoice.example.com" for n in ("owner", "sales", "acct"))
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
SELLER = {"environment": "sandbox", "crn": "1010010000", "street": "Prince Sultan", "building_number": "2322",
          "district": "Al Murabba", "city": "Riyadh", "postal_code": "23333", "branch_name": "Riyadh"}
BUYER_ADDRESS = {"address_line": "King Fahd Road", "building_number": "1111", "district": "Al Olaya", "city": "Riyadh",
                 "postal_code": "12222", "country": "SA"}


@pytest.fixture
def zatca(app):
    fake = FakeZatca()
    app.state.einvoice_http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    yield fake
    app.state.einvoice_http.close()


@pytest.fixture
def ei(api):
    for e in (OWNER, SALES, ACCT):
        api.signup(e)
    org = api.create_org(OWNER, "einvoice-co")
    api.add_member(OWNER, org, SALES, "sales")
    api.add_member(OWNER, org, ACCT, "accountant")
    vat = api.req("POST", ACCT, f"/orgs/{org}/taxes", json={"name": "VAT 15%", "rate": "15"}).json()["id"]
    cash = api.req("POST", SALES, f"/orgs/{org}/customers", json={"name": "عميل نقدي"}).json()["id"]
    return {"org": org, "vat": vat, "cash": cash}


def u(s, path):
    return f"/orgs/{s['org']}{path}"


def onboard(api, s):
    api.req("PATCH", OWNER, u(s, ""), json={"vat_number": "399999999900003"})
    assert api.req("PUT", OWNER, u(s, "/einvoicing"), json=SELLER).status_code == 200
    r = api.req("POST", OWNER, u(s, "/einvoicing/onboard"), json={"otp": "123345"})
    assert r.status_code == 200, r.text
    return r.json()


def post_invoice(api, s, customer, lines=None, day=None):
    lines = lines or [{"description": "قهوة", "quantity": "2", "unit_price": "50", "tax_id": s["vat"]}]
    inv = api.req("POST", SALES, u(s, "/invoices"), json={"customer_id": customer, "issue_date": day or date.today().isoformat(),
                                                          "lines": lines})
    assert inv.status_code == 201, inv.text
    return api.req("POST", ACCT, u(s, f"/invoices/{inv.json()['id']}/post"))


def stored(app, s, number):
    with app.state.sessionmaker() as db:
        bind_tenant(db, s["org"])
        return db.scalar(select(EInvoiceDocument).where(EInvoiceDocument.number == number))


def test_onboarding_needs_the_seller_details_and_zatcas_one_time_password(api, ei, zatca, app):
    first = api.req("GET", OWNER, u(ei, "/einvoicing")).json()
    assert first["status"] == "off" and any("VAT number" in p for p in first["problems"])
    assert api.req("GET", ACCT, u(ei, "/einvoicing")).status_code == 403
    missing = api.req("POST", OWNER, u(ei, "/einvoicing/onboard"), json={"otp": "123345"})
    assert missing.status_code == 422 and "building number" in missing.json()["detail"]
    api.req("PATCH", OWNER, u(ei, ""), json={"vat_number": "399999999900003"})
    api.req("PUT", OWNER, u(ei, "/einvoicing"), json=SELLER)
    wrong = api.req("POST", OWNER, u(ei, "/einvoicing/onboard"), json={"otp": "999999"})
    assert wrong.status_code == 422 and "Invalid OTP" in wrong.json()["detail"]
    done = api.req("POST", OWNER, u(ei, "/einvoicing/onboard"), json={"otp": "123345"}).json()
    assert (done["status"], done["problems"]) == ("onboarded", []) and done["certificate_expires_at"]
    # The six sample documents were checked before the production certificate was asked for.
    assert zatca.calls == ["/compliance", "/compliance"] + ["/compliance/invoices"] * 6 + ["/production/csids"]
    with app.state.sessionmaker() as db:
        bind_tenant(db, ei["org"])
        row = db.scalar(select(EInvoiceSettings))
        assert row.private_key.startswith("v1:") and "PRIVATE KEY" not in row.private_key
    assert api.req("PUT", OWNER, u(ei, "/einvoicing"), json=SELLER | {"environment": "production"}).status_code == 409
    assert api.req("GET", SALES, u(ei, "/einvoicing/status")).json() == {"active": True, "environment": "sandbox"}


def test_every_posted_invoice_is_signed_chained_and_sent(api, ei, zatca, app):
    onboard(api, ei)
    first = post_invoice(api, ei, ei["cash"])
    assert first.status_code == 200, first.text
    first = first.json()
    doc = first["einvoice"]
    assert (doc["kind"], doc["status"], doc["icv"], doc["type_code"]) == ("simplified", "reported", 1, "388")
    qr = signing.read_tlv(first["zatca_qr"])
    assert (qr[1].decode(), qr[2].decode(), qr[4].decode(), qr[5].decode()) == ("Einvoice-Co", "399999999900003", "115.00", "15.00")
    assert len(qr) == 9                                                    # simplified: with the certificate's signature
    one = stored(app, ei, first["number"])
    assert signing.verify(one.xml.encode())
    xml = api.req("GET", ACCT, u(ei, f"/einvoicing/documents/{doc['id']}/xml"))
    assert xml.status_code == 200 and xml.headers["content-type"].startswith("application/xml")

    second = post_invoice(api, ei, ei["cash"]).json()
    two = stored(app, ei, second["number"])
    pih = etree.fromstring(two.xml.encode()).findtext(
        f".//{{{CAC}}}AdditionalDocumentReference[{{{CBC}}}ID='PIH']/{{{CAC}}}Attachment/{{{CBC}}}EmbeddedDocumentBinaryObject")
    assert (two.icv, pih) == (2, one.invoice_hash)                         # the chain

    # A buyer with a Saudi VAT number gets a standard invoice, which needs their national address.
    b2b = api.req("POST", SALES, u(ei, "/customers"), json={"name": "شركة النخبة", "tax_number": "399999999800003"}).json()
    refused = post_invoice(api, ei, b2b["id"])
    assert refused.status_code == 422 and "building number" in refused.json()["detail"]
    api.req("PATCH", SALES, u(ei, f"/customers/{b2b['id']}"), json=BUYER_ADDRESS)
    std = post_invoice(api, ei, b2b["id"]).json()
    assert (std["einvoice"]["kind"], std["einvoice"]["status"]) == ("standard", "cleared")
    assert len(signing.read_tlv(std["zatca_qr"])) == 8
    stamped = stored(app, ei, std["number"])
    assert stamped.cleared_xml and "<cbc:CompanyID>399999999800003</cbc:CompanyID>" in stamped.cleared_xml
    listed = api.req("GET", SALES, u(ei, "/einvoicing/documents")).json()
    assert [d["icv"] for d in listed] == [3, 2, 1]


def test_zatca_down_or_saying_no(api, ei, zatca, app):
    onboard(api, ei)
    zatca.down = True
    posted = post_invoice(api, ei, ei["cash"])
    assert posted.status_code == 200                                       # the invoice is posted all the same
    doc = posted.json()["einvoice"]
    assert doc["status"] == "pending" and "HTTP 503" in doc["note"] and "sent again" in doc["note"]
    assert api.req("POST", SALES, u(ei, f"/einvoicing/documents/{doc['id']}/send")).status_code == 403
    again = api.req("POST", ACCT, u(ei, f"/einvoicing/documents/{doc['id']}/send")).json()
    assert again["status"] == "pending" and again["attempts"] == 2
    zatca.down = False
    later = post_invoice(api, ei, ei["cash"]).json()["einvoice"]         # sent at once
    assert later["status"] == "reported"
    assert send_einvoices(app.state.sessionmaker, app.state.settings, http=app.state.einvoice_http) == {ei["org"]: 1}
    assert stored(app, ei, doc["number"]).status == "reported"
    assert api.req("POST", ACCT, u(ei, f"/einvoicing/documents/{doc['id']}/send")).status_code == 409

    zatca.refuse_with = ["The invoice issue date can't be in the future"]
    rejected = post_invoice(api, ei, ei["cash"]).json()["einvoice"]
    assert rejected["status"] == "rejected" and any("future" in e for e in rejected["errors"])
    assert api.req("GET", OWNER, u(ei, "/einvoicing")).json()["rejected"] == 1


def test_credit_notes_and_tax_categories(api, ei, zatca, app):
    onboard(api, ei)
    assert api.req("POST", ACCT, u(ei, "/taxes"), json={"name": "Bad", "rate": "0", "zatca_category": "S"}).status_code == 422
    assert api.req("POST", ACCT, u(ei, "/taxes"), json={"name": "Bad", "rate": "0", "zatca_category": "Z"}).status_code == 422
    export = api.req("POST", ACCT, u(ei, "/taxes"), json={"name": "Export", "rate": "0", "zatca_category": "Z",
                                                          "exemption_code": "VATEX-SA-32", "exemption_reason": "Export of goods"})
    assert export.status_code == 201 and export.json()["zatca_category"] == "Z"
    inv = post_invoice(api, ei, ei["cash"], lines=[
        {"description": "Chair", "quantity": "3", "unit_price": "100", "discount_percent": "10", "tax_id": ei["vat"]},
        {"description": "Shipping abroad", "quantity": "1", "unit_price": "40", "tax_id": export.json()["id"]},
        {"description": "Fee", "quantity": "1", "unit_price": "5"}]).json()
    root = etree.fromstring(stored(app, ei, inv["number"]).xml.encode())
    cats = {c.findtext(f"{{{CBC}}}ID"): (c.findtext(f"{{{CBC}}}Percent"), c.findtext(f"{{{CBC}}}TaxExemptionReasonCode"))
            for c in root.iter(f"{{{CAC}}}TaxCategory")}
    assert cats == {"S": ("15.00", None), "Z": ("0.00", "VATEX-SA-32"), "O": ("0.00", "VATEX-SA-OOS")}
    assert root.findtext(f".//{{{CAC}}}LegalMonetaryTotal/{{{CBC}}}PayableAmount") == "355.50"   # 270 + 40.50 VAT + 40 + 5
    assert root.findtext(f".//{{{CAC}}}InvoiceLine/{{{CAC}}}Price/{{{CBC}}}PriceAmount") == "90.00"    # after the discount

    note = api.req("POST", ACCT, u(ei, f"/invoices/{inv['id']}/credit-notes"), json={
        "issue_date": date.today().isoformat(), "reason": "Damaged chair",
        "lines": [{"invoice_line_id": inv["lines"][0]["id"], "quantity": "1"}]}).json()
    assert (note["einvoice"]["type_code"], note["einvoice"]["status"], note["einvoice"]["icv"]) == ("381", "reported", 2)
    cn = etree.fromstring(stored(app, ei, note["number"]).xml.encode())
    assert cn.findtext(f".//{{{CAC}}}BillingReference/{{{CAC}}}InvoiceDocumentReference/{{{CBC}}}ID") == inv["number"]
    assert cn.findtext(f".//{{{CAC}}}PaymentMeans/{{{CBC}}}InstructionNote") == "Damaged chair"


def test_turned_off_means_no_documents(api, ei, zatca):
    onboard(api, ei)
    assert api.req("POST", OWNER, u(ei, "/einvoicing/off")).json()["status"] == "off"
    inv = post_invoice(api, ei, ei["cash"]).json()
    assert inv["einvoice"] is None and inv["zatca_qr"] is not None        # phase 1 QR as before


@requires_pg
def test_the_database_keeps_issued_documents_as_signed(api, ei, zatca, app):
    onboard(api, ei)
    number = post_invoice(api, ei, ei["cash"]).json()["number"]
    for sql in ("UPDATE einvoice_documents SET xml = 'x' WHERE number = :n",
                "UPDATE einvoice_documents SET status = 'pending' WHERE number = :n",
                "DELETE FROM einvoice_documents WHERE number = :n"):
        with app.state.sessionmaker() as db:
            bind_tenant(db, ei["org"])
            with pytest.raises(DBAPIError, match="e-invoice|permission denied"):
                db.execute(text(sql), {"n": number})
                db.commit()
