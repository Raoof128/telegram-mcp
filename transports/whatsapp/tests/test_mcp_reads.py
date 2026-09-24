import os

from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.mcp import acl, reads
from whatsvault.search import index as IDX
from whatsvault.search.query import SearchQuery

WINDOW = 24 * 3600 * 1000


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(conn, "vault")
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    conn.execute("INSERT INTO conversations(id, account_id, type, subject) VALUES('cnv','acc','dm','Chat A')")
    conn.execute("INSERT INTO conversations(id, account_id, type, subject) VALUES('sec','acc','dm','Secret')")
    conn.execute("INSERT INTO contacts(id, wa_id, display_name) VALUES('cnt_1','+61412345678','Mona')")
    return conn


def _msg(conn, mid, conv, body):
    conn.execute(
        "INSERT INTO messages(id, account_id, conversation_id, sender_contact_id, direction, ts_lower_ms, "
        "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
        "VALUES(?, 'acc',?, 'cnt_1','in',1,60001,'min','text',?,'manual_export',0)",
        (mid, conv, body),
    )
    IDX.index_message(conn, mid, body)


def test_search_redacts_and_wraps(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_1", "cnv", "hello mona")
    res = reads.search(conn, SearchQuery(terms=["hello"]))
    assert res and res[0]["body"]["_wv_untrusted"] is True
    assert "+61412345678" not in str(res)


def test_search_never_returns_local_only(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_1", "cnv", "secret plan")
    _msg(conn, "msg_2", "sec", "secret plan")
    acl.set_visibility(conn, "sec", "LOCAL_ONLY")
    res = reads.search(conn, SearchQuery(terms=["secret"]))
    convs = {r["conversation_id"] for r in res}
    assert "sec" not in convs and "cnv" in convs


def test_get_messages_fenced(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "msg_2", "sec", "hi")
    acl.set_visibility(conn, "sec", "LOCAL_ONLY")
    assert reads.get_messages(conn, "sec") == []


def test_get_messages_scoped_to_conversation(tmp_path):
    conn = _vault(tmp_path)
    _msg(conn, "m1", "cnv", "a")
    _msg(conn, "m2", "sec", "b")
    ids = [v["message_id"] for v in reads.get_messages(conn, "cnv")]
    assert ids == ["m1"]


def test_conversation_window(tmp_path):
    conn = _vault(tmp_path)
    ctrl = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(ctrl, "control")
    ctrl.execute("INSERT INTO conversation_windows(conversation_id, last_inbound_ms) VALUES('cnv', 1000)")
    ctrl.commit()
    assert reads.get_conversation_window(ctrl, conn, "cnv", 1000 + WINDOW - 1)["open"] is True
    assert reads.get_conversation_window(ctrl, conn, "cnv", 1000 + WINDOW + 1)["open"] is False
    assert reads.get_conversation_window(ctrl, conn, "none", 5)["open"] is False


def test_list_templates_empty_after_phase5_schema(tmp_path):
    # Phase 5 control migration 0003 creates the templates table -> OK with an empty catalogue
    ctrl = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(ctrl, "control")
    out = reads.list_templates(ctrl)
    assert out["status"] == "OK" and out["templates"] == []


def test_get_message_status(tmp_path):
    conn = _vault(tmp_path)
    conn.execute(
        "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
        "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible, wamid) "
        "VALUES('mo','acc','cnv','out',1,2,'ms','text','hi','cloud_api',0,'wamid1')"
    )
    conn.execute(
        "INSERT INTO message_status_events(id, wamid, status, provider_ts_ms) VALUES('evt_1','wamid1','delivered',10)"
    )
    conn.commit()
    st = reads.get_message_status(conn, "mo")
    assert st["delivery_rank"] == 2
