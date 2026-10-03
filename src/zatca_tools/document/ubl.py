"""Writing the UBL 2.1 invoice XML.

The layout — element order, four-space indentation, number formats — is the one
ZATCA has been accepting from the ZATCA Tools platform in production. It matters
more than it looks: the invoice hash is computed over this exact text (after
removing the signature, the QR code and the UBL extensions, with the whitespace
they leave behind), so two documents with the same content but different
indentation have different hashes.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Union

from .._rounding import fmt

NS = {
    "": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
}

TYPE_CODES = {"invoice": "388", "credit": "381", "debit": "383"}
TRANSACTION_CODES = {"standard": "0100000", "simplified": "0200000"}

#: Placeholders for the three parts that are added at signing and excluded from the hash.
UBL_EXTENSIONS = "\x00UBL_EXTENSIONS\x00"
QR = "\x00QR\x00"
SIGNATURE = "\x00SIGNATURE\x00"

Node = Union["El", str]


def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r", "&#13;")


def _escape_attr(value: str) -> str:
    return _escape_text(value).replace('"', "&quot;")


class El:
    """A minimal element: a name, attributes, and either text or children."""

    __slots__ = ("name", "attrs", "text", "children")

    def __init__(self, name: str, text: Optional[str] = None, attrs: Optional[Mapping[str, str]] = None, children: Optional[Iterable[Node]] = None) -> None:
        self.name = name
        self.attrs = dict(attrs or {})
        self.text = text
        self.children = [c for c in (children or []) if c is not None]

    def render(self, depth: int) -> str:
        pad = "    " * depth
        attrs = "".join(f' {k}="{_escape_attr(v)}"' for k, v in self.attrs.items())
        if self.text is not None:
            return f"{pad}<{self.name}{attrs}>{_escape_text(self.text)}</{self.name}>"
        if not self.children:
            return f"{pad}<{self.name}{attrs}/>"
        inner = "\n".join(c if isinstance(c, str) else c.render(depth + 1) for c in self.children)
        return f"{pad}<{self.name}{attrs}>\n{inner}\n{pad}</{self.name}>"


def _amount(name: str, value: float) -> El:
    return El(name, fmt(value, 2), {"currencyID": "SAR"})


def render(payload: Mapping[str, Any]) -> str:
    """The document's XML with placeholders where the extensions, QR and signature go."""
    form = payload["invoiceType"]["invoice"]
    kind = payload["invoiceType"]["type"]

    children: list[Node] = [
        UBL_EXTENSIONS,
        El("cbc:ProfileID", "reporting:1.0"),
        El("cbc:ID", str(payload["id"])),
        El("cbc:UUID", str(payload["uuid"])),
        El("cbc:IssueDate", payload["issueDate"]),
        El("cbc:IssueTime", payload["issueTime"]),
        El("cbc:InvoiceTypeCode", TYPE_CODES[kind], {"name": TRANSACTION_CODES[form]}),
        El("cbc:DocumentCurrencyCode", "SAR"),
        El("cbc:TaxCurrencyCode", "SAR"),
    ]
    for reference in payload.get("billingReferences", []):
        children.append(El("cac:BillingReference", children=[El("cac:InvoiceDocumentReference", children=[El("cbc:ID", str(reference["id"]))])]))
    children += [
        El("cac:AdditionalDocumentReference", children=[El("cbc:ID", "ICV"), El("cbc:UUID", str(payload["icv"]))]),
        El("cac:AdditionalDocumentReference", children=[
            El("cbc:ID", "PIH"),
            El("cac:Attachment", children=[El("cbc:EmbeddedDocumentBinaryObject", payload["pih"], {"mimeCode": "text/plain"})]),
        ]),
        QR,
        SIGNATURE,
        El("cac:AccountingSupplierParty", children=[_party(payload["supplier"])]),
        El("cac:AccountingCustomerParty", children=[_party(payload["customer"]) if payload.get("customer") else El("cac:Party")]),
    ]
    if payload.get("delivery"):
        children.append(El("cac:Delivery", children=[El("cbc:ActualDeliveryDate", payload["delivery"]["actualDeliveryDate"])]))

    means = payload["paymentMeans"]
    children.append(El("cac:PaymentMeans", children=[
        El("cbc:PaymentMeansCode", str(means["code"])),
        El("cbc:InstructionNote", str(means["note"])) if means.get("note") else None,
    ]))

    for charge in payload.get("allowanceCharges", []):
        children.append(El("cac:AllowanceCharge", children=[
            El("cbc:ChargeIndicator", "true" if charge["isCharge"] else "false"),
            El("cbc:AllowanceChargeReason", str(charge["reason"])),
            _amount("cbc:Amount", charge["amount"]),
            *[_tax_category(category) for category in charge["taxCategories"]],
        ]))

    tax_total = payload["taxTotal"]
    children.append(El("cac:TaxTotal", children=[_amount("cbc:TaxAmount", tax_total["taxAmount"])]))
    children.append(El("cac:TaxTotal", children=[
        _amount("cbc:TaxAmount", tax_total["taxAmount"]),
        *[
            El("cac:TaxSubtotal", children=[
                _amount("cbc:TaxableAmount", sub["taxableAmount"]),
                _amount("cbc:TaxAmount", sub["taxAmount"]),
                _tax_category(sub["taxCategory"]),
            ])
            for sub in tax_total["subTotals"]
        ],
    ]))

    totals = payload["legalMonetaryTotal"]
    children.append(El("cac:LegalMonetaryTotal", children=[
        _amount("cbc:LineExtensionAmount", totals["lineExtensionAmount"]),
        _amount("cbc:TaxExclusiveAmount", totals["taxExclusiveAmount"]),
        _amount("cbc:TaxInclusiveAmount", totals["taxInclusiveAmount"]),
        _amount("cbc:AllowanceTotalAmount", totals["allowanceTotalAmount"]),
        _amount("cbc:PrepaidAmount", totals["prepaidAmount"]),
        _amount("cbc:PayableAmount", totals["payableAmount"]),
    ]))

    for line in payload["invoiceLines"]:
        children.append(El("cac:InvoiceLine", children=[
            El("cbc:ID", str(line["id"])),
            El("cbc:InvoicedQuantity", fmt(line["quantity"], 6), {"unitCode": str(line["unitCode"])}),
            _amount("cbc:LineExtensionAmount", line["lineExtensionAmount"]),
            El("cac:TaxTotal", children=[
                _amount("cbc:TaxAmount", line["taxTotal"]["taxAmount"]),
                _amount("cbc:RoundingAmount", line["taxTotal"]["roundingAmount"]),
            ]),
            El("cac:Item", children=[
                El("cbc:Name", line["item"]["name"]),
                *[
                    El("cac:ClassifiedTaxCategory", children=[
                        El("cbc:ID", category["id"]),
                        El("cbc:Percent", fmt(category["percent"], 2)),
                        El("cac:TaxScheme", children=[El("cbc:ID", "VAT")]),
                    ])
                    for category in line["item"]["classifiedTaxCategory"]
                ],
            ]),
            El("cac:Price", children=[_amount("cbc:PriceAmount", line["price"]["amount"])]),
        ]))

    namespaces = " ".join(f'xmlns{":" + k if k else ""}="{v}"' for k, v in NS.items())
    body = "\n".join(c if isinstance(c, str) else c.render(1) for c in children)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<Invoice {namespaces}>\n{body}\n</Invoice>\n'


