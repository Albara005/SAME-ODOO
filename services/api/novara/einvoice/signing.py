"""Signing a UBL document the way ZATCA's validator checks it (XAdES enveloped signature, ECDSA on secp256k1).

1. Invoice hash: the document without ext:UBLExtensions, cac:Signature and the QR reference, canonicalized
   (C14N 1.1; identical to C14N 1.0 for these documents), SHA-256, base64.
2. Signature: ECDSA-SHA256 over the hash bytes, with the device's private key.
3. Signed properties: signing time, the certificate's digest (base64 of the hex SHA-256 of its base64 text),
   issuer and serial. Their digest is taken over their exclusive canonical form, hex, then base64.
4. QR code (TLV, base64): seller, VAT number, time, total, VAT, invoice hash, signature, public key and,
   for simplified documents, the certificate's own signature (tags 1-9).

Checked against ZATCA's sandbox (all six document types, reporting and clearance). The sandbox does not
check the ECDSA signature itself, so the tests verify it with the certificate's public key.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from lxml import etree

from novara.einvoice.ubl import CAC, CBC, EXT, Document, build

DS = "http://www.w3.org/2000/09/xmldsig#"
XADES = "http://uri.etsi.org/01903/v1.3.2#"
SIG = "urn:oasis:names:specification:ubl:schema:xsd:CommonSignatureComponents-2"
SAC = "urn:oasis:names:specification:ubl:schema:xsd:SignatureAggregateComponents-2"
SBC = "urn:oasis:names:specification:ubl:schema:xsd:SignatureBasicComponents-2"
_XPATH_NS = {"ext": EXT, "cac": CAC, "cbc": CBC}
_EXCLUDED = ("ext:UBLExtensions", "cac:Signature", "cac:AdditionalDocumentReference[cbc:ID='QR']")


@dataclass(frozen=True)
class Signed:
    xml: bytes
    invoice_hash: str
    signature: str
    qr: str


def invoice_hash(root: etree._Element) -> str:
    copy = etree.fromstring(etree.tostring(root))
    for path in _EXCLUDED:
        for el in copy.xpath(path, namespaces=_XPATH_NS):
            el.getparent().remove(el)
    return base64.b64encode(hashlib.sha256(etree.tostring(copy, method="c14n")).digest()).decode()


def _hex_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).hexdigest().encode()).decode()


def certificate(cert_b64: str) -> x509.Certificate:
    return x509.load_der_x509_certificate(base64.b64decode(cert_b64))


def issuer_name(cert: x509.Certificate) -> str:
    """As ZATCA writes it: most specific first, ", " between (e.g. "CN=PRZEINVOICESCA4-CA, DC=extgazt, ...")."""
    return ", ".join(rdn.rfc4514_string() for rdn in reversed(cert.issuer.rdns))


def tlv(values: list[str | bytes]) -> str:
    out = b""
    for tag, value in enumerate(values, start=1):
        data = value if isinstance(value, bytes) else value.encode()
        if len(data) > 255:
            raise ValueError(f"QR field {tag} is too long")
        out += bytes([tag, len(data)]) + data
    return base64.b64encode(out).decode()


def read_tlv(qr: str) -> dict[int, bytes]:
    raw, fields, i = base64.b64decode(qr), {}, 0
    while i < len(raw):
        tag, length = raw[i], raw[i + 1]
        fields[tag] = raw[i + 2:i + 2 + length]
        i += 2 + length
    return fields


def _signed_properties(cert_b64: str, cert: x509.Certificate, signing_time: datetime) -> str:
    ds = f'xmlns:ds="{DS}"'
    return (
        f'<xades:SignedProperties xmlns:xades="{XADES}" Id="xadesSignedProperties">'
        "<xades:SignedSignatureProperties>"
        f"<xades:SigningTime>{signing_time.strftime('%Y-%m-%dT%H:%M:%S')}</xades:SigningTime>"
        "<xades:SigningCertificate><xades:Cert><xades:CertDigest>"
        f'<ds:DigestMethod {ds} Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"></ds:DigestMethod>'
        f"<ds:DigestValue {ds}>{_hex_b64(cert_b64.encode())}</ds:DigestValue>"
        "</xades:CertDigest><xades:IssuerSerial>"
        f"<ds:X509IssuerName {ds}>{escape(issuer_name(cert))}</ds:X509IssuerName>"
        f"<ds:X509SerialNumber {ds}>{cert.serial_number}</ds:X509SerialNumber>"
        "</xades:IssuerSerial></xades:Cert></xades:SigningCertificate>"
        "</xades:SignedSignatureProperties></xades:SignedProperties>"
    )


def _extensions(digest: str, props_digest: str, signature: str, cert_b64: str, props: str) -> str:
    """The signature block as text. It goes into the document as written: ZATCA reads the signed properties
    with their own namespace declarations, which an XML library would drop as redundant."""
    return (
        "<ext:UBLExtensions><ext:UBLExtension>"
        "<ext:ExtensionURI>urn:oasis:names:specification:ubl:dsig:enveloped:xades</ext:ExtensionURI>"
        f'<ext:ExtensionContent><sig:UBLDocumentSignatures xmlns:sig="{SIG}" xmlns:sac="{SAC}" xmlns:sbc="{SBC}">'
        "<sac:SignatureInformation><cbc:ID>urn:oasis:names:specification:ubl:signature:1</cbc:ID>"
        "<sbc:ReferencedSignatureID>urn:oasis:names:specification:ubl:signature:Invoice</sbc:ReferencedSignatureID>"
        f'<ds:Signature xmlns:ds="{DS}" Id="signature"><ds:SignedInfo>'
        '<ds:CanonicalizationMethod Algorithm="http://www.w3.org/2006/12/xml-c14n11"/>'
        '<ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256"/>'
        '<ds:Reference Id="invoiceSignedData" URI=""><ds:Transforms>'
        '<ds:Transform Algorithm="http://www.w3.org/TR/1999/REC-xpath-19991116">'
        "<ds:XPath>not(//ancestor-or-self::ext:UBLExtensions)</ds:XPath></ds:Transform>"
        '<ds:Transform Algorithm="http://www.w3.org/TR/1999/REC-xpath-19991116">'
        "<ds:XPath>not(//ancestor-or-self::cac:Signature)</ds:XPath></ds:Transform>"
        '<ds:Transform Algorithm="http://www.w3.org/TR/1999/REC-xpath-19991116">'
        "<ds:XPath>not(//ancestor-or-self::cac:AdditionalDocumentReference[cbc:ID='QR'])</ds:XPath></ds:Transform>"
        '<ds:Transform Algorithm="http://www.w3.org/2006/12/xml-c14n11"/></ds:Transforms>'
        '<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>'
        f"<ds:DigestValue>{digest}</ds:DigestValue></ds:Reference>"
        '<ds:Reference Type="http://www.w3.org/2000/09/xmldsig#SignatureProperties" URI="#xadesSignedProperties">'
        '<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>'
        f"<ds:DigestValue>{props_digest}</ds:DigestValue></ds:Reference></ds:SignedInfo>"
        f"<ds:SignatureValue>{signature}</ds:SignatureValue>"
        f"<ds:KeyInfo><ds:X509Data><ds:X509Certificate>{cert_b64}</ds:X509Certificate></ds:X509Data></ds:KeyInfo>"
        f'<ds:Object><xades:QualifyingProperties xmlns:xades="{XADES}" Target="signature">{props}'
        "</xades:QualifyingProperties></ds:Object></ds:Signature>"
        "</sac:SignatureInformation></sig:UBLDocumentSignatures></ext:ExtensionContent>"
        "</ext:UBLExtension></ext:UBLExtensions>"
    )


def sign(doc: Document, private_key: ec.EllipticCurvePrivateKey, cert_b64: str,
         signing_time: datetime | None = None) -> Signed:
    root = build(doc)
    digest = invoice_hash(root)
    signature = base64.b64encode(private_key.sign(base64.b64decode(digest), ec.ECDSA(hashes.SHA256()))).decode()
    cert = certificate(cert_b64)
    props = _signed_properties(cert_b64, cert, signing_time or datetime.now(timezone.utc))
    props_digest = _hex_b64(etree.tostring(etree.fromstring(props), method="c14n", exclusive=True))
    public_key = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    fields: list[str | bytes] = [doc.seller.name, doc.seller.vat_number,
                                 f"{doc.issue_date.isoformat()}T{doc.issue_time.strftime('%H:%M:%S')}",
                                 f"{doc.total:.2f}", f"{doc.tax_total:.2f}", digest, signature, public_key]
    if doc.kind == "simplified":
        fields.append(cert.signature)
    qr = tlv(fields)
    blocks = ("<cac:AdditionalDocumentReference><cbc:ID>QR</cbc:ID><cac:Attachment>"
              f'<cbc:EmbeddedDocumentBinaryObject mimeCode="text/plain">{qr}</cbc:EmbeddedDocumentBinaryObject>'
              "</cac:Attachment></cac:AdditionalDocumentReference>"
              "<cac:Signature><cbc:ID>urn:oasis:names:specification:ubl:signature:Invoice</cbc:ID>"
              "<cbc:SignatureMethod>urn:oasis:names:specification:ubl:dsig:enveloped:xades</cbc:SignatureMethod>"
              "</cac:Signature>")
    text = etree.tostring(root, encoding="unicode")
    head = text.index(">") + 1                      # after the Invoice start tag (it declares ext, cac and cbc)
    supplier = text.index("<cac:AccountingSupplierParty>")
    text = (text[:head] + _extensions(digest, props_digest, signature, cert_b64, props) + text[head:supplier]
            + blocks + text[supplier:])
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n' + text).encode()
    if invoice_hash(etree.fromstring(xml)) != digest:  # what ZATCA recomputes from the final document
        raise RuntimeError("the signed document no longer matches its hash")
    return Signed(xml=xml, invoice_hash=digest, signature=signature, qr=qr)


def verify(signed_xml: bytes) -> bool:
    """The document's hash and ECDSA signature check out against the certificate it carries."""
    root = etree.fromstring(signed_xml)
    digest = root.findtext(f".//{{{DS}}}Reference[@Id='invoiceSignedData']/{{{DS}}}DigestValue")
    if digest != invoice_hash(root):
        return False
    cert = certificate(root.findtext(f".//{{{DS}}}X509Certificate"))
    try:
        cert.public_key().verify(base64.b64decode(root.findtext(f".//{{{DS}}}SignatureValue")),
                                 base64.b64decode(digest), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True
