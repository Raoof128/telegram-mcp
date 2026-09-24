"""comms v0.3 Task B3: the truncation root is the latest verified checkpoint at or before the cutoff (A15)."""

import pytest

from comms.core.audit.chain import COMMS, append_event, insert_checkpoint
from comms.core.audit.retention_root import choose_root
from comms.core.storage.db import write_tx
from tests.core import schema_fixtures as fx
from tests.core.audit.test_chain_epochs_verify import CHECKPOINT, KEYS, _unguard, public_for
from tests.core.audit.test_epochs import event

STAMPS = (
    "2026-09-01T00:00:00.000000Z",
    "2026-09-10T00:00:00.000000Z",
    "2026-09-20T00:00:00.000000Z",
)


@pytest.fixture
def conn(tmp_path):
    """Three periodic checkpoints, at seq 2, 4 and 6, stamped 1, 10 and 20 September."""
    conn = fx.migrated(tmp_path)
    with write_tx(conn):
        for stamp in STAMPS:
            append_event(conn, COMMS, KEYS[1], event())
            append_event(conn, COMMS, KEYS[1], event())
            insert_checkpoint(conn, COMMS, CHECKPOINT, now=stamp, reason="PERIODIC")
    _unguard(conn)
    return conn


def test_root_is_latest_verified_at_or_before_cutoff_not_newest(conn):
    root = choose_root(conn, COMMS, cutoff="2026-09-15T00:00:00Z", public_keys=public_for)
    assert (root["chain_epoch"], root["chain_seq"], root["created_at"]) == (1, 4, STAMPS[1])
    exact = choose_root(conn, COMMS, cutoff=STAMPS[1], public_keys=public_for)
    assert exact["chain_seq"] == 4  # "at or before" includes the cutoff itself


def test_a_bad_signature_checkpoint_is_skipped_and_reported(conn):
    conn.execute("UPDATE audit_checkpoints SET signature = ? WHERE chain_seq = 4", ("00" * 64,))
    skipped: list[str] = []
    root = choose_root(
        conn, COMMS, cutoff="2026-09-15T00:00:00Z", public_keys=public_for, skipped=skipped
    )
    assert root["chain_seq"] == 2
    (bad,) = conn.execute(
        "SELECT checkpoint_ref FROM audit_checkpoints WHERE chain_seq = 4"
    ).fetchone()
    assert skipped == [bad]


def test_a_checkpoint_whose_event_row_is_gone_or_differs_is_skipped(conn):
    conn.execute("DELETE FROM audit_events WHERE chain_seq = 4")
    assert (
        choose_root(conn, COMMS, cutoff="2026-09-15T00:00:00Z", public_keys=public_for)["chain_seq"]
        == 2
    )
    conn.execute("UPDATE audit_events SET event_mac = ? WHERE chain_seq = 2", ("f" * 64,))
    assert choose_root(conn, COMMS, cutoff="2026-09-15T00:00:00Z", public_keys=public_for) is None


def test_no_eligible_root_returns_none(conn):
    assert choose_root(conn, COMMS, cutoff="2026-08-01T00:00:00Z", public_keys=public_for) is None
    assert (
        choose_root(conn, COMMS, cutoff="2026-09-30T00:00:00Z", public_keys=lambda _k: None) is None
    )


def test_the_cutoff_must_be_a_utc_timestamp(conn):
    with pytest.raises(ValueError):
        choose_root(conn, COMMS, cutoff="yesterday", public_keys=public_for)
