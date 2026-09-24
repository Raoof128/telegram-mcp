"""comms v0.3 Task B1: seal an epoch and open the next, in the caller's transaction (design §B.1)."""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.core import refs
from comms.core.audit.chain import (
    COMMS,
    ChainError,
    append_event,
    event_mac,
    genesis_mac,
    head,
    seal_and_open_epoch,
    verify_checkpoints,
)
from comms.core.keys import ids
from comms.core.storage.db import write_tx
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW

OLD, NEW = b"\x01" * 32, b"\x02" * 32
CHECKPOINT = b"\x03" * 32
PUBLIC = Ed25519PrivateKey.from_private_bytes(CHECKPOINT).public_key().public_bytes_raw()


def event(kind: str = "system.test_marker") -> dict:
    return {
        "event_id": refs.mint("audit_event"),
        "ts": "2026-09-24T00:00:00.000000Z",
        "kind": kind,
        "subject_ref": None,
        "subject_digest": None,
        "payload": '{"count":1}',
    }


@pytest.fixture
def conn(tmp_path):
    conn = fx.migrated(tmp_path)
    with write_tx(conn):
        append_event(conn, COMMS, OLD, event())
        append_event(conn, COMMS, OLD, event())
    return conn


def _seal(conn, first=None, *, old=OLD):
    with write_tx(conn):
        return seal_and_open_epoch(
            conn,
            COMMS,
            old_key=old,
            new_key=NEW,
            checkpoint_key=CHECKPOINT,
            first_event=first or event(),
            now=NOW,
        )


def test_new_epoch_starts_at_seq_1_with_genesis_prev(conn):
    first = event()
    opened = _seal(conn, first)
    assert (opened["chain_epoch"], opened["chain_seq"]) == (2, 1)
    assert opened["prev_event_mac"] == genesis_mac(COMMS, 2)
    expected = event_mac(
        COMMS, NEW, chain_epoch=2, chain_seq=1, prev_event_mac=genesis_mac(COMMS, 2), event=first
    )
    assert opened["event_mac"] == expected


def test_sealing_checkpoint_matches_the_old_epochs_last_event(conn):
    old_head = head(conn, COMMS)
    _seal(conn)
    row = conn.execute(
        "SELECT chain_epoch, chain_seq, last_event_id, last_event_mac, reason, signing_key_id"
        " FROM audit_checkpoints"
    ).fetchone()
    assert row == (
        1,
        2,
        old_head["event_id"],
        old_head["event_mac"],
        "EPOCH_SEAL",
        ids.ed25519_key_id(PUBLIC),
    )
    verify_checkpoints(conn, COMMS, lambda key_id: PUBLIC)


def test_append_after_seal_continues_the_new_epoch(conn):
    opened = _seal(conn)
    with write_tx(conn):
        after = append_event(conn, COMMS, NEW, event())
    assert (after["chain_epoch"], after["chain_seq"]) == (2, 2)
    assert after["prev_event_mac"] == opened["event_mac"]


def test_seal_requires_a_transaction(conn):
    with pytest.raises(ChainError, match="transaction"):
        seal_and_open_epoch(
            conn,
            COMMS,
            old_key=OLD,
            new_key=NEW,
            checkpoint_key=CHECKPOINT,
            first_event=event(),
            now=NOW,
        )


def test_sealing_refuses_a_head_that_does_not_verify_under_the_old_key(conn):
    with pytest.raises(ChainError, match="old key"):
        _seal(conn, old=b"\x09" * 32)
    assert conn.execute("SELECT count(*) FROM audit_checkpoints").fetchone()[0] == 0
    assert head(conn, COMMS)["chain_epoch"] == 1


def test_sealing_an_empty_chain_is_refused(tmp_path):
    empty = fx.migrated(tmp_path, name="empty.db")
    with pytest.raises(ChainError, match="empty"), write_tx(empty):
        seal_and_open_epoch(
            empty,
            COMMS,
            old_key=OLD,
            new_key=NEW,
            checkpoint_key=CHECKPOINT,
            first_event=event(),
            now=NOW,
        )


def test_the_new_key_must_differ_from_the_old(conn):
    with pytest.raises(ChainError, match="new key"), write_tx(conn):
        seal_and_open_epoch(
            conn,
            COMMS,
            old_key=OLD,
            new_key=OLD,
            checkpoint_key=CHECKPOINT,
            first_event=event(),
            now=NOW,
        )
