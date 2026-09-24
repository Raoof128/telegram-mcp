import os

from whatsvault import doctor
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.search import index as IDX


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(conn, "vault")
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    conn.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    return conn


def _msg(conn, mid, body):
    conn.execute(
        "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
        "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
        "VALUES(?, 'acc','cnv','in',1,60001,'min','text',?,'manual_export',0)",
        (mid, body),
    )


def _by(findings, name):
    return next(f for f in findings if f["check"] == name)


def test_parity_ok_after_index(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_1", "hello")
    IDX.index_message(conn, "msg_1", "hello")
    assert all(x["ok"] for x in doctor.check_search(conn))


def test_missing_index_flagged(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_1", "hello")
    conn.commit()  # never indexed
    assert _by(doctor.check_search(conn), "search_missing")["ok"] is False


def test_orphan_flagged(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_1", "hello")
    IDX.index_message(conn, "msg_1", "hello")
    conn.execute("DELETE FROM messages WHERE id='msg_1'")
    conn.commit()
    assert _by(doctor.check_search(conn), "search_orphans")["ok"] is False


def test_stale_normaliser_flagged(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_1", "hello")
    IDX.index_message(conn, "msg_1", "hello")
    conn.execute("UPDATE search_documents SET normaliser_version=0 WHERE message_id='msg_1'")
    conn.commit()
    assert _by(doctor.check_search(conn), "search_normaliser_stale")["ok"] is False
