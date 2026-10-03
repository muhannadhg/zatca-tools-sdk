"""Against ZATCA's real public sandbox (the developer portal). Opt in:

    ZATCA_SDK_SANDBOX=1 pytest tests/test_sandbox.py

Uses only the sandbox's published test values (OTP 123345, VAT 399999999900003).
Nothing here is a tax invoice, and nothing is sent anywhere but ZATCA.
"""

import os

import pytest

from zatca_tools import Zatca

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.skipif(os.environ.get("ZATCA_SDK_SANDBOX") != "1", reason="set ZATCA_SDK_SANDBOX=1 to run against ZATCA's sandbox"),
]

BUYER = {
    "name": "Sample Buyer Trading Co.",
    "vat_number": "399999999800003",
    "address": {"street": "Prince Sultan Street", "building_number": "8228", "district": "Al Rawdah", "city": "Jeddah", "postal_code": "23435"},
}


def test_the_quick_start_as_documented(tmp_path):
    zatca = Zatca("sandbox")
    zatca.onboard()

    invoice = zatca.create_invoice({
        "number": "INV-1001",
        "type": "simplified",
        "items": [{"name": "Product", "quantity": 2, "unit_price": 100}],
    })
    result = zatca.submit(invoice)

    assert result.success, result.to_dict()
    assert result.status == "REPORTED" and result.error is None
    assert invoice.save_xml(tmp_path / "INV-1001.xml").exists()


def test_clearance_a_credit_note_a_rejection_and_renewal():
    zatca = Zatca("sandbox")
    zatca.onboard()

    b2b = zatca.create_invoice({"type": "standard", "number": "PYTEST-INV-2", "buyer": BUYER, "items": [{"name": "Consulting", "quantity": 10, "unit_price": 350}]})
    cleared = zatca.submit(b2b)
    assert cleared.success and cleared.status == "CLEARED", cleared.to_dict()
    assert cleared.cleared_xml and cleared.xml == cleared.cleared_xml

    note = zatca.create_invoice({"type": "simplified", "kind": "credit", "number": "PYTEST-CRN-1", "original_invoice": "PYTEST-INV-1", "reason": "Returned item", "items": [{"name": "Product", "quantity": 1, "unit_price": 100}]})
    assert zatca.submit(note).status == "REPORTED"

    # A real rejection: the invoice is altered after signing, so its total no longer matches its QR code.
    bad = zatca.create_invoice({"type": "simplified", "number": "PYTEST-BAD-1", "items": [{"name": "Product", "quantity": 1, "unit_price": 10}]})
    for element in ("TaxInclusiveAmount", "PayableAmount"):
        bad.xml = bad.xml.replace(f'<cbc:{element} currencyID="SAR">11.50<', f'<cbc:{element} currencyID="SAR">99.00<')
    rejected = zatca.submit(bad)
    assert not rejected.success and rejected.status == "NOT_REPORTED"
    assert rejected.error.source == "zatca" and rejected.error.code == "BR-CO-15" and rejected.error.help_url

    renewed = zatca.renew()
    assert renewed.certificate and renewed.secret
    after = zatca.create_invoice({"type": "simplified", "number": "PYTEST-INV-9", "items": [{"name": "Product", "quantity": 1, "unit_price": 10}]})
    assert after.icv == zatca.chain.icv and zatca.submit(after).success
