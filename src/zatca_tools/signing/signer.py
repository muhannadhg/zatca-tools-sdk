"""Hashing and signing a document the way ZATCA verifies it.

The invoice hash is SHA-256 over the canonical XML (C14N) of the document with
three parts removed: the UBL extensions (where the signature lives), the
``cac:Signature`` element and the QR code reference. Whitespace around the removed
parts stays where it was, exactly as an XPath-filtered canonicalisation leaves it.
The signature is ECDSA-SHA256 over that hash; the XAdES signed properties carry
the signing time and the certificate's digest, issuer and serial.

This layout is the one the ZATCA Tools platform signs with in production.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from lxml import etree

from ..document import ubl
from ..errors import SigningError
from . import qr as qr_code
from .certificate import SigningIdentity

_SIGNED_PROPERTIES = (
    '<xades:SignedProperties xmlns:xades="http://uri.etsi.org/01903/v1.3.2#" Id="xadesSignedProperties">\n'
    "                                <xades:SignedSignatureProperties>\n"
    "                                    <xades:SigningTime>{time}</xades:SigningTime>\n"
    "                                    <xades:SigningCertificate>\n"
    "                                        <xades:Cert>\n"
    "                                            <xades:CertDigest>\n"
    '                                                <ds:DigestMethod xmlns:ds="http://www.w3.org/2000/09/xmldsig#" Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>\n'
    '                                                <ds:DigestValue xmlns:ds="http://www.w3.org/2000/09/xmldsig#">{digest}</ds:DigestValue>\n'
    "                                            </xades:CertDigest>\n"
    "                                            <xades:IssuerSerial>\n"
    '                                                <ds:X509IssuerName xmlns:ds="http://www.w3.org/2000/09/xmldsig#">{issuer}</ds:X509IssuerName>\n'
    '                                                <ds:X509SerialNumber xmlns:ds="http://www.w3.org/2000/09/xmldsig#">{serial}</ds:X509SerialNumber>\n'
    "                                            </xades:IssuerSerial>\n"
    "                                        </xades:Cert>\n"
    "                                    </xades:SigningCertificate>\n"
    "                                </xades:SignedSignatureProperties>\n"
    "                            </xades:SignedProperties>"
)

_EXTENSION = """<ext:UBLExtension>
    <ext:ExtensionURI>urn:oasis:names:specification:ubl:dsig:enveloped:xades</ext:ExtensionURI>
    <ext:ExtensionContent>
        <sig:UBLDocumentSignatures xmlns:sig="urn:oasis:names:specification:ubl:schema:xsd:CommonSignatureComponents-2" xmlns:sac="urn:oasis:names:specification:ubl:schema:xsd:SignatureAggregateComponents-2" xmlns:sbc="urn:oasis:names:specification:ubl:schema:xsd:SignatureBasicComponents-2">
            <sac:SignatureInformation>
                <cbc:ID>urn:oasis:names:specification:ubl:signature:1</cbc:ID>
                <sbc:ReferencedSignatureID>urn:oasis:names:specification:ubl:signature:Invoice</sbc:ReferencedSignatureID>
                <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#" Id="signature">
                    <ds:SignedInfo>
                        <ds:CanonicalizationMethod Algorithm="http://www.w3.org/2006/12/xml-c14n11"/>
                        <ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256"/>
                        <ds:Reference Id="invoiceSignedData" URI="">
                            <ds:Transforms>
                                <ds:Transform Algorithm="http://www.w3.org/TR/1999/REC-xpath-19991116">
                                    <ds:XPath>not(//ancestor-or-self::ext:UBLExtensions)</ds:XPath>
                                </ds:Transform>
                                <ds:Transform Algorithm="http://www.w3.org/TR/1999/REC-xpath-19991116">
                                    <ds:XPath>not(//ancestor-or-self::cac:Signature)</ds:XPath>
                                </ds:Transform>
                                <ds:Transform Algorithm="http://www.w3.org/TR/1999/REC-xpath-19991116">
                                    <ds:XPath>not(//ancestor-or-self::cac:AdditionalDocumentReference[cbc:ID='QR'])</ds:XPath>
                                </ds:Transform>
                                <ds:Transform Algorithm="http://www.w3.org/2006/12/xml-c14n11"/>
                            </ds:Transforms>
                            <ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>
                            <ds:DigestValue>{invoice_hash}</ds:DigestValue>
                        </ds:Reference>
                        <ds:Reference Type="http://www.w3.org/2000/09/xmldsig#SignatureProperties" URI="#xadesSignedProperties">
                            <ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>
                            <ds:DigestValue>{properties_digest}</ds:DigestValue>
                        </ds:Reference>
                    </ds:SignedInfo>
                    <ds:SignatureValue>{signature}</ds:SignatureValue>
                    <ds:KeyInfo>
                        <ds:X509Data>
                            <ds:X509Certificate>{certificate}</ds:X509Certificate>
                        </ds:X509Data>
                    </ds:KeyInfo>
                    <ds:Object>
                        <xades:QualifyingProperties xmlns:xades="http://uri.etsi.org/01903/v1.3.2#" Target="signature">
                            <xades:SignedProperties Id="xadesSignedProperties">
                                <xades:SignedSignatureProperties>
                                    <xades:SigningTime>{time}</xades:SigningTime>
                                    <xades:SigningCertificate>
                                        <xades:Cert>
                                            <xades:CertDigest>
                                                <ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>
                                                <ds:DigestValue>{digest}</ds:DigestValue>
                                            </xades:CertDigest>
                                            <xades:IssuerSerial>
                                                <ds:X509IssuerName>{issuer}</ds:X509IssuerName>
                                                <ds:X509SerialNumber>{serial}</ds:X509SerialNumber>
                                            </xades:IssuerSerial>
                                        </xades:Cert>
                                    </xades:SigningCertificate>
                                </xades:SignedSignatureProperties>
                            </xades:SignedProperties>
                        </xades:QualifyingProperties>
                    </ds:Object>
                </ds:Signature>
            </sac:SignatureInformation>
        </sig:UBLDocumentSignatures>
    </ext:ExtensionContent>
</ext:UBLExtension>
"""

_QR_REFERENCE = """    <cac:AdditionalDocumentReference>
        <cbc:ID>QR</cbc:ID>
        <cac:Attachment>
            <cbc:EmbeddedDocumentBinaryObject mimeCode="text/plain">{qr}</cbc:EmbeddedDocumentBinaryObject>
        </cac:Attachment>
    </cac:AdditionalDocumentReference>"""

_SIGNATURE_ELEMENT = """    <cac:Signature>
        <cbc:ID>urn:oasis:names:specification:ubl:signature:Invoice</cbc:ID>
        <cbc:SignatureMethod>urn:oasis:names:specification:ubl:dsig:enveloped:xades</cbc:SignatureMethod>
    </cac:Signature>"""


@dataclass(frozen=True)
class Signed:
    xml: str
    hash: str
    qr: str
    signature: str
    signing_time: str


def canonical_for_hash(rendered: str) -> bytes:
    """The bytes the invoice hash covers: the document minus extensions, QR and signature, canonicalised."""
    text = rendered.replace(ubl.UBL_EXTENSIONS, "    ").replace(ubl.QR, "    ").replace(ubl.SIGNATURE, "    ")
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, no_network=True)
    root = etree.fromstring(text.encode("utf-8"), parser)
    return etree.tostring(root, method="c14n", exclusive=False, with_comments=False)


def invoice_hash(rendered: str) -> str:
    return base64.b64encode(hashlib.sha256(canonical_for_hash(rendered)).digest()).decode()


def sign(rendered: str, payload: Mapping[str, Any], identity: SigningIdentity, signing_time: Optional[datetime] = None) -> Signed:
    """Sign a rendered document (``ubl.render``) and assemble the signed XML."""
    canonical = canonical_for_hash(rendered)
    digest = hashlib.sha256(canonical).digest()
    document_hash = base64.b64encode(digest).decode()
    try:
        signature = identity.sign(digest)
    except Exception as exc:  # noqa: BLE001
        raise SigningError("Signing failed with the given private key.") from exc

    moment = (signing_time or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    certificate_digest = identity.certificate_hash()
    issuer = identity.issuer_name()
    serial = identity.serial_number()

    properties = _SIGNED_PROPERTIES.format(time=moment, digest=certificate_digest, issuer=issuer, serial=serial)
    properties_digest = base64.b64encode(hashlib.sha256(properties.encode("utf-8")).hexdigest().encode()).decode()

    totals = payload["legalMonetaryTotal"]
    fields: list[tuple[int, Any]] = [
        (1, payload["supplier"]["registrationName"]),
        (2, payload["supplier"]["taxId"]),
        (3, f"{payload['issueDate']}T{payload['issueTime']}"),
        (4, _xml_amount(totals["taxInclusiveAmount"])),
        (5, _xml_amount(payload["taxTotal"]["taxAmount"])),
        (6, document_hash),
        (7, signature),
        (8, identity.public_key_der()),
    ]
    if payload["invoiceType"]["invoice"] == "simplified":
        fields.append((9, identity.certificate_signature()))
    qr = qr_code.encode(fields)

    extension = _EXTENSION.format(
        invoice_hash=document_hash,
        properties_digest=properties_digest,
        signature=signature,
        certificate=identity.raw_certificate,
        time=moment,
        digest=certificate_digest,
        issuer=_escape(issuer),
        serial=serial,
    )
    signed = (
        rendered.replace(ubl.UBL_EXTENSIONS, "    <ext:UBLExtensions>" + extension + "</ext:UBLExtensions>")
        .replace(ubl.QR, _QR_REFERENCE.format(qr=qr))
        .replace(ubl.SIGNATURE, _SIGNATURE_ELEMENT)
    )
    return Signed(xml=signed, hash=document_hash, qr=qr, signature=signature, signing_time=moment)


def _xml_amount(value: float) -> str:
    from .._rounding import fmt

    return fmt(value, 2)


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
