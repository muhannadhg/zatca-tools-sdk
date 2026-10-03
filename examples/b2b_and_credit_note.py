"""A B2B tax invoice (cleared by ZATCA), a credit note against it, and both as branded PDFs.

    python b2b_and_credit_note.py
"""

from zatca_tools import Zatca

zatca = Zatca("sandbox", design={"accent": "#0F766E", "footer": "Payment within 30 days. Thank you for your business."})
zatca.onboard()

buyer = {
    "name": "Buyer Trading Co.",
    "vat_number": "399999999800003",
    "address": {"street": "Prince Sultan Street", "building_number": "8228", "district": "Al Rawdah", "city": "Jeddah", "postal_code": "23435"},
}

invoice = zatca.create_invoice({
    "type": "standard",
    "number": "INV-2001",
    "buyer": buyer,
    "items": [
        {"name": "Consulting (hours)", "quantity": 10, "unit_price": 350},
        {"name": "Medical devices", "quantity": 2, "unit_price": 1200, "tax_category": "Z", "tax_reason_code": "VATEX-SA-35"},
    ],
})
cleared = zatca.submit(invoice)
print("Invoice:", cleared.status)
if cleared.success:
    cleared.save_xml("INV-2001.xml")  # ZATCA's stamped copy: the one the buyer gets
    cleared.save_pdf("INV-2001.pdf")

note = zatca.create_invoice({
    "type": "standard",
    "kind": "credit",
    "number": "CRN-2001",
    "original_invoice": "INV-2001",
    "reason": "Two hours not delivered",
    "buyer": buyer,
    "items": [{"name": "Consulting (hours)", "quantity": 2, "unit_price": 350}],
})
result = zatca.submit(note)
print("Credit note:", result.status)
if result.success:
    result.save_pdf("CRN-2001.pdf")
for warning in result.warnings:
    print("warning:", warning.code, warning.help_url)
