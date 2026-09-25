"""comms v0.3 Task A7: comms.db schema v2 — audit chain, integrity latch, lineage, cutover state."""

import pytest
import sqlcipher3

from comms.core.audit.chain import COMMS
from comms.core.storage.db import open_comms_db
from comms.core.storage.migrations import MIGRATIONS, migrate
from tests.core import schema_fixtures as fx

Integrity = sqlcipher3.dbapi2.IntegrityError
PHASES = [
    "NONE",
    "CUTOVER_ENTERED",
    "LEGACY_DRAINED",
    "LEGACY_VERIFIED",
    "LEGACY_SEALED",
    "LEGACY_ANCHORED",
    "COMMS_GENESIS",
    "COMMS_ANCHORED",
    "LEGACY_CLIENT_AUTH_REVOKED",
    "COMPLETE",
]


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


def _event(conn, seq, epoch=1):
    conn.execute(
        "INSERT INTO audit_events (event_id, ts, kind, subject_ref, subject_digest, payload, chain_epoch,"
        " chain_seq, prev_event_mac, event_mac) VALUES (?, ?, 'k', NULL, NULL, '{}', ?, ?, ?, ?)",
        (f"aev_{'a' * 25}{'abcdefg'[seq % 7]}", fx.T0, epoch, seq, "0" * 64, "1" * 64),
    )


def test_migrate_is_v2_and_rerunnable(conn):
    # v3 (B8) and v4 (D1) build on v2; each migration is rerunnable and the chain ends at the last.
    last = MIGRATIONS[-1].version
    assert last >= 3 and migrate(conn, MIGRATIONS) == last
    assert migrate(conn, MIGRATIONS) == last


def test_audit_tables_match_the_comms_profile_columns(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(audit_events)")]
    assert tuple(cols) == (
        *COMMS.event_columns,
        "chain_epoch",
        "chain_seq",
        "prev_event_mac",
        "event_mac",
    )
    cp = {r[1] for r in conn.execute("PRAGMA table_info(audit_checkpoints)")}
    assert cp == {
        "checkpoint_ref",
        "chain_epoch",
        "chain_seq",
        "last_event_id",
        "last_event_mac",
        "reason",
        "created_at",
        "signing_key_id",
        "signature",
    }


def test_audit_events_and_checkpoints_are_append_only(conn):
    _event(conn, 1)
    with pytest.raises(Integrity, match="append-only"):
        conn.execute("UPDATE audit_events SET payload = '[]'")
    with pytest.raises(Integrity, match="append-only"):
        conn.execute("DELETE FROM audit_events")
    conn.execute(
        "INSERT INTO audit_checkpoints VALUES ('ack_x', 1, 1, 'e', 'm', 'R', ?, 'k', 's')", (fx.T0,)
    )
    with pytest.raises(Integrity, match="append-only"):
        conn.execute("UPDATE audit_checkpoints SET reason = 'X'")
    with pytest.raises(Integrity, match="append-only"):
        conn.execute("DELETE FROM audit_checkpoints")
    with pytest.raises(Integrity):
        _event(conn, 1)  # UNIQUE(chain_epoch, chain_seq)


def test_integrity_starts_ok_and_is_one_row(conn):
    assert conn.execute("SELECT id, state FROM audit_integrity").fetchall() == [(1, "ok")]
    with pytest.raises(Integrity):
        conn.execute("INSERT INTO audit_integrity (id, state) VALUES (2, 'ok')")
    with pytest.raises(Integrity):
        conn.execute("UPDATE audit_integrity SET state = 'maybe'")


def test_lineage_is_immutable(conn):
    conn.execute(
        "INSERT INTO audit_lineage VALUES ('cut_x','d',1,'h','cd','kid','cd2','g',1,'ld',?)",
        (fx.T0,),
    )
    with pytest.raises(Integrity, match="immutable"):
        conn.execute("UPDATE audit_lineage SET legacy_final_head = 'z'")
    with pytest.raises(Integrity, match="immutable"):
        conn.execute("DELETE FROM audit_lineage")


def test_cutover_moves_only_to_its_exact_next_state(conn):
    with pytest.raises(Integrity, match="exact next state"):
        conn.execute("UPDATE cutover_state SET phase = 'COMMS_GENESIS'")  # forward skip
    for phase in PHASES[1:]:
        conn.execute("UPDATE cutover_state SET phase = ?", (phase,))
    with pytest.raises(Integrity, match="exact next state"):
        conn.execute("UPDATE cutover_state SET phase = 'NONE'")  # backwards
    with pytest.raises(Integrity):
        conn.execute("UPDATE cutover_transitions SET to_phase = 'X'")


def test_cutover_ref_and_legacy_digest_immutable_once_set(conn):
    conn.execute("UPDATE cutover_state SET cutover_ref = 'cut_a', legacy_checkpoint_digest = 'd1'")
    with pytest.raises(Integrity, match="immutable"):
        conn.execute("UPDATE cutover_state SET cutover_ref = 'cut_b'")
    with pytest.raises(Integrity, match="immutable"):
        conn.execute("UPDATE cutover_state SET legacy_checkpoint_digest = 'd2'")
    conn.execute("UPDATE cutover_state SET cutover_ref = 'cut_a'")  # same value is not a change


def test_campaign_events_gain_event_digest(conn):
    assert "event_digest" in {r[1] for r in conn.execute("PRAGMA table_info(campaign_events)")}


def test_v1_campaign_rows_survive_v2(tmp_path):
    conn = open_comms_db(tmp_path / "old.db", fx.KEY)
    migrate(conn, MIGRATIONS[:1])
    w = fx.world(conn)
    conn.execute(
        "INSERT INTO campaign_events (event_ref, campaign_ref, event_type, ts, payload)"
        " VALUES ('cev_x', ?, 'campaign.created', ?, '{}')",
        (w["campaign_ref"], fx.T0),
    )
    before = {
        t: conn.execute(f"SELECT * FROM {t}").fetchall()
        for t in ("campaigns", "generations", "delivery_jobs", "job_origins", "campaign_events")
    }
    assert migrate(conn, MIGRATIONS[:2]) == 2  # exactly v2: v3 widens generations (B8)
    for table, rows in before.items():
        after = conn.execute(f"SELECT * FROM {table}").fetchall()
        if table == "campaign_events":
            after = [r[:-1] for r in after]  # the new event_digest column (NULL on old rows)
        assert after == rows, table
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
