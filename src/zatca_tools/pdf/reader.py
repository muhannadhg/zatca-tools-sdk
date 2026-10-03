"""Reading a signed or cleared ZATCA invoice back out of its XML, for printing.

The PDF is drawn from the XML that was signed — or from ZATCA's cleared copy —
never from the input dict, so the printed figures cannot disagree with the ones
ZATCA holds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from lxml import etree

NS = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}


@dataclass
class Party:
    name: str = ""
    vat_number: Optional[str] = None
    identifier: Optional[str] = None
    identifier_scheme: Optional[str] = None
    street: Optional[str] = None
    building_number: Optional[str] = None
    district: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None

    @property
    def address_lines(self) -> list[str]:
        first = " ".join(part for part in (self.building_number, self.street) if part)
        parts = [part for part in (self.district, self.city) if part]
        arabic = any("\u0600" <= char <= "\u06ff" for part in parts for char in part)
        second = ("، " if arabic else ", ").join(parts)
        third = " ".join(part for part in (self.postal_code, self.country if self.country and self.country != "SA" else None) if part)
        return [line for line in (first, second, third) if line]


@dataclass
class Line:
    number: str
    name: str
    quantity: float
    unit: str
    unit_price: float
    net: float
    tax: float
    total: float
    category: str
    rate: float


@dataclass
class Subtotal:
    category: str
    rate: float
    taxable: float
    tax: float
    reason_code: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class PrintedInvoice:
    number: str
    uuid: str
    simplified: bool
    kind: str  # invoice | credit | debit
    issue_date: str
    issue_time: str
    currency: str
    seller: Party
    buyer: Optional[Party]
    lines: list[Line]
    subtotals: list[Subtotal]
    line_total: float
    allowance: float
    taxable: float
    tax: float
    total: float
    payable: float
    qr: str
    original_invoice: Optional[str] = None
    reason: Optional[str] = None
    delivery_date: Optional[str] = None
    #: What the XML does not carry, from the Invoice when there is one: per item
    #: the description, the agreed price and its own discount, the exemption code.
    items: list[dict] = field(default_factory=list)
    #: The items before an invoice-level discount, and that discount — likewise.
    gross: Optional[float] = None
    discount: Optional[float] = None


def read(xml: str) -> PrintedInvoice:
    root = etree.fromstring(xml.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True))
    text = lambda path, node=root: (node.findtext(path, namespaces=NS) or "").strip()  # noqa: E731
    number = lambda path, node=root: float(text(path, node) or 0)  # noqa: E731

    type_code = root.find("cbc:InvoiceTypeCode", NS)
    kind = {"388": "invoice", "381": "credit", "383": "debit"}.get((type_code.text or "").strip() if type_code is not None else "", "invoice")
    simplified = (type_code.get("name", "") if type_code is not None else "").startswith("02")

    qr = ""
    for reference in root.findall("cac:AdditionalDocumentReference", NS):
        if text("cbc:ID", reference) == "QR":
            qr = text("cac:Attachment/cbc:EmbeddedDocumentBinaryObject", reference)

    buyer_node = root.find("cac:AccountingCustomerParty/cac:Party", NS)
    buyer = _party(buyer_node) if buyer_node is not None and len(buyer_node) else None

    totals = root.find("cac:LegalMonetaryTotal", NS)
    subtotals, tax = [], 0.0
    for total in root.findall("cac:TaxTotal", NS):
        nodes = total.findall("cac:TaxSubtotal", NS)
        if nodes:
            tax = number("cbc:TaxAmount", total)
        for node in nodes:
            category = node.find("cac:TaxCategory", NS)
            subtotals.append(Subtotal(
                category=text("cbc:ID", category),
                rate=float(text("cbc:Percent", category) or 0),
                taxable=number("cbc:TaxableAmount", node),
                tax=number("cbc:TaxAmount", node),
                reason_code=text("cbc:TaxExemptionReasonCode", category) or None,
                reason=text("cbc:TaxExemptionReason", category) or None,
            ))

    lines = []
    for node in root.findall("cac:InvoiceLine", NS):
        quantity = node.find("cbc:InvoicedQuantity", NS)
        category = node.find("cac:Item/cac:ClassifiedTaxCategory", NS)
        lines.append(Line(
            number=text("cbc:ID", node),
            name=text("cac:Item/cbc:Name", node),
            quantity=float((quantity.text or 0) if quantity is not None else 0),
            unit=(quantity.get("unitCode") if quantity is not None else None) or "PCE",
            unit_price=number("cac:Price/cbc:PriceAmount", node),
            net=number("cbc:LineExtensionAmount", node),
            tax=number("cac:TaxTotal/cbc:TaxAmount", node),
            total=number("cac:TaxTotal/cbc:RoundingAmount", node),
            category=text("cbc:ID", category) if category is not None else "S",
            rate=float(text("cbc:Percent", category) or 0) if category is not None else 0.0,
        ))

    return PrintedInvoice(
        number=text("cbc:ID"),
        uuid=text("cbc:UUID"),
        simplified=simplified,
        kind=kind,
        issue_date=text("cbc:IssueDate"),
        issue_time=text("cbc:IssueTime"),
        currency=text("cbc:DocumentCurrencyCode") or "SAR",
        seller=_party(root.find("cac:AccountingSupplierParty/cac:Party", NS)),
        buyer=buyer,
        lines=lines,
        subtotals=subtotals,
        line_total=number("cbc:LineExtensionAmount", totals),
        allowance=number("cbc:AllowanceTotalAmount", totals),
        taxable=number("cbc:TaxExclusiveAmount", totals),
        tax=tax,
        total=number("cbc:TaxInclusiveAmount", totals),
        payable=number("cbc:PayableAmount", totals),
        qr=qr,
        original_invoice=text("cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID") or None,
        reason=text("cac:PaymentMeans/cbc:InstructionNote") or None,
        delivery_date=text("cac:Delivery/cbc:ActualDeliveryDate") or None,
    )


def _party(node: Optional[etree._Element]) -> Party:
    if node is None:
        return Party()
    text = lambda path: (node.findtext(path, namespaces=NS) or "").strip() or None  # noqa: E731
    identifier = node.find("cac:PartyIdentification/cbc:ID", NS)
    return Party(
        name=text("cac:PartyLegalEntity/cbc:RegistrationName") or "",
        vat_number=text("cac:PartyTaxScheme/cbc:CompanyID"),
        identifier=(identifier.text or "").strip() or None if identifier is not None else None,
        identifier_scheme=identifier.get("schemeID") if identifier is not None else None,
        street=text("cac:PostalAddress/cbc:StreetName"),
        building_number=text("cac:PostalAddress/cbc:BuildingNumber"),
        district=text("cac:PostalAddress/cbc:CitySubdivisionName"),
        city=text("cac:PostalAddress/cbc:CityName"),
        postal_code=text("cac:PostalAddress/cbc:PostalZone"),
        country=text("cac:PostalAddress/cac:Country/cbc:IdentificationCode"),
    )
