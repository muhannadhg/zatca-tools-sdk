"""Reading a document's lines: validation, VAT-inclusive prices, line discounts, halala fitting.

The arithmetic here is the arithmetic the ZATCA Tools platform signs invoices
with in production, ported line for line. Two rules shape most of it:

* ``quantity × unit price = line net`` to the halala (BR-KSA-EN16931-11), so a
  line discount is applied to the *unit* price and the net re-derived from it;
* rounding happens once, on the final figure (XML Implementation Standard, s.10),
  so a line priced *with* VAT keeps its exact worth before rounding
  (``exact_net``) and the tax is computed from that.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Optional, Sequence

from .._rounding import line_net, round_half_up
from . import categories

#: How far, in halalas, fitting a basket to its base may move a unit price from its exact worth.
FIT_TOLERANCE = 2.0

DESCRIPTION_MAX = 5000

_TRIM = " \t\n\r\0\x0b"


def _is_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, str):
        try:
            return math.isfinite(float(value.strip()))
        except ValueError:
            return False
    return False


def _num(value: Any, default: float = 0.0) -> float:
    return float(value) if _is_numeric(value) else default


# --------------------------------------------------------------------------- validation


def problems(raw: Any, default_category: Optional[str] = None, with_treatment: bool = True, prefix: str = "lines") -> list[dict[str, str]]:
    """Every problem in the lines (named ``prefix`` in the input), each naming its field. Empty when the lines are usable.

    A line that cannot be read refuses the whole document instead of vanishing
    from a signed invoice: the caller would believe it billed both.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) == 0:
        return [{"field": prefix, "message": f"{prefix} needs at least one entry with name, quantity and unit_price."}]

    found: list[dict[str, str]] = []
    for i, line in enumerate(raw):
        at = f"{prefix}[{i}]"
        if not isinstance(line, Mapping):
            found.append({"field": at, "message": f"{at} must be an object with name, quantity and unit_price."})
            continue
        name = line.get("name")
        if not isinstance(name, (str, int, float)) or isinstance(name, bool) or str(name).strip(_TRIM) == "":
            found.append({"field": f"{at}.name", "message": f"{at}.name is required."})
        quantity = line.get("quantity")
        price = line.get("unit_price")
        if not (_is_numeric(quantity) and float(quantity) > 0):
            found.append({"field": f"{at}.quantity", "message": f"{at}.quantity must be a number greater than zero."})
        if not (_is_numeric(price) and float(price) > 0):
            found.append({"field": f"{at}.unit_price", "message": f"{at}.unit_price must be a number greater than zero."})
        if _is_numeric(quantity) and _is_numeric(price) and float(quantity) > 0 and float(price) > 0:
            if round_half_up(float(quantity) * float(price), 2) <= 0:
                found.append({"field": at, "message": f"{at} comes to 0.00 SAR; quantity × unit_price must be at least 0.01."})
        discount = line.get("discount")
        if discount not in (None, "") and (not _is_numeric(discount) or float(discount) < 0):
            found.append({"field": f"{at}.discount", "message": f"{at}.discount must be a number, zero or more."})
        if line.get("discount_type") is not None and line.get("discount_type") not in ("amount", "percent"):
            found.append({"field": f"{at}.discount_type", "message": f"{at}.discount_type must be 'amount' or 'percent'."})
        if with_treatment:
            problem = _treatment_problem(line, at, default_category)
            if problem:
                found.append(problem)
    return found


def _spelled(value: Any) -> Optional[str]:
    if not isinstance(value, str) or value.strip() == "":
        return None
    return value.strip().upper()


