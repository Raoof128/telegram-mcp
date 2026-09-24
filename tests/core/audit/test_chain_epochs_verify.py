"""comms v0.3 Task B2: verify_chain across contiguous sealed epochs and from a verified root.

The Phase-5 probe's five attacks become named regressions: a missing middle epoch, a
truncated epoch tail, a forged root, a root without its row, and a skipped epoch number.
"""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.core.audit.chain import (
    COMMS,
    ChainError,
    append_event,
    insert_checkpoint,
    seal_and_open_epoch,
    verify_chain,
)
from comms.core.keys import ids
from comms.core.storage.db import write_tx
from tests.core import schema_fixtures as fx
from tests.core.audit.test_epochs import event
from tests.core.campaign_helpers import NOW

KEYS = {1: b"\x11" * 32, 2: b"\x22" * 32, 3: b"\x33" * 32}
CHECKPOINT = b"\x44" * 32
PUBLIC = Ed25519PrivateKey.from_private_bytes(CHECKPOINT).public_key().public_bytes_raw()


def public_for(key_id):
    return PUBLIC if key_id == ids.ed25519_key_id(PUBLIC) else None


def _unguard(conn):
    for name in (
        "audit_events_append_only_d",
        "audit_events_delete_only_behind_root",  # schema v3 (B18)
        "audit_events_append_only_u",
        "audit_checkpoints_append_only_d",
        "audit_checkpoints_append_only_u",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")


@pytest.fixture
def chain(tmp_path):
    """Epoch 1: seq 1..3 (a periodic checkpoint at 2), sealed; epoch 2: seq 1..2, sealed; epoch 3: seq 1..2."""
    conn = fx.migrated(tmp_path)
    with write_tx(conn):
        for i in range(3):
            append_event(conn, COMMS, KEYS[1], event())
            if i == 1:
                insert_checkpoint(
                    conn, COMMS, CHECKPOINT, now="2026-09-24T00:00:00.000000Z", reason="PERIODIC"
                )
    for epoch in (2, 3):
        with write_tx(conn):
            seal_and_open_epoch(
                conn,
                COMMS,
                old_key=KEYS[epoch - 1],
                new_key=KEYS[epoch],
                checkpoint_key=CHECKPOINT,
                first_event=event(),
                now=NOW,
            )
            append_event(conn, COMMS, KEYS[epoch], event())
    _unguard(conn)
    return conn


def _verify(conn, **kw):
    verify_chain(conn, COMMS, KEYS.__getitem__, public_for=public_for, **kw)


def _root(conn, epoch, seq):
    row = conn.execute(
        "SELECT chain_epoch, chain_seq, last_event_id, last_event_mac FROM audit_checkpoints"
        " WHERE chain_epoch = ? AND chain_seq = ?",
        (epoch, seq),
    ).fetchone()
    return dict(
        zip(("chain_epoch", "chain_seq", "last_event_id", "last_event_mac"), row, strict=True)
    )


def test_two_legitimate_epochs_verify(chain):
    _verify(chain)


def test_verification_from_a_verified_root_accepts_a_truncated_prefix(chain):
    root = _root(chain, 1, 2)
    chain.execute("DELETE FROM audit_events WHERE chain_epoch = 1 AND chain_seq < 2")
    _verify(chain, root=root)
    with pytest.raises(ChainError):
        _verify(chain)  # without the root the missing prefix is caught


def test_missing_middle_epoch_fails(chain):
    chain.execute("DELETE FROM audit_events WHERE chain_epoch = 2")
    with pytest.raises(ChainError, match="epoch"):
        _verify(chain)


def test_truncated_epoch_tail_fails(chain):
    chain.execute("DELETE FROM audit_events WHERE chain_epoch = 1 AND chain_seq = 3")
    with pytest.raises(ChainError, match="seal"):
        _verify(chain)


def test_a_deleted_final_epoch_after_its_seal_fails(chain):
    chain.execute("DELETE FROM audit_events WHERE chain_epoch = 3")
    with pytest.raises(ChainError, match="seal"):
        _verify(chain)


def test_forged_root_signature_fails(chain):
    root = _root(chain, 1, 2)
    chain.execute("DELETE FROM audit_events WHERE chain_epoch = 1 AND chain_seq < 2")
    chain.execute(
        "UPDATE audit_checkpoints SET signature = ? WHERE chain_epoch = 1 AND chain_seq = 2",
        ("00" * 64,),
    )
    with pytest.raises(ChainError, match="signature"):
        _verify(chain, root=root)


def test_root_without_its_row_fails(chain):
    root = _root(chain, 1, 2)
    chain.execute("DELETE FROM audit_events WHERE chain_epoch = 1 AND chain_seq <= 2")
    with pytest.raises(ChainError, match="root"):
        _verify(chain, root=root)


def test_skipped_epoch_number_fails(tmp_path):
    conn = fx.migrated(tmp_path, name="skip.db")
    with write_tx(conn):
        append_event(conn, COMMS, KEYS[1], event())
    with write_tx(conn):
        seal_and_open_epoch(
            conn,
            COMMS,
            old_key=KEYS[1],
            new_key=KEYS[2],
            checkpoint_key=CHECKPOINT,
            first_event=event(),
            now=NOW,
        )
    _unguard(conn)
    conn.execute("UPDATE audit_events SET chain_epoch = 3 WHERE chain_epoch = 2")
    with pytest.raises(ChainError, match="epoch"):
        _verify(conn)


def test_a_forged_epoch_seal_fails(chain):
    chain.execute(
        "UPDATE audit_checkpoints SET signature = ? WHERE reason = 'EPOCH_SEAL' AND chain_epoch = 1",
        ("00" * 64,),
    )
    with pytest.raises(ChainError, match="signature"):
        _verify(chain)


def test_multiple_epochs_need_public_keys(chain):
    with pytest.raises(ChainError, match="public"):
        verify_chain(chain, COMMS, KEYS.__getitem__)


def test_keys_by_epoch_uses_exactly_that_epochs_key(chain):
    swapped = {1: KEYS[2], 2: KEYS[1], 3: KEYS[3]}
    with pytest.raises(ChainError, match="MAC"):
        verify_chain(chain, COMMS, swapped.__getitem__, public_for=public_for)
    with pytest.raises(ChainError, match="key"):
        verify_chain(chain, COMMS, {1: KEYS[1], 2: KEYS[2]}.__getitem__, public_for=public_for)
