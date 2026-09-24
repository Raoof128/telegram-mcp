"""_GatewayClient against a real TelegramClient and a fake sender: the wire boundary."""

import asyncio
import time

import pytest
from telethon import errors
from telethon.tl import functions, types

from comms.transports.telegram.telegram.deadline import WorkBudget, WorkBudgetExceeded
from comms.transports.telegram.telegram.telethon_adapter import (
    _default_factory,
    _operation,
    qualified,
)


class FakeSender:
    def __init__(self, outcome):
        self.outcome, self.sent = outcome, []

    def send(self, request, ordered=False):
        self.sent.append(qualified(request))
        future = asyncio.get_running_loop().create_future()
        if isinstance(self.outcome, BaseException):
            future.set_exception(self.outcome)
        else:
            future.set_result(self.outcome)
        return future


def _client(tmp_path, outcome):
    client = _default_factory(
        str(tmp_path / "s"),
        1,
        "0" * 32,
        receive_updates=False,
        request_retries=0,
        flood_sleep_threshold=0,
        raise_last_call_error=True,
    )
    client._sender = FakeSender(outcome)
    return client


def _history():
    return functions.messages.GetHistoryRequest(types.InputPeerEmpty(), 0, None, 0, 1, 0, 0, 0)


async def test_a_reviewed_request_is_sent_once_and_caches_entities(tmp_path):
    client = _client(
        tmp_path,
        types.messages.Messages(
            messages=[], topics=[], chats=[], users=[types.User(id=7, access_hash=1)]
        ),
    )
    with _operation("mcp.retrieval", WorkBudget()):
        await client(_history())
    assert client._sender.sent == ["messages.GetHistoryRequest"]  # InvokeWithoutUpdates unwrapped
    assert isinstance(client.session.get_input_entity(7), types.InputPeerUser)
    client.session.close()


async def test_outside_an_operation_or_its_allowlist_nothing_is_sent(tmp_path):
    client = _client(tmp_path, None)
    with pytest.raises(PermissionError):
        await client(_history())  # no operation at all
    with _operation("mcp.retrieval", WorkBudget()), pytest.raises(PermissionError):
        await client(functions.messages.ReadHistoryRequest(types.InputPeerEmpty(), 0))
    with _operation("mcp.retrieval", WorkBudget()), pytest.raises(PermissionError):
        await client(functions.updates.GetDifferenceRequest(pts=1, date=None, qts=0))
    assert client._sender.sent == []
    client.session.close()


async def test_auth_restart_is_raised_once_with_no_hidden_sleep(tmp_path):
    client = _client(tmp_path, errors.AuthRestartError(request=None))
    started = time.monotonic()
    with _operation("admin.login", WorkBudget()), pytest.raises(errors.AuthRestartError):
        await client(
            functions.auth.SendCodeRequest("9996621234", 1, "0" * 32, types.CodeSettings())
        )
    assert time.monotonic() - started < 0.5  # Telethon's own _call would sleep 2 s here
    assert client._sender.sent == ["auth.SendCodeRequest"]
    client.session.close()


async def test_every_request_is_charged_to_the_operation(tmp_path):
    client = _client(tmp_path, types.messages.Messages(messages=[], topics=[], chats=[], users=[]))
    with _operation("mcp.retrieval", WorkBudget(max_rpcs=1)):
        await client(_history())
        with pytest.raises(WorkBudgetExceeded):
            await client(_history())
    assert client._sender.sent == ["messages.GetHistoryRequest"]
    client.session.close()


def test_the_real_request_path_has_no_branch_for_test_clients():
    """CLAUDE.md: no test-only flags in production paths. Charging is uniform."""
    import inspect

    from comms.transports.telegram.telegram.telethon_adapter import TelethonSession

    assert "isinstance(self._client" not in inspect.getsource(TelethonSession._call_reviewed)


async def test_a_reviewed_call_is_charged_exactly_once_on_the_real_client(tmp_path):
    from comms.transports.telegram.telegram.deadline import Deadline
    from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession

    outcome = types.messages.Messages(messages=[], topics=[], chats=[], users=[])

    def factory(*args, **kwargs):
        client = _default_factory(*args, **kwargs)
        client._sender = FakeSender(outcome)
        return client

    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=factory,
    )
    session._prepare_dir()
    session._build()
    session.connected = session.authorized = True
    budget = WorkBudget(max_rpcs=1)
    await session._call_reviewed(
        _history(), operation="mcp.retrieval", client_ref="c", deadline=Deadline(5), budget=budget
    )
    assert budget.used == 1  # the session and the client did not both charge it
    session._client.session.close()
