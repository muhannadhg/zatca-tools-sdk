# Changelog

## 0.1.3 — 2026-10-03

- README and NOTICE: wording only.

## 0.1.2 — 2026-10-03

- Help links: BR-CO-15 (invoice total with VAT) now opens its own guide in the ZATCA error reference, which documents 138 codes.

## 0.1.1 — 2026-10-03

- README: the Fatoora paragraph in English.

## 0.1.0 — 2026-10-03

First release.

- Three calls: `Zatca(...).onboard()` once per system, `create_invoice()` locally, `submit()` to ZATCA. In the sandbox, `onboard()` needs nothing and the seller defaults to ZATCA's test seller.
- Onboarding in one call: key pair and CSR generated locally, compliance CSID, compliance checks, production CSID; `renew()` before expiry. Every step also available on its own through `onboarding()`.
- Invoices: standard and simplified, credit and debit notes; VAT per treatment (S, Z, E, O) with exemption reasons; prices with or without VAT; item and invoice discounts; half-up rounding on the final figure. Unknown fields and characters XML cannot carry are refused with the field named.
- The invoice chain (ICV, PIH) kept by the client; saved and restored as `zatca.chain.to_dict()`.
- Signing: UBL 2.1 XML, XAdES signature (ECDSA secp256k1), invoice hash, QR code (TLV).
- Results: `success`, `status` (REPORTED, CLEARED, NOT_REPORTED, NOT_CLEARED, NOT_SENT, UNKNOWN, FAILED), `error` and `errors` with `code`, `message`, `source` and `help_url`. `submit()` never raises for the outcome.
- Help links into the ZATCA Tools error reference and these docs, built offline from a shipped list of codes.
- Printed invoices: PDF/A-3b with the XML embedded, Arabic and English, branded with logo, accent colour and footer.
- Verified against ZATCA's sandbox end to end, against the ZATCA Tools production pipeline (17 invoice scenarios byte for byte, 500 random baskets figure for figure), and with veraPDF for PDF/A-3b.
