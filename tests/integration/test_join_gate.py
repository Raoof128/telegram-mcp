"""Phase-2J join gate: the real broker against the real consent agent.

Both halves green in isolation proves nothing about byte agreement, so
Phase 2 is not complete until this gate passes. Every scenario here drives
the shipped artifacts: ``telegram_mcp.ipc.rendezvous.serve_rendezvous`` and
``telegram_mcp.consent.broker.ConsentBroker`` on one side, the packaged
Swift binary on the other, over a real Unix socket.

Twelve of the thirteen run headless. The thirteenth performs a real Touch ID
approval end to end and is platform-gated: it needs a certificate-signed
bundle, a paired Secure Enclave key and an operator present to approve. It
also imports a daemon pin, which it removes afterwards, so the host ends in
the state it started.
"""

import base64
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.agent.stub_broker import AGENT_BIN, SCENARIOS, run_scenario

# Plan 2a Task 9 Step 4's gate set.
JOIN_SCENARIOS = (
    "good-approval",
    "display-tamper",
    "challenge-tamper",
    "wrong-daemon-key",
    "wrong-approval-key",
    "wrong-key-id",
    "wrong-challenge-sha256",
    "duplicate-approval",
    "runtime-id-mismatch",
    "broker-death-mid-prompt",
    "agent-death-mid-prompt",
    "daemon-key-rotation",
    "agent-key-rotation",
)

BUNDLE_ENV = "TELEGRAM_MCP_AGENT_BUNDLE"


@pytest.fixture(scope="session", autouse=True)
def _agent_built(consent_agent_binary):
    """Reuse the agent-suite fixture so the bundle exists for this file too."""
    return consent_agent_binary


def test_the_gate_set_is_the_one_the_plan_names():
    """A guard against the gate quietly shrinking to what happens to pass."""
    assert len(JOIN_SCENARIOS) == 13
    assert len(set(JOIN_SCENARIOS)) == 13
    assert set(JOIN_SCENARIOS) <= set(SCENARIOS)


def test_good_approval_is_accepted_by_the_broker():
    result = run_scenario("good-approval", timeout=90)
    assert result["agent_rendered"] is True
    assert result["broker_accepted"] is True
    assert result["signature_valid"] is True


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("display-tamper", "DISPLAY-MISMATCH"),
        ("challenge-tamper", "CHALLENGE-SIGNATURE-INVALID"),
        ("wrong-daemon-key", "CHALLENGE-SIGNATURE-INVALID"),
        ("daemon-key-rotation", "CHALLENGE-SIGNATURE-INVALID"),
    ],
)
def test_the_agent_refuses_what_it_cannot_verify(scenario, expected):
    """Agent-side refusals: nothing is signed and the daemon is told why."""
    result = run_scenario(scenario, timeout=90)
    assert result["approved"] is False
    assert result["broker_accepted"] is False
    assert result["denial_reason"] == expected


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("wrong-approval-key", "bad-agent-signature"),
        ("wrong-key-id", "unknown-key"),
        ("wrong-challenge-sha256", "challenge-mismatch"),
        ("agent-key-rotation", "unknown-key"),
    ],
)
def test_the_broker_refuses_a_bad_envelope(scenario, expected):
    """Broker-side refusals, in the order §9.8 fixes them."""
    result = run_scenario(scenario, timeout=90)
    assert result["agent_rendered"] is True, "the agent should have answered"
    assert result["broker_accepted"] is False
    assert result["denial_reason"] == expected


def test_an_approval_cannot_be_consumed_twice():
    result = run_scenario("duplicate-approval", timeout=90)
    assert result["broker_accepted"] is True
    assert result["second_consume"] == "rejected"


def test_a_stale_runtime_id_is_refused_at_the_handshake():
    result = run_scenario("runtime-id-mismatch", timeout=90)
    assert result["handshake_completed"] is False
    assert result["agent_rendered"] is False
    assert result["agent_exited"] is True


def test_broker_death_mid_prompt_leaves_no_orphan_agent():
    result = run_scenario("broker-death-mid-prompt", timeout=90)
    assert result["agent_exited"] is True
    assert result["exit_seconds"] < 5.0


def test_agent_death_mid_prompt_releases_the_pending_challenge():
    result = run_scenario("agent-death-mid-prompt", timeout=90)
    assert result["pending_before_death"] == 1
    assert result["swept"] == 1
    assert result["pending_after_sweep"] == 0
    assert result["broker_accepted"] is False


def test_a_foreign_transport_key_never_reaches_a_prompt():
    result = run_scenario("wrong-transport-key", timeout=90)
    assert result["handshake_completed"] is False
    assert result["agent_rendered"] is False


@pytest.mark.platform_gated
def test_join_gate_good_approval_with_real_touch_id(paired_agent_binary, tmp_path):
    """The one interactive scenario: a real Touch ID approval, end to end.

    Imports a daemon pin so the production ``run`` path can authenticate,
    then removes it, leaving the operator's keychain as it was.
    """
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from tests.agent.stub_broker import CHALLENGE_KEY

    daemon_public = (
        ed25519.Ed25519PrivateKey.from_private_bytes(CHALLENGE_KEY).public_key().public_bytes_raw()
    )
    encoded = base64.urlsafe_b64encode(daemon_public).decode().rstrip("=")
    imported = subprocess.run(  # noqa: PLW1510 -- returncode is asserted below
        [str(paired_agent_binary), "pairing", "import-daemon-pin", encoded],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert imported.returncode == 0, imported.stderr
    try:
        result = run_scenario(
            "good-approval", timeout=240, binary=str(paired_agent_binary), interactive=True
        )
        assert result["broker_accepted"] is True
        assert result["signature_valid"] is True
    finally:
        subprocess.run(
            [str(paired_agent_binary), "pairing", "forget-daemon-pin"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        status = json.loads(
            subprocess.run(  # noqa: PLW1510 -- the assertion below is the check
                [str(paired_agent_binary), "pairing-status"],
                capture_output=True,
                text=True,
                timeout=60,
            ).stdout
        )
        assert "daemon_pin" not in status["records"], "the temporary pin outlived the test"


def test_the_agent_binary_under_test_is_the_packaged_one():
    override = os.environ.get(BUNDLE_ENV)
    binary = Path(override) if override else Path(AGENT_BIN)
    assert binary.exists(), binary
    assert "TelegramMCPConsent.app" in str(binary)
