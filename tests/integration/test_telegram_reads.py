"""The four reads over a fake Telegram, a real authority snapshot and real refs."""

from datetime import UTC, datetime

import pytest
from telethon import errors
from telethon.tl import types

from comms.transports.telegram.canonical import jcs_dumps
from comms.transports.telegram.disclosure.coordinator import RetrievalRefusal
from comms.transports.telegram.disclosure.seams import CoordinatorAuthority
from comms.transports.telegram.keys.store import load_key, provision_missing
from comms.transports.telegram.runtime.identity import resolve_principal
from comms.transports.telegram.storage.db import bind_cursor_store, open_db
from comms.transports.telegram.storage.refstore import RefStore
from comms.transports.telegram.telegram.reads import TelegramReads
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession
from tests.authority_fixtures import PROJECT_REF, seed_authority_rows, seed_project_world
from tests.telegram.fake_client import FakeClient

WHEN = datetime(2026, 9, 21, 8, 0, 0, tzinfo=UTC)
ALI = types.User(id=100, access_hash=1, first_name="Ali", username="ali")
BOB = types.User(id=101, access_hash=5, first_name="Bob")
ZED = types.User(id=555, access_hash=6, first_name="Zed")
NEWS = types.Channel(
    id=7, title="News", photo=types.ChatPhotoEmpty(), date=WHEN, broadcast=True, access_hash=3
)
TEAM = types.Chat(
    id=9, title="Team", photo=types.ChatPhotoEmpty(), participants_count=3, date=WHEN, version=1
)
CLIENT = "tcl_" + "a" * 26


def _dialog(peer, *, unread=0, archived=False, top=10, minute=0):
    dialog = types.Dialog(
        peer=peer,
        top_message=top,
        read_inbox_max_id=0,
        read_outbox_max_id=0,
        unread_count=unread,
        unread_mentions_count=0,
        unread_reactions_count=0,
        unread_poll_votes_count=0,
        notify_settings=types.PeerNotifySettings(),
        folder_id=1 if archived else None,
    )
    message = types.Message(id=top, peer_id=peer, date=WHEN.replace(minute=minute), message="x")
    return dialog, message


def _peer_dialogs(specs, users=(ALI,), chats=(NEWS, TEAM)):
    pairs = [_dialog(peer, **kw) for peer, kw in specs]
    return types.messages.PeerDialogs(
        dialogs=[d for d, _ in pairs],
        messages=[m for _, m in pairs],
        chats=list(chats),
        users=list(users),
        state=types.updates.State(pts=1, qts=0, date=WHEN, seq=0, unread_count=0),
    )


DEFAULT_DIALOGS = [
    (types.PeerUser(100), {"unread": 2, "minute": 5}),
    (types.PeerChannel(7), {"unread": 4, "minute": 9, "archived": True}),
    (types.PeerChat(9), {"unread": 1, "minute": 1}),
]


async def make_reads_world(tmp_path):
    """Shared by the 4b and 4c read tests (a plain helper, so importing it is lint-clean)."""
    provision_missing(tmp_path / "keys", phases=(2, 3))
    conn = open_db(tmp_path / "meta.db")
    seed_authority_rows(conn)
    refs = seed_project_world(conn)
    authority = CoordinatorAuthority(
        conn,
        privacy_key=load_key("privacy-key"),
        cursor_key=load_key("cursor-key"),
        cursor_store=bind_cursor_store(conn),
        runtime_id=b"\x05" * 16,
    )
    fake = FakeClient({"messages.GetPeerDialogsRequest": _peer_dialogs(DEFAULT_DIALOGS)})
    for entity in (ALI, BOB, NEWS, TEAM):
        fake.session.remember(entity)
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    reads = TelegramReads(session, conn, mint_cursor=authority.mint_project_cursor)
    principal = resolve_principal(conn, CLIENT)

    def snap(tool, **args):
        args.setdefault("project_ref", PROJECT_REF)
        request = authority.freeze_arguments(tool, args, principal=principal)
        return request.validated_args, authority.snapshot(tool, request)

    return conn, fake, reads, snap, refs


@pytest.fixture
async def world(tmp_path):
    return await make_reads_world(tmp_path)


