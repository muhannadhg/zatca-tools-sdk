"""Turning a plain dict into a signed invoice — every check made before anything is sent."""

from __future__ import annotations

import base64
import binascii
import re
import uuid as uuid_lib
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from lxml import etree

from .._rounding import round_half_up
from ..environment import INITIAL_PIH
from ..errors import SigningError, ValidationError, XmlError
from ..models import Seller, is_valid_vat
from ..results import Chain, Invoice, Message
from ..signing import signer
from ..signing.certificate import SigningIdentity
from ..tax import categories, id_schemes
from ..tax import lines as line_items
from ..tax.totals import DocumentTotals
from . import payload as payloads
from . import ubl

#: Saudi Arabia keeps UTC+3 all year.
RIYADH = timezone(timedelta(hours=3), "Asia/Riyadh")

#: KSA VAT Implementing Regulations art. 53: a supply of SAR 1,000 or more to a VAT-registered buyer needs a tax invoice.
B2B_THRESHOLD = 1000.0

#: The fields an invoice dict may carry. Anything else is refused, so a misspelt
#: field cannot be silently left off a signed invoice.
FIELDS = {
    "type", "kind", "number", "date", "time", "items", "buyer", "discount", "prices_include_vat",
    "original_invoice", "reason", "delivery_date", "uuid", "icv", "pih", "default_tax_category",
}
ITEM_FIELDS = {"name", "quantity", "unit_price", "discount", "discount_type", "tax_category", "tax_reason_code", "unit", "description"}

#: Earlier spellings, and the usual guesses, with the field they mean.
RENAMED = {
    "form": "type", "lines": "items", "issue_date": "date", "issue_time": "time", "billing_reference": "original_invoice",
    "seller": "Zatca(seller=...)", "invoice_type": "type", "price": "unit_price", "qty": "quantity", "notes": "reason",
}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}$")
#: Characters XML 1.0 cannot carry: an invoice holding one could not be signed or read.
_NOT_XML = re.compile("[^\u0009\u000a\u000d -퟿-�\U00010000-\U0010ffff]")


