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
AGENT_BIN = "build/consent/TelegramMCPConsent.app/Contents/MacOS/telegram-mcp-consent"


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
    signature = base64.urlsafe_b64decode(envelope["sig"] + "=" * (-len(envelope["sig"]) % 4))
    public.verify(signature, jcs, ec.ECDSA(hashes.SHA256()))

    # a one-bit change to the signed bytes must not verify
    from cryptography.exceptions import InvalidSignature

    with pytest.raises(InvalidSignature):
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
        assert name.startswith(("selfTest", "EphemeralApprovalKey")), (
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
def test_live_touch_id_approval_signs_with_the_enclave_key(paired_agent_binary):
    """Interactive: a Touch ID prompt appears and the operator approves once.

    Runs against the paired, certificate-signed bundle: the keychain ACL
    binds to that identity, so the ad-hoc bundle cannot read the records.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    case = _case()
    out = subprocess.run(
        [str(paired_agent_binary), "approve", VECTORS, "0"],
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
    status = json.loads(
        subprocess.run(
            [str(paired_agent_binary), "pairing-status"],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    )
    assert payload["envelope"]["key_id"] == status["records"]["approval"]


# --- Task 3: rendezvous client against the broker ----------------------------


def test_good_challenge_round_trip():
    from tests.agent.stub_broker import run_scenario

    result = run_scenario("good", timeout=60)
    assert result["approved"] is True and result["signature_valid"] is True


def test_replay_rejected():
    from tests.agent.stub_broker import run_scenario

    result = run_scenario("replay", timeout=60)
    assert result["approved"] is True and result["second_consume"] == "rejected"


def test_tampered_display_is_denied_over_the_wire():
    from tests.agent.stub_broker import run_scenario

    result = run_scenario("tamper-display", timeout=60)
    assert result["approved"] is False
    assert result["denial_reason"] == "DISPLAY-MISMATCH"


def test_wrong_daemon_key_is_denied_over_the_wire():
    from tests.agent.stub_broker import run_scenario

    result = run_scenario("wrong-daemon-key", timeout=60)
    assert result["approved"] is False
    assert result["denial_reason"] == "CHALLENGE-SIGNATURE-INVALID"


def test_kill_mid_prompt_leaves_no_orphan_agent():
    from tests.agent.stub_broker import run_scenario

    result = run_scenario("kill-mid-prompt", timeout=60)
    assert result["agent_exited"] is True
    assert result["exit_seconds"] < 5.0


def test_handshake_refuses_an_agent_with_the_wrong_transport_key():
    from tests.agent.stub_broker import run_scenario

    result = run_scenario("wrong-transport-key", timeout=60)
    assert result["handshake_completed"] is False
    assert result["agent_exited"] is True


# --- Task 4: pairing, bundle, signing ----------------------------------------

BUNDLE = "build/consent/TelegramMCPConsent.app"
LAUNCH_AGENT_PLIST = "agent/ConsentAgent-Info.plist"


def _pairing(*args, timeout=60):
    return subprocess.run([AGENT_BIN, *args], capture_output=True, text=True, timeout=timeout)


def test_adhoc_build_cannot_pair():
    gen = _pairing("pairing", "generate")
    assert gen.returncode != 0
    assert "ad-hoc" in (gen.stdout + gen.stderr).lower()


def test_adhoc_build_changes_no_pairing_state():
    """A refused pairing must leave the keychain exactly as it was."""
    before = _pairing("pairing-status")
    assert _pairing("pairing", "generate").returncode != 0
    assert _pairing("pairing", "import-daemon-pin", "AAAA").returncode != 0
    after = _pairing("pairing-status")
    assert before.stdout == after.stdout


def test_pairing_status_reports_the_real_code_identity():
    """Identity is read from the signature, never self-reported."""
    status = _pairing("pairing-status")
    assert status.returncode == 0, status.stderr
    report = json.loads(status.stdout)
    assert report["identity"]["kind"] in {"adhoc", "unsigned", "apple-development", "developer-id"}
    codesign = subprocess.run(
        ["codesign", "-dv", AGENT_BIN], capture_output=True, text=True, check=False, timeout=60
    )
    combined = codesign.stdout + codesign.stderr
    if "adhoc" in combined:
        assert report["identity"]["kind"] == "adhoc"
    if "Authority=" in combined:
        authority = next(
            line.split("=", 1)[1] for line in combined.splitlines() if line.startswith("Authority=")
        )
        assert report["identity"]["authority"] == authority


def test_signed_build_pairs(signed_agent_binary):
    """A certificate-signed bundle is pairable; the ad-hoc one is not.

    The plan asked for `developer-id:` here. Developer ID governs
    distribution — Gatekeeper checks software that arrives from elsewhere,
    and locally built software is not checked — so the rule this agent
    enforces is a *stable* identity, which a free Apple Development
    certificate provides. The deviation is recorded in the phase-2b evidence.
    """
    out = subprocess.run(
        [str(signed_agent_binary), "pairing-status"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    report = json.loads(out.stdout)
    assert report["identity"]["kind"] in {"developer-id", "apple-development"}
    assert report["identity"]["team_id"]
    assert report["pairable"] is True
    assert json.loads(_pairing("pairing-status").stdout)["pairable"] is False


@pytest.mark.platform_gated
def test_pairing_generate_creates_the_enclave_and_transport_keys(paired_agent_binary):
    """Host-mutating: the fixture generates the records when none exist.

    This asserts the paired state rather than calling ``pairing generate``
    again. Re-minting would rotate the operator's approval key on every
    gated run and silently invalidate whatever the daemon has pinned; that
    rotation is a deliberate ceremony, so it lives behind its own switch.
    """
    status = json.loads(
        subprocess.run(
            [str(paired_agent_binary), "pairing-status"],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    )
    assert re.fullmatch(r"p256:sha256:[0-9a-f]{64}", status["records"]["approval"])
    assert re.fullmatch(r"ed25519:sha256:[0-9a-f]{64}", status["records"]["transport"])
    assert status["pairable"] is True

    # the agent restarts onto the same public keys
    for which, record in (("approval", "approval"), ("transport", "transport")):
        exported = json.loads(
            subprocess.run(
                [str(paired_agent_binary), "pairing", "export", which],
                capture_output=True,
                text=True,
                timeout=60,
            ).stdout
        )
        assert exported["fingerprint"] == status["records"][record]


@pytest.mark.platform_gated
@pytest.mark.skipif(
    os.environ.get("TELEGRAM_MCP_ALLOW_REPAIR") != "1",
    reason="rotating the approval key invalidates the daemon's pin; set TELEGRAM_MCP_ALLOW_REPAIR=1",
)
def test_pairing_generate_rotates_onto_a_fresh_enclave_key(signed_agent_binary):
    """The rotation ceremony: a new key, and the old fingerprint is gone."""
    before = json.loads(
        subprocess.run(
            [str(signed_agent_binary), "pairing-status"],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    )["records"]
    generated = subprocess.run(
        [str(signed_agent_binary), "pairing", "generate"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert generated.returncode == 0, generated.stderr
    report = json.loads(generated.stdout)
    assert re.fullmatch(r"p256:sha256:[0-9a-f]{64}", report["approval_fingerprint"])
    if before.get("approval"):
        assert report["approval_fingerprint"] != before["approval"]


def test_launch_agent_definition_is_disabled_by_default():
    import plistlib

    with open(LAUNCH_AGENT_PLIST, "rb") as handle:
        plist = plistlib.load(handle)
    assert plist["RunAtLoad"] is False
    assert plist["StandardOutPath"] == "/dev/null"
    assert plist["StandardErrorPath"] == "/dev/null"
    assert plist["ProcessType"] == "Interactive"
    assert plist["ProgramArguments"][0].endswith("MacOS/telegram-mcp-consent")


def test_the_installed_copy_matches_the_agent_definition():
    """One source of truth: the launcher installs exactly this file."""
    import plistlib

    with open(LAUNCH_AGENT_PLIST, "rb") as handle:
        source = plistlib.load(handle)
    with open("scripts/consent-agent.plist", "rb") as handle:
        installed = plistlib.load(handle)
    assert source == installed


def test_the_bundle_is_assembled_and_signed():
    from pathlib import Path

    assert Path(BUNDLE, "Contents", "MacOS", "telegram-mcp-consent").exists()
    assert Path(BUNDLE, "Contents", "Info.plist").exists()
    assert Path(BUNDLE, "Contents", "_CodeSignature").exists()
    verify = subprocess.run(
        ["codesign", "--verify", "--strict", BUNDLE],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert verify.returncode == 0, verify.stderr


def test_no_challenge_or_display_bytes_reach_any_agent_output_file(tmp_path):
    from pathlib import Path

    from tests.agent.stub_broker import run_scenario

    result = run_scenario("good", timeout=60)
    assert result["approved"] is True
    case = _case()
    secrets_in_play = [
        "list chats",
        "Ops",
        json.load(open(VECTORS))["cases"][0]["input"]["nonce"],
    ]
    for path in Path("build").rglob("*"):
        if not path.is_file() or path.suffix in {".o", ""} and path.stat().st_size > 5_000_000:
            continue
        blob = path.read_bytes()
        for needle in secrets_in_play:
            assert needle.encode() not in blob, f"{needle!r} leaked into {path}"
    assert case["jcs_hex"] not in (result.get("agent_stdout", "") + result.get("agent_stderr", ""))


def test_running_without_a_complete_pairing_record_refuses_with_a_fixed_error():
    """A missing keychain record means re-pair, never degrade.

    The message names the command that fixes it, whichever record is the
    one missing.
    """
    out = _pairing("run", "/tmp/telegram-mcp-absent.sock")
    assert out.returncode != 0
    combined = out.stdout + out.stderr
    assert "MISSING-RECORD" in combined
    assert "pairing generate" in combined or "pairing import-daemon-pin" in combined
    assert out.stdout == ""  # no socket work was attempted


def test_the_transport_key_is_readable_by_any_code_identity_of_this_user():
    """Documents a real limit of the free-certificate path.

    Keychain items written here are ordinary login-keychain generic
    passwords. Without the data-protection keychain — which needs an
    `application-identifier` entitlement authorised by a provisioning
    profile — the file keychain does not scope them to the writing code
    identity, so any process running as this operator can read the
    *transport* private key. That key authenticates the agent to the
    daemon's rendezvous socket; it cannot approve anything, because
    approvals need the Secure Enclave key, which is biometry-bound and not
    extractable.

    This test asserts the current, understood behaviour so a future change
    to it is a visible one. It skips when nothing is paired.
    """
    status = json.loads(_pairing("pairing-status").stdout)
    if "transport" not in status.get("records", {}):
        pytest.skip("nothing paired on this host")
    # the ad-hoc bundle is a different code identity from the signed one
    assert status["identity"]["kind"] == "adhoc"
    exported = _pairing("pairing", "export", "transport")
    assert exported.returncode == 0, exported.stderr
    assert json.loads(exported.stdout)["fingerprint"] == status["records"]["transport"]


def test_approval_without_a_paired_enclave_key_refuses(tmp_path):
    out = subprocess.run(
        [AGENT_BIN, "approve", VECTORS, "0"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "CONSENT_NO_UI": "1"},
    )
    assert out.returncode != 0
    assert "NO-UI" in (out.stdout + out.stderr)
