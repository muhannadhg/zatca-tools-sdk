"""Talking to ZATCA, with ZATCA replaced by a mock transport. Nothing here touches the network."""

import base64
import json

import httpx
import pytest

from zatca_tools import (
    ComplianceCheckError,
    CsrRequest,
    ValidationError,
    Zatca,
    ZatcaRequestError,
)

SITE = "https://zatcatools.com"


def make(spec, credentials, handler, environment="simulation"):
    return Zatca(environment, seller=spec["seller"], credentials=credentials, chain="new", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def verdict(extra=None, warnings=(), errors=()):
    return {
        "validationResults": {
            "status": "ERROR" if errors else ("WARNING" if warnings else "PASS"),
            "infoMessages": [],
            "warningMessages": [{"type": "WARNING", "code": c, "category": "KSA", "message": m, "status": "WARNING"} for c, m in warnings],
            "errorMessages": [{"type": "ERROR", "code": c, "category": "KSA", "message": m, "status": "ERROR"} for c, m in errors],
        },
        **(extra or {}),
    }


def reply(status, body):
    return lambda request: httpx.Response(status, json=body)


# -- verdicts ----------------------------------------------------------------------------


def test_reporting_sends_what_zatca_expects(spec, test_credentials, simple_invoice):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=verdict({"reportingStatus": "REPORTED"}))

    zatca = make(spec, test_credentials, handler)
    invoice = zatca.create_invoice(simple_invoice)
    result = zatca.submit(invoice)

    assert result.success and result.status == "REPORTED" and result.error is None
    assert seen["url"].endswith("/e-invoicing/simulation/invoices/reporting/single")
    assert seen["headers"]["Clearance-Status"] == "0" and seen["headers"]["Accept-Version"] == "V2"
    token = base64.b64encode(test_credentials["certificate"].encode()).decode()
    assert seen["headers"]["Authorization"] == "Basic " + base64.b64encode(f"{token}:test-secret".encode()).decode()
    assert seen["body"]["uuid"] == invoice.uuid and seen["body"]["invoiceHash"] == invoice.hash
    assert base64.b64decode(seen["body"]["invoice"]).decode() == invoice.xml


def test_warnings_are_accepted_with_their_messages_and_links(spec, test_credentials, simple_invoice):
    zatca = make(spec, test_credentials, reply(202, verdict({"reportingStatus": "REPORTED"}, warnings=[("BR-KSA-63", "Buyer address")])))
    result = zatca.submit(zatca.create_invoice(simple_invoice))
    assert result.success and result.http_status == 202 and result.errors == []
    warning = result.warnings[0]
    assert (warning.type, warning.code, warning.source) == ("warning", "BR-KSA-63", "zatca")
    assert warning.help_url == f"{SITE}/en/docs/errors/br-ksa-63"


def test_a_rejection_is_a_result_with_zatcas_reasons(spec, test_credentials, simple_invoice):
    zatca = make(spec, test_credentials, reply(400, verdict({"reportingStatus": "NOT_REPORTED"}, errors=[("BR-KSA-37", "Building number"), ("invoiceTotal_QRCODE_INVALID", "QR total")])))
    result = zatca.submit(zatca.create_invoice(simple_invoice))
    assert not result.success and result.status == "NOT_REPORTED" and result.validation_status == "ERROR"
    assert result.error.code == "BR-KSA-37" and result.error.source == "zatca"
    assert result.error.help_url.startswith(f"{SITE}/en/docs/errors")
    # A code the reference does not document gets its front page, not an invented URL.
    assert result.errors[1].help_url == f"{SITE}/en/docs/errors"
    data = result.to_dict()
    assert data["error"]["code"] == "BR-KSA-37" and len(data["errors"]) == 2


def test_a_message_without_a_code_keeps_what_zatca_said(spec, test_credentials, simple_invoice):
    body = verdict({"reportingStatus": "NOT_REPORTED"})
    body["validationResults"]["status"] = "ERROR"
    body["validationResults"]["errorMessages"] = [{"message": "Something is wrong", "detail": 7}, "plain text"]
    result = make(spec, test_credentials, reply(400, body)).submit(make(spec, test_credentials, reply(400, body)).create_invoice(simple_invoice))
    assert [(e.code, e.message, e.help_url) for e in result.errors] == [(None, "Something is wrong", None), (None, "plain text", None)]
    assert result.raw["validationResults"]["errorMessages"][0]["detail"] == 7  # nothing ZATCA sent is lost


