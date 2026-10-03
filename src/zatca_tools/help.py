"""Where to read about an error: a link into the ZATCA Tools documentation, built offline.

The explanations live on zatcatools.com, not in this package. The SDK ships only
the list of codes the error reference documents (``data/error_reference.json``,
exported from the site) and turns a code into a URL locally — nothing is
fetched, and nothing is needed to handle an error in code.

A URL carries a code and nothing else: never invoice content, a VAT number, a
certificate or anything from ZATCA's answer. A code that is not on the
published list gets the reference's front page, so an arbitrary string never
travels into a URL either.

    >>> help_url("BR-KSA-63")       # a code with a written guide
    'https://zatcatools.com/en/docs/errors/br-ksa-63'
    >>> help_url("BR-KSA-26")       # documented, no guide: its row in the reference
    'https://zatcatools.com/en/docs/errors#BR-KSA-26'
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any, Optional

SITE = "https://zatcatools.com"
REFERENCE_INDEX = "/en/docs/errors"
GUIDE = "/en/docs/errors/{slug}"
REFERENCE_ROW = "/en/docs/errors#{code}"

#: The SDK's own codes, and the few ZATCA onboarding codes the error reference
#: does not cover, explained in the SDK documentation.
SDK_PAGES = {
    "validation_error": "/docs/sdk/advanced#validation_error",
    "xml_error": "/docs/sdk/advanced#xml_error",
    "signing_error": "/docs/sdk/advanced#signing_error",
    "authentication_error": "/docs/sdk/advanced#authentication_error",
    "zatca_request_error": "/docs/sdk/advanced#zatca_request_error",
    "zatca_service_error": "/docs/sdk/advanced#zatca_service_error",
    "network_error": "/docs/sdk/advanced#network_error",
    "compliance_checks_failed": "/docs/sdk/advanced#compliance_checks_failed",
    "missing_exemption_reason": "/docs/sdk/advanced#missing_exemption_reason",
    "buyer_address_incomplete": "/docs/sdk/advanced#buyer_address_incomplete",
    "standard_invoice_required": "/docs/sdk/advanced#standard_invoice_required",
    "cleared_xml_unreadable": "/docs/sdk/advanced#cleared_xml_unreadable",
    "pdf_error": "/docs/sdk/advanced#pdf_error",
    "Invalid-OTP": "/docs/sdk/setup#otp",
    "certificate-permissions": "/docs/sdk/advanced#certificate-permissions",
}

#: What a code looks like. Anything else is not a code, and gets no URL.
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")


def help_url(code: Any) -> Optional[str]:
    """The page that explains ``code``, or None when ``code`` is not a code.

    In order: the SDK's own page for an SDK code; the written guide for a ZATCA
    rule that has one; its row in the error reference for a documented rule
    without a guide; the reference's front page for any other code.
    """
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        return None
    if code in SDK_PAGES:
        return SITE + SDK_PAGES[code]
    guides, codes = _reference()
    canonical = code.upper()
    if canonical in guides:
        return SITE + GUIDE.format(slug=canonical.lower())
    if canonical in codes:
        return SITE + REFERENCE_ROW.format(code=canonical)
    return SITE + REFERENCE_INDEX


@lru_cache(maxsize=1)
def _reference() -> tuple[frozenset[str], frozenset[str]]:
    """The codes with a guide, and every documented code. Empty if the list cannot be read."""
    data = _load()
    guides = frozenset(code for code in data.get("guides", []) if isinstance(code, str))
    codes = frozenset(code for code in data.get("codes", []) if isinstance(code, str)) | guides
    return guides, codes


def _load() -> dict[str, Any]:
    try:
        data = json.loads(resources.files("zatca_tools").joinpath("data").joinpath("error_reference.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


__all__ = ["help_url"]
