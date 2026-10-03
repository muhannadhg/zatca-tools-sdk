"""Spreading a document-level discount across the lines that earned it.

UBL lets a discount live either on the document (lines stay gross) or in the
lines (nothing declared on the document) — never both, because BR-CO-13 derives
the taxable amount as ``Σ line net − document allowances``. It lives in the lines
here, so every number a reader adds up on the printed invoice is a number that
was signed. Shares are exact by construction: proportional shares in halalas,
leftover halalas to the largest remainders, until they sum to the discount.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence

from .._rounding import line_net, round_half_up
from . import categories
from .lines import fit_to_base, signed_unit_price


def _all_exact(lines: Sequence[Mapping[str, Any]]) -> bool:
    return len(lines) > 0 and all("exact_net" in line for line in lines)


def settle(lines: Sequence[Mapping[str, Any]], discount: float, exact_discount: Optional[float] = None) -> list[dict[str, float]]:
    """Each line as it is signed: its net, its unit price, its VAT, the discount it absorbed, its exact worth."""
    exact_mode = exact_discount is not None and _all_exact(lines)
    pieces = []
    for line, piece in zip(lines, spread(lines, discount, exact_discount)):
        net = piece["net"]
        quantity = float(line.get("quantity", 0) or 0)
        price = float(line.get("net_price", line.get("price", 0)) or 0)
        # A discounted line's unit price comes down with it (BT-146 is the price charged),
        # rounded UP to the halala so the absorbed total never exceeds the discount.
        if piece["share"] > 0 and quantity > 0:
            price = (
                math.ceil(round_half_up(net / quantity * 100, 6)) / 100
                if exact_mode
                else math.ceil(net / quantity * 100) / 100
            )
            net = line_net(quantity, price)
        pieces.append({
            "net": net,
            "price": price,
            "tax": round_half_up(net * categories.rate(categories.of(line)) / 100, 2),
            "share": piece["share"],
            "exact": piece["exact"],
        })
    return _apportion_tax(lines, pieces) if exact_mode else pieces


def _apportion_tax(lines: Sequence[Mapping[str, Any]], pieces: list[dict[str, float]]) -> list[dict[str, float]]:
    """Each line's VAT as its share of the document's VAT, which is rounded once (s.10)."""
    groups: dict[str, list[int]] = {}
    for i, line in enumerate(lines):
        groups.setdefault(categories.of(line), []).append(i)

    for category, indexes in groups.items():
        rate = categories.rate(category)
        exact = {i: max(0.0, float(pieces[i]["exact"])) for i in indexes}
        exact_sum = sum(exact.values())
        taxable = int(round_half_up(round_half_up(exact_sum, 2) * 100))
        total = int(round_half_up(round_half_up(exact_sum * rate / 100, 2) * 100))
        nets = {i: int(round_half_up(float(pieces[i]["net"]) * 100)) for i in indexes}

        shares = None
        if sum(nets.values()) == taxable:
            gross = _shares(taxable + total, {i: amount * (100 + rate) for i, amount in exact.items()})
            candidate = {i: gross[i] - nets[i] for i in indexes}
            if min(candidate.values()) >= 0:
                shares = candidate
        if shares is None:
            shares = _shares(total, {i: amount * rate for i, amount in exact.items()})

        for i in indexes:
            pieces[i]["tax"] = round_half_up(shares[i] / 100, 2)
    return pieces


def _shares(total: int, ideals: Mapping[Any, float]) -> dict[Any, int]:
    """Whole halalas shared out in proportion: floors first, leftovers to the largest remainders."""
    shares: dict[Any, int] = {}
    remainders: dict[Any, float] = {}
    for key, ideal in ideals.items():
        shares[key] = int(math.floor(round_half_up(max(0.0, ideal), 6)))
        remainders[key] = ideal - shares[key]

    left = total - sum(shares.values())
    order = sorted(remainders, key=lambda k: remainders[k], reverse=left > 0)

    passes = 0
    while left != 0 and order and passes < 1000:
        for key in order:
            if left == 0:
                break
            if left > 0:
                shares[key] += 1
                left -= 1
            elif shares[key] > 0:
                shares[key] -= 1
                left += 1
        passes += 1
    return shares


def spread(lines: Sequence[Mapping[str, Any]], discount: float, exact_discount: Optional[float] = None) -> list[dict[str, float]]:
    """The discount each line absorbs: ``net`` (signed), ``share`` (absorbed), ``exact`` (worth before rounding)."""
    gross = [round_half_up(float(line.get("lineExtensionAmount", 0) or 0), 2) for line in lines]
    total = round_half_up(sum(gross), 2)
    discount = round_half_up(discount, 2)
    exact_mode = exact_discount is not None and _all_exact(lines)

    exact_gross = [float(line.get("exact_net", line.get("lineExtensionAmount", 0)) or 0) for line in lines]
    exact_total = sum(exact_gross)
    if exact_discount is None:
        exact_discount = discount

    def exact_nets(taken: float) -> list[float]:
        mixed = _mixed_exact_nets(lines, exact_gross, taken)
        if mixed is not None:
            return mixed
        return [amount - taken * amount / exact_total if exact_total > 0 else amount for amount in exact_gross]

    def none() -> list[dict[str, float]]:
        exact = exact_nets(0.0)
        return [{"net": amount, "share": 0.0, "exact": exact[i]} for i, amount in enumerate(gross)]

    if discount <= 0 or total <= 0:
        return none()
    # A discount that swallows the whole invoice cannot be represented: leave it to validation.
    if discount >= total:
        return none()
    if exact_mode:
        return _spread_exact(lines, gross, exact_nets(exact_discount))

    target = int(round_half_up(discount * 100))
    gross_h = [int(round_half_up(amount * 100)) for amount in gross]
    total_h = sum(gross_h)

    shares: list[int] = []
    remainders: dict[int, float] = {}
    for i, amount in enumerate(gross_h):
        ideal = amount * target / total_h
        shares.append(int(math.floor(ideal)))
        remainders[i] = ideal - shares[i]

    leftover = target - sum(shares)
    order = sorted(remainders, key=lambda k: remainders[k], reverse=True)
    passes = 0
    while leftover > 0 and passes <= len(order):
        for i in order:
            if leftover <= 0:
                break
            if shares[i] < gross_h[i]:  # never take more from a line than it is worth
                shares[i] += 1
                leftover -= 1
        passes += 1

    exact = exact_nets(exact_discount)
    return [
        {"net": round_half_up((gross_h[i] - shares[i]) / 100, 2), "share": round_half_up(shares[i] / 100, 2), "exact": exact[i]}
        for i in range(len(gross))
    ]


def _mixed_exact_nets(lines: Sequence[Mapping[str, Any]], exact_gross: list[float], taken: float) -> Optional[list[float]]:
    """A discount typed with VAT across several treatments: split in halalas between the subtotals first."""

    def group_of(line: Mapping[str, Any]) -> str:
        category = categories.of(line)
        return f"{category}|{categories.reason_code_for(category, line.get('tax_reason_code')) or ''}"

    groups: dict[str, list[int]] = {}
    for i, line in enumerate(lines):
        groups.setdefault(group_of(line), []).append(i)
    if len(groups) < 2 or taken <= 0:
        return None

    net: dict[str, float] = {}
    with_vat: dict[str, float] = {}
    rates: dict[str, float] = {}
    for key, indexes in groups.items():
        rates[key] = categories.rate(categories.of(lines[indexes[0]]))
        net[key] = sum(exact_gross[i] for i in indexes)
        with_vat[key] = net[key] * (1 + rates[key] / 100)

    net_total = sum(net.values())
    if net_total <= 0:
        return None

    vat_total = sum(with_vat.values())
    typed = int(round_half_up(taken * vat_total / net_total * 100))
    ceilings = {key: int(math.floor(round_half_up(amount * 100, 6))) for key, amount in with_vat.items()}
    shares = _shares(typed, {key: (amount * typed / vat_total if vat_total > 0 else 0.0) for key, amount in with_vat.items()})

    exact = list(exact_gross)
    for key, indexes in groups.items():
        share = min(shares[key], ceilings[key]) / 100
        off = share / (1 + rates[key] / 100)
        for i in indexes:
            exact[i] = exact_gross[i] - off * exact_gross[i] / net[key] if net[key] > 0 else exact_gross[i]
    return exact


def _spread_exact(lines: Sequence[Mapping[str, Any]], gross: list[float], exact: list[float]) -> list[dict[str, float]]:
    """Each line signed at its own exact worth after the discount, rounded once."""
    quantities = [float(line.get("quantity", 0) or 0) for line in lines]
    prices: list[float] = []
    for i in range(len(lines)):
        q = quantities[i]
        price = signed_unit_price(max(0.0, exact[i]) / q, q) if q > 0 else 0.0
        if line_net(q, price) > gross[i]:  # never above the line as it stood before the discount
            price = math.floor(round_half_up(gross[i] / q * 100, 6)) / 100 if q > 0 else 0.0
        prices.append(price)

    bumps = fit_to_base(prices, quantities, exact, [categories.of(line) for line in lines], gross)

    out = []
    for i in range(len(gross)):
        net = min(line_net(quantities[i], round_half_up(prices[i] + bumps[i] / 100, 2)), gross[i])
        out.append({"net": net, "share": round_half_up(gross[i] - net, 2), "exact": exact[i]})
    return out
