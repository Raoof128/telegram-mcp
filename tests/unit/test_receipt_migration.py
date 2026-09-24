"""Migration v2 keeps every v1 receipt byte-identical and verifiable (5b-3 design §2.2, A2, G1)."""

import base64
import hashlib
import secrets
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.transports.telegram.disclosure import receipts
from comms.transports.telegram.disclosure.audit.chain import (
    EVENT_COLUMNS,
    append_event,
    immediate_transaction,
    mint_event_id,
    verify_chain,
)
from comms.transports.telegram.disclosure.keys import publish_verification_key
from comms.transports.telegram.disclosure.verify import verify_persisted_receipt
from comms.transports.telegram.storage.migrations import MIGRATIONS, migrate
from tests.authority_fixtures import seed_authority_rows

CHAIN = b"c" * 32
V1_COLUMNS = (
    "disclosure_ref, committed_at, principal_id, client_id, account_id, tool_name,"
    " security_epoch, policy_epoch, project_scope_digest, project_count, effective_egress_level,"
    " records_disclosed, bytes_disclosed, partial, commit_status, consent_key_id,"
    " consent_challenge_digest, canonical_result_provenance_digest, canonical_coverage_digest,"
    " proof_payload_sha256, proof_key_id, proof_signature"
)


def _receipt_objects(conn):
    return sorted(
        (row[0], row[1])
        for row in conn.execute(
            "SELECT type, name FROM sqlite_master"
            " WHERE tbl_name = 'disclosure_receipts' AND type IN ('index', 'trigger')"
        )
    )


def _counts(conn):
    return {
        t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("disclosure_receipts", "exposure_ledger", "audit_events")
    }


@pytest.fixture
def v1_world(tmp_path):
    """A database at schema v1 holding three genuinely signed v1 receipts with children."""
    conn = sqlite3.connect(tmp_path / "m.db")
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn, migrations=MIGRATIONS[:1])
    seed_authority_rows(conn)
    seed = secrets.token_bytes(32)
    raw = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    kid = "ed25519:sha256:" + hashlib.sha256(raw).hexdigest()
    publish_verification_key(
        conn,
        key_id=kid,
        purpose="disclosure_proof",
        algorithm="Ed25519",
        public_key_b64url=base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
        activated_at="2026-09-24T00:00:00Z",
    )
    refs = []
    for i in range(3):
        ref = receipts.mint_disclosure_ref()
        fields = {
            "disclosure_ref": ref,
            "principal_ref": "prn_" + "a" * 26,
            "client_ref": "tcl_" + "a" * 26,
            "account_ref": "tga_" + "a" * 26,
            "tool_name": "telegram_get_messages",
            "security_epoch": 1,
            "policy_epoch": 1,
            "project_scope_digest": "hmac-sha256:" + "0" * 64,
            "project_count": 1,
            "effective_egress_level": "full_text",
            "records_disclosed": 3 + i,
            "bytes_disclosed": 100 + i,
            "partial": False,
            "committed_at": f"2026-09-24T00:00:0{i}Z",
            "consent_key_id": "p256:sha256:" + "1" * 64,
            "consent_challenge_digest": "2" * 64,
            "canonical_result_provenance_digest": "3" * 64,
            "canonical_coverage_digest": None,
        }
        signed = receipts.sign_payload(
            receipts.build_proof_payload(**fields), private_seed=seed, proof_key_id=kid
        )
        with immediate_transaction(conn):
            conn.execute(
                f"INSERT INTO disclosure_receipts ({V1_COLUMNS}) VALUES"
                " (?, ?, 1, 1, 1, ?, 1, 1, ?, 1, 'full_text', ?, ?, 0, 'committed', ?, ?, ?, ?, ?, ?, ?)",
                (
                    ref,
                    fields["committed_at"],
                    fields["tool_name"],
                    fields["project_scope_digest"],
                    fields["records_disclosed"],
                    fields["bytes_disclosed"],
                    fields["consent_key_id"],
                    fields["consent_challenge_digest"],
                    "3" * 64,
                    None,
                    signed["proof_payload_sha256"],
                    kid,
                    signed["proof_signature"],
                ),
            )
            conn.execute(
                "INSERT INTO exposure_ledger (disclosure_ref, ts, client_id, budget_subject_kind,"
                " budget_subject_digest, records_disclosed, bytes_disclosed, effective_egress_level)"
                " VALUES (?, ?, 1, 'client_global', 'd', 3, 100, 'full_text')",
                (ref, fields["committed_at"]),
            )
            event = dict.fromkeys(EVENT_COLUMNS)
            event.update(
                event_id=mint_event_id(),
                ts=fields["committed_at"],
                tool_name="telegram_get_messages",
                status="ok",
                disclosure_ref=ref,
            )
            append_event(conn, CHAIN, event)
        refs.append(ref)
    return conn, refs


