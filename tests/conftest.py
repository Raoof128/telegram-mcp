"""Shared pytest configuration: the platform-gated opt-in.

Tests marked ``platform_gated`` mutate or interrogate the host (service
accounts, launchd, Touch ID, real installs). They are skipped unless the
operator asks for them with ``--run-platform-gated``, so the default suite
stays headless and side-effect free.
"""

import pytest

from telegram_mcp.keys import store as key_store


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


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-platform-gated"):
        return
    skip = pytest.mark.skip(reason="needs --run-platform-gated (host-mutating)")
    for item in items:
        if "platform_gated" in item.keywords:
            item.add_marker(skip)
