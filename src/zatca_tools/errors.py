"""Exceptions, for what stops an operation before ZATCA has judged an invoice.

Every error — raised here, or carried in a result — answers the same three
questions: ``code`` (what), ``message`` (why, in a sentence) and ``help_url``
(where to read how to fix it). ``source`` says where it happened:

* ``local``   — in your process, before anything was sent: ``ValidationError``
                (the data), ``XmlError`` (the document), ``SigningError`` (the
                certificate or key), ``PdfError`` (the printed copy).
* ``network`` — between you and ZATCA: ``NetworkError``.
* ``zatca``   — ZATCA answered, but not with a verdict on an invoice:
                ``AuthenticationError``, ``ZatcaRequestError``,
                ``ZatcaServiceError``, ``ComplianceCheckError``.

``Zatca.submit()`` does not raise these: it returns a ``SubmissionResult``
whose ``error`` holds the same code, message and help link. They are raised by
``create_invoice()`` (local errors) and by onboarding.

Codes written in snake_case are the SDK's own. Every other code came from ZATCA.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from .help import help_url

if TYPE_CHECKING:
    from .results import ComplianceReport, Message


class ZatcaToolsError(Exception):
    """Base class for every error raised by this package."""

    code = "error"
    source = "local"

    @property
    def message(self) -> str:
        return str(self)

    @property
    def help_url(self) -> Optional[str]:
        return help_url(self.code)

    def details(self) -> dict[str, Any]:
        """What this kind of error adds to code, message, source and help_url."""
        return {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": {"code": self.code, "message": self.message, "source": self.source, "help_url": self.help_url, **self.details()},
        }


class ValidationError(ZatcaToolsError, ValueError):
    """The data cannot become a valid invoice. Nothing was sent.

    ``errors`` lists every problem at once: ``field``, ``message``, and — when
    the check applies one of ZATCA's rules — that ``rule`` and its ``help_url``.
    """

    code = "validation_error"

    def __init__(self, message: str, errors: Optional[list[dict[str, Any]]] = None) -> None:
        super().__init__(message)
        self.errors = [problem(**e) for e in (errors or [])]

    def details(self) -> dict[str, Any]:
        return {"errors": self.errors}


def problem(field: str, message: str, rule: Optional[str] = None, **_: Any) -> dict[str, Any]:
    """One field's problem. ``rule`` is the ZATCA rule the local check applies — ZATCA did not return it."""
    return {"field": field, "message": message, "rule": rule, "help_url": help_url(rule) if rule else None}


class XmlError(ZatcaToolsError):
    """The invoice could not be written as well-formed XML. Nothing was sent."""

    code = "xml_error"


class PdfError(ZatcaToolsError):
    """The PDF could not be produced: the logo is unreadable, the XML cannot be printed, or the installation is incomplete."""

    code = "pdf_error"


class SigningError(ZatcaToolsError):
    """The certificate or private key cannot sign: unreadable, the wrong curve, or not a pair. Nothing was sent."""

    code = "signing_error"


class NetworkError(ZatcaToolsError):
    """The request failed in transit.

    ``may_have_reached_zatca`` is True for a timeout after the request was sent:
    ZATCA may have processed it. Send the *same* invoice again (same UUID, same
    hash); never create a new one for the same sale.
    """

    code = "network_error"
    source = "network"

    def __init__(self, message: str, may_have_reached_zatca: bool) -> None:
        super().__init__(message)
        self.may_have_reached_zatca = may_have_reached_zatca

    def details(self) -> dict[str, Any]:
        return {"may_have_reached_zatca": self.may_have_reached_zatca}


class AuthenticationError(ZatcaToolsError):
    """ZATCA refused the credentials (HTTP 401): the certificate and secret, or the environment, do not match."""

    code = "authentication_error"
    source = "zatca"

    def __init__(self, message: str, http_status: int = 401) -> None:
        super().__init__(message)
        self.http_status = http_status

    def details(self) -> dict[str, Any]:
        return {"http_status": self.http_status}


class ZatcaServiceError(ZatcaToolsError):
    """ZATCA answered, but not with a verdict: a server error, maintenance, rate limiting or an unexpected body."""

    code = "zatca_service_error"
    source = "zatca"

    def __init__(self, message: str, http_status: Optional[int] = None, body: Any = None) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.body = body

    def details(self) -> dict[str, Any]:
        return {"http_status": self.http_status}


class ZatcaRequestError(ZatcaToolsError):
    """ZATCA refused the request itself (HTTP 4xx other than 401) — an invalid or expired OTP, a CSR it cannot read.

    ``errors`` holds ZATCA's own messages, each with its code and help link;
    ``body`` keeps the answer exactly as it came.
    """

    code = "zatca_request_error"
    source = "zatca"

    def __init__(self, message: str, http_status: int, errors: Optional[list["Message"]] = None, body: Any = None) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.errors = list(errors or [])
        self.body = body

    @property
    def help_url(self) -> Optional[str]:
        # ZATCA's own code is the more precise pointer when it gave one.
        for error in self.errors:
            if error.code and error.help_url:
                return error.help_url
        return help_url(self.code)

    def details(self) -> dict[str, Any]:
        return {"http_status": self.http_status, "errors": [e.to_dict() for e in self.errors]}


class ComplianceCheckError(ZatcaToolsError):
    """ZATCA did not pass the sample invoices onboarding sends; ``report`` holds every check and ZATCA's reasons."""

    code = "compliance_checks_failed"
    source = "zatca"

    def __init__(self, message: str, report: "ComplianceReport") -> None:
        super().__init__(message)
        self.report = report

    @property
    def errors(self) -> list["Message"]:
        return [error for check in self.report.checks for error in check.errors]

    def details(self) -> dict[str, Any]:
        return {"errors": [e.to_dict() for e in self.errors]}


__all__ = [
    "AuthenticationError",
    "ComplianceCheckError",
    "NetworkError",
    "PdfError",
    "SigningError",
    "ValidationError",
    "XmlError",
    "ZatcaRequestError",
    "ZatcaServiceError",
    "ZatcaToolsError",
]
