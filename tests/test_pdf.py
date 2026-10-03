"""Printed invoices: PDF/A-3 with the XML inside, drawn from the XML, branded but never rearranged.

Conformance itself is checked by veraPDF (Java), which this suite runs when
ZATCA_SDK_VERAPDF points at the veraPDF launcher:

    ZATCA_SDK_VERAPDF=/path/to/verapdf pytest tests/test_pdf.py
"""

import io
import os
import subprocess
import sys

import pytest

pypdf = pytest.importorskip("pypdf")

from zatca_tools import InvoiceDesign, PdfError, ValidationError  # noqa: E402
from zatca_tools.pdf.layout import _isolate  # noqa: E402
from zatca_tools.results import SubmissionResult  # noqa: E402


def read(pdf_bytes):
    return pypdf.PdfReader(io.BytesIO(pdf_bytes))


def text_of(reader):
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_a_receipt_is_pdf_a3_with_its_xml_inside(zatca, simple_invoice):
    invoice = zatca.create_invoice(simple_invoice)
    reader = read(invoice.to_pdf())

    xmp = reader.xmp_metadata.xmp_metadata if hasattr(reader.xmp_metadata, "xmp_metadata") else None
    metadata = reader.trailer["/Root"]["/Metadata"].get_object().get_data().decode("utf-8")
    assert "<pdfaid:part>3</pdfaid:part>" in metadata and "<pdfaid:conformance>B</pdfaid:conformance>" in metadata, xmp

    attachments = reader.attachments
    assert list(attachments) == ["INV-1.xml"]
    assert attachments["INV-1.xml"][0].decode("utf-8") == invoice.xml

    catalog = reader.trailer["/Root"]
    spec = catalog["/AF"][0].get_object()
    assert spec["/AFRelationship"] == "/Alternative" and spec["/F"] == "INV-1.xml"
    assert catalog["/OutputIntents"][0].get_object()["/S"] == "/GTS_PDFA1"


def test_what_is_printed_is_what_was_signed(zatca, simple_invoice):
    invoice = zatca.create_invoice({**simple_invoice, "items": [{"name": "Service", "quantity": 3, "unit_price": 100, "description": "October retainer"}]})
    printed = text_of(read(invoice.to_pdf()))
    for expected in ("INV-1", "2026-10-01", "09:00:00", "399999999900003", "1010010000", "300.00", "45.00", "345.00", "October retainer", "Simplified Tax Invoice"):
        assert expected in printed, expected


def test_the_printed_qr_code_scans_to_the_signed_one(zatca, simple_invoice):
    zxing = pytest.importorskip("zxingcpp")
    from PIL import Image

    invoice = zatca.create_invoice(simple_invoice)
    page = read(invoice.to_pdf()).pages[0]
    images = [Image.open(io.BytesIO(image.data)) for image in page.images]
    found = [result.text for image in images for result in zxing.read_barcodes(image)]
    assert found == [invoice.qr]


def test_a_standard_invoice_prints_only_once_cleared(zatca, simple_invoice, buyer):
    invoice = zatca.create_invoice({**simple_invoice, "type": "standard", "buyer": buyer})
    with pytest.raises(ValidationError) as exc:
        invoice.to_pdf()
    assert "result.save_pdf" in exc.value.errors[0]["message"]

    stamped = invoice.xml.replace("<cbc:ProfileID>", "<cbc:ProfileID>", 1)  # stands in for ZATCA's cleared copy
    cleared = SubmissionResult(operation="clearance", success=True, status="CLEARED", invoice=invoice, cleared_xml=stamped)
    reader = read(cleared.to_pdf())
    assert reader.attachments["INV-1.xml"][0].decode("utf-8") == stamped
    assert "cleared" in reader.trailer["/Root"]["/AF"][0].get_object()["/Desc"]
    assert "Buyer Co" in text_of(reader) and "399999999800003" in text_of(reader)


def test_a_rejected_invoice_is_not_printed_from_its_result(zatca, simple_invoice):
    invoice = zatca.create_invoice(simple_invoice)
    rejected = SubmissionResult(operation="reporting", success=False, status="NOT_REPORTED", invoice=invoice)
    with pytest.raises(ValidationError):
        rejected.to_pdf()


def test_a_long_invoice_runs_over_pages_and_says_so(zatca, simple_invoice):
    items = [{"name": f"Item {i}", "quantity": 1, "unit_price": 10 + i} for i in range(1, 61)]
    reader = read(zatca.create_invoice({**simple_invoice, "items": items}).to_pdf())
    assert len(reader.pages) >= 2
    pages = [page.extract_text() for page in reader.pages]
    assert all(f"Page {n} of {len(pages)}" in text for n, text in enumerate(pages, 1))
    assert all("INV-1" in text for text in pages)  # every page names the invoice it belongs to
    assert "Item 60" in pages[-1]


