"""External head anchor (design §6.3, §6.7; frozen spec §12.2)."""

import json
import os

import pytest

from comms.transports.telegram.disclosure.audit.anchor import (
    ANCHOR_VERSION,
    CLEAN,
    FAIL_CLOSED,
    RECOVERY_REQUIRED,
    AnchorError,
    derive_integrity,
    read_anchor,
    write_anchor,
)
from comms.transports.telegram.disclosure.audit.chain import append_event, mint_event_id
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.migrations import migrate

_KEY = bytes(range(32))


def _append(conn, event):
    """Every append runs inside a caller-owned transaction (design §6.5)."""
    from comms.core.storage.db import write_tx

    with write_tx(conn):
        return append_event(conn, _KEY, event)


@pytest.fixture
def anchor_dir(tmp_path):
    directory = tmp_path / "anchor"
    directory.mkdir(mode=0o700)
    return directory


def _write(path, **overrides):
    fields = {
        "chain_epoch": 1,
        "chain_seq": 1,
        "event_id": "evt_" + "0" * 26,
        "event_mac": "a" * 64,
        "now": "2026-09-22T00:00:00Z",
    }
    fields.update(overrides)
    write_anchor(path, _KEY, **fields)


def test_anchor_round_trips_with_the_frozen_fields(anchor_dir):
    path = anchor_dir / "anchor.json"
    _write(path)
    anchor = read_anchor(path, _KEY)
    assert anchor["version"] == ANCHOR_VERSION
    assert set(anchor) == {
        "version",
        "chain_epoch",
        "chain_seq",
        "event_id",
        "event_mac",
        "updated_at",
        "anchor_mac",
    }


def test_anchor_file_is_0600(anchor_dir):
    path = anchor_dir / "anchor.json"
    _write(path)
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_a_tampered_anchor_is_refused(anchor_dir):
    path = anchor_dir / "anchor.json"
    _write(path)
    body = json.loads(path.read_text())
    body["chain_seq"] = 99
    path.write_text(json.dumps(body))
    with pytest.raises(AnchorError):
        read_anchor(path, _KEY)


def test_a_symlinked_anchor_is_refused(anchor_dir, tmp_path):
    real = tmp_path / "elsewhere.json"
    _write(real)
    link = anchor_dir / "anchor.json"
    link.symlink_to(real)
    with pytest.raises(AnchorError):
        read_anchor(link, _KEY)


def test_a_world_readable_anchor_is_refused(anchor_dir):
    path = anchor_dir / "anchor.json"
    _write(path)
    os.chmod(path, 0o644)
    with pytest.raises(AnchorError):
        read_anchor(path, _KEY)


def test_an_unknown_version_is_refused(anchor_dir):
    path = anchor_dir / "anchor.json"
    _write(path)
    body = json.loads(path.read_text())
    body["version"] = 99
    path.write_text(json.dumps(body))
    with pytest.raises(AnchorError):
        read_anchor(path, _KEY)


def test_no_temp_file_survives_a_successful_write(anchor_dir):
    path = anchor_dir / "anchor.json"
    _write(path)
    _write(path, chain_seq=2)
    assert [p.name for p in anchor_dir.iterdir()] == ["anchor.json"]


# --- the integrity table (design §6.7) --------------------------------------


def _chain(tmp_path):
    conn = open_db(tmp_path / "meta.db")
    migrate(conn)
    return conn


def _event():
    return {
        "event_id": mint_event_id(),
        "ts": "2026-09-22T00:00:00Z",
        "tool_name": "telegram_get_messages",
        "principal_ref": "prn_a",
        "client_ref": "tcl_a",
        "account_ref": "tga_a",
        "peer_ref": None,
        "project_ref": None,
        "project_count": 1,
        "policy_epoch": 1,
        "result_count": 1,
        "duration_ms": 1,
        "telegram_rpc_count": 0,
        "status": "ok",
        "error_code": None,
        "disclosure_ref": None,
    }


def test_anchor_matching_the_head_is_clean(tmp_path, anchor_dir):
    conn = _chain(tmp_path)
    appended = _append(conn, _event())
    path = anchor_dir / "anchor.json"
    _write(path, **{k: appended[k] for k in ("chain_epoch", "chain_seq", "event_id", "event_mac")})
    assert derive_integrity(conn, _KEY, path) == CLEAN


def test_head_one_ahead_is_recovery_required(tmp_path, anchor_dir):
    conn = _chain(tmp_path)
    first = _append(conn, _event())
    path = anchor_dir / "anchor.json"
    _write(path, **{k: first[k] for k in ("chain_epoch", "chain_seq", "event_id", "event_mac")})
    _append(conn, _event())  # crashed before refreshing the anchor
    assert derive_integrity(conn, _KEY, path) == RECOVERY_REQUIRED


def test_head_two_ahead_fails_closed(tmp_path, anchor_dir):
    conn = _chain(tmp_path)
    first = _append(conn, _event())
    path = anchor_dir / "anchor.json"
    _write(path, **{k: first[k] for k in ("chain_epoch", "chain_seq", "event_id", "event_mac")})
    _append(conn, _event())
    _append(conn, _event())
    assert derive_integrity(conn, _KEY, path) == FAIL_CLOSED


def test_anchor_ahead_of_the_head_fails_closed(tmp_path, anchor_dir):
    conn = _chain(tmp_path)
    appended = _append(conn, _event())
    path = anchor_dir / "anchor.json"
    _write(path, **{k: appended[k] for k in ("chain_epoch", "chain_seq", "event_id", "event_mac")})
    conn.execute("DELETE FROM audit_events")  # DB-only truncation
    conn.commit()
    assert derive_integrity(conn, _KEY, path) == FAIL_CLOSED
