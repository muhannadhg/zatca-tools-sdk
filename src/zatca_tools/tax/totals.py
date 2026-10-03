"""One settlement of a document: totals, discount spread, allowance, tax per treatment."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from .._rounding import round_half_up
from .breakdown import TaxBreakdown
from .discounts import settle


class DocumentTotals:
    """The figures a document is signed with.

    ``exact_discount`` is the discount before rounding to the halala — set when
    every line knows its exact worth (prices typed with VAT): the tax is then
    worked out from exact values, as ZATCA's s.10 requires ("rounding shall be
    done on the final calculation results, not on any intermediate results").
    """

    def __init__(self, lines: Sequence[Mapping[str, Any]], discount: float = 0.0, exact_discount: Optional[float] = None) -> None:
        self.line_total = round_half_up(sum(float(line["lineExtensionAmount"]) for line in lines), 2)
        self.discount = min(max(0.0, discount), self.line_total)

        exact_lines = len(lines) > 0 and all("exact_net" in line for line in lines)
        if not exact_lines:
            self.exact_discount: Optional[float] = None
        elif self.discount <= 0:
            self.exact_discount = 0.0
        elif exact_discount is not None and abs(exact_discount - self.discount) < 0.01:
            self.exact_discount = exact_discount
        else:
            self.exact_discount = self.discount

        settled = settle(lines, self.discount, self.exact_discount)
        self.allocated = []
        for line, piece in zip(lines, settled):
            allocated = {**line, "lineExtensionAmount": piece["net"]}
            if "exact_net" in line:
                allocated["exact_net"] = piece["exact"]
            self.allocated.append(allocated)

        allocated_total = round_half_up(sum(float(line["lineExtensionAmount"]) for line in self.allocated), 2)
        absorbed = round_half_up(self.line_total - allocated_total, 2)

        self.breakdown = TaxBreakdown(self.allocated, round_half_up(self.discount - absorbed, 2))
        self.taxable = self.breakdown.taxable_total()
        self.tax = self.breakdown.tax_total()
        self.grand = round_half_up(self.taxable + self.tax, 2)
        self.document_allowance = round_half_up(allocated_total - self.taxable, 2)
        self.rounding_allowance = round_half_up(self.document_allowance - round_half_up(self.discount - absorbed, 2), 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_total": self.line_total,
            "discount": self.discount,
            "taxable": self.taxable,
            "tax": self.tax,
            "total": self.grand,
            "allowance": self.document_allowance,
            "subtotals": self.breakdown.summary(),
        }
