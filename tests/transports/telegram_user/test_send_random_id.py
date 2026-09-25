"""comms v0.3 Task C15: MTProto sends deduplicated by random_id, reconciled once (A20, A42)."""

import hashlib

import pytest
from telethon import errors
from telethon.tl import functions, types

from comms.core import domains
from comms.core.delivery import freeze
from comms.core.delivery.engine import Engine, EngineCrash, ExecutorLease
from comms.core.delivery.recovery import recover
from comms.core.delivery.transport import ResultKind
from comms.core.providers.capability import Capability
from comms.core.storage.db import write_tx
from comms.transports.telegram.telegram.errors import GatewayError
from comms.transports.telegram.telegram.send_attempt import SendAttempt
from comms.transports.telegram.telegram.telethon_adapter import (
    WRITE_RPCS,
    TelegramConfig,
    TelethonSession,
    qualified,
)
from comms.transports.telegram.user.send import (
    RECONCILE_WINDOW_S,
    correlate_message_id,
    random_id_for,
    send,
)
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, person, ready
from tests.telegram.fake_client import FakeClient

KEY = "ab" * 32
CHAT = "-1001234567890"
PEER = types.InputPeerChannel(1234567890, 99)


class FakeServer:
    """Telegram's side: dedupes by random_id and counts the messages a person would see."""

    def __init__(self, script):
        self.script = list(
            script
        )  # per call: "ok", "ok_then_drop", "drop", "flood", "forbidden", ...
        self.visible = {}  # random_id -> message id
        self.calls = []
        self.now = 0.0

    def clock(self):
        return self.now

    async def send_text_once(self, peer, text, random_id, *, timeout, reply_to=None):
        assert timeout > 0
        self.calls.append((random_id, text, timeout))
        step = self.script.pop(0)
        if step == "advance_then_drop":
            self.now += RECONCILE_WINDOW_S + 1
            return SendAttempt("ambiguous")
        if step in ("drop", "timeout"):
            return SendAttempt("ambiguous")
        if step == "flood":
            return SendAttempt("flood", retry_after=30)
        if step == "forbidden":
            return SendAttempt("refused")
        if step == "weird":
            return SendAttempt("failed")
        if random_id in self.visible:
            return SendAttempt("duplicate")
        self.visible[random_id] = len(self.visible) + 100
        if step == "ok_then_drop":
            return SendAttempt("ambiguous")  # accepted, then the link dropped
        return SendAttempt("sent", message_id=self.visible[random_id])


async def _send(server):
    return await send(server, PEER, CHAT, "hello", random_id_for(KEY), clock=server.clock)


def test_random_id_deterministic_nonzero_signed():
    rid = random_id_for(KEY)
    assert rid == random_id_for(KEY) and rid != random_id_for("cd" * 32)
    assert -(2**63) <= rid < 2**63 and rid != 0
    raw = hashlib.sha256(domains.MTPROTO_RANDOM_ID + bytes.fromhex(KEY)).digest()[:8]
    assert rid == (int.from_bytes(raw, "big", signed=True) or 1)
    assert domains.MTPROTO_RANDOM_ID == b"comms-mtproto-random-id/v1\0"
    with pytest.raises(ValueError):
        random_id_for("not hex")


async def test_a_clean_send_is_accepted_with_the_chat_qualified_ref():
    server = FakeServer(["ok"])
    result = await _send(server)
    assert (result.kind, result.provider_message_ref) == (ResultKind.ACCEPTED, f"{CHAT}:100")
    assert len(server.calls) == 1


async def test_ambiguous_then_duplicate_is_accepted_one_visible_effect():
    server = FakeServer(["ok_then_drop", "ok"])
    result = await _send(server)
    assert result.kind is ResultKind.ACCEPTED and result.provider_message_ref is None
    assert len(server.visible) == 1


async def test_ambiguous_then_success_is_accepted():
    server = FakeServer(["drop", "ok"])
    result = await _send(server)
    assert (result.kind, result.provider_message_ref) == (ResultKind.ACCEPTED, f"{CHAT}:100")
    assert len(server.visible) == 1


@pytest.mark.parametrize("second", ["drop", "timeout", "weird", "forbidden", "flood"])
async def test_at_most_one_duplicate_rpc_with_identical_random_id(second):
    server = FakeServer(["timeout", second])
    result = await _send(server)
    assert result.kind is ResultKind.OUTCOME_UNKNOWN
    assert len(server.calls) == 2 and server.calls[0][:2] == server.calls[1][:2]


