"""Proof-carrying retrieval receipts (frozen spec §23A, Appendix K).

The payload carries exactly the twenty-one Appendix-K fields, signed with
the dedicated Ed25519 disclosure key over RFC 8785 canonical bytes.
``proof_payload_sha256`` is the lowercase hex SHA-256 of those exact bytes
and must equal the persisted receipt column of the same name.

A verifier needs only ``proof_payload``, ``proof_signature`` and the
matching public key — no database, no Telegram. Nothing in this module
opens either.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from comms.transports.telegram.canonical import jcs_dumps
from comms.transports.telegram.opaque import mint_opaque_ref

__all__ = [
    "APPENDIX_K_FIELDS",
    "PROOF_SCHEMA",
    "ReceiptError",
    "build_proof_payload",
    "mint_disclosure_ref",
    "sign_payload",
    "verify_proof",
]

PROOF_SCHEMA = "tg-mcp-disclosure/v1"

# Appendix K.1, in the order the appendix lists them. Set equality is what
# is enforced; JCS sorts the keys for the signed bytes.
APPENDIX_K_FIELDS: tuple[str, ...] = (
    "schema",
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
    "commit_status",
    "committed_at",
    "consent_verified",
    "consent_key_id",
    "consent_challenge_digest",
    "canonical_result_provenance_digest",
    "canonical_coverage_digest",
)

_SEARCH_TOOLS = frozenset({"telegram_search_messages", "telegram_cross_project_search"})

# Fields the caller does not supply because they are constants of a
# committed receipt.
_DERIVED = {"schema": PROOF_SCHEMA, "commit_status": "committed", "consent_verified": True}


class ReceiptError(Exception):
    """The proof payload is out of contract."""


def mint_disclosure_ref() -> str:
    """Mint a ``tdr_`` disclosure-receipt reference (spec §11.1)."""
    return mint_opaque_ref("tdr_")


def build_proof_payload(**fields: Any) -> dict[str, Any]:
    """Assemble the Appendix-K payload, rejecting anything out of contract."""
    supplied = set(fields)
    expected = set(APPENDIX_K_FIELDS) - set(_DERIVED)
    unknown = supplied - expected
    if unknown:
        # Never name the offending value; the field name is enough.
        raise ReceiptError("proof payload carries unknown fields")
    missing = expected - supplied
    if missing:
        raise ReceiptError("proof payload is missing required fields")

    payload: dict[str, Any] = dict(fields)
    payload.update(_DERIVED)

    tool_name = payload["tool_name"]
    coverage = payload["canonical_coverage_digest"]
    if tool_name in _SEARCH_TOOLS and coverage is None:
        raise ReceiptError("search tools require a coverage digest")
    if tool_name not in _SEARCH_TOOLS and coverage is not None:
        raise ReceiptError("non-search tools must not carry a coverage digest")

    return payload


def _canonical(payload: Mapping[str, Any]) -> bytes:
    if set(payload) != set(APPENDIX_K_FIELDS):
        raise ReceiptError("proof payload is not the Appendix-K shape")
    return jcs_dumps(payload)


def sign_payload(
    payload: Mapping[str, Any], *, private_seed: bytes, proof_key_id: str
) -> dict[str, str]:
    """Sign the canonical payload bytes; returns the three ``meta`` fields."""
    raw = _canonical(payload)
    signature = Ed25519PrivateKey.from_private_bytes(private_seed).sign(raw)
    return {
        "proof_key_id": proof_key_id,
        "proof_signature": base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
        "proof_payload_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify_proof(
    payload: Mapping[str, Any],
    *,
    proof_signature: str,
    proof_payload_sha256: str,
    public_key_b64url: str,
) -> bool:
    """Appendix K.2 steps 2, 3 and 5. No database, no network."""
    try:
        raw = _canonical(payload)
    except ReceiptError:
        return False
    if hashlib.sha256(raw).hexdigest() != proof_payload_sha256:
        return False
    try:
        public = Ed25519PublicKey.from_public_bytes(_b64url_decode(public_key_b64url))
        public.verify(_b64url_decode(proof_signature), raw)
    except (InvalidSignature, ValueError):
        return False
    return True
