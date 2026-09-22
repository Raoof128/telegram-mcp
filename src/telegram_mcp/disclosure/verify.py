"""Reconstruct and verify a persisted receipt (Appendix K.2)."""

from __future__ import annotations

import sqlite3

from telegram_mcp.disclosure.keys import lookup_verification_key
from telegram_mcp.disclosure.receipts import build_proof_payload, verify_proof

_JOIN = """
SELECT r.disclosure_ref, p.principal_ref, c.client_ref, a.account_ref, r.tool_name,
       r.security_epoch, r.policy_epoch, r.project_scope_digest, r.project_count,
       r.effective_egress_level, r.records_disclosed, r.bytes_disclosed, r.partial,
       r.committed_at, r.consent_key_id, r.consent_challenge_digest,
       r.canonical_result_provenance_digest, r.canonical_coverage_digest,
       r.proof_payload_sha256, r.proof_key_id, r.proof_signature
  FROM disclosure_receipts r
  JOIN principals  p ON p.id = r.principal_id
  JOIN mcp_clients c ON c.id = r.client_id
  JOIN accounts    a ON a.id = r.account_id
 WHERE r.disclosure_ref = ?
"""


def verify_persisted_receipt(conn: sqlite3.Connection, disclosure_ref: str) -> bool:
    """Rebuild the canonical payload from the row and check the signature.

    There is no ``proof_payload`` column by design, so the payload is rebuilt
    — which is why ``principal_ref``, ``client_ref`` and ``account_ref`` must
    stay immutable for at least the receipt retention window. A rotation that
    re-mints one of them changes the rebuilt bytes and the signature stops
    verifying, indistinguishably from tampering.
    """
    row = conn.execute(_JOIN, (disclosure_ref,)).fetchone()
    if row is None:
        return False
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
        "proof_payload_sha256",
        "proof_key_id",
        "proof_signature",
    )
    record = dict(zip(names, row, strict=True))
    signature = record.pop("proof_signature")
    digest = record.pop("proof_payload_sha256")
    key = lookup_verification_key(conn, record.pop("proof_key_id"))
    if key is None:
        return False
    record["partial"] = bool(record["partial"])
    payload = build_proof_payload(**record)
    return verify_proof(
        payload,
        proof_signature=signature,
        proof_payload_sha256=digest,
        public_key_b64url=key["public_key_b64url"],
    )
