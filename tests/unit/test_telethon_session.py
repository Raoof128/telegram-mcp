"""The session core: construction, lock, readiness, login state machine, errors."""

import asyncio
import os
import stat

import pytest
from telethon import errors
from telethon.tl import functions, types

from comms.transports.telegram.telegram.deadline import Deadline, WorkBudget
from comms.transports.telegram.telegram.errors import GatewayError
from comms.transports.telegram.telegram.telethon_adapter import (
    TelegramConfig,
    TelethonSession,
    translate,
)
from tests.telegram.fake_client import FakeClient

PHONE = "9996621234"


def _session(tmp_path, client=None, **kw):
    fake = client or FakeClient()
    captured = {}

    def factory(path, api_id, api_hash, **kwargs):
        captured.update(path=path, api_id=api_id, kwargs=kwargs)
        return fake

    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "session", **kw),
        api_hash="0" * 32,
        client_factory=factory,
    )
    return session, fake, captured


def _history():
    return functions.messages.GetHistoryRequest(types.InputPeerEmpty(), 0, None, 0, 1, 0, 0, 0)


async def test_the_client_is_built_exactly_as_section_36_requires(tmp_path):
    session, _fake, captured = _session(tmp_path)
    await session.start()
    assert captured["kwargs"] == {
        "receive_updates": False,
        "request_retries": 0,
        "flood_sleep_threshold": 0,
        "raise_last_call_error": True,
    }
    assert stat.S_IMODE(os.stat(tmp_path / "session").st_mode) == 0o700
    await session.stop()


async def test_a_second_session_on_the_same_directory_fails_closed(tmp_path):
    first, _f, _c = _session(tmp_path)
    await first.start()
    second, _f2, _c2 = _session(tmp_path)
    with pytest.raises(GatewayError) as exc:
        await second.start()
    assert exc.value.code == "ACCOUNT_UNAVAILABLE"
    await first.stop()


async def test_test_dc_is_applied_to_a_fresh_session(tmp_path):
    session, fake, _c = _session(tmp_path, test_dc=(2, "149.154.167.40", 80))
    await session.start()
    assert fake.session.dc == (2, "149.154.167.40", 80)


@pytest.mark.parametrize(
    "exc,code",
    [
        (errors.FloodWaitError(request=None, capture=7), "FLOOD_WAIT"),
        (errors.AuthKeyUnregisteredError(request=None), "SESSION_REVOKED"),
        (errors.SessionRevokedError(request=None), "SESSION_REVOKED"),
        (errors.UserDeactivatedBanError(request=None), "SESSION_REVOKED"),
        (errors.ChannelPrivateError(request=None), "NOT_ACCESSIBLE"),
        (errors.MsgIdInvalidError(request=None), "MESSAGE_NOT_FOUND"),
        (errors.ServerError(request=None, message="x", code=500), "TELEGRAM_UNAVAILABLE"),
        (errors.AuthRestartError(request=None), "TELEGRAM_UNAVAILABLE"),
        (errors.PhoneMigrateError(request=None, capture=4), "TELEGRAM_UNAVAILABLE"),
        (ConnectionError(), "TELEGRAM_UNAVAILABLE"),
        (errors.RPCError(request=None, message="WEIRD", code=400), "INTERNAL_ERROR"),
    ],
)
def test_errors_translate_specific_first(exc, code):
    translated = translate(exc)
    assert translated.code == code
    if code == "FLOOD_WAIT":
        assert translated.retry_after == 7


async def test_an_unreviewed_request_is_refused_before_the_client(tmp_path):
    session, fake, _c = _session(tmp_path)
    await session.start()
    fake.calls.clear()
    read_ack = functions.messages.ReadHistoryRequest(types.InputPeerEmpty(), 0)
    for request, operation in ((read_ack, "mcp.retrieval"), (_history(), "admin.discover")):
        with pytest.raises(GatewayError) as exc:
            await session._call_reviewed(
                request,
                operation=operation,
                client_ref="c",
                deadline=Deadline(5),
                budget=WorkBudget(),
            )
        assert exc.value.code == "INTERNAL_ERROR"
    assert fake.calls == []  # neither reached the client


async def test_a_revoked_session_latches_and_refuses_further_calls(tmp_path):
    fake = FakeClient({"messages.GetHistoryRequest": errors.AuthKeyUnregisteredError(request=None)})
    session, _fake, _c = _session(tmp_path, client=fake)
    await session.start()
    for _ in range(2):
        with pytest.raises(GatewayError) as exc:
            await session._call_reviewed(
                _history(),
                operation="mcp.retrieval",
                client_ref="c",
                deadline=Deadline(5),
                budget=WorkBudget(),
            )
        assert exc.value.code == "SESSION_REVOKED"
    assert fake.calls.count("messages.GetHistoryRequest") == 1


async def test_a_slow_request_hits_the_deadline(tmp_path):
    fake = FakeClient()
    session, _f, _c = _session(tmp_path, client=fake)
    await session.start()

    async def hang(request):
        await asyncio.sleep(5)

    fake.__class__ = type("Slow", (FakeClient,), {"__call__": lambda self, r: hang(r)})
    with pytest.raises(GatewayError) as exc:
        await session._call_reviewed(
            _history(),
            operation="mcp.retrieval",
            client_ref="c",
            deadline=Deadline(0.1),
            budget=WorkBudget(),
        )
    assert exc.value.code == "DEADLINE_EXCEEDED"


