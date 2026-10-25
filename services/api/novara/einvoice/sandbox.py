"""End-to-end check against ZATCA's developer sandbox, through the whole API:

    python -m novara.einvoice.sandbox

Creates a throwaway organization in a temporary SQLite database, onboards its device with the sandbox's
public one-time password (123345), posts a simplified invoice (cash customer), a standard one (a buyer with a
VAT number and address) with a discount, a second tax rate and an untaxed line, and credits both. Every
document must come back reported or cleared, without errors. Needs network access to gw-fatoora.zatca.gov.sa;
it is not part of the test suite (run by hand, or the "ZATCA sandbox" workflow).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from novara.config import Settings
from novara.mail import MemoryMailer
from novara.main import create_app

EMAIL, PASSWORD = "sandbox@novara.example.com", "sandbox-password-123"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(env="test", database_url=f"sqlite:///{Path(tmp) / 'sandbox.db'}",
                            jwt_secret="sandbox-check-secret-that-is-long-enough", storage_root=str(Path(tmp) / "files"))
        c = TestClient(create_app(settings, mailer=MemoryMailer()))
        c.post("/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD, "full_name": "Sandbox"})
        token = c.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        def call(method: str, path: str, **kw):
            r = c.request(method, f"/api/v1{path}", headers=h, **kw)
            if r.status_code >= 400:
                raise SystemExit(f"{method} {path}: {r.status_code} {r.text}")
            return r.json()

        org = call("POST", "/orgs", json={"name": "NOVARA Sandbox Co", "slug": "novara-sandbox"})["id"]
        o = lambda p: f"/orgs/{org}{p}"  # noqa: E731
        call("PATCH", o(""), json={"vat_number": "399999999900003"})
        call("PUT", o("/einvoicing"), json={"environment": "sandbox", "crn": "1010010000", "street": "Prince Sultan",
                                            "building_number": "2322", "district": "Al Murabba", "city": "Riyadh",
                                            "postal_code": "23333", "branch_name": "Riyadh", "industry": "Retail"})
        status = call("POST", o("/einvoicing/onboard"), json={"otp": "123345"})
        print("onboarded:", status["status"], "certificate valid until", status["certificate_expires_at"])
        vat15 = call("POST", o("/taxes"), json={"name": "VAT 15%", "rate": "15"})["id"]
        export = call("POST", o("/taxes"), json={"name": "Exports 0%", "rate": "0", "zatca_category": "Z",
                                                 "exemption_code": "VATEX-SA-32", "exemption_reason": "Export of goods"})["id"]
        cash = call("POST", o("/customers"), json={"name": "عميل نقدي"})["id"]
        b2b = call("POST", o("/customers"), json={
            "name": "شركة النخبة", "tax_number": "399999999800003", "address_line": "King Fahd Road",
            "building_number": "1111", "district": "Al Olaya", "city": "Riyadh", "postal_code": "12222", "country": "SA"})["id"]
        failures = 0
        for customer, lines in ((cash, [{"description": "قهوة عربية", "quantity": "3", "unit_price": "45.50", "tax_id": vat15}]),
                                (b2b, [{"description": "كرسي مكتب", "quantity": "4", "unit_price": "333.335",
                                        "discount_percent": "7.5", "tax_id": vat15},
                                       {"description": "شحن للخارج", "quantity": "1", "unit_price": "120", "tax_id": export},
                                       {"description": "رسوم خدمة", "quantity": "1", "unit_price": "10"}])):
            inv = call("POST", o("/invoices"), json={"customer_id": customer, "issue_date": _today(), "lines": lines})
            inv = call("POST", o(f"/invoices/{inv['id']}/post"))
            failures += _report(inv["number"], inv["einvoice"])
            note = call("POST", o(f"/invoices/{inv['id']}/credit-notes"), json={
                "issue_date": _today(), "reason": "Returned goods",
                "lines": [{"invoice_line_id": inv["lines"][0]["id"], "quantity": "1"}]})
            failures += _report(note["number"], note["einvoice"])
        return 1 if failures else 0


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def _report(number: str, doc: dict | None) -> int:
    if doc is None:
        print(f"{number}: no e-invoice")
        return 1
    ok = doc["status"] in ("reported", "cleared") and not doc["errors"]
    print(f"{number}: {doc['kind']} {doc['status']}" + (f" errors={doc['errors']}" if doc["errors"] else "")
          + (f" warnings={doc['warnings']}" if doc["warnings"] else "") + (f" note={doc['note']}" if doc["note"] else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
