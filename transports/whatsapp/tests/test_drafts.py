import os

import pytest

from whatsvault.approval import drafts as DR
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _control(tmp_path):
    conn = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(conn, "control")
    return conn


def _prep(conn, **kw):
    base = {
        "conversation_id": "cnv",
        "account_id": "acc",
        "phone_number_id": "PN1",
        "recipient_wa_id": "61999",
        "text": "hello",
        "now_ms": 1000,
        "window_open": True,
    }
    base.update(kw)
    return DR.prepare(conn, **base)


def test_prepare_pending_with_nonce(tmp_path):
    conn = _control(tmp_path)
    r = _prep(conn)
    row = conn.execute(
        "SELECT state, nonce, recipient_wa_id FROM drafts WHERE id=?", (r["draft_id"],)
    ).fetchone()
    assert row[0] == "PENDING_APPROVAL" and len(bytes(row[1])) == 32 and row[2] == "61999"


def test_closed_window_freeform_refused(tmp_path):
    conn = _control(tmp_path)
    with pytest.raises(DR.DraftRefused) as e:
        _prep(conn, window_open=False)
    assert e.value.code == "P2_WINDOW_CLOSED"


def test_idempotent_repeat_prepare(tmp_path):
    conn = _control(tmp_path)
    a, b = _prep(conn), _prep(conn)
    assert a["draft_id"] == b["draft_id"] and b["reused"] is True
    assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1


def test_prepare_and_sender_share_one_policy_module():
    from whatsvault.approval import drafts, policy, sender

    assert drafts.policy is policy and sender.policy is policy  # #11 single source
