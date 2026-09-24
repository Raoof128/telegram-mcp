"""comms v0.3 Task B31: the audit model's verify against the real engine, 200 seeded walks.

Each walk draws operations the model allows — append, seal-and-open, checkpoint, tick,
truncate at a cutoff, and an attacker's delete — applies each to the model and to a real
comms database through the real engine functions, and after every step requires the
model's verdict to equal the real one (verify_chain from the root, plus the anchored head).
"""

import random
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from comms.core import timeutil
from comms.core.audit.chain import (
    COMMS,
    ChainError,
    append_event,
    head,
    insert_checkpoint,
    seal_and_open_epoch,
    verify_chain,
)
from comms.core.audit.verify_all import _root_of
from comms.core.keys import ids
from comms.core.maintenance.retention import _truncate_comms
from comms.core.storage.db import write_tx
from formal import audit_model as model
from tests.core import schema_fixtures as fx
from tests.core.audit.test_epochs import event
from tests.core.campaign_helpers import NOW

KEYS = {1: b"\x11" * 32, 2: b"\x22" * 32, 3: b"\x33" * 32}
CHECKPOINT = b"\x44" * 32
PUBLIC = Ed25519PrivateKey.from_private_bytes(CHECKPOINT).public_key().public_bytes_raw()
WALKS, STEPS = 200, 12
CHAIN_OPS = {"append", "seal_and_open", "checkpoint", "tick", "truncate", "attack_delete"}


def _public_for(key_id):
    return PUBLIC if key_id == ids.ed25519_key_id(PUBLIC) else None


def _at(t: int):
    return NOW + timedelta(days=t)


def _real_verdict(conn, anchor) -> bool:
    current = head(conn, COMMS)
    if current is None or (current["chain_epoch"], current["chain_seq"]) != anchor:
        return False
    try:
        verify_chain(
            conn, COMMS, KEYS.__getitem__, root=_root_of(conn, COMMS), public_for=_public_for
        )
    except ChainError:
        return False
    return True


def _apply(conn, state, op):
    kind = op[0]
    epoch = state.full[-1][0]
    if kind == "append":
        with write_tx(conn):
            append_event(conn, COMMS, KEYS[epoch], event())
    elif kind == "seal_and_open":
        with write_tx(conn):
            seal_and_open_epoch(
                conn,
                COMMS,
                old_key=KEYS[epoch],
                new_key=KEYS[epoch + 1],
                checkpoint_key=CHECKPOINT,
                first_event=event(),
                now=_at(state.time),
            )
    elif kind == "checkpoint":
        with write_tx(conn):
            insert_checkpoint(
                conn, COMMS, CHECKPOINT, now=timeutil.iso(_at(state.time)), reason="PERIODIC"
            )
    elif kind == "truncate":
        _truncate_comms(conn, _at(op[1]), KEYS.__getitem__, _at(op[1]))
    elif kind == "attack_delete":
        target = state.events[op[1]]
        conn.execute("DROP TRIGGER IF EXISTS audit_events_delete_only_behind_root")
        conn.execute("DELETE FROM audit_events WHERE chain_epoch = ? AND chain_seq = ?", target)
        conn.commit()


@pytest.mark.parametrize("seed", range(WALKS))
def test_the_model_and_the_engine_agree(tmp_path, seed, monkeypatch):
    monkeypatch.setattr(
        "comms.core.maintenance.retention.registry_public_for", lambda conn: _public_for
    )
    rng = random.Random(seed)
    conn = fx.migrated(tmp_path)
    with write_tx(conn):
        append_event(conn, COMMS, KEYS[1], event())
    state = model.State()
    for _ in range(STEPS):
        allowed = [
            (op, nxt)
            for op in model.operations(state)
            if op[0] in CHAIN_OPS and (nxt := model.step(state, op)) is not None
        ]
        if not allowed:
            break
        op, nxt = rng.choice(allowed)
        _apply(conn, state, op)
        state = nxt
        assert _real_verdict(conn, state.anchor) == model.verify(state), (seed, op, state)
