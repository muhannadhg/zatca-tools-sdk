"""Reading the signing certificate and key, in the shapes ZATCA's signature format needs."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ..errors import SigningError


def _body(certificate: str) -> str:
    """The certificate as one base64 line, whatever it came in as (PEM, base64 with breaks)."""
    text = certificate.strip()
    if "BEGIN CERTIFICATE" in text:
        text = re.sub(r"-----(BEGIN|END) CERTIFICATE-----", "", text)
    return re.sub(r"\s+", "", text)


@dataclass(frozen=True)
class SigningIdentity:
    """A loaded certificate and private key, ready to sign documents."""

    raw_certificate: str
    certificate: x509.Certificate
    private_key: ec.EllipticCurvePrivateKey
    key_matches_certificate: bool = True

    @classmethod
    def load(cls, certificate: str, private_key: str, strict: bool = True) -> "SigningIdentity":
        """Load a certificate and its private key.

        ``strict`` refuses a key that does not belong to the certificate — a document
        signed that way fails ZATCA's signature check. The sandbox is the exception:
        its production CSID is a fixed sample certificate that matches no one's key,
        and the sandbox accepts documents signed alongside it.
        """
        raw = _body(certificate)
        try:
            cert = x509.load_der_x509_certificate(base64.b64decode(raw))
        except Exception as exc:  # noqa: BLE001 - any parse failure is the same problem for the caller
            raise SigningError("The certificate could not be read. Pass the certificate exactly as ZATCA issued it (the decoded binarySecurityToken) or as PEM.") from exc
        try:
            key = serialization.load_pem_private_key(private_key.encode() if isinstance(private_key, str) else private_key, password=None)
        except Exception as exc:  # noqa: BLE001
            raise SigningError("The private key could not be read. Pass the PEM generated with the CSR.") from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256k1":
            raise SigningError("ZATCA signatures use an EC key on the secp256k1 curve.")
        matches = key.public_key().public_numbers() == cert.public_key().public_numbers()  # type: ignore[union-attr]
        if strict and not matches:
            raise SigningError("The private key does not belong to this certificate.")
        return cls(raw, cert, key, matches)

    # -- the values the XAdES signature and the QR code carry ------------------------------

    def certificate_hash(self) -> str:
        """``xades:CertDigest`` as ZATCA computes it: base64 of the hex SHA-256 of the base64 certificate text."""
        return base64.b64encode(hashlib.sha256(self.raw_certificate.encode()).hexdigest().encode()).decode()

    def issuer_name(self) -> str:
        return ", ".join(attribute.rfc4514_string() for rdn in reversed(self.certificate.issuer.rdns) for attribute in rdn)

    def serial_number(self) -> str:
        return str(self.certificate.serial_number)

    def public_key_der(self) -> bytes:
        return self.certificate.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

    def certificate_signature(self) -> bytes:
        """The issuer's signature on the certificate — QR tag 9 on simplified documents."""
        return self.certificate.signature

    def sign(self, digest: bytes) -> str:
        """ECDSA-SHA256 over the invoice hash, DER-encoded, base64."""
        return base64.b64encode(self.private_key.sign(digest, ec.ECDSA(hashes.SHA256()))).decode()

    def expires_at(self) -> Optional[datetime]:
        try:
            return self.certificate.not_valid_after_utc
        except AttributeError:  # cryptography < 42
            return self.certificate.not_valid_after.replace(tzinfo=timezone.utc)
