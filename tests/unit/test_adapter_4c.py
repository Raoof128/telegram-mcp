"""4c adapter primitives: by-id anchors, topic replies, per-peer search."""

from datetime import UTC, datetime

import pytest
from telethon.tl import functions, types

from comms.transports.telegram.telegram.deadline import Deadline, WorkBudget
from comms.transports.telegram.telegram.errors import GatewayError
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession
from tests.telegram.fake_client import FakeClient

WHEN = datetime(2026, 9, 21, 8, 0, 0, tzinfo=UTC)
ALI = types.User(id=100, access_hash=1, first_name="Ali")
FORUM = types.Channel(
    id=8,
    title="Forum",
    photo=types.ChatPhotoEmpty(),
    date=WHEN,
    megagroup=True,
    forum=True,
    access_hash=4,
)


def _kw():
    """Fresh per call: a module-level Deadline expires, and a shared budget drains."""
    return {"client_ref": "c", "deadline": Deadline(5), "budget": WorkBudget()}


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
    fake.calls.clear()
    return session, fake


def _msgs(*messages, chats=(), users=()):
    return types.messages.Messages(
        messages=list(messages), topics=[], chats=list(chats), users=list(users)
    )


async def test_anchor_by_id_classifies_topics_and_the_forum(tmp_path):
    seen = []

    def by_id(request):
        seen.append(request)
        return types.messages.ChannelMessages(
            pts=1,
            count=3,
            topics=[],
            chats=[FORUM],
            users=[],
            messages=[
                types.Message(
                    id=50,
                    peer_id=types.PeerChannel(8),
                    date=WHEN,
                    message="in topic",
                    reply_to=types.MessageReplyHeader(
                        forum_topic=True, reply_to_msg_id=40, reply_to_top_id=12
                    ),
                ),
                types.MessageService(
                    id=12,
                    peer_id=types.PeerChannel(8),
                    date=WHEN,
                    action=types.MessageActionTopicCreate(title="T", icon_color=0),
                ),
                types.MessageEmpty(id=51, peer_id=types.PeerChannel(8)),
            ],
        )

    session, fake = await _session(tmp_path, {"channels.GetMessagesRequest": by_id}, FORUM)
    views, is_forum = await session.fetch_by_ids("channel", 8, [50, 12, 51], **_kw())
    assert is_forum is True and fake.calls == ["channels.GetMessagesRequest"]
    reply, root = views  # the deleted entry is gone
    assert (reply.reply_to_top_id, reply.forum_topic, reply.topic_root) == (12, True, False)
    assert root.topic_root is True
    assert [m.id for m in seen[0].id] == [50, 12, 51]


async def test_a_dm_anchor_uses_messages_get_messages(tmp_path):
    session, fake = await _session(
        tmp_path,
        {
            "messages.GetMessagesRequest": _msgs(
                types.Message(id=5, peer_id=types.PeerUser(100), date=WHEN, message="hi"),
                users=[ALI],
            )
        },
        ALI,
    )
    views, is_forum = await session.fetch_by_ids("user", 100, [5], **_kw())
    assert [v.message_id for v in views] == [5] and is_forum is False
    assert fake.calls == ["messages.GetMessagesRequest"]


async def test_replies_stay_inside_the_topic_request(tmp_path):
    seen = []
    session, _fake = await _session(
        tmp_path, {"messages.GetRepliesRequest": lambda r: seen.append(r) or _msgs()}, FORUM
    )
    await session.fetch_replies(
        "channel", 8, 12, offset_id=50, add_offset=-3, limit=3, min_id=50, max_id=0, **_kw()
    )
    request = seen[0]
    assert (
        request.msg_id,
        request.offset_id,
        request.add_offset,
        request.limit,
        request.min_id,
    ) == (12, 50, -3, 3, 50)


async def test_search_passes_absent_bounds_as_zero_and_reports_completeness(tmp_path):
    seen = []

    def search(request):
        seen.append(request)
        return types.messages.MessagesSlice(
            count=10,
            inexact=True,
            topics=[],
            chats=[],
            users=[ALI],
            messages=[
                types.Message(id=i, peer_id=types.PeerUser(100), date=WHEN, message="needle")
                for i in (9, 8)
                if not request.offset_id or i < request.offset_id  # Telegram: nothing below 8
            ],
        )

    session, _fake = await _session(tmp_path, {"messages.SearchRequest": search}, ALI)
    page = await session.search_peer(
        "user", 100, "needle", min_date=None, max_date=None, offset_id=0, limit=2, **_kw()
    )
    assert seen[0].min_date is None and seen[0].max_date is None  # Telethon sends 0: unbounded
    assert isinstance(seen[0].filter, types.InputMessagesFilterEmpty) and seen[0].q == "needle"
    assert (page.exhausted, page.inexact, page.next_offset) == (False, True, 8)
    last = await session.search_peer(
        "user", 100, "needle", min_date=None, max_date=None, offset_id=8, limit=5, **_kw()
    )
    assert last.exhausted is True and last.next_offset is None


