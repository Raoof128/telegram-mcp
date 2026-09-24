"""comms v0.3 Task C20: the MTProto context source — the Phase-4 read engine without its
authority wrapper (P §19–21; A32; §13.2 bounds)."""

import ast
import asyncio
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from telethon.tl import types

from comms.core.providers.protocols import (
    ADAPTER_CONTRACTS,
    ContextQuery,
    ContextRefused,
    ProviderTarget,
)
from comms.transports.telegram.disclosure.bounds import TEXT_CODEPOINTS_MAX
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession
from comms.transports.telegram.user import context as context_module
from comms.transports.telegram.user.context import UserContext
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import Registry, run_suite
from tests.telegram.fake_client import FakeClient

NOW = datetime(2026, 9, 25, tzinfo=UTC)
SENT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
SUPER = ProviderTarget("telegram", "telegram_user", "dst_s", "-1000000000077")
BASIC = ProviderTarget("telegram", "telegram_user", "dst_b", "-55")


def _message(mid, text="hi", sender=42):
    return types.Message(
        id=mid,
        peer_id=types.PeerChannel(77),
        date=SENT,
        message=text,
        from_id=types.PeerUser(sender),
    )


def _slice(ids, count=1000, text="hi"):
    return types.messages.ChannelMessages(
        pts=1,
        count=count,
        messages=[_message(i, text) for i in ids],
        topics=[],
        chats=[],
        users=[types.User(id=42, access_hash=9, first_name="Ali")],
    )


def _read(tmp_path, script, query, readiness_break=False):
    seen = []

    def recorder(answer):
        def respond(request):
            seen.append(request)
            return answer

        return respond

    fake = FakeClient({name: recorder(answer) for name, answer in script.items()})
    fake.session.remember(
        types.Channel(
            id=77, title="t", photo=types.ChatPhotoEmpty(), date=None, access_hash=5, megagroup=True
        )
    )
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    def run(coro):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(10)

    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    run(session.start())
    if readiness_break:
        session.revoked = True
    try:
        return UserContext(session, run=run, clock=lambda: NOW).read(query), seen
    finally:
        run(session.stop())
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()


def test_provenance_live(tmp_path):
    page, _ = _read(
        tmp_path,
        {"messages.GetHistoryRequest": _slice([50, 49])},
        ContextQuery(SUPER, "recent", {"limit": 2}),
    )
    assert page.provenance == "telegram_live"
    assert all(i["source"] == "telegram_live" and i["observed_at"] for i in page.items)
    first = page.items[0]
    assert (first["message_id"], first["sender_id"], first["sent_at"]) == (
        50,
        "42",
        "2026-09-24T12:00:00Z",  # the Phase-4 message timestamp, unchanged
    )
    assert first["untrusted"] == {"text": "hi", "sender_name": "Ali"}


def test_phase4_bounds_preserved(tmp_path):
    # §13.2: 32,000 codepoints per page, enforced while building; the cursor resumes after the last kept.
    big = "x" * 4096
    page, _ = _read(
        tmp_path,
        {"messages.GetHistoryRequest": _slice(range(100, 80, -1), text=big)},
        ContextQuery(SUPER, "recent", {"limit": 20}),
    )
    kept = [i["message_id"] for i in page.items]
    assert sum(len(i["untrusted"]["text"]) for i in page.items) <= TEXT_CODEPOINTS_MAX
    assert 0 < len(kept) < 20 and page.next_cursor == str(kept[-1])
    # _page_end: a short channel page is not the end (Telethon: channels withhold messages) ...
    page, _ = _read(
        tmp_path,
        {"messages.GetHistoryRequest": _slice([500, 300])},
        ContextQuery(SUPER, "recent", {"limit": 5}),
    )
    assert page.next_cursor == "300"
    # ... but a complete messages.Messages is.
    done = types.messages.Messages(
        messages=[_message(2), _message(1)], topics=[], chats=[], users=[]
    )
    page, _ = _read(
        tmp_path, {"messages.GetHistoryRequest": done}, ContextQuery(SUPER, "recent", {"limit": 5})
    )
    assert page.next_cursor is None


