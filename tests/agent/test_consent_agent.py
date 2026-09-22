# ruff: noqa: SIM115, PLW1510 -- test bodies are brief-verbatim (plan pins
# `open(...)`/`subprocess.run(...)` shapes; full output must be captured).
"""Phase-2b consent-agent shell tests (Task 1: JCS + challenge verification).

Later tasks append: display-tamper (Task 2), stub-broker scenarios (Task 3),
pairing/signing identity (Task 4). Every binary path goes through AGENT_BIN
(loose swiftc build until Task 4 re-points it at the bundle binary).
"""

import json
import subprocess

VECTORS = "tests/fixtures/consent/jcs_vectors.json"
AGENT_BIN = "build/consent/telegram-mcp-consent"  # Task 4 re-points this at the bundle binary and re-runs green


def test_jcs_vectors_match_frozen_bytes():
    cases = json.load(open(VECTORS))["cases"]
    assert len(cases) >= 3
    out = subprocess.run(
        [AGENT_BIN, "selftest-jcs", VECTORS],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert "JCS-OK" in out.stdout


def test_challenge_signatures_verify_and_tamper_rejected():
    cases = json.load(open(VECTORS))["cases"]
    assert len(cases) >= 3
    out = subprocess.run(
        [AGENT_BIN, "selftest-verify", VECTORS],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert "VERIFY-OK" in out.stdout
