# tests/unit/test_consent_phase4.py
"""Phase 4a: what a consumed approval must carry back (design §2.3, G3, G4)."""

import hashlib
import json

from telegram_mcp.consent.broker import ConsentBroker, ConsentError
from telegram_mcp.consent.challenge import StubSigner

PRINCIPAL = "prn_" + "a" * 26
CLIENT = "tcl_" + "b" * 26
ACCOUNT = "tga_" + "c" * 26


def _broker(stub: StubSigner) -> ConsentBroker:
    return ConsentBroker(
        challenge_key=b"\x01" * 32, agent_verify=stub.verify, runtime_id=b"\x02" * 16
    )


async def _issue_and_consume(broker: ConsentBroker, stub: StubSigner, exposure: str):
    handle = await broker.issue(
        tool="telegram_list_projects",
        request_hmac="ab" * 32,
        principal=PRINCIPAL,
        client=CLIENT,
        account=ACCOUNT,
        policy_epoch=1,
        project_scope_digest="1" * 64,
        security_epoch=1,
        display_digest="2" * 64,
        exposure_snapshot_digest=exposure,
    )
    challenge = broker.challenge_bytes(handle)
    envelope = {
        "challenge_sha256": hashlib.sha256(challenge).hexdigest(),
        "sig": stub.sign(challenge),
        "key_id": stub.key_id,
    }
    return json.loads(challenge), await broker.consume(handle, envelope)


async def test_consumed_challenge_carries_the_signed_nonce_and_exposure_digest():
    stub = StubSigner(seed=0x07)
    signed, consumed = await _issue_and_consume(_broker(stub), stub, "3" * 64)
    assert consumed.nonce == signed["nonce"]
    assert consumed.exposure_snapshot_digest == "3" * 64 == signed["exposure_snapshot_digest"]


async def test_identical_arguments_get_different_nonces():
    stub = StubSigner(seed=0x07)
    broker = _broker(stub)
    _first_signed, first = await _issue_and_consume(broker, stub, "3" * 64)
    _second_signed, second = await _issue_and_consume(broker, stub, "3" * 64)
    assert first.nonce != second.nonce


def test_a_consent_timeout_is_a_denial():
    # §27.1: CONSENT_DENIED is "denied/cancelled/timed out".
    assert ConsentError("challenge-expired").dispatch_code == "CONSENT_DENIED"
