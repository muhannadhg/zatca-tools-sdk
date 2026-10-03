# ZATCA Tools SDK

[![tests](https://github.com/muhannadhg/zatca-tools-sdk/actions/workflows/tests.yml/badge.svg)](https://github.com/muhannadhg/zatca-tools-sdk/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/zatca-tools-sdk)](https://pypi.org/project/zatca-tools-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/zatca-tools-sdk)](https://pypi.org/project/zatca-tools-sdk/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**ZATCA e-invoicing for Saudi Arabia, in Python** — Fatoora Phase 2 from your
own application. Create an invoice, send it to ZATCA, read the answer, and
print it as PDF/A-3. By [ZATCA Tools](https://zatcatools.com).

Free and open source (MIT). Runs inside your application and talks to ZATCA
directly: no ZATCA Tools account, no server of ours in between.

Documentation: [Start here](https://zatcatools.com/docs/sdk) ·
[Connect to ZATCA](https://zatcatools.com/docs/sdk/setup) ·
[Advanced](https://zatcatools.com/docs/sdk/advanced) ·
[ZATCA error reference](https://zatcatools.com/en/docs/errors)

> A free, open-source Python library for connecting to the Zakat, Tax and Customs
> Authority (ZATCA) through its Fatoora platform — Phase 2 of Saudi e-invoicing:
> create and sign invoices with their QR code, clear and report them, and print
> PDF/A-3 invoices in Arabic and English. It runs inside your system and connects
> to ZATCA directly. Documentation: [zatcatools.com/docs/sdk](https://zatcatools.com/docs/sdk)

## Install

Python 3.10+.

```bash
pip install zatca-tools-sdk
```

## Your first invoice

Against ZATCA's public sandbox, with nothing to set up:

```python
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
```

- `create_invoice()` checks the data, computes VAT and totals, builds and signs
  the XML, and makes the QR code — on your machine. Nothing is sent.
- `submit()` sends it to ZATCA: reporting for a simplified invoice, clearance
  for a standard one. It never raises for the outcome; read the result.

## Reading the result

| | |
|---|---|
| `result.success` | True only when ZATCA reported or cleared the invoice |
| `result.status` | `REPORTED` · `CLEARED` · `NOT_REPORTED` · `NOT_CLEARED` (rejected) · `NOT_SENT` (no connection) · `UNKNOWN` (no answer: send the same invoice again) · `FAILED` (ZATCA refused the request, not the invoice) |
| `result.error.code` | ZATCA's code (`BR-KSA-63`) or the SDK's own (`network_error`) |
| `result.error.message` | why, in one sentence |
| `result.error.help_url` | the page that explains how to fix it |

Help links point into the [ZATCA error reference](https://zatcatools.com/en/docs/errors)
and this SDK's documentation. They are built offline from a list of codes
shipped with the SDK, and carry the code and nothing else.

## Your own certificate

The sandbox lends everyone a test certificate. To send real invoices, your
system needs its own — one call, with a one-time password (OTP) that only the
taxpayer can generate on the Fatoora portal:

```python
zatca = Zatca("production", seller={
    "vat_number": "310000000000003",
    "name": "My Company LLC",
    "cr_number": "1010010000",
    "address": {"street": "King Fahd Road", "building_number": "1234", "district": "Al Olaya", "city": "Riyadh", "postal_code": "12345"},
})
credentials = zatca.onboard(otp="123456")
```

Keep `credentials.export()` as a secret, and save `zatca.chain.to_dict()` after
every invoice; pass both back on the next start:

```python
zatca = Zatca("production", seller=SELLER, credentials=saved_credentials, chain=saved_chain)
```

Everything else — environments, what to do with each status, renewal — is in
[Connect to ZATCA](https://zatcatools.com/docs/sdk/setup). Run
[`examples/connect.py`](examples/connect.py) to see the whole lifecycle.

## Printed invoices

`save_pdf()` writes PDF/A-3 with the signed XML embedded, Arabic and English,
validated by veraPDF as PDF/A-3b. Brand it:

```python
zatca = Zatca("sandbox", design={"logo": "logo.png", "accent": "#0F766E", "footer": "Thank you for your business."})
```

A standard invoice is printed from its result once ZATCA has cleared it
(`result.save_pdf(...)`), so the PDF carries ZATCA's cleared copy.

## Your data stays with you

- `create_invoice()` and `save_pdf()` make no network call.
- Network calls go only to ZATCA (`gw-fatoora.zatca.gov.sa`): `onboard()`,
  `renew()`, `submit()` and the advanced calls behind them.
- Nothing goes to ZATCA Tools — no telemetry, no analytics, no logs.
- The private key is created on your machine and never sent.

## Tests

```bash
pip install -e ".[dev]"
pytest                                             # offline
ZATCA_SDK_SANDBOX=1 pytest tests/test_sandbox.py   # live, against ZATCA's public sandbox
ZATCA_SDK_VERAPDF=/path/to/verapdf pytest tests/test_pdf.py -k verapdf
```

The offline suite compares the SDK, invoice by invoice, with the pipeline the
ZATCA Tools platform signs production invoices with — same totals, XML, hash
and QR for 17 scenarios, the same settlement for 500 random baskets — and
checks every code snippet in the documentation against the real API.

## Contributing and security

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
Report vulnerabilities privately: [SECURITY.md](SECURITY.md).

Prefer not to run your own integration? [ZATCA Tools](https://zatcatools.com)
is the hosted platform built on the same engine: dashboard, Shopify and
WooCommerce integrations, and a REST API.

## Licence

MIT — see [LICENSE](LICENSE) and [NOTICE](NOTICE) (including the bundled IBM
Plex Sans Arabic font, SIL Open Font License, and the ZATCA Tools mark).
Independent project; not affiliated with or endorsed by the Zakat, Tax and
Customs Authority.