def _verify_at_v1_schema(conn, ref: str) -> bool:
    """v1 verification exactly as it ran before 5b-3: the schema has no proof_version yet."""
    row = conn.execute(
        "SELECT r.disclosure_ref, p.principal_ref, c.client_ref, a.account_ref, r.tool_name,"
        " r.security_epoch, r.policy_epoch, r.project_scope_digest, r.project_count,"
        " r.effective_egress_level, r.records_disclosed, r.bytes_disclosed, r.partial,"
        " r.committed_at, r.consent_key_id, r.consent_challenge_digest,"
        " r.canonical_result_provenance_digest, r.canonical_coverage_digest,"
        " r.proof_payload_sha256, r.proof_key_id, r.proof_signature"
        " FROM disclosure_receipts r JOIN principals p ON p.id = r.principal_id"
        " JOIN mcp_clients c ON c.id = r.client_id JOIN accounts a ON a.id = r.account_id"
        " WHERE r.disclosure_ref = ?",
        (ref,),
    ).fetchone()
    names = (
        "disclosure_ref",
        "principal_ref",
        "client_ref",
        "account_ref",
        "tool_name",
        "security_epoch",
        "policy_epoch",
        "project_scope_digest",
        "project_count",
        "effective_egress_level",
        "records_disclosed",
        "bytes_disclosed",
        "partial",
        "committed_at",
        "consent_key_id",
        "consent_challenge_digest",
        "canonical_result_provenance_digest",
        "canonical_coverage_digest",
    )
    fields = dict(zip(names, row[:18], strict=True))
    fields["partial"] = bool(fields["partial"])
    key = conn.execute(
        "SELECT public_key_b64url FROM verification_keys WHERE key_id = ?", (row[19],)
    ).fetchone()
    return receipts.verify_proof(
        receipts.build_proof_payload(**fields),
        proof_signature=row[20],
        proof_payload_sha256=row[18],
        public_key_b64url=key[0],
    )


def test_v1_receipts_verify_before_and_after_the_migration(v1_world):
    conn, refs = v1_world
    assert all(_verify_at_v1_schema(conn, r) for r in refs)
    migrate(conn)
    assert all(verify_persisted_receipt(conn, r) for r in refs)
    assert conn.execute("SELECT DISTINCT proof_version FROM disclosure_receipts").fetchall() == [
        (1,)
    ]
    verify_chain(conn, CHAIN)


def test_the_rebuild_preserves_rows_ids_indexes_triggers_and_counts(v1_world):
    conn, _refs = v1_world
    before_rows = conn.execute(
        f"SELECT id, {V1_COLUMNS} FROM disclosure_receipts ORDER BY id"
    ).fetchall()
    before_objects, before_counts = _receipt_objects(conn), _counts(conn)
    migrate(conn)
    after_rows = conn.execute(
        f"SELECT id, {V1_COLUMNS} FROM disclosure_receipts ORDER BY id"
    ).fetchall()
    assert after_rows == before_rows
    assert _receipt_objects(conn) == before_objects
    assert _counts(conn) == before_counts
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE disclosure_receipts SET consent_key_id = NULL WHERE id = 1",
        "UPDATE disclosure_receipts SET proof_version = 2 WHERE id = 1",
    ],
)
def test_a_v1_row_cannot_masquerade_as_v2(v1_world, tamper):
    conn, _refs = v1_world
    migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(tamper)


def test_children_still_enforce_the_foreign_key(v1_world):
    conn, _refs = v1_world
    migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO exposure_ledger (disclosure_ref, ts, client_id, budget_subject_kind,"
            " budget_subject_digest, records_disclosed, bytes_disclosed, effective_egress_level)"
            " VALUES ('tdr_missing', 't', 1, 'client_global', 'd', 1, 1, 'full_text')"
        )
