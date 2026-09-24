import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from whatsvault.approval import capabilities as CAP
from whatsvault.approval import devices as D
from whatsvault.approval import sender
from whatsvault.approval import verify as V
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.providers.fake_meta import FakeMeta


def _sec1(p):
    return p.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def _msg(v, mid, conv, wamid, direction):
    v.execute(
        "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
        "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible, wamid) "
        "VALUES(?, 'acc',?,?,1,2,'ms','text','x','cloud_api',0,?)",
        (mid, conv, direction, wamid),
    )


def _setup(tmp_path, *, with_grant=True):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    v.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','PN1')")
    v.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    v.execute("INSERT INTO conversations(id, account_id, type) VALUES('other','acc','dm')")
    _msg(v, "m_in", "cnv", "wamid.IN", "in")
    _msg(v, "m_out", "cnv", "wamid.OUT", "out")
    _msg(v, "m_other", "other", "wamid.OTHER", "in")
    v.commit()
    sign = ec.generate_private_key(ec.SECP256R1())
    did = D.enroll(
        c, "iphone", signing_pub=_sec1(sign), agreement_pub=_sec1(ec.generate_private_key(ec.SECP256R1()))
    )
    if with_grant:
        f = {
            "capability_id": "cap_1",
            "device_id": did,
            "account_id": "acc",
            "conversation_id": "cnv",
            "action": "MARK_READ",
            "created_at_ms": 1,
            "expires_at_ms": 10**12,
            "max_actions": 5,
            "nonce": os.urandom(32),
        }
        CAP.store_grant(c, f, V.sign_for_test(CAP.encode_grant(f), sign))
    return v, c


def test_valid_inbound_marks_read_and_consumes(tmp_path):
    v, c = _setup(tmp_path)
    fm = FakeMeta()
    r = sender.mark_read(v, c, fm, conversation_id="cnv", wamid="wamid.IN", account_id="acc", now_ms=100)
    assert r["outcome"] == "OK" and fm.mark_reads == ["wamid.IN"]
    assert c.execute("SELECT used_count FROM capability_grants").fetchone()[0] == 1


def test_wamid_from_other_conversation_rejected(tmp_path):
    v, c = _setup(tmp_path)
    r = sender.mark_read(
        v, c, FakeMeta(), conversation_id="cnv", wamid="wamid.OTHER", account_id="acc", now_ms=100
    )
    assert r["reason"] == "TARGET_CONVERSATION_MISMATCH"
    assert c.execute("SELECT used_count FROM capability_grants").fetchone()[0] == 0  # not consumed


def test_outbound_wamid_rejected(tmp_path):
    v, c = _setup(tmp_path)
    r = sender.mark_read(
        v, c, FakeMeta(), conversation_id="cnv", wamid="wamid.OUT", account_id="acc", now_ms=100
    )
    assert r["reason"] == "NOT_INBOUND"


def test_no_grant_authorization_missing(tmp_path):
    v, c = _setup(tmp_path, with_grant=False)
    r = sender.mark_read(
        v, c, FakeMeta(), conversation_id="cnv", wamid="wamid.IN", account_id="acc", now_ms=100
    )
    assert r["reason"] == "AUTHORIZATION_MISSING"
