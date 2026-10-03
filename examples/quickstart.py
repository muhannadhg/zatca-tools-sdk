"""The quick start: one invoice through ZATCA's public sandbox. No account, no setup.

    pip install zatca-tools-sdk
    python quickstart.py
"""

from zatca_tools import Zatca

zatca = Zatca("sandbox")
zatca.onboard()  # the sandbox's test credentials, straight from ZATCA — a few seconds

invoice = zatca.create_invoice({
    "number": "INV-1001",
    "type": "simplified",
    "items": [
        {"name": "Product", "quantity": 2, "unit_price": 100},
    ],
})

result = zatca.submit(invoice)

if result.success:
    print("ZATCA says:", result.status)  # REPORTED
    invoice.save_xml("INV-1001.xml")
    invoice.save_pdf("INV-1001.pdf")
else:
    print(result.error.message)
    print(result.error.help_url)
