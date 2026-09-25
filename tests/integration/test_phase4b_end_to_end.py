"""The four tools through ingress, coordinator and a fake Telegram."""

import asyncio
import secrets
from pathlib import Path

import uvicorn
from telethon.tl import types

from comms.transports.telegram.keys.store import provision_lease_seed, provision_missing
from comms.transports.telegram.runtime.legacy_composition import build_runtime
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession
from tests.authority_fixtures import PROJECT_REF, seed_authority_rows, seed_project_world
from tests.integration.test_phase4a_end_to_end import CODEX, RUNTIME, _free_port, call
from tests.integration.test_telegram_reads import (
    ALI,
    BOB,
    DEFAULT_DIALOGS,
    NEWS,
    TEAM,
    WHEN,
    _peer_dialogs,
)
from tests.telegram.fake_client import FakeClient

MARKER = "leak-" + secrets.token_hex(16)


async def _world(tmp_path, monkeypatch, *, with_telegram=True, authorized=True):
    monkeypatch.chdir(tmp_path)
    keys = tmp_path / "keys"
    provision_missing(keys, phases=(2, 3))
    seeds = {CODEX: provision_lease_seed(keys, CODEX)}
    conn = open_db(tmp_path / "meta.db")
    seed_authority_rows(conn)
    refs = seed_project_world(conn)
    (tmp_path / "anchor").mkdir(mode=0o700)
    session = None
    fake = FakeClient(
        {
            "messages.GetPeerDialogsRequest": _peer_dialogs(DEFAULT_DIALOGS),
            "messages.GetHistoryRequest": types.messages.Messages(
                messages=[
                    types.Message(id=1, peer_id=types.PeerUser(100), date=WHEN, message=MARKER)
                ],
                topics=[],
                chats=[],
                users=[ALI],
            ),
        },
        authorized=authorized,
    )
    for entity in (ALI, BOB, NEWS, TEAM):
        fake.session.remember(entity)
    if with_telegram:
        session = TelethonSession(
            TelegramConfig(api_id=1, session_dir=tmp_path / "tg"),
            api_hash="0" * 32,
            client_factory=lambda *a, **k: fake,
        )
        await session.start()
        fake.calls.clear()  # the start-up authorisation probe is not part of any tool call
    port = _free_port()
    services = build_runtime(
        conn,
        key_dir=keys,
        anchor_path=tmp_path / "anchor" / "anchor.json",
        runtime_id=RUNTIME,
        port=port,
        telegram=session,
    )
    tasks = []
    server = uvicorn.Server(
        uvicorn.Config(services.ingress_app, host="127.0.0.1", port=port, log_level="warning")
    )
    tasks.append(asyncio.create_task(server.serve()))
    while not server.started:
        await asyncio.sleep(0.02)
    return {
        "seeds": seeds,
        "port": port,
        "refs": refs,
        "conn": conn,
        "server": server,
        "tasks": tasks,
        "fake": fake,
    }


async def _close(world):
    world["server"].should_exit = True
    await asyncio.sleep(0.2)
    for task in world["tasks"]:
        task.cancel()


async def test_list_chats_then_get_messages_with_receipts_and_no_body_at_rest(
    tmp_path, monkeypatch
):
    world = await _world(tmp_path, monkeypatch)
    try:
        chats = await call(world, CODEX, "telegram_list_chats", {"project_ref": PROJECT_REF})
        assert chats["ok"] is True, chats
        assert chats["meta"]["disclosure"]["receipt_ref"].startswith("tdr_")
        body = await call(
            world,
            CODEX,
            "telegram_get_messages",
            {"project_ref": PROJECT_REF, "peer_ref": world["refs"]["user:100"]},
        )
        assert body["ok"] is True, body
        assert body["data"]["messages"][0]["text"] == MARKER
        assert body["meta"]["content_trust"] == "untrusted_external_content"
        world["conn"].commit()
        for path in Path(tmp_path).glob("meta.db*"):
            assert MARKER.encode() not in path.read_bytes(), path  # §19.4: no body at rest
        assert "messages.ReadHistoryRequest" not in world["fake"].calls
    finally:
        await _close(world)


async def test_an_unauthorised_session_refuses_before_retrieval(tmp_path, monkeypatch):
    world = await _world(tmp_path, monkeypatch, authorized=False)
    try:
        body = await call(world, CODEX, "telegram_list_chats", {"project_ref": PROJECT_REF})
        assert body["error"]["code"] == "AUTH_REQUIRED"
    finally:
        await _close(world)


async def test_without_a_session_the_telegram_tools_refuse_before_retrieval(tmp_path, monkeypatch):
    world = await _world(tmp_path, monkeypatch, with_telegram=False)
    try:
        body = await call(world, CODEX, "telegram_list_chats", {"project_ref": PROJECT_REF})
        assert body["error"]["code"] == "AUTH_REQUIRED"
    finally:
        await _close(world)
