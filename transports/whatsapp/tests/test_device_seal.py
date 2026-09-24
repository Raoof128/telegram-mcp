import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from whatsvault.crypto import device_seal as DS


def _dev():
    p = ec.generate_private_key(ec.SECP256R1())
    pub = p.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return p, pub


def test_roundtrip_ciphertext_only():
    priv, pub = _dev()
    env = DS.seal(pub, b"WV_DEVICE_SEAL_SENTINEL", aad=b"drf_1")
    assert b"WV_DEVICE_SEAL_SENTINEL" not in env  # sealed
    assert DS.open_sealed(priv, env, aad=b"drf_1") == b"WV_DEVICE_SEAL_SENTINEL"


def test_aad_mismatch_fails():
    priv, pub = _dev()
    env = DS.seal(pub, b"x", aad=b"a")
    with pytest.raises(DS.DeviceSealError):
        DS.open_sealed(priv, env, aad=b"b")
