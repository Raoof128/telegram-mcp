import os

import pytest
import sqlcipher3

from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(conn, "vault")
    return conn


def test_ingest_events_semantic_key_unique(tmp_path):
    conn = _vault(tmp_path)
    ins = (
        "INSERT INTO ingest_events(id, provider, semantic_event_key, family, received_at_ms, "
        "raw_payload_sha256, raw_payload, parser_version) VALUES(?,?,?,?,?,?,?,?)"
    )
    conn.execute(ins, ("evt_1", "meta", "KEY1", "MESSAGE_INBOUND", 1, "h", b"\x00", 1))
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(ins, ("evt_2", "meta", "KEY1", "MESSAGE_INBOUND", 2, "h", b"\x00", 1))


def test_messages_wamid_unique_but_nulls_allowed(tmp_path):
    conn = _vault(tmp_path)
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn1')")
    conn.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    cols = (
        "id, account_id, conversation_id, direction, ts_lower_ms, ts_upper_ms_exclusive, "
        "ts_precision, type, text_original, origin, window_eligible"
    )

    def ins(mid, wamid=None):
        conn.execute(
            f"INSERT INTO messages({cols}, wamid) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, "acc", "cnv", "in", 1, 2, "s", "text", "hi", "cloud_api", 0, wamid),
        )

    ins("msg_a")
    ins("msg_b")
    ins("msg_c", "wamid.X")
    with pytest.raises(sqlcipher3.IntegrityError):
        ins("msg_d", "wamid.X")


def test_interval_check_rejects_inverted_ts(tmp_path):
    conn = _vault(tmp_path)
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    conn.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(
            "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
            "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
            "VALUES('msg_x','acc','cnv','in',5,5,'s','text','x','cloud_api',0)"
        )


def test_status_events_have_no_mandatory_fk(tmp_path):
    conn = _vault(tmp_path)
    conn.execute(
        "INSERT INTO message_status_events(id, wamid, status, provider_ts_ms, recipient_id) "
        "VALUES('evt_s','wamid.Y','sent',1,'r')"
    )
    assert conn.execute("SELECT message_internal_id FROM message_status_events").fetchone()[0] is None
