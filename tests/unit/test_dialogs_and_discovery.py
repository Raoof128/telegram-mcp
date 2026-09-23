from datetime import UTC, datetime

import pytest
from telethon.tl import types

from telegram_mcp.telegram.deadline import Deadline, WorkBudget
from telegram_mcp.telegram.discovery import DiscoveryStore
from telegram_mcp.telegram.telethon_adapter import TelegramConfig, TelethonSession
from tests.telegram.fake_client import FakeClient

WHEN = datetime(2026, 9, 21, 0, 15, 34, tzinfo=UTC)


def _dialogs_result(n_users=2, *, archived_first=False):
    users = [
        types.User(id=100 + i, access_hash=1, first_name=f"U{i}", username=f"u{i}")
        for i in range(n_users)
    ]
    channel = types.Channel(
        id=7, title="News", photo=types.ChatPhotoEmpty(), date=WHEN, broadcast=True, access_hash=3
    )
    group = types.Channel(
        id=8, title="Team", photo=types.ChatPhotoEmpty(), date=WHEN, megagroup=True, access_hash=4
    )
    peers = [types.PeerUser(u.id) for u in users] + [types.PeerChannel(7), types.PeerChannel(8)]
    dialogs = []
    messages = []
    for i, peer in enumerate(peers):
        dialogs.append(
            types.Dialog(
                peer=peer,
                top_message=10 + i,
                read_inbox_max_id=0,
                read_outbox_max_id=5,
                unread_count=i,
                unread_mentions_count=0,
                unread_reactions_count=0,
                unread_poll_votes_count=0,
                notify_settings=types.PeerNotifySettings(
                    mute_until=datetime(2099, 1, 1, tzinfo=UTC) if i == 1 else None
                ),
                folder_id=1 if (archived_first and i == 0) else None,
            )
        )
        messages.append(types.Message(id=10 + i, peer_id=peer, date=WHEN, message="x"))
    return types.messages.Dialogs(
        dialogs=dialogs, messages=messages, chats=[channel, group], users=users
    )


async def _session(tmp_path, script):
    fake = FakeClient(script)
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    return session, fake


async def test_scan_maps_every_chat_kind(tmp_path):
    session, _fake = await _session(
        tmp_path, {"messages.GetDialogsRequest": _dialogs_result(archived_first=True)}
    )
    views, complete = await session.scan_dialogs(
        client_ref="c", deadline=Deadline(5), budget=WorkBudget()
    )
    by_id = {(v.peer_type, v.peer_id): v for v in views}
    assert complete is True
    assert by_id[("user", 100)].chat_type == "private" and by_id[("user", 100)].is_archived is True
    assert by_id[("user", 101)].is_muted is True
    assert by_id[("channel", 7)].chat_type == "channel"
    assert by_id[("channel", 8)].chat_type == "supergroup"
    assert by_id[("user", 100)].last_message_at == "2026-09-21T00:15:34Z"


async def test_discovery_handles_expire_and_die_with_the_policy_epoch(tmp_path):
    now = [0.0]
    store = DiscoveryStore(clock=lambda: now[0])
    session, _fake = await _session(tmp_path, {"messages.GetDialogsRequest": _dialogs_result()})
    views, _ = await session.scan_dialogs(client_ref="c", deadline=Deadline(5), budget=WorkBudget())
    listed = store.new_snapshot(views, policy_epoch=1)
    handle = listed[0]["handle"]
    assert handle.startswith("tgl_") and "100" not in handle
    with pytest.raises(ValueError):
        store.take(handle, policy_epoch=2)  # epoch moved
    listed = store.new_snapshot(views, policy_epoch=1)
    handle = listed[0]["handle"]
    now[0] = 301.0
    with pytest.raises(ValueError):
        store.take(handle, policy_epoch=1)  # 5-minute TTL


async def test_peer_dialogs_skip_peers_missing_from_the_cache(tmp_path):
    result = _dialogs_result()
    session, fake = await _session(
        tmp_path,
        {
            "messages.GetPeerDialogsRequest": types.messages.PeerDialogs(
                dialogs=result.dialogs[:1],
                messages=result.messages[:1],
                chats=[],
                users=result.users[:1],
                state=types.updates.State(pts=1, qts=0, date=WHEN, seq=0, unread_count=0),
            )
        },
    )
    fake.session.remember(result.users[0])
    got = await session.peer_dialogs(
        [("user", 100), ("user", 999)], client_ref="c", deadline=Deadline(5), budget=WorkBudget()
    )
    assert set(got) == {"user:100"}
