"""The entry point: ``Zatca``."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Mapping, Optional, Union

import httpx

from .api import ZatcaApi
from .csr import CsrRequest
from .design import InvoiceDesign
from .document.prepare import create as create_document
from .environment import SANDBOX_OTP, Environment
from .errors import (
    AuthenticationError,
    ComplianceCheckError,
    NetworkError,
    ValidationError,
    ZatcaRequestError,
    ZatcaServiceError,
)
from .models import SANDBOX_SELLER, SANDBOX_VAT, Credentials, Seller
from .onboarding import Onboarding, forms_for
from .results import Chain, Invoice, SubmissionResult
from .signing.certificate import SigningIdentity

#: What a submission can fail with before ZATCA judges the invoice. ``submit()`` returns these as results.
_NO_VERDICT = (NetworkError, AuthenticationError, ZatcaRequestError, ZatcaServiceError)


class Zatca:
    """ZATCA e-invoicing from your own application.

    >>> zatca = Zatca("sandbox")
    >>> zatca.onboard()                              # once per unit; automatic in the sandbox
    >>> invoice = zatca.create_invoice({...})        # local: validate, compute, sign, hash, QR
    >>> result = zatca.submit(invoice)               # ZATCA: reporting (B2C) or clearance (B2B)
    >>> result.success, result.status, result.error

    Only ``onboard``, ``renew``, ``submit`` (and the advanced ``report``,
    ``clear``, ``check_compliance`` and ``onboarding()``) talk to the network,
    and only to ZATCA. ``create_invoice`` never does.
    """

    def __init__(
        self,
        environment: Union[Environment, str] = Environment.SANDBOX,
        *,
        seller: Optional[Union[Seller, Mapping[str, Any]]] = None,
        credentials: Optional[Union[Credentials, Mapping[str, Any]]] = None,
        chain: Optional[Union[Chain, Mapping[str, Any], str]] = None,
        design: Optional[Union[InvoiceDesign, Mapping[str, Any]]] = None,
        http_client: Optional[httpx.Client] = None,
        timeout: float = 30.0,
        allowance_reason: str = "Discount",
    ) -> None:
        self.environment = Environment.parse(environment)
        self.design = InvoiceDesign.parse(design)
        sandbox = self.environment is Environment.SANDBOX
        self.seller = Seller.from_dict(seller) if seller is not None else (SANDBOX_SELLER if sandbox else None)
        if sandbox and self.seller.vat_number != SANDBOX_VAT:
            raise ValidationError(
                "ZATCA's sandbox accepts invoices for its test VAT number only.",
                [{"field": "seller.vat_number", "message": f"In the sandbox the seller's VAT number must be {SANDBOX_VAT} (ZATCA answers certificate-permissions otherwise). Leave seller out in the sandbox, or use 'simulation' to test with your own VAT number."}],
            )
        self.credentials = Credentials.from_dict(credentials) if credentials is not None else None
        self._chain = Chain.parse(chain) if chain is not None else (Chain.new() if sandbox else None)
        self.allowance_reason = allowance_reason
        self._http_client = http_client
        self._timeout = timeout
        self._api: Optional[ZatcaApi] = None
        self._identity: Optional[SigningIdentity] = None
        self._lock = threading.Lock()

    # -- setup -------------------------------------------------------------------------

    def onboard(
        self,
        otp: Optional[str] = None,
        *,
        branch: Optional[str] = None,
        location: Optional[str] = None,
        industry: Optional[str] = None,
        invoice_types: str = "1100",
    ) -> Credentials:
        """Get this unit's certificate from ZATCA — once per unit, before its first invoice.

        Generates the private key here (it never leaves), sends ZATCA a CSR with
        the ``otp``, passes ZATCA's compliance checks and obtains the production
        certificate. In the sandbox the OTP is ZATCA's public test value and can
        be left out; everywhere else only the taxpayer can generate it, on the
        Fatoora portal.

        Returns the credentials and keeps them on this client. Store
        ``credentials.export()`` as a secret: the next start passes it back as
        ``Zatca(credentials=...)``.
        """
        seller = self._require_seller()
        keys = self.onboarding().generate_csr(self._csr_request(seller, branch, location, industry, invoice_types))
        compliance = self.onboarding().request_compliance_csid(keys, self._otp(otp))
        report = self.onboarding().run_compliance_checks(compliance.credentials(keys.private_key), forms=forms_for(invoice_types))
        if not report.passed:
            failed = next(check for check in report.checks if not check.success)
            reason = failed.error.message if failed.error else failed.status
            raise ComplianceCheckError(f"ZATCA did not pass the {failed.invoice.type} {failed.invoice.kind} compliance check: {reason}", report)
        production = self.onboarding().request_production_csid(compliance)
        self.credentials = production.credentials(keys.private_key)
        self._identity = None
        if self._chain is None:
            self._chain = Chain.new()
        return self.credentials

    def renew(
        self,
        otp: Optional[str] = None,
        *,
        branch: Optional[str] = None,
        location: Optional[str] = None,
        industry: Optional[str] = None,
        invoice_types: str = "1100",
    ) -> Credentials:
        """Replace this unit's certificate before it expires (``credentials.expires_at``). Needs a fresh OTP.

        The chain carries on: the next invoice follows the last one signed with the old certificate.
        """
        current = self._require_credentials()
        keys = self.onboarding().generate_csr(self._csr_request(self._require_seller(), branch, location, industry, invoice_types))
        renewed = self.onboarding().renew_production_csid(current, keys, self._otp(otp))
        self.credentials = renewed.credentials(keys.private_key)
        self._identity = None
        return self.credentials

    @property
    def chain(self) -> Optional[Chain]:
        """The counter and hash of the last invoice this unit signed. Save ``chain.to_dict()`` after each invoice."""
        return self._chain

    # -- invoices ----------------------------------------------------------------------

    def create_invoice(self, invoice: Mapping[str, Any], *, signing_time: Optional[datetime] = None) -> Invoice:
        """Validate the data, compute VAT and totals, build the XML, sign it, hash it and make the QR code.

        Entirely local: nothing is sent. Raises ``ValidationError`` listing every
        problem at once when the data cannot make a valid invoice.
        """
        seller = self._require_seller()
        identity = self._signing_identity()
        with self._lock:
            explicit = isinstance(invoice, Mapping) and "icv" in invoice
            if self._chain is None and not explicit:
                raise ValidationError(
                    "This client does not know where the unit's chain stands.",
                    [{"field": "chain", "message": "Pass chain= to Zatca(...): the dict saved from zatca.chain.to_dict() after the unit's last invoice, or 'new' for a unit that has never issued one."}],
                )
            created = create_document(
                invoice,
                seller,
                identity,
                chain=self._chain or Chain.new(),
                allowance_reason=self.allowance_reason,
                signing_time=signing_time,
            )
            self._chain = Chain(created.icv, created.hash)
        created.design = self.design
        return created

    def submit(self, invoice: Invoice) -> SubmissionResult:
        """Send an invoice the way ZATCA requires — clearance for standard, reporting for simplified — and return the outcome.

        Never raises for the outcome: a rejection, a refused request and a lost
        connection all come back as a result with ``success`` False, a
        ``status`` saying which, and an ``error`` saying why.
        """
        invoice = self._require_invoice(invoice)
        return self.clear(invoice) if invoice.type == "standard" else self.report(invoice)

    def report(self, invoice: Invoice) -> SubmissionResult:
        """Report a simplified (B2C) invoice. ZATCA expects it within 24 hours of issue."""
        if self._require_invoice(invoice).type != "simplified":
            raise ValidationError("Only simplified invoices are reported.", [{"field": "type", "message": "A standard (B2B) invoice is cleared: use submit() or clear()."}])
        return self._outcome("reporting", invoice)

    def clear(self, invoice: Invoice) -> SubmissionResult:
        """Clear a standard (B2B) invoice. Share it with the buyer only once cleared — as ``result.xml``."""
        if self._require_invoice(invoice).type != "standard":
            raise ValidationError("Only standard invoices are cleared.", [{"field": "type", "message": "A simplified (B2C) invoice is reported: use submit() or report()."}])
        return self._outcome("clearance", invoice)

    def check_compliance(self, invoice: Invoice) -> SubmissionResult:
        """Send an invoice to ZATCA's compliance endpoint, as onboarding does. Nothing is reported or cleared."""
        return self._outcome("compliance", self._require_invoice(invoice))

    # -- advanced ----------------------------------------------------------------------

    def onboarding(self) -> Onboarding:
        """The onboarding steps one by one — key pair and CSR, compliance CSID, checks, production CSID."""
        return Onboarding(self)

    @property
    def api(self) -> ZatcaApi:
        if self._api is None:
            self._api = ZatcaApi(self.environment, self._http_client, self._timeout)
        return self._api

    def close(self) -> None:
        if self._api is not None:
            self._api.close()

    def __enter__(self) -> "Zatca":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- internals ---------------------------------------------------------------------

    def _outcome(self, operation: str, invoice: Invoice) -> SubmissionResult:
        try:
            return self._send(operation, invoice)
        except _NO_VERDICT as error:
            return SubmissionResult.from_failure(operation, invoice, error)

    def _send(self, operation: str, invoice: Invoice) -> SubmissionResult:
        """The request itself; failures before a verdict raise."""
        creds = self._require_credentials()
        call = {"reporting": self.api.report, "clearance": self.api.clear, "compliance": self.api.compliance_check}[operation]
        status, body = call(creds.certificate, creds.secret, invoice.xml, invoice.hash, invoice.uuid)
        return SubmissionResult.from_response(operation, status, body, invoice)

    def _otp(self, otp: Optional[str]) -> str:
        if otp is not None and str(otp).strip():
            return str(otp).strip()
        if self.environment is Environment.SANDBOX:
            return SANDBOX_OTP
        raise ValidationError(
            "Onboarding needs an OTP from the Fatoora portal.",
            [{"field": "otp", "message": "Generate it on the Fatoora portal (Onboard new solution unit/device); it is valid for one hour. Only the taxpayer can create it."}],
        )

    def _csr_request(self, seller: Seller, branch: Optional[str], location: Optional[str], industry: Optional[str], invoice_types: str) -> CsrRequest:
        return CsrRequest(
            vat_number=seller.vat_number,
            organization_name=seller.name,
            organization_unit=branch or "Main branch",
            location=location or seller.address.city or "Riyadh",
            industry=industry or "General",
            invoice_types=invoice_types,
        )

    def _require_seller(self) -> Seller:
        if self.seller is None:
            raise ValidationError("Set the seller first.", [{"field": "seller", "message": "Pass seller= to Zatca(...): your VAT number, legal name, CR number and address."}])
        return self.seller

    def _require_credentials(self) -> Credentials:
        if self.credentials is None:
            raise ValidationError(
                "This client has no credentials yet.",
                [{"field": "credentials", "message": "Call zatca.onboard() once (in the sandbox it needs nothing else), or pass the saved credentials as Zatca(credentials=...)."}],
            )
        return self.credentials

    @staticmethod
    def _require_invoice(invoice: Any) -> Invoice:
        if not isinstance(invoice, Invoice):
            raise ValidationError("submit() takes an invoice from create_invoice().", [{"field": "invoice", "message": "Create it first: invoice = zatca.create_invoice({...})."}])
        return invoice

    def _signing_identity(self) -> SigningIdentity:
        if self._identity is None:
            creds = self._require_credentials()
            self._identity = SigningIdentity.load(creds.certificate, creds.private_key, strict=self.environment is not Environment.SANDBOX)
        return self._identity

    def _with(self, *, credentials: Optional[Union[Credentials, Mapping[str, Any]]] = None, seller: Optional[Union[Seller, Mapping[str, Any]]] = None) -> "Zatca":
        """A copy with other credentials or another seller — its own chain, the same connection."""
        clone = Zatca.__new__(Zatca)
        clone.__dict__.update(self.__dict__)
        clone._lock = threading.Lock()
        if credentials is not None:
            clone.credentials = Credentials.from_dict(credentials)
            clone._identity = None
        if seller is not None:
            clone.seller = Seller.from_dict(seller)
        return clone


__all__ = ["Zatca"]
