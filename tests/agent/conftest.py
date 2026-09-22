"""Build the Swift consent agent before its shell tests run.

Plan 2b Task 1 Step 5 pins the build as a manual command
(``swiftc -O agent/consent-agent.swift -o build/consent/telegram-mcp-consent``).
``build/`` is gitignored, so a fresh checkout has no binary and the shell
tests would fail on a missing path instead of on agent behavior. This
fixture runs that exact command when the binary is absent or older than the
source, and skips the module where ``swiftc`` is unavailable (non-macOS CI).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path("agent/consent-agent.swift")
BINARY = Path("build/consent/telegram-mcp-consent")


@pytest.fixture(scope="session", autouse=True)
def consent_agent_binary() -> Path:
    if not SOURCE.exists():
        pytest.skip(f"{SOURCE} is absent")
    if BINARY.exists() and BINARY.stat().st_mtime >= SOURCE.stat().st_mtime:
        return BINARY
    if shutil.which("swiftc") is None:
        pytest.skip("swiftc is unavailable; consent-agent shell tests need a macOS toolchain")
    BINARY.parent.mkdir(parents=True, exist_ok=True)
    build = subprocess.run(  # noqa: PLW1510 -- returncode is asserted below with stderr
        ["swiftc", "-O", str(SOURCE), "-o", str(BINARY)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert build.returncode == 0, build.stderr
    return BINARY
