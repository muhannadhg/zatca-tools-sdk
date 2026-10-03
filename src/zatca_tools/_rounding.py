"""Rounding exactly as ZATCA expects it — half up, on the decimal value.

ZATCA's XML Implementation Standard (s.10) rounds half up on the third decimal.
Python's built-in ``round`` rounds half to even, and on the binary value of a
float, so ``round(2.675, 2)`` is 2.67. Every amount in this package goes through
the helpers below instead, which round the *shortest decimal representation* of
a number half away from zero: 2.675 becomes 2.68, as a person would expect and
as the Authority's validator computes.
"""

from __future__ import annotations

import math
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

__all__ = ["round_half_up", "line_net", "fmt"]


def round_half_up(value: float, places: int = 0) -> float:
    """Round ``value`` half away from zero to ``places`` decimals."""
    if value != value or value in (math.inf, -math.inf):  # NaN / infinity pass through
        return value
    quantum = Decimal(1).scaleb(-places)
    return float(Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP))


def line_net(quantity: float, price: float) -> float:
    """A line's net, ``quantity × price``, half up to the halala — in decimal arithmetic.

    Computed in decimals rather than binary floating point, because two
    computations of the "same" net must never disagree: in binary,
    1.5 × 4.55 is 6.8249999999999997 and rounds to 6.82, while the true product
    6.825 rounds half up to 6.83 — which is also what ZATCA's validator computes
    for BR-KSA-EN16931-11.
    """
    product = Decimal(f"{quantity:.6f}") * Decimal(f"{price:.10f}")
    return float((product + Decimal("0.005")).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def fmt(value: float, places: int = 2) -> str:
    """An amount as written into the XML: half up, fixed decimals, no thousands separator."""
    quantum = Decimal(1).scaleb(-places)
    text = str(Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP))
    return "0" if places == 0 and text in ("-0", "0") else text
