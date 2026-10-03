"""The seller, its address, and the credentials that sign and authenticate its documents."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from datetime import timezone
from typing import Any, Mapping, Optional, Union

from .errors import ValidationError
from .tax import id_schemes

_VAT = re.compile(r"^3\d{13}3$")


def is_valid_vat(vat: Optional[str]) -> bool:
    """A Saudi VAT registration number: 15 digits, first and last digit 3."""
    return vat is not None and bool(_VAT.match(str(vat)))


def is_vat_group(vat: str) -> bool:
    """The 11th digit is 1 for a VAT group (the CSR's organisation unit is then the member's TIN)."""
    return len(vat) == 15 and vat[10] == "1"


@dataclass(frozen=True)
class Address:
    """A Saudi national address, as ZATCA reads it (BR-KSA-09, BR-KSA-63)."""

    street: str
    city: str
    building_number: Optional[str] = None
    district: Optional[str] = None
    postal_code: Optional[str] = None
    country: str = "SA"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Address":
        def text(key: str) -> Optional[str]:
            value = data.get(key)
            return None if value is None or str(value).strip() == "" else str(value).strip()

        return cls(
            street=text("street") or "",
            city=text("city") or "",
            building_number=text("building_number"),
            district=text("district"),
            postal_code=text("postal_code"),
            country=(text("country") or "SA").upper(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "street": self.street,
            "building_number": self.building_number,
            "district": self.district,
            "city": self.city,
            "postal_code": self.postal_code,
            "country": self.country,
        }


@dataclass(frozen=True)
class Seller:
    """The taxpayer issuing the documents: the identity printed on, and signed into, every invoice."""

    vat_number: str
    name: str
    cr_number: str
    address: Address
    cr_scheme: str = "CRN"

    @classmethod
    def from_dict(cls, data: Union["Seller", Mapping[str, Any]]) -> "Seller":
        if isinstance(data, Seller):
            return data
        address = data.get("address")
        return cls(
            vat_number=str(data.get("vat_number") or "").strip(),
            name=str(data.get("name") or "").strip(),
            cr_number=id_schemes.clean(data.get("cr_number")),
            cr_scheme=str(data.get("cr_scheme") or "CRN").strip().upper(),
            address=address if isinstance(address, Address) else Address.from_dict(address or {}),
        )

    def validate(self) -> None:
        """Raise ``ValidationError`` listing every problem with the seller's details."""
        problems: list[dict[str, str]] = []
        if not is_valid_vat(self.vat_number):
            problems.append({"field": "seller.vat_number", "message": "seller.vat_number must be 15 digits, starting and ending with 3."})
        if len(self.name) < 2:
            problems.append({"field": "seller.name", "message": "seller.name is the legal name registered with ZATCA."})
        if self.cr_scheme not in id_schemes.SELLER:
            problems.append({"field": "seller.cr_scheme", "message": f"seller.cr_scheme must be one of {', '.join(id_schemes.SELLER)}."})
        elif not id_schemes.looks_valid(self.cr_number, self.cr_scheme):
            problems.append({"field": "seller.cr_number", "message": f"seller.cr_number: {id_schemes.shape_hint(self.cr_scheme)}."})
        for key in ("street", "city", "building_number", "postal_code"):
            if not getattr(self.address, key):
                problems.append({"field": f"seller.address.{key}", "message": f"seller.address.{key} is required (BR-KSA-09).", "rule": "BR-KSA-09"})
        if self.address.country != "SA":
            problems.append({"field": "seller.address.country", "message": "seller.address.country must be SA."})
        if problems:
            raise ValidationError("The seller's details are incomplete.", problems)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vat_number": self.vat_number,
            "name": self.name,
            "cr_number": self.cr_number,
            "cr_scheme": self.cr_scheme,
            "address": self.address.to_dict(),
        }


@dataclass(frozen=True)
class Credentials:
    """What signs and authenticates documents for one EGS unit.

    ``certificate`` and ``secret`` are exactly what ZATCA returned when the CSID
    was issued (``binarySecurityToken`` decoded once, i.e. the base64 body of the
    certificate), and ``private_key`` is the PEM generated with the CSR. They never
    leave your infrastructure except as the HTTP Basic credentials ZATCA requires.
    """

    certificate: str
    secret: str
    private_key: str = field(repr=False)

    def __repr__(self) -> str:  # never print secrets, even by accident
        return f"Credentials(certificate='{self.certificate[:12]}…', secret='***', private_key='***')"

    @classmethod
    def from_dict(cls, data: Union["Credentials", Mapping[str, Any]]) -> "Credentials":
        if isinstance(data, Credentials):
            return data
        try:
            return cls(certificate=str(data["certificate"]).strip(), secret=str(data["secret"]).strip(), private_key=str(data["private_key"]))
        except KeyError as missing:
            raise ValidationError(
                "Credentials need certificate, secret and private_key.",
                [{"field": f"credentials.{missing.args[0]}", "message": f"credentials.{missing.args[0]} is required."}],
            ) from None

    def export(self) -> dict[str, str]:
        """Everything needed to restore these credentials — store it as a secret, never in logs."""
        return {"certificate": self.certificate, "secret": self.secret, "private_key": self.private_key}

    @property
    def expires_at(self) -> Optional[str]:
        """When the certificate expires (ISO 8601, UTC) — renew before then. None if it cannot be read."""
        try:
            from cryptography import x509

            cert = x509.load_der_x509_certificate(base64.b64decode(self.certificate))
        except Exception:  # noqa: BLE001 - informative only
            return None
        moment = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()


#: ZATCA's public sandbox accepts invoices for this test VAT number only.
SANDBOX_VAT = "399999999900003"

#: The seller used in the sandbox when none is given: ZATCA's test VAT number and a sample address.
SANDBOX_SELLER = Seller(
    vat_number=SANDBOX_VAT,
    name="Sandbox Test Company",
    cr_number="1010010000",
    address=Address(street="King Fahd Road", city="Riyadh", building_number="1234", district="Al Olaya", postal_code="12345"),
)
