"""Command completeness by name, not count (design D9, §5.4)."""

import secrets
import time

import pytest

from comms.transports.telegram.ipc.admin import ADMIN_COMMANDS, PRESENCE_GATED, AdminRouter
from comms.transports.telegram.keys.store import provision_missing, set_store_dir
from comms.transports.telegram.runtime.composition import admin_handlers
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession
from tests.authority_fixtures import seed_authority_rows
from tests.telegram.fake_client import FakeClient
from tests.unit.test_dialogs_and_discovery import _dialogs_result

CLI_ONLY = {"doctor", "serve"}
LATER_IN_PHASE_5 = {"auth revoke-this-session", "project drift", "policy export", "policy import"}
# Named, each with its reason (design D9):
#  tunnel rotate-binding -- Phase 6 (the CLI `rotate` already covers the pin)
#  release verify        -- Phase 7
#  consent approve       -- design rev 2 G7: spec line 927 makes it a MAY, a
#     generic admin prompt would approve a disclosure blind, and a pending
#     challenge exists only while its own prompt is in flight
#     (prompter.py:146-150), so there is nothing orphaned to approve.
DEFERRED = {"tunnel rotate-binding", "release verify", "consent approve"}


class _Broker:
    def pending_count(self):
        return 0

    def invalidate_where(self, predicate):
        return 0


class _Prompter:
    connected = False


@pytest.fixture
async def handlers(tmp_path):
    store = tmp_path / "keys"
    provision_missing(store, phases=(2, 3))
    set_store_dir(store)
    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    (tmp_path / "anchor").mkdir(mode=0o700)
    fake = FakeClient({"messages.GetDialogsRequest": _dialogs_result()})
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    return admin_handlers(
        conn,
        key_dir=store,
        anchor_path=tmp_path / "anchor" / "anchor.json",
        telegram=session,
        broker=_Broker(),
        prompter=_Prompter(),
        seeds=lambda ref: None,
        runtime_id=secrets.token_bytes(16),
        clock=time.time,
    )


async def test_exactly_the_named_commands_lack_a_handler(handlers):
    missing = set(ADMIN_COMMANDS) - set(handlers)
    assert missing == CLI_ONLY | LATER_IN_PHASE_5 | DEFERRED


async def test_every_missing_admin_command_answers_not_available(handlers):
    router = AdminRouter(handlers, presence_verifier=lambda proof: True)
    for command in sorted(LATER_IN_PHASE_5 | DEFERRED):
        response = await router.adispatch({"cmd": command, "args": {"presence": {}}})
        assert response["code"] == "NOT_AVAILABLE_IN_PHASE", command


def test_the_presence_set_is_unchanged_in_5a():
    from tests.unit.test_admin_handlers import MUTATING

    assert set(PRESENCE_GATED) == MUTATING  # by name, not count