async def test_list_chats_is_the_project_newest_first_without_archived(world):
    _conn, fake, reads, snap, refs = world
    args, s = snap("telegram_list_chats", limit=20, chat_type="any", archived="exclude")
    out = await reads.list_chats(args, s)
    assert [c["peer_ref"] for c in out["chats"]] == [refs["user:100"], refs["chat:9"]]
    first = out["chats"][0]
    assert first["origin_project_refs"] == [PROJECT_REF] and first["username"] == "ali"
    assert out["project"] == {"project_ref": PROJECT_REF, "display_name": "Alpha"}
    assert "_next_cursor" not in out and "_partial" not in out
    assert "messages.GetDialogsRequest" not in fake.calls  # never pages the whole account


async def test_archived_only_needs_the_owner_to_include_archived(world):
    conn, _fake, reads, snap, refs = world
    args, s = snap("telegram_list_chats", limit=20, chat_type="any", archived="only")
    assert (await reads.list_chats(args, s))["chats"] == []  # owner excludes archived
    conn.execute("UPDATE policy_state SET include_archived = 1, policy_epoch = policy_epoch + 1")
    conn.commit()
    args, s = snap("telegram_list_chats", limit=20, chat_type="any", archived="only")
    assert [c["peer_ref"] for c in (await reads.list_chats(args, s))["chats"]] == [
        refs["channel:7"]
    ]


async def test_archived_chat_excluded_from_unread_total(world):
    """Review Focus 6."""
    _conn, _fake, reads, snap, _refs = world
    args, s = snap("telegram_get_unread", limit=30, include_muted=True, chat_type="any")
    out = await reads.get_unread(args, s)
    assert out["total_unread_visible"] == 3 and out["total_is_exact"] is True
    assert sorted(c["unread_count"] for c in out["chats"]) == [1, 2]


async def test_paging_is_keyset_and_never_repeats(world):
    _conn, _fake, reads, snap, refs = world
    args, s = snap("telegram_list_chats", limit=1, chat_type="any", archived="exclude")
    first = await reads.list_chats(args, s)
    cursor = first["_next_cursor"]
    args2, s2 = snap(
        "telegram_list_chats", limit=1, chat_type="any", archived="exclude", cursor=cursor
    )
    second = await reads.list_chats(args2, s2)
    assert [c["peer_ref"] for c in first["chats"] + second["chats"]] == [
        refs["user:100"],
        refs["chat:9"],
    ]
    assert "_next_cursor" not in second


async def test_a_chat_that_moves_mid_walk_is_not_repeated(world):
    _conn, fake, reads, snap, refs = world
    args, s = snap("telegram_list_chats", limit=1, chat_type="any", archived="exclude")
    first = await reads.list_chats(args, s)
    assert [c["peer_ref"] for c in first["chats"]] == [refs["user:100"]]
    # user:100 gets a newer message before page 2: it must not come back
    moved = [(types.PeerUser(100), {"unread": 3, "minute": 30})] + DEFAULT_DIALOGS[1:]
    fake.script["messages.GetPeerDialogsRequest"] = _peer_dialogs(moved)
    args2, s2 = snap(
        "telegram_list_chats",
        limit=1,
        chat_type="any",
        archived="exclude",
        cursor=first["_next_cursor"],
    )
    second = await reads.list_chats(args2, s2)
    assert [c["peer_ref"] for c in second["chats"]] == [refs["chat:9"]]


