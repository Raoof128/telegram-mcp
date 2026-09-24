"""Task 7: C4 shared-membership rules, cross-table integrity, open-path safety.

Every check here attempts a direct database write and proves it fails, as
spec §12.3 requires ("application checks alone are insufficient unless
contract tests deliberately attempt inconsistent direct DB writes and prove
they fail").
"""

import os
import sqlite3
import stat

import pytest

from comms.transports.telegram.storage.db import (
    StorageError,
    open_db,
    purge_expired_cursors,
    require_foreign_keys,
    startup_gc,
)
from comms.transports.telegram.storage.migrations import migrate

TS = "2026-09-22T00:00:00Z"
LATER = "2099-01-01T00:00:00Z"


def _conn(tmp_path, name="t.db"):
    conn = sqlite3.connect(tmp_path / name)
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def _base(conn, *, accounts=(1,), peers=(), projects=()):
    for account in accounts:
        conn.execute(
            "INSERT INTO accounts(id, account_ref, telegram_user_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (account, f"tga_{'a' * 25}{account}", 1000 + account, TS, TS),
        )
    conn.execute(
        "INSERT INTO principals(id, principal_ref, principal_key, auth_mode, created_at)"
        " VALUES (1, ?, 'k1', 'local', ?)",
        (f"prn_{'b' * 26}", TS),
    )
    conn.execute(
        "INSERT INTO mcp_clients(id, principal_id, client_ref, auth_kind, auth_binding,"
        " client_kind, enabled, created_at) VALUES (1, 1, ?, 'bearer', 'b1', 'codex_local', 1, ?)",
        (f"tcl_{'c' * 26}", TS),
    )
    for account in accounts:
        conn.execute(
            "INSERT INTO policy_state(principal_id, account_id, mode, policy_epoch,"
            " include_archived, include_private, include_groups, include_channels, updated_at)"
            " VALUES (1, ?, 'allowlist', 1, 0, 1, 1, 1, ?)",
            (account, TS),
        )
    for project_id, account, enabled in projects:
        conn.execute(
            "INSERT INTO projects(id, account_id, project_ref, slug, display_name, enabled,"
            " project_epoch, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                project_id,
                account,
                f"tpr_{'d' * 25}{project_id}",
                f"s{project_id}",
                f"S{project_id}",
                1 if enabled else 0,
                TS,
                TS,
            ),
        )
    for peer_id, account in peers:
        conn.execute(
            "INSERT INTO peers(id, account_id, peer_ref, telegram_peer_type, telegram_peer_id,"
            " first_seen_at, last_seen_at) VALUES (?, ?, ?, 'user', ?, ?, ?)",
            (peer_id, account, f"tgp_{'e' * 25}{peer_id}", 5000 + peer_id, TS, TS),
        )
    conn.commit()


def _add_membership(conn, project_id, peer_id, kind="primary"):
    conn.execute(
        "INSERT INTO project_peers(project_id, peer_id, membership_kind, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (project_id, peer_id, kind, TS, TS),
    )


# --- C4: shared membership --------------------------------------------------


def test_second_project_add_rejected_unless_shared(tmp_path):
    conn = _conn(tmp_path)
    _base(conn, peers=((1, 1),), projects=((1, 1, True), (2, 1, True)))
    _add_membership(conn, 1, 1)
    with pytest.raises(sqlite3.IntegrityError):
        _add_membership(conn, 2, 1)
    _add_membership(conn, 2, 1, kind="shared")


def test_retained_disabled_project_still_blocks_an_undeclared_add(tmp_path):
    """C4: all retained memberships are inspected, not only enabled ones."""
    conn = _conn(tmp_path)
    _base(conn, peers=((1, 1),), projects=((1, 1, False), (2, 1, True)))
    _add_membership(conn, 1, 1)
    with pytest.raises(sqlite3.IntegrityError):
        _add_membership(conn, 2, 1)


