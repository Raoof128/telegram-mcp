"""comms v0.3 Task B16: recovery by auth login, and the explicit --new-account switch (design §B.6)."""

import ast
from pathlib import Path

import pytest

from comms.transports.telegram.ipc.handlers.auth import auth_handlers
from comms.transports.telegram.storage.authority_view import load_security
from comms.transports.telegram.storage.identity import active_account
from comms.transports.telegram.storage.settings import get_setting
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW
from tests.telegram.fake_client import FakeClient

ROOT = Path(__file__).resolve().parents[2]
PHONE = "9996621234"


@pytest.fixture
async def env(tmp_path):
    world = comms_world(tmp_path)
    legacy = world["port"].conn
    legacy.execute(
        "INSERT INTO principals (principal_ref, principal_key, auth_mode, created_at)"
        " VALUES (?, 'k', 'local', '2026-09-24T00:00:00Z')",
        ("prn_" + "a" * 26,),
    )
    legacy.commit()
    fake = FakeClient(authorized=False)
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    handlers = auth_handlers(legacy, session, writer=world["writer"], now=lambda: NOW)
    world.update(legacy=legacy, fake=fake, session=session, h=handlers)
    return world


async def _login(env, **extra):
    started = await env["h"]["auth login"]({"step": "start", "phone": PHONE, **extra})
    return await env["h"]["auth login"](
        {"step": "code", "login": started["login"], "code": "22222"}
    )


def _account_count(conn):
    return conn.execute("SELECT count(*) FROM accounts").fetchone()[0]


async def test_login_from_revoked_bumps_session_generation_and_epoch(env):
    first = await _login(env)
    await env["h"]["auth revoke-this-session"]({})
    assert get_setting(env["legacy"], "telegram.session_state") == "logged_out"
    epoch, generation = (
        load_security(env["legacy"])[0],
        get_setting(env["legacy"], "telegram.session_generation"),
    )
    env["fake"].authorized = False
    again = await _login(env)
    assert again["account_ref"] == first["account_ref"]
    assert load_security(env["legacy"])[0] == epoch + 1
    assert get_setting(env["legacy"], "telegram.session_generation") == generation + 1
    assert get_setting(env["legacy"], "telegram.session_state") == "active"
    assert env["session"].readiness() is None


async def test_different_user_refused_without_new_account(env):
    await _login(env)
    env["fake"].me_id = 777
    env["fake"].authorized = False
    with pytest.raises(ValueError, match="new_account"):
        await _login(env)
    assert _account_count(env["legacy"]) == 1
    assert not list(
        (env["tmp"] / "s").glob("primary.session*")
    )  # the other account's session is not kept


async def test_new_account_switches_pointer_keeps_old_rows(env):
    first = await _login(env)
    env["fake"].me_id = 777
    env["fake"].authorized = False
    second = await _login(env, new_account=True)
    assert second["account_ref"] != first["account_ref"]
    assert _account_count(env["legacy"]) == 2  # the old account's rows stay for history
    ref = (
        env["legacy"]
        .execute("SELECT account_ref FROM accounts WHERE id = ?", (active_account(env["legacy"]),))
        .fetchone()[0]
    )
    assert ref == second["account_ref"]


def test_no_unscoped_accounts_query_outside_identity_module():
    allowed = {
        ROOT / "src/comms/transports/telegram/storage/identity.py",
        ROOT / "src/comms/transports/telegram/storage/migrations.py",
    }
    for path in (ROOT / "src").rglob("*.py"):
        if path in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "FROM accounts" in node.value
            ):
                assert "WHERE" in node.value, (path, node.value)
