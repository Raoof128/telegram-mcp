"""Phase-5a primitives compose inside a caller-owned transaction (design §2.1, 0B G5)."""

import secrets

import pytest

from comms.transports.telegram.authority.epochs import set_locked
from comms.transports.telegram.disclosure.audit import anchor, chain
from comms.transports.telegram.disclosure.audit.chain import immediate_transaction
from comms.transports.telegram.storage.db import bind_epoch_state, open_db, write_epoch_state
from comms.transports.telegram.storage.refstore import RefStore
from comms.transports.telegram.storage.settings import get_setting
from tests.authority_fixtures import seed_authority_rows

NOW = "2026-09-24T00:00:00Z"


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "m.db")
    seed_authority_rows(c)
    return c


def _event(tool="admin.lock"):
    e = {c: None for c in chain.EVENT_COLUMNS}
    e.update(event_id=chain.mint_event_id(), ts=NOW, tool_name=tool, status="ok")
    return e


def test_the_append_guard_is_the_coordinators_guard():
    from comms.transports.telegram.disclosure import coordinator

    assert coordinator.APPEND_GUARD is chain.APPEND_GUARD


def test_insert_checkpoint_rolls_back_with_its_transaction(conn):
    key = secrets.token_bytes(32)
    with immediate_transaction(conn):
        chain.append_event(conn, key, _event())
    with pytest.raises(RuntimeError), immediate_transaction(conn):
        chain.insert_checkpoint(conn, secrets.token_bytes(32), now=NOW)
        raise RuntimeError("abort")
    assert conn.execute("SELECT COUNT(*) FROM audit_checkpoints").fetchone()[0] == 0


def test_insert_checkpoint_refuses_outside_a_transaction(conn):
    with pytest.raises(chain.ChainError):
        chain.insert_checkpoint(conn, secrets.token_bytes(32), now=NOW)


def test_latch_degraded_sets_all_three_settings(conn):
    anchor.latch_degraded(conn, reason="anchor_refresh_failure")
    assert get_setting(conn, "audit.integrity_degraded") == 1
    assert get_setting(conn, "audit.degraded_reason") == "anchor_refresh_failure"
    assert get_setting(conn, "audit.degraded_disclosure_ref") == ""


def test_ensure_peer_in_tx_rolls_back_with_its_transaction(conn):
    refs = RefStore(conn, account_id=1)
    with pytest.raises(RuntimeError), immediate_transaction(conn):
        refs.ensure_peer_in_tx("user", 5, display_name="X", username=None)
        raise RuntimeError("abort")
    assert refs.peer_by_identity("user:5") is None


def test_ensure_peer_in_tx_refuses_outside_a_transaction(conn):
    with pytest.raises(ValueError, match="transaction"):
        RefStore(conn, account_id=1).ensure_peer_in_tx("user", 5, display_name=None, username=None)


def test_write_epoch_state_rolls_back_with_its_transaction(conn):
    state = bind_epoch_state(conn)
    set_locked(state, True, now=NOW)
    with pytest.raises(RuntimeError), immediate_transaction(conn):
        write_epoch_state(conn, state)
        raise RuntimeError("abort")
    assert conn.execute("SELECT locked FROM security_state").fetchone()[0] == 0
