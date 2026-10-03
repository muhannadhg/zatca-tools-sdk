"""Help links: from a code to the page that explains it — offline, and never carrying anything but the code.

The pages themselves are checked against the site by the ZATCA Tools platform's own test
suite: every URL this package can build resolves there.
"""

import json
import re
from importlib import resources

import pytest

from zatca_tools import help as help_module
from zatca_tools import help_url
from zatca_tools.errors import (
    AuthenticationError,
    ComplianceCheckError,
    NetworkError,
    SigningError,
    ValidationError,
    XmlError,
    ZatcaRequestError,
    ZatcaServiceError,
)
from zatca_tools.results import ComplianceReport, Message

SITE = "https://zatcatools.com"
DATA = json.loads(resources.files("zatca_tools").joinpath("data").joinpath("error_reference.json").read_text(encoding="utf-8"))


def test_a_code_with_a_guide_links_to_the_guide():
    assert "BR-KSA-63" in DATA["guides"]
    assert help_url("BR-KSA-63") == f"{SITE}/en/docs/errors/br-ksa-63"
    assert help_url("br-ksa-63") == f"{SITE}/en/docs/errors/br-ksa-63"  # ZATCA's codes are upper case; spelling is forgiven


def test_a_documented_code_without_a_guide_links_to_its_row():
    code = next(c for c in DATA["codes"] if c not in DATA["guides"])
    assert help_url(code) == f"{SITE}/en/docs/errors#{code}"


def test_an_unknown_code_gets_the_reference_front_page_without_the_code():
    assert help_url("invoiceTotal_QRCODE_INVALID") == f"{SITE}/en/docs/errors"
    assert help_url("BR-NOT-A-RULE") == f"{SITE}/en/docs/errors"


def test_the_sdks_own_codes_link_to_the_sdk_documentation():
    for code in ("validation_error", "xml_error", "signing_error", "network_error", "authentication_error",
                 "zatca_request_error", "zatca_service_error", "compliance_checks_failed"):
        assert help_url(code) == f"{SITE}/docs/sdk/advanced#{code}"
    assert help_url("Invalid-OTP") == f"{SITE}/docs/sdk/setup#otp"


@pytest.mark.parametrize("value", [None, "", 42, ["BR-KSA-63"], "BR KSA 63", "a" * 81, "BR-KSA-63\n", "-leading-dash",
                                   "310000000000003 Riyadh", "<script>", "https://evil.example/x", "../../etc/passwd"])
def test_something_that_is_not_a_code_gets_no_link(value):
    assert help_url(value) is None


def test_no_link_ever_carries_more_than_a_known_code():
    # Whatever ZATCA puts in a code field, the URL holds a code from the published list or nothing.
    for value in ["BR-KSA-63", "BR-KSA-26", "anything-at-all", "399999999900003", "X" * 80]:
        url = help_url(value)
        assert url is not None and url.startswith(SITE + "/")
        tail = url[len(SITE):]
        assert tail in ("/en/docs/errors",) or re.fullmatch(r"/en/docs/errors(/[a-z0-9-]+|#[A-Z0-9-]+)|/docs/sdk/(advanced|setup)#[A-Za-z_-]+", tail), url
        assert "399999999900003" not in url


def test_a_missing_or_broken_list_degrades_to_the_front_page(monkeypatch):
    help_module._reference.cache_clear()
    monkeypatch.setattr(help_module, "_load", lambda: {})
    try:
        assert help_url("BR-KSA-63") == f"{SITE}/en/docs/errors"
        assert help_url("network_error") == f"{SITE}/docs/sdk/advanced#network_error"
        assert help_url(None) is None
    finally:
        help_module._reference.cache_clear()


def test_a_corrupt_list_file_does_not_raise(monkeypatch):
    help_module._reference.cache_clear()

    class Broken:
        def joinpath(self, *_):
            return self

        def read_text(self, **_):
            return "{ not json"

    monkeypatch.setattr(help_module.resources, "files", lambda _: Broken())
    try:
        assert help_url("BR-KSA-63") == f"{SITE}/en/docs/errors"
    finally:
        help_module._reference.cache_clear()


def test_the_shipped_list_and_the_known_paths_agree():
    assert DATA["site"] == help_module.SITE
    assert (DATA["index"], DATA["guide"], DATA["reference"]) == (help_module.REFERENCE_INDEX, help_module.GUIDE, help_module.REFERENCE_ROW)
    assert set(DATA["guides"]) <= set(DATA["codes"])
    assert "_about" in DATA and not any(len(c) > 80 for c in DATA["codes"])
    # Codes only: the explanations stay on the site.
    assert set(DATA) == {"_about", "exported_at", "site", "index", "guide", "reference", "guides", "codes"}


def test_every_error_answers_code_message_and_help():
    errors = [
        ValidationError("bad", [{"field": "x", "message": "y"}]),
        XmlError("bad"),
        SigningError("bad"),
        NetworkError("bad", may_have_reached_zatca=False),
        AuthenticationError("bad"),
        ZatcaServiceError("bad", http_status=503),
        ZatcaRequestError("bad", http_status=400),
        ComplianceCheckError("bad", ComplianceReport([])),
    ]
    for error in errors:
        data = error.to_dict()
        assert data["success"] is False
        assert data["error"]["code"] == error.code and data["error"]["message"] == "bad"
        assert data["error"]["source"] in ("local", "network", "zatca")
        assert data["error"]["help_url"] == f"{SITE}/docs/sdk/advanced#{error.code}"
    assert {e.source for e in errors[:3]} == {"local"} and errors[3].source == "network"


def test_a_message_prefers_the_rule_it_applies():
    local = Message("warning", "buyer_address_incomplete", "…", source="local", rule="BR-KSA-63")
    assert local.help_url == f"{SITE}/en/docs/errors/br-ksa-63"
    assert Message("warning", "standard_invoice_required", "…", source="local").help_url == f"{SITE}/docs/sdk/advanced#standard_invoice_required"
    assert Message("error", None, "no code").help_url is None
    assert Message("error", None, "no code").to_dict()["help_url"] is None