def _treatment_problem(line: Mapping[str, Any], at: str, default_category: Optional[str]) -> Optional[dict[str, str]]:
    category = _spelled(line.get("tax_category"))
    reason = _spelled(line.get("tax_reason_code"))
    if category is not None and not categories.is_valid(category):
        return {"field": f"{at}.tax_category", "message": f"{at}.tax_category must be S (standard 15%), Z (zero-rated), E (exempt) or O (out of scope)."}
    if reason is not None and reason not in categories.REASONS:
        return {"field": f"{at}.tax_reason_code", "message": f"{at}.tax_reason_code '{reason}' is not a ZATCA exemption code."}
    effective = category or (default_category if categories.is_valid(default_category) else categories.STANDARD)
    if categories.needs_reason(effective):
        if reason is not None and categories.REASONS[reason]["category"] != effective:
            return {"field": f"{at}.tax_reason_code", "message": f"{at}.tax_reason_code {reason} belongs to category {categories.REASONS[reason]['category']}, not {effective}."}
        return None
    if reason is not None:
        return {"field": f"{at}.tax_reason_code", "message": f"{at}.tax_reason_code {reason} is for tax_category {categories.REASONS[reason]['category']}; a standard-rated line carries no reason."}
    return None


def canonical(lines: Iterable[Any]) -> list[Any]:
    """``tax_category`` and ``tax_reason_code`` in their canonical spelling ("z" is Z)."""
    out = []
    for line in lines:
        if isinstance(line, Mapping):
            line = dict(line)
            for field in ("tax_category", "tax_reason_code"):
                if isinstance(line.get(field), str):
                    line[field] = line[field].strip().upper()
        out.append(line)
    return out


def with_reasons(lines: list[Any]) -> list[Any]:
    """A zero-rated, exempt or out-of-scope line that named no reason borrows one, when it can be known.

    Only the reason the other lines of the same category name on this very
    document, when they all name the same one. When they name two, picking either
    would be a guess, so the line stays without one (ZATCA warns, it does not reject).
    """
    named: dict[str, dict[str, bool]] = {}
    for line in lines:
        if not isinstance(line, Mapping):
            continue
        category, reason = line.get("tax_category"), line.get("tax_reason_code")
        if isinstance(category, str) and isinstance(reason, str) and categories.reason_code_for(category, reason) == reason:
            named.setdefault(category, {})[reason] = True
    out = []
    for line in lines:
        if isinstance(line, Mapping):
            category = line.get("tax_category")
            if isinstance(category, str) and categories.needs_reason(category) and not line.get("tax_reason_code"):
                options = named.get(category, {})
                if len(options) == 1:
                    line = {**line, "tax_reason_code": next(iter(options))}
        out.append(line)
    return out


# --------------------------------------------------------------------------- normalising


def normalize(
    raw: Sequence[Any],
    prices_include_vat: bool = False,
    default_category: Optional[str] = None,
    default_reason: Optional[str] = None,
    derived_net: bool = False,
) -> list[dict[str, Any]]:
    """Lines as the rest of the package reads them; incomplete rows are dropped (validate first)."""
    lines: list[dict[str, Any]] = []
    for line in raw:
        if not isinstance(line, Mapping):
            continue
        category = categories.of(line, default_category)
        rate = categories.rate(category)
        name = str(line.get("name") if line.get("name") is not None else "").rstrip(_TRIM)
        quantity = _num(line.get("quantity", 0))
        price = _num(line.get("unit_price", line.get("price", 0)))

        # The LINE's own rate: a zero-rated line's typed price is already its net.
        exact_price = None
        if prices_include_vat:
            exact_price = price / (1 + rate / 100)
        elif derived_net:
            exact_price = price

        if exact_price is not None:
            price = signed_unit_price(exact_price, quantity)
        elif _is_numeric(line.get("exact_net")):
            price = signed_unit_price(price, quantity)

        if name.strip(_TRIM) == "" or quantity <= 0 or price <= 0:
            continue

        kind = "percent" if line.get("discount_type") == "percent" else "amount"
        discount = max(0.0, _num(line.get("discount", 0)))
        exact_discount = None
        if kind == "amount" and (prices_include_vat or derived_net) and discount > 0:
            exact_discount = discount / (1 + rate / 100) if prices_include_vat else discount
            discount = round_half_up(exact_discount, 2)

        charged = net_price(price, quantity, kind, discount)
        net = line_net(quantity, charged)

        chosen_reason = line.get("tax_reason_code")
        if chosen_reason is None:
            chosen_reason = default_reason if category == default_category else None

        out: dict[str, Any] = {
            "name": name,
            "quantity": quantity,
            "price": price,
            "tax_category": category,
            "tax_reason_code": categories.reason_code_for(category, chosen_reason),
            "lineExtensionAmount": net,
        }

        # What the line is worth before ANY rounding — the tax is computed from it.
        if exact_price is not None:
            exact_unit = exact_price
            if discount > 0 and quantity > 0:
                per_unit = (
                    exact_price * min(discount, 100.0) / 100
                    if kind == "percent"
                    else (exact_discount if exact_discount is not None else discount) / quantity
                )
                exact_unit = max(0.0, exact_price - per_unit)
            out["exact_net"] = quantity * exact_unit
        elif _is_numeric(line.get("exact_net")):
            out["exact_net"] = float(line["exact_net"])

        if line.get("unit"):
            out["unitCode"] = str(line["unit"])

        description = str(line.get("description") or "").rstrip(_TRIM)
        if description.strip(_TRIM) != "":
            out["description"] = description[:DESCRIPTION_MAX]

        if discount > 0:
            out["discount"] = min(discount, 100.0) if kind == "percent" else discount
            out["discount_type"] = kind
            out["net_price"] = charged
            out["line_discount"] = round_half_up(line_net(quantity, price) - net, 2)

        lines.append(out)

    return balanced(lines)