async def test_reconcile_window_bounded():
    server = FakeServer(["advance_then_drop"])
    result = await _send(server)
    assert result.kind is ResultKind.OUTCOME_UNKNOWN and len(server.calls) == 1
    server = FakeServer(["drop", "ok"])
    await _send(server)
    assert all(timeout <= RECONCILE_WINDOW_S for _r, _m, timeout in server.calls)


async def test_flood_wait_first_call_is_failed_transient():
    result = await _send(FakeServer(["flood"]))
    assert (result.kind, result.retry_after) == (ResultKind.FAILED_TRANSIENT, 30)


async def test_a_documented_refusal_first_is_failed_permanent():
    assert (await _send(FakeServer(["forbidden"]))).kind is ResultKind.FAILED_PERMANENT


async def test_a_duplicate_on_the_first_call_is_accepted():
    """An earlier attempt of this job (before a restart) already reached Telegram."""
    server = FakeServer(["ok"])
    server.visible[random_id_for(KEY)] = 7
    result = await _send(server)
    assert result.kind is ResultKind.ACCEPTED and len(server.calls) == 1


async def test_anything_else_first_is_outcome_unknown_without_a_reissue():
    server = FakeServer(["weird"])
    assert (await _send(server)).kind is ResultKind.OUTCOME_UNKNOWN and len(server.calls) == 1


def test_the_request_is_the_reviewed_send_class():
    request = functions.messages.SendMessageRequest(PEER, "x", random_id=1)
    assert qualified(request) in WRITE_RPCS[Capability.MESSAGE_SEND]


def test_update_message_id_after_restart_correlates_by_random_id(tmp_path):
    """Crash after the transmission; on restart a late updateMessageID binds the attempt."""
    conn = fx.migrated(tmp_path)
    transport = fakes.KeyedTelegram(conn, key_of=lambda idem: str(random_id_for(idem)))
    rcp, _ = person(conn, tg="channel:1234567890")
    cmp = ready(conn, {"recipients": [rcp]}, frozenset({"telegram"}))
    freeze.send(conn, cmp, {"telegram": transport}, now=NOW)
    lease = ExecutorLease(fakes.FakeLock())
    with pytest.raises(EngineCrash):
        Engine(
            conn, {"telegram": transport}, clock=lambda: NOW, crash_at="after_deliver_before_record"
        ).execute(lease, cmp)
    recover(lease, conn, now=NOW)
    key = conn.execute("SELECT idempotency_key FROM delivery_jobs").fetchone()[0]
    with write_tx(conn):
        assert correlate_message_id(conn, random_id_for(key), 555, now=NOW) is True
    ref = conn.execute("SELECT provider_message_ref FROM delivery_attempts").fetchone()[0]
    assert ref == "-1001234567890:555"
    with write_tx(conn):
        assert (
            correlate_message_id(conn, random_id_for(key), 555, now=NOW) is False
        )  # already bound
        assert correlate_message_id(conn, 12345, 1, now=NOW) is False  # not ours


async def test_the_session_sends_one_reviewed_rpc_per_capability_call(tmp_path):
    fake = FakeClient({"messages.SendMessageRequest": errors.RandomIdDuplicateError(request=None)})
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    fake.calls.clear()
    result = await send(session, types.InputPeerSelf(), "42", "hi", random_id_for(KEY))
    assert result.kind is ResultKind.ACCEPTED and fake.calls == ["messages.SendMessageRequest"]
    with pytest.raises(GatewayError, match="INTERNAL_ERROR"):  # not in message.send's set
        await session.call_capability(
            Capability.MESSAGE_SEND, functions.messages.DeleteMessagesRequest(id=[1]), timeout=5
        )
    await session.stop()


@pytest.mark.parametrize(
    "scripted,outcome",
    [
        (errors.RandomIdDuplicateError(request=None), "duplicate"),
        (errors.ChatWriteForbiddenError(request=None), "refused"),
        (errors.FloodWaitError(request=None, capture=9), "flood"),
        (ConnectionError("dropped"), "ambiguous"),
        (errors.ServerError(request=None, message="x", code=500), "ambiguous"),
        (errors.RPCError(request=None, message="WEIRD", code=400), "failed"),
        (
            lambda request: types.UpdateShortSentMessage(id=77, pts=1, pts_count=1, date=None),
            "sent",
        ),
    ],
)
async def test_the_adapter_classifies_each_send_outcome(tmp_path, scripted, outcome):
    fake = FakeClient({"messages.SendMessageRequest": scripted})
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    attempt = await session.send_text_once(types.InputPeerSelf(), "hi", 5, timeout=5)
    assert attempt.outcome == outcome
    assert (attempt.retry_after, attempt.message_id) == (
        9 if outcome == "flood" else None,
        77 if outcome == "sent" else None,
    )
    await session.stop()