def test_enabling_a_project_rejects_undeclared_overlap(tmp_path):
    conn = _conn(tmp_path)
    _base(conn, peers=((1, 1),), projects=((1, 1, True), (2, 1, False)))
    # Built in an order no insert trigger objects to: the disabled project
    # claims the peer first, then the enabled one declares itself shared.
    _add_membership(conn, 2, 1)
    _add_membership(conn, 1, 1, kind="shared")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE projects SET enabled = 1 WHERE id = 2")


def test_changing_a_membership_to_primary_under_overlap_is_rejected(tmp_path):
    conn = _conn(tmp_path)
    _base(conn, peers=((1, 1),), projects=((1, 1, True), (2, 1, True)))
    _add_membership(conn, 1, 1)
    _add_membership(conn, 2, 1, kind="shared")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE project_peers SET membership_kind = 'primary' WHERE project_id = 2")


# --- §12.3 cross-table integrity -------------------------------------------


def test_cross_account_project_peer_insert_fails(tmp_path):
    conn = _conn(tmp_path)
    _base(conn, accounts=(1, 2), peers=((1, 2),), projects=((1, 1, True),))
    with pytest.raises(sqlite3.IntegrityError):
        _add_membership(conn, 1, 1)


def test_client_project_grant_requires_owner_policy_on_that_account(tmp_path):
    conn = _conn(tmp_path)
    _base(conn, accounts=(1,), projects=((1, 1, True),))
    conn.execute(
        "INSERT INTO accounts(id, account_ref, telegram_user_id, created_at, updated_at)"
        " VALUES (9, ?, 1999, ?, ?)",
        (f"tga_{'z' * 26}", TS, TS),
    )
    conn.execute(
        "INSERT INTO projects(id, account_id, project_ref, slug, display_name, enabled,"
        " project_epoch, created_at, updated_at) VALUES (9, 9, ?, 's9', 'S9', 1, 1, ?, ?)",
        (f"tpr_{'z' * 26}", TS, TS),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO client_projects(client_id, project_id, egress_level,"
            " excerpt_max_codepoints, created_at, updated_at)"
            " VALUES (1, 9, 'full_text', NULL, ?, ?)",
            (TS, TS),
        )


def test_message_ref_account_must_match_its_peer(tmp_path):
    conn = _conn(tmp_path)
    _base(conn, accounts=(1, 2), peers=((1, 1),))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO message_refs(account_id, peer_id, message_ref, telegram_message_id,"
            " minted_at, last_used_at) VALUES (2, 1, ?, 77, ?, ?)",
            (f"tgm_{'f' * 26}", TS, TS),
        )


def test_inconsistent_cursor_principal_client_insert_fails(tmp_path):
    conn = _conn(tmp_path)
    _base(conn)
    conn.execute(
        "INSERT INTO principals(id, principal_ref, principal_key, auth_mode, created_at)"
        " VALUES (2, ?, 'k2', 'local', ?)",
        (f"prn_{'y' * 26}", TS),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cursors(cursor_ref, principal_id, client_id, account_id,"
            " security_epoch, policy_epoch, project_scope_digest, tool_name, query_digest,"
            " state_json, created_at, expires_at)"
            " VALUES (?, 2, 1, 1, 1, 1, '0', 'telegram_list_chats', '1', '{}', ?, ?)",
            (f"tgc_{'g' * 26}", TS, LATER),
        )


def test_receipt_principal_must_own_its_client(tmp_path):
    conn = _conn(tmp_path)
    _base(conn)
    conn.execute(
        "INSERT INTO principals(id, principal_ref, principal_key, auth_mode, created_at)"
        " VALUES (2, ?, 'k2', 'local', ?)",
        (f"prn_{'y' * 26}", TS),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO disclosure_receipts(disclosure_ref, committed_at, principal_id,"
            " client_id, account_id, tool_name, security_epoch, policy_epoch,"
            " project_scope_digest, project_count, effective_egress_level, records_disclosed,"
            " bytes_disclosed, partial, commit_status, consent_key_id,"
            " consent_challenge_digest, canonical_result_provenance_digest,"
            " proof_payload_sha256, proof_key_id, proof_signature)"
            " VALUES (?, ?, 2, 1, 1, 'telegram_list_chats', 1, 1, '0', 0, 'metadata_only',"
            " 0, 0, 0, 'committed', 'k', 'd', 'p', 's', 'kid', 'sig')",
            (f"tdr_{'h' * 26}", TS),
        )


# --- open path --------------------------------------------------------------


def test_open_db_sets_and_verifies_foreign_keys_and_sidecar_modes(tmp_path):
    root = tmp_path / "db"
    conn = open_db(root / "meta.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    for suffix in ("", "-wal", "-shm"):
        sidecar = root / ("meta.db" + suffix)
        if sidecar.exists():
            assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600


def test_open_db_refuses_a_symlinked_path(tmp_path):
    real = tmp_path / "real.db"
    real.write_bytes(b"")
    link = tmp_path / "link.db"
    link.symlink_to(real)
    with pytest.raises(StorageError):
        open_db(link)


def test_open_db_refuses_a_world_readable_directory(tmp_path):
    root = tmp_path / "loose"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    with pytest.raises(StorageError):
        open_db(root / "meta.db")


def test_open_db_fails_closed_on_a_corrupt_database(tmp_path):
    root = tmp_path / "db"
    root.mkdir(mode=0o700)
    target = root / "meta.db"
    conn = open_db(target)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    raw = bytearray(target.read_bytes())
    for offset in range(100, min(len(raw), 6000)):
        raw[offset] ^= 0xFF
    target.write_bytes(bytes(raw))
    with pytest.raises(StorageError):
        open_db(target)


def test_require_foreign_keys_refuses_an_unprotected_connection(tmp_path):
    conn = sqlite3.connect(tmp_path / "off.db")
    migrate(conn)
    with pytest.raises(StorageError):
        require_foreign_keys(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    require_foreign_keys(conn)


def test_startup_gc_drops_every_prior_runtime_cursor(tmp_path):
    conn = _conn(tmp_path)
    _base(conn)
    for ref, expires in ((f"tgc_{'a' * 26}", TS), (f"tgc_{'b' * 26}", LATER)):
        conn.execute(
            "INSERT INTO cursors(cursor_ref, principal_id, client_id, account_id,"
            " security_epoch, policy_epoch, project_scope_digest, tool_name, query_digest,"
            " state_json, created_at, expires_at)"
            " VALUES (?, 1, 1, 1, 1, 1, '0', 'telegram_list_chats', '1', '{}', ?, ?)",
            (ref, TS, expires),
        )
    conn.commit()
    assert startup_gc(conn) == 2
    assert conn.execute("SELECT count(*) FROM cursors").fetchone()[0] == 0


def test_hourly_gc_drops_only_expired_cursors(tmp_path):
    conn = _conn(tmp_path)
    _base(conn)
    for ref, expires in ((f"tgc_{'a' * 26}", TS), (f"tgc_{'b' * 26}", LATER)):
        conn.execute(
            "INSERT INTO cursors(cursor_ref, principal_id, client_id, account_id,"
            " security_epoch, policy_epoch, project_scope_digest, tool_name, query_digest,"
            " state_json, created_at, expires_at)"
            " VALUES (?, 1, 1, 1, 1, 1, '0', 'telegram_list_chats', '1', '{}', ?, ?)",
            (ref, TS, expires),
        )
    conn.commit()
    assert purge_expired_cursors(conn, now="2026-09-23T00:00:00Z") == 1
    kept = [row[0] for row in conn.execute("SELECT cursor_ref FROM cursors")]
    assert kept == [f"tgc_{'b' * 26}"]
