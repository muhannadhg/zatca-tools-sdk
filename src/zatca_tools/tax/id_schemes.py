"""Identifiers a party carries beside its VAT number, and what kind each is.

ZATCA's XML names the document an identifier came from: BT-29-1 for the seller
(BR-KSA-08) and BT-46-1 for the buyer (BR-KSA-14). A buyer without a VAT
registration must be identified by one of these on a standard tax invoice.
"""

from __future__ import annotations

import re
from typing import Optional

DEFAULT = "CRN"

#: What a seller may sell under (BR-KSA-08).
SELLER = ("CRN", "MOM", "MLS", "700", "SAG", "OTH")

#: What a buyer may be identified by (BR-KSA-14): the seller's list plus a person's papers.
BUYER = ("CRN", "MOM", "MLS", "700", "SAG", "NAT", "GCC", "IQA", "PAS", "OTH")

LABELS = {
    "CRN": "Commercial registration",
    "MOM": "MOMRAH licence",
    "MLS": "MHRSD licence",
    "700": "Unified number (700)",
    "SAG": "MISA licence",
    "NAT": "National ID",
    "GCC": "GCC ID",
    "IQA": "Iqama number",
    "PAS": "Passport",
    "OTH": "Other identifier",
}

_SHAPES = {
    "CRN": re.compile(r"^\d{10}$"),
    "700": re.compile(r"^7\d{9}$"),
    "NAT": re.compile(r"^1\d{9}$"),
    "IQA": re.compile(r"^2\d{9}$"),
}
_FREE_FORM = re.compile(r"^[A-Za-z0-9]{3,30}$")

_HINTS = {
    "CRN": "a commercial registration is 10 digits",
    "700": "a unified number is 10 digits starting with 7",
    "NAT": "a national ID is 10 digits starting with 1, with a valid check digit",
    "IQA": "an Iqama number is 10 digits starting with 2, with a valid check digit",
}


def is_valid(code: Optional[str], allowed: tuple[str, ...] = BUYER) -> bool:
    return code is not None and code in allowed


def normalise(code: Optional[str], allowed: tuple[str, ...] = BUYER) -> str:
    """A code we know, or the default — never a code written into the XML unchecked."""
    code = str(code or "").strip().upper()
    return code if is_valid(code, allowed) else DEFAULT


def clean(value: Optional[str]) -> str:
    """The number as it should be sent: spaces, dashes, slashes and dots dropped, letters upper-case."""
    return re.sub(r"[\s\-/.]+", "", str(value or "")).upper()


def looks_valid(value: str, code: Optional[str]) -> bool:
    code = normalise(code)
    if not (_SHAPES.get(code) or _FREE_FORM).match(value):
        return False
    # A person's papers carry a check digit, and ZATCA verifies it (BR-KSA-F-13).
    return code not in ("NAT", "IQA") or _check_digit_holds(value)


def shape_hint(code: Optional[str]) -> str:
    code = normalise(code)
    return _HINTS.get(code, f"{LABELS[code]}: letters and digits only, no dashes or spaces")


def _check_digit_holds(value: str) -> bool:
    """The Luhn-style check digit printed on a Saudi national ID and an Iqama."""
    total = 0
    for i in range(9):
        digit = int(value[i])
        if i % 2 == 0:
            digit *= 2
            digit -= 9 if digit > 9 else 0
        total += digit
    return (10 - total % 10) % 10 == int(value[9])
