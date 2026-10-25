"""The device's key and certificate signing request for ZATCA onboarding.

The key is ECDSA on secp256k1. The request names the organization and the device, and carries ZATCA's
fields in a subject alternative name: the solution and device serial (SN), the VAT number (UID), the
document types the device issues (title: "1100" = standard and simplified), its location
(registeredAddress) and the industry (businessCategory). The certificate template extension says which
ZATCA environment it is for.
"""

from __future__ import annotations

import base64

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ObjectIdentifier

TEMPLATES = {"sandbox": "TSTZATCA-Code-Signing", "simulation": "PREZATCA-Code-Signing", "production": "ZATCA-Code-Signing"}
_TEMPLATE_OID = ObjectIdentifier("1.3.6.1.4.1.311.20.2")   # Microsoft certificate template name
_REGISTERED_ADDRESS = ObjectIdentifier("2.5.4.26")


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256K1())


def key_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def load_key(pem: str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem.encode(), None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    return key


def request(key: ec.EllipticCurvePrivateKey, *, environment: str, common_name: str, organization: str, unit: str,
            vat_number: str, serial: str, location: str, industry: str, invoice_types: str = "1100") -> str:
    """The CSR, base64-encoded PEM as the compliance API takes it."""
    template = TEMPLATES[environment].encode()
    der = bytes([0x13, len(template)]) + template          # PrintableString
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "SA"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, unit[:64]),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization[:64]),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name[:64]),
    ])
    details = x509.Name([
        x509.NameAttribute(NameOID.SURNAME, serial),
        x509.NameAttribute(NameOID.USER_ID, vat_number),
        x509.NameAttribute(NameOID.TITLE, invoice_types),
        x509.NameAttribute(_REGISTERED_ADDRESS, location[:64]),
        x509.NameAttribute(NameOID.BUSINESS_CATEGORY, industry[:64]),
    ])
    csr = (x509.CertificateSigningRequestBuilder().subject_name(subject)
           .add_extension(x509.UnrecognizedExtension(_TEMPLATE_OID, der), critical=False)
           .add_extension(x509.SubjectAlternativeName([x509.DirectoryName(details)]), critical=False)
           .sign(key, hashes.SHA256()))
    return base64.b64encode(csr.public_bytes(serialization.Encoding.PEM)).decode()