async def test_a_large_project_pages_to_the_end_with_constant_state(tmp_path):
    """Gauntlet G-4: 1,100 member chats (past the old 1,024 seen_ids cap)."""
    from comms.transports.telegram.storage.refstore import RefStore
    from tests.authority_fixtures import seed_authority_rows

    provision_missing(tmp_path / "keys", phases=(2, 3))
    conn = open_db(tmp_path / "meta.db")
    seed_authority_rows(conn)
    now = "2026-09-23T00:00:00Z"
    conn.execute(
        "INSERT INTO client_projects (client_id, project_id, can_read, can_cross_search,"
        " egress_level, excerpt_max_codepoints, created_at, updated_at)"
        " VALUES (1, 1, 1, 0, 'metadata_only', NULL, ?, ?)",
        (now, now),
    )
    conn.commit()
    refs = RefStore(conn, account_id=1)
    users = [types.User(id=10_000 + i, access_hash=1, first_name=f"U{i}") for i in range(1100)]
    for user in users:
        row = refs.ensure_peer("user", user.id, display_name=user.first_name, username=None)
        conn.execute(
            "INSERT INTO peer_policy VALUES (1, 1, 'user', ?, 'allow', ?, ?)", (user.id, now, now)
        )
        conn.execute(
            "INSERT INTO project_peers VALUES (1, ?, 'primary', ?, ?)", (row.row_id, now, now)
        )
        conn.commit()

    def dialogs(request):
        wanted = {p.peer.user_id for p in request.peers}
        chosen = [u for u in users if u.id in wanted]
        pairs = [_dialog(types.PeerUser(u.id), unread=1, minute=u.id % 60) for u in chosen]
        return types.messages.PeerDialogs(
            dialogs=[d for d, _ in pairs],
            messages=[m for _, m in pairs],
            chats=[],
            users=chosen,
            state=types.updates.State(pts=1, qts=0, date=WHEN, seq=0, unread_count=0),
        )

    fake = FakeClient({"messages.GetPeerDialogsRequest": dialogs})
    for user in users:
        fake.session.remember(user)
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    authority = CoordinatorAuthority(
        conn,
        privacy_key=load_key("privacy-key"),
        cursor_key=load_key("cursor-key"),
        cursor_store=bind_cursor_store(conn),
        runtime_id=b"\x05" * 16,
    )
    reads = TelegramReads(session, conn, mint_cursor=authority.mint_project_cursor)
    principal = resolve_principal(conn, CLIENT)
    seen: list[str] = []
    cursor = None
    for _page in range(20):
        args = {"project_ref": PROJECT_REF, "limit": 100, "include_muted": True, "chat_type": "any"}
        if cursor:
            args["cursor"] = cursor
        request = authority.freeze_arguments("telegram_get_unread", args, principal=principal)
        snapshot = authority.snapshot("telegram_get_unread", request)
        assert len(jcs_dumps(dict(snapshot.state))) < 200  # constant-size cursor state
        out = await reads.get_unread(request.validated_args, snapshot)
        seen += [c["peer_ref"] for c in out["chats"]]
        assert "_partial" not in out and out["total_unread_visible"] == 1100
        cursor = out.get("_next_cursor")
        if cursor is None:
            break
    assert len(seen) == 1100 and len(set(seen)) == 1100


async def test_a_member_missing_from_the_cache_makes_unread_inexact(world):
    _conn, fake, reads, snap, _refs = world
    del fake.session.entities[-9]  # Team is no longer in the entity cache
    fake.script["messages.GetPeerDialogsRequest"] = _peer_dialogs(DEFAULT_DIALOGS[:2])
    args, s = snap("telegram_get_unread", limit=30, include_muted=True, chat_type="any")
    out = await reads.get_unread(args, s)
    assert out["total_unread_visible"] is None and out["total_is_exact"] is False
    assert out["_partial"] is True


async def test_a_rename_refreshes_the_cached_name(world):
    conn, fake, reads, snap, refs = world
    renamed = types.Chat(
        id=9,
        title="Team 2",
        photo=types.ChatPhotoEmpty(),
        participants_count=3,
        date=WHEN,
        version=2,
    )
    fake.script["messages.GetPeerDialogsRequest"] = _peer_dialogs(
        DEFAULT_DIALOGS, chats=(NEWS, renamed)
    )
    args, s = snap("telegram_list_chats", limit=20, chat_type="any", archived="exclude")
    names = {c["peer_ref"]: c["display_name"] for c in (await reads.list_chats(args, s))["chats"]}
    assert names[refs["chat:9"]] == "Team 2"
    assert RefStore(conn, account_id=1).peer_by_ref(refs["chat:9"]).display_name == "Team 2"


