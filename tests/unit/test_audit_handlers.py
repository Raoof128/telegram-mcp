"""Lock and audit commands through the audited runner (design §2.4, 0B G5, G16)."""

import secrets

import pytest

from comms.transports.telegram.disclosure.audit.anchor import (
    CLEAN,
    derive_integrity,
    latch_degraded,
)
from comms.transports.telegram.disclosure.audit.chain import verify_chain
from comms.transports.telegram.ipc.handlers._wrapper import AuditSink
from comms.transports.telegram.ipc.handlers.audit import audit_handlers
from comms.transports.telegram.storage.db import open_db
from tests.authority_fixtures import seed_authority_rows

KEY = secrets.token_bytes(32)
CHECKPOINT = secrets.token_bytes(32)
NOW = "2026-09-24T00:00:00Z"


@pytest.fixture
def world(tmp_path):
    from comms.transports.telegram.disclosure.keys import ensure_current_published

    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    ensure_current_published(conn, purpose="audit_checkpoint", private_seed=CHECKPOINT, now=NOW)
    anchor_dir = tmp_path / "anchor"
    anchor_dir.mkdir(mode=0o700)
    sink = AuditSink(KEY, anchor_dir / "anchor.json", lambda: NOW)
    return conn, sink, audit_handlers(conn, sink=sink, checkpoint_key=CHECKPOINT)


def _security(conn):
    return tuple(conn.execute("SELECT security_epoch, locked FROM security_state").fetchone())


def test_lock_and_unlock_bump_the_epoch_and_are_chained(world):
    conn, sink, h = world
    epoch, _ = _security(conn)
    assert h["lock"]({})["anchor"] == "refreshed"
    assert _security(conn) == (epoch + 1, 1)
    assert h["unlock"]({})["anchor"] == "refreshed"
    assert _security(conn) == (epoch + 2, 0)
    names = [r[0] for r in conn.execute("SELECT tool_name FROM audit_events ORDER BY chain_seq")]
    assert names == ["admin.lock", "admin.unlock"]
    verify_chain(conn, KEY)
    assert derive_integrity(conn, KEY, sink.anchor_path) == CLEAN


def test_lock_status_reports_without_writing(world):
    conn, _sink, h = world
    before = conn.total_changes
    status = h["lock status"]({})
    assert status == {
        "locked": False,
        "security_epoch": _security(conn)[0],
        "audit_degraded": False,
    }
    assert conn.total_changes == before


def test_lock_is_refused_while_degraded_and_nothing_changes(world):
    conn, _sink, h = world
    latch_degraded(conn, reason="anchor_refresh_failure")
    before = _security(conn)
    with pytest.raises(PermissionError):
        h["lock"]({})
    assert _security(conn) == before


def test_checkpoint_signs_the_head_and_verify_reports_it(world):
    _conn, _sink, h = world
    h["lock"]({})
    made = h["audit checkpoint"]({})
    assert made["chain_seq"] == 1
    report = h["audit verify"]({})
    assert report == {"integrity": CLEAN, "checkpoints": "verified", "audit_degraded": False}
    # registry-verified: the checkpoint names its own published key


def test_no_checkpoints_is_none_not_verified(world):
    """Global constraint: a check that could not run never reports success (review #6)."""
    _conn, _sink, h = world
    h["lock"]({})
    assert h["audit verify"]({})["checkpoints"] == "none"


def test_a_checkpoint_under_an_unpublished_key_fails(world):
    conn, _sink, h = world
    h["lock"]({})
    h["audit checkpoint"]({})
    conn.execute("DELETE FROM verification_keys WHERE purpose = 'audit_checkpoint'")
    conn.commit()
    assert h["audit verify"]({})["checkpoints"] == "failed"


def test_a_tampered_checkpoint_fails(world):
    conn, _sink, h = world
    h["lock"]({})
    h["audit checkpoint"]({})
    conn.execute("UPDATE audit_checkpoints SET last_event_mac = 'deadbeef'")
    conn.commit()
    assert h["audit verify"]({})["checkpoints"] == "failed"


def test_checkpoint_on_an_empty_chain_is_refused(world):
    _conn, _sink, h = world
    with pytest.raises(ValueError, match="empty"):
        h["audit checkpoint"]({})


def test_repair_is_refused_when_nothing_needs_repair(world):
    _conn, _sink, h = world
    h["lock"]({})
    with pytest.raises(PermissionError, match="not repairable"):
        h["audit repair-anchor"]({})


def test_an_anchor_failure_is_recovered_by_repair_then_writes_resume(world):
    """The operator path when a lock commits but its anchor cannot be refreshed.

    The command's commit stands, and every audited command is refused until
    `audit repair-anchor` succeeds. The chain is exactly one event ahead of
    the anchor, so repair is legal (RECOVERY_REQUIRED). This is the ordering
    the audit-degraded runbook (5c) documents.
    """
    import os

    conn, sink, h = world
    h["lock"]({})  # anchor written at seq 1
    os.chmod(sink.anchor_path.parent, 0o500)  # the next refresh cannot write
    try:
        assert h["unlock"]({})["anchor"] == "degraded"  # committed: now unlocked
    finally:
        os.chmod(sink.anchor_path.parent, 0o700)
    assert _security(conn)[1] == 0
    with pytest.raises(PermissionError):
        h["lock"]({})
    assert h["audit repair-anchor"]({})["integrity"] == CLEAN
    assert h["lock"]({})["anchor"] == "refreshed"


def test_the_security_change_hook_runs_after_commit(world, tmp_path):
    conn, sink, _h = world
    calls = []
    h = audit_handlers(
        conn, sink=sink, checkpoint_key=CHECKPOINT, on_security_change=lambda: calls.append(1)
    )
    h["lock"]({})
    assert calls == [1]
