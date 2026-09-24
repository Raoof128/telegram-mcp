import os

from whatsvault.approval import reconcile
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _dbs(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    return v, c


def _indeterminate_attempt(c, atm="atm_1"):
    c.execute(
        "INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, kind, state) "
        "VALUES('drf_1','cnv','acc','PN1','text','SUBMITTING')"
    )
    c.execute(
        "INSERT INTO send_attempts(id, draft_id, idempotency_key, state, created_at_ms, updated_at_ms) "
        "VALUES(?, 'drf_1','idem','INDETERMINATE',1,1)",
        (atm,),
    )
    c.commit()


def test_reaches_vault_version_6_with_callback_column(tmp_path):
    v, _ = _dbs(tmp_path)
    assert M.user_version(v) >= 6
    cols = [r[1] for r in v.execute("PRAGMA table_info(message_status_events)").fetchall()]
    assert "biz_opaque_callback_data" in cols


def test_callback_resolves_indeterminate(tmp_path):
    v, c = _dbs(tmp_path)
    _indeterminate_attempt(c)
    r = reconcile.on_status_event(
        v,
        c,
        {
            "wamid": "wamid.X",
            "biz_opaque_callback_data": "wv1:atm_1",
            "status": "delivered",
            "provider_ts_ms": 100,
        },
        now_ms=200,
    )
    assert r["outcome"] == "RESOLVED"
    row = c.execute("SELECT state, wamid FROM send_attempts WHERE id='atm_1'").fetchone()
    assert row[0] == "SUBMITTED" and row[1] == "wamid.X"


def test_recipient_time_only_is_possible_match_not_auto_resolved(tmp_path):
    v, c = _dbs(tmp_path)
    _indeterminate_attempt(c)
    r = reconcile.on_status_event(
        v,
        c,
        {
            "wamid": None,
            "biz_opaque_callback_data": None,
            "status": "delivered",
            "recipient_id": "61999",
            "provider_ts_ms": 100,
        },
        now_ms=200,
    )
    assert r["outcome"] == "POSSIBLE_MATCH"
    assert c.execute("SELECT state FROM send_attempts WHERE id='atm_1'").fetchone()[0] == "INDETERMINATE"
    assert (
        c.execute("SELECT COUNT(*) FROM reconciliation_candidates WHERE state='POSSIBLE_MATCH'").fetchone()[0]
        == 1
    )


def test_resolve_candidate(tmp_path):
    v, c = _dbs(tmp_path)
    r = reconcile.on_status_event(
        v,
        c,
        {
            "wamid": None,
            "biz_opaque_callback_data": None,
            "status": "delivered",
            "recipient_id": "61999",
            "provider_ts_ms": 100,
        },
        now_ms=200,
    )
    reconcile.resolve(c, r["candidate_id"], decision="dismiss")
    assert (
        c.execute("SELECT state FROM reconciliation_candidates WHERE id=?", (r["candidate_id"],)).fetchone()[
            0
        ]
        == "DISMISSED"
    )
