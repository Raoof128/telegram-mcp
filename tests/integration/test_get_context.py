"""get_context (spec §20, design §4.1): windows, topics, General, refs, caps."""

import pytest
from telethon import errors
from telethon.tl import types

from comms.transports.telegram.disclosure.coordinator import AuthorityRefusal, RetrievalRefusal
from comms.transports.telegram.storage.refstore import RefStore
from tests.integration.test_telegram_reads import ALI, WHEN, _peer_dialogs, make_reads_world


@pytest.fixture
async def world(tmp_path):
    return await make_reads_world(tmp_path)


FORUM = types.Channel(
    id=7,
    title="Forum",
    photo=types.ChatPhotoEmpty(),
    date=WHEN,
    megagroup=True,
    forum=True,
    access_hash=3,
)


def _msg(i, peer, *, topic=None, root=False, text=None):
    if root:
        return types.MessageService(
            id=i,
            peer_id=peer,
            date=WHEN,
            action=types.MessageActionTopicCreate(title="T", icon_color=0),
        )
    reply = types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=topic) if topic else None
    return types.Message(id=i, peer_id=peer, date=WHEN, message=text or f"m{i}", reply_to=reply)


def _server(messages, *, anchor_inclusive, users=(ALI,), chats=()):
    """A Telegram-like history server. ``anchor_inclusive`` flips the one edge
    Telegram leaves undocumented: whether a negative add_offset window
    includes ``offset_id`` itself."""
    ordered = sorted(messages, key=lambda m: -m.id)
    calls = []

    def history(request):
        calls.append(request)
        ids = [m.id for m in ordered]
        start = (
            next((k for k, i in enumerate(ids) if i < request.offset_id), len(ids))
            if request.offset_id
            else 0
        )
        if request.add_offset < 0 and anchor_inclusive and request.offset_id in ids:
            start = ids.index(request.offset_id)
        start = max(0, start + request.add_offset)
        window = ordered[start : start + request.limit]
        window = [
            m
            for m in window
            if (not request.min_id or m.id > request.min_id)
            and (not request.max_id or m.id < request.max_id)
        ]
        return types.messages.Messages(
            messages=window, topics=[], chats=list(chats), users=list(users)
        )

    return history, calls


def _by_ids(messages, chats=(), users=(ALI,)):
    index = {m.id: m for m in messages}

    def lookup(request):
        found = [
            index.get(i.id, types.MessageEmpty(id=i.id, peer_id=messages[0].peer_id))
            for i in request.id
        ]
        return types.messages.Messages(
            messages=found, topics=[], chats=list(chats), users=list(users)
        )

    return lookup


def _anchor_ref(conn, identity, message_id):
    refs = RefStore(conn, account_id=1)
    return refs.message_ref(refs.peer_by_identity(identity).row_id, message_id)


@pytest.mark.parametrize("anchor_inclusive", [True, False])
async def test_an_ordinary_window_is_exact_under_either_edge_semantics(world, anchor_inclusive):
    conn, fake, reads, snap, _refs = world
    chat = [_msg(i, types.PeerUser(100)) for i in range(40, 61)]
    history, calls = _server(chat, anchor_inclusive=anchor_inclusive)
    fake.script.update(
        {"messages.GetHistoryRequest": history, "messages.GetMessagesRequest": _by_ids(chat)}
    )
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "user:100", 50), before=3, after=2
    )
    out = await reads.get_context(args, s)
    assert [m["text"] for m in out["messages"]] == ["m47", "m48", "m49", "m50", "m51", "m52"]
    assert out["anchor_message_ref"] == out["messages"][3]["message_ref"]
    assert out["peer"] == {"peer_ref": s.peer_ref, "display_name": "Ali"}
    assert all(r.limit <= 100 for r in calls) and len(calls) == 2