def _party(party: Mapping[str, Any]) -> El:
    children: list[Optional[El]] = []
    if party.get("identificationId"):
        children.append(El("cac:PartyIdentification", children=[El("cbc:ID", str(party["identificationId"]), {"schemeID": str(party.get("identificationType") or "CRN")})]))
    address = party.get("address")
    if address:
        children.append(El("cac:PostalAddress", children=[
            El("cbc:StreetName", address["street"]) if address.get("street") else None,
            El("cbc:BuildingNumber", address["buildingNumber"]) if address.get("buildingNumber") else None,
            El("cbc:CitySubdivisionName", address["subdivision"]) if address.get("subdivision") else None,
            El("cbc:CityName", address["city"]) if address.get("city") else None,
            El("cbc:PostalZone", address["postalZone"]) if address.get("postalZone") else None,
            El("cac:Country", children=[El("cbc:IdentificationCode", address.get("country") or "SA")]),
        ]))
    children.append(El("cac:PartyTaxScheme", children=[
        El("cbc:CompanyID", str(party["taxId"])) if party.get("taxId") else None,
        El("cac:TaxScheme", children=[El("cbc:ID", "VAT")]),
    ]))
    children.append(El("cac:PartyLegalEntity", children=[El("cbc:RegistrationName", str(party["registrationName"]))]))
    return El("cac:Party", children=children)


def _tax_category(category: Mapping[str, Any]) -> El:
    return El("cac:TaxCategory", children=[
        El("cbc:ID", category["id"], {"schemeID": "UN/ECE 5305", "schemeAgencyID": "6"}),
        El("cbc:Percent", fmt(category["percent"], 0)),
        El("cbc:TaxExemptionReasonCode", category["reasonCode"]) if category.get("reasonCode") else None,
        El("cbc:TaxExemptionReason", category["reason"]) if category.get("reason") else None,
        El("cac:TaxScheme", children=[El("cbc:ID", "VAT", {"schemeID": "UN/ECE 5153", "schemeAgencyID": "6"})]),
    ])
