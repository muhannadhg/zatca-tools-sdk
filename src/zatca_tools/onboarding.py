"""Onboarding an EGS unit step by step: CSR → compliance CSID → compliance checks → production CSID.

``Zatca.onboard()`` runs all four in one call; this is the same path with every
step in your hands.

1. ``generate_csr``           — local. A new key pair; the private key stays with you.
2. ``request_compliance_csid`` — sends the CSR and a one-time password (OTP) to ZATCA.
   The OTP comes from the Fatoora portal (simulation/production) and only the
   taxpayer can generate it; in the sandbox it is always 123345.
3. ``run_compliance_checks``  — signs sample documents with the compliance CSID and
   sends them to ZATCA's compliance endpoint: one invoice, credit note and debit
   note for each form the unit issues.
4. ``request_production_csid`` — exchanges the compliance CSID for the production CSID
   that signs and authenticates real documents.
"""

from __future__ import annotations

import base64
from datetime import timezone
from typing import TYPE_CHECKING, Iterable, Optional, Union

from cryptography import x509

from .csr import CsrRequest, KeyPair, generate_csr
from .environment import INITIAL_PIH
from .errors import ValidationError
from .models import Credentials, Seller
from .results import ComplianceReport, CsidResult, SubmissionResult


def forms_for(invoice_types: str) -> tuple[str, ...]:
    """The invoice types a unit issues, from the CSR's flags: 1100 both, 1000 standard, 0100 simplified."""
    forms = tuple(form for form, flag in (("standard", invoice_types[:1]), ("simplified", invoice_types[1:2])) if flag == "1")
    return forms or ("standard", "simplified")

if TYPE_CHECKING:
    from .client import Zatca

#: The sample buyer used on standard-form compliance documents (ZATCA's test VAT number).
SAMPLE_BUYER = {
    "name": "Sample Buyer Trading Co.",
    "vat_number": "399999999800003",
    "id": "1010101010",
    "id_scheme": "CRN",
    "address": {"street": "King Fahd Road", "building_number": "8228", "district": "Al Olaya", "city": "Riyadh", "postal_code": "12244", "country": "SA"},
}


class Onboarding:
    def __init__(self, zatca: "Zatca") -> None:
        self._zatca = zatca

    def generate_csr(self, request: Union[CsrRequest, dict], **kwargs: str) -> KeyPair:
        """Generate the key pair and CSR locally. Nothing is sent."""
        if isinstance(request, dict):
            request = CsrRequest(**{**request, **kwargs})
        return generate_csr(request, self._zatca.environment)

    def request_compliance_csid(self, csr: Union[KeyPair, str], otp: str) -> CsidResult:
        pem = csr.csr if isinstance(csr, KeyPair) else csr
        _, body = self._zatca.api.compliance_csid(base64.b64encode(pem.encode()).decode(), str(otp).strip())
        return _csid("compliance", body)

    def run_compliance_checks(
        self,
        credentials: Union[Credentials, dict],
        seller: Optional[Union[Seller, dict]] = None,
        forms: Iterable[str] = ("standard", "simplified"),
    ) -> ComplianceReport:
        """Sign and send the sample documents ZATCA requires before a production CSID.

        ``forms`` follows the unit's invoice types: both for ``1100``, ``("standard",)``
        for ``1000``, ``("simplified",)`` for ``0100``. A request that fails before
        ZATCA judges a sample (network, credentials) raises; a sample ZATCA rejects
        is a failed check in the report.
        """
        compliance = self._zatca._with(credentials=credentials, seller=seller)
        checks: list[SubmissionResult] = []
        pih = INITIAL_PIH
        icv = 0
        for form in forms:
            first_invoice = None
            for kind in ("invoice", "credit", "debit"):
                icv += 1
                number = f"COMP-{form[:3].upper()}-{icv}"
                sample = {
                    "type": form,
                    "kind": kind,
                    "number": number,
                    "icv": icv,
                    "pih": pih,
                    "items": [{"name": "Compliance sample", "quantity": 1, "unit_price": 100}],
                }
                if form == "standard":
                    sample["buyer"] = SAMPLE_BUYER
                if kind != "invoice":
                    sample["original_invoice"] = first_invoice
                    sample["reason"] = "Returned goods" if kind == "credit" else "Price adjustment"
                invoice = compliance.create_invoice(sample)
                checks.append(compliance._send("compliance", invoice))
                pih = invoice.hash
                if kind == "invoice":
                    first_invoice = number
        return ComplianceReport(checks)

    def request_production_csid(self, compliance: Union[CsidResult, Credentials, dict], request_id: Optional[str] = None) -> CsidResult:
        if isinstance(compliance, CsidResult):
            certificate, secret, request_id = compliance.certificate, compliance.secret, request_id or compliance.request_id
        else:
            creds = Credentials.from_dict(compliance)
            certificate, secret = creds.certificate, creds.secret
        if not request_id:
            raise ValidationError("request_production_csid needs the compliance CSID's request_id.", [{"field": "request_id", "message": "Pass the CsidResult from request_compliance_csid(), or its request_id."}])
        _, body = self._zatca.api.production_csid(certificate, secret, str(request_id))
        return _csid("production", body)

    def renew_production_csid(self, credentials: Union[Credentials, dict], csr: Union[KeyPair, str], otp: str) -> CsidResult:
        """Renew a production CSID before it expires, with a new CSR and a fresh OTP."""
        creds = Credentials.from_dict(credentials)
        pem = csr.csr if isinstance(csr, KeyPair) else csr
        _, body = self._zatca.api.renew_production_csid(creds.certificate, creds.secret, base64.b64encode(pem.encode()).decode(), str(otp).strip())
        return _csid("production", body)


def _csid(kind: str, body: dict) -> CsidResult:
    token = str(body.get("binarySecurityToken") or "")
    certificate = base64.b64decode(token).decode() if token else ""
    expires = None
    if certificate:
        try:
            cert = x509.load_der_x509_certificate(base64.b64decode(certificate))
            expires_at = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=timezone.utc)
            expires = expires_at.astimezone(timezone.utc).isoformat()
        except Exception:  # noqa: BLE001 - expiry is informative only
            expires = None
    return CsidResult(kind=kind, certificate=certificate, secret=str(body.get("secret") or ""), request_id=str(body.get("requestID") or ""), expires_at=expires)


__all__ = ["Onboarding", "SAMPLE_BUYER", "forms_for"]