def test_clearance_returns_zatcas_copy(spec, test_credentials, simple_invoice, buyer):
    stamped = "<Invoice>stamped</Invoice>"
    zatca = make(spec, test_credentials, reply(200, verdict({"clearanceStatus": "CLEARED", "clearedInvoice": base64.b64encode(stamped.encode()).decode()})))
    result = zatca.submit(zatca.create_invoice({**simple_invoice, "type": "standard", "buyer": buyer}))
    assert result.success and result.status == "CLEARED" and result.operation == "clearance"
    assert result.cleared_xml == stamped and result.xml == stamped


def test_an_unreadable_cleared_copy_is_a_warning_not_a_loss(spec, test_credentials, simple_invoice, buyer):
    zatca = make(spec, test_credentials, reply(200, verdict({"clearanceStatus": "CLEARED", "clearedInvoice": "%%%not base64"})))
    result = zatca.submit(zatca.create_invoice({**simple_invoice, "type": "standard", "buyer": buyer}))
    assert result.success and result.cleared_xml is None
    assert [w.code for w in result.warnings] == ["cleared_xml_unreadable"]
    assert result.raw["clearedInvoice"] == "%%%not base64"


def test_the_wrong_door_is_refused_before_sending(spec, test_credentials, simple_invoice):
    zatca = make(spec, test_credentials, lambda r: pytest.fail("nothing should be sent"))
    with pytest.raises(ValidationError):
        zatca.clear(zatca.create_invoice(simple_invoice))
    with pytest.raises(ValidationError):
        zatca.submit({"type": "simplified"})


# -- no verdict: the request failed, the invoice was not judged ---------------------------


def test_refused_credentials(spec, test_credentials, simple_invoice):
    result = make(spec, test_credentials, reply(401, {})).submit(make(spec, test_credentials, reply(401, {})).create_invoice(simple_invoice))
    assert not result.success and result.status == "FAILED" and result.http_status == 401
    assert (result.error.code, result.error.source) == ("authentication_error", "zatca")
    assert result.error.help_url == f"{SITE}/docs/sdk/advanced#authentication_error"


def test_a_server_error(spec, test_credentials, simple_invoice):
    zatca = make(spec, test_credentials, lambda r: httpx.Response(503, text="maintenance"))
    result = zatca.submit(zatca.create_invoice(simple_invoice))
    assert result.status == "FAILED" and result.http_status == 503 and result.error.code == "zatca_service_error"


def test_a_400_without_validation_is_a_refused_request_not_a_rejection(spec, test_credentials, simple_invoice):
    zatca = make(spec, test_credentials, reply(400, {"code": "Invalid-Request", "message": "Malformed body", "errors": [{"code": "X-1", "message": "bad"}]}))
    result = zatca.submit(zatca.create_invoice(simple_invoice))
    assert result.status == "FAILED" and result.status != "NOT_REPORTED"
    assert [e.code for e in result.errors] == ["zatca_request_error", "X-1"]


def test_a_success_without_a_verdict_is_not_taken_for_one(spec, test_credentials, simple_invoice):
    zatca = make(spec, test_credentials, reply(200, {"hello": "world"}))
    result = zatca.submit(zatca.create_invoice(simple_invoice))
    assert not result.success and result.status == "FAILED" and result.error.code == "zatca_service_error"


@pytest.mark.parametrize("error, status", [(httpx.ConnectError("refused"), "NOT_SENT"), (httpx.ReadTimeout("slow"), "UNKNOWN")])
def test_network_errors_say_whether_zatca_may_have_it(spec, test_credentials, simple_invoice, error, status):
    def handler(request):
        raise error

    zatca = make(spec, test_credentials, handler)
    result = zatca.submit(zatca.create_invoice(simple_invoice))
    assert not result.success and result.status == status
    assert (result.error.code, result.error.source) == ("network_error", "network")
    assert result.error.help_url == f"{SITE}/docs/sdk/advanced#network_error"


# -- onboarding ---------------------------------------------------------------------------


def mocked_zatca(test_credentials, calls, compliance=None):
    token = base64.b64encode(test_credentials["certificate"].encode()).decode()

    def handler(request):
        calls.append((request.method, request.url.path, request.headers.get("OTP")))
        if request.url.path.endswith("/compliance"):
            assert base64.b64decode(json.loads(request.content)["csr"]).startswith(b"-----BEGIN CERTIFICATE REQUEST-----")
            return httpx.Response(200, json={"binarySecurityToken": token, "secret": "s3cret", "requestID": 1234567890123})
        if request.url.path.endswith("/compliance/invoices"):
            return httpx.Response(200, json=compliance or verdict({"reportingStatus": "REPORTED"}))
        if request.url.path.endswith("/production/csids"):
            if request.method == "POST":
                assert json.loads(request.content) == {"compliance_request_id": "1234567890123"}
            return httpx.Response(200, json={"binarySecurityToken": token, "secret": "prod" if request.method == "POST" else "renewed", "requestID": 30368})
        raise AssertionError(request.url)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_onboard_runs_every_step_in_one_call(test_credentials):
    calls = []
    zatca = Zatca("sandbox", http_client=mocked_zatca(test_credentials, calls))
    credentials = zatca.onboard()
    assert credentials.secret == "prod" and zatca.credentials is credentials
    assert calls[0] == ("POST", "/e-invoicing/developer-portal/compliance", "123345")  # the sandbox's public OTP
    assert [c[1].rsplit("/", 1)[-1] for c in calls] == ["compliance"] + ["invoices"] * 6 + ["csids"]
    assert zatca.chain.icv == 0  # a new unit starts a new chain
    assert "PRIVATE KEY" in credentials.export()["private_key"]


