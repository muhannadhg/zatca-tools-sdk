"""ZATCA Tools SDK — ZATCA (Fatoora) e-invoicing for Saudi Arabia, from your own application.

    from zatca_tools import Zatca

    zatca = Zatca("sandbox")
    zatca.onboard()
    invoice = zatca.create_invoice({"type": "simplified", "number": "INV-1001",
                                    "items": [{"name": "Product", "quantity": 2, "unit_price": 100}]})
    result = zatca.submit(invoice)

Runs entirely in your environment: invoices are built, signed and hashed
locally, and sent straight to ZATCA. Nothing passes through ZATCA Tools.
"""

__version__ = "0.1.0"

from .client import Zatca  # noqa: E402
from .csr import CsrRequest, KeyPair  # noqa: E402
from .design import InvoiceDesign  # noqa: E402
from .environment import INITIAL_PIH, Environment  # noqa: E402
from .errors import (  # noqa: E402
    AuthenticationError,
    ComplianceCheckError,
    NetworkError,
    PdfError,
    SigningError,
    ValidationError,
    XmlError,
    ZatcaRequestError,
    ZatcaServiceError,
    ZatcaToolsError,
)
from .help import help_url  # noqa: E402
from .models import Address, Credentials, Seller  # noqa: E402
from .results import Chain, ComplianceReport, CsidResult, Invoice, Message, SubmissionResult  # noqa: E402
from .signing.qr import decode as decode_qr  # noqa: E402

__all__ = [
    "Address",
    "AuthenticationError",
    "Chain",
    "ComplianceCheckError",
    "ComplianceReport",
    "Credentials",
    "CsidResult",
    "CsrRequest",
    "Environment",
    "INITIAL_PIH",
    "Invoice",
    "InvoiceDesign",
    "KeyPair",
    "Message",
    "NetworkError",
    "PdfError",
    "Seller",
    "SigningError",
    "SubmissionResult",
    "ValidationError",
    "XmlError",
    "Zatca",
    "ZatcaRequestError",
    "ZatcaServiceError",
    "ZatcaToolsError",
    "decode_qr",
    "help_url",
    "__version__",
]