async def test_global_search_is_never_sendable(tmp_path):
    session, fake = await _session(tmp_path, {})
    request = functions.messages.SearchGlobalRequest(
        q="x",
        filter=types.InputMessagesFilterEmpty(),
        min_date=None,
        max_date=None,
        offset_rate=0,
        offset_peer=types.InputPeerEmpty(),
        offset_id=0,
        limit=1,
    )
    with pytest.raises(GatewayError) as exc:
        await session._call_reviewed(request, operation="mcp.retrieval", **_kw())
    assert exc.value.code == "INTERNAL_ERROR" and fake.calls == []


async def test_messages_from_another_chat_are_never_attributed(tmp_path):
    session, _fake = await _session(
        tmp_path,
        {
            "messages.SearchRequest": _msgs(
                types.Message(id=1, peer_id=types.PeerUser(100), date=WHEN, message="mine"),
                types.Message(
                    id=2, peer_id=types.PeerUser(555), date=WHEN, message="someone else's"
                ),
            )
        },
        ALI,
    )
    page = await session.search_peer(
        "user", 100, "x", min_date=None, max_date=None, offset_id=0, limit=5, **_kw()
    )
    assert [v.text for v in page.views] == ["mine"]


async def test_dialogs_nobody_asked_for_are_not_returned(tmp_path):
    other = types.User(id=555, access_hash=2, first_name="Zed")
    dialogs = types.messages.PeerDialogs(
        dialogs=[
            types.Dialog(
                peer=types.PeerUser(u.id),
                top_message=1,
                read_inbox_max_id=0,
                read_outbox_max_id=0,
                unread_count=0,
                unread_mentions_count=0,
                unread_reactions_count=0,
                unread_poll_votes_count=0,
                notify_settings=types.PeerNotifySettings(),
            )
            for u in (ALI, other)
        ],
        messages=[],
        chats=[],
        users=[ALI, other],
        state=types.updates.State(pts=1, qts=0, date=WHEN, seq=0, unread_count=0),
    )
    session, _fake = await _session(tmp_path, {"messages.GetPeerDialogsRequest": dialogs}, ALI)
    got = await session.peer_dialogs([("user", 100), ("user", 555)], **_kw())  # 555 is not cached
    assert set(got) == {"user:100"}


def _slice(*messages, count=99, inexact=False):
    return types.messages.MessagesSlice(
        count=count, inexact=inexact, messages=list(messages), topics=[], chats=[], users=[ALI]
    )


def _dm(i, peer=100):
    return types.Message(id=i, peer_id=types.PeerUser(peer), date=WHEN, message=f"m{i}")


async def _search(tmp_path, response, *, limit):
    session, _fake = await _session(tmp_path, {"messages.SearchRequest": response}, ALI)
    return await session.search_peer(
        "user", 100, "x", min_date=None, max_date=None, offset_id=0, limit=limit, **_kw()
    )


@pytest.mark.parametrize(
    "response,limit,exhausted,next_offset",
    [
        # Telethon 1.45.0 client/messages.py:213-225: a short slice is NOT the end
        # (channels withhold messages); only these three signals are.
        (_slice(_dm(90), _dm(80)), 5, False, 80),
        (_msgs(_dm(90), _dm(80), _dm(70)), 3, True, None),  # not a slice: everything
        (_slice(_dm(3), _dm(2)), 5, True, None),  # the highest id is within the limit
        (_slice(), 5, True, None),  # empty
        (_slice(_dm(90), _dm(80), _dm(70)), 3, False, 70),
    ],
)
async def test_the_last_page_is_telegrams_own_signal_not_a_short_page(
    tmp_path, response, limit, exhausted, next_offset
):
    page = await _search(tmp_path, response, limit=limit)
    assert (page.exhausted, page.next_offset) == (exhausted, next_offset)


async def test_a_foreign_message_never_steers_the_offset_and_is_never_complete(tmp_path):
    page = await _search(tmp_path, _slice(_dm(90), _dm(80), _dm(5, peer=555)), limit=3)
    assert [v.message_id for v in page.views] == [90, 80]
    assert page.next_offset == 80 and page.exhausted is False and page.inexact is True
    assert page.examined == 3  # the dropped entry was still examined (spec §13.2's 500)


async def test_history_keeps_paging_past_a_short_slice(tmp_path):
    session, _fake = await _session(
        tmp_path, {"messages.GetHistoryRequest": _slice(_dm(90), _dm(80))}, ALI
    )
    views, below = await session.fetch_history("user", 100, offset_id=0, max_id=0, limit=5, **_kw())
    assert [v.message_id for v in views] == [90, 80] and below == 80
    session, _fake = await _session(
        tmp_path / "full", {"messages.GetHistoryRequest": _msgs(_dm(90), _dm(80))}, ALI
    )
    _views, below = await session.fetch_history(
        "user", 100, offset_id=0, max_id=0, limit=2, **_kw()
    )
    assert below is None  # a full non-slice page is still everything