async def test_the_work_budget_is_spent_per_request(tmp_path):
    session, _f, _c = _session(tmp_path)
    await session.start()
    budget = WorkBudget(max_rpcs=1)
    kw = {
        "operation": "mcp.retrieval",
        "client_ref": "c",
        "deadline": Deadline(5),
        "budget": budget,
    }
    await session._call_reviewed(_history(), **kw)
    with pytest.raises(GatewayError) as exc:
        await session._call_reviewed(_history(), **kw)
    assert exc.value.code == "WORK_BUDGET_EXCEEDED"


async def test_input_peers_come_only_from_the_cache(tmp_path):
    session, fake, _c = _session(tmp_path)
    await session.start()
    fake.calls.clear()
    fake.session.remember(types.User(id=42, access_hash=9, first_name="Ali"))
    assert isinstance(session.input_peer("user", 42), types.InputPeerUser)
    with pytest.raises(GatewayError) as exc:
        session.input_peer("user", 43)
    assert exc.value.code == "NOT_ACCESSIBLE"
    assert fake.calls == []  # a cache miss never becomes a network lookup


async def test_login_is_raw_reviewed_requests_and_never_resends(tmp_path):
    fake = FakeClient(authorized=False)
    session, _f, _c = _session(tmp_path, client=fake)
    await session.start()
    await session.send_code(PHONE, Deadline(5))
    await session.send_code(PHONE, Deadline(5))  # the operator re-runs "start"
    assert await session.sign_in_code(PHONE, "22222", Deadline(5)) == "authorized"
    assert await session.me(Deadline(5)) == 4242
    auth = [c for c in fake.calls if not c.startswith("updates.")]
    assert auth == [
        "auth.SendCodeRequest",
        "auth.SendCodeRequest",
        "auth.SignInRequest",
        "users.GetUsersRequest",
    ]
    assert "auth.ResendCodeRequest" not in fake.calls
    assert "updates.GetDifferenceRequest" not in fake.calls  # no content pulled at login
    assert session.readiness() is None


async def test_two_factor_login_runs_srp_offline(tmp_path):
    fake = FakeClient(authorized=False)
    session, _f, _c = _session(tmp_path, client=fake)
    await session.start()
    await session.send_code(PHONE, Deadline(5))
    assert await session.sign_in_code(PHONE, "needs-2fa", Deadline(5)) == "password_needed"
    await session.sign_in_password("hunter2", Deadline(5))
    assert fake.calls[-2:] == ["account.GetPasswordRequest", "auth.CheckPasswordRequest"]
    assert session.readiness() is None


async def test_auth_restart_is_one_request_and_no_retry(tmp_path):
    fake = FakeClient(
        {"auth.SendCodeRequest": errors.AuthRestartError(request=None)}, authorized=False
    )
    session, _f, _c = _session(tmp_path, client=fake)
    await session.start()
    with pytest.raises(GatewayError) as exc:
        await session.send_code(PHONE, Deadline(5))
    assert exc.value.code == "TELEGRAM_UNAVAILABLE"
    assert fake.calls.count("auth.SendCodeRequest") == 1


async def test_phone_migrate_is_one_explicit_budgeted_switch(tmp_path):
    fake = FakeClient(
        {"auth.SendCodeRequest": [errors.PhoneMigrateError(request=None, capture=4)]},
        authorized=False,
    )
    session, _f, _c = _session(tmp_path, client=fake)
    await session.start()
    await session.send_code(PHONE, Deadline(5))
    assert [c for c in fake.calls if not c.startswith("updates.")] == [
        "auth.SendCodeRequest",
        "switch_dc:4",
        "auth.SendCodeRequest",
    ]


async def test_logout_local_never_logs_out_and_removes_only_session_files(tmp_path):
    session, fake, _c = _session(tmp_path)
    await session.start()
    (tmp_path / "session" / "primary.session").write_bytes(b"x")
    await session.logout_local()
    assert fake.logged_out is False
    assert not (tmp_path / "session" / "primary.session").exists()


async def test_logout_local_forgets_the_in_memory_client(tmp_path):
    """The auth key lives in the client object too; dropping only files is no logout."""
    built = []

    def factory(*_a, **_k):
        built.append(FakeClient(authorized=len(built) == 0))
        return built[-1]

    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "session"),
        api_hash="0" * 32,
        client_factory=factory,
    )
    await session.start()
    assert session.readiness() is None
    await session.logout_local()
    assert session.readiness() == "AUTH_REQUIRED"
    assert await session.is_authorized(Deadline(5)) is False  # a fresh client, not the old key
    assert len(built) == 2


async def test_an_unreachable_telegram_is_a_state_not_a_crash(tmp_path):
    class Down(FakeClient):
        async def connect(self):
            raise ConnectionError("unreachable")

    session, _fake, _c = _session(tmp_path, client=Down())
    await session.start()  # the lock is held; the link is not
    assert session.readiness() == "TELEGRAM_UNAVAILABLE"
    with pytest.raises(GatewayError) as exc:
        await session.send_code(PHONE, Deadline(1))
    assert exc.value.code == "TELEGRAM_UNAVAILABLE"


async def test_an_unauthorised_session_is_not_ready(tmp_path):
    session, _fake, _c = _session(tmp_path, client=FakeClient(authorized=False))
    await session.start()
    assert session.readiness() == "AUTH_REQUIRED"