def test_recent_resumes_from_the_cursor(tmp_path):
    _page, seen = _read(
        tmp_path,
        {"messages.GetHistoryRequest": _slice([10])},
        ContextQuery(SUPER, "recent", {"limit": 5, "cursor": "11"}),
    )
    assert (seen[0].offset_id, seen[0].limit, seen[0].add_offset) == (11, 5, 0)


def test_around_centres_on_the_message(tmp_path):
    page, seen = _read(
        tmp_path,
        {"messages.GetHistoryRequest": _slice([12, 11, 10, 9, 8])},
        ContextQuery(SUPER, "around", {"message_id": 10, "before": 2, "after": 2}),
    )
    assert (seen[0].offset_id, seen[0].add_offset, seen[0].limit) == (10, -3, 5)
    assert [i["message_id"] for i in page.items] == [12, 11, 10, 9, 8] and page.next_cursor is None


def test_search_is_per_peer(tmp_path):
    page, seen = _read(
        tmp_path,
        {"messages.SearchRequest": _slice([7])},
        ContextQuery(SUPER, "search", {"query": "nowruz", "limit": 10}),
    )
    assert seen[0].q == "nowruz" and isinstance(seen[0].peer, types.InputPeerChannel)
    assert [i["message_id"] for i in page.items] == [7]


def test_members_page(tmp_path):
    participants = types.channels.ChannelParticipants(
        count=2,
        participants=[
            types.ChannelParticipantCreator(42, types.ChatAdminRights()),
            types.ChannelParticipant(43, SENT),
        ],
        chats=[],
        users=[
            types.User(id=42, access_hash=9, first_name="Ali"),
            types.User(id=43, access_hash=8, first_name="Sara", last_name="K"),
        ],
    )
    page, seen = _read(
        tmp_path,
        {"channels.GetParticipantsRequest": participants},
        ContextQuery(SUPER, "members", {"limit": 2}),
    )
    assert [(i["user_id"], i["role"], i["untrusted"]) for i in page.items] == [
        (42, "creator", {"name": "Ali"}),
        (43, "member", {"name": "Sara K"}),
    ]
    assert page.next_cursor == "2" and seen[0].limit == 2


@pytest.mark.parametrize(
    "kind,args",
    [
        ("recent", {"limit": 0}),
        ("recent", {"limit": 101}),
        ("recent", {"cursor": "-1"}),
        ("search", {"query": ""}),
        ("search", {"query": "x" * 257}),
        ("around", {"message_id": 1, "before": 51}),
        ("members", {"cursor": "x"}),
        ("recent", {"extra": 1}),
    ],
)
def test_malformed_arguments_are_refused(tmp_path, kind, args):
    with pytest.raises(ValueError):
        _read(tmp_path, {}, ContextQuery(SUPER, kind, args))


def test_unknown_kinds_and_an_unusable_session_are_refused(tmp_path):
    with pytest.raises(ContextRefused) as refused:
        _read(tmp_path, {}, ContextQuery(SUPER, "everything"))
    assert refused.value.code == "PROVIDER_UNSUPPORTED"
    with pytest.raises(ContextRefused) as refused:
        _read(tmp_path, {}, ContextQuery(SUPER, "recent"), readiness_break=True)
    assert refused.value.code == "SESSION_REVOKED"


def test_no_policy_or_budget_imported():
    forbidden = (
        "comms.transports.telegram.authority",
        "comms.transports.telegram.disclosure.coordinator",
        "comms.transports.telegram.disclosure.budget",
        "comms.transports.telegram.disclosure.seams",
        "comms.transports.telegram.disclosure.search_authority",
        "comms.transports.telegram.storage",
        "comms.transports.telegram.telegram.reads",
        "telethon",
    )
    tree = ast.parse(Path(context_module.__file__).read_text())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert not [m for m in imported if m.startswith(forbidden)]


def test_the_whole_telegram_user_conformance_suite_passes():
    registry = Registry({k: v for k, v in REGISTRY.cases.items() if k[0] == "telegram_user"})
    report = run_suite(registry, {"telegram_user": ADAPTER_CONTRACTS["telegram_user"]})
    assert report.ok and report.skipped == {}, report.failures
