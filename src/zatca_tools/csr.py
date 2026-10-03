"""Generating the key pair and Certificate Signing Request for one EGS unit — locally.

The private key is created on your machine and never leaves it; only the CSR
(which contains the public key) is sent to ZATCA to obtain a CSID.

ZATCA's CSR layout: subject ``C, OU, O, CN``; a certificate-template extension
(OID 1.3.6.1.4.1.311.20.2) naming the environment; and a subjectAltName
directory name carrying the EGS serial number (SN), the VAT number (UID), the
invoice types the unit issues (title), its location (registeredAddress) and the
business category.
"""

from __future__ import annotations

import base64
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ObjectIdentifier

from .environment import Environment
from .errors import ValidationError
from .models import is_valid_vat, is_vat_group

TEMPLATE_OID = ObjectIdentifier("1.3.6.1.4.1.311.20.2")
_REGISTERED_ADDRESS = ObjectIdentifier("2.5.4.26")
_FORBIDDEN = re.compile(r"[!@#$%&*_<=]")


@dataclass(frozen=True)
class CsrRequest:
    """What ZATCA needs to know about the EGS unit (the device or solution that issues documents).

    ``invoice_types`` is the four-character flag ZATCA uses: ``1100`` for a unit
    that issues both standard (B2B) and simplified (B2C) documents, ``1000`` for
    standard only, ``0100`` for simplified only.
    """

    vat_number: str
    organization_name: str
    organization_unit: str
    location: str
    industry: str
    invoice_types: str = "1100"
    solution_name: str = "ZatcaToolsSDK"
    model: str = "Python"
    serial: str = field(default_factory=lambda: str(uuid.uuid4()))
    common_name: Optional[str] = None

    @property
    def egs_serial_number(self) -> str:
        return f"1-{self.solution_name}|2-{self.model}|3-{self.serial}"

    def validate(self) -> None:
        problems = []
        if not is_valid_vat(self.vat_number):
            problems.append({"field": "vat_number", "message": "vat_number must be 15 digits, starting and ending with 3."})
        for name in ("organization_name", "organization_unit", "location", "industry", "solution_name", "model", "serial"):
            value = getattr(self, name)
            if not str(value or "").strip():
                problems.append({"field": name, "message": f"{name} is required."})
            elif _FORBIDDEN.search(str(value)):
                problems.append({"field": name, "message": f"{name} may not contain any of ! @ # $ % & * _ < ="})
        if not re.fullmatch(r"[01]{4}", self.invoice_types or "") or self.invoice_types[:2] == "00":
            problems.append({"field": "invoice_types", "message": "invoice_types is four 0/1 flags, e.g. 1100 (standard and simplified)."})
        if is_valid_vat(self.vat_number) and is_vat_group(self.vat_number) and not re.fullmatch(r"\d{10}", self.organization_unit or ""):
            problems.append({"field": "organization_unit", "message": "For a VAT group, organization_unit is the 10-digit TIN of the member whose unit is onboarded."})
        if problems:
            raise ValidationError("The CSR details are incomplete.", problems)


@dataclass(frozen=True)
class KeyPair:
    """The CSR to send to ZATCA, and the private key to keep. Store the key as a secret."""

    csr: str = field(repr=True)
    private_key: str = field(repr=False)
    egs_serial_number: str = ""

    def csr_base64(self) -> str:
        """The CSR as ZATCA's compliance endpoint expects it: the PEM, base64-encoded once more."""
        return base64.b64encode(self.csr.encode()).decode()

    def to_dict(self, include_private_key: bool = False) -> dict[str, str]:
        out = {"csr": self.csr, "egs_serial_number": self.egs_serial_number}
        if include_private_key:
            out["private_key"] = self.private_key
        return out


def generate_csr(request: CsrRequest, environment: Environment) -> KeyPair:
    request.validate()
    key = ec.generate_private_key(ec.SECP256K1())

    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "SA"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, request.organization_unit),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, request.organization_name),
        x509.NameAttribute(NameOID.COMMON_NAME, request.common_name or f"{request.solution_name}-{request.vat_number}"),
    ])
    directory = x509.Name([
        x509.NameAttribute(NameOID.SURNAME, request.egs_serial_number),
        x509.NameAttribute(NameOID.USER_ID, request.vat_number),
        x509.NameAttribute(NameOID.TITLE, request.invoice_types),
        x509.NameAttribute(_REGISTERED_ADDRESS, request.location),
        x509.NameAttribute(NameOID.BUSINESS_CATEGORY, request.industry),
    ])

    template = environment.csr_template.encode("utf-8")
    template_der = bytes([0x0C, len(template)]) + template  # ASN.1 UTF8String

    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .add_extension(x509.UnrecognizedExtension(TEMPLATE_OID, template_der), critical=False)
        .add_extension(x509.SubjectAlternativeName([x509.DirectoryName(directory)]), critical=False)
        .sign(key, hashes.SHA256())
    )

    return KeyPair(
        csr=csr.public_bytes(serialization.Encoding.PEM).decode(),
        private_key=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
        egs_serial_number=request.egs_serial_number,
    )
