"""Shared authority rows for tests that need a §12.3-valid receipt.

Importable as ``tests.authority_fixtures`` because ``tests`` is a package;
``conftest`` is not importable from a subdirectory, so the helpers live here.
"""

from __future__ import annotations

PROJECT_REF = "tpr_" + "a" * 26


def seed_authority_rows(conn) -> dict[str, int]:
    """Insert one account, principal, client, policy row and project.

    ``disclosure_receipts`` carries a §12.3 tuple-consistency trigger that
    rejects a receipt whose client belongs to a different principal, or whose
    (principal, account) pair has no ``policy_state`` row. Hand-written SQL in
    each test would trip it for reasons unrelated to what is under test, so
    the rows are built once here.
    """
    now = "2026-09-22T00:00:00Z"
    conn.execute(
        "INSERT INTO accounts (account_ref, telegram_user_id, created_at, updated_at)"
        " VALUES (?, 1, ?, ?)",
        ("tga_" + "a" * 26, now, now),
    )
    conn.execute(
        "INSERT INTO principals (principal_ref, principal_key, auth_mode, created_at)"
        " VALUES (?, 'key', 'bearer', ?)",
        ("prn_" + "a" * 26, now),
    )
    conn.execute(
        "INSERT INTO mcp_clients (principal_id, client_ref, auth_kind, auth_binding,"
        " client_kind, created_at) VALUES (1, ?, 'bearer', 'binding', 'codex_local', ?)",
        ("tcl_" + "a" * 26, now),
    )
    conn.execute(
        "INSERT INTO policy_state (principal_id, account_id, mode, policy_epoch,"
        " include_archived, include_private, include_groups, include_channels, updated_at)"
        " VALUES (1, 1, 'allowlist', 1, 0, 1, 1, 1, ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO projects (account_id, project_ref, slug, display_name, enabled,"
        " project_epoch, created_at, updated_at) VALUES (1, ?, 'alpha', 'Alpha', 1, 1, ?, ?)",
        (PROJECT_REF, now, now),
    )
    conn.commit()
    return {"account_id": 1, "principal_id": 1, "client_id": 1}


def insert_committed_receipt(conn, *, disclosure_ref: str, records: int, size: int) -> None:
    """Insert one committed receipt row that satisfies the §12.3 trigger."""
    conn.execute(
        "INSERT INTO disclosure_receipts (disclosure_ref, committed_at, principal_id,"
        " client_id, account_id, tool_name, security_epoch, policy_epoch,"
        " project_scope_digest, project_count, effective_egress_level, records_disclosed,"
        " bytes_disclosed, partial, commit_status, consent_key_id, consent_challenge_digest,"
        " canonical_result_provenance_digest, proof_payload_sha256, proof_key_id,"
        " proof_signature) VALUES (?, '2026-09-22T00:00:00Z', 1, 1, 1,"
        " 'telegram_get_messages', 1, 1, 'digest', 1, 'metadata_only', ?, ?, 0, 'committed',"
        " 'consent-key', 'challenge', 'provenance', 'payload-sha', 'proof-key', 'signature')",
        (disclosure_ref, records, size),
    )
    conn.commit()


def seed_project_world(conn) -> dict[str, str]:
    """A granted project with three member chats and one allowed non-member."""
    from comms.transports.telegram.storage.refstore import RefStore

    now = "2026-09-23T00:00:00Z"
    conn.execute(
        "INSERT INTO client_projects (client_id, project_id, can_read, can_cross_search,"
        " egress_level, excerpt_max_codepoints, created_at, updated_at)"
        " VALUES (1, 1, 1, 0, 'full_text', NULL, ?, ?)",
        (now, now),
    )
    conn.commit()
    refs = RefStore(conn, account_id=1)
    out: dict[str, str] = {}
    for peer_type, peer_id, name, member in (
        ("user", 100, "Ali", True),
        ("user", 101, "Bob", False),
        ("channel", 7, "News", True),
        ("chat", 9, "Team", True),
    ):
        row = refs.ensure_peer(peer_type, peer_id, display_name=name, username=None)
        out[row.identity] = row.peer_ref
        conn.execute(
            "INSERT INTO peer_policy (principal_id, account_id, telegram_peer_type,"
            " telegram_peer_id, decision, created_at, updated_at)"
            " VALUES (1, 1, ?, ?, 'allow', ?, ?)",
            (peer_type, peer_id, now, now),
        )
        if member:
            conn.execute(
                "INSERT INTO project_peers (project_id, peer_id, membership_kind, created_at,"
                " updated_at) VALUES (1, ?, 'primary', ?, ?)",
                (row.row_id, now, now),
            )
        conn.commit()  # RefStore opens BEGIN IMMEDIATE: no implicit transaction may be open
    return out


BETA_REF = "tpr_" + "b" * 26


def seed_second_project(conn) -> None:
    """Project Beta: channel:7 shared with Alpha, plus Bob. Both grants allow cross search.

    Alpha is full_text; Beta is metadata_only, so a record from the shared
    channel must come out as metadata only (spec §23B.2 intersection).
    """
    now = "2026-09-23T00:00:00Z"
    conn.execute(
        "INSERT INTO projects (account_id, project_ref, slug, display_name, enabled,"
        " project_epoch, created_at, updated_at) VALUES (1, ?, 'beta', 'Beta', 1, 1, ?, ?)",
        (BETA_REF, now, now),
    )
    conn.execute("UPDATE client_projects SET can_cross_search = 1")
    conn.execute(
        "INSERT INTO client_projects (client_id, project_id, can_read, can_cross_search,"
        " egress_level, excerpt_max_codepoints, created_at, updated_at)"
        " VALUES (1, 2, 1, 1, 'metadata_only', NULL, ?, ?)",
        (now, now),
    )
    for identity in ("channel:7", "user:101"):
        peer_type, _, raw = identity.partition(":")
        row = conn.execute(
            "SELECT id FROM peers WHERE telegram_peer_type = ? AND telegram_peer_id = ?",
            (peer_type, int(raw)),
        ).fetchone()
        conn.execute(
            "INSERT INTO project_peers (project_id, peer_id, membership_kind, created_at,"
            " updated_at) VALUES (2, ?, 'shared', ?, ?)",
            (row[0], now, now),
        )
    conn.commit()
