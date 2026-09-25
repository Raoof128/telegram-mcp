"""Access tokens: compact JWS, EdDSA only (comms v0.3 Task D32; A35).

``header.payload.signature`` in unpadded base64url, the header exactly
``{"alg": "EdDSA", "typ": "at+jwt"}``. Verification refuses any other algorithm (``none``,
``HS256``, …), any other header, any second encoding, and any signature that does not verify
under the given Ed25519 public key; it never trusts the header to pick a key or algorithm.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from comms.core.canonical import jcs_dumps

__all__ = ["HEADER", "public_key_of", "sign_access_token", "verify_access_token"]

HEADER = {"alg": "EdDSA", "typ": "at+jwt"}
_HEADER_BYTES = jcs_dumps(HEADER)


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(text: str) -> bytes | None:
    if not text or not text.isascii():
        return None
    try:
        data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return None
    return data if _encode(data) == text else None


def public_key_of(private_seed: bytes) -> bytes:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key = Ed25519PrivateKey.from_private_bytes(private_seed)
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def sign_access_token(private_seed: bytes, claims: dict[str, Any]) -> str:
    """An EdDSA (Ed25519) compact JWS over the canonical claims."""
    signing_input = f"{_encode(_HEADER_BYTES)}.{_encode(jcs_dumps(claims))}"
    signature = Ed25519PrivateKey.from_private_bytes(private_seed).sign(signing_input.encode())
    return f"{signing_input}.{_encode(signature)}"


def verify_access_token(public_key: bytes, token: str) -> dict[str, Any] | None:
    """The claims of a token this key signed with EdDSA, or None — nothing else is trusted."""
    if not isinstance(token, str) or len(token) > 4096:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header, payload, signature = (_decode(p) for p in parts)
    if header != _HEADER_BYTES or payload is None or signature is None:
        return None  # the algorithm allowlist is exactly EdDSA, in exactly this header
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, f"{parts[0]}.{parts[1]}".encode()
        )
    except (InvalidSignature, ValueError):
        return None
    try:
        claims = json.loads(payload)
    except ValueError:
        return None
    return claims if isinstance(claims, dict) else None
