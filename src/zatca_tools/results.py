"""What operations return. Every result has ``to_dict()``; XML is kept out of it unless asked for."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional, Union

from .design import InvoiceDesign
from .environment import INITIAL_PIH
from .errors import NetworkError, ValidationError, ZatcaRequestError, ZatcaToolsError
from .help import help_url as _help_url
from .models import Credentials

if TYPE_CHECKING:
    pass

DesignInput = Union[InvoiceDesign, Mapping[str, Any], None]


@dataclass(frozen=True)
class Message:
    """One error, warning or note — from ZATCA, from a local check, or from the network.

    ``code`` is ZATCA's code (``BR-KSA-63``), or the SDK's own in snake_case
    (``network_error``), or None when ZATCA sent none. For a local check that
    applies one of ZATCA's rules, ``rule`` names it. ``help_url`` points to the
    page that explains it, when there is one.
    """

    type: str  # "error" | "warning" | "info"
    code: Optional[str]
    message: str
    category: Optional[str] = None
    source: str = "zatca"  # "zatca" | "local" | "network"
    rule: Optional[str] = None

    @property
    def help_url(self) -> Optional[str]:
        return _help_url(self.rule) if self.rule else _help_url(self.code)

    @classmethod
    def from_error(cls, error: ZatcaToolsError) -> "Message":
        return cls("error", error.code, error.message, source=error.source)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "code": self.code,
            "message": self.message,
            "source": self.source,
            "category": self.category,
            "rule": self.rule,
            "help_url": self.help_url,
        }


def zatca_messages(items: Any, kind: str) -> list[Message]:
    """ZATCA's messages as they came: a missing code stays missing, never invented."""
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, Mapping):
            code = item.get("code")
            text = item.get("message")
            out.append(Message(kind, str(code) if code not in (None, "") else None, str(text) if text is not None else "", category=item.get("category")))
        elif item is not None:
            out.append(Message(kind, None, str(item)))
    return out


@dataclass(frozen=True)
class Chain:
    """Where an EGS unit's chain stands: the counter and the hash of the last invoice it signed.

    ZATCA links every invoice to the one before it. The next invoice gets
    ``icv + 1`` and carries ``hash`` as its previous-invoice hash. Save
    ``to_dict()`` after each invoice; pass it back as ``Zatca(chain=...)`` on
    the next start.
    """

    icv: int
    hash: str

    @classmethod
    def new(cls) -> "Chain":
        """A unit that has not signed anything yet."""
        return cls(0, INITIAL_PIH)

    @classmethod
    def parse(cls, value: Union["Chain", Mapping[str, Any], str]) -> "Chain":
        if isinstance(value, Chain):
            return value
        if value == "new":
            return cls.new()
        if isinstance(value, Mapping):
            icv, last = value.get("icv"), value.get("hash")
            if isinstance(icv, int) and not isinstance(icv, bool) and icv >= 0 and _is_base64(last):
                return cls(icv, str(last))
        raise ValidationError(
            "chain is where this unit's chain stands.",
            [{"field": "chain", "message": "Pass the dict saved from zatca.chain.to_dict() — {'icv': <last counter>, 'hash': <last invoice hash>} — or 'new' for a unit that has never signed an invoice."}],
        )

    def to_dict(self) -> dict[str, Any]:
        return {"icv": self.icv, "hash": self.hash}


def _is_base64(value: Any) -> bool:
    try:
        return bool(value) and bool(base64.b64decode(str(value), validate=True))
    except (binascii.Error, ValueError):
        return False


