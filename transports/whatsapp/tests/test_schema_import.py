import os

import pytest
import sqlcipher3

from whatsvault import ids
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(conn, "vault")
    return conn


def test_reaches_version_2(tmp_path):
    assert M.user_version(_vault(tmp_path)) >= 2


def test_imp_prefix_registered():
    assert ids.new_id("imp").startswith("imp_")


def _seed_batch(conn, bat="bat_1"):
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    conn.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    conn.execute(
        "INSERT INTO import_batches(id, source_kind, source_sha256, declared_date_format, "
        "declared_timezone, self_participant_label) VALUES(?, 'manual_export','sha','DMY','UTC','You')",
        (bat,),
    )


def _msg(conn, mid, body):
    conn.execute(
        "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
        "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
        "VALUES(?, 'acc','cnv','in',1,60001,'min','text',?,'manual_export',0)",
        (mid, body),
    )


def test_observation_unique_per_batch_ordinal(tmp_path):
    conn = _vault(tmp_path)
    _seed_batch(conn)
    _msg(conn, "msg_1", "hi")
    _msg(conn, "msg_2", "yo")
    ins = (
        "INSERT INTO message_import_observations(batch_id, message_id, source_ordinal, "
        "source_start_offset, source_end_offset, source_fingerprint) VALUES('bat_1',?,?,0,10,'fp')"
    )
    conn.execute(ins, ("msg_1", 1))
    with pytest.raises(sqlcipher3.IntegrityError):  # DIFFERENT message, SAME ordinal -> UNIQUE(batch,ordinal)
        conn.execute(ins, ("msg_2", 1))


def test_participant_link_state_constrained(tmp_path):
    conn = _vault(tmp_path)
    _seed_batch(conn)
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(
            "INSERT INTO import_participants(id, import_batch_id, raw_display_name, link_state) "
            "VALUES('imp_1','bat_1','Mona','WHATEVER')"
        )


def test_batch_declared_fields_immutable(tmp_path):
    conn = _vault(tmp_path)
    _seed_batch(conn)
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE import_batches SET declared_date_format='MDY' WHERE id='bat_1'")


def test_observation_is_write_once(tmp_path):
    conn = _vault(tmp_path)
    _seed_batch(conn)
    _msg(conn, "msg_1", "hi")
    conn.execute(
        "INSERT INTO message_import_observations(batch_id, message_id, source_ordinal) "
        "VALUES('bat_1','msg_1',0)"
    )
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(
            "UPDATE message_import_observations SET source_ordinal=5 "
            "WHERE batch_id='bat_1' AND message_id='msg_1'"
        )
    conn.execute("DELETE FROM message_import_observations WHERE batch_id='bat_1'")  # undo path allowed


def test_participant_link_state_may_transition(tmp_path):
    conn = _vault(tmp_path)
    _seed_batch(conn)
    conn.execute("INSERT INTO contacts(id, display_name) VALUES('cnt_x','Mona')")
    conn.execute(
        "INSERT INTO import_participants(id, import_batch_id, raw_display_name) "
        "VALUES('imp_1','bat_1','Mona')"
    )
    conn.execute(
        "UPDATE import_participants SET link_state='LINKED_EXPLICIT', linked_contact_id='cnt_x' "
        "WHERE id='imp_1'"
    )  # sanctioned
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE import_participants SET raw_display_name='Forged' WHERE id='imp_1'")
