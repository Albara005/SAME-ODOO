"""A stand-in for ZATCA's Fatoora API (developer sandbox paths), for the tests and the browser tests.

It issues real certificates from a test CA for the key in the device's CSR, and checks each document's
hash and ECDSA signature the way the validator should, so a broken signature is refused.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from lxml import etree

from novara.einvoice import signing


class FakeZatca:
    """Issues certificates from a test CA for the CSR's key; checks documents like the validator does."""

    def __init__(self):
        self.ca_key = ec.generate_private_key(ec.SECP256K1())
        self.calls: list[str] = []
        self.down = False
        self.refuse_with: list[str] = []

    def _cert(self, csr_b64: str | None, public_key=None) -> str:
        if csr_b64:
            public_key = x509.load_pem_x509_csr(base64.b64decode(csr_b64)).public_key()
        now = datetime.now(timezone.utc)
        issuer = x509.Name([x509.NameAttribute(NameOID.DOMAIN_COMPONENT, "local"),
                            x509.NameAttribute(NameOID.COMMON_NAME, "TEST-ZATCA-CA")])
        cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "device")]))
                .issuer_name(issuer).public_key(public_key).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=365))
                .sign(self.ca_key, hashes.SHA256()))
        return base64.b64encode(base64.b64encode(cert.public_bytes(Encoding.DER))).decode()

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/developer-portal", 1)[1]
        self.calls.append(path)
        if self.down:
            return httpx.Response(503, text="Service Unavailable")
        body = json.loads(request.content or b"{}")
        if path == "/compliance":
            if request.headers.get("OTP") != "123345":
                return httpx.Response(400, json={"errors": [{"message": "Invalid OTP"}]})
            self.public_key = x509.load_pem_x509_csr(base64.b64decode(body["csr"])).public_key()
            return httpx.Response(200, json={"requestID": 1234, "binarySecurityToken": self._cert(body["csr"]), "secret": "s1"})
        if path == "/production/csids":
            return httpx.Response(200, json={"requestID": 5678, "binarySecurityToken": self._cert(None, self.public_key),
                                             "secret": "s2"})
        xml = base64.b64decode(body["invoice"])
        root = etree.fromstring(xml)
        ok = signing.verify(xml) and body["invoiceHash"] == signing.invoice_hash(root)
        errors = [] if ok else [{"code": "invalid-signature", "message": "Invalid signature"}]
        errors += [{"code": "BR-KSA-XX", "message": m} for m in self.refuse_with]
        status = "PASS" if not errors else "ERROR"
        result = {"validationResults": {"status": status, "errorMessages": errors, "warningMessages": []}}
        if path == "/invoices/clearance/single":
            result |= {"clearanceStatus": "CLEARED" if not errors else "NOT_CLEARED",
                       "clearedInvoice": base64.b64encode(xml).decode() if not errors else None}
        elif path == "/invoices/reporting/single":
            result |= {"reportingStatus": "REPORTED" if not errors else "NOT_REPORTED"}
        return httpx.Response(200 if not errors else 400, json=result)
