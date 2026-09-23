"""CoordinatorConsent: real broker, real prompter, a signing fake agent."""

import asyncio
import base64
import hashlib
import socket

import pytest

from telegram_mcp.consent.broker import ConsentBroker
from telegram_mcp.consent.challenge import StubSigner
from telegram_mcp.consent.prompter import Prompter
from telegram_mcp.disclosure.budget import GLOBAL, BucketKey, Usage
from telegram_mcp.disclosure.coordinator import ConsentRefusal
from telegram_mcp.disclosure.exposure import exposure_digest
from telegram_mcp.disclosure.seams import CoordinatorConsent
from telegram_mcp.ipc.framing import decode_json_frame, encode_json_frame, read_frame, write_frame

G = BucketKey(1, GLOBAL, "a" * 64)


class _Snap:
    principal_ref = "prn_" + "a" * 26
    client_ref = "tcl_" + "b" * 26
    account_ref = "tga_" + "c" * 26
    client_kind = "codex_local"
    policy_epoch = 1
    security_epoch = 1
    scope_hex = "1" * 64
    egress_level = "metadata_only"


class _Req:
    canonical_request_hmac = "ab" * 32


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


async def _world(answer="approve"):
    stub = StubSigner(seed=0x07)
    broker = ConsentBroker(
        challenge_key=b"\x01" * 32, agent_verify=stub.verify, runtime_id=b"\x02" * 16
    )
    prompter = Prompter()
    left, right = socket.socketpair()
    d_reader, d_writer = await asyncio.open_connection(sock=left)
    a_reader, a_writer = await asyncio.open_connection(sock=right)
    session = asyncio.create_task(prompter.attach(d_reader, d_writer))
    await asyncio.sleep(0)  # let attach() register the session before anyone prompts

    async def agent():
        while True:
            raw = await read_frame(a_reader)
            if not raw:
                return
            frame = decode_json_frame(raw)
            challenge = base64.urlsafe_b64decode(
                frame["challenge"] + "=" * (-len(frame["challenge"]) % 4)
            )
            if answer == "silent":
                continue
            if answer == "deny":
                reply = {"type": "DENIAL", "handle": frame["handle"], "reason": "USER-CANCEL"}
            else:
                reply = {
                    "type": "APPROVAL",
                    "handle": frame["handle"],
                    "envelope": {
                        "challenge_sha256": hashlib.sha256(challenge).hexdigest(),
                        "sig": _b64url(stub.sign(challenge)),
                        "key_id": stub.key_id,
                    },
                }
            await write_frame(a_writer, encode_json_frame(reply))

    helper = asyncio.create_task(agent())
    return broker, prompter, session, helper


async def _ask(consent, projected=None):
    projected = projected or {G: Usage(3, 300)}
    issued = await consent.issue(
        tool_name="telegram_list_projects",
        snapshot=_Snap(),
        request=_Req(),
        tier="normal",
        projected=projected,
        worst_case={G: Usage(1, 100)},
    )
    return issued, await consent.consume(issued)


async def test_approval_binds_the_displayed_exposure():
    broker, prompter, session, helper = await _world()
    consent = CoordinatorConsent(broker, prompter, wait_s=5)
    issued, approval = await _ask(consent)
    assert approval is not None and len(approval.nonce) == 22
    assert approval.exposure_snapshot_digest == exposure_digest("normal", {G: Usage(3, 300)})
    assert consent.snapshot_matches(approval, tier="normal", projected={G: Usage(3, 300)})
    assert not consent.snapshot_matches(approval, tier="normal", projected={G: Usage(4, 300)})
    assert "2->3 records" in issued.display["risk_class"]
    session.cancel(), helper.cancel()


async def test_two_prompts_never_share_an_approval():
    broker, prompter, session, helper = await _world()
    consent = CoordinatorConsent(broker, prompter, wait_s=5)
    (one_issued, one), (two_issued, two) = await asyncio.gather(_ask(consent), _ask(consent))
    assert one.handle == one_issued.handle and two.handle == two_issued.handle
    assert one.handle != two.handle and one.nonce != two.nonce
    session.cancel(), helper.cancel()


async def test_denial_and_timeout_are_denials_and_leave_nothing_pending():
    for mode in ("deny", "silent"):
        broker, prompter, session, helper = await _world(answer=mode)
        consent = CoordinatorConsent(broker, prompter, wait_s=0.2)
        _issued, approval = await _ask(consent)
        assert approval is None
        assert broker.pending_count() == 0
        session.cancel(), helper.cancel()


async def test_cancellation_mid_prompt_propagates_and_leaves_nothing_pending():
    broker, prompter, session, helper = await _world(answer="silent")
    consent = CoordinatorConsent(broker, prompter, wait_s=30)
    task = asyncio.create_task(_ask(consent))
    while broker.pending_count() == 0:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)  # the PROMPT frame is on the wire
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert broker.pending_count() == 0
    session.cancel(), helper.cancel()


async def test_no_agent_is_unavailable_and_leaves_nothing_pending():
    stub = StubSigner(seed=0x07)
    broker = ConsentBroker(
        challenge_key=b"\x01" * 32, agent_verify=stub.verify, runtime_id=b"\x02" * 16
    )
    consent = CoordinatorConsent(broker, Prompter(), wait_s=5)
    with pytest.raises(ConsentRefusal) as refused:
        await _ask(consent)
    assert refused.value.code == "CONSENT_UNAVAILABLE"
    assert broker.pending_count() == 0
