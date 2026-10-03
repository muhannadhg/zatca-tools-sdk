# Test fixtures

`test_private_key.pem` and `test_certificate.txt` are a **test-only** key pair,
generated for this test suite and self-signed by `TEST-ONLY-SDK-CA`. They were
never issued by ZATCA, are valid nowhere, and protect nothing.

`reference/` holds what the ZATCA Tools platform produced for the scenarios in
`scenarios.json`: the SDK is compared against it byte for byte. These files are
kept exactly as produced (see `.gitattributes`).
