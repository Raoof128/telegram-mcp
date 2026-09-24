import hashlib
import os

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


def _imp(conn, **kw):
    kw.setdefault("source_sha256", "sha")
    kw.setdefault("date_format", "DMY")
    kw.setdefault("tz_name", "UTC")
    kw.setdefault("conversation_id", "cnv")
    kw.setdefault("account_id", "acc")
    kw.setdefault("self_participant_label", "You")
    return W.import_batch(conn, DMY, **kw)


def test_store_verify_and_reparse(tmp_path):
    conn = _vault(tmp_path)
    raw = DMY.encode("utf-8")
    ssha = hashlib.sha256(raw).hexdigest()
    key = os.urandom(32)
    path, sealed_sha = W.store_source_artifact(str(tmp_path), ssha, raw, key)
    assert os.path.exists(path)
    res = _imp(conn, source_sha256=ssha, source_artifact=(path, sealed_sha))
    dr = W.reparse(conn, res["batch_id"], key, str(tmp_path))
    assert dr["would_add"] == 2


def test_overlapping_batches_undo_is_observation_aware(tmp_path):
    conn = _vault(tmp_path)
    a = _imp(conn)
    b = _imp(conn)  # same text -> dedup to the shared two messages
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    W.undo_batch(conn, a["batch_id"])
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2  # B still observes
    u = W.undo_batch(conn, b["batch_id"])
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert u["messages_deleted"] == 2


def test_undo_sets_undone_at_and_clears_sources(tmp_path):
    conn = _vault(tmp_path)
    r = _imp(conn)
    W.undo_batch(conn, r["batch_id"])
    assert (
        conn.execute("SELECT undone_at_ms FROM import_batches WHERE id=?", (r["batch_id"],)).fetchone()[0]
        is not None
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM conversation_sources WHERE import_batch_id=?", (r["batch_id"],)
        ).fetchone()[0]
        == 0
    )
