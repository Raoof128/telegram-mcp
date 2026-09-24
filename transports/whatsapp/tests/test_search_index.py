import os

from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.search import index as IDX
from whatsvault.search import normalise as N


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


def _lex(conn, q):
    return [
        r[0]
        for r in conn.execute(
            "SELECT sd.message_id FROM fts_lexical f JOIN search_documents sd ON sd.rowid=f.rowid "
            "WHERE fts_lexical MATCH ?",
            (q,),
        ).fetchall()
    ]


def _comp(conn, q):
    return [
        r[0]
        for r in conn.execute(
            "SELECT sd.message_id FROM fts_compact f JOIN search_documents sd ON sd.rowid=f.rowid "
            "WHERE fts_compact MATCH ?",
            (q,),
        ).fetchall()
    ]


def test_reaches_version_3(tmp_path):
    assert M.user_version(_vault(tmp_path)) >= 3


def test_index_and_search_both_tiers(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_1", "می‌روم")
    _msg(conn, "msg_2", "hello world")
    IDX.index_message(conn, "msg_1", "می‌روم")
    IDX.index_message(conn, "msg_2", "hello world")
    assert _lex(conn, '"می روم"') == ["msg_1"]
    assert _comp(conn, '"میروم"') == ["msg_1"]
    assert "msg_2" in _lex(conn, "hello")


def test_reindex_only_stale(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_1", "salam")
    IDX.index_message(conn, "msg_1", "salam")
    conn.execute("UPDATE search_documents SET normaliser_version=0 WHERE message_id='msg_1'")
    conn.commit()
    assert IDX.reindex_stale(conn) == 1
    assert (
        conn.execute("SELECT normaliser_version FROM search_documents WHERE message_id='msg_1'").fetchone()[0]
        == N.NORMALISER_VERSION
    )
    assert IDX.reindex_stale(conn) == 0


def test_reindex_keeps_fts_consistent(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_1", "salam")
    IDX.index_message(conn, "msg_1", "salam")
    conn.execute("UPDATE search_documents SET normaliser_version=0 WHERE message_id='msg_1'")
    conn.commit()
    IDX.reindex_stale(conn)
    conn.execute("INSERT INTO fts_lexical(fts_lexical) VALUES('integrity-check')")  # raises if corrupt
    assert _lex(conn, "salam") == ["msg_1"]


def test_rebuild_all_from_messages(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_1", "hello")
    _msg(conn, "msg_2", "world")
    IDX.index_message(conn, "msg_1", "hello")
    IDX.index_message(conn, "msg_2", "world")
    conn.execute("DELETE FROM search_documents")
    conn.commit()  # wipe derived
    assert IDX.rebuild_all(conn) == 2
    assert set(_lex(conn, "hello")) == {"msg_1"}
    conn.execute("INSERT INTO fts_lexical(fts_lexical) VALUES('integrity-check')")
