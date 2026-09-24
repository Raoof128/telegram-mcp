"""Shared pytest configuration: host isolation and gating.

Tests marked ``platform_gated`` mutate or interrogate the host (service
accounts, launchd, Touch ID, real installs). They are skipped unless the
operator asks for them with ``--run-platform-gated``, so the default suite
stays headless and side-effect free.
"""

import pytest

from comms.transports.telegram.keys import store as key_store


@pytest.fixture(autouse=True)
def _isolate_key_store():
    """Reset the process-wide key-store binding around every test.

    ``set_store_dir`` and the fingerprint cache are module globals, so a test
    that binds a store leaves that binding visible to the next one. Two
    unreproducible cross-file failures in this suite pointed here, and an
    isolated binding is what the daemon has in production anyway: one store
    per start.
    """
    saved_dir = key_store._STORE_DIR
    saved_cache = dict(key_store._FINGERPRINT_CACHE)
    try:
        yield
    finally:
        key_store._STORE_DIR = saved_dir
        key_store._FINGERPRINT_CACHE.clear()
        key_store._FINGERPRINT_CACHE.update(saved_cache)


def pytest_addoption(parser):
    parser.addoption(
        "--run-platform-gated",
        action="store_true",
        default=False,
        help="run tests that mutate or interrogate this host (macOS, admin rights)",
    )
    parser.addoption(
        "--run-telegram-testdc",
        action="store_true",
        default=False,
        help="run tests against Telegram's test DC (needs TG_TESTDC_* and the Keychain item)",
    )


def pytest_collection_modifyitems(config, items):
    gates = (
        ("--run-platform-gated", "platform_gated", "needs --run-platform-gated (host-mutating)"),
        ("--run-telegram-testdc", "telegram_testdc", "needs --run-telegram-testdc (network)"),
    )
    for option, marker, reason in gates:
        if config.getoption(option):
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def ephemeral_disclosure_key() -> tuple[str, str]:
    """A real Ed25519 key that exists only for this test and signs nothing."""
    import base64
    import hashlib
    import secrets

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    seed = secrets.token_bytes(32)
    raw = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    key_id = "ed25519:sha256:" + hashlib.sha256(raw).hexdigest()
    public = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return key_id, public