async def test_resolve_peer_ranks_exact_then_username_then_prefix(world):
    conn, _fake, reads, snap, refs = world
    args, s = snap("telegram_resolve_peer", query="ali", chat_type="any", limit=10)
    out = await reads.resolve_peer(args, s)
    assert out["matches"][0]["peer_ref"] == refs["user:100"]
    assert out["matches"][0]["match_kind"] == "exact_display_name" and out["ambiguous"] is False
    args, s = snap("telegram_resolve_peer", query="@ali", chat_type="any", limit=10)
    assert (await reads.resolve_peer(args, s))["matches"][0]["match_kind"] == "exact_username"
    args, s = snap("telegram_resolve_peer", query="e", chat_type="any", limit=10)
    out = await reads.resolve_peer(args, s)
    assert [m["display_name"] for m in out["matches"]] == ["Team"]  # News is archived: hidden
    conn.execute("UPDATE policy_state SET include_archived = 1, policy_epoch = policy_epoch + 1")
    conn.commit()
    args, s = snap("telegram_resolve_peer", query="e", chat_type="any", limit=10)
    out = await reads.resolve_peer(args, s)
    assert out["ambiguous"] is True  # "News" and "Team" both contain "e"
    args, s = snap("telegram_resolve_peer", query="bob", chat_type="any", limit=10)
    assert (await reads.resolve_peer(args, s))["matches"] == []  # allowed, but not a member


def _history(*messages, users=(ALI, ZED), chats=(TEAM,)):
    return types.messages.Messages(
        messages=list(messages), topics=[], chats=list(chats), users=list(users)
    )


def _slice(*messages, users=(ALI, ZED), chats=(TEAM,)):
    """A page of a longer history: Telegram answers with a slice, never plain messages."""
    return types.messages.MessagesSlice(
        count=99, messages=list(messages), topics=[], chats=list(chats), users=list(users)
    )


async def test_sender_outside_project_gets_null_ref(world):
    """Review Focus 2."""
    conn, fake, reads, snap, refs = world
    fake.script["messages.GetHistoryRequest"] = _history(
        types.Message(
            id=3, peer_id=types.PeerChat(9), date=WHEN, message="hi", from_id=types.PeerUser(555)
        ),
        types.Message(
            id=2,
            peer_id=types.PeerChat(9),
            date=WHEN,
            message="yo",
            from_id=types.PeerUser(100),
            reply_to=types.MessageReplyHeader(reply_to_msg_id=1),
        ),
    )
    args, s = snap("telegram_get_messages", peer_ref=refs["chat:9"], limit=30)
    out = await reads.get_messages(args, s)
    zed, ali = out["messages"]
    assert (zed["sender_display_name"], zed["sender_peer_ref"]) == ("Zed", None)
    assert ali["sender_peer_ref"] == refs["user:100"]
    assert ali["reply_to_message_ref"].startswith("tgm_") and ali["topic_title"] is None
    assert RefStore(conn, account_id=1).peer_by_identity("user:555") is None  # never minted
    assert out["peer"] == {"peer_ref": refs["chat:9"], "display_name": "Team", "chat_type": "group"}
    assert "_next_cursor" not in out


async def test_get_messages_pages_below_its_anchor(world):
    _conn, fake, reads, snap, refs = world
    seen = []

    def history(request):
        seen.append((request.offset_id, request.max_id))
        top = request.offset_id - 1 if request.offset_id else 50
        return _slice(
            *(
                types.Message(id=i, peer_id=types.PeerUser(100), date=WHEN, message=str(i))
                for i in range(top, top - 2, -1)
            )
        )

    fake.script["messages.GetHistoryRequest"] = history
    args, s = snap("telegram_get_messages", peer_ref=refs["user:100"], limit=2)
    first = await reads.get_messages(args, s)
    args2, s2 = snap(
        "telegram_get_messages", peer_ref=refs["user:100"], limit=2, cursor=first["_next_cursor"]
    )
    second = await reads.get_messages(args2, s2)
    assert seen == [(0, 0), (49, 51)]
    assert [m["text"] for m in first["messages"] + second["messages"]] == ["50", "49", "48", "47"]