def create(
    data: Mapping[str, Any],
    seller: Seller,
    identity: SigningIdentity,
    *,
    chain: Chain,
    allowance_reason: str = "Discount",
    signing_time: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> Invoice:
    if not isinstance(data, Mapping):
        raise ValidationError("An invoice is a dict.", [{"field": "invoice", "message": "Pass the invoice as a dict: type, number and items at least."}])

    seller.validate()
    moment = (now or datetime.now(RIYADH)).astimezone(RIYADH)
    errors: list[dict[str, Any]] = []
    warnings: list[Message] = []

    def fail(field: str, message: str, rule: Optional[str] = None) -> None:
        errors.append({"field": field, "message": message, "rule": rule})

    for key in data:
        if key not in FIELDS:
            hint = f" Did you mean {RENAMED[key]}?" if key in RENAMED else ""
            fail(str(key), f"'{key}' is not an invoice field.{hint}")
    for path, char in _unwritable(data, ""):
        fail(path, f"{path} contains a character XML cannot carry (U+{ord(char):04X}); remove it.")

    invoice_type = data.get("type")
    if invoice_type not in ("simplified", "standard"):
        fail("type", "type must be 'simplified' (B2C — a receipt, reported) or 'standard' (B2B tax invoice, cleared).")
    kind = data.get("kind", "invoice")
    if kind not in ("invoice", "credit", "debit"):
        fail("kind", "kind must be 'invoice', 'credit' (credit note) or 'debit' (debit note).")

    number = str(data.get("number") or "").strip()
    if not number:
        fail("number", "number is required: your own invoice number, e.g. INV-1001.")
    elif len(number) > 127:
        fail("number", "number is at most 127 characters.")

    if "icv" in data:
        # Advanced: the caller keeps the chain itself.
        icv = data.get("icv")
        if isinstance(icv, bool) or not isinstance(icv, int) or icv < 1:
            fail("icv", "icv is the unit's invoice counter: an integer, 1 for its first invoice, +1 for each one after.")
        pih = data.get("pih") or (INITIAL_PIH if icv == 1 else None)
        if pih is None:
            fail("pih", "pih is required with icv: the hash of the unit's previous invoice.")
    else:
        icv, pih = chain.icv + 1, data.get("pih") or chain.hash
    if pih is not None and not _is_base64(pih):
        fail("pih", "pih is the hash of the previous invoice (its Invoice.hash), base64.")

    invoice_uuid = data.get("uuid") or str(uuid_lib.uuid4())
    try:
        invoice_uuid = str(uuid_lib.UUID(str(invoice_uuid)))
    except ValueError:
        fail("uuid", "uuid must be a UUID, or left out to have one generated.")

    issue_date = data.get("date") or moment.date().isoformat()
    issue_time = data.get("time") or moment.strftime("%H:%M:%S")
    if not _DATE.match(str(issue_date)) or not _is_date(str(issue_date)):
        fail("date", "date is YYYY-MM-DD.")
    elif str(issue_date) > moment.date().isoformat():
        fail("date", f"date cannot be after today in Riyadh ({moment.date().isoformat()}); ZATCA refuses a future-dated invoice.", "BR-KSA-04")
    if not _TIME.match(str(issue_time)):
        fail("time", "time is HH:MM:SS, Riyadh time.")

    delivery_date = data.get("delivery_date")
    if delivery_date is not None and (not _DATE.match(str(delivery_date)) or not _is_date(str(delivery_date))):
        fail("delivery_date", "delivery_date is YYYY-MM-DD.")

    default_category = str(data.get("default_tax_category") or categories.STANDARD).upper()
    if not categories.is_valid(default_category):
        fail("default_tax_category", "default_tax_category must be S, Z, E or O.")

    prices_include_vat = bool(data.get("prices_include_vat", False))
    raw_discount = data.get("discount", 0) or 0
    if isinstance(raw_discount, bool) or not isinstance(raw_discount, (int, float)) or raw_discount < 0:
        fail("discount", "discount is an amount, zero or more.")
        raw_discount = 0

    raw_items = data.get("items")
    for i, item in enumerate(raw_items if isinstance(raw_items, (list, tuple)) else []):
        for key in item if isinstance(item, Mapping) else []:
            if key not in ITEM_FIELDS:
                hint = f" Did you mean {RENAMED[key]}?" if key in RENAMED else ""
                fail(f"items[{i}].{key}", f"'{key}' is not an item field.{hint}")
    errors += line_items.problems(raw_items, default_category, prefix="items")

    original = str(data.get("original_invoice") or "").strip() or None
    reason = str(data.get("reason") or "").strip() or None
    if kind in ("credit", "debit"):
        if not original:
            fail("original_invoice", "A credit or debit note names the invoice it adjusts: original_invoice.", "BR-KSA-56")
        if not reason:
            fail("reason", "A credit or debit note states why it was issued: reason.", "BR-KSA-17")

    buyer_input = data.get("buyer")
    buyer = _check_buyer(buyer_input, invoice_type, fail, warnings) if buyer_input else None
    if invoice_type == "standard" and not buyer_input:
        fail("buyer", "A standard (B2B) invoice names its buyer: buyer.name, buyer.vat_number or buyer.id, and buyer.address.")

    if errors:
        raise ValidationError(_summary(errors), errors)

    # -- the arithmetic ----------------------------------------------------------------
    raw = line_items.with_reasons(line_items.canonical(raw_items))
    discount = float(raw_discount)
    derived = (not prices_include_vat) and line_items.derived_net(raw, default_category, discount)
    lines = line_items.normalize(raw, prices_include_vat, default_category, None, derived)

    emptied = line_items.emptied_line(lines)
    if emptied is not None:
        raise ValidationError("An item discount takes an item to zero.", [{"field": "items", "message": f"The discount on '{emptied}' equals or exceeds its amount."}])

    rate = line_items.document_rate(lines, default_category)
    net = line_items.net_discount(discount, rate, prices_include_vat)
    totals = DocumentTotals(lines, net, line_items.exact_net_discount(discount, rate, prices_include_vat, discount if derived else None))
    if round_half_up(totals.line_total - totals.discount, 2) <= 0:
        raise ValidationError("The discount takes the whole invoice to zero.", [{"field": "discount", "message": "discount leaves nothing to tax; no tax invoice can be issued."}])

    if categories.requires_national_id(lines) and not (buyer and buyer.get("id_scheme") == "NAT" and buyer.get("id")):
        raise ValidationError(
            "Education or healthcare zero-rated for a citizen needs the buyer's national ID.",
            [{"field": "buyer.id", "message": "Items with VATEX-SA-EDU or VATEX-SA-HEA need buyer.id with id_scheme NAT.", "rule": "BR-KSA-49"}],
        )

    for i, line in enumerate(lines):
        if line["tax_category"] in ("Z", "E") and not line.get("tax_reason_code"):
            name = categories.CATEGORIES[line["tax_category"]]["name"].lower()
            warnings.append(Message("warning", "missing_exemption_reason", f"items[{i}] is {name} without tax_reason_code; ZATCA accepts it with a warning.", source="local", rule="BR-KSA-69"))

    # The sandbox signs with ZATCA's shared sample certificate, which matches no
    # one's key; SigningIdentity.load only lets that through in the sandbox.

    if invoice_type == "simplified" and kind == "invoice" and totals.grand >= B2B_THRESHOLD and buyer and is_valid_vat(buyer.get("vat_number")):
        warnings.append(Message("warning", "standard_invoice_required", "A supply of SAR 1,000 or more to a VAT-registered buyer needs a standard tax invoice (VAT Implementing Regulations, art. 53).", source="local"))

    payload = payloads.build(
        seller,
        invoice_type,
        kind,
        number=number,
        uuid=invoice_uuid,
        icv=icv,
        pih=str(pih),
        issue_date=str(issue_date),
        issue_time=str(issue_time),
        lines=lines,
        discount=totals.discount,
        discount_exact=totals.exact_discount,
        buyer=payloads.buyer_party(buyer),
        billing_reference=original,
        note_reason=reason,
        delivery_date=str(delivery_date) if delivery_date else None,
    )
    for charge in payload.get("allowanceCharges", []):
        charge["reason"] = allowance_reason

    try:
        signed = signer.sign(ubl.render(payload), payload, identity, signing_time)
    except SigningError:
        raise
    except (etree.LxmlError, UnicodeError, ValueError) as exc:
        raise XmlError(f"The invoice could not be written as well-formed XML: {exc}") from exc
    breakdown = payload.pop("_breakdown")

    summary = {
        "line_total": totals.line_total,
        "discount": totals.discount,
        "taxable": payload["legalMonetaryTotal"]["taxExclusiveAmount"],
        "tax": payload["taxTotal"]["taxAmount"],
        "total": payload["legalMonetaryTotal"]["payableAmount"],
        "allowance": payload["legalMonetaryTotal"]["allowanceTotalAmount"],
        "subtotals": breakdown.summary(),
    }
    signed_items = [
        {
            "name": line["item"]["name"],
            "quantity": line["quantity"],
            "unit_price": line["price"]["amount"],
            "net": line["lineExtensionAmount"],
            "tax": line["taxTotal"]["taxAmount"],
            "total": line["taxTotal"]["roundingAmount"],
            "tax_category": line["tax_category"],
            "tax_rate": line["item"]["classifiedTaxCategory"][0]["percent"],
            "tax_reason_code": line.get("tax_reason_code"),
            "unit": line["unitCode"],
            "description": source.get("description"),
            # The price agreed before an item's own discount, and that discount — for printing.
            "agreed_price": source.get("price"),
            "item_discount": source.get("line_discount") or 0.0,
            "discount_type": source.get("discount_type") or "amount",
            "discount_value": source.get("discount"),
        }
        for line, source in zip(payload["invoiceLines"], lines)
    ]

    return Invoice(
        type=invoice_type,
        kind=kind,
        number=number,
        uuid=invoice_uuid,
        icv=icv,
        pih=str(pih),
        date=str(issue_date),
        time=str(issue_time),
        hash=signed.hash,
        qr=signed.qr,
        xml=signed.xml,
        totals=summary,
        items=signed_items,
        warnings=warnings,
        signing_time=signed.signing_time,
    )


def _check_buyer(buyer: Any, invoice_type: Any, fail: Any, warnings: list[Message]) -> Optional[dict[str, Any]]:
    if not isinstance(buyer, Mapping):
        fail("buyer", "buyer is an object.")
        return None
    out = dict(buyer)
    name = str(buyer.get("name") or "").strip()
    if not name:
        fail("buyer.name", "buyer.name is required.")
    vat = str(buyer.get("vat_number") or "").strip() or None
    if vat is not None and not is_valid_vat(vat):
        fail("buyer.vat_number", "buyer.vat_number must be 15 digits, starting and ending with 3.")
    identifier = id_schemes.clean(buyer.get("id")) or None
    scheme = str(buyer.get("id_scheme") or "CRN").strip().upper()
    if identifier is not None:
        if scheme not in id_schemes.BUYER:
            fail("buyer.id_scheme", f"buyer.id_scheme must be one of {', '.join(id_schemes.BUYER)}.")
        elif not id_schemes.looks_valid(identifier, scheme):
            fail("buyer.id", f"buyer.id: {id_schemes.shape_hint(scheme)}.")
    if invoice_type == "standard" and vat is None and identifier is None:
        fail("buyer.id", "A buyer without a VAT number is identified by buyer.id and buyer.id_scheme on a standard invoice.", "BR-KSA-14")

    address = buyer.get("address") or {}
    if not isinstance(address, Mapping):
        fail("buyer.address", "buyer.address is an object.")
        address = {}
    country = str(address.get("country") or "SA").upper()
    if invoice_type == "standard":
        for key in ("street", "city"):
            if not str(address.get(key) or "").strip():
                fail(f"buyer.address.{key}", f"buyer.address.{key} is required on a standard invoice.", "BR-KSA-10")
        if country == "SA":
            missing = [key for key in ("building_number", "district", "postal_code") if not str(address.get(key) or "").strip()]
            if missing:
                warnings.append(Message("warning", "buyer_address_incomplete", f"buyer.address is missing {', '.join(missing)}; ZATCA accepts it with a warning.", source="local", rule="BR-KSA-63"))
    out.update({"name": name, "vat_number": vat, "id": identifier, "id_scheme": scheme, "address": {**dict(address), "country": country}})
    return out


def _unwritable(value: Any, path: str) -> list[tuple[str, str]]:
    """Every string in ``value`` holding a character XML cannot carry, with where it is."""
    if isinstance(value, str):
        found = _NOT_XML.search(value)
        return [(path or "invoice", found.group())] if found else []
    if isinstance(value, Mapping):
        return [hit for key, item in value.items() for hit in _unwritable(item, f"{path}.{key}" if path else str(key))]
    if isinstance(value, (list, tuple)):
        return [hit for i, item in enumerate(value) for hit in _unwritable(item, f"{path}[{i}]")]
    return []


def _is_base64(value: Any) -> bool:
    try:
        return bool(base64.b64decode(str(value), validate=True))
    except (binascii.Error, ValueError):
        return False


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


def _summary(errors: list[dict[str, Any]]) -> str:
    first = errors[0]["message"]
    return first if len(errors) == 1 else f"{first} (and {len(errors) - 1} more)"
