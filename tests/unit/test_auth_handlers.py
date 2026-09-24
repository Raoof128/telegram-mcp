import logging

import pytest

from comms.transports.telegram.ipc.handlers.auth import auth_handlers
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.identity import ensure_owner_principal
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession
from tests.telegram.fake_client import FakeClient

KEY = b"\x07" * 32
PHONE = "9996621234"


@pytest.fixture
async def world(tmp_path):
    conn = open_db(tmp_path / "m.db")
    ensure_owner_principal(conn, privacy_key=KEY)
    fake = FakeClient(authorized=False)
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    fake.calls.clear()  # start() probes authorisation once; tests count what follows
    now = [0.0]
    return conn, fake, auth_handlers(conn, session, clock=lambda: now[0]), now


async def test_code_login_creates_the_account(world):
    conn, fake, h, _now = world
    started = await h["auth login"]({"step": "start", "phone": PHONE})
    done = await h["auth login"]({"step": "code", "login": started["login"], "code": "22222"})
    assert done["authorized"] is True and done["account_ref"].startswith("tga_")
    assert conn.execute("SELECT count(*) FROM policy_state").fetchone()[0] == 1
    assert [c for c in fake.calls if c.startswith("auth.")] == [
        "auth.SendCodeRequest",
        "auth.SignInRequest",
    ]
    assert "auth.ResendCodeRequest" not in fake.calls


async def test_password_step_needs_its_own_approval_and_live_handle(world, caplog):
    _conn, _fake, h, now = world
    caplog.set_level(logging.DEBUG)
    started = await h["auth login"]({"step": "start", "phone": PHONE})
    step = await h["auth login"]({"step": "code", "login": started["login"], "code": "needs-2fa"})
    assert step == {"next": "password", "login": started["login"]}
    now[0] = 601.0  # the handle's 10 minutes have passed
    with pytest.raises(ValueError, match="login session"):
        await h["auth login"](
            {"step": "password", "login": started["login"], "password": "hunter2"}
        )
    assert "hunter2" not in caplog.text and PHONE not in caplog.text


async def test_a_handle_cannot_skip_or_repeat_a_step(world):
    _conn, _fake, h, _now = world
    started = await h["auth login"]({"step": "start", "phone": PHONE})
    with pytest.raises(ValueError):
        await h["auth login"]({"step": "password", "login": started["login"], "password": "x"})


async def test_malformed_phone_is_refused_before_any_request(world):
    _conn, fake, h, _now = world
    with pytest.raises(ValueError):
        await h["auth login"]({"step": "start", "phone": "call me"})
    assert fake.calls == []


async def test_status_and_local_logout(world):
    _conn, fake, h, _now = world
    assert (await h["auth status"]({}))["authorized"] is False
    assert (await h["auth logout-local"]({}))["logged_out_locally"] is True
    assert fake.logged_out is False
    assert "auth revoke-this-session" not in h
