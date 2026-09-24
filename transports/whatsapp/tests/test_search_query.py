import os

import pytest

from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.search import index as IDX
from whatsvault.search import query as Q


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(conn, "vault")
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    conn.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    return conn


def _msg(conn, mid, body, lo=1, hi=60001):
    conn.execute(
        "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
        "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
        "VALUES(?, 'acc','cnv','in',?,?,'min','text',?,'manual_export',0)",
        (mid, lo, hi, body),
    )
    IDX.index_message(conn, mid, body)


def test_fts_operators_in_term_are_literal(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_a", "foo or bar baz")
    _msg(conn, "msg_b", "only foo here")
    res = Q.run(conn, Q.SearchQuery(terms=["foo OR bar"]))  # must be a literal phrase, not FTS OR
    ids = [r["message_id"] for r in res]
    assert "msg_a" in ids and "msg_b" not in ids


def test_stray_quote_does_not_break(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_q", 'say "hi" now')
    res = Q.run(conn, Q.SearchQuery(terms=['say "hi"']))  # embedded quotes must be escaped, not injected
    assert [r["message_id"] for r in res] == ["msg_q"]


def test_too_many_terms_raises(tmp_path):
    with pytest.raises(Q.QueryTooComplex):
        Q.compile_lexical(Q.SearchQuery(terms=["x"] * (Q.MAX_TERMS + 1)))


def test_interval_overlap_includes_straddling_message(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_s", "straddle", lo=60000, hi=120000)  # minute [60000,120000)
    # from_ms is INSIDE the interval; a lower-bound filter (ts_lower>=from) would drop it.
    res = Q.run(conn, Q.SearchQuery(terms=["straddle"], from_ms=119000, to_ms=10**12))
    assert [r["message_id"] for r in res] == ["msg_s"]
    # fully outside -> excluded
    res2 = Q.run(conn, Q.SearchQuery(terms=["straddle"], from_ms=200000))
    assert res2 == []


def test_cross_tier_dedup_keeps_lexical(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_1", "salam")  # matches both unicode61 and trigram
    res = Q.run(conn, Q.SearchQuery(terms=["salam"]))
    assert len(res) == 1 and res[0]["message_id"] == "msg_1" and res[0]["tier"] == "lexical"


def test_direction_filter_is_sql_not_match(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_in", "hello")
    conn.execute(
        "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
        "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
        "VALUES('msg_out','acc','cnv','out',1,60001,'min','text','hello','manual_export',0)"
    )
    IDX.index_message(conn, "msg_out", "hello")
    res = Q.run(conn, Q.SearchQuery(terms=["hello"], direction="out"))
    assert [r["message_id"] for r in res] == ["msg_out"]
