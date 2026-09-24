"""Mac -> iPhone device seal (spec §6 INV-DEVICE-SEAL, ledger #5). Ephemeral P-256 ->
ECDH with the device's Secure-Enclave KEY-AGREEMENT key -> HKDF-SHA256 -> AES-256-GCM.
Distinct from the X25519 edge seal: the agreement key is a P-256 SE key, so the Tunnel
carries ciphertext the Mac cannot read after sealing."""

import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"WVD1"
_INFO = b"WHATSVAULT-DEVICE-SEAL-V1"
_EPH_END = 4 + 65
_NONCE_END = _EPH_END + 12
_CTLEN_END = _NONCE_END + 4


class DeviceSealError(Exception):
    pass


def seal(agreement_pub_sec1: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    eph = ec.generate_private_key(ec.SECP256R1())
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), agreement_pub_sec1)
    k = HKDF(hashes.SHA256(), 32, None, _INFO).derive(eph.exchange(ec.ECDH(), pub))
    nonce = os.urandom(12)
    ephpub = eph.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    ct = AESGCM(k).encrypt(nonce, plaintext, aad)
    return MAGIC + ephpub + nonce + struct.pack(">I", len(ct)) + ct


def open_sealed(agreement_priv, envelope: bytes, aad: bytes = b"") -> bytes:
    if envelope[:4] != MAGIC:
        raise DeviceSealError("bad device envelope")
    ephpub = envelope[4:_EPH_END]
    nonce = envelope[_EPH_END:_NONCE_END]
    clen = struct.unpack(">I", envelope[_NONCE_END:_CTLEN_END])[0]
    ct = envelope[_CTLEN_END : _CTLEN_END + clen]
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ephpub)
    k = HKDF(hashes.SHA256(), 32, None, _INFO).derive(agreement_priv.exchange(ec.ECDH(), pub))
    try:
        return AESGCM(k).decrypt(nonce, ct, aad)
    except InvalidTag as exc:
        raise DeviceSealError("device seal auth failed") from exc
