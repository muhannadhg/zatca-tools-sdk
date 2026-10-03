"""Random baskets settled by the production pipeline, settled again here — figure for figure.

``fixtures/random_baskets.json`` holds 500 seeded random baskets (several lines,
fractional quantities, prices with three decimals, VAT-inclusive prices, line and
document discounts, zero-rated, exempt and out-of-scope lines) with the nets,
unit prices, subtotals and totals the platform signed them with.
"""

import json

import pytest

from zatca_tools.tax import lines as line_items
from zatca_tools.tax.totals import DocumentTotals

from .conftest import FIXTURES

CASES = json.loads((FIXTURES / "random_baskets.json").read_text(encoding="utf-8"))


def settle(case):
    raw = line_items.canonical(case["lines"])
    included = case["prices_include_vat"]
    discount = case["discount"]
    derived = (not included) and line_items.derived_net(raw, "S", discount)
    normalized = line_items.normalize(raw, included, "S", None, derived)
    rate = line_items.document_rate(normalized, "S")
    totals = DocumentTotals(normalized, line_items.net_discount(discount, rate, included), line_items.exact_net_discount(discount, rate, included, discount if derived else None))
    return normalized, totals


def test_there_are_enough_cases():
    assert len(CASES) >= 400


@pytest.mark.parametrize("index", range(len(CASES)))
def test_same_settlement(index):
    case = CASES[index]
    normalized, totals = settle(case)
    expected = case["out"]
    assert [line["lineExtensionAmount"] for line in totals.allocated] == pytest.approx(expected["nets"], abs=1e-9)
    assert [line.get("net_price", line["price"]) for line in normalized] == pytest.approx(expected["prices"], abs=1e-9)
    assert (totals.taxable, totals.tax, totals.grand, totals.document_allowance) == pytest.approx(
        (expected["taxable"], expected["tax"], expected["grand"], expected["allowance"]), abs=1e-9
    )
    assert json.dumps(totals.breakdown.sub_totals(), sort_keys=True) == json.dumps(expected["subtotals"], sort_keys=True)