async def test_zero_neighbours_is_just_the_anchor_and_no_history(world):
    conn, fake, reads, snap, _refs = world
    chat = [_msg(50, types.PeerUser(100))]
    fake.script["messages.GetMessagesRequest"] = _by_ids(chat)
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "user:100", 50), before=0, after=0
    )
    out = await reads.get_context(args, s)
    assert [m["text"] for m in out["messages"]] == ["m50"]
    assert "messages.GetHistoryRequest" not in fake.calls


async def test_a_deleted_anchor_is_message_not_found(world):
    conn, fake, reads, snap, _refs = world
    fake.script["messages.GetMessagesRequest"] = _by_ids([_msg(49, types.PeerUser(100))])
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "user:100", 50), before=1, after=1
    )
    with pytest.raises(RetrievalRefusal) as exc:
        await reads.get_context(args, s)
    assert exc.value.code == "MESSAGE_NOT_FOUND"


def _forum_world(fake, messages):
    fake.session.remember(FORUM)
    fake.script["messages.GetPeerDialogsRequest"] = _peer_dialogs(
        [(types.PeerChannel(7), {"unread": 0, "minute": 1})], chats=(FORUM,)
    )
    fake.script["channels.GetMessagesRequest"] = _by_ids(messages, chats=(FORUM,))


async def test_a_named_topic_window_uses_replies_and_never_history(world):
    conn, fake, reads, snap, _refs = world
    peer = types.PeerChannel(7)
    thread = [_msg(12, peer, root=True)] + [_msg(i, peer, topic=12) for i in (20, 22, 24, 26)]
    _forum_world(fake, thread)
    seen = []

    def replies(request):
        seen.append(request)
        rows = [
            m
            for m in thread[1:]
            if (not request.min_id or m.id > request.min_id)
            and (not request.max_id or m.id < request.max_id)
        ]
        rows.sort(key=lambda m: -m.id)
        return types.messages.Messages(
            messages=rows[: request.limit], topics=[], chats=[FORUM], users=[]
        )

    fake.script["messages.GetRepliesRequest"] = replies
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "channel:7", 22), before=5, after=1
    )
    out = await reads.get_context(args, s)
    assert [m["text"] for m in out["messages"]] == ["m20", "m22", "m24"]
    assert {r.msg_id for r in seen} == {12} and "messages.GetHistoryRequest" not in fake.calls


async def test_a_topic_root_has_nothing_before_it(world):
    conn, fake, reads, snap, _refs = world
    peer = types.PeerChannel(7)
    thread = [_msg(12, peer, root=True), _msg(20, peer, topic=12)]
    _forum_world(fake, thread)
    fake.script["messages.GetRepliesRequest"] = lambda r: types.messages.Messages(
        messages=[thread[1]] if r.min_id == 12 else [], topics=[], chats=[FORUM], users=[]
    )
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "channel:7", 12), before=5, after=5
    )
    out = await reads.get_context(args, s)
    assert out["messages"][0]["message_ref"] == out["anchor_message_ref"]
    assert len(out["messages"]) == 2 and fake.calls.count("messages.GetRepliesRequest") == 1


async def test_general_never_includes_a_named_topic_message(world):
    conn, fake, reads, snap, _refs = world
    peer = types.PeerChannel(7)
    chat = [
        _msg(i, peer, topic=(12 if i % 2 else None)) for i in range(30, 71)
    ]  # odd ids are in topic 12
    _forum_world(fake, chat)
    history, _calls = _server(chat, anchor_inclusive=False, users=(), chats=(FORUM,))
    fake.script["messages.GetHistoryRequest"] = history
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "channel:7", 50), before=4, after=4
    )
    out = await reads.get_context(args, s)
    assert [m["text"] for m in out["messages"]] == [f"m{i}" for i in range(42, 60, 2)]
    assert all(m["forum_topic"] is False for m in out["messages"])


