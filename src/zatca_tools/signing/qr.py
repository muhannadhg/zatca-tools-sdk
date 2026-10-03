"""The QR code ZATCA requires: TLV (tag, length, value) fields, base64-encoded.

Tags: 1 seller name · 2 VAT number · 3 timestamp · 4 total with VAT · 5 VAT total ·
6 invoice hash · 7 ECDSA signature · 8 public key · 9 the certificate's signature
(simplified documents only; a cleared standard invoice's QR has eight tags).
"""

from __future__ import annotations

import base64
from typing import Union

from ..errors import ValidationError

NAMES = {
    1: "seller_name",
    2: "vat_number",
    3: "timestamp",
    4: "total",
    5: "vat_total",
    6: "invoice_hash",
    7: "signature",
    8: "public_key",
    9: "certificate_signature",
}


def encode(fields: list[tuple[int, Union[str, bytes]]]) -> str:
    out = bytearray()
    for tag, value in fields:
        data = value.encode("utf-8") if isinstance(value, str) else value
        if len(data) > 255:
            raise ValidationError(
                f"QR field {NAMES.get(tag, tag)} is {len(data)} bytes; a QR field holds at most 255.",
                [{"field": NAMES.get(tag, str(tag)), "message": "Too long for the QR code (255 bytes)."}],
            )
        out += bytes([tag, len(data)]) + data
    return base64.b64encode(bytes(out)).decode()


def decode(qr: str) -> dict[str, str]:
    """Read a ZATCA QR back: text fields as text, binary fields (7–9 may be binary) as base64."""
    raw = base64.b64decode(qr)
    fields: dict[str, str] = {}
    i = 0
    while i + 2 <= len(raw):
        tag, length = raw[i], raw[i + 1]
        value = raw[i + 2 : i + 2 + length]
        if len(value) != length:
            raise ValueError("Truncated QR field")
        name = NAMES.get(tag, f"tag_{tag}")
        if tag in (8, 9):
            fields[name] = base64.b64encode(value).decode()
        else:
            try:
                fields[name] = value.decode("utf-8")
            except UnicodeDecodeError:
                fields[name] = base64.b64encode(value).decode()
        i += 2 + length
    return fields