def test_the_design_is_yours_the_mark_is_optional(zatca, simple_invoice, tmp_path):
    from PIL import Image

    logo = tmp_path / "logo.png"
    Image.new("RGBA", (300, 120), (200, 30, 30, 0)).save(logo)  # fully transparent: must not print black
    invoice = zatca.create_invoice(simple_invoice)

    branded = text_of(read(invoice.to_pdf({"logo": str(logo), "accent": "c81e1e", "footer": "Returns within 14 days."})))
    assert "Returns within 14 days." in branded and "ZATCA Tools" in branded
    plain = text_of(read(invoice.to_pdf(InvoiceDesign(zatca_tools_mark=False))))
    assert "ZATCA Tools" not in plain and "zatcatools.com" not in plain


def test_the_client_design_is_the_default(spec, test_credentials, simple_invoice):
    from zatca_tools import Zatca

    zatca = Zatca("simulation", seller=spec["seller"], credentials=test_credentials, chain="new", design={"footer": "Paid by card."})
    assert "Paid by card." in text_of(read(zatca.create_invoice(simple_invoice).to_pdf()))


@pytest.mark.parametrize("options, field", [
    ({"accent": "green"}, "design.accent"),
    ({"logo": "/no/such/logo.png"}, "design.logo"),
    ({"footer": "x" * 2001}, "design.footer"),
    ({"colour": "#000000"}, "design.colour"),
])
def test_a_design_problem_is_refused_by_name(options, field):
    with pytest.raises(ValidationError) as exc:
        InvoiceDesign.parse(options)
    assert exc.value.errors[0]["field"] == field


def test_an_unreadable_logo_is_a_pdf_error(zatca, simple_invoice):
    with pytest.raises(PdfError) as exc:
        zatca.create_invoice(simple_invoice).to_pdf({"logo": b"not an image"})
    assert exc.value.code == "pdf_error" and exc.value.help_url.endswith("/docs/sdk/advanced#pdf_error")


def test_an_incomplete_installation_says_how_to_repair_it(zatca, simple_invoice, monkeypatch):
    import zatca_tools.pdf

    invoice = zatca.create_invoice(simple_invoice)
    for name in [m for m in sys.modules if m == "fpdf" or m.startswith("fpdf.") or m == "zatca_tools.pdf.layout"]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.delattr(zatca_tools.pdf, "layout", raising=False)
    monkeypatch.setitem(sys.modules, "fpdf", None)
    with pytest.raises(PdfError) as exc:
        invoice.to_pdf()
    assert "pip install --force-reinstall zatca-tools-sdk" in exc.value.message


def test_a_percentage_inside_arabic_keeps_its_sign_beside_its_figure():
    assert _isolate("ضريبة القيمة المضافة 15%") == "ضريبة القيمة المضافة ⁦15%⁩"
    assert _isolate("خصم 12.5 % على الكل") == "خصم ⁦12.5 %⁩ على الكل"
    assert _isolate("No percent here") == "No percent here"


@pytest.mark.skipif(not os.environ.get("ZATCA_SDK_VERAPDF"), reason="set ZATCA_SDK_VERAPDF to the veraPDF launcher")
def test_verapdf_says_pdf_a_3b(zatca, simple_invoice, buyer, tmp_path):
    receipt = zatca.create_invoice({**simple_invoice, "items": [{"name": "خدمة استشارية", "quantity": 1, "unit_price": 100}, {"name": "أدوية", "quantity": 2, "unit_price": 30, "tax_category": "Z", "tax_reason_code": "VATEX-SA-35"}]})
    receipt.save_pdf(tmp_path / "receipt.pdf", {"footer": "شكرًا لكم\nThank you"})
    b2b = zatca.create_invoice({**simple_invoice, "number": "INV-2", "type": "standard", "buyer": buyer})
    SubmissionResult(operation="clearance", success=True, status="CLEARED", invoice=b2b, cleared_xml=b2b.xml).save_pdf(tmp_path / "b2b.pdf")
    run = subprocess.run([os.environ["ZATCA_SDK_VERAPDF"], "--flavour", "3b", "--format", "text", str(tmp_path / "receipt.pdf"), str(tmp_path / "b2b.pdf")], capture_output=True, text=True, shell=sys.platform == "win32")
    lines = [line for line in run.stdout.splitlines() if line.strip()]
    assert len(lines) == 2 and all(line.startswith("PASS") for line in lines), run.stdout + run.stderr
