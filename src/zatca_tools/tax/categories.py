"""The four VAT treatments a Saudi invoice line can carry, and ZATCA's exemption reasons.

For zero-rated (Z), exempt (E) and out-of-scope (O) supplies, ZATCA reads a
reason *code* from its published list together with the reason *text* on the tax
subtotal; a code never travels without its text (BR-KSA-83). The text written
into the XML is the Authority's own wording, verbatim — odd capitals included.

A reason is optional: ZATCA accepts a zero-rated or exempt subtotal without one,
with warning BR-KSA-69 / BR-KSA-23. None is ever guessed here — a guessed reason
would state a legal basis the seller never gave.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

STANDARD = "S"
STANDARD_RATE = 15.0

#: category code -> (rate, English name, whether ZATCA expects an exemption reason)
CATEGORIES: dict[str, dict[str, Any]] = {
    "S": {"rate": 15.0, "name": "Standard rate (15%)", "needs_reason": False},
    "Z": {"rate": 0.0, "name": "Zero rated", "needs_reason": True},
    "E": {"rate": 0.0, "name": "Exempt", "needs_reason": True},
    "O": {"rate": 0.0, "name": "Out of scope", "needs_reason": True},
}

#: ZATCA's VATEX code list: code -> (category, reason text written to the XML)
REASONS: dict[str, dict[str, str]] = {
    # Zero rated
    "VATEX-SA-32": {"category": "Z", "reason": "Export of goods"},
    "VATEX-SA-33": {"category": "Z", "reason": "Export of services"},
    "VATEX-SA-34-1": {"category": "Z", "reason": "The international transport of Goods"},
    "VATEX-SA-34-2": {"category": "Z", "reason": "international transport of passengers"},
    "VATEX-SA-34-3": {"category": "Z", "reason": "services directly connected and incidental to a Supply of international passenger transport"},
    "VATEX-SA-34-4": {"category": "Z", "reason": "Supply of a qualifying means of transport"},
    "VATEX-SA-34-5": {"category": "Z", "reason": "Any services relating to Goods or passenger transportation, as defined in article twenty five of these Regulations"},
    "VATEX-SA-35": {"category": "Z", "reason": "Medicines and medical equipment"},
    "VATEX-SA-36": {"category": "Z", "reason": "Qualifying metals"},
    "VATEX-SA-EDU": {"category": "Z", "reason": "Private education to citizen"},
    "VATEX-SA-HEA": {"category": "Z", "reason": "Private healthcare to citizen"},
    # Exempt
    "VATEX-SA-29": {"category": "E", "reason": "Financial services mentioned in Article 29 of the VAT Regulations"},
    "VATEX-SA-29-7": {"category": "E", "reason": "Life insurance services mentioned in Article 29 of the VAT Regulations"},
    "VATEX-SA-30": {"category": "E", "reason": "Real estate transactions mentioned in Article 30 of the VAT Regulations"},
    # Out of scope
    "VATEX-SA-OOS": {"category": "O", "reason": "Outside scope of VAT"},
}


def is_valid(category: Optional[str]) -> bool:
    return category is not None and category in CATEGORIES


def rate(category: Optional[str]) -> float:
    """The percentage that goes on the line and the subtotal."""
    return CATEGORIES.get(category if category is not None else STANDARD, {}).get("rate", STANDARD_RATE)


def needs_reason(category: Optional[str]) -> bool:
    return bool(CATEGORIES.get(category if category is not None else STANDARD, {}).get("needs_reason", False))


def reason_code_for(category: Optional[str], chosen: Optional[str] = None) -> Optional[str]:
    """The reason code a line carries: the one chosen when it belongs to the category, else none.

    Out of scope has a single code, so there is nothing to guess and it is filled in.
    """
    if not needs_reason(category):
        return None
    if chosen is not None and REASONS.get(chosen, {}).get("category") == category:
        return chosen
    return "VATEX-SA-OOS" if category == "O" else None


def reason_text(code: Optional[str]) -> Optional[str]:
    """ZATCA's own wording for a code — the string written into the XML."""
    return None if code is None else REASONS.get(code, {}).get("reason")


def requires_national_id(lines: list[Mapping[str, Any]]) -> bool:
    """BR-KSA-49: education or healthcare zero-rated for a citizen needs the buyer's national ID."""
    return any(line.get("tax_reason_code") in ("VATEX-SA-EDU", "VATEX-SA-HEA") for line in lines)


def of(line: Mapping[str, Any], default: Optional[str] = None) -> str:
    """The category a line is actually issued under.

    A line that names none takes ``default`` (the seller's usual treatment), or
    standard rate. A line that names something unrecognisable is standard-rated:
    a line wrongly taxed at 15% is a correctable overpayment, while a line wrongly
    zero-rated is under-declared VAT.
    """
    category = line.get("tax_category", line.get("taxCategory"))
    if category is None or category == "":
        return default if is_valid(default) else STANDARD
    return category if is_valid(category) else STANDARD
