# ruff: noqa: SIM115, PLW1510 -- test bodies are brief-verbatim (plan pins
# `open(...)`/`subprocess.run(...)` shapes; full output must be captured).
"""Phase-2b consent-agent shell tests (Task 1: JCS + challenge verification).

Later tasks append: display-tamper (Task 2), stub-broker scenarios (Task 3),
pairing/signing identity (Task 4). Every binary path goes through AGENT_BIN
(loose swiftc build until Task 4 re-points it at the bundle binary).
"""

import hashlib
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


def test_jcs_encode_rejects_directly_constructed_non_ascii_key():
    out = subprocess.run(
        [AGENT_BIN, "selftest-jcs-rejects-nonascii-key"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert "REJECT-OK" in out.stdout


# --- Task 2: display gate and approval ---------------------------------------

import base64
import os
import re

import pytest

AGENT_SOURCE = "agent/consent-agent.swift"
NO_UI = {**os.environ, "CONSENT_NO_UI": "1"}


def _case(index=0):
    return json.load(open(VECTORS))["cases"][index]


def test_tampered_display_refuses_to_render():
    out = subprocess.run(
        [AGENT_BIN, "selftest-display-tamper", VECTORS],
        capture_output=True,
        text=True,
        timeout=60,
        env=NO_UI,
    )
    assert out.returncode != 0
    assert "DISPLAY-MISMATCH" in (out.stdout + out.stderr)


def test_display_gate_never_reaches_the_prompt_on_mismatch():
    """The tamper path must fail before LocalAuthentication is touched."""
    out = subprocess.run(
        [AGENT_BIN, "selftest-display-tamper", VECTORS],
        capture_output=True,
        text=True,
        timeout=60,
        env=NO_UI,
    )
    assert "PROMPT-ATTEMPTED" not in (out.stdout + out.stderr)


def test_selftest_approve_emits_a_verifiable_approval_envelope():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    case = _case()
    out = subprocess.run(
        [AGENT_BIN, "selftest-approve", VECTORS, "0"],
        capture_output=True,
        text=True,
        timeout=60,
        env=NO_UI,
    )
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    envelope = payload["envelope"]
    assert set(envelope) == {"challenge_sha256", "sig", "key_id"}

    jcs = bytes.fromhex(case["jcs_hex"])
    assert envelope["challenge_sha256"] == hashlib.sha256(jcs).hexdigest()
    assert re.fullmatch(r"p256:sha256:[0-9a-f]{64}", envelope["key_id"])

    der = base64.urlsafe_b64decode(payload["public_key_der_b64url"] + "==")
    public = serialization.load_der_public_key(der)
    assert isinstance(public, ec.EllipticCurvePublicKey)
    assert envelope["key_id"] == "p256:sha256:" + hashlib.sha256(der).hexdigest()
    signature = base64.urlsafe_b64decode(
        envelope["sig"] + "=" * (-len(envelope["sig"]) % 4)
    )
    public.verify(signature, jcs, ec.ECDSA(hashes.SHA256()))

    # a one-bit change to the signed bytes must not verify
    with pytest.raises(Exception):
        public.verify(signature, jcs[:-1] + bytes([jcs[-1] ^ 0x01]), ec.ECDSA(hashes.SHA256()))


def test_approve_refuses_a_challenge_signed_by_the_wrong_daemon_key():
    out = subprocess.run(
        [AGENT_BIN, "selftest-approve-wrong-daemon-key", VECTORS],
        capture_output=True,
        text=True,
        timeout=60,
        env=NO_UI,
    )
    assert out.returncode != 0
    assert "CHALLENGE-SIGNATURE-INVALID" in (out.stdout + out.stderr)


def test_the_real_approval_path_has_no_test_key_branch():
    """The ephemeral key may appear only inside selftest functions."""
    source = open(AGENT_SOURCE).read()
    chunks = re.split(r"\nfunc |\nstruct |\nenum ", source)
    for chunk in chunks:
        name = chunk.split("(")[0].split("{")[0].split(":")[0].strip()
        if "EphemeralApprovalKey" not in chunk:
            continue
        assert name.startswith("selfTest") or name.startswith("EphemeralApprovalKey"), (
            f"ephemeral approval key referenced outside a selftest: {name!r}"
        )


def test_the_no_ui_switch_can_only_refuse_never_substitute_a_key():
    """CONSENT_NO_UI is read once, by a named guard whose effect is refusal."""
    source = open(AGENT_SOURCE).read()
    readers = [line for line in source.splitlines() if "CONSENT_NO_UI" in line]
    assert len(readers) == 1, readers
    guard = source.split("func uiSuppressed")[1].split("\n}")[0]
    assert "CONSENT_NO_UI" in guard, "the switch is read outside its named guard"
    assert "ApprovalKey" not in guard and "Ephemeral" not in guard


def test_no_ui_environment_makes_a_real_prompt_fatal():
    """CONSENT_NO_UI=1 must abort the real approve path, never prompt."""
    out = subprocess.run(
        [AGENT_BIN, "approve", VECTORS, "0"],
        capture_output=True,
        text=True,
        timeout=60,
        env=NO_UI,
    )
    assert out.returncode != 0
    assert "NO-UI" in (out.stdout + out.stderr)


@pytest.mark.platform_gated
def test_live_touch_id_approval_signs_with_the_enclave_key():
    """Interactive: a Touch ID prompt appears and the operator approves once."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    case = _case()
    out = subprocess.run(
        [AGENT_BIN, "approve", VECTORS, "0"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    der = base64.urlsafe_b64decode(payload["public_key_der_b64url"] + "==")
    public = serialization.load_der_public_key(der)
    signature = base64.urlsafe_b64decode(
        payload["envelope"]["sig"] + "=" * (-len(payload["envelope"]["sig"]) % 4)
    )
    public.verify(signature, bytes.fromhex(case["jcs_hex"]), ec.ECDSA(hashes.SHA256()))
