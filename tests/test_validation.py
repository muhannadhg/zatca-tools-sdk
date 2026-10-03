"""What is refused before anything is signed or sent — every problem at once, each naming its field."""

import pytest

from zatca_tools import Seller, ValidationError, Zatca


def fields(exc):
    return {e["field"] for e in exc.value.errors}


def test_every_problem_is_reported_at_once(zatca):
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({"type": "b2b", "kind": "refund", "icv": 0, "uuid": "nope", "date": "01/10/2026",
                              "items": [{"name": "", "quantity": "abc", "unit_price": -5}]})
    assert {"type", "kind", "number", "icv", "uuid", "date", "items[0].name", "items[0].quantity", "items[0].unit_price"} <= fields(exc)
    assert exc.value.code == "validation_error" and exc.value.source == "local"


def test_an_invoice_needs_items(zatca, simple_invoice):
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "items": []})
    assert fields(exc) == {"items"}


def test_a_misspelt_field_is_refused_with_a_hint(zatca, simple_invoice):
    data = {**simple_invoice, "lines": simple_invoice["items"], "form": "simplified"}
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice(data)
    messages = {e["field"]: e["message"] for e in exc.value.errors}
    assert "Did you mean items?" in messages["lines"] and "Did you mean type?" in messages["form"]
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "items": [{"name": "x", "quantity": 1, "price": 5}]})
    assert "items[0].price" in fields(exc)


def test_a_character_xml_cannot_carry_is_refused_where_it_is(zatca, simple_invoice):
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "items": [{"name": "Coffee\x01", "quantity": 1, "unit_price": 10}]})
    assert fields(exc) == {"items[0].name"} and "U+0001" in exc.value.errors[0]["message"]


def test_a_check_that_applies_a_zatca_rule_names_it_and_links_its_guide(zatca, simple_invoice):
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "date": "2999-01-01"})
    error = exc.value.errors[0]
    assert error["rule"] == "BR-KSA-04"
    assert error["help_url"] == "https://zatcatools.com/en/docs/errors/br-ksa-04"
    # The rule is the one the check applies, not a code ZATCA returned.
    assert "BR-KSA-04" not in error["message"]


def test_a_check_with_no_zatca_rule_has_no_rule_link(zatca, simple_invoice):
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "number": ""})
    assert exc.value.errors[0]["rule"] is None and exc.value.errors[0]["help_url"] is None
    assert exc.value.help_url == "https://zatcatools.com/docs/sdk/advanced#validation_error"


def test_the_error_as_data(zatca, simple_invoice):
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "kind": "credit"})
    data = exc.value.to_dict()
    assert data["success"] is False
    assert data["error"]["code"] == "validation_error" and data["error"]["source"] == "local"
    assert {e["field"]: e["rule"] for e in data["error"]["errors"]} == {"original_invoice": "BR-KSA-56", "reason": "BR-KSA-17"}


def test_a_standard_invoice_names_its_buyer(zatca, simple_invoice):
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "type": "standard"})
    assert fields(exc) == {"buyer"}


def test_a_buyer_without_vat_is_identified_and_has_an_address(zatca, simple_invoice):
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "type": "standard", "buyer": {"name": "Somebody"}})
    assert {"buyer.id", "buyer.address.street", "buyer.address.city"} <= fields(exc)
    rules = {e["field"]: e["rule"] for e in exc.value.errors}
    assert rules["buyer.id"] == "BR-KSA-14" and rules["buyer.address.street"] == "BR-KSA-10"


def test_a_national_id_with_a_wrong_check_digit_is_refused(zatca, simple_invoice):
    buyer = {"name": "A Citizen", "id": "1000000009", "id_scheme": "NAT", "address": {"street": "Main St", "city": "Riyadh"}}
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "type": "standard", "buyer": buyer})
    assert "buyer.id" in fields(exc)


def test_a_treatment_must_be_one_zatca_knows(zatca, simple_invoice):
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "items": [{"name": "x", "quantity": 1, "unit_price": 1, "tax_category": "X"}]})
    assert fields(exc) == {"items[0].tax_category"}
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "items": [{"name": "x", "quantity": 1, "unit_price": 1, "tax_category": "E", "tax_reason_code": "VATEX-SA-35"}]})
    assert "belongs to category Z" in exc.value.errors[0]["message"]


def test_a_discount_that_takes_everything_is_refused(zatca, simple_invoice):
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "discount": 100})
    assert fields(exc) == {"discount"}


def test_education_for_a_citizen_needs_the_national_id(zatca, simple_invoice):
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice({**simple_invoice, "items": [{"name": "Tutoring", "quantity": 1, "unit_price": 100, "tax_category": "Z", "tax_reason_code": "VATEX-SA-EDU"}]})
    assert exc.value.errors[0]["rule"] == "BR-KSA-49"


def test_the_seller_is_checked(test_credentials, simple_invoice):
    zatca = Zatca("simulation", seller={"vat_number": "123", "name": "X", "cr_number": "12", "address": {}}, credentials=test_credentials, chain="new")
    with pytest.raises(ValidationError) as exc:
        zatca.create_invoice(simple_invoice)
    assert {"seller.vat_number", "seller.cr_number", "seller.address.street", "seller.address.city"} <= fields(exc)


def test_the_sandbox_only_takes_its_test_vat_number(spec):
    with pytest.raises(ValidationError) as exc:
        Zatca("sandbox", seller={**spec["seller"], "vat_number": "310000000000003"})
    assert fields(exc) == {"seller.vat_number"} and "399999999900003" in exc.value.errors[0]["message"]
    assert Zatca("sandbox").seller.vat_number == "399999999900003"


def test_local_warnings_are_returned_not_raised(zatca, simple_invoice):
    invoice = zatca.create_invoice({
        **simple_invoice,
        "buyer": {"name": "Big Buyer", "vat_number": "399999999800003"},
        "items": [{"name": "Equipment", "quantity": 1, "unit_price": 2000}, {"name": "Medicine", "quantity": 1, "unit_price": 10, "tax_category": "Z"}],
    })
    warnings = {w.code: w for w in invoice.warnings}
    assert set(warnings) == {"missing_exemption_reason", "standard_invoice_required"}
    assert all(w.source == "local" and w.type == "warning" for w in invoice.warnings)
    assert warnings["missing_exemption_reason"].rule == "BR-KSA-69"
    assert warnings["missing_exemption_reason"].help_url == "https://zatcatools.com/en/docs/errors/br-ksa-69"
    assert warnings["standard_invoice_required"].help_url == "https://zatcatools.com/docs/sdk/advanced#standard_invoice_required"


def test_credentials_are_required_to_sign(simple_invoice):
    with pytest.raises(ValidationError) as exc:
        Zatca("sandbox").create_invoice(simple_invoice)
    assert "onboard()" in exc.value.errors[0]["message"]


def test_credentials_never_print_their_secrets(test_credentials):
    from zatca_tools import Credentials

    text = repr(Credentials.from_dict(test_credentials))
    assert "test-secret" not in text and "PRIVATE KEY" not in text


def test_seller_from_dict_cleans_the_registration_number():
    seller = Seller.from_dict({"vat_number": "399999999900003", "name": "Co", "cr_number": "1010-010 000", "address": {"street": "s", "city": "c"}})
    assert seller.cr_number == "1010010000"
