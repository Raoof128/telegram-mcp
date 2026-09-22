"""Shared pytest configuration: host isolation, gating, and the agent bundle.

Tests marked ``platform_gated`` mutate or interrogate the host (service
accounts, launchd, Touch ID, real installs). They are skipped unless the
operator asks for them with ``--run-platform-gated``, so the default suite
stays headless and side-effect free.

The consent-agent bundle fixtures live here rather than in
``tests/agent/conftest.py`` because the Phase-2J join gate drives the same
packaged binary, and two builds of it would be two different things under
test. Nothing builds it unless a test asks for it by name.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

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


SOURCE = Path("agent/consent-agent.swift")
BUNDLE = Path("build/consent/TelegramMCPConsent.app")
BINARY = BUNDLE / "Contents" / "MacOS" / "telegram-mcp-consent"
PACKAGER = Path("scripts/package_agent.sh")


@pytest.fixture(scope="session")
def consent_agent_binary() -> Path:
    if not SOURCE.exists():
        pytest.skip(f"{SOURCE} is absent")
    if BINARY.exists() and BINARY.stat().st_mtime >= SOURCE.stat().st_mtime:
        return BINARY
    if shutil.which("swiftc") is None:
        pytest.skip("swiftc is unavailable; consent-agent shell tests need a macOS toolchain")
    build = subprocess.run(  # noqa: PLW1510 -- returncode is asserted below with stderr
        ["/bin/bash", str(PACKAGER)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return BINARY


SIGNED_BUNDLE_DIR = Path("build/consent-signed")
SIGNED_BINARY = (
    SIGNED_BUNDLE_DIR / "TelegramMCPConsent.app" / "Contents" / "MacOS" / "telegram-mcp-consent"
)


def discover_signing_identity() -> str | None:
    """First valid codesigning identity on this host, or None.

    Pairing needs a stable, certificate-backed identity; Developer ID is a
    distribution concern and is not required for an agent that never leaves
    this Mac. A free Apple Development certificate satisfies the rule.
    """
    if shutil.which("security") is None:
        return None
    found = subprocess.run(  # noqa: PLW1510 -- absence is a skip, not a failure
        ["security", "find-identity", "-v", "-p", "codesigning"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    for line in found.stdout.splitlines():
        match = re.search(r'"([^"]+)"', line)
        if match:
            return match.group(1)
    return None


@pytest.fixture(scope="session")
def signed_agent_binary(consent_agent_binary: Path) -> Path:
    """A second bundle signed with this host's certificate identity."""
    identity = discover_signing_identity()
    if identity is None:
        pytest.skip("no codesigning identity on this host")
    build = subprocess.run(  # noqa: PLW1510 -- returncode is asserted below
        ["/bin/bash", str(PACKAGER)],
        capture_output=True,
        text=True,
        timeout=600,
        env={
            **os.environ,
            "TELEGRAM_MCP_SIGN_IDENTITY": identity,
            "TELEGRAM_MCP_BUNDLE_DIR": str(SIGNED_BUNDLE_DIR),
        },
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return SIGNED_BINARY


@pytest.fixture(scope="session")
def paired_agent_binary(signed_agent_binary: Path) -> Path:
    """Ensure the signed bundle holds an Enclave approval key, once per run.

    Pairing is host-mutating, so this fixture only ever runs behind
    ``--run-platform-gated``. It generates nothing if the records already
    exist, and it never imports a daemon pin: that public key belongs to a
    real runtime, and pinning a test fixture's key into the operator's
    keychain would be a lie about what is paired.
    """
    import json

    status = subprocess.run(  # noqa: PLW1510 -- absence is handled below
        [str(signed_agent_binary), "pairing-status"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    records = json.loads(status.stdout).get("records", {})
    if "approval" in records and "transport" in records:
        return signed_agent_binary
    generated = subprocess.run(  # noqa: PLW1510 -- returncode is asserted below
        [str(signed_agent_binary), "pairing", "generate"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    return signed_agent_binary


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
