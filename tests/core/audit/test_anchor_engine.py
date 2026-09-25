"""comms v0.3 Task A6: the anchor engine in comms.core with legacy and comms profiles."""

import json
import os
from pathlib import Path

import pytest

from comms.core.audit import anchor as eng
from comms.core.audit.anchor import COMMS_ANCHOR, AnchorError
from comms.core.audit.chain import COMMS
from comms.core.storage.db import TransactionIOError, write_tx
from comms.transports.telegram.disclosure.audit.profile import LEGACY_ANCHOR
from tests.core import schema_fixtures as fx
from tests.core.audit.test_chain_engine import KEY, _comms_event, _conn

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads(
    (ROOT / "tests" / "fixtures" / "audit" / "legacy_chain_vectors.json").read_text()
)


def _dir(tmp_path):
    d = tmp_path / "anchor"
    d.mkdir(mode=0o700)
    os.chmod(d, 0o700)
    return d / "head.anchor"


def test_legacy_anchor_reproduces_the_vector():
    key = bytes.fromhex(VECTORS["chain_key_hex"])
    assert (
        eng.anchor_mac(LEGACY_ANCHOR, key, VECTORS["anchor"]["body"])
        == VECTORS["anchor"]["anchor_mac"]
    )


def _write(profile, path, key=KEY, seq=3):
    eng.write_anchor(
        profile,
        path,
        key,
        chain_epoch=1,
        chain_seq=seq,
        event_id="aev_" + "a" * 26,
        event_mac="0" * 64,
        now="2026-09-24T00:00:00.000000Z",
    )


def test_comms_anchor_round_trips_and_rejects_a_legacy_anchor_file(tmp_path):
    path = _dir(tmp_path)
    _write(COMMS_ANCHOR, path)
    assert eng.read_anchor(COMMS_ANCHOR, path, KEY)["chain_seq"] == 3
    _write(LEGACY_ANCHOR, path)
    with pytest.raises(AnchorError, match="MAC"):
        eng.read_anchor(COMMS_ANCHOR, path, KEY)


def test_refresh_is_atomic_under_a_crash_before_rename(tmp_path, monkeypatch):
    path = _dir(tmp_path)
    _write(COMMS_ANCHOR, path, seq=3)

    def crash(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        _write(COMMS_ANCHOR, path, seq=4)
    monkeypatch.undo()
    assert eng.read_anchor(COMMS_ANCHOR, path, KEY)["chain_seq"] == 3
    assert sorted(p.name for p in path.parent.iterdir()) == ["head.anchor"]


def test_write_anchor_refuses_inside_a_comms_transaction(tmp_path):
    conn = fx.migrated(tmp_path)
    with write_tx(conn), pytest.raises(TransactionIOError):
        eng.write_anchor(
            COMMS_ANCHOR,
            _dir(tmp_path),
            KEY,
            chain_epoch=1,
            chain_seq=1,
            event_id="aev_" + "a" * 26,
            event_mac="0" * 64,
            now="t",
            guard_conn=conn,
        )


def test_read_anchor_fails_closed_on_permissions_and_symlinks(tmp_path):
    path = _dir(tmp_path)
    _write(COMMS_ANCHOR, path)
    os.chmod(path, 0o644)
    with pytest.raises(AnchorError, match="0600"):
        eng.read_anchor(COMMS_ANCHOR, path, KEY)
    os.chmod(path, 0o600)
    link = path.parent / "link"
    link.symlink_to(path)
    with pytest.raises(AnchorError, match="symlink"):
        eng.read_anchor(COMMS_ANCHOR, link, KEY)


def test_derive_integrity_states(tmp_path):
    conn = _conn("sqlite3")
    path = _dir(tmp_path)
    conn.execute("BEGIN IMMEDIATE")
    heads = [eng_append(conn, n) for n in range(1, 4)]
    conn.execute("COMMIT")

    def at(h):
        eng.write_anchor(
            COMMS_ANCHOR,
            path,
            KEY,
            chain_epoch=h["chain_epoch"],
            chain_seq=h["chain_seq"],
            event_id=h["event_id"],
            event_mac=h["event_mac"],
            now="t",
        )

    at(heads[2])
    assert eng.derive_integrity(conn, COMMS, COMMS_ANCHOR, lambda e: KEY, path) == eng.CLEAN
    at(heads[1])
    assert (
        eng.derive_integrity(conn, COMMS, COMMS_ANCHOR, lambda e: KEY, path)
        == eng.RECOVERY_REQUIRED
    )
    at(heads[0])
    assert eng.derive_integrity(conn, COMMS, COMMS_ANCHOR, lambda e: KEY, path) == eng.FAIL_CLOSED
    conn.execute("DELETE FROM audit_events WHERE chain_seq = 3")
    at(heads[2])
    assert eng.derive_integrity(conn, COMMS, COMMS_ANCHOR, lambda e: KEY, path) == eng.FAIL_CLOSED


def eng_append(conn, n):
    from comms.core.audit import chain

    return chain.append_event(conn, COMMS, KEY, _comms_event(n))
