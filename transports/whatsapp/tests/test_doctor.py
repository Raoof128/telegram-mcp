import os

from whatsvault import doctor, ids
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _dbs(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    v.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    v.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    c.execute("INSERT INTO conversation_windows(conversation_id, last_inbound_ms) VALUES('cnv',0)")
    return v, c


def _msg(v, mid, origin, direction, ts, eligible):
    v.execute(
        "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
        "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
        f"VALUES('{mid}','acc','cnv','{direction}',{ts},{ts + 1000},'s','text','x','{origin}',{eligible})"
    )


def test_rebuild_uses_eligible_evidence_only(tmp_path):
    v, c = _dbs(tmp_path)
    _msg(v, "msg_1", "cloud_api", "in", 5000, 1)
    _msg(v, "msg_2", "cloud_api", "in", 9000, 1)
    _msg(v, "msg_3", "cloud_api", "in", 999000, 0)
    _msg(v, "msg_4", "history_sync", "in", 888000, 0)
    _msg(v, "msg_5", "cloud_api", "out", 12000, 1)
    v.commit()
    out = doctor.rebuild_window_from_evidence(v, c, "cnv")
    assert out["evidence_ms"] == 9000
    assert (
        c.execute("SELECT last_inbound_ms FROM conversation_windows WHERE conversation_id='cnv'").fetchone()[
            0
        ]
        == 9000
    )


def test_doctor_repairs_forged_future_window_downward(tmp_path):
    v, c = _dbs(tmp_path)
    c.execute("UPDATE conversation_windows SET last_inbound_ms=32503680000000 WHERE conversation_id='cnv'")
    _msg(v, "msg_1", "cloud_api", "in", 9000, 1)
    v.commit()
    out = doctor.rebuild_window_from_evidence(v, c, "cnv")
    assert out["drift"] is True
    assert out["evidence_ms"] == 9000
    assert (
        c.execute("SELECT last_inbound_ms FROM conversation_windows WHERE conversation_id='cnv'").fetchone()[
            0
        ]
        == 9000
    )


def test_advance_window_is_monotonic_for_live_path(tmp_path):
    v, c = _dbs(tmp_path)
    c.execute("UPDATE conversation_windows SET last_inbound_ms=50000 WHERE conversation_id='cnv'")
    assert doctor.advance_window(c, "cnv", 9000) == 50000
    assert doctor.advance_window(c, "cnv", 70000) == 70000


def test_check_vault_flags_bad_id_and_runs_integrity(tmp_path):
    v, _ = _dbs(tmp_path)
    good = ids.new_id("msg")
    _msg(v, good, "cloud_api", "in", 1, 1)
    v.execute(
        "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
        "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
        "VALUES('BADID','acc','cnv','in',1,2,'s','text','x','cloud_api',0)"
    )
    v.commit()
    findings = {f["check"]: f for f in doctor.check_vault(v)}
    assert findings["message_id_prefix"]["ok"] is False
    assert findings["integrity_check"]["ok"] is True
    assert findings["foreign_key_check"]["ok"] is True
    assert "cipher_integrity_check" in findings
