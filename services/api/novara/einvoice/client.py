"""ZATCA's Fatoora API (https://sandbox.zatca.gov.sa/IntegrationSandbox and the developer portal docs).

    POST /compliance                   OTP from the Fatoora portal + CSR -> compliance certificate (CSID)
    POST /compliance/invoices          sample documents, checked before a production certificate is issued
    POST /production/csids             compliance request id -> production certificate
    POST /invoices/reporting/single    simplified documents, within 24 hours
    POST /invoices/clearance/single    standard documents, before they're given to the buyer (ZATCA stamps them)

Certificates and secrets come back as "binarySecurityToken" (base64 of the certificate's base64) and
"secret"; calls after onboarding authenticate with HTTP Basic token:secret.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

import httpx

BASES = {
    "sandbox": "https://gw-fatoora.zatca.gov.sa/e-invoicing/developer-portal",
    "simulation": "https://gw-fatoora.zatca.gov.sa/e-invoicing/simulation",
    "production": "https://gw-fatoora.zatca.gov.sa/e-invoicing/core",
}
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class FatooraUnavailable(RuntimeError):
    """ZATCA didn't answer or answered with a server error: try again later."""


class FatooraRefused(RuntimeError):
    """ZATCA refused the request itself (a wrong OTP, bad credentials...)."""


@dataclass(frozen=True)
class Credentials:
    request_id: str
    token: str          # binarySecurityToken
    secret: str

    @property
    def certificate(self) -> str:
        """The certificate's base64 (DER) text."""
        return base64.b64decode(self.token).decode()


@dataclass
class Result:
    """What ZATCA said about a document: accepted (possibly with warnings) or not, and why."""
    accepted: bool
    status: str                          # REPORTED / CLEARED / NOT_REPORTED / NOT_CLEARED / PASS / ERROR
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cleared_xml: bytes | None = None


class Fatoora:
    def __init__(self, environment: str, http: httpx.Client | None = None):
        self.base = BASES[environment]
        self._http = http

    def _post(self, path: str, body: dict, headers: dict) -> httpx.Response:
        client = self._http or httpx.Client(timeout=TIMEOUT)
        try:
            return client.post(self.base + path, json=body, headers={
                "Accept-Version": "V2", "Accept-Language": "en", "Accept": "application/json", **headers})
        except httpx.HTTPError as e:
            raise FatooraUnavailable(f"ZATCA didn't answer ({type(e).__name__})") from None
        finally:
            if self._http is None:
                client.close()

    @staticmethod
    def _basic(c: Credentials) -> dict:
        return {"Authorization": "Basic " + base64.b64encode(f"{c.token}:{c.secret}".encode()).decode()}

    def _credentials(self, r: httpx.Response) -> Credentials:
        if r.status_code >= 500:
            raise FatooraUnavailable(f"ZATCA answered HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200 or not body.get("binarySecurityToken"):
            errors = body.get("errors") or [body.get("message") or body.get("dispositionMessage") or f"HTTP {r.status_code}"]
            raise FatooraRefused("; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors)[:500])
        return Credentials(request_id=str(body.get("requestID", "")), token=body["binarySecurityToken"], secret=body["secret"])

    def compliance_csid(self, csr: str, otp: str) -> Credentials:
        return self._credentials(self._post("/compliance", {"csr": csr}, {"OTP": otp}))

    def production_csid(self, compliance: Credentials) -> Credentials:
        return self._credentials(self._post("/production/csids", {"compliance_request_id": compliance.request_id},
                                            self._basic(compliance)))

    def _result(self, r: httpx.Response, *, cleared: bool = False) -> Result:
        if r.status_code >= 500 or r.status_code == 429:
            raise FatooraUnavailable(f"ZATCA answered HTTP {r.status_code}")
        if r.status_code == 401:
            raise FatooraRefused("ZATCA didn't accept the device's credentials")
        try:
            body = r.json()
        except ValueError:
            raise FatooraUnavailable(f"ZATCA's answer wasn't JSON (HTTP {r.status_code})") from None
        vr = body.get("validationResults") or {}
        status = body.get("clearanceStatus") or body.get("reportingStatus") or vr.get("status") or "ERROR"
        messages = lambda key: [f"{m.get('code', '')}: {m.get('message', '')}".strip(": ") for m in vr.get(key) or []]  # noqa: E731
        xml = base64.b64decode(body["clearedInvoice"]) if cleared and body.get("clearedInvoice") else None
        accepted = r.status_code in (200, 202) and status in ("REPORTED", "CLEARED", "PASS")
        return Result(accepted=accepted, status=status, warnings=messages("warningMessages"),
                      errors=messages("errorMessages"), cleared_xml=xml)

    @staticmethod
    def _document(invoice_hash: str, uuid: str, xml: bytes) -> dict:
        return {"invoiceHash": invoice_hash, "uuid": uuid, "invoice": base64.b64encode(xml).decode()}

    def check(self, compliance: Credentials, invoice_hash: str, uuid: str, xml: bytes) -> Result:
        return self._result(self._post("/compliance/invoices", self._document(invoice_hash, uuid, xml), self._basic(compliance)))

    def report(self, production: Credentials, invoice_hash: str, uuid: str, xml: bytes) -> Result:
        return self._result(self._post("/invoices/reporting/single", self._document(invoice_hash, uuid, xml),
                                       self._basic(production) | {"Clearance-Status": "0"}))

    def clear(self, production: Credentials, invoice_hash: str, uuid: str, xml: bytes) -> Result:
        return self._result(self._post("/invoices/clearance/single", self._document(invoice_hash, uuid, xml),
                                       self._basic(production) | {"Clearance-Status": "1"}), cleared=True)
