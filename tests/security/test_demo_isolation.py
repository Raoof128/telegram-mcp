"""Demo isolation: no Telegram/session/network surface in the Phase-1 binary."""

import sys

from telegram_mcp import config as config_module


def test_no_telethon_import():
    assert "telethon" not in sys.modules
    assert not hasattr(config_module, "Telethon")
    import telegram_mcp.config

    assert "telethon" not in telegram_mcp.config.__dict__.get("__doc__", "")


def test_no_session_or_database_access(monkeypatch):
    import sqlite3

    def _forbidden(*args, **kwargs):
        raise AssertionError("session/database access forbidden in safe_demo")

    monkeypatch.setattr(sqlite3, "connect", _forbidden)
    # Config construction itself must not touch the network or filesystem.
    from telegram_mcp.config import DemoConfig

    DemoConfig()


def test_only_limited_profiles_exist():
    from telegram_mcp.config import DemoConfig

    fields = set(DemoConfig.model_fields)
    assert fields == {"mode", "host", "port", "max_request_bytes", "max_response_bytes"}
    assert DemoConfig.model_fields["mode"].annotation is not None


def test_synthetic_disclosure_key_is_marked_and_tripwired():
    """The Phase-1 status key is a publicly known zero seed. Pin it.

    Appendix K.2 step 4 tells a verifier to resolve ``proof_key_id`` against
    the key ``telegram_status`` advertises. Phase 1 advertises a key derived
    from 32 zero bytes, whose private half everyone has. That is honest only
    while nothing signs anything. When Phase 3 lands a real disclosure
    signer, this test MUST fail — that is its purpose. Replacing it means
    retiring the factory, not relaxing the assertion.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from telegram_mcp.tools.status import make_status

    data = make_status()["data"]
    zero_seed_public = (
        Ed25519PrivateKey.from_private_bytes(bytes(32)).public_key().public_bytes_raw()
    )
    import base64

    expected = base64.urlsafe_b64encode(zero_seed_public).rstrip(b"=").decode("ascii")

    assert data["disclosure_proof_public_key"] == expected
    # The id must stay visibly synthetic: it is deliberately NOT the frozen
    # <kind>:sha256:<hex> shape, so no verifier can mistake it for a real key.
    assert data["disclosure_proof_key_id"] == "synthetic-test-key-v1"
    assert ":sha256:" not in data["disclosure_proof_key_id"]