@dataclass
class Invoice:
    """An invoice built, validated, signed and hashed locally — nothing has been sent.

    ``submit()`` sends it. Until ZATCA answers it is not reported or cleared,
    whatever it looks like.
    """

    type: str  # "simplified" | "standard"
    kind: str  # "invoice" | "credit" | "debit"
    number: str
    uuid: str
    icv: int
    pih: str
    date: str
    time: str
    hash: str
    qr: str
    xml: str = field(repr=False)
    totals: dict[str, Any] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[Message] = field(default_factory=list)
    signing_time: str = ""
    design: Optional[InvoiceDesign] = field(default=None, repr=False, compare=False)

    @property
    def operation(self) -> str:
        """What ZATCA does with it: clearance for standard (B2B), reporting for simplified (B2C)."""
        return "clearance" if self.type == "standard" else "reporting"

    def save_xml(self, path: Union[str, Path]) -> Path:
        target = Path(path)
        target.write_text(self.xml, encoding="utf-8", newline="\n")
        return target

    def to_pdf(self, design: DesignInput = None) -> bytes:
        """The printed invoice: PDF/A-3 with this XML embedded.

        A simplified invoice (a receipt) is printed at the sale and reported
        within 24 hours, so it can be printed now. A standard invoice is shared
        only once ZATCA has cleared it: print the result of ``submit()``.
        """
        if self.type == "standard":
            raise ValidationError(
                "A standard invoice is shared only once ZATCA has cleared it.",
                [{"field": "type", "message": "Print it from the result: result = zatca.submit(invoice); result.save_pdf(...) — the PDF then carries ZATCA's cleared copy."}],
            )
        return _render(self.xml, design if design is not None else self.design, self, cleared=False)

    def save_pdf(self, path: Union[str, Path], design: DesignInput = None) -> Path:
        target = Path(path)
        target.write_bytes(self.to_pdf(design))
        return target

    def to_dict(self, include_xml: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "stage": "created",
            "invoice": {
                "type": self.type,
                "kind": self.kind,
                "number": self.number,
                "uuid": self.uuid,
                "icv": self.icv,
                "pih": self.pih,
                "hash": self.hash,
                "date": self.date,
                "time": self.time,
                "signing_time": self.signing_time,
            },
            "submission": self.operation,
            "totals": self.totals,
            "qr": self.qr,
            "warnings": [w.to_dict() for w in self.warnings],
            "xml_bytes": len(self.xml.encode("utf-8")),
        }
        if include_xml:
            out["xml"] = self.xml
        return out

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(**kwargs), ensure_ascii=False, indent=2)


#: ``status`` when ZATCA accepted the invoice.
ACCEPTED = ("REPORTED", "CLEARED")


