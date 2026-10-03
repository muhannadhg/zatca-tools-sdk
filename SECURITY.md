# Security policy

Please report vulnerabilities privately to **security@zatcatools.com** (or support@zatcatools.com), not in public issues. Include what you found, how to reproduce it and the version. We acknowledge within three working days.

## What the SDK does with sensitive material

- The private key is generated locally (`generate_csr`) and is never transmitted.
- The certificate and secret are sent only to ZATCA, as the HTTP Basic credentials its API requires.
- The SDK makes no request to any host other than `gw-fatoora.zatca.gov.sa`, sends no telemetry and writes no logs.
- `Credentials` and `KeyPair` do not print secrets in `repr`; results leave secrets out of `to_dict()` unless asked.

## Your side

Store private keys and secrets in a secret manager or encrypted storage. Never commit them. Do not log full documents or requests if your logs are less protected than your invoicing data.
