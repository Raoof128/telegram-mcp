import hashlib
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from whatsvault.approval import canonical, devices, sender, verify
from whatsvault.approval.clockguard import ClockGuard
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.providers.fake_meta import FakeMeta


def _sec1(p):
    return p.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def _setup(tmp_path, *, body=b"hello", body_sha=None, window_open=True, revoked=False):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    sign = ec.generate_private_key(ec.SECP256R1())
    did = devices.enroll(
        c, "iphone", signing_pub=_sec1(sign), agreement_pub=_sec1(ec.generate_private_key(ec.SECP256R1()))
    )
    if revoked:
        devices.revoke(c, did)
    nonce = os.urandom(32)
    bsha = body_sha if body_sha is not None else hashlib.sha256(body).digest()
    now = 1_000_000
    c.execute(
        "INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, recipient_wa_id, "
        "body_bytes, body_sha256, kind, attachments_digest, nonce, created_at_ms, expires_at_ms, "
        "state) VALUES('drf_1','cnv','acc','PN1','61999',?,?, 'text', ?, ?, ?, ?, 'PENDING_APPROVAL')",
        (body, bsha, canonical.attachments_digest([]), nonce, now, now + 600000),
    )
    if window_open:
        c.execute(
            "INSERT INTO conversation_windows(conversation_id, last_inbound_ms) VALUES('cnv', ?)", (now,)
        )
    c.commit()
    return v, c, sign, did, nonce, now


def _env(c, sign, did, nonce, decision="APPROVE"):
    draft = c.execute("SELECT * FROM drafts WHERE id='drf_1'").fetchone()
    env = {"device_id": did, "draft_id": "drf_1", "decision": decision, "nonce": nonce}
    env["signature"] = verify.sign_for_test(canonical.encode(sender._canonical_fields(draft, env)), sign)
    return env


def _guard(now):
    return ClockGuard(lambda: now)


def test_valid_approve_submitted_nonce_consumed(tmp_path):
    v, c, sign, did, nonce, now = _setup(tmp_path)
    r = sender.execute_write(v, c, FakeMeta("ok"), _env(c, sign, did, nonce), _guard(now))
    assert r["outcome"] == "SUBMITTED" and r["wamid"] == "wamid.NEW"
    assert c.execute("SELECT COUNT(*) FROM approval_nonces").fetchone()[0] == 1


def test_replay_denied(tmp_path):
    v, c, sign, did, nonce, now = _setup(tmp_path)
    env = _env(c, sign, did, nonce)
    sender.execute_write(v, c, FakeMeta("ok"), env, _guard(now))
    assert (
        sender.execute_write(v, c, FakeMeta("ok"), env, _guard(now))["reason"] == "APPROVAL_ALREADY_CONSUMED"
    )


def test_reject_never_sends(tmp_path):
    v, c, sign, did, nonce, now = _setup(tmp_path)
    fm = FakeMeta("ok")
    r = sender.execute_write(v, c, fm, _env(c, sign, did, nonce, decision="REJECT"), _guard(now))
    assert r["reason"] == "NOT_APPROVED" and fm.sends == []


def test_payload_changed(tmp_path):
    v, c, sign, did, nonce, now = _setup(
        tmp_path, body=b"hello", body_sha=hashlib.sha256(b"goodbye").digest()
    )
    r = sender.execute_write(v, c, FakeMeta("ok"), _env(c, sign, did, nonce), _guard(now))
    assert r["reason"] == "PAYLOAD_CHANGED"


def test_window_closed_despite_valid_signature(tmp_path):
    v, c, sign, did, nonce, now = _setup(tmp_path, window_open=False)
    r = sender.execute_write(v, c, FakeMeta("ok"), _env(c, sign, did, nonce), _guard(now))
    assert r["reason"] == "P2_WINDOW_CLOSED"


def test_revoked_device_denied(tmp_path):
    v, c, sign, did, nonce, now = _setup(tmp_path, revoked=True)
    r = sender.execute_write(v, c, FakeMeta("ok"), _env(c, sign, did, nonce), _guard(now))
    assert r["reason"] == "DEVICE_INACTIVE"


def test_timeout_after_send_is_indeterminate(tmp_path):
    v, c, sign, did, nonce, now = _setup(tmp_path)
    r = sender.execute_write(v, c, FakeMeta("timeout_after_send"), _env(c, sign, did, nonce), _guard(now))
    assert r["outcome"] == "INDETERMINATE"
    assert c.execute("SELECT state FROM send_attempts").fetchone()[0] == "INDETERMINATE"


def test_clock_backward_refused(tmp_path):
    v, c, sign, did, nonce, now = _setup(tmp_path)
    seq = iter([now, now - 1000])
    g = ClockGuard(lambda: next(seq))
    g.trusted_now()  # prime last = now
    r = sender.execute_write(v, c, FakeMeta("ok"), _env(c, sign, did, nonce), g)
    assert r["outcome"] == "REFUSED" and r["reason"] == "CLOCK_UNTRUSTED"


def test_recover_startup_submitting_to_indeterminate(tmp_path):
    v, c, sign, did, nonce, now = _setup(tmp_path)
    c.execute(
        "INSERT INTO send_attempts(id, draft_id, idempotency_key, state, created_at_ms, updated_at_ms) "
        "VALUES('atm_x','drf_1','idem1','SUBMITTING',1,1)"
    )
    c.commit()
    assert sender.recover_startup(c, 999)["recovered"] == 1
    assert c.execute("SELECT state FROM send_attempts WHERE id='atm_x'").fetchone()[0] == "INDETERMINATE"
