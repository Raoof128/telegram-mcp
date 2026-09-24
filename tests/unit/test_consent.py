"""Consent challenge bytes and broker (Task 3, Phase-2a authority plan)."""

import base64
import hashlib
import json
import os
import re
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.transports.telegram.consent.challenge import (
    CHALLENGE_TTL_S,
    FIXTURE_CHALLENGE_KEY,
    SYNTHETIC_EXPOSURE_SNAPSHOT,
    StubSigner,
    canonical_challenge,
    display_digest,
    jcs_dumps,
    sign_challenge,
    synthetic_exposure_digest,
    verify_challenge_signature,
)

# --- Step 1: JCS byte-exactness (verbatim from the brief) ---


def test_jcs_is_sorted_compact_utf8():
    assert (
        jcs_dumps({"b": 1, "a": "é"})
        == b'{"a":"\\u00e9"'.replace(b"\\u00e9", "é".encode()) + b',"b":1}'
    )


# --- JCS profile edges ---


def test_jcs_escapes_only_c0_plus_quote_backslash():
    assert jcs_dumps({"a": 'x\x00y\nz\tq"r\\s'}) == b'{"a":"x\\u0000y\\nz\\tq\\"r\\\\s"}'
    # DEL and C1-and-above stay literal UTF-8.
    assert jcs_dumps({"a": "\x7f\x85\x9fé"}) == '{"a": "\x7f\x85\x9fé"}'.replace(" ", "").encode()


def test_jcs_rejects_non_canonical():
    import math

    with pytest.raises(ValueError):
        jcs_dumps({"a": 1.5})
    with pytest.raises(ValueError):
        jcs_dumps({"a": math.nan})
    with pytest.raises(ValueError):
        jcs_dumps({"a": math.inf})
    with pytest.raises(ValueError):
        jcs_dumps({"a": "lone\ud800surrogate"})
    with pytest.raises(ValueError):
        jcs_dumps({"é": 1})
    with pytest.raises(ValueError):
        jcs_dumps({1: "x"})
    with pytest.raises(ValueError):
        jcs_dumps({"a": b"bytes"})


# --- Step 3: display digest (verbatim from the brief) ---


def test_display_digest_vectors():
    payload = {
        "client_display": "Codex",
        "action_display": "read",
        "project_display": ["P"],
        "peer_display": None,
        "risk_class": "excerpt",
    }
    raw = jcs_dumps(payload)
    assert display_digest(payload) == hashlib.sha256(b"telegram-mcp-display-v1" + raw).hexdigest()


def test_display_digest_tamper_changes_digest():
    payload = {
        "client_display": "Codex",
        "action_display": "read",
        "project_display": ["P"],
        "peer_display": None,
        "risk_class": "excerpt",
    }
    before = display_digest(payload)
    tampered = dict(payload, action_display="write")
    assert display_digest(tampered) != before
    assert before == hashlib.sha256(b"telegram-mcp-display-v1" + jcs_dumps(payload)).hexdigest()


def test_synthetic_exposure_digest_is_frozen():
    assert jcs_dumps(SYNTHETIC_EXPOSURE_SNAPSHOT) == (
        b'{"bytes_disclosed":0,"mode":"synthetic",'
        b'"records_disclosed":0,"schema":"tg-mcp-exposure-snapshot/v1"}'
    )
    assert (
        synthetic_exposure_digest()
        == hashlib.sha256(jcs_dumps(SYNTHETIC_EXPOSURE_SNAPSHOT)).hexdigest()
    )


def test_canonical_challenge_field_set():
    raw = canonical_challenge(
        tool="telegram_list_projects",
        request_hmac="ab" * 32,
        principal="prn_" + "a" * 26,
        client="tcl_" + "b" * 26,
        account="tga_" + "c" * 26,
        policy_epoch=1,
        project_scope_digest="0" * 64,
        security_epoch=1,
        runtime_id=b"\x00" * 16,
        display_digest="1" * 64,
        exposure_snapshot_digest="2" * 64,
        nonce="A" * 22,
        expiry=1_800_000_000,
    )
    assert set(json.loads(raw)) == {
        "account",
        "canonical_request_hmac",
        "client",
        "display_digest",
        "exposure_snapshot_digest",
        "expiry",
        "nonce",
        "policy_epoch",
        "principal",
        "project_scope_digest",
        "runtime_id",
        "security_epoch",
        "tool",
    }
    assert json.loads(raw)["runtime_id"] == "00" * 16


