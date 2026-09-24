import pytest
from cryptography.exceptions import InvalidTag

from whatsvault.crypto import atrest
from whatsvault.crypto.keystore import KeyExists, KeyMissing, MemoryKeyStore


def test_provision_then_require():
    ks = MemoryKeyStore()
    k = ks.provision("attk", 32)
    assert len(k) == 32
    assert ks.require("attk", 32) == k


def test_provision_twice_refuses():
    ks = MemoryKeyStore()
    ks.provision("attk", 32)
    with pytest.raises(KeyExists):
        ks.provision("attk", 32)


def test_require_missing_is_hard_failure():
    with pytest.raises(KeyMissing):
        MemoryKeyStore().require("nope", 32)


def test_require_wrong_length_rejected():
    ks = MemoryKeyStore()
    ks._d["short"] = b"\x00" * 16
    with pytest.raises(ValueError):
        ks.require("short", 32)


def test_seal_open_roundtrip_versioned():
    key = MemoryKeyStore().provision(atrest.ATTACHMENT_KEY_NAME, 32)
    pt = b"an image's raw bytes \x00\xff\x10"
    sealed = atrest.seal_blob(key, pt, key_id=1, aad=b"att_ABC")
    assert sealed[:4] == atrest.MAGIC
    assert atrest.open_blob(key, sealed, aad=b"att_ABC") == pt


def test_tampered_ciphertext_fails():
    key = MemoryKeyStore().provision(atrest.ATTACHMENT_KEY_NAME, 32)
    sealed = bytearray(atrest.seal_blob(key, b"secret media"))
    sealed[-1] ^= 0x01
    with pytest.raises(InvalidTag):
        atrest.open_blob(key, bytes(sealed))


def test_wrong_aad_fails():
    key = MemoryKeyStore().provision(atrest.ATTACHMENT_KEY_NAME, 32)
    sealed = atrest.seal_blob(key, b"m", aad=b"att_ONE")
    with pytest.raises(InvalidTag):
        atrest.open_blob(key, sealed, aad=b"att_TWO")
