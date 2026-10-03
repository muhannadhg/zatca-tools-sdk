"""The tax subtotals ZATCA expects: one per VAT treatment and exemption reason.

A document may mix treatments — 15% shampoo beside zero-rated medicine — and each
subtotal carries its own taxable base, its own tax and, for anything but standard
rate, its own exemption reason. The reason is part of the key: two zero-rated
lines on different grounds are two subtotals (ZATCA accepts that with warning
BR-Z-08 / BR-E-08).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .._rounding import round_half_up
from . import categories


class TaxBreakdown:
    """Subtotals per treatment, each taxable amount and tax rounded once from the exact base."""

    def __init__(self, lines: Sequence[Mapping[str, Any]], document_allowance: float = 0.0) -> None:
        self._groups: dict[str, dict[str, Any]] = {}
        for line in lines:
            category = categories.of(line)
            reason = categories.reason_code_for(category, line.get("tax_reason_code"))
            key = f"{category}|{reason or ''}"
            group = self._groups.setdefault(key, {"category": category, "taxable": 0.0, "exact": 0.0, "reasonCode": reason})
            group["taxable"] += float(line.get("lineExtensionAmount", 0) or 0)
            group["exact"] += float(line.get("exact_net", line.get("lineExtensionAmount", 0)) or 0)

        # When every line knows its exact worth, the exact figures already carry the
        # whole discount: the document allowance is a consequence, not an input.
        exact = len(lines) > 0 and all("exact_net" in line for line in lines)

        if not self._groups:
            self._groups[categories.STANDARD + "|"] = {"category": categories.STANDARD, "taxable": 0.0, "exact": 0.0, "reasonCode": None}

        # The document-level remainder (only ever halalas) lands on the largest base.
        self._allowance_key = next(iter(self._groups))
        for key, group in self._groups.items():
            if group["taxable"] > self._groups[self._allowance_key]["taxable"]:
                self._allowance_key = key

        if not exact and abs(document_allowance) > 0.001:
            self._groups[self._allowance_key]["taxable"] -= document_allowance
            self._groups[self._allowance_key]["exact"] -= document_allowance

        for group in self._groups.values():
            group["taxable"] = round_half_up(group["exact"], 2)
            group["tax"] = round_half_up(group["exact"] * categories.rate(group["category"]) / 100, 2)

    def taxable_total(self) -> float:
        return round_half_up(sum(g["taxable"] for g in self._groups.values()), 2)

    def tax_total(self) -> float:
        return round_half_up(sum(g["tax"] for g in self._groups.values()), 2)

    def grand_total(self) -> float:
        return round_half_up(self.taxable_total() + self.tax_total(), 2)

    def allowance_category(self) -> dict[str, Any]:
        """The tax category a document-level allowance is declared under."""
        group = self._groups[self._allowance_key]
        category: dict[str, Any] = {"id": group["category"], "percent": categories.rate(group["category"]), "taxScheme": {"id": "VAT"}}
        if group.get("reasonCode"):
            category["reasonCode"] = group["reasonCode"]
            category["reason"] = categories.reason_text(group["reasonCode"])
        return category

    def sub_totals(self) -> list[dict[str, Any]]:
        out = []
        for group in self._groups.values():
            tax_category: dict[str, Any] = {"id": group["category"], "percent": categories.rate(group["category"]), "taxScheme": {"id": "VAT"}}
            if group["reasonCode"] is not None:
                tax_category["reasonCode"] = group["reasonCode"]
                tax_category["reason"] = categories.reason_text(group["reasonCode"])
            out.append({"taxableAmount": group["taxable"], "taxAmount": group["tax"], "taxCategory": tax_category})
        return out

    def summary(self) -> list[dict[str, Any]]:
        """The subtotals in the SDK's public snake_case shape."""
        return [
            {
                "category": g["category"],
                "rate": categories.rate(g["category"]),
                "taxable": g["taxable"],
                "tax": g["tax"],
                "reason_code": g["reasonCode"],
                "reason": categories.reason_text(g["reasonCode"]),
            }
            for g in self._groups.values()
        ]

