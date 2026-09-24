"""comms v0.3 Task B15: Telegram revocation errors map durably (design §B.6)."""

import pytest
from telethon import errors
from telethon.tl import functions

from comms.transports.telegram.telegram.deadline import Deadline, WorkBudget
from comms.transports.telegram.telegram.errors import GatewayError
from comms.transports.telegram.telegram.telethon_adapter import (
    TelegramConfig,
    TelethonSession,
    translate,
)
from tests.telegram.fake_client import FakeClient

REVOKED = [
    errors.AuthKeyUnregisteredError,
    errors.SessionRevokedError,
    errors.SessionExpiredError,
    errors.AuthKeyDuplicatedError,
]
DEACTIVATED = [errors.UserDeactivatedError, errors.UserDeactivatedBanError]


@pytest.mark.parametrize("error", REVOKED)
def test_auth_key_unregistered_is_session_revoked(error):
    assert translate(error(request=None)).code == "SESSION_REVOKED"


@pytest.mark.parametrize("error", DEACTIVATED)
def test_user_deactivated_is_account_unavailable(error):
    assert translate(error(request=None)).code == "ACCOUNT_UNAVAILABLE"


def test_auth_key_not_found_is_telegram_unavailable_not_durable():
    assert translate(errors.AuthKeyNotFound()).code == "TELEGRAM_UNAVAILABLE"


@pytest.fixture
async def session(tmp_path):
    fake = FakeClient()
    s = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await s.start()
    return s, fake


async def _read(s):
    return await s._call_reviewed(
        functions.messages.GetPeerDialogsRequest(peers=[]),
        operation="mcp.retrieval",
        client_ref="c",
        deadline=Deadline(5),
        budget=WorkBudget(max_rpcs=1),
    )


@pytest.mark.parametrize(
    "error, code, durable",
    [
        (errors.AuthKeyUnregisteredError(request=None), "SESSION_REVOKED", "SESSION_REVOKED"),
        (
            errors.UserDeactivatedBanError(request=None),
            "ACCOUNT_UNAVAILABLE",
            "ACCOUNT_UNAVAILABLE",
        ),
        (errors.AuthKeyNotFound(), "TELEGRAM_UNAVAILABLE", None),
    ],
)
async def test_a_read_failure_latches_only_the_durable_states(session, error, code, durable):
    s, fake = session
    fake.script["messages.GetPeerDialogsRequest"] = error
    with pytest.raises(GatewayError) as raised:
        await _read(s)
    assert raised.value.code == code
    assert s.readiness() == durable


async def test_a_new_sign_in_clears_the_account_unavailable_latch(session):
    s, fake = session
    fake.script["messages.GetPeerDialogsRequest"] = errors.UserDeactivatedError(request=None)
    with pytest.raises(GatewayError):
        await _read(s)
    s._signed_in()
    assert s.readiness() is None
