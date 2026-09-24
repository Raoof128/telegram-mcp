"""Status factory: gateway health plus the current disclosure public key.

Appendix K.2 step 4 resolves a receipt's ``proof_key_id`` against the key
this tool advertises, so what goes here is load-bearing. The caller passes
the provisioned key; this module never derives one, and in particular never
derives the all-zero development key that earlier builds advertised — whose
private half is public knowledge and would let anyone forge a receipt that
verifies.
"""

from __future__ import annotations

__all__ = ["ephemeral_disclosure_key", "make_status"]


def ephemeral_disclosure_key() -> tuple[str, str]:
    """Mint a throwaway key pair for a build that signs nothing.

    The synthetic demo must still answer ``telegram_status``, and the frozen
    contract types both key fields as non-null strings. A fresh per-process
    Ed25519 key is the honest filler: it is real, its private half exists
    only in this process and never leaves it, and it is not a value anyone
    else holds.
    """
    import base64
    import hashlib
    import secrets

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    seed = secrets.token_bytes(32)
    raw = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    key_id = "ed25519:sha256:" + hashlib.sha256(raw).hexdigest()
    public = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return key_id, public


def make_status(*, disclosure_key: tuple[str, str]) -> dict:
    """Build the ``telegram_status`` result.

    ``disclosure_key`` is ``(key_id, public_key_b64url)`` and is **required**:
    the frozen contract types both fields as non-null strings, so there is no
    "no key" state to report.
    """
    key_id, public_key = disclosure_key
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
            "disclosure_proof_key_id": key_id,
            "disclosure_proof_public_key": public_key,
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
