import os

import pytest

from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.importers import whatsapp_export as W

DMY = "13/04/2026, 5:32 pm - Mona: hi\n13/04/2026, 5:33 pm - You: yo\n"


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(conn, "vault")
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    conn.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    return conn


def _imp(conn, text=DMY, **kw):
    kw.setdefault("source_sha256", "sha")
    kw.setdefault("date_format", "DMY")
    kw.setdefault("tz_name", "UTC")
    kw.setdefault("conversation_id", "cnv")
    kw.setdefault("account_id", "acc")
    kw.setdefault("self_participant_label", "You")
    return W.import_batch(conn, text, **kw)


def test_missing_self_refused(tmp_path):
    with pytest.raises(W.ImportRefused) as e:
        _imp(_vault(tmp_path), self_participant_label=None)
    assert e.value.code == "MISSING_SELF"


def test_missing_tz_refused(tmp_path):
    with pytest.raises(W.ImportRefused) as e:
        _imp(_vault(tmp_path), tz_name=None)
    assert e.value.code == "MISSING_TZ"


def test_format_mismatch_refused(tmp_path):
    with pytest.raises(W.ImportRefused) as e:
        _imp(_vault(tmp_path), date_format="MDY")  # DMY-only file
    assert e.value.code == "FORMAT_MISMATCH"


def test_clean_import_writes_evidence(tmp_path):
    conn = _vault(tmp_path)
    res = _imp(conn)
    assert res["added"] == 2
    rows = conn.execute(
        "SELECT direction, origin, window_eligible, sender_contact_id, import_fingerprint, tz_basis "
        "FROM messages ORDER BY ts_lower_ms"
    ).fetchall()
    assert len(rows) == 2
    for _direction, origin, we, scid, fp, tzb in rows:
        assert origin == "manual_export" and we == 0 and scid is None and fp is not None
        assert tzb == "explicit_import_setting"
    assert {r[0] for r in rows} == {"in", "out"}  # Mona -> in, You -> out
    obs = conn.execute("SELECT sender_import_participant_id FROM message_import_observations").fetchall()
    assert obs and all(o[0] is not None for o in obs)
    cs = conn.execute("SELECT source_kind, write_capable FROM conversation_sources").fetchall()
    assert cs and cs[0][0] == "manual_export" and cs[0][1] == 0


def test_reimport_dedupes_but_records_second_batch(tmp_path):
    conn = _vault(tmp_path)
    _imp(conn)
    res2 = _imp(conn)
    assert res2["added"] == 0
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(DISTINCT batch_id) FROM message_import_observations").fetchone()[0] == 2


def test_never_touches_window(tmp_path):
    conn = _vault(tmp_path)
    _imp(conn)
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE window_eligible=1").fetchone()[0] == 0


def test_dst_unresolved_then_resolved(tmp_path):
    conn = _vault(tmp_path)
    fold = "05/04/2026, 2:30 am - Mona: hi\n"
    with pytest.raises(W.ImportRefused) as e:
        _imp(conn, text=fold, tz_name="Australia/Sydney")
    assert e.value.code == "DST_UNRESOLVED"
    res = _imp(conn, text=fold, tz_name="Australia/Sydney", dst_resolutions={0: 0})
    assert res["added"] == 1


def test_identical_lines_same_minute_are_distinct(tmp_path):
    conn = _vault(tmp_path)
    text = "13/04/2026, 5:32 pm - Mona: ok\n13/04/2026, 5:32 pm - Mona: ok\n"
    res = _imp(conn, text=text)
    assert res["added"] == 2  # occurrence_index keeps identical lines distinct


def test_imported_messages_are_searchable(tmp_path):
    """An import is the only way to get data into the vault while live ingest is
    Phase-0 gated, so an unindexed import makes search silently useless. doctor's
    `search_missing` check already asserted this; nothing satisfied it.
    """
    from whatsvault import doctor
    from whatsvault.search.query import SearchQuery
    from whatsvault.search.query import run as search_run

    conn = _vault(tmp_path)
    W.import_batch(
        conn,
        "[01/02/2026, 14:32:01] Alice: meeting on saturday at the cafe\n",
        source_sha256="a" * 64,
        date_format="DMY",
        tz_name="UTC",
        conversation_id="cnv",
        account_id="acc",
        self_participant_label="Me",
    )
    assert conn.execute("SELECT COUNT(*) FROM search_documents").fetchone()[0] == 1
    assert len(search_run(conn, SearchQuery(terms=["saturday"]))) == 1
    missing = next(f for f in doctor.check_search(conn) if f["check"] == "search_missing")
    assert missing["ok"] is True, missing["detail"]


def test_reimport_does_not_duplicate_index_rows(tmp_path):
    """Import is idempotent by fingerprint; the index must not drift from it."""
    from whatsvault.search.query import SearchQuery
    from whatsvault.search.query import run as search_run

    conn = _vault(tmp_path)
    text = "[01/02/2026, 14:32:01] Alice: meeting on saturday\n"
    for _ in range(2):
        W.import_batch(
            conn,
            text,
            source_sha256="a" * 64,
            date_format="DMY",
            tz_name="UTC",
            conversation_id="cnv",
            account_id="acc",
            self_participant_label="Me",
        )
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM search_documents").fetchone()[0] == 1
    assert len(search_run(conn, SearchQuery(terms=["saturday"]))) == 1
