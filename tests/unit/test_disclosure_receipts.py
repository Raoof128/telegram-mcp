"""Appendix-K receipts: build, sign, verify (design §4)."""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.transports.telegram.disclosure.receipts import (
    APPENDIX_K_FIELDS,
    PROOF_SCHEMA,
    ReceiptError,
    build_proof_payload,
    mint_disclosure_ref,
    sign_payload,
    verify_proof,
)

_SEED = bytes(range(32))


def _public_b64url(seed: bytes) -> str:
    raw = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _payload(**overrides):
    fields = {
        "disclosure_ref": mint_disclosure_ref(),
        "principal_ref": "prn_" + "a" * 26,
        "client_ref": "tcl_" + "b" * 26,
        "account_ref": "tga_" + "c" * 26,
        "tool_name": "telegram_get_messages",
        "security_epoch": 12,
        "policy_epoch": 31,
        "project_scope_digest": "hmac-sha256:" + "0" * 64,
        "project_count": 1,
        "effective_egress_level": "excerpt",
        "records_disclosed": 20,
        "bytes_disclosed": 16384,
        "partial": False,
        "committed_at": "2026-09-22T00:00:00Z",
        "consent_key_id": "p256:sha256:" + "1" * 64,
        "consent_challenge_digest": "2" * 64,
        "canonical_result_provenance_digest": "3" * 64,
        "canonical_coverage_digest": None,
    }
    fields.update(overrides)
    return build_proof_payload(**fields)


def test_payload_carries_exactly_the_twenty_one_appendix_k_fields():
    payload = _payload()
    assert len(APPENDIX_K_FIELDS) == 21
    assert set(payload) == set(APPENDIX_K_FIELDS)
    assert payload["schema"] == PROOF_SCHEMA
    assert payload["consent_verified"] is True
    assert payload["commit_status"] == "committed"


def test_disclosure_ref_uses_the_frozen_tdr_shape():
    ref = mint_disclosure_ref()
    assert ref.startswith("tdr_") and len(ref) == 30


def test_signature_verifies_and_digest_matches():
    payload = _payload()
    signed = sign_payload(payload, private_seed=_SEED, proof_key_id="ed25519:sha256:" + "f" * 64)
    assert verify_proof(
        payload,
        proof_signature=signed["proof_signature"],
        proof_payload_sha256=signed["proof_payload_sha256"],
        public_key_b64url=_public_b64url(_SEED),
    )


def test_one_changed_field_breaks_verification():
    payload = _payload()
    signed = sign_payload(payload, private_seed=_SEED, proof_key_id="ed25519:sha256:" + "f" * 64)
    tampered = dict(payload, records_disclosed=21)
    assert not verify_proof(
        tampered,
        proof_signature=signed["proof_signature"],
        proof_payload_sha256=signed["proof_payload_sha256"],
        public_key_b64url=_public_b64url(_SEED),
    )


def test_a_wrong_key_does_not_verify():
    payload = _payload()
    signed = sign_payload(payload, private_seed=_SEED, proof_key_id="ed25519:sha256:" + "f" * 64)
    assert not verify_proof(
        payload,
        proof_signature=signed["proof_signature"],
        proof_payload_sha256=signed["proof_payload_sha256"],
        public_key_b64url=_public_b64url(bytes(32)),
    )


def test_payload_refuses_unknown_fields():
    with pytest.raises(ReceiptError):
        _payload(search_query="invoices")


def test_payload_refuses_a_missing_field():
    with pytest.raises(ReceiptError):
        build_proof_payload(disclosure_ref=mint_disclosure_ref())


def test_search_tools_must_carry_a_coverage_digest():
    with pytest.raises(ReceiptError):
        _payload(tool_name="telegram_search_messages", canonical_coverage_digest=None)


def test_non_search_tools_must_not_carry_one():
    with pytest.raises(ReceiptError):
        _payload(tool_name="telegram_get_messages", canonical_coverage_digest="4" * 64)
