"""comms v0.3 Task A11: legacy `tgml1` client-auth revocation (A33, G3), enforced by the legacy DB."""

import os
import sqlite3

import pytest

from comms.transports.telegram.ipc.handlers.clients import client_handlers
from comms.transports.telegram.ipc.leases import (
    LeaseError,
    mint_lease,
    revoke_client_auth,
    verify_lease,
)
from comms.transports.telegram.keys.store import read_lease_seed
from comms.transports.telegram.runtime.identity import resolve_principal
from comms.transports.telegram.storage.authority_view import load_security
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.migrations import migrate

NOW = "2026-09-24T00:00:00Z"
REFS = ("tcl_" + "a" * 26, "tcl_" + "b" * 26)


@pytest.fixture
def legacy(tmp_path):
    conn = open_db(tmp_path / "legacy.db")
    migrate(conn)
    conn.execute(
        "INSERT INTO principals (principal_ref, principal_key, auth_mode, created_at)"
        " VALUES (?, 'key', 'bearer', ?)",
        ("prn_" + "a" * 26, NOW),
    )
    for ref, kind in zip(REFS, ("codex_local", "claude_code_local"), strict=True):
        conn.execute(
            "INSERT INTO mcp_clients (principal_id, client_ref, auth_kind, auth_binding,"
            " client_kind, created_at) VALUES (1, ?, 'bearer', ?, ?, ?)",
            (ref, f"lease-seed:{ref}", kind, NOW),
        )
    conn.commit()
    key_dir = tmp_path / "keys"
    key_dir.mkdir(mode=0o700)
    for ref in REFS:
        path = key_dir / f"lease-seed.{ref}"
        path.write_bytes(bytes(32))
        os.chmod(path, 0o600)
    stale = key_dir / f"lease-seed.{REFS[0]}.next"
    stale.write_bytes(bytes(32))
    os.chmod(stale, 0o600)
    return conn, key_dir


def test_revocation_disables_every_bearer_deletes_every_seed_and_bumps_the_epoch_once(legacy):
    conn, key_dir = legacy
    assert revoke_client_auth(conn, key_dir, now=NOW) == (2, 2)
    assert conn.execute("SELECT count(*) FROM mcp_clients WHERE enabled = 1").fetchone()[0] == 0
    assert list(key_dir.glob("lease-seed.*")) == []
    assert load_security(conn)[0] == 2


def test_revocation_is_idempotent_and_reports_the_same_result(legacy):
    conn, key_dir = legacy
    revoke_client_auth(conn, key_dir, now=NOW)
    assert revoke_client_auth(conn, key_dir, now=NOW) == (2, 2)
    assert load_security(conn)[0] == 2


def test_a_resume_after_the_commit_but_before_unlinking_still_removes_the_seeds(legacy):
    conn, key_dir = legacy
    revoke_client_auth(conn, key_dir, now=NOW)
    left = key_dir / f"lease-seed.{REFS[1]}"
    left.write_bytes(bytes(32))
    os.chmod(left, 0o600)
    assert revoke_client_auth(conn, key_dir, now=NOW) == (2, 2)
    assert list(key_dir.glob("lease-seed.*")) == []


def test_a_lease_minted_before_revocation_is_refused_after_it(legacy):
    conn, key_dir = legacy
    seed = read_lease_seed(key_dir, REFS[0])
    token = mint_lease(seed=seed, client=REFS[0], epoch=1, now=1_000)
    revoke_client_auth(conn, key_dir, now=NOW)
    seeds = {r: s for r in REFS if (s := read_lease_seed(key_dir, r)) is not None}
    with pytest.raises(LeaseError):
        verify_lease(token, seeds=seeds, epoch=load_security(conn)[0], now=1_000)
    assert resolve_principal(conn, REFS[0]) is None


def test_the_legacy_db_refuses_a_bearer_after_revocation(legacy):
    conn, key_dir = legacy
    revoke_client_auth(conn, key_dir, now=NOW)
    with pytest.raises(sqlite3.IntegrityError, match="tgml1 is retired"):
        conn.execute("UPDATE mcp_clients SET enabled = 1 WHERE client_ref = ?", (REFS[0],))
    with pytest.raises(sqlite3.IntegrityError, match="tgml1 is retired"):
        conn.execute(
            "INSERT INTO mcp_clients (principal_id, client_ref, auth_kind, auth_binding,"
            " client_kind, created_at) VALUES (1, ?, 'bearer', 'x', 'codex_local', ?)",
            ("tcl_" + "c" * 26, NOW),
        )
    with pytest.raises(sqlite3.IntegrityError, match="one-way"):
        conn.execute("UPDATE settings SET value_json = '\"open\"' WHERE key = 'auth.tgml1_state'")
    with pytest.raises(sqlite3.IntegrityError, match="one-way"):
        conn.execute("DELETE FROM settings WHERE key = 'auth.tgml1_state'")
    conn.rollback()


def test_client_rotate_after_revocation_is_refused_and_leaves_no_seed(legacy):
    conn, key_dir = legacy
    revoke_client_auth(conn, key_dir, now=NOW)
    rotate = client_handlers(conn, key_dir=key_dir)["client rotate"]
    with pytest.raises(sqlite3.IntegrityError, match="tgml1 is retired"):
        rotate({"client": "codex_local", "enable": True})
    assert list(key_dir.glob("lease-seed.*")) == []
