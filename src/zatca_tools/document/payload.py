"""From validated input to the document's content: every figure the XML will carry.

The discount is pushed down into the lines rather than declared on the document,
so the printed invoice adds up; whatever the lines cannot absorb (a few halalas,
because a line net must stay ``quantity × unit price``) is declared once at
document level. The taxable amount is then restated as a subtraction, so the
identity ZATCA checks — ``taxable = Σ line nets − allowances`` (BR-CO-13) —
holds by construction whichever way the halalas fell.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from .._rounding import round_half_up
from ..models import Seller
from ..tax import categories
from ..tax.breakdown import TaxBreakdown
from ..tax.discounts import settle


def build(
    seller: Seller,
    form: str,
    kind: str,
    *,
    number: str,
    uuid: str,
    icv: int,
    pih: str,
    issue_date: str,
    issue_time: str,
    lines: list[dict[str, Any]],
    discount: float,
    discount_exact: Optional[float],
    buyer: Optional[Mapping[str, Any]] = None,
    billing_reference: Optional[str] = None,
    note_reason: Optional[str] = None,
    delivery_date: Optional[str] = None,
) -> dict[str, Any]:
    discount = round_half_up(discount or 0, 2)
    invoice_lines = _map_lines(lines, settle(lines, discount, discount_exact))

    gross = round_half_up(sum(float(line["lineExtensionAmount"]) for line in lines), 2)
    line_extension_total = round_half_up(sum(float(line["lineExtensionAmount"]) for line in invoice_lines), 2)
    document_allowance = round_half_up(discount - (gross - line_extension_total), 2)

    breakdown = TaxBreakdown(invoice_lines, document_allowance)
    taxable = breakdown.taxable_total()
    tax_amount = breakdown.tax_total()
    document_allowance = round_half_up(line_extension_total - taxable, 2)

    payment_means: dict[str, Any] = {"code": "10"}
    if kind in ("credit", "debit"):
        payment_means["note"] = note_reason  # BR-KSA-17: a note states why it was issued

    payload: dict[str, Any] = {
        "uuid": uuid,
        "id": number,
        "issueDate": issue_date,
        "issueTime": issue_time,
        "invoiceType": {"invoice": form, "type": kind},
        "icv": icv,
        "pih": pih,
        "supplier": {
            "taxId": seller.vat_number,
            "registrationName": seller.name,
            "identificationId": seller.cr_number,
            "identificationType": seller.cr_scheme,
            "address": {
                "street": seller.address.street,
                "buildingNumber": seller.address.building_number,
                "subdivision": seller.address.district,
                "city": seller.address.city,
                "postalZone": seller.address.postal_code,
                "country": seller.address.country or "SA",
            },
        },
        "paymentMeans": payment_means,
        "invoiceLines": invoice_lines,
        "taxTotal": {"taxAmount": tax_amount, "subTotals": breakdown.sub_totals()},
        "legalMonetaryTotal": {
            "lineExtensionAmount": line_extension_total,
            "taxExclusiveAmount": taxable,
            "taxInclusiveAmount": round_half_up(taxable + tax_amount, 2),
            "allowanceTotalAmount": document_allowance,
            "prepaidAmount": 0,
            "payableAmount": round_half_up(taxable + tax_amount, 2),
        },
    }

    if document_allowance > 0:
        payload["allowanceCharges"] = [{
            "isCharge": False,
            "reason": "خصم",
            "amount": document_allowance,
            "taxCategories": [breakdown.allowance_category()],
        }]

    if form == "standard":
        payload["customer"] = dict(buyer or {})
        payload["delivery"] = {"actualDeliveryDate": delivery_date or issue_date}
    elif buyer:
        payload["customer"] = dict(buyer)

    if kind in ("credit", "debit") and billing_reference:
        payload["billingReferences"] = [{"id": billing_reference}]

    payload["_breakdown"] = breakdown
    return payload


def buyer_party(buyer: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """The buyer as the XML describes it. ``taxId`` is None (no CompanyID) for a buyer without VAT."""
    if not buyer:
        return None
    party: dict[str, Any] = {
        "taxId": buyer.get("vat_number") or None,
        "registrationName": buyer.get("name"),
        "identificationId": buyer.get("id") or None,
        "identificationType": buyer.get("id_scheme") or "CRN",
    }
    address = buyer.get("address") or {}
    parts = {
        "street": address.get("street"),
        "buildingNumber": address.get("building_number"),
        "subdivision": address.get("district"),
        "city": address.get("city"),
        "postalZone": address.get("postal_code"),
    }
    parts = {key: value for key, value in parts.items() if value not in (None, "")}
    if parts:
        party["address"] = {**parts, "country": address.get("country") or "SA"}
    return party


def _map_lines(lines: list[dict[str, Any]], settled: list[dict[str, float]]) -> list[dict[str, Any]]:
    mapped = []
    for i, line in enumerate(lines):
        piece = settled[i]
        category = categories.of(line)
        item: dict[str, Any] = {
            "id": i + 1,
            "unitCode": line.get("unitCode") or "PCE",
            "quantity": line["quantity"],
            "lineExtensionAmount": piece["net"],
            "item": {
                # One line for ZATCA: the item name (BT-153) is a name, not a paragraph.
                "name": re.sub(r"\s+", " ", str(line["name"])).strip(),
                "classifiedTaxCategory": [{"id": category, "percent": categories.rate(category), "taxScheme": {"id": "VAT"}}],
            },
            "price": {"amount": piece["price"]},
            "taxTotal": {"taxAmount": piece["tax"], "roundingAmount": round_half_up(piece["net"] + piece["tax"], 2)},
            "tax_category": category,
            "tax_reason_code": line.get("tax_reason_code"),
        }
        if "exact_net" in line:
            item["exact_net"] = piece["exact"]
        mapped.append(item)
    return mapped
