"""Sealed edge-relay envelope (spec §2.1/§7, ledger #1).

X25519 (ephemeral) -> HKDF-SHA256 -> AES-256-GCM. EVERY field bound as AAD is ALSO
on the wire, so the Mac reconstructs the exact AAD from the envelope alone (this is
the fix for the original undecryptable format). AAD = the header prefix through
event_id_hash. open_sealed raises three distinct errors so the failure taxonomy
(#37) can tell transient (no key) from poison (bad tag / malformed)."""

import hashlib
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"WVE1"
ALG_X25519_HKDF_AESGCM = 1
_INFO = b"WHATSVAULT-EDGE-SEAL-V1"
_HDR_LEN = 4 + 1 + 1 + 1 + 4 + 32  # magic|env_ver|alg|cver|key_id|event_id_hash = 43
_EPH_END = _HDR_LEN + 32
_NONCE_END = _EPH_END + 12
_CTLEN_END = _NONCE_END + 4


class SealedError(Exception):
    pass


class BadEnvelope(SealedError):
    pass


class KeyUnavailable(SealedError):
    pass


class AeadAuthFailed(SealedError):
    pass


def _header_bytes(env_ver, alg, cver, key_id, event_id_hash) -> bytes:
    if len(event_id_hash) != 32:
        raise ValueError("event_id_hash must be 32 bytes")
    return MAGIC + bytes([env_ver, alg, cver]) + struct.pack(">I", key_id) + event_id_hash


def seal(
    recipient_pub,
    plaintext,
    *,
    recipient_key_id,
    event_id_hash,
    crypto_version=1,
    algorithm_id=ALG_X25519_HKDF_AESGCM,
    envelope_version=1,
) -> bytes:
    eph = X25519PrivateKey.generate()
    shared = eph.exchange(X25519PublicKey.from_public_bytes(recipient_pub))
    aeskey = HKDF(hashes.SHA256(), 32, None, _INFO).derive(shared)
    nonce = os.urandom(12)
    aad = _header_bytes(envelope_version, algorithm_id, crypto_version, recipient_key_id, event_id_hash)
    ct = AESGCM(aeskey).encrypt(nonce, plaintext, aad)
    return aad + eph.public_key().public_bytes_raw() + nonce + struct.pack(">I", len(ct)) + ct


def parse_header(envelope) -> dict:
    if len(envelope) < _CTLEN_END or envelope[:4] != MAGIC:
        raise BadEnvelope("magic/length")
    env_ver, alg, cver = envelope[4], envelope[5], envelope[6]
    key_id = struct.unpack(">I", envelope[7:11])[0]
    event_id_hash = envelope[11:43]
    ct_len = struct.unpack(">I", envelope[_NONCE_END:_CTLEN_END])[0]
    ct = envelope[_CTLEN_END : _CTLEN_END + ct_len]
    if len(ct) != ct_len:
        raise BadEnvelope("truncated ciphertext")
    return {
        "envelope_version": env_ver,
        "algorithm_id": alg,
        "crypto_version": cver,
        "recipient_key_id": key_id,
        "event_id_hash": event_id_hash,
        "ciphertext_sha256": hashlib.sha256(ct).hexdigest(),
    }


def open_sealed(envelope, key_lookup):
    hdr = parse_header(envelope)  # raises BadEnvelope on malformed
    aad = envelope[:_HDR_LEN]
    ephpub = envelope[_HDR_LEN:_EPH_END]
    nonce = envelope[_EPH_END:_NONCE_END]
    ct_len = struct.unpack(">I", envelope[_NONCE_END:_CTLEN_END])[0]
    ct = envelope[_CTLEN_END : _CTLEN_END + ct_len]
    priv = key_lookup(hdr["recipient_key_id"])
    if priv is None:
        raise KeyUnavailable(f"no private key for recipient_key_id {hdr['recipient_key_id']}")
    try:
        shared = X25519PrivateKey.from_private_bytes(priv).exchange(X25519PublicKey.from_public_bytes(ephpub))
        aeskey = HKDF(hashes.SHA256(), 32, None, _INFO).derive(shared)
        pt = AESGCM(aeskey).decrypt(nonce, ct, aad)
    except InvalidTag as exc:
        raise AeadAuthFailed("AEAD tag verification failed") from exc
    return pt, hdr