def test_onboarding_outside_the_sandbox_needs_the_otp(spec, test_credentials):
    zatca = Zatca("production", seller=spec["seller"], http_client=mocked_zatca(test_credentials, calls := []))
    with pytest.raises(ValidationError) as exc:
        zatca.onboard()
    assert exc.value.errors[0]["field"] == "otp" and calls == []


def test_a_failed_compliance_check_stops_onboarding(test_credentials):
    rejected = verdict({"reportingStatus": "NOT_REPORTED"}, errors=[("BR-KSA-37", "Building number")])
    zatca = Zatca("sandbox", http_client=mocked_zatca(test_credentials, [], compliance=rejected))
    with pytest.raises(ComplianceCheckError) as exc:
        zatca.onboard()
    assert exc.value.code == "compliance_checks_failed" and exc.value.errors[0].code == "BR-KSA-37"
    assert zatca.credentials is None


def test_the_steps_one_by_one(spec, test_credentials):
    calls = []
    zatca = Zatca("sandbox", http_client=mocked_zatca(test_credentials, calls))
    keys = zatca.onboarding().generate_csr(CsrRequest(vat_number="399999999900003", organization_name="Co", organization_unit="Main", location="RRRD2929", industry="Retail"))
    compliance = zatca.onboarding().request_compliance_csid(keys, "123345")
    assert compliance.certificate == test_credentials["certificate"] and compliance.request_id == "1234567890123"

    # Signed with the test key the mocked certificate belongs to.
    report = zatca.onboarding().run_compliance_checks(compliance.credentials(test_credentials["private_key"]))
    assert report.passed and len(report.checks) == 6
    assert [c.invoice.kind for c in report.checks] == ["invoice", "credit", "debit"] * 2
    assert [c.invoice.icv for c in report.checks] == [1, 2, 3, 4, 5, 6]
    assert all(report.checks[i].invoice.pih == report.checks[i - 1].invoice.hash for i in range(1, 6))

    production = zatca.onboarding().request_production_csid(compliance)
    assert production.kind == "production" and production.secret == "prod"
    assert "secret" not in production.to_dict()


def test_renewal_keeps_the_chain(test_credentials):
    calls = []
    zatca = Zatca("sandbox", credentials=test_credentials, http_client=mocked_zatca(test_credentials, calls))
    zatca.create_invoice({"type": "simplified", "number": "INV-1", "items": [{"name": "x", "quantity": 1, "unit_price": 1}]})
    before = zatca.chain
    renewed = zatca.renew()
    assert calls == [("PATCH", "/e-invoicing/developer-portal/production/csids", "123345")]
    assert renewed.secret == "renewed" and zatca.chain == before


@pytest.mark.parametrize("body", [
    {"errors": [{"code": "Invalid-OTP", "message": "The provided OTP is invalid"}]},  # what the sandbox answered on 2026-10-03
    {"errorCode": "Invalid-OTP", "errorMessage": "The provided OTP is invalid"},
])
def test_an_invalid_otp(body):
    zatca = Zatca("sandbox", http_client=httpx.Client(transport=httpx.MockTransport(reply(400, body))))
    with pytest.raises(ZatcaRequestError) as exc:
        zatca.onboarding().request_compliance_csid("-----BEGIN CERTIFICATE REQUEST-----", "000000")
    error = exc.value
    assert error.http_status == 400 and error.errors[0].code == "Invalid-OTP"
    assert error.help_url == f"{SITE}/docs/sdk/setup#otp"
    assert error.to_dict()["error"]["errors"][0]["message"] == "The provided OTP is invalid"


def test_no_request_goes_anywhere_but_zatca(spec, test_credentials, simple_invoice):
    hosts = set()

    def handler(request):
        hosts.add(request.url.host)
        return httpx.Response(200, json=verdict({"reportingStatus": "REPORTED"}))

    for environment in ("sandbox", "simulation", "production"):
        zatca = make(spec, test_credentials, handler, environment)
        zatca.submit(zatca.create_invoice(simple_invoice))
    assert hosts == {"gw-fatoora.zatca.gov.sa"}