async def test_general_short_of_budget_is_partial_not_padded(world):
    conn, fake, reads, snap, _refs = world
    peer = types.PeerChannel(7)
    chat = [_msg(50, peer)] + [
        _msg(i, peer, topic=12) for i in range(1, 50)
    ]  # nothing General below
    _forum_world(fake, chat)
    history, _calls = _server(chat, anchor_inclusive=False, users=(), chats=(FORUM,))
    fake.script["messages.GetHistoryRequest"] = history
    reads._max_rpcs = 4  # dialogs + anchor + two pages, then the budget fires
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "channel:7", 50), before=10, after=0
    )
    out = await reads.get_context(args, s)
    assert out["_partial"] is True and [m["text"] for m in out["messages"]] == ["m50"]


async def test_a_historical_ref_does_not_survive_a_later_deny(world):
    conn, _fake, _reads, snap, _refs = world
    ref = _anchor_ref(conn, "user:100", 50)
    conn.execute("UPDATE peer_policy SET decision = 'deny' WHERE telegram_peer_id = 100")
    conn.execute("UPDATE policy_state SET policy_epoch = policy_epoch + 1")
    conn.commit()
    with pytest.raises(AuthorityRefusal) as exc:
        snap("telegram_get_context", message_ref=ref, before=1, after=1)
    assert exc.value.code == "NOT_ACCESSIBLE"


async def test_an_unknown_message_ref_is_ref_not_found(world):
    _conn, _fake, _reads, snap, _refs = world
    with pytest.raises(AuthorityRefusal) as exc:
        snap("telegram_get_context", message_ref="tgm_" + "z" * 26, before=1, after=1)
    assert exc.value.code == "REF_NOT_FOUND"


async def test_the_page_cap_never_drops_the_anchor(world):
    conn, fake, reads, snap, _refs = world
    chat = [_msg(i, types.PeerUser(100), text="\x01" * 4096) for i in range(1, 102)]
    history, _calls = _server(chat, anchor_inclusive=False)
    fake.script.update(
        {"messages.GetHistoryRequest": history, "messages.GetMessagesRequest": _by_ids(chat)}
    )
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "user:100", 51), before=50, after=50
    )
    out = await reads.get_context(args, s)
    refs = [m["message_ref"] for m in out["messages"]]
    assert out["anchor_message_ref"] in refs and out["_partial"] is True and 1 <= len(refs) <= 101


async def test_a_flood_wait_on_the_window_is_flood_wait(world):
    conn, fake, reads, snap, _refs = world
    chat = [_msg(50, types.PeerUser(100))]
    fake.script.update(
        {
            "messages.GetMessagesRequest": _by_ids(chat),
            "messages.GetHistoryRequest": errors.FloodWaitError(request=None, capture=9),
        }
    )
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "user:100", 50), before=1, after=0
    )
    with pytest.raises(RetrievalRefusal) as exc:
        await reads.get_context(args, s)
    assert (exc.value.code, exc.value.retry_after) == ("FLOOD_WAIT", 9)


async def test_forum_classification_comes_from_the_dialog(world):
    """A by-id response that omits the chat entity must not turn a forum ordinary."""
    conn, fake, reads, snap, _refs = world
    peer = types.PeerChannel(7)
    thread = [_msg(12, peer, root=True)] + [_msg(i, peer, topic=12) for i in (20, 22, 24)]
    _forum_world(fake, thread)
    fake.script["channels.GetMessagesRequest"] = _by_ids(thread, chats=(), users=())
    fake.script["messages.GetHistoryRequest"] = types.messages.Messages(  # the wrong path
        messages=[], topics=[], chats=[], users=[]
    )
    fake.script["messages.GetRepliesRequest"] = lambda r: types.messages.Messages(
        messages=[m for m in thread[1:] if m.id != 22], topics=[], chats=[FORUM], users=[]
    )
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "channel:7", 22), before=1, after=1
    )
    await reads.get_context(args, s)
    assert "messages.GetRepliesRequest" in fake.calls
    assert "messages.GetHistoryRequest" not in fake.calls
