import os

import pytest
import sqlcipher3

from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(conn, "vault")
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    conn.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    conn.execute(
        "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
        "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
        "VALUES('msg_1','acc','cnv','in',1,2,'s','text','original body','cloud_api',0)"
    )
    conn.commit()
    return conn


@pytest.mark.parametrize(
    "col,val",
    [
        ("text_original", "'tampered'"),
        ("sender_contact_id", "'cnt_evil'"),
        ("origin", "'manual_export'"),
        ("wamid", "'wamid.injected'"),
        ("window_eligible", "1"),
        ("ts_lower_ms", "9999"),
    ],
)
def test_evidence_fields_are_immutable(tmp_path, col, val):
    conn = _vault(tmp_path)
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(f"UPDATE messages SET {col}={val} WHERE id='msg_1'")


@pytest.mark.parametrize(
    "col,val",
    [
        ("delivery_rank", "2"),
        ("failed_at_ms", "123"),
        ("deleted_at_ms", "456"),
        ("edited_at_ms", "789"),
    ],
)
def test_projection_fields_remain_updatable(tmp_path, col, val):
    conn = _vault(tmp_path)
    conn.execute(f"UPDATE messages SET {col}={val} WHERE id='msg_1'")
    assert conn.execute(f"SELECT {col} FROM messages WHERE id='msg_1'").fetchone()[0] == int(val)


def test_ingest_events_fully_immutable(tmp_path):
    conn = _vault(tmp_path)
    conn.execute(
        "INSERT INTO ingest_events(id, provider, semantic_event_key, family, received_at_ms, "
        "raw_payload_sha256, raw_payload, parser_version) VALUES('evt_1','meta','K','MESSAGE_INBOUND',1,'h',X'0102',1)"
    )
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE ingest_events SET raw_payload=X'9999' WHERE id='evt_1'")
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE ingest_events SET family='OTHER' WHERE id='evt_1'")


def test_status_backlink_updatable_but_evidence_frozen(tmp_path):
    conn = _vault(tmp_path)
    conn.execute(
        "INSERT INTO message_status_events(id, wamid, status, provider_ts_ms) VALUES('evt_s','w','sent',1)"
    )
    conn.execute("UPDATE message_status_events SET message_internal_id='msg_1' WHERE id='evt_s'")
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE message_status_events SET status='read' WHERE id='evt_s'")
