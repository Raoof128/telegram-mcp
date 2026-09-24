"""Transactional schema migrations: the 19 spec tables, indexes, triggers.

Every ``CREATE TABLE`` below is transcribed from spec §12.2 in table order.
Two corrections from the release roadmap are applied, and nothing else:

* **C2 (excerpt width).** The spec's own CHECK reads
  ``(egress_level = 'excerpt' AND excerpt_max_codepoints BETWEEN 64 AND
  4000) OR (egress_level IN ('metadata_only','full_text') AND
  excerpt_max_codepoints IS NULL)``. With ``egress_level='excerpt'`` and a
  NULL width the first branch evaluates to NULL and the second to false, so
  the whole CHECK is NULL — and SQLite admits a row whose CHECK is NULL.
  An ``excerpt`` grant with no width would therefore pass. The transcription
  adds the explicit ``excerpt_max_codepoints IS NOT NULL`` conjunct; the
  64-4000 range is preserved exactly.
* **C4 (shared membership).** Spec §12.2 speaks of a peer already present in
  another *enabled* project; the triggers here inspect every *retained*
  membership, enabled or not, and additionally reject enabling a project
  whose overlapping memberships are not declared ``shared``.

Cross-table integrity (spec §12.3) is enforced by triggers, not by
application code, because §12.3 requires direct inconsistent writes to fail:
``project_peers`` must join a project and peer on the same account;
``client_projects`` must join a client of the owner principal to a project on
an account that principal has ``policy_state`` for; ``message_refs.account_id``
must match its peer's account; and cursor/receipt principal-client-account
tuples must be mutually consistent.

The five Phase-3 tables (``disclosure_receipts``, ``exposure_ledger``,
``verification_keys``, ``audit_checkpoints``, ``audit_events``) are created as
reserved schema only. Phase 2 writes no row to any of them.

Default deny is row absence (design §6 ruling): no migration may create a
``client_projects`` row. ``can_read DEFAULT 1`` applies only to grants the
operator explicitly creates.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

__all__ = [
    "MIGRATIONS",
    "REQUIRED_INDEXES",
    "SCHEMA_TABLES",
    "SCHEMA_VERSION",
    "Migration",
    "current_version",
    "migrate",
]

SCHEMA_VERSION = (
    3  # 2: versioned receipts (5b-3 §2.2); 3: the v0.3 legacy audit seal (comms v0.3 A6)
)

# Spec §12.2 table order.
SCHEMA_TABLES: tuple[str, ...] = (
    "schema_version",
    "accounts",
    "principals",
    "mcp_clients",
    "projects",
    "project_peers",
    "client_projects",
    "peers",
    "policy_state",
    "peer_policy",
    "message_refs",
    "cursors",
    "security_state",
    "disclosure_receipts",
    "exposure_ledger",
    "verification_keys",
    "audit_checkpoints",
    "audit_events",
    "settings",
)

# Spec §12.4 minimum index set.
REQUIRED_INDEXES: tuple[str, ...] = (
    "idx_cursors_expires_at",
    "idx_message_refs_last_used_at",
    "idx_exposure_ledger_window",
    "idx_project_peers_peer_id",
    "idx_client_projects_project_id",
    "idx_audit_events_chain",
)

_TABLES = (
    """
    CREATE TABLE schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE accounts (
        id INTEGER PRIMARY KEY,
        account_ref TEXT NOT NULL UNIQUE,
        telegram_user_id INTEGER NOT NULL UNIQUE,
        label TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE principals (
        id INTEGER PRIMARY KEY,
        principal_ref TEXT NOT NULL UNIQUE,
        principal_key TEXT NOT NULL UNIQUE,
        auth_mode TEXT NOT NULL,
        label TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE mcp_clients (
        id INTEGER PRIMARY KEY,
        principal_id INTEGER NOT NULL,
        client_ref TEXT NOT NULL UNIQUE,
        auth_kind TEXT NOT NULL CHECK(auth_kind IN ('mtls','bearer')),
        auth_binding TEXT NOT NULL UNIQUE,
        client_kind TEXT NOT NULL
            CHECK(client_kind IN ('openai_tunnel','codex_local','claude_code_local')),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
        created_at TEXT NOT NULL,
        rotated_at TEXT,
        last_seen_at TEXT,
        FOREIGN KEY(principal_id) REFERENCES principals(id)
    )
    """,
    """
    CREATE TABLE projects (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL,
        project_ref TEXT NOT NULL UNIQUE,
        slug TEXT NOT NULL,
        display_name TEXT NOT NULL,
        description TEXT,
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
        project_epoch INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(account_id, slug),
        FOREIGN KEY(account_id) REFERENCES accounts(id)
    )
    """,
    """
    CREATE TABLE project_peers (
        project_id INTEGER NOT NULL,
        peer_id INTEGER NOT NULL,
        membership_kind TEXT NOT NULL CHECK(membership_kind IN ('primary','shared')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(project_id, peer_id),
        FOREIGN KEY(project_id) REFERENCES projects(id),
        FOREIGN KEY(peer_id) REFERENCES peers(id)
    )
    """,
    """
    CREATE TABLE client_projects (
        client_id INTEGER NOT NULL,
        project_id INTEGER NOT NULL,
        can_read INTEGER NOT NULL DEFAULT 1 CHECK(can_read IN (0,1)),
        can_cross_search INTEGER NOT NULL DEFAULT 0 CHECK(can_cross_search IN (0,1)),
        egress_level TEXT NOT NULL
            CHECK(egress_level IN ('metadata_only','excerpt','full_text')),
        excerpt_max_codepoints INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK(
          (egress_level = 'excerpt'
             AND excerpt_max_codepoints IS NOT NULL
             AND excerpt_max_codepoints BETWEEN 64 AND 4000) OR
          (egress_level IN ('metadata_only','full_text')
             AND excerpt_max_codepoints IS NULL)
        ),
        PRIMARY KEY(client_id, project_id),
        FOREIGN KEY(client_id) REFERENCES mcp_clients(id),
        FOREIGN KEY(project_id) REFERENCES projects(id)
    )
    """,
    """
    CREATE TABLE peers (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL,
        peer_ref TEXT NOT NULL UNIQUE,
        telegram_peer_type TEXT NOT NULL,
        telegram_peer_id INTEGER NOT NULL,
        display_name_cache TEXT,
        username_cache TEXT,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        UNIQUE(account_id, telegram_peer_type, telegram_peer_id),
        FOREIGN KEY(account_id) REFERENCES accounts(id)
    )
    """,
    """
    CREATE TABLE policy_state (
        principal_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        mode TEXT NOT NULL CHECK(mode IN ('allowlist','all_cloud_chats')),
        policy_epoch INTEGER NOT NULL CHECK(policy_epoch >= 1),
        include_archived INTEGER NOT NULL CHECK(include_archived IN (0,1)),
        include_private INTEGER NOT NULL CHECK(include_private IN (0,1)),
        include_groups INTEGER NOT NULL CHECK(include_groups IN (0,1)),
        include_channels INTEGER NOT NULL CHECK(include_channels IN (0,1)),
        updated_at TEXT NOT NULL,
        PRIMARY KEY(principal_id, account_id),
        FOREIGN KEY(principal_id) REFERENCES principals(id),
        FOREIGN KEY(account_id) REFERENCES accounts(id)
    )
    """,
    """
    CREATE TABLE peer_policy (
        principal_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        telegram_peer_type TEXT NOT NULL,
        telegram_peer_id INTEGER NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('allow','deny')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(principal_id, account_id, telegram_peer_type, telegram_peer_id),
        FOREIGN KEY(principal_id) REFERENCES principals(id),
        FOREIGN KEY(account_id) REFERENCES accounts(id)
    )
    """,
    """
    CREATE TABLE message_refs (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL,
        peer_id INTEGER NOT NULL,
        message_ref TEXT NOT NULL UNIQUE,
        telegram_message_id INTEGER NOT NULL,
        minted_at TEXT NOT NULL,
        last_used_at TEXT NOT NULL,
        UNIQUE(account_id, peer_id, telegram_message_id),
        FOREIGN KEY(account_id) REFERENCES accounts(id),
        FOREIGN KEY(peer_id) REFERENCES peers(id)
    )
    """,
    """
    CREATE TABLE cursors (
        id INTEGER PRIMARY KEY,
        cursor_ref TEXT NOT NULL UNIQUE,
        principal_id INTEGER NOT NULL,
        client_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        security_epoch INTEGER NOT NULL,
        policy_epoch INTEGER NOT NULL,
        project_scope_digest TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        query_digest TEXT NOT NULL,
        state_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY(principal_id) REFERENCES principals(id),
        FOREIGN KEY(client_id) REFERENCES mcp_clients(id),
        FOREIGN KEY(account_id) REFERENCES accounts(id)
    )
    """,
    """
    CREATE TABLE security_state (
        singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
        security_epoch INTEGER NOT NULL,
        locked INTEGER NOT NULL CHECK(locked IN (0,1)),
        locked_at TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE disclosure_receipts (
        id INTEGER PRIMARY KEY,
        disclosure_ref TEXT NOT NULL UNIQUE,
        committed_at TEXT NOT NULL,
        principal_id INTEGER NOT NULL,
        client_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        tool_name TEXT NOT NULL,
        security_epoch INTEGER NOT NULL,
        policy_epoch INTEGER NOT NULL,
        project_scope_digest TEXT NOT NULL,
        project_count INTEGER NOT NULL CHECK(project_count >= 0 AND project_count <= 8),
        effective_egress_level TEXT NOT NULL
            CHECK(effective_egress_level IN ('metadata_only','excerpt','full_text')),
        records_disclosed INTEGER NOT NULL CHECK(records_disclosed >= 0),
        bytes_disclosed INTEGER NOT NULL CHECK(bytes_disclosed >= 0),
        partial INTEGER NOT NULL CHECK(partial IN (0,1)),
        commit_status TEXT NOT NULL CHECK(commit_status = 'committed'),
        consent_key_id TEXT NOT NULL,
        consent_challenge_digest TEXT NOT NULL,
        canonical_result_provenance_digest TEXT NOT NULL,
        canonical_coverage_digest TEXT,
        proof_payload_sha256 TEXT NOT NULL,
        proof_key_id TEXT NOT NULL,
        proof_signature TEXT NOT NULL,
        FOREIGN KEY(principal_id) REFERENCES principals(id),
        FOREIGN KEY(client_id) REFERENCES mcp_clients(id),
        FOREIGN KEY(account_id) REFERENCES accounts(id)
    )
    """,
    """
    CREATE TABLE exposure_ledger (
        id INTEGER PRIMARY KEY,
        disclosure_ref TEXT NOT NULL,
        ts TEXT NOT NULL,
        client_id INTEGER NOT NULL,
        budget_subject_kind TEXT NOT NULL
            CHECK(budget_subject_kind IN ('project','client_global')),
        budget_subject_digest TEXT NOT NULL,
        records_disclosed INTEGER NOT NULL CHECK(records_disclosed >= 0),
        bytes_disclosed INTEGER NOT NULL CHECK(bytes_disclosed >= 0),
        effective_egress_level TEXT NOT NULL
            CHECK(effective_egress_level IN ('metadata_only','excerpt','full_text')),
        UNIQUE(disclosure_ref, budget_subject_kind, budget_subject_digest),
        FOREIGN KEY(client_id) REFERENCES mcp_clients(id),
        FOREIGN KEY(disclosure_ref) REFERENCES disclosure_receipts(disclosure_ref)
    )
    """,
    """
    CREATE TABLE verification_keys (
        key_id TEXT PRIMARY KEY,
        purpose TEXT NOT NULL
            CHECK(purpose IN ('disclosure_proof','audit_checkpoint','policy_backup')),
        algorithm TEXT NOT NULL,
        public_key_b64url TEXT NOT NULL,
        activated_at TEXT NOT NULL,
        retired_at TEXT
    )
    """,
    """
    CREATE TABLE audit_checkpoints (
        id INTEGER PRIMARY KEY,
        checkpoint_ref TEXT NOT NULL UNIQUE,
        chain_epoch INTEGER NOT NULL CHECK(chain_epoch >= 1),
        chain_seq INTEGER NOT NULL CHECK(chain_seq >= 1),
        last_event_id TEXT NOT NULL,
        last_event_mac TEXT NOT NULL,
        created_at TEXT NOT NULL,
        signing_key_id TEXT NOT NULL,
        signature TEXT NOT NULL,
        UNIQUE(chain_epoch, chain_seq)
    )
    """,
    """
    CREATE TABLE audit_events (
        id INTEGER PRIMARY KEY,
        event_id TEXT NOT NULL UNIQUE,
        ts TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        principal_ref TEXT,
        client_ref TEXT,
        account_ref TEXT,
        peer_ref TEXT,
        project_ref TEXT,
        project_count INTEGER
            CHECK(project_count IS NULL OR (project_count >= 0 AND project_count <= 8)),
        policy_epoch INTEGER CHECK(policy_epoch IS NULL OR policy_epoch >= 0),
        result_count INTEGER CHECK(result_count IS NULL OR result_count >= 0),
        duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0),
        telegram_rpc_count INTEGER
            CHECK(telegram_rpc_count IS NULL OR telegram_rpc_count >= 0),
        status TEXT NOT NULL CHECK(status IN ('ok','error','denied','cancelled')),
        error_code TEXT,
        disclosure_ref TEXT,
        chain_epoch INTEGER NOT NULL CHECK(chain_epoch >= 1),
        chain_seq INTEGER NOT NULL CHECK(chain_seq >= 1),
        prev_event_mac TEXT NOT NULL,
        event_mac TEXT NOT NULL,
        FOREIGN KEY(disclosure_ref) REFERENCES disclosure_receipts(disclosure_ref),
        UNIQUE(chain_epoch, chain_seq)
    )
    """,
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
)

_INDEXES = (
    "CREATE INDEX idx_cursors_expires_at ON cursors(expires_at)",
    "CREATE INDEX idx_message_refs_last_used_at ON message_refs(last_used_at)",
    (
        "CREATE INDEX idx_exposure_ledger_window"
        " ON exposure_ledger(client_id, budget_subject_kind, budget_subject_digest, ts)"
    ),
    "CREATE INDEX idx_project_peers_peer_id ON project_peers(peer_id)",
    "CREATE INDEX idx_client_projects_project_id ON client_projects(project_id)",
    "CREATE INDEX idx_audit_events_chain ON audit_events(chain_epoch, chain_seq)",
)

# §12.3 same-account rule for project_peers, in both write directions.
_SAME_ACCOUNT_WHEN = (
    "(SELECT account_id FROM projects WHERE id = NEW.project_id)"
    " IS NOT (SELECT account_id FROM peers WHERE id = NEW.peer_id)"
)
# C4: any retained membership of the same peer in another project counts.
_OVERLAP_WHEN = (
    "NEW.membership_kind <> 'shared' AND EXISTS ("
    "SELECT 1 FROM project_peers other"
    " WHERE other.peer_id = NEW.peer_id AND other.project_id <> NEW.project_id)"
)
# §12.3 client_projects rule: owner's client, and a project on an account the
# owner principal has policy_state for.
_GRANT_WHEN = (
    "NOT EXISTS ("
    "SELECT 1 FROM mcp_clients c"
    " JOIN projects p ON p.id = NEW.project_id"
    " JOIN policy_state ps ON ps.principal_id = c.principal_id AND ps.account_id = p.account_id"
    " WHERE c.id = NEW.client_id)"
)
_MESSAGE_REF_WHEN = "(SELECT account_id FROM peers WHERE id = NEW.peer_id) IS NOT NEW.account_id"
# §12.3 receipt/cursor tuple consistency.
_TUPLE_WHEN = (
    "(SELECT principal_id FROM mcp_clients WHERE id = NEW.client_id) IS NOT NEW.principal_id"
    " OR NOT EXISTS (SELECT 1 FROM policy_state ps"
    " WHERE ps.principal_id = NEW.principal_id AND ps.account_id = NEW.account_id)"
)


def _guard(name: str, event: str, table: str, when: str, message: str) -> str:
    return (
        f"CREATE TRIGGER {name} BEFORE {event} ON {table} FOR EACH ROW"
        f" WHEN {when}"
        f" BEGIN SELECT RAISE(ABORT, '{message}'); END"
    )


# One definition, used by migration 1 and re-created by the v2 rebuild.
_RECEIPTS_TRIGGER = _guard(
    "disclosure_receipts_tuple_consistency_insert",
    "INSERT",
    "disclosure_receipts",
    _TUPLE_WHEN,
    "disclosure_receipts: principal, client and account must be consistent",
)

_TRIGGERS = (
    _guard(
        "project_peers_same_account_insert",
        "INSERT",
        "project_peers",
        _SAME_ACCOUNT_WHEN,
        "project_peers: project and peer must share an account",
    ),
    _guard(
        "project_peers_same_account_update",
        "UPDATE",
        "project_peers",
        _SAME_ACCOUNT_WHEN,
        "project_peers: project and peer must share an account",
    ),
    _guard(
        "project_peers_shared_required_insert",
        "INSERT",
        "project_peers",
        _OVERLAP_WHEN,
        "project_peers: overlapping membership must be declared shared",
    ),
    _guard(
        "project_peers_shared_required_update",
        "UPDATE",
        "project_peers",
        _OVERLAP_WHEN,
        "project_peers: overlapping membership must be declared shared",
    ),
    _guard(
        "projects_enable_requires_declared_overlap",
        "UPDATE OF enabled",
        "projects",
        "NEW.enabled = 1 AND OLD.enabled = 0 AND EXISTS ("
        "SELECT 1 FROM project_peers mine"
        " WHERE mine.project_id = NEW.id AND mine.membership_kind <> 'shared'"
        " AND EXISTS (SELECT 1 FROM project_peers other"
        " WHERE other.peer_id = mine.peer_id AND other.project_id <> mine.project_id))",
        "projects: enabling would activate an undeclared shared membership",
    ),
    _guard(
        "client_projects_owner_consistency_insert",
        "INSERT",
        "client_projects",
        _GRANT_WHEN,
        "client_projects: client and project must belong to the owner principal",
    ),
    _guard(
        "client_projects_owner_consistency_update",
        "UPDATE",
        "client_projects",
        _GRANT_WHEN,
        "client_projects: client and project must belong to the owner principal",
    ),
    _guard(
        "message_refs_account_matches_peer_insert",
        "INSERT",
        "message_refs",
        _MESSAGE_REF_WHEN,
        "message_refs: account must match the referenced peer",
    ),
    _guard(
        "message_refs_account_matches_peer_update",
        "UPDATE",
        "message_refs",
        _MESSAGE_REF_WHEN,
        "message_refs: account must match the referenced peer",
    ),
    _guard(
        "cursors_tuple_consistency_insert",
        "INSERT",
        "cursors",
        _TUPLE_WHEN,
        "cursors: principal, client and account must be consistent",
    ),
    _guard(
        "cursors_tuple_consistency_update",
        "UPDATE",
        "cursors",
        _TUPLE_WHEN,
        "cursors: principal, client and account must be consistent",
    ),
    _RECEIPTS_TRIGGER,
)

# Spec §12.2: the security_state singleton is created transactionally at
# initialisation with security_epoch=1 and locked=0.
_SEEDS = (
    (
        "INSERT INTO security_state(singleton_id, security_epoch, locked, updated_at)"
        " VALUES (1, 1, 0, :now)"
    ),
)


@dataclass(frozen=True)
class Migration:
    """One schema version and the statements that build it.

    ``rebuild`` marks SQLite's documented 12-step table rebuild: foreign keys
    are switched off *outside* the transaction (a pragma inside one is a
    no-op), and ``PRAGMA foreign_key_check`` must be empty before commit.
    """

    version: int
    statements: tuple[str, ...]
    rebuild: bool = False


_V1_RECEIPT_COLUMNS = (
    "id, disclosure_ref, committed_at, principal_id, client_id, account_id, tool_name,"
    " security_epoch, policy_epoch, project_scope_digest, project_count, effective_egress_level,"
    " records_disclosed, bytes_disclosed, partial, commit_status, consent_key_id,"
    " consent_challenge_digest, canonical_result_provenance_digest, canonical_coverage_digest,"
    " proof_payload_sha256, proof_key_id, proof_signature"
)

# Comms 5b-3 design §2.1-§2.2: versioned receipts. v1 rows keep their consent
# values byte-identical (proof_version defaults to 1); v2 (owner_direct) rows
# carry no consent and a soft-threshold flag. The table CHECK makes a
# version/shape disagreement unrepresentable.
_RECEIPTS_V2: tuple[str, ...] = (
    """
    CREATE TABLE disclosure_receipts_v2 (
        id INTEGER PRIMARY KEY,
        disclosure_ref TEXT NOT NULL UNIQUE,
        committed_at TEXT NOT NULL,
        principal_id INTEGER NOT NULL,
        client_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        tool_name TEXT NOT NULL,
        security_epoch INTEGER NOT NULL,
        policy_epoch INTEGER NOT NULL,
        project_scope_digest TEXT NOT NULL,
        project_count INTEGER NOT NULL CHECK(project_count >= 0 AND project_count <= 8),
        effective_egress_level TEXT NOT NULL
            CHECK(effective_egress_level IN ('metadata_only','excerpt','full_text')),
        records_disclosed INTEGER NOT NULL CHECK(records_disclosed >= 0),
        bytes_disclosed INTEGER NOT NULL CHECK(bytes_disclosed >= 0),
        partial INTEGER NOT NULL CHECK(partial IN (0,1)),
        commit_status TEXT NOT NULL CHECK(commit_status = 'committed'),
        consent_key_id TEXT,
        consent_challenge_digest TEXT,
        canonical_result_provenance_digest TEXT NOT NULL,
        canonical_coverage_digest TEXT,
        proof_payload_sha256 TEXT NOT NULL,
        proof_key_id TEXT NOT NULL,
        proof_signature TEXT NOT NULL,
        proof_version INTEGER NOT NULL DEFAULT 1 CHECK(proof_version IN (1,2)),
        soft_threshold_exceeded INTEGER
            CHECK(soft_threshold_exceeded IS NULL OR soft_threshold_exceeded IN (0,1)),
        CHECK(
          (proof_version = 1 AND consent_key_id IS NOT NULL
             AND consent_challenge_digest IS NOT NULL AND soft_threshold_exceeded IS NULL) OR
          (proof_version = 2 AND consent_key_id IS NULL
             AND consent_challenge_digest IS NULL AND soft_threshold_exceeded IS NOT NULL)
        ),
        FOREIGN KEY(principal_id) REFERENCES principals(id),
        FOREIGN KEY(client_id) REFERENCES mcp_clients(id),
        FOREIGN KEY(account_id) REFERENCES accounts(id)
    )
    """,
    (
        f"INSERT INTO disclosure_receipts_v2 ({_V1_RECEIPT_COLUMNS}, proof_version)"
        f" SELECT {_V1_RECEIPT_COLUMNS}, 1 FROM disclosure_receipts"
    ),
    "DROP TABLE disclosure_receipts",
    "ALTER TABLE disclosure_receipts_v2 RENAME TO disclosure_receipts",
    _RECEIPTS_TRIGGER,
)


# comms v0.3 cutover (spec A6, G3): the legacy seal is enforced by the database itself.
_V03_SEAL = (
    """
    CREATE TRIGGER legacy_audit_sealed BEFORE INSERT ON audit_events
      WHEN (SELECT value_json FROM settings WHERE key = 'audit.append_state') = '"sealed"'
      BEGIN SELECT RAISE(ABORT, 'legacy chain is sealed'); END
    """,
    """
    CREATE TRIGGER legacy_seal_one_way_u BEFORE UPDATE ON settings
      WHEN OLD.key = 'audit.append_state' AND OLD.value_json = '"sealed"'
      BEGIN SELECT RAISE(ABORT, 'legacy seal is one-way (sealed)'); END
    """,
    """
    CREATE TRIGGER legacy_seal_one_way_d BEFORE DELETE ON settings
      WHEN OLD.key = 'audit.append_state' AND OLD.value_json = '"sealed"'
      BEGIN SELECT RAISE(ABORT, 'legacy seal is one-way (sealed)'); END
    """,
)

MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, _TABLES + _INDEXES + _TRIGGERS + _SEEDS),
    Migration(2, _RECEIPTS_V2, rebuild=True),
    Migration(3, _V03_SEAL),
)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def current_version(conn: sqlite3.Connection) -> int:
    """Highest applied schema version, or 0 before any migration."""
    try:
        row = conn.execute("SELECT max(version) FROM schema_version").fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row and row[0] is not None else 0


def migrate(conn: sqlite3.Connection, *, migrations: tuple[Migration, ...] = MIGRATIONS) -> int:
    """Apply pending migrations, each in its own transaction; return the new version.

    Rerunnable: already-applied versions are skipped. A failing statement
    rolls that migration back, so ``schema_version`` never advances past a
    partially built schema; earlier versions stay applied and consistent.
    A ``rebuild`` migration runs with foreign keys off (set outside the
    transaction) and must leave ``PRAGMA foreign_key_check`` empty.
    """
    pending = [m for m in migrations if m.version > current_version(conn)]
    if not pending:
        return current_version(conn)
    conn.commit()  # close any implicit transaction before an explicit one
    now = _now_iso()
    prior_fk = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    for migration in sorted(pending, key=lambda m: m.version):
        if migration.rebuild:
            conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN")
            for statement in migration.statements:
                conn.execute(statement, {"now": now} if ":now" in statement else ())
            if migration.rebuild and conn.execute("PRAGMA foreign_key_check").fetchall():
                raise sqlite3.IntegrityError("foreign key check failed after rebuild")
            conn.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                (migration.version, now),
            )
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise
        finally:
            if migration.rebuild:
                # Restore the caller's setting; migrate() never changes it.
                conn.execute(f"PRAGMA foreign_keys = {'ON' if prior_fk else 'OFF'}")
                if int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) != prior_fk:
                    raise sqlite3.IntegrityError("foreign-key setting was not restored")
    return current_version(conn)
