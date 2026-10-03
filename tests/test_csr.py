"""The CSR, read back with cryptography: every field ZATCA reads is where ZATCA reads it."""

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ObjectIdentifier

from zatca_tools import CsrRequest, Environment, ValidationError
from zatca_tools.csr import generate_csr


def request(**overrides):
    values = dict(vat_number="399999999900003", organization_name="Test Co", organization_unit="Riyadh Branch", location="RRRD2929", industry="Retail", serial="ed22f1d8-e6a2-1118-9b58-d9a8f11e445f")
    values.update(overrides)
    return CsrRequest(**values)


@pytest.mark.parametrize("environment, template", [
    (Environment.SANDBOX, "TSTZATCA-Code-Signing"),
    (Environment.SIMULATION, "PREZATCA-Code-Signing"),
    (Environment.PRODUCTION, "ZATCA-Code-Signing"),
])
def test_the_csr_carries_what_zatca_reads(environment, template):
    keys = generate_csr(request(), environment)
    csr = x509.load_pem_x509_csr(keys.csr.encode())
    assert csr.is_signature_valid

    subject = {a.oid: a.value for a in csr.subject}
    assert subject[NameOID.COUNTRY_NAME] == "SA"
    assert subject[NameOID.ORGANIZATIONAL_UNIT_NAME] == "Riyadh Branch"
    assert subject[NameOID.ORGANIZATION_NAME] == "Test Co"
    assert subject[NameOID.COMMON_NAME] == "ZatcaToolsSDK-399999999900003"

    extension = csr.extensions.get_extension_for_oid(ObjectIdentifier("1.3.6.1.4.1.311.20.2")).value
    assert extension.value == bytes([0x0C, len(template)]) + template.encode()

    directory = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DirectoryName)[0]
    values = {a.oid.dotted_string: a.value for a in directory}
    assert values["2.5.4.4"] == "1-ZatcaToolsSDK|2-Python|3-ed22f1d8-e6a2-1118-9b58-d9a8f11e445f"
    assert values["0.9.2342.19200300.100.1.1"] == "399999999900003"
    assert values["2.5.4.12"] == "1100"
    assert values["2.5.4.26"] == "RRRD2929"
    assert values["2.5.4.15"] == "Retail"

    key = serialization.load_pem_private_key(keys.private_key.encode(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey) and key.curve.name == "secp256k1"
    assert "private_key" not in keys.to_dict()


def test_bad_csr_details_are_refused():
    with pytest.raises(ValidationError) as exc:
        generate_csr(request(vat_number="12345", organization_name="A&B", invoice_types="0011"), Environment.SANDBOX)
    assert {e["field"] for e in exc.value.errors} == {"vat_number", "organization_name", "invoice_types"}


def test_a_vat_group_member_is_named_by_its_tin():
    with pytest.raises(ValidationError):
        generate_csr(request(vat_number="300000000010003", organization_unit="Head office"), Environment.SANDBOX)
    generate_csr(request(vat_number="300000000010003", organization_unit="3001234567"), Environment.SANDBOX)
