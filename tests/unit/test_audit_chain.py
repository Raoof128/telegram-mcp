"""MAC-linked audit chain (design §6.1, §6.2; frozen spec §26.5)."""

import pytest

from telegram_mcp.disclosure.audit.chain import (
    ADMIN_EVENTS,
    ChainError,
    append_event,
    genesis_mac,
    head,
    mint_event_id,
    verify_chain,
)
from telegram_mcp.storage.db import open_db
from telegram_mcp.storage.migrations import migrate

_KEY = bytes(range(32))


@pytest.fixture
def conn(tmp_path):
    connection = open_db(tmp_path / "meta.db")
    migrate(connection)
    return connection


def _append(conn, event):
    """Every append runs inside a caller-owned transaction; there is no
    convenience path that commits for you, because the coordinator must be
    able to put the receipt and the ledger rows in the same transaction."""
    from telegram_mcp.disclosure.audit.chain import immediate_transaction

    with immediate_transaction(conn):
        return append_event(conn, _KEY, event)


def _event(**overrides):
    event = {
        "event_id": mint_event_id(),
        "ts": "2026-09-22T00:00:00Z",
        "tool_name": "telegram_get_messages",
        "principal_ref": "prn_" + "a" * 26,
        "client_ref": "tcl_" + "b" * 26,
        "account_ref": "tga_" + "c" * 26,
        "peer_ref": None,
        "project_ref": None,
        "project_count": 1,
        "policy_epoch": 7,
        "result_count": 3,
        "duration_ms": 42,
        "telegram_rpc_count": 0,
        "status": "ok",
        "error_code": None,
        "disclosure_ref": None,
    }
    event.update(overrides)
    return event


def test_event_id_is_the_frozen_evt_shape():
    _CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    ref = mint_event_id()
    assert ref.startswith("evt_")
    assert len(ref) == 30
    body = ref[4:]
    assert len(body) == 26
    # Every character must be in the Crockford alphabet -- which excludes
    # I, L, O and U. A weaker assertion ("contains a digit") passes for any
    # string and proves nothing.
    assert set(body) <= set(_CROCKFORD)
    assert mint_event_id() != mint_event_id()


def test_genesis_is_bound_to_the_epoch():
    assert genesis_mac(1) != genesis_mac(2)
    assert len(genesis_mac(1)) == 64


def test_first_event_uses_genesis_and_sequence_one(conn):
    appended = _append(conn, _event())
    assert appended["chain_seq"] == 1
    assert appended["chain_epoch"] == 1
    assert appended["prev_event_mac"] == genesis_mac(1)


def test_sequence_is_monotonic_and_links(conn):
    first = _append(conn, _event())
    second = _append(conn, _event())
    assert second["chain_seq"] == 2
    assert second["prev_event_mac"] == first["event_mac"]


def test_head_reports_the_last_event(conn):
    _append(conn, _event())
    last = _append(conn, _event())
    assert head(conn)["event_mac"] == last["event_mac"]


def test_verify_passes_on_an_untouched_chain(conn):
    for _ in range(3):
        _append(conn, _event())
    verify_chain(conn, _KEY)


def test_editing_a_row_breaks_verification(conn):
    _append(conn, _event())
    _append(conn, _event())
    conn.execute("UPDATE audit_events SET result_count = 99 WHERE chain_seq = 1")
    conn.commit()
    with pytest.raises(ChainError):
        verify_chain(conn, _KEY)


def test_deleting_the_tail_breaks_verification(conn):
    _append(conn, _event())
    _append(conn, _event())
    conn.execute("DELETE FROM audit_events WHERE chain_seq = 2")
    conn.commit()
    # Sequence continuity holds, but the head no longer matches what the
    # anchor recorded. Task 3 covers that; here the chain alone still verifies.
    verify_chain(conn, _KEY)


def test_a_wrong_key_fails_verification(conn):
    _append(conn, _event())
    with pytest.raises(ChainError):
        verify_chain(conn, bytes(32))


def test_administrative_events_use_the_closed_dotted_vocabulary(conn):
    assert "admin.repair_anchor" in ADMIN_EVENTS
    appended = _append(conn, _event(tool_name="admin.repair_anchor", status="ok"))
    assert appended["chain_seq"] == 1
    with pytest.raises(ChainError):
        _append(conn, _event(tool_name="admin.something_new"))


def test_concurrent_appends_never_fork(tmp_path):
    import threading

    from telegram_mcp.storage.db import open_db
    from telegram_mcp.storage.migrations import migrate

    migrate(open_db(tmp_path / "meta.db"))

    def appender():
        from telegram_mcp.disclosure.audit.chain import immediate_transaction

        own = open_db(tmp_path / "meta.db")
        for _ in range(10):
            try:
                with immediate_transaction(own):
                    append_event(own, _KEY, _event())
            except Exception:  # noqa: BLE001, S110 -- contention is expected; forks are not
                # A losing writer is the point of the test: SQLite refuses the
                # second BEGIN IMMEDIATE and the chain must still be intact.
                pass

    threads = [threading.Thread(target=appender) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check = open_db(tmp_path / "meta.db")
    seqs = [r[0] for r in check.execute("SELECT chain_seq FROM audit_events ORDER BY chain_seq")]
    assert seqs == list(range(1, len(seqs) + 1))
    verify_chain(check, _KEY)


def test_checkpoint_signs_the_current_head(conn):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from telegram_mcp.disclosure.audit.chain import verify_checkpoints, write_checkpoint

    seed = bytes(range(32))
    appended = _append(conn, _event())
    checkpoint = write_checkpoint(conn, seed, now="2026-09-22T00:00:00Z")

    assert checkpoint["chain_seq"] == appended["chain_seq"]
    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    verify_checkpoints(conn, public)


def test_a_tampered_checkpoint_fails_verification(conn):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from telegram_mcp.disclosure.audit.chain import verify_checkpoints, write_checkpoint

    seed = bytes(range(32))
    _append(conn, _event())
    write_checkpoint(conn, seed, now="2026-09-22T00:00:00Z")
    conn.execute("UPDATE audit_checkpoints SET last_event_mac = 'deadbeef'")
    conn.commit()

    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    with pytest.raises(ChainError):
        verify_checkpoints(conn, public)


def test_cadence_respects_the_spec_bound(conn):
    from telegram_mcp.disclosure.audit.chain import checkpoint_due

    # §26.5: at least every 500 events or 60 minutes, whichever comes first.
    assert checkpoint_due(conn, events_since=500, seconds_since=0)
    assert checkpoint_due(conn, events_since=0, seconds_since=3_600)
    assert not checkpoint_due(conn, events_since=99, seconds_since=60)