@dataclass
class SubmissionResult:
    """What became of one invoice sent to ZATCA.

    ``success`` is True only when ZATCA reported or cleared it (possibly with
    warnings). Otherwise ``status`` says what happened and ``error`` why:

    ============================  ===================================================
    ``REPORTED`` / ``CLEARED``    accepted — read ``warnings``
    ``NOT_REPORTED`` / ``NOT_CLEARED``  rejected by ZATCA — ``errors`` are its reasons;
                                  fix them and issue a new invoice
    ``NOT_SENT``                  never reached ZATCA (no connection) — send it again
    ``UNKNOWN``                   sent, no answer — ZATCA may have it; send the same
                                  invoice again, never a new one for the same sale
    ``FAILED``                    ZATCA refused the request, not the invoice
                                  (credentials, service down) — fix, then send it again
    ============================  ===================================================
    """

    operation: str  # "reporting" | "clearance" | "compliance"
    success: bool
    status: str
    invoice: Invoice
    errors: list[Message] = field(default_factory=list)
    warnings: list[Message] = field(default_factory=list)
    info: list[Message] = field(default_factory=list)
    http_status: Optional[int] = None
    validation_status: Optional[str] = None  # PASS / WARNING / ERROR, when ZATCA validated it
    cleared_xml: Optional[str] = field(default=None, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def error(self) -> Optional[Message]:
        """The first of ``errors`` — the one to show. None on success."""
        return self.errors[0] if self.errors else None

    @classmethod
    def from_response(cls, operation: str, http_status: int, body: Mapping[str, Any], invoice: Invoice) -> "SubmissionResult":
        results = body.get("validationResults") if isinstance(body.get("validationResults"), Mapping) else {}
        validation = results.get("status")
        warnings = zatca_messages(results.get("warningMessages"), "warning")
        if operation == "compliance":
            verdict = body.get("reportingStatus") or body.get("clearanceStatus")
            success = http_status in (200, 202) and validation in ("PASS", "WARNING")
            status = verdict or (validation or "FAILED")
        else:
            verdict = body.get("reportingStatus" if operation == "reporting" else "clearanceStatus")
            success = verdict == ("REPORTED" if operation == "reporting" else "CLEARED")
            status = verdict or ("NOT_REPORTED" if operation == "reporting" else "NOT_CLEARED")
        cleared_xml = None
        cleared = body.get("clearedInvoice")
        if isinstance(cleared, str) and cleared:
            try:
                cleared_xml = base64.b64decode(cleared, validate=True).decode("utf-8")
            except (binascii.Error, ValueError):
                warnings.append(Message("warning", "cleared_xml_unreadable", "ZATCA cleared the invoice but its stamped copy could not be decoded; it is kept as received in result.raw['clearedInvoice'].", source="local"))
        return cls(
            operation=operation,
            success=bool(success),
            status=str(status),
            invoice=invoice,
            errors=zatca_messages(results.get("errorMessages"), "error"),
            warnings=warnings,
            info=zatca_messages(results.get("infoMessages"), "info"),
            http_status=http_status,
            validation_status=validation,
            cleared_xml=cleared_xml,
            raw=dict(body),
        )

    @classmethod
    def from_failure(cls, operation: str, invoice: Invoice, error: ZatcaToolsError) -> "SubmissionResult":
        """A request that ended without a verdict: the invoice was not judged."""
        if isinstance(error, NetworkError):
            status = "UNKNOWN" if error.may_have_reached_zatca else "NOT_SENT"
        else:
            status = "FAILED"
        errors = [Message.from_error(error)]
        if isinstance(error, ZatcaRequestError):
            errors += error.errors
        body = getattr(error, "body", None)
        return cls(
            operation=operation,
            success=False,
            status=status,
            invoice=invoice,
            errors=errors,
            http_status=getattr(error, "http_status", None),
            raw=dict(body) if isinstance(body, Mapping) else {},
        )

    @property
    def xml(self) -> str:
        """The invoice of record: ZATCA's stamped copy for a cleared invoice, otherwise what you signed."""
        return self.cleared_xml or self.invoice.xml

    def save_xml(self, path: Union[str, Path]) -> Path:
        target = Path(path)
        target.write_text(self.xml, encoding="utf-8", newline="\n")
        return target

    def to_pdf(self, design: DesignInput = None) -> bytes:
        """The printed invoice of record: PDF/A-3 with ``result.xml`` embedded — ZATCA's cleared copy for clearance."""
        if not self.success:
            raise ValidationError(
                "Only an invoice ZATCA accepted is printed from its result.",
                [{"field": "result", "message": f"This one is {self.status}: {self.error.message if self.error else 'no verdict'}"}],
            )
        return _render(self.xml, design if design is not None else self.invoice.design, self.invoice, cleared=self.cleared_xml is not None)

    def save_pdf(self, path: Union[str, Path], design: DesignInput = None) -> Path:
        target = Path(path)
        target.write_bytes(self.to_pdf(design))
        return target

    def to_dict(self, include_xml: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "success": self.success,
            "status": self.status,
            "operation": self.operation,
            "invoice": {"number": self.invoice.number, "uuid": self.invoice.uuid, "icv": self.invoice.icv, "hash": self.invoice.hash},
            "error": self.error.to_dict() if self.error else None,
            "errors": [m.to_dict() for m in self.errors],
            "warnings": [m.to_dict() for m in self.warnings],
            "http_status": self.http_status,
            "validation_status": self.validation_status,
            "cleared_xml_available": self.cleared_xml is not None,
        }
        if include_xml:
            out["xml"] = self.xml
        return out

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(**kwargs), ensure_ascii=False, indent=2)


def _render(xml: str, design: DesignInput, invoice: "Invoice", *, cleared: bool) -> bytes:
    from .pdf import render

    discount = invoice.totals.get("discount") or 0.0
    return render(
        xml,
        InvoiceDesign.parse(design),
        items=invoice.items,
        gross=invoice.totals.get("line_total") if discount > 0 else None,
        discount=discount if discount > 0 else None,
        cleared=cleared,
    )


@dataclass(frozen=True)
class CsidResult:
    """A CSID (certificate + secret) issued by ZATCA."""

    kind: str  # "compliance" | "production"
    certificate: str = field(repr=False)
    secret: str = field(repr=False)
    request_id: str = ""
    expires_at: Optional[str] = None

    def credentials(self, private_key: str) -> Credentials:
        """Pair the certificate with the private key generated alongside the CSR."""
        return Credentials(certificate=self.certificate, secret=self.secret, private_key=private_key)

    def to_dict(self, include_secrets: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "request_id": self.request_id, "expires_at": self.expires_at}
        if include_secrets:
            out["certificate"] = self.certificate
            out["secret"] = self.secret
        return out


@dataclass
class ComplianceReport:
    """The sample invoices ZATCA requires a unit to pass before it issues a production CSID."""

    checks: list[SubmissionResult]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.success for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [
                {
                    "type": check.invoice.type,
                    "kind": check.invoice.kind,
                    "number": check.invoice.number,
                    "success": check.success,
                    "status": check.status,
                    "validation_status": check.validation_status,
                    "warnings": [m.to_dict() for m in check.warnings],
                    "errors": [m.to_dict() for m in check.errors],
                }
                for check in self.checks
            ],
        }
