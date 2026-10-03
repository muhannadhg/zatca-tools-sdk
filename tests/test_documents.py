"""Created invoices: what they carry, how the chain moves, and the shape of their JSON."""

import base64
import json

import pytest
from lxml import etree

from zatca_tools import INITIAL_PIH, Chain, ValidationError, Zatca, decode_qr
from zatca_tools._rounding import fmt, line_net, round_half_up
from zatca_tools.signing import qr


def test_round_half_up_is_decimal_not_binary():
    assert round_half_up(2.675, 2) == 2.68
    assert round_half_up(0.285, 2) == 0.29
    assert round_half_up(-1.005, 2) == -1.01
    assert line_net(1.5, 4.55) == 6.83  # 6.825 in decimal; binary floating point says 6.82
    assert fmt(15, 0) == "15" and fmt(1.5, 6) == "1.500000"


def test_a_created_invoice_is_complete_and_local(zatca, simple_invoice):
    invoice = zatca.create_invoice(simple_invoice)
    assert invoice.operation == "reporting"
    assert invoice.icv == 1 and invoice.pih == INITIAL_PIH  # the first invoice of a unit
    assert invoice.totals["total"] == 115.0 and invoice.totals["tax"] == 15.0
    assert invoice.items[0]["tax_rate"] == 15.0 and invoice.items[0]["unit"] == "PCE"
    assert len(base64.b64decode(invoice.hash)) == 32

    root = etree.fromstring(invoice.xml.encode())
    ns = {"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"}
    assert root.findtext("cbc:ID", namespaces=ns) == "INV-1"
    assert root.findtext("cbc:IssueTime", namespaces=ns) == "09:00:00"  # Riyadh time, no zone

    data = invoice.to_dict()
    assert data["stage"] == "created" and data["invoice"]["type"] == "simplified"
    assert "xml" not in data and data["xml_bytes"] > 1000
    assert invoice.to_dict(include_xml=True)["xml"] == invoice.xml
    json.dumps(data)  # serialisable as is


def test_the_chain_moves_by_itself(zatca, simple_invoice):
    first = zatca.create_invoice(simple_invoice)
    second = zatca.create_invoice({**simple_invoice, "number": "INV-2"})
    assert (first.icv, second.icv) == (1, 2)
    assert second.pih == first.hash
    assert zatca.chain == Chain(2, second.hash)
    assert zatca.chain.to_dict() == {"icv": 2, "hash": second.hash}


def test_a_saved_chain_carries_on_after_a_restart(spec, test_credentials, zatca, simple_invoice):
    zatca.create_invoice(simple_invoice)
    saved = json.loads(json.dumps(zatca.chain.to_dict()))
    restarted = Zatca("production", seller=spec["seller"], credentials=test_credentials, chain=saved)
    after = restarted.create_invoice({**simple_invoice, "number": "INV-2"})
    assert after.icv == 2 and after.pih == saved["hash"]


def test_outside_the_sandbox_the_chain_is_never_guessed(spec, test_credentials, simple_invoice):
    zatca = Zatca("production", seller=spec["seller"], credentials=test_credentials)
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice(simple_invoice)
    assert exc.value.errors[0]["field"] == "chain"
    with pytest.raises(ValidationError):
        Zatca("production", seller=spec["seller"], credentials=test_credentials, chain={"icv": "seven"})


def test_the_chain_can_be_kept_by_hand(spec, test_credentials, simple_invoice):
    zatca = Zatca("production", seller=spec["seller"], credentials=test_credentials)
    invoice = zatca.create_invoice({**simple_invoice, "icv": 41, "pih": INITIAL_PIH})
    assert invoice.icv == 41 and zatca.chain.icv == 41
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "icv": 42})
    assert exc.value.errors[0]["field"] == "pih"


def test_the_qr_code_carries_the_invoice(zatca, simple_invoice):
    fields = decode_qr(zatca.create_invoice(simple_invoice).qr)
    assert fields["seller_name"] == "شركة الاختبار للتجارة"
    assert fields["vat_number"] == "399999999900003"
    assert fields["timestamp"] == "2026-10-01T09:00:00"
    assert fields["total"] == "115.00" and fields["vat_total"] == "15.00"
    assert "certificate_signature" in fields  # simplified invoices carry tag 9


def test_a_standard_invoice_qr_has_eight_tags(zatca, simple_invoice, buyer):
    invoice = zatca.create_invoice({**simple_invoice, "type": "standard", "buyer": buyer})
    assert invoice.operation == "clearance"
    assert "certificate_signature" not in decode_qr(invoice.qr)


def test_a_qr_field_over_255_bytes_is_refused():
    with pytest.raises(ValidationError):
        qr.encode([(1, "ش" * 200)])


def test_prices_with_vat_settle_to_what_the_customer_paid(zatca, simple_invoice):
    invoice = zatca.create_invoice({**simple_invoice, "prices_include_vat": True, "discount": 40, "items": [{"name": "Chair repair", "quantity": 1, "unit_price": 400}]})
    assert invoice.totals["total"] == 360.0


def test_a_credit_note(zatca, simple_invoice):
    note = zatca.create_invoice({**simple_invoice, "kind": "credit", "number": "CRN-1", "original_invoice": "INV-1", "reason": "Returned item"})
    assert note.kind == "credit" and "<cbc:InvoiceTypeCode name=\"0200000\">381<" in note.xml
    assert "<cbc:ID>INV-1</cbc:ID>" in note.xml


def test_the_invoice_can_be_saved(zatca, simple_invoice, tmp_path):
    path = zatca.create_invoice(simple_invoice).save_xml(tmp_path / "invoice.xml")
    assert path.read_text(encoding="utf-8").startswith('<?xml version="1.0" encoding="UTF-8"?>')


def test_creating_an_invoice_touches_no_network(spec, test_credentials, simple_invoice):
    import httpx

    def refuse(request):
        raise AssertionError("create_invoice must not send anything")

    zatca = Zatca("simulation", seller=spec["seller"], credentials=test_credentials, chain="new", http_client=httpx.Client(transport=httpx.MockTransport(refuse)))
    zatca.create_invoice(simple_invoice)
