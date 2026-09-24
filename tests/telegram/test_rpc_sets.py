"""comms v0.3 Task C14: Telethon client discipline and the capability RPC sets (A21)."""

import asyncio

import pytest
from telethon.network.requeststate import RequestState
from telethon.tl import functions, types

from comms.core.providers.capability import Capability as C
from comms.core.providers.semantics import READS, SUPPORT
from comms.transports.telegram.telegram import telethon_adapter as adapter
from comms.transports.telegram.telegram.deadline import Deadline, WorkBudget
from comms.transports.telegram.telegram.errors import GatewayError
from comms.transports.telegram.telegram.telethon_adapter import (
    ADMIN_RPCS,
    OPERATIONS,
    READ_RPCS,
    SESSION_RPCS,
    WRITE_RPCS,
    TelegramConfig,
    TelethonSession,
    capability_operation,
)
from tests.telegram.fake_client import FakeClient

USER_CAPS = {c for c, actors in SUPPORT.items() if "telegram_user" in actors}


def _real_session(tmp_path, **kwargs):
    captured = {}

    def factory(path, api_id, api_hash, **kw):
        captured.update(kw)
        return adapter._default_factory(path, api_id, api_hash, **kw)

    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "session"),
        api_hash="0" * 32,
        client_factory=factory,
    )
    session._prepare_dir()
    session._build()
    return session, captured


def test_client_constructed_with_request_retries_0_and_flood_sleep_threshold_0(tmp_path):
    session, kwargs = _real_session(tmp_path)
    assert kwargs == {
        "receive_updates": False,
        "request_retries": 0,
        "flood_sleep_threshold": 0,
        "raise_last_call_error": True,
        "auto_reconnect": False,  # A21: a reconnect is always an explicit Comms decision
        "connection_retries": 0,
    }
    client = session._client
    assert (client._request_retries, client.flood_sleep_threshold) == (0, 0)
    assert client._sender._auto_reconnect is False and client._sender._retries == 0


def test_rpc_sets_disjoint_and_named_by_capability():
    assert set(READ_RPCS) == USER_CAPS & READS
    assert set(WRITE_RPCS) | set(ADMIN_RPCS) == USER_CAPS - READS
    assert not set(WRITE_RPCS) & set(ADMIN_RPCS)
    reads, writes, admins = (
        frozenset().union(*s.values()) for s in (READ_RPCS, WRITE_RPCS, ADMIN_RPCS)
    )
    assert not (reads & writes) and not (reads & admins) and not (writes & admins)
    assert not (SESSION_RPCS & (reads | writes | admins))
    for rpc_set in (READ_RPCS, WRITE_RPCS, ADMIN_RPCS):
        for cap, names in rpc_set.items():
            assert names and OPERATIONS[capability_operation(cap)] == names, cap
            for name in names:  # every name is a real request class in this Telethon layer
                module, cls = name.split(".")
                assert hasattr(getattr(functions, module), cls), name


def test_no_write_or_admin_rpc_is_allowed_on_a_read_path():
    writes = frozenset().union(*WRITE_RPCS.values(), *ADMIN_RPCS.values())
    for operation in ("mcp.retrieval", "admin.discover", "admin.status", "admin.login"):
        assert not (OPERATIONS[operation] & writes), operation
    assert OPERATIONS[capability_operation(C.MESSAGE_SEND)] == {"messages.SendMessageRequest"}


class _Sender:
    def __init__(self):
        self.sent = []

    async def send(self, request, ordered=False):
        self.sent.append(adapter.qualified(request))
        return types.Updates(updates=[], users=[], chats=[], date=None, seq=0)


async def test_recorder_refuses_unlisted_constructor(tmp_path):
    session, _kw = _real_session(tmp_path)
    sender = _Sender()
    delete = functions.messages.DeleteMessagesRequest(id=[1], revoke=True)
    send = functions.messages.SendMessageRequest(types.InputPeerSelf(), "hi", random_id=7)
    with adapter._operation(capability_operation(C.MESSAGE_SEND), WorkBudget()):
        with pytest.raises(PermissionError):
            await session._client._call(sender, delete)
        await session._client._call(sender, send)
    with adapter._operation("mcp.retrieval", WorkBudget()), pytest.raises(PermissionError):
        await session._client._call(sender, send)
    assert sender.sent == ["messages.SendMessageRequest"]


class _Connection:
    async def disconnect(self):
        return None


async def _drop_mid_rpc(sender):
    """An in-flight write, then the connection drops while Telethon's reconnect would succeed."""
    loop = asyncio.get_running_loop()
    state = RequestState(
        functions.messages.SendMessageRequest(types.InputPeerSelf(), "hi", random_id=7)
    )
    state.future = loop.create_future()
    sender._pending_state[1] = state
    sender._user_connected, sender._connection = True, _Connection()

    async def connected():
        sender._connection = _Connection()

    sender._connect = connected
    await sender._reconnect(ConnectionError("dropped"))
    return state


async def test_reconnect_does_not_replay_in_flight_write(tmp_path):
    session, _kw = _real_session(tmp_path)
    state = await _drop_mid_rpc(session._client._sender)
    assert list(session._client._sender._send_queue._deque) == []  # nothing queued to resend
    with pytest.raises(ConnectionError):
        state.future.result()  # the caller sees the failure; Comms decides what happens next


async def test_the_replay_the_guard_prevents_is_real(tmp_path):
    """Control: Telethon's default (auto_reconnect=True) re-sends the in-flight write."""
    session, _kw = _real_session(tmp_path)
    sender = session._client._sender
    sender._auto_reconnect, sender._retries = True, 1
    state = await _drop_mid_rpc(sender)
    assert state in list(sender._send_queue._deque) and not state.future.done()


async def test_a_dropped_link_marks_the_session_disconnected(tmp_path):
    fake = FakeClient({"messages.GetHistoryRequest": ConnectionError("dropped")})
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    assert session.connected
    history = functions.messages.GetHistoryRequest(types.InputPeerEmpty(), 0, None, 0, 1, 0, 0, 0)
    with pytest.raises(GatewayError, match="TELEGRAM_UNAVAILABLE"):
        await session._call_reviewed(
            history,
            operation="mcp.retrieval",
            client_ref="c",
            deadline=Deadline(5),
            budget=WorkBudget(),
        )
    assert session.connected is False  # the daemon's keeper reconnects explicitly
    await session.stop()
