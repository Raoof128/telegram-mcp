import os

import pytest
import sqlcipher3

from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _control(tmp_path):
    conn = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(conn, "control")
    return conn


def test_nonce_and_hash_are_32_byte_blobs(tmp_path):
    conn = _control(tmp_path)
    good = (
        "INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, kind, "
        "nonce, body_sha256, state) VALUES('drf_1','cnv','acc','pn','text',?,?,'DRAFT')"
    )
    conn.execute(good, (b"\x11" * 32, b"\x22" * 32))
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(
            "INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, kind, nonce, state) "
            "VALUES('drf_2','cnv','acc','pn','text',?, 'DRAFT')",
            (b"\x11" * 16,),
        )


def test_draft_state_is_constrained(tmp_path):
    conn = _control(tmp_path)
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(
            "INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, kind, state) "
            "VALUES('drf_3','cnv','acc','pn','text','MAGICALLY_APPROVED')"
        )


def test_nonce_single_use_and_idempotency(tmp_path):
    conn = _control(tmp_path)
    conn.execute(
        "INSERT INTO approval_nonces(nonce, consumed_by, consumed_at_ms) VALUES(?,?,?)",
        (b"\x33" * 32, "atm_1", 1),
    )
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(
            "INSERT INTO approval_nonces(nonce, consumed_by, consumed_at_ms) VALUES(?,?,?)",
            (b"\x33" * 32, "atm_2", 2),
        )
    conn.execute(
        "INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, kind, state) VALUES('drf_1','cnv','acc','pn','text','DRAFT')"
    )
    conn.execute(
        "INSERT INTO send_attempts(id, draft_id, idempotency_key, state) VALUES('atm_1','drf_1','IK1','SUBMITTING')"
    )
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(
            "INSERT INTO send_attempts(id, draft_id, idempotency_key, state) VALUES('atm_9','drf_1','IK1','SUBMITTING')"
        )


def test_same_db_foreign_keys_enforced(tmp_path):
    conn = _control(tmp_path)
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(
            "INSERT INTO send_attempts(id, draft_id, idempotency_key, state) VALUES('atm_x','drf_missing','IK2','SUBMITTING')"
        )


def test_draft_freezes_once_state_leaves_draft(tmp_path):
    conn = _control(tmp_path)
    conn.execute(
        "INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, kind, nonce, body_sha256, state) "
        "VALUES('drf_1','cnv','acc','pn','text',?,?,'DRAFT')",
        (b"\x11" * 32, b"\x22" * 32),
    )
    conn.execute("UPDATE drafts SET state='PENDING_APPROVAL' WHERE id='drf_1'")
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE drafts SET body_sha256=? WHERE id='drf_1'", (b"\x99" * 32,))


def test_audit_log_append_only(tmp_path):
    conn = _control(tmp_path)
    conn.execute(
        "INSERT INTO audit_log(id, actor, tool, args_hash, outcome, ts_ms) VALUES('aud_1','mcp','search','h','ok',1)"
    )
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE audit_log SET outcome='changed' WHERE id='aud_1'")
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("DELETE FROM audit_log WHERE id='aud_1'")
