"""Receipt proof v2 (owner_direct) and version-dispatched verification (5b-3 design §2.1)."""

import base64
import hashlib
import secrets
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.core.canonical import jcs_dumps
from comms.transports.telegram.disclosure import receipts
from comms.transports.telegram.disclosure.audit.chain import immediate_transaction
from comms.transports.telegram.disclosure.keys import publish_verification_key
from comms.transports.telegram.disclosure.verify import verify_persisted_receipt
from comms.transports.telegram.storage.db import open_db
from tests.authority_fixtures import seed_authority_rows

COMMON = {
    "principal_ref": "prn_" + "a" * 26,
    "client_ref": "tcl_" + "a" * 26,
    "account_ref": "tga_" + "a" * 26,
    "tool_name": "telegram_get_messages",
    "security_epoch": 1,
    "policy_epoch": 2,
    "project_scope_digest": "hmac-sha256:" + "0" * 64,
    "project_count": 1,
    "effective_egress_level": "excerpt",
    "records_disclosed": 4,
    "bytes_disclosed": 321,
    "partial": False,
    "committed_at": "2026-09-24T00:00:00Z",
    "canonical_result_provenance_digest": "3" * 64,
    "canonical_coverage_digest": None,
}
V1 = {
    **COMMON,
    "disclosure_ref": "tdr_" + "a" * 26,
    "consent_key_id": "p256:sha256:" + "1" * 64,
    "consent_challenge_digest": "2" * 64,
}
# Captured from the unchanged v1 builder before 5b-3 Task 3 touched receipts.py.
V1_DIGEST = "af00e9731743c883b86a393d9bbed4abaed821cb2d58aec140b2e74fb28b2c42"


def test_v1_payload_bytes_are_unchanged():
    assert hashlib.sha256(jcs_dumps(receipts.build_proof_payload(**V1))).hexdigest() == V1_DIGEST


def test_v2_payload_is_owner_direct_and_carries_no_consent():
    payload = receipts.build_proof_payload_v2(
        **COMMON, disclosure_ref="tdr_" + "b" * 26, soft_threshold_exceeded=True
    )
    assert payload["schema"] == "tg-mcp-disclosure/v2"
    assert payload["authorization_mode"] == "owner_direct"
    assert payload["soft_threshold_exceeded"] is True
    assert not any(key.startswith("consent") for key in payload)


def test_v2_refuses_consent_fields_and_a_non_boolean_flag():
    with pytest.raises(receipts.ReceiptError):
        receipts.build_proof_payload_v2(
            **COMMON,
            disclosure_ref="tdr_" + "b" * 26,
            soft_threshold_exceeded=True,
            consent_key_id="x",
        )
    with pytest.raises(receipts.ReceiptError):
        receipts.build_proof_payload_v2(
            **COMMON, disclosure_ref="tdr_" + "b" * 26, soft_threshold_exceeded=1
        )


def test_a_v2_payload_claiming_consent_never_verifies():
    seed = secrets.token_bytes(32)
    payload = receipts.build_proof_payload_v2(
        **COMMON, disclosure_ref="tdr_" + "b" * 26, soft_threshold_exceeded=False
    )
    signed = receipts.sign_payload(payload, private_seed=seed, proof_key_id="k")
    public = (
        base64.urlsafe_b64encode(
            Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
        )
        .rstrip(b"=")
        .decode()
    )
    forged = {**payload, "authorization_mode": "consent"}
    assert receipts.verify_proof(payload, public_key_b64url=public, **_meta(signed))
    assert not receipts.verify_proof(forged, public_key_b64url=public, **_meta(signed))


def _meta(signed):
    return {
        "proof_signature": signed["proof_signature"],
        "proof_payload_sha256": signed["proof_payload_sha256"],
    }


@pytest.fixture
def world(tmp_path):
    conn = open_db(tmp_path / "m.db")
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
    return conn, seed, kid


def _insert_v2(conn, seed, kid, *, soft: bool) -> str:
    ref = receipts.mint_disclosure_ref()
    payload = receipts.build_proof_payload_v2(
        **COMMON, disclosure_ref=ref, soft_threshold_exceeded=soft
    )
    signed = receipts.sign_payload(payload, private_seed=seed, proof_key_id=kid)
    with immediate_transaction(conn):
        conn.execute(
            "INSERT INTO disclosure_receipts (disclosure_ref, committed_at, principal_id, client_id,"
            " account_id, tool_name, security_epoch, policy_epoch, project_scope_digest, project_count,"
            " effective_egress_level, records_disclosed, bytes_disclosed, partial, commit_status,"
            " consent_key_id, consent_challenge_digest, canonical_result_provenance_digest,"
            " canonical_coverage_digest, proof_payload_sha256, proof_key_id, proof_signature,"
            " proof_version, soft_threshold_exceeded) VALUES (?, ?, 1, 1, 1, ?, 1, 2, ?, 1,"
            " 'excerpt', 4, 321, 0, 'committed', NULL, NULL, ?, NULL, ?, ?, ?, 2, ?)",
            (
                ref,
                COMMON["committed_at"],
                COMMON["tool_name"],
                COMMON["project_scope_digest"],
                "3" * 64,
                signed["proof_payload_sha256"],
                kid,
                signed["proof_signature"],
                int(soft),
            ),
        )
    return ref


@pytest.mark.parametrize("soft", [True, False])
def test_a_persisted_v2_receipt_verifies(world, soft):
    conn, seed, kid = world
    assert verify_persisted_receipt(conn, _insert_v2(conn, seed, kid, soft=soft))


def test_a_flipped_soft_flag_breaks_verification(world):
    conn, seed, kid = world
    ref = _insert_v2(conn, seed, kid, soft=False)
    conn.execute(
        "UPDATE disclosure_receipts SET soft_threshold_exceeded = 1 WHERE disclosure_ref = ?",
        (ref,),
    )
    assert not verify_persisted_receipt(conn, ref)


def test_version_and_shape_must_agree_before_any_signature_work(world):
    conn, seed, kid = world
    ref = _insert_v2(conn, seed, kid, soft=False)
    conn.execute("PRAGMA ignore_check_constraints = ON")
    conn.execute(
        "UPDATE disclosure_receipts SET consent_key_id = 'x', consent_challenge_digest = 'y'"
        " WHERE disclosure_ref = ?",
        (ref,),
    )
    conn.execute("PRAGMA ignore_check_constraints = OFF")
    assert not verify_persisted_receipt(conn, ref)
    with pytest.raises(sqlite3.IntegrityError):  # and the table itself refuses such a row
        conn.execute(
            "UPDATE disclosure_receipts SET proof_version = 2 WHERE disclosure_ref = ?", (ref,)
        )