def test_daemon_sign_roundtrip():
    raw = b'{"a":1}'
    sig = sign_challenge(raw, FIXTURE_CHALLENGE_KEY)
    public_bytes = (
        Ed25519PrivateKey.from_private_bytes(FIXTURE_CHALLENGE_KEY)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    assert verify_challenge_signature(raw, sig, public_bytes) is True
    assert verify_challenge_signature(b'{"a":2}', sig, public_bytes) is False


def test_stub_signer_shape():
    stub = StubSigner(seed=0x07)
    assert stub.key_id.startswith("p256:sha256:") and len(stub.key_id) == len("p256:sha256:") + 64
    msg = b"exact-bytes"
    assert stub.verify(stub.sign(msg), msg) is True
    assert stub.verify(stub.sign(msg), b"other-bytes") is False
    other = StubSigner(seed=0x08)
    assert other.key_id != stub.key_id
    assert stub.verify(other.sign(msg), msg) is False


def test_opaque_minter_single_copy():
    import comms.transports.telegram.consent.challenge as challenge_mod
    import comms.transports.telegram.opaque as opaque_mod

    # No second copy: challenge.py imports the minter/validator objects.
    assert challenge_mod.mint_opaque_ref is opaque_mod.mint_opaque_ref
    assert challenge_mod.validate_ref_format is opaque_mod.validate_ref_format
    refs = {opaque_mod.mint_opaque_ref("tgu_") for _ in range(100)}
    assert len(refs) == 100
    assert all(re.fullmatch(r"tgu_[a-z2-7]{26}", r) for r in refs)
    assert opaque_mod.validate_ref_format(next(iter(refs))) == "tgu_"
    with pytest.raises(ValueError):
        opaque_mod.validate_ref_format("tgu_short")


# --- Step 5: broker tests (verbatim from the brief) ---


async def _issued(stub, broker):
    handle = await broker.issue(
        tool="telegram_list_projects",
        request_hmac="ab" * 32,
        principal="prn_" + "a" * 26,
        client="tcl_" + "b" * 26,
        account="tga_" + "c" * 26,
        policy_epoch=1,
        project_scope_digest="0" * 64,
        security_epoch=1,
        display_digest="1" * 64,
        exposure_snapshot_digest="2" * 64,
    )
    raw = broker.challenge_bytes(handle)
    envelope = {
        "challenge_sha256": hashlib.sha256(raw).hexdigest(),
        "sig": stub.sign(raw),
        "key_id": stub.key_id,
    }
    return handle, envelope


async def test_replay_fails():
    from comms.transports.telegram.consent.broker import ConsentBroker, ConsentError
    from comms.transports.telegram.consent.challenge import StubSigner

    stub = StubSigner(seed=0x07)
    broker = ConsentBroker(
        challenge_key=b"\x01" * 32, agent_verify=stub.verify, runtime_id=b"\x00" * 16
    )
    handle, envelope = await _issued(stub, broker)
    await broker.consume(handle, envelope=envelope)
    with pytest.raises(ConsentError):
        await broker.consume(handle, envelope=envelope)


async def test_envelope_fields_all_verified():
    from comms.transports.telegram.consent.broker import ConsentBroker, ConsentError
    from comms.transports.telegram.consent.challenge import StubSigner

    stub = StubSigner(seed=0x07)
    broker = ConsentBroker(
        challenge_key=b"\x01" * 32, agent_verify=stub.verify, runtime_id=b"\x00" * 16
    )
    handle, good = await _issued(stub, broker)
    for bad in (
        {**good, "challenge_sha256": "0" * 64},
        {**good, "key_id": "p256:sha256:" + "f" * 64},
        {**good, "sig": b"\x00" * 64},
    ):
        with pytest.raises(ConsentError):
            await broker.consume(handle, envelope=bad)


# --- Broker: expiry, rate limits, invalidation, no-auto-approve ---


def _broker(stub, now_fn=None):
    from comms.transports.telegram.consent.broker import ConsentBroker

    return ConsentBroker(
        challenge_key=b"\x01" * 32, agent_verify=stub.verify, runtime_id=b"\x00" * 16, now=now_fn
    )


async def _issue_default(broker, **over):
    kw = {
        "tool": "telegram_list_projects",
        "request_hmac": "ab" * 32,
        "principal": "prn_" + "a" * 26,
        "client": "tcl_" + "b" * 26,
        "account": "tga_" + "c" * 26,
        "policy_epoch": 1,
        "project_scope_digest": "0" * 64,
        "security_epoch": 1,
        "display_digest": "1" * 64,
        "exposure_snapshot_digest": "2" * 64,
    }
    kw.update(over)
    return await broker.issue(**kw)


async def _envelope_for(stub, broker, handle):
    raw = broker.challenge_bytes(handle)
    return {
        "challenge_sha256": hashlib.sha256(raw).hexdigest(),
        "sig": stub.sign(raw),
        "key_id": stub.key_id,
    }


async def test_expiry_45s_frozen_clock():
    from comms.transports.telegram.consent.broker import ConsentError

    clock = [1_000_000.0]
    stub = StubSigner(seed=0x07)
    broker = _broker(stub, now_fn=lambda: clock[0])

    handle = await _issue_default(broker)
    clock[0] += CHALLENGE_TTL_S  # exactly 45s: still valid
    await broker.consume(handle, envelope=await _envelope_for(stub, broker, handle))

    handle2 = await _issue_default(broker)
    clock[0] += CHALLENGE_TTL_S + 0.001  # past deadline
    with pytest.raises(ConsentError) as exc:
        await broker.consume(handle2, envelope=await _envelope_for(stub, broker, handle2))
    assert exc.value.code == "challenge-expired"
    assert exc.value.dispatch_code == "CONSENT_DENIED"


async def test_rate_limits_per_client():
    from comms.transports.telegram.consent.broker import ConsentError

    clock = [2_000_000.0]
    stub = StubSigner(seed=0x07)
    broker = _broker(stub, now_fn=lambda: clock[0])

    for _ in range(5):
        await _issue_default(broker)
    with pytest.raises(ConsentError) as exc:
        await _issue_default(broker)
    assert exc.value.code == "rate-limited"
    assert exc.value.dispatch_code == "CONSENT_UNAVAILABLE"

    clock[0] += 61.0  # minute window slides
    await _issue_default(broker)

    # 30/hr: 30 issues spaced 100 seconds apart, the 31st fails.
    clock2 = [3_000_000.0]
    broker2 = _broker(stub, now_fn=lambda: clock2[0])
    for _ in range(30):
        await _issue_default(broker2)
        clock2[0] += 100.0
    with pytest.raises(ConsentError):
        await _issue_default(broker2)


async def test_invalidation_on_epoch_grant_disconnect():
    from comms.transports.telegram.consent.broker import ConsentError

    stub = StubSigner(seed=0x07)
    broker = _broker(stub)
    h_epoch = await _issue_default(broker, policy_epoch=1)
    h_grant = await _issue_default(broker, project_scope_digest="0" * 64)
    h_disc = await _issue_default(broker, client="tcl_" + "d" * 26)
    assert broker.pending_count() == 3
    # Epoch bump sweeps every record on the old epoch.
    assert broker.invalidate_where(lambda r: r.policy_epoch != 2) == 3
    assert broker.pending_count() == 0
    for handle in (h_epoch, h_grant, h_disc):
        with pytest.raises(ConsentError):
            await broker.consume(
                handle,
                envelope={"challenge_sha256": "0" * 64, "sig": b"", "key_id": stub.key_id},
            )


async def test_invalidation_legs_separately():
    from comms.transports.telegram.consent.broker import ConsentError

    stub = StubSigner(seed=0x07)
    broker = _broker(stub)
    # Epoch leg.
    handle = await _issue_default(broker, policy_epoch=1)
    assert broker.invalidate_where(lambda r: r.policy_epoch == 1) == 1
    with pytest.raises(ConsentError):
        await broker.consume(
            handle, envelope={"challenge_sha256": "0" * 64, "sig": b"", "key_id": stub.key_id}
        )
    # Grant leg (scope digest changed without an epoch bump).
    handle = await _issue_default(broker, project_scope_digest="0" * 64)
    assert broker.invalidate_where(lambda r: r.project_scope_digest != "9" * 64) == 1
    with pytest.raises(ConsentError):
        await broker.consume(
            handle, envelope={"challenge_sha256": "0" * 64, "sig": b"", "key_id": stub.key_id}
        )
    # Disconnect leg (per-client sweep).
    gone_client = "tcl_" + "e" * 26
    handle = await _issue_default(broker, client=gone_client)
    assert broker.invalidate_where(lambda r: r.client == gone_client) == 1
    with pytest.raises(ConsentError):
        await broker.consume(
            handle, envelope={"challenge_sha256": "0" * 64, "sig": b"", "key_id": stub.key_id}
        )
    # Single-handle invalidate.
    handle = await _issue_default(broker)
    assert broker.invalidate(handle) is True
    assert broker.invalidate(handle) is False


async def test_no_auto_approve_path():
    from comms.transports.telegram.consent.broker import ConsentBroker, ConsentError

    stub = StubSigner(seed=0x07)
    broker = _broker(stub)
    assert not hasattr(broker, "approve")
    # Shell-triggered issuance alone grants nothing: consume without a valid
    # agent signature fails every way.
    handle = await _issue_default(broker)
    raw = broker.challenge_bytes(handle)
    good_sha = hashlib.sha256(raw).hexdigest()
    for bad_envelope in (
        {"challenge_sha256": good_sha, "sig": b"", "key_id": stub.key_id},
        {"challenge_sha256": good_sha, "key_id": stub.key_id},
        {
            "challenge_sha256": good_sha,
            "sig": StubSigner(seed=0x08).sign(raw),
            "key_id": StubSigner(seed=0x08).key_id,
        },
    ):
        with pytest.raises(ConsentError):
            await broker.consume(handle, envelope=bad_envelope)
    assert isinstance(broker, ConsentBroker)


async def test_tampered_challenge_bytes_fail_agent_sig():
    from comms.transports.telegram.consent.broker import ConsentError

    stub = StubSigner(seed=0x07)
    broker = _broker(stub)
    handle = await _issue_default(broker)
    raw = broker.challenge_bytes(handle)
    tampered = bytearray(raw)
    tampered[-2] ^= 0x01
    envelope = {
        "challenge_sha256": hashlib.sha256(raw).hexdigest(),
        "sig": stub.sign(bytes(tampered)),
        "key_id": stub.key_id,
    }
    with pytest.raises(ConsentError) as exc:
        await broker.consume(handle, envelope=envelope)
    assert exc.value.code == "bad-agent-signature"


async def test_error_codes_map_to_dispatch():
    from comms.transports.telegram.consent.broker import ConsentError

    assert ConsentError("challenge-mismatch").dispatch_code == "CONSENT_DENIED"
    assert ConsentError("unknown-key").dispatch_code == "CONSENT_DENIED"
    assert ConsentError("bad-agent-signature").dispatch_code == "CONSENT_DENIED"
    assert ConsentError("unknown-challenge").dispatch_code == "CONSENT_DENIED"
    assert ConsentError("rate-limited").dispatch_code == "CONSENT_UNAVAILABLE"
    assert ConsentError("challenge-expired").dispatch_code == "CONSENT_DENIED"


# --- Step 7: frozen JCS vectors (single source of truth for Plan 2b) ---

VECTORS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "canonical" / "jcs_vectors.json"

_FIXED_RUNTIME = b"\x00" * 16
_FIXED_EXPIRY_BASE = 1_800_000_000


def _display_case_payloads():
    base = {"client_display": "Codex", "risk_class": "excerpt"}
    return [
        (
            "persian",
            {
                **base,
                "action_display": "خواندن پیام‌ها",
                "project_display": ["پروژه خانواده"],
                "peer_display": "گروه خانواده",
            },
        ),
        (
            "latin-accent",
            {
                **base,
                "action_display": "café — naïve résumé",
                "project_display": ["P"],
                "peer_display": "André",
            },
        ),
        (
            "emoji",
            {
                **base,
                "action_display": "🔍 search 📨❤️",
                "project_display": ["🎉"],
                "peer_display": "🎉 release",
            },
        ),
        (
            "c0-controls",
            {
                **base,
                "action_display": "a\x00b\nc\td",
                "project_display": ["P"],
                "peer_display": None,
            },
        ),
        (
            "c1-controls",
            {
                **base,
                "action_display": "a\x85b\x9fc",
                "project_display": ["P"],
                "peer_display": None,
            },
        ),
        (
            "escapes",
            {
                **base,
                "action_display": 'say "hi" \\ bye\nnewline',
                "project_display": ["P"],
                "peer_display": None,
            },
        ),
        (
            "bidi",
            {
                **base,
                "action_display": "a\u202eb\u2066c\u2069d",
                "project_display": ["P"],
                "peer_display": None,
            },
        ),
        ("nested", {"a": {"z": [3, 2, 1], "m": None, "t": True, "n": {"x": "é"}}}),
        ("array-of-objects", {"items": [{"b": 1, "a": 2}, {"b": 3, "a": 4}]}),
    ]


def _build_vector_cases():
    cases = []
    for index, (name, display_input) in enumerate(_display_case_payloads()):
        digest = display_digest(display_input)
        nonce_raw = hashlib.sha256(f"tg-mcp-vector/{name}".encode()).digest()[:16]
        nonce = base64.urlsafe_b64encode(nonce_raw).decode("ascii").rstrip("=")
        raw = canonical_challenge(
            tool="telegram_list_projects",
            request_hmac="ab" * 32,
            principal="prn_" + "a" * 26,
            client="tcl_" + "b" * 26,
            account="tga_" + "c" * 26,
            policy_epoch=1,
            project_scope_digest="0" * 64,
            security_epoch=1,
            runtime_id=_FIXED_RUNTIME,
            display_digest=digest,
            exposure_snapshot_digest=synthetic_exposure_digest(),
            nonce=nonce,
            expiry=_FIXED_EXPIRY_BASE + index,
        )
        cases.append(
            {
                "name": name,
                "input": json.loads(raw.decode("utf-8")),
                "display_input": display_input,
                "jcs_hex": raw.hex(),
                "display_digest": digest,
                "challenge_digest": hashlib.sha256(raw).hexdigest(),
                "sig": sign_challenge(raw, FIXTURE_CHALLENGE_KEY),
            }
        )
    private = Ed25519PrivateKey.from_private_bytes(FIXTURE_CHALLENGE_KEY)
    public_hex = (
        private.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )
    return {"version": 1, "challenge_key_public_hex": public_hex, "cases": cases}


@pytest.mark.skipif(
    os.environ.get("TELEGRAM_MCP_WRITE_VECTORS") != "1" and VECTORS_PATH.exists(),
    reason="generator runs once to freeze vectors (TELEGRAM_MCP_WRITE_VECTORS=1)",
)
def test_generate_vectors_once():
    VECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if VECTORS_PATH.exists() and os.environ.get("TELEGRAM_MCP_WRITE_VECTORS") != "1":
        pytest.skip("vectors already frozen")
    VECTORS_PATH.write_text(
        json.dumps(_build_vector_cases(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def test_vectors_frozen_shape_and_bytes():
    data = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["cases"]) >= 3
    public_bytes = bytes.fromhex(data["challenge_key_public_hex"])
    for case in data["cases"]:
        for key in ("input", "jcs_hex", "display_digest", "challenge_digest", "sig"):
            assert key in case, case.get("name")
        raw = bytes.fromhex(case["jcs_hex"])
        # Byte-equality: recompute everything from the frozen inputs.
        assert jcs_dumps(case["input"]) == raw
        assert display_digest(case["display_input"]) == case["display_digest"]
        assert case["input"]["display_digest"] == case["display_digest"]
        assert hashlib.sha256(raw).hexdigest() == case["challenge_digest"]
        assert verify_challenge_signature(raw, case["sig"], public_bytes) is True
