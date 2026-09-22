"""Build and package the Swift consent agent before its shell tests run.

Task 1 built a loose binary; Task 4 moved every test onto the signed bundle
(``scripts/package_agent.sh``), which is what the LaunchAgent loads and what
pairing judges. ``build/`` is gitignored, so a fresh checkout has no bundle
and the shell tests would fail on a missing path instead of on agent
behavior. This fixture runs the packaging script when the bundle binary is
absent or older than the source, and skips the module where ``swiftc`` is
unavailable (non-macOS CI).

The default signature is ad-hoc, which is exactly what the pairing tests
need: an ad-hoc bundle must be refused. Set ``TELEGRAM_MCP_SIGN_IDENTITY``
to sign with a real identity.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path("agent/consent-agent.swift")
BUNDLE = Path("build/consent/TelegramMCPConsent.app")
BINARY = BUNDLE / "Contents" / "MacOS" / "telegram-mcp-consent"
PACKAGER = Path("scripts/package_agent.sh")


@pytest.fixture(scope="session", autouse=True)
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
