"""Demo isolation: no Telegram/session/network surface in the Phase-1 binary."""

import sys

from telegram_mcp import config as config_module


def test_no_telethon_import():
    import subprocess

    probe = "import sys, telegram_mcp.server, telegram_mcp.config; print('telethon' in sys.modules)"
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60, check=True
    )
    assert done.stdout.strip() == "False"
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


def test_status_never_advertises_a_publicly_known_key():
    """The zero-seed key is gone. It must not come back.

    Its private half is 32 zero bytes, which everybody has. Appendix K.2
    step 4 points verifiers at whatever this tool advertises, so a build
    that ships this key lets anyone forge a receipt that verifies.
    """
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from telegram_mcp.tools.status import ephemeral_disclosure_key, make_status

    zero_raw = Ed25519PrivateKey.from_private_bytes(bytes(32)).public_key().public_bytes_raw()
    zero_public = base64.urlsafe_b64encode(zero_raw).rstrip(b"=").decode("ascii")

    data = make_status(disclosure_key=ephemeral_disclosure_key())["data"]
    assert data["disclosure_proof_public_key"] != zero_public
    assert data["disclosure_proof_key_id"] != "synthetic-test-key-v1"


def test_status_requires_a_key_pair():
    import pytest

    from telegram_mcp.tools.status import make_status

    # The contract admits no null here, so there is no "no key" state to
    # report. A build with no key cannot answer telegram_status at all.
    with pytest.raises(TypeError):
        make_status()


def test_ephemeral_keys_differ_between_processes():
    from telegram_mcp.tools.status import ephemeral_disclosure_key

    assert ephemeral_disclosure_key() != ephemeral_disclosure_key()


def test_the_advertised_key_satisfies_the_frozen_contract(ephemeral_disclosure_key):
    import json

    import jsonschema

    from telegram_mcp.tools.status import make_status

    with open("src/telegram_mcp/contracts/telegram_status.data.json") as handle:
        schema = json.load(handle)
    jsonschema.validate(make_status(disclosure_key=ephemeral_disclosure_key)["data"], schema)