def document_rate(lines: Sequence[Mapping[str, Any]], fallback_category: str) -> float:
    """The rate a document-level figure (its discount) is converted at — blended when the basket mixes rates."""
    found = list(dict.fromkeys(categories.of(line, fallback_category) for line in lines))
    if len(found) <= 1:
        return categories.rate(found[0] if found else fallback_category)
    net = 0.0
    gross = 0.0
    for line in lines:
        worth = float(line.get("exact_net", line.get("lineExtensionAmount", 0)) or 0)
        net += worth
        gross += worth * (1 + categories.rate(categories.of(line, fallback_category)) / 100)
    return (gross / net - 1) * 100 if net > 0 else categories.rate(fallback_category)


def derived_net(raw: Sequence[Any], default_category: Optional[str] = None, discount: float = 0.0) -> bool:
    """Whether net figures are shelf prices the sender divided by the rate (28.00 sent as 24.3478260869…).

    Such figures are settled at the worth that was sent, rounded once — exactly
    as if the shelf prices had been sent with ``prices_include_vat``.
    """

    def finer(amount: float) -> bool:
        return abs(amount * 100 - round_half_up(amount * 100)) > 0.000001

    def divided(amount: float, rate: float) -> bool:
        shelf = amount * (1 + rate / 100)
        return rate > 0 and abs(shelf - round_half_up(shelf, 2)) < 0.000001

    state = {"derived": False}

    def judge(amount: float, rate: float) -> bool:
        if amount <= 0 or not finer(amount):
            return True
        if not divided(amount, rate):
            return False
        state["derived"] = True
        return True

    for line in raw:
        if not isinstance(line, Mapping) or str(line.get("name") or "").strip(_TRIM) == "" or _num(line.get("quantity", 0)) <= 0:
            continue
        rate = categories.rate(categories.of(line, default_category))
        if not judge(_num(line.get("unit_price", line.get("price", 0))), rate):
            return False
        if line.get("discount_type") != "percent" and not judge(_num(line.get("discount", 0)), rate):
            return False

    if not judge(discount, categories.rate(categories.of({}, default_category))):
        return False
    return state["derived"]


def signed_unit_price(exact_unit: float, quantity: float) -> float:
    """The two-decimal unit price a line is signed at: half up (whether a basket adds up is fit_to_base's job)."""
    return round_half_up(exact_unit, 2)


