import json
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from whatsvault.approval import devices as D
from whatsvault.approval import relay as R
from whatsvault.crypto import device_seal as DS
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _sec1(p):
    return p.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def _setup(tmp_path):
    conn = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(conn, "control")
    did = D.enroll(
        conn,
        "iphone",
        signing_pub=_sec1(ec.generate_private_key(ec.SECP256R1())),
        agreement_pub=_sec1(agree := ec.generate_private_key(ec.SECP256R1())),
    )
    conn.execute(
        "INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, recipient_wa_id, "
        "body_bytes, kind, nonce, state) VALUES('drf_1','cnv','acc','PN1','61999',?,'text',?, "
        "'PENDING_APPROVAL')",
        (b"hello", os.urandom(32)),
    )
    conn.commit()
    return conn, did, agree


def test_sealed_draft_detail_is_ciphertext_and_opens(tmp_path):
    conn, did, agree = _setup(tmp_path)
    env = R.sealed_draft_detail(conn, "drf_1", did)
    assert b"61999" not in env
    detail = json.loads(DS.open_sealed(agree, env, aad=b"drf_1"))
    assert detail["recipient_wa_id"] == "61999" and detail["body"] == "hello"


def test_structural_check_rejects_unknown_device(tmp_path):
    conn, did, agree = _setup(tmp_path)
    env = json.dumps(
        {
            "device_id": "dev_nope",
            "draft_id": "drf_1",
            "decision": "APPROVE",
            "signature_hex": "11" * 64,
            "nonce_hex": "bb" * 32,
        }
    ).encode()
    with pytest.raises(R.RelayRejected):
        R.accept_envelope(conn, env)


def test_bad_signature_length_rejected(tmp_path):
    conn, did, agree = _setup(tmp_path)
    env = json.dumps(
        {
            "device_id": did,
            "draft_id": "drf_1",
            "decision": "APPROVE",
            "signature_hex": "11" * 10,
            "nonce_hex": "bb" * 32,
        }
    ).encode()
    with pytest.raises(R.RelayRejected):
        R.accept_envelope(conn, env)


def test_idempotent_accept_writes_no_approved_state(tmp_path):
    conn, did, agree = _setup(tmp_path)
    env = json.dumps(
        {
            "device_id": did,
            "draft_id": "drf_1",
            "decision": "APPROVE",
            "signature_hex": "11" * 64,
            "nonce_hex": "bb" * 32,
        }
    ).encode()
    a1 = R.accept_envelope(conn, env)
    a2 = R.accept_envelope(conn, env)
    assert a1 == a2 and conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1
    assert conn.execute("SELECT state FROM drafts WHERE id='drf_1'").fetchone()[0] == "PENDING_APPROVAL"