async def test_a_deleted_message_does_not_end_paging(world):
    _conn, fake, reads, snap, refs = world
    fake.script["messages.GetHistoryRequest"] = _slice(
        types.Message(id=10, peer_id=types.PeerUser(100), date=WHEN, message="a"),
        types.MessageEmpty(id=9, peer_id=types.PeerUser(100)),
    )
    args, s = snap("telegram_get_messages", peer_ref=refs["user:100"], limit=2)
    out = await reads.get_messages(args, s)
    assert len(out["messages"]) == 1 and "_next_cursor" in out


async def test_flood_wait_is_flood_wait_and_charges_nothing(world):
    """Review Focus 4, at the read boundary (the coordinator side is Task 14)."""
    _conn, fake, reads, snap, refs = world
    fake.script["messages.GetHistoryRequest"] = errors.FloodWaitError(request=None, capture=12)
    args, s = snap("telegram_get_messages", peer_ref=refs["user:100"], limit=30)
    with pytest.raises(RetrievalRefusal) as exc:
        await reads.get_messages(args, s)
    assert (exc.value.code, exc.value.retry_after) == ("FLOOD_WAIT", 12)


async def test_a_chat_the_owner_scope_hides_is_not_readable(world):
    conn, fake, reads, snap, refs = world
    conn.execute("UPDATE policy_state SET include_groups = 0, policy_epoch = policy_epoch + 1")
    conn.commit()
    args, s = snap("telegram_get_messages", peer_ref=refs["chat:9"], limit=30)
    with pytest.raises(RetrievalRefusal) as exc:
        await reads.get_messages(args, s)
    assert exc.value.code == "NOT_ACCESSIBLE"
    assert "messages.GetHistoryRequest" not in fake.calls  # refused before any history


async def test_a_long_page_is_fitted_under_the_cap(world):
    _conn, fake, reads, snap, refs = world
    fake.script["messages.GetHistoryRequest"] = _history(
        *(
            types.Message(id=i, peer_id=types.PeerUser(100), date=WHEN, message="\x01" * 4096)
            for i in range(10, 0, -1)
        )
    )
    args, s = snap("telegram_get_messages", peer_ref=refs["user:100"], limit=10)
    out = await reads.get_messages(args, s)
    assert 0 < len(out["messages"]) < 10 and "_next_cursor" in out


def _spy_admit_live(monkeypatch):
    """Record every class decision retrieval asks the one evaluator for (Task 4A)."""
    import comms.transports.telegram.telegram.reads as reads_module

    seen: list[tuple[str | None, bool]] = []
    real = reads_module.admit_live

    def spy(view, request, **facts):
        verdict = real(view, request, **facts)
        seen.append((request.peer_identity, verdict))
        return verdict

    monkeypatch.setattr(reads_module, "admit_live", spy)
    return seen


async def _refused_by_owner_class(world, monkeypatch, *, sql, identity):
    conn, fake, reads, snap, refs = world
    conn.execute(f"UPDATE policy_state SET {sql}, policy_epoch = policy_epoch + 1")
    conn.commit()
    seen = _spy_admit_live(monkeypatch)
    args, s = snap("telegram_get_messages", peer_ref=refs[identity], limit=30)
    with pytest.raises(RetrievalRefusal) as exc:
        await reads.get_messages(args, s)
    assert exc.value.code == "NOT_ACCESSIBLE"
    assert (identity, False) in seen  # the evaluator's owner_class step denied it
    assert "messages.GetHistoryRequest" not in fake.calls


async def test_an_archived_member_is_not_readable_when_archived_chats_are_excluded(
    world, monkeypatch
):
    await _refused_by_owner_class(
        world, monkeypatch, sql="include_archived = 0", identity="channel:7"
    )


async def test_a_private_member_is_not_readable_when_private_chats_are_excluded(world, monkeypatch):
    await _refused_by_owner_class(
        world, monkeypatch, sql="include_private = 0", identity="user:100"
    )


async def test_a_group_member_is_not_readable_when_groups_are_excluded(world, monkeypatch):
    await _refused_by_owner_class(world, monkeypatch, sql="include_groups = 0", identity="chat:9")


async def test_a_channel_member_is_not_readable_when_channels_are_excluded(world, monkeypatch):
    await _refused_by_owner_class(
        world,
        monkeypatch,
        sql="include_archived = 1, include_channels = 0",
        identity="channel:7",
    )
