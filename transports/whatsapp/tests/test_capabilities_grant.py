import json
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from whatsvault.approval import capabilities as CAP
from whatsvault.approval import devices as D
from whatsvault.approval import verify as V
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _sec1(p):
    return p.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def _setup(tmp_path):
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    sign = ec.generate_private_key(ec.SECP256R1())
    did = D.enroll(
        c, "iphone", signing_pub=_sec1(sign), agreement_pub=_sec1(ec.generate_private_key(ec.SECP256R1()))
    )
    return c, sign, did


def _grant(did, **kw):
    base = {
        "capability_id": "cap_1",
        "device_id": did,
        "account_id": "acc",
        "conversation_id": "cnv",
        "action": "MARK_READ",
        "created_at_ms": 1000,
        "expires_at_ms": 10000,
        "max_actions": 2,
        "nonce": os.urandom(32),
    }
    base.update(kw)
    return base


def _store(c, sign, fields):
    return CAP.store_grant(c, fields, V.sign_for_test(CAP.encode_grant(fields), sign))


def test_consume_up_to_max_then_refuse(tmp_path):
    c, sign, did = _setup(tmp_path)
    _store(c, sign, _grant(did, max_actions=2))
    assert CAP.verify_and_consume(c, "MARK_READ", "cnv", 2000) is True
    assert CAP.verify_and_consume(c, "MARK_READ", "cnv", 2000) is True
    assert CAP.verify_and_consume(c, "MARK_READ", "cnv", 2000) is False


def test_expired_refuses(tmp_path):
    c, sign, did = _setup(tmp_path)
    _store(c, sign, _grant(did, expires_at_ms=1500))
    assert CAP.verify_and_consume(c, "MARK_READ", "cnv", 2000) is False


def test_conversation_scoped(tmp_path):
    c, sign, did = _setup(tmp_path)
    _store(c, sign, _grant(did))
    assert CAP.verify_and_consume(c, "MARK_READ", "OTHER", 2000) is False


def test_revoked_device_refuses(tmp_path):
    c, sign, did = _setup(tmp_path)
    _store(c, sign, _grant(did))
    D.revoke(c, did)
    assert CAP.verify_and_consume(c, "MARK_READ", "cnv", 2000) is False


def test_store_rejects_bad_signature(tmp_path):
    c, sign, did = _setup(tmp_path)
    with pytest.raises(CAP.GrantRejected):
        CAP.store_grant(c, _grant(did), b"\x00" * 64)


def test_no_mac_mint_path():
    assert not hasattr(CAP, "mint_grant")  # #7: the Mac cannot mint a capability


def test_golden_vector_and_mutation_rejects(tmp_path):
    v = json.loads(
        (__import__("pathlib").Path(__file__).parent / "golden" / "capability_vectors.json").read_text()
    )
    fields = {k: (bytes.fromhex(x) if k == "nonce" else x) for k, x in v["fields"].items()}
    assert CAP.encode_grant(fields).hex() == v["encoded_hex"]
    c, sign, did = _setup(tmp_path)
    f = _grant(did)
    sig = V.sign_for_test(CAP.encode_grant(f), sign)
    escalated = {**f, "action": "SEND"}  # scope escalation attempt
    key = D.active_signing_key(c, did)
    assert V.verify(CAP.encode_grant(escalated), sig, key) is False
