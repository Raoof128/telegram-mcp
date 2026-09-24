"""A verifier needs no database handle (design §4.1; Appendix K.2)."""

import base64
import sqlite3

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.transports.telegram.disclosure.receipts import (
    build_proof_payload,
    mint_disclosure_ref,
    sign_payload,
    verify_proof,
)

_SEED = bytes(range(32))


def test_verification_succeeds_with_sqlite_and_sockets_forbidden(monkeypatch):
    payload = build_proof_payload(
        disclosure_ref=mint_disclosure_ref(),
        principal_ref="prn_" + "a" * 26,
        client_ref="tcl_" + "b" * 26,
        account_ref="tga_" + "c" * 26,
        tool_name="telegram_get_messages",
        security_epoch=1,
        policy_epoch=1,
        project_scope_digest="hmac-sha256:" + "0" * 64,
        project_count=1,
        effective_egress_level="metadata_only",
        records_disclosed=0,
        bytes_disclosed=2,
        partial=False,
        committed_at="2026-09-22T00:00:00Z",
        consent_key_id="p256:sha256:" + "1" * 64,
        consent_challenge_digest="2" * 64,
        canonical_result_provenance_digest="3" * 64,
        canonical_coverage_digest=None,
    )
    signed = sign_payload(payload, private_seed=_SEED, proof_key_id="ed25519:sha256:" + "f" * 64)
    raw = Ed25519PrivateKey.from_private_bytes(_SEED).public_key().public_bytes_raw()
    public = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    def _forbidden(*args, **kwargs):
        raise AssertionError("the verifier must not touch a database or a socket")

    monkeypatch.setattr(sqlite3, "connect", _forbidden)
    import socket

    monkeypatch.setattr(socket, "socket", _forbidden)

    assert verify_proof(
        payload,
        proof_signature=signed["proof_signature"],
        proof_payload_sha256=signed["proof_payload_sha256"],
        public_key_b64url=public,
    )
