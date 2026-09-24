import hashlib
import json
import pathlib

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from whatsvault.crypto import sealed as S


def _keypair():
    p = X25519PrivateKey.generate()
    return p.private_bytes_raw(), p.public_key().public_bytes_raw()


def test_roundtrip_recovers_plaintext_and_header():
    priv, pub = _keypair()
    eidh = hashlib.sha256(b"evt").digest()
    env = S.seal(pub, b"hello world", recipient_key_id=7, event_id_hash=eidh)
    pt, hdr = S.open_sealed(env, lambda k: priv if k == 7 else None)
    assert pt == b"hello world"
    assert hdr["recipient_key_id"] == 7 and hdr["event_id_hash"] == eidh and hdr["crypto_version"] == 1


def test_aad_tamper_fails():
    priv, pub = _keypair()
    env = bytearray(S.seal(pub, b"x", recipient_key_id=1, event_id_hash=b"\0" * 32))
    env[6] ^= 1  # crypto_version byte is AAD
    with pytest.raises(S.AeadAuthFailed):
        S.open_sealed(bytes(env), lambda k: priv)


def test_key_unavailable_distinct_from_auth_failed():
    priv, pub = _keypair()
    env = S.seal(pub, b"x", recipient_key_id=1, event_id_hash=b"\0" * 32)
    with pytest.raises(S.KeyUnavailable):
        S.open_sealed(env, lambda k: None)  # transient
    other, _ = _keypair()
    with pytest.raises(S.AeadAuthFailed):
        S.open_sealed(env, lambda k: other)  # wrong key present


def test_bad_magic_is_bad_envelope():
    with pytest.raises(S.BadEnvelope):
        S.open_sealed(b"XXXX" + b"\0" * 100, lambda k: b"\0" * 32)


def test_parse_header_needs_no_key():
    priv, pub = _keypair()
    eidh = hashlib.sha256(b"e").digest()
    env = S.seal(pub, b"payload", recipient_key_id=9, event_id_hash=eidh)
    h = S.parse_header(env)
    assert h["recipient_key_id"] == 9 and h["crypto_version"] == 1 and h["event_id_hash"] == eidh
    assert len(h["ciphertext_sha256"]) == 64 and h["envelope_version"] == 1


def test_golden_vector_opens():
    v = json.loads((pathlib.Path(__file__).parent / "golden" / "sealed_vectors.json").read_text())
    priv, env = bytes.fromhex(v["recipient_priv"]), bytes.fromhex(v["envelope"])
    pt, hdr = S.open_sealed(env, lambda k: priv if k == v["recipient_key_id"] else None)
    assert pt == v["plaintext"].encode() and hdr["recipient_key_id"] == v["recipient_key_id"]
