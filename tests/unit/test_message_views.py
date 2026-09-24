from datetime import UTC, datetime

import pytest
from telethon import errors
from telethon.tl import types

from comms.transports.telegram.telegram.deadline import Deadline, WorkBudget
from comms.transports.telegram.telegram.errors import GatewayError
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession
from tests.telegram.fake_client import FakeClient

WHEN = datetime(2026, 9, 21, 8, 0, 0, tzinfo=UTC)
ALI = types.User(id=100, access_hash=1, first_name="Ali")
ZED = types.User(id=555, access_hash=2, first_name="Zed")
NEWS = types.Channel(
    id=7, title="News", photo=types.ChatPhotoEmpty(), date=WHEN, broadcast=True, access_hash=3
)
TEAM = types.Channel(
    id=8, title="Team", photo=types.ChatPhotoEmpty(), date=WHEN, megagroup=True, access_hash=4
)


def _history(messages, users=(), chats=()):
    return types.messages.Messages(
        messages=list(messages), topics=[], chats=list(chats), users=list(users)
    )


async def _session(tmp_path, script, *cached):
    fake = FakeClient(script)
    for entity in cached:
        fake.session.remember(entity)
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    fake.calls.clear()  # start() probes authorisation once; tests count what follows
    return session, fake


async def _fetch(session, peer_type, peer_id, limit=20):
    views, _below = await session.fetch_history(
        peer_type,
        peer_id,
        offset_id=0,
        max_id=0,
        limit=limit,
        client_ref="c",
        deadline=Deadline(5),
        budget=WorkBudget(),
    )
    return views


async def test_a_full_page_reports_the_oldest_raw_id_even_when_deleted(tmp_path):
    # A page of a longer history: Telegram sends a slice (plain messages means "everything").
    result = types.messages.MessagesSlice(
        count=99,
        messages=[
            types.Message(id=10, peer_id=types.PeerUser(100), date=WHEN, message="a"),
            types.MessageEmpty(id=9, peer_id=types.PeerUser(100)),
        ],
        topics=[],
        chats=[],
        users=[ALI],
    )
    session, _fake = await _session(tmp_path, {"messages.GetHistoryRequest": result}, ALI)
    views, below = await session.fetch_history(
        "user",
        100,
        offset_id=0,
        max_id=0,
        limit=2,
        client_ref="c",
        deadline=Deadline(5),
        budget=WorkBudget(),
    )
    assert [v.message_id for v in views] == [10] and below == 9


async def test_dm_directions_and_deleted_entries(tmp_path):
    result = _history(
        [
            types.Message(id=3, peer_id=types.PeerUser(100), date=WHEN, message="hi", out=True),
            types.MessageEmpty(id=2, peer_id=types.PeerUser(100)),
            types.Message(
                id=1,
                peer_id=types.PeerUser(100),
                date=WHEN,
                message="hello",
                edit_date=WHEN,
                edit_hide=True,
            ),
        ],
        users=[ALI],
    )
    session, _fake = await _session(tmp_path, {"messages.GetHistoryRequest": result}, ALI)
    views = await _fetch(session, "user", 100)
    assert [v.message_id for v in views] == [3, 1]
    out, incoming = views
    assert (out.sender_kind, out.sender, out.outgoing) == ("user", None, True)
    assert (incoming.sender, incoming.sender_display_name) == (("user", 100), "Ali")
    assert incoming.edited is False and incoming.sent_at == "2026-09-21T08:00:00Z"


async def test_group_senders_replies_media_and_service(tmp_path):
    result = _history(
        [
            types.Message(
                id=9,
                peer_id=types.PeerChannel(8),
                date=WHEN,
                message="x",
                from_id=types.PeerUser(555),
                reply_to=types.MessageReplyHeader(reply_to_msg_id=4, forum_topic=True),
            ),
            types.Message(
                id=8,
                peer_id=types.PeerChannel(8),
                date=WHEN,
                message="y",
                from_id=types.PeerChannel(8),
                post_author="Admin",
            ),
            types.MessageService(
                id=7,
                peer_id=types.PeerChannel(8),
                date=WHEN,
                action=types.MessageActionPinMessage(),
            ),
            types.Message(
                id=6,
                peer_id=types.PeerChannel(8),
                date=WHEN,
                message="",
                from_id=types.PeerUser(555),
                media=types.MessageMediaPhoto(),
            ),
        ],
        users=[ZED],
        chats=[TEAM],
    )
    session, _fake = await _session(tmp_path, {"messages.GetHistoryRequest": result}, TEAM)
    reply, anon, service, photo = await _fetch(session, "channel", 8)
    assert (reply.sender_kind, reply.sender, reply.sender_display_name) == (
        "user",
        ("user", 555),
        "Zed",
    )
    assert (reply.reply_to_id, reply.forum_topic) == (4, True)
    assert (anon.sender_kind, anon.sender, anon.post_author) == ("anonymous_admin", None, "Admin")
    assert (service.sender_kind, service.text, service.sender) == ("service", None, None)
    assert (photo.has_media, photo.media_kind) == (True, "photo")


async def test_a_broadcast_post_is_sent_by_the_channel(tmp_path):
    result = _history(
        [types.Message(id=5, peer_id=types.PeerChannel(7), date=WHEN, message="news", post=True)],
        chats=[NEWS],
    )
    session, _fake = await _session(tmp_path, {"messages.GetHistoryRequest": result}, NEWS)
    (post,) = await _fetch(session, "channel", 7)
    assert (post.sender_kind, post.sender, post.sender_display_name) == (
        "channel",
        ("channel", 7),
        "News",
    )


async def test_cache_miss_is_not_accessible_without_rpc(tmp_path):
    """Review Focus 3."""
    session, fake = await _session(tmp_path, {})
    with pytest.raises(GatewayError) as exc:
        await _fetch(session, "user", 100)
    assert exc.value.code == "NOT_ACCESSIBLE" and fake.calls == []


async def test_flood_wait_on_history_is_flood_wait(tmp_path):
    session, _fake = await _session(
        tmp_path,
        {"messages.GetHistoryRequest": errors.FloodWaitError(request=None, capture=12)},
        ALI,
    )
    with pytest.raises(GatewayError) as exc:
        await _fetch(session, "user", 100)
    assert (exc.value.code, exc.value.retry_after) == ("FLOOD_WAIT", 12)
