"""Key IDs, ``<kind>:sha256:<64 hex>``: the one rule for both the legacy store and comms.

An HMAC key's ID hashes the secret; an Ed25519 key's ID hashes its raw public key.
IDs may be stored for lookup, but every load recomputes and compares them.
"""

from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

__all__ = ["ed25519_key_id", "ed25519_public", "hmac_key_id"]


def hmac_key_id(secret: bytes) -> str:
    return "hmac:sha256:" + hashlib.sha256(secret).hexdigest()


def ed25519_public(seed: bytes) -> bytes:
    key = Ed25519PrivateKey.from_private_bytes(seed)
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def ed25519_key_id(public: bytes) -> str:
    return "ed25519:sha256:" + hashlib.sha256(public).hexdigest()
