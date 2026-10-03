"""The HTTP calls to ZATCA's Fatoora API — made directly from your application.

Nothing here talks to ZATCA Tools. Each method returns ``(http_status, body)``
for the caller to interpret; transport failures and credential refusals become
exceptions that say whether the request may have reached ZATCA.
"""

from __future__ import annotations

import base64
from typing import Any, Optional

import httpx

from . import __version__
from .environment import Environment
from .errors import AuthenticationError, NetworkError, ZatcaRequestError, ZatcaServiceError
from .results import Message, zatca_messages

#: Statuses on which ZATCA can answer with a verdict on the document (accepted, warned, rejected).
VERDICT_STATUSES = (200, 202, 400)


def is_verdict(body: dict[str, Any]) -> bool:
    """Whether ZATCA judged the document: it validated it, or said reported/cleared.

    A 400 without validation results is a refused request, not a rejected invoice.
    """
    return isinstance(body.get("validationResults"), dict) or any(body.get(key) for key in ("reportingStatus", "clearanceStatus"))


class ZatcaApi:
    def __init__(self, environment: Environment, http_client: Optional[httpx.Client] = None, timeout: float = 30.0) -> None:
        self.environment = environment
        self._client = http_client or httpx.Client(timeout=timeout)
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- certificates ------------------------------------------------------------------

    def compliance_csid(self, csr_base64: str, otp: str) -> tuple[int, dict[str, Any]]:
        return self._send("POST", "/compliance", {"csr": csr_base64}, headers={"OTP": otp})

    def production_csid(self, certificate: str, secret: str, compliance_request_id: str) -> tuple[int, dict[str, Any]]:
        return self._send("POST", "/production/csids", {"compliance_request_id": compliance_request_id}, auth=(certificate, secret))

    def renew_production_csid(self, certificate: str, secret: str, csr_base64: str, otp: str) -> tuple[int, dict[str, Any]]:
        return self._send("PATCH", "/production/csids", {"csr": csr_base64}, headers={"OTP": otp}, auth=(certificate, secret))

    # -- documents ---------------------------------------------------------------------

    def report(self, certificate: str, secret: str, xml: str, invoice_hash: str, uuid: str) -> tuple[int, dict[str, Any]]:
        return self._document("/invoices/reporting/single", "0", certificate, secret, xml, invoice_hash, uuid)

    def clear(self, certificate: str, secret: str, xml: str, invoice_hash: str, uuid: str) -> tuple[int, dict[str, Any]]:
        return self._document("/invoices/clearance/single", "1", certificate, secret, xml, invoice_hash, uuid)

    def compliance_check(self, certificate: str, secret: str, xml: str, invoice_hash: str, uuid: str) -> tuple[int, dict[str, Any]]:
        return self._document("/compliance/invoices", None, certificate, secret, xml, invoice_hash, uuid)

    # -- plumbing ----------------------------------------------------------------------

    def _document(self, path: str, clearance: Optional[str], certificate: str, secret: str, xml: str, invoice_hash: str, uuid: str) -> tuple[int, dict[str, Any]]:
        body = {"invoiceHash": invoice_hash, "uuid": uuid, "invoice": base64.b64encode(xml.encode("utf-8")).decode()}
        headers = {"Clearance-Status": clearance} if clearance is not None else {}
        return self._send("POST", path, body, headers=headers, auth=(certificate, secret), verdict=True)

    def _send(
        self,
        method: str,
        path: str,
        body: dict[str, Any],
        headers: Optional[dict[str, str]] = None,
        auth: Optional[tuple[str, str]] = None,
        verdict: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        request_headers = {
            "Accept": "application/json",
            "Accept-Language": "en",
            "Accept-Version": "V2",
            "Content-Type": "application/json",
            "User-Agent": f"zatca-tools-sdk-python/{__version__}",
            **(headers or {}),
        }
        if auth is not None:
            token = base64.b64encode(auth[0].strip().encode()).decode()
            request_headers["Authorization"] = "Basic " + base64.b64encode(f"{token}:{auth[1].strip()}".encode()).decode()

        try:
            response = self._client.request(method, self.environment.base_url + path, json=body, headers=request_headers)
        except httpx.ConnectError as exc:
            raise NetworkError(f"Could not connect to ZATCA ({exc.__class__.__name__}).", may_have_reached_zatca=False) from exc
        except httpx.ConnectTimeout as exc:
            raise NetworkError("Timed out connecting to ZATCA.", may_have_reached_zatca=False) from exc
        except httpx.TimeoutException as exc:
            raise NetworkError("ZATCA did not answer in time; the request may have been processed.", may_have_reached_zatca=True) from exc
        except httpx.TransportError as exc:
            raise NetworkError(f"The connection to ZATCA failed ({exc.__class__.__name__}).", may_have_reached_zatca=True) from exc

        status = response.status_code
        try:
            data = response.json() if response.content.strip() else {}
        except ValueError:
            data = {"raw": response.text[:1000]}
        if not isinstance(data, dict):
            data = {"value": data}

        if status == 401:
            raise AuthenticationError("ZATCA refused the credentials (401). Check the certificate, the secret and that they belong to this environment.")
        if verdict and status in VERDICT_STATUSES and is_verdict(data):
            return status, data
        if verdict and 200 <= status < 300:
            raise ZatcaServiceError(f"ZATCA answered HTTP {status} without a verdict on the invoice.", http_status=status, body=data)
        if 200 <= status < 300:
            return status, data
        if status >= 500 or status in (303, 429):
            raise ZatcaServiceError(_describe(status, data), http_status=status, body=data)
        raise ZatcaRequestError(_describe(status, data), http_status=status, errors=_zatca_errors(data), body=data)


def _zatca_errors(data: dict[str, Any]) -> list[Message]:
    """ZATCA's reasons for refusing a request, in either of the shapes it uses."""
    errors = zatca_messages(data.get("errors"), "error")
    if not errors and (data.get("errorCode") or data.get("errorMessage")):
        errors = zatca_messages([{"code": data.get("errorCode"), "message": data.get("errorMessage")}], "error")
    return errors


def _describe(status: int, data: dict[str, Any]) -> str:
    if status == 303:
        return "ZATCA answered 303: clearance is switched off for this taxpayer; report the document instead."
    if status == 429:
        return "ZATCA answered 429: too many requests. Wait and retry."
    messages = []
    for key in ("message", "errorMessage"):
        if isinstance(data.get(key), str):
            messages.append(data[key])
    for error in data.get("errors", []) if isinstance(data.get("errors"), list) else []:
        messages.append(str(error.get("message") or error.get("code") or error) if isinstance(error, dict) else str(error))
    detail = "; ".join(messages) if messages else "no details"
    return f"ZATCA answered HTTP {status}: {detail}"
