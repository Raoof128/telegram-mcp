"""Versioned AES-256-GCM sealing for attachment blobs at rest (INV-ATREST).
Envelope: magic(4) || version(1) || key_id(4, big-endian) || nonce(12) || ct||tag."""

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ATTACHMENT_KEY_NAME = "whatsvault.attachment.key.v1"
MAGIC = b"WVA1"
_VERSION = 1
_NONCE_LEN = 12
_HEADER_LEN = 4 + 1 + 4 + _NONCE_LEN


def seal_blob(key: bytes, plaintext: bytes, key_id: int = 0, aad: bytes = b"") -> bytes:
    nonce = os.urandom(_NONCE_LEN)
    header = MAGIC + bytes([_VERSION]) + key_id.to_bytes(4, "big") + nonce
    ct = AESGCM(key).encrypt(nonce, plaintext, header + aad)
    return header + ct


def open_blob(key: bytes, sealed: bytes, aad: bytes = b"") -> bytes:
    if len(sealed) < _HEADER_LEN or sealed[:4] != MAGIC:
        raise ValueError("not a WhatsVault attachment envelope")
    if sealed[4] != _VERSION:
        raise ValueError(f"unsupported envelope version {sealed[4]}")
    header = sealed[:_HEADER_LEN]
    nonce = sealed[9:_HEADER_LEN]
    ct = sealed[_HEADER_LEN:]
    return AESGCM(key).decrypt(nonce, ct, header + aad)
