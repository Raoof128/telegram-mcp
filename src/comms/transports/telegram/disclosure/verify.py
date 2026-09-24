"""Reconstruct and verify a persisted receipt (Appendix K.2)."""

from __future__ import annotations

import sqlite3

from comms.transports.telegram.disclosure.keys import lookup_verification_key
from comms.transports.telegram.disclosure.receipts import (
    ReceiptError,
    build_proof_payload,
    build_proof_payload_v2,
    verify_proof,
)

_JOIN = """
SELECT r.disclosure_ref, p.principal_ref, c.client_ref, a.account_ref, r.tool_name,
       r.security_epoch, r.policy_epoch, r.project_scope_digest, r.project_count,
       r.effective_egress_level, r.records_disclosed, r.bytes_disclosed, r.partial,
       r.committed_at, r.consent_key_id, r.consent_challenge_digest,
       r.canonical_result_provenance_digest, r.canonical_coverage_digest,
       r.proof_payload_sha256, r.proof_key_id, r.proof_signature,
       r.proof_version, r.soft_threshold_exceeded
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
        "proof_version",
        "soft_threshold_exceeded",
    )
    record = dict(zip(names, row, strict=True))
    signature = record.pop("proof_signature")
    digest = record.pop("proof_payload_sha256")
    version = record.pop("proof_version")
    soft = record.pop("soft_threshold_exceeded")
    consent = (record["consent_key_id"], record["consent_challenge_digest"])
    # Dispatch on the explicit, persisted version; never infer it from nulls.
    # The shape must agree with the version before any signature work.
    if version == 1:
        if None in consent or soft is not None:
            return False
    elif version == 2:
        if consent != (None, None) or soft not in (0, 1):
            return False
        del record["consent_key_id"], record["consent_challenge_digest"]
    else:
        return False
    key = lookup_verification_key(conn, record.pop("proof_key_id"))
    if key is None:
        return False
    record["partial"] = bool(record["partial"])
    try:
        payload = (
            build_proof_payload(**record)
            if version == 1
            else build_proof_payload_v2(**record, soft_threshold_exceeded=bool(soft))
        )
    except ReceiptError:
        return False
    return verify_proof(
        payload,
        proof_signature=signature,
        proof_payload_sha256=digest,
        public_key_b64url=key["public_key_b64url"],
    )
