"""Synthetic disconnected status factory (development build only).

Uses a public deterministic test key. Production key providers must never
import or reuse this factory.

**Phase-3 obligation.** Appendix K.2 step 4 tells a verifier to resolve a
receipt's ``proof_key_id`` against the key this tool advertises. The key
below is derived from 32 zero bytes: its private half is public knowledge.
That is honest while nothing signs anything, and a forgery oracle the
moment a real disclosure signer exists. Phase 3a MUST replace this factory
with the recomputed ``disclosure-key`` id and public half before any
receipt is signed. ``tests/security/test_demo_isolation.py`` carries the
tripwire that fails when it is time.
"""

import base64
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_KEY_ID = "synthetic-test-key-v1"


def _test_key() -> tuple[str, str]:
    raw = Ed25519PrivateKey.from_private_bytes(bytes(32)).public_key().public_bytes_raw()
    public = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    fingerprint = "ed25519:sha256:" + hashlib.sha256(raw).hexdigest()
    return public, fingerprint


def make_status() -> dict:
    public, _fingerprint = _test_key()
    return {
        "data": {
            "connected": False,
            "authorised": False,
            "account_ref": None,
            "account_label": None,
            "read_scope_mode": None,
            "policy_epoch": None,
            "security_epoch": 1,
            "security_locked": False,
            "disclosure_proof_key_id": _KEY_ID,
            "disclosure_proof_public_key": public,
            "capabilities": {
                "read_chats": False,
                "search_messages": False,
                "project_namespaces": True,
                "cross_project_search": True,
                "project_selection_required": True,
                "proof_carrying_retrieval": True,
                "egress_profiles": True,
                "exposure_budgets": True,
                "tamper_evident_audit": True,
                "emergency_lock": True,
                "write_messages": False,
                "attachments": False,
                "secret_chats": False,
            },
        },
        "meta": {
            "source": "gateway",
            "content_trust": "non_instructional_gateway_metadata",
            "truncated": False,
            "partial": False,
            "next_cursor": None,
            "disclosure": None,
            "coverage": None,
        },
    }
