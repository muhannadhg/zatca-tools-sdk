"""ZATCA's three environments and what differs between them."""

from __future__ import annotations

from enum import Enum


class Environment(str, Enum):
    """Where documents go.

    * ``sandbox``    — ZATCA's developer portal. Open to anyone, fixed test OTP
      (123345), fixed test VAT number. Nothing issued there is a tax invoice.
    * ``simulation`` — the Fatoora simulation portal: your real VAT number and an
      OTP from the simulation portal. Nothing issued there is a tax invoice.
    * ``production`` — real tax invoices. Needs an OTP from fatoora.zatca.gov.sa.
    """

    SANDBOX = "sandbox"
    SIMULATION = "simulation"
    PRODUCTION = "production"

    @property
    def base_url(self) -> str:
        return {
            Environment.SANDBOX: "https://gw-fatoora.zatca.gov.sa/e-invoicing/developer-portal",
            Environment.SIMULATION: "https://gw-fatoora.zatca.gov.sa/e-invoicing/simulation",
            Environment.PRODUCTION: "https://gw-fatoora.zatca.gov.sa/e-invoicing/core",
        }[self]

    @property
    def csr_template(self) -> str:
        """The certificate template name written into the CSR (OID 1.3.6.1.4.1.311.20.2)."""
        return {
            Environment.SANDBOX: "TSTZATCA-Code-Signing",
            Environment.SIMULATION: "PREZATCA-Code-Signing",
            Environment.PRODUCTION: "ZATCA-Code-Signing",
        }[self]

    @classmethod
    def parse(cls, value: "Environment | str") -> "Environment":
        if isinstance(value, Environment):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            raise ValueError("environment must be 'sandbox', 'simulation' or 'production'.") from None


#: The OTP of ZATCA's public sandbox: a published test value, the same for everyone.
SANDBOX_OTP = "123345"

#: The previous-invoice hash of the very first document an EGS unit signs: base64 of the hex SHA-256 of "0".
INITIAL_PIH = "NWZlY2ViNjZmZmM4NmYzOGQ5NTI3ODZjNmQ2OTZjNzljMmRiYzIzOWRkNGU5MWI0NjcyOWQ3M2EyN2ZiNTdlOQ=="