def balanced(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lines whose signed nets add up, per treatment, to the base they are worth together."""
    if not lines or any("exact_net" not in line for line in lines):
        return lines
    prices = [float(line.get("net_price", line["price"])) for line in lines]
    bumps = fit_to_base(
        prices,
        [float(line["quantity"]) for line in lines],
        [float(line["exact_net"]) for line in lines],
        [categories.of(line) for line in lines],
    )
    for i, line in enumerate(lines):
        if bumps[i] == 0:
            continue
        raised = round_half_up(prices[i] + bumps[i] / 100, 2)
        line["lineExtensionAmount"] = line_net(float(line["quantity"]), raised)
        if "net_price" in line:
            line["net_price"] = raised
            line["line_discount"] = round_half_up(line_net(float(line["quantity"]), float(line["price"])) - line["lineExtensionAmount"], 2)
        else:
            line["price"] = raised
    return lines


def fit_to_base(
    prices: Sequence[float],
    quantities: Sequence[float],
    exact: Sequence[float],
    line_categories: Sequence[str],
    caps: Optional[Sequence[float]] = None,
) -> list[int]:
    """Halalas of unit price each line moves by so that, per treatment, the signed nets meet the exact base.

    Short of the base, a line is raised a halala at a time (a negative allowance
    cannot be declared); over it, the line furthest over its worth is lowered.
    No unit price moves more than ``FIT_TOLERANCE`` halalas from its worth. What
    cannot be fitted stays over and is declared as an allowance.
    """
    bumps = [0] * len(prices)

    def net(i: int, extra: int = 0) -> float:
        return line_net(quantities[i], round_half_up(prices[i] + (bumps[i] + extra) / 100, 2))

    def off(i: int, extra: int = 0) -> float:
        return (prices[i] + (bumps[i] + extra) / 100 - exact[i] / quantities[i]) * 100

    def signed(indexes: list[int]) -> float:
        return round_half_up(sum(net(i) for i in indexes), 2)

    groups: dict[str, list[int]] = {}
    for i, category in enumerate(line_categories):
        if quantities[i] > 0:
            groups.setdefault(category, []).append(i)

    for indexes in groups.values():
        target = round_half_up(sum(exact[i] for i in indexes), 2)

        for _ in range(10000):
            short = round_half_up(target - signed(indexes), 2)
            if short < 0.005:
                break
            pick = None
            best = None
            for i in indexes:
                if caps is not None and net(i, 1) > caps[i] + 0.00001:
                    continue
                step = round_half_up(net(i, 1) - net(i), 2)
                fits = step <= short + 0.00001
                within = off(i, 1) <= FIT_TOLERANCE + 0.000001
                key = (0 if within else 1, 0 if fits else 1, 0.0 if fits else step, off(i))
                if best is None or key < best:
                    best = key
                    pick = i
            if pick is None:
                break
            bumps[pick] += 1

        for _ in range(10000):
            over = round_half_up(signed(indexes) - target, 2)
            if over < 0.005:
                break
            pick = None
            gap = -math.inf
            for i in indexes:
                if (
                    prices[i] + (bumps[i] - 1) / 100 < 0.01
                    or round_half_up(net(i) - net(i, -1), 2) > over + 0.00001
                    or off(i, -1) < -FIT_TOLERANCE - 0.000001
                ):
                    continue
                above = off(i)
                if above >= gap - 0.0000001:  # a tie goes to the later line
                    gap = above
                    pick = i
            if pick is None:
                break
            bumps[pick] -= 1

    return bumps


def net_discount(amount: float, rate: float, prices_include_vat: bool) -> float:
    """A document-level discount as a net amount — typed with VAT it is divided by the rate first."""
    if not prices_include_vat or amount <= 0:
        return round_half_up(amount, 2)
    return round_half_up(amount / (1 + rate / 100), 2)


def exact_net_discount(amount: float, rate: float, prices_include_vat: bool, carried: Any = None) -> Optional[float]:
    """The same document discount before rounding to the halala — what the tax is worked out from — or None."""
    if amount <= 0:
        return None
    if prices_include_vat:
        return amount / (1 + rate / 100)
    if _is_numeric(carried) and abs(round_half_up(float(carried), 2) - round_half_up(amount, 2)) < 0.001:
        return float(carried)
    return None


def net_price(price: float, quantity: float, kind: str, discount: float) -> float:
    """The unit price actually charged after the line's own discount."""
    if discount <= 0 or quantity <= 0:
        return price
    per_unit = price * min(discount, 100.0) / 100 if kind == "percent" else discount / quantity
    return max(0.0, round_half_up(price - per_unit, 2))


def emptied_line(lines: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """The name of the first line a discount has taken to zero, or None."""
    for line in lines:
        if float(line["lineExtensionAmount"]) <= 0:
            return str(line["name"])
    return None
