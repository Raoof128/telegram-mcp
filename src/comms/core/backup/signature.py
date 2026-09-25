"""``comms-backup-signature/v1``: a detached Ed25519 signature over a backup ciphertext (B24).

``signing_input = "comms-backup-signature/v1\\0" ‖ SHA-256(ciphertext)``. The sidecar is
strict JCS with exactly six keys; its key ID is recomputed from the public key, never
trusted. Whether the signer is trusted for import is the caller's question
(``comms.core.keys.signers``); this module only proves who signed what.
"""

from __future__ import annotations

import hashlib
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from comms.core import domains
from comms.core.canonical import jcs_dumps
from comms.core.keys import ids
from comms.core.strict_json import strict_json_loads

__all__ = ["SCHEMA", "SignatureError", "sign", "signing_input", "verify"]

SCHEMA = domains.BACKUP_SIGNATURE.rstrip(b"\0").decode()  # one copy of the name
_KEYS = {"alg", "ciphertext_sha256", "key_id", "public_key", "schema", "signature"}


class SignatureError(Exception):
    """A backup signature was refused. Fixed messages."""


def signing_input(ciphertext: bytes) -> bytes:
    return domains.BACKUP_SIGNATURE + hashlib.sha256(ciphertext).digest()


def sign(ciphertext: bytes, private_seed: bytes) -> bytes:
    key = Ed25519PrivateKey.from_private_bytes(private_seed)
    public = key.public_key().public_bytes_raw()
    return jcs_dumps(
        {
            "alg": "ed25519",
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "key_id": ids.ed25519_key_id(public),
            "public_key": public.hex(),
            "schema": SCHEMA,
            "signature": key.sign(signing_input(ciphertext)).hex(),
        }
    )


def verify(ciphertext: bytes, sidecar: bytes) -> str:
    """Return the signer's recomputed key ID, or raise ``SignatureError``."""
    try:
        body: Any = strict_json_loads(sidecar.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise SignatureError("sidecar is not strict JSON") from None
    if not isinstance(body, dict) or set(body) != _KEYS or jcs_dumps(body) != sidecar:
        raise SignatureError("sidecar is not the canonical schema")
    if body["schema"] != SCHEMA or body["alg"] != "ed25519":
        raise SignatureError("unsupported signature schema")
    if body["ciphertext_sha256"] != hashlib.sha256(ciphertext).hexdigest():
        raise SignatureError("ciphertext does not match its signature")
    try:
        public = bytes.fromhex(body["public_key"])
        signature = bytes.fromhex(body["signature"])
    except (TypeError, ValueError):
        raise SignatureError("sidecar encoding refused") from None
    if len(public) != 32 or ids.ed25519_key_id(public) != body["key_id"]:
        raise SignatureError("key id does not recompute from the public key")
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, signing_input(ciphertext))
    except (InvalidSignature, ValueError):
        raise SignatureError("signature does not verify") from None
    return str(body["key_id"])
