"""Task 7: schema transcription, the C2 excerpt-width fix, and migration safety.

The C2 tests below are the plan's pinned bodies with one addition: ``_seed``
also creates the ``policy_state`` row, because spec §12.3 requires a
``client_projects`` row to link a client of the owner principal to a project
on an account that principal has ``policy_state`` for. Without that row the
join is inconsistent and the trigger fires first, which would hide the CHECK
being tested.
"""

import sqlite3

import pytest

from telegram_mcp.storage.migrations import (
    MIGRATIONS,
    REQUIRED_INDEXES,
    SCHEMA_TABLES,
    SCHEMA_VERSION,
    Migration,
    current_version,
    migrate,
)

TS = "2026-09-22T00:00:00Z"


def _seed(conn):
    conn.execute(
        "INSERT INTO accounts(id, account_ref, telegram_user_id, created_at, updated_at)"
        " VALUES (1, 'tga_" + "a" * 26 + "', 1001, '" + TS + "', '" + TS + "')"
    )
    conn.execute(
        "INSERT INTO principals(id, principal_ref, principal_key, auth_mode, created_at)"
        " VALUES (1, 'prn_" + "b" * 26 + "', 'k1', 'local', '" + TS + "')"
    )
    conn.execute(
        "INSERT INTO mcp_clients(id, principal_id, client_ref, auth_kind, auth_binding,"
        " client_kind, enabled, created_at)"
        " VALUES (1, 1, 'tcl_" + "c" * 26 + "', 'bearer', 'b1', 'codex_local', 1, '" + TS + "')"
    )
    conn.execute(
        "INSERT INTO projects(id, account_id, project_ref, slug, display_name, enabled,"
        " project_epoch, created_at, updated_at)"
        " VALUES (1, 1, 'tpr_" + "d" * 26 + "', 's1', 'S1', 1, 1, '" + TS + "', '" + TS + "')"
    )
    conn.execute(
        "INSERT INTO policy_state(principal_id, account_id, mode, policy_epoch,"
        " include_archived, include_private, include_groups, include_channels, updated_at)"
        " VALUES (1, 1, 'allowlist', 1, 0, 1, 1, 1, '" + TS + "')"
    )


def _migrated(tmp_path, name="t.db"):
    conn = sqlite3.connect(tmp_path / name)
    migrate(conn)
    return conn


def test_excerpt_null_rejected(tmp_path):
    conn = _migrated(tmp_path)
    _seed(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO client_projects(client_id, project_id, egress_level,"
            " excerpt_max_codepoints, created_at, updated_at)"
            " VALUES (1, 1, 'excerpt', NULL, '" + TS + "', '" + TS + "')"
        )


def test_excerpt_width_accepted_and_others_reject_limits(tmp_path):
    conn = _migrated(tmp_path)
    _seed(conn)
    conn.execute(
        "INSERT INTO client_projects(client_id, project_id, egress_level,"
        " excerpt_max_codepoints, created_at, updated_at)"
        " VALUES (1, 1, 'excerpt', 200, '" + TS + "', '" + TS + "')"
    )
    conn.execute("DELETE FROM client_projects")
    conn.execute(
        "INSERT INTO client_projects(client_id, project_id, egress_level,"
        " excerpt_max_codepoints, created_at, updated_at)"
        " VALUES (1, 1, 'metadata_only', NULL, '" + TS + "', '" + TS + "')"
    )
    conn.execute("DELETE FROM client_projects")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO client_projects(client_id, project_id, egress_level,"
            " excerpt_max_codepoints, created_at, updated_at)"
            " VALUES (1, 1, 'full_text', 200, '" + TS + "', '" + TS + "')"
        )


@pytest.mark.parametrize("width", [63, 4001, 0, -1])
def test_excerpt_width_range_is_enforced(tmp_path, width):
    conn = _migrated(tmp_path)
    _seed(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO client_projects(client_id, project_id, egress_level,"
            " excerpt_max_codepoints, created_at, updated_at)"
            f" VALUES (1, 1, 'excerpt', {width}, '{TS}', '{TS}')"
        )


def test_all_nineteen_tables_exist(tmp_path):
    conn = _migrated(tmp_path)
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert len(SCHEMA_TABLES) == 19
    assert set(SCHEMA_TABLES) <= names


def test_required_indexes_exist(tmp_path):
    conn = _migrated(tmp_path)
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert set(REQUIRED_INDEXES) <= names


def test_security_state_singleton_is_created_at_one_unlocked(tmp_path):
    conn = _migrated(tmp_path)
    rows = list(conn.execute("SELECT singleton_id, security_epoch, locked FROM security_state"))
    assert rows == [(1, 1, 0)]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO security_state(singleton_id, security_epoch, locked, updated_at)"
            " VALUES (2, 1, 0, '" + TS + "')"
        )


def test_migration_is_rerunnable_and_records_its_version(tmp_path):
    conn = _migrated(tmp_path)
    assert current_version(conn) == SCHEMA_VERSION
    assert migrate(conn) == SCHEMA_VERSION
    assert migrate(conn) == SCHEMA_VERSION
    assert conn.execute("SELECT count(*) FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("SELECT count(*) FROM security_state").fetchone()[0] == 1


def test_failing_migration_rolls_back_and_leaves_version_unchanged(tmp_path):
    conn = sqlite3.connect(tmp_path / "bad.db")
    broken = (
        Migration(
            1,
            (
                "CREATE TABLE scratch_probe (id INTEGER PRIMARY KEY)",
                "CREATE TABLE this is not sql",
            ),
        ),
    )
    with pytest.raises(sqlite3.Error):
        migrate(conn, migrations=broken)
    assert current_version(conn) == 0
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "scratch_probe" not in tables


def test_phase_three_tables_migrate_as_reserved_and_stay_empty(tmp_path):
    conn = _migrated(tmp_path)
    _seed(conn)
    # a full Phase-2 shaped flow: grant, peer, membership, cursor row
    conn.execute(
        "INSERT INTO client_projects(client_id, project_id, egress_level,"
        " excerpt_max_codepoints, created_at, updated_at)"
        " VALUES (1, 1, 'full_text', NULL, '" + TS + "', '" + TS + "')"
    )
    conn.execute(
        "INSERT INTO peers(id, account_id, peer_ref, telegram_peer_type, telegram_peer_id,"
        " first_seen_at, last_seen_at)"
        " VALUES (1, 1, 'tgp_" + "e" * 26 + "', 'user', 5001, '" + TS + "', '" + TS + "')"
    )
    conn.execute(
        "INSERT INTO project_peers(project_id, peer_id, membership_kind, created_at, updated_at)"
        " VALUES (1, 1, 'primary', '" + TS + "', '" + TS + "')"
    )
    conn.execute(
        "INSERT INTO cursors(cursor_ref, principal_id, client_id, account_id, security_epoch,"
        " policy_epoch, project_scope_digest, tool_name, query_digest, state_json,"
        " created_at, expires_at)"
        " VALUES ('tgc_" + "f" * 26 + "', 1, 1, 1, 1, 1, '0', 'telegram_list_chats', '1', '{}',"
        " '" + TS + "', '" + TS + "')"
    )
    conn.commit()
    for table in (
        "disclosure_receipts",
        "exposure_ledger",
        "audit_checkpoints",
        "audit_events",
        "verification_keys",
    ):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_migrations_declare_one_version_each_in_order():
    versions = [m.version for m in MIGRATIONS]
    assert versions == sorted(set(versions))
    assert versions[-1] == SCHEMA_VERSION
