"""search_messages and cross_project_search over a fake Telegram (spec §21, §21A, §23D)."""

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from telethon import errors
from telethon.tl import types

from telegram_mcp.disclosure.coordinator import RetrievalRefusal, _split_sidecar
from telegram_mcp.disclosure.coverage import validate_coverage
from telegram_mcp.telegram.reads import TelegramReads
from tests.authority_fixtures import BETA_REF, PROJECT_REF, seed_second_project
from tests.integration.test_telegram_reads import make_reads_world

BASE = datetime(2026, 9, 21, 8, 0, 0, tzinfo=UTC)


def _peer_key(peer):
    for attr, kind in (("user_id", "user"), ("chat_id", "chat"), ("channel_id", "channel")):
        if hasattr(peer, attr):
            return f"{kind}:{getattr(peer, attr)}"
    raise AssertionError(peer)


def _telegram_peer(identity):
    kind, _, raw = identity.partition(":")
    return {"user": types.PeerUser, "chat": types.PeerChat, "channel": types.PeerChannel}[kind](
        int(raw)
    )


def _search_server(corpus, *, inexact=()):
    """corpus: identity -> list of (message_id, minutes after BASE). Newest first, offset exclusive."""
    seen = []

    def search(request):
        identity = _peer_key(request.peer)
        seen.append((identity, request))
        rows = sorted(corpus.get(identity, []), key=lambda r: -r[0])
        rows = [r for r in rows if not request.offset_id or r[0] < request.offset_id]
        page = rows[: request.limit]
        messages = [
            types.Message(
                id=i,
                peer_id=_telegram_peer(identity),
                date=BASE + timedelta(minutes=m),
                message=f"needle {identity} {i}",
            )
            for i, m in page
        ]
        if identity in inexact or len(rows) > len(page):
            # Like Telegram: a slice while more remain (or when it is unsure),
            # the plain messages type only for everything that is left.
            return types.messages.MessagesSlice(
                count=len(rows),
                inexact=identity in inexact,
                messages=messages,
                topics=[],
                chats=[],
                users=[],
            )
        return types.messages.Messages(messages=messages, topics=[], chats=[], users=[])

    return search, seen


@pytest.fixture
async def world(tmp_path):
    conn, fake, reads, snap, refs = await make_reads_world(tmp_path)
    seed_second_project(conn)
    authority = reads._mint.__self__
    reads = TelegramReads(
        reads._session,
        conn,
        mint_cursor=authority.mint_project_cursor,
        mint_search_cursor=authority.mint_search_cursor,
    )
    fake.calls.clear()  # start() probes authorisation once; tests count what follows
    return conn, fake, reads, snap, refs


def _check(out):
    data, side = _split_sidecar(out)
    validate_coverage(
        side["_coverage"], next_cursor=side.get("_next_cursor"), partial=bool(side.get("_partial"))
    )
    return data, side


async def test_a_project_search_is_per_peer_newest_first_and_complete(world):
    _conn, fake, reads, snap, _refs = world
    search, seen = _search_server(
        {"user:100": [(10, 5), (11, 9)], "chat:9": [(20, 7)], "channel:7": [(30, 8)]}
    )
    fake.script["messages.SearchRequest"] = search
    args, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=20)
    data, side = _check(await reads.search_messages(args, s))
    assert [r["text"] for r in data["results"]] == [
        "needle user:100 11",
        "needle chat:9 20",
        "needle user:100 10",
    ]
    assert {identity for identity, _ in seen} == {
        "user:100",
        "chat:9",
    }  # archived channel: owner excludes it
    assert side["_coverage"]["complete"] is True and "_next_cursor" not in side
    assert data["search_scope"] == "project" and all(
        r["origin_project_refs"] == [PROJECT_REF] for r in data["results"]
    )
    assert set(fake.calls) <= {"messages.GetPeerDialogsRequest", "messages.SearchRequest"}


async def test_since_is_inclusive_until_exclusive_with_a_second_of_over_fetch(world):
    _conn, fake, reads, snap, _refs = world
    search, seen = _search_server({"user:100": [(1, 0), (2, 10), (3, 20)]})
    fake.script["messages.SearchRequest"] = search
    args, s = snap(
        "telegram_search_messages",
        project_ref=PROJECT_REF,
        query="needle",
        since="2026-09-21T08:00:00Z",
        until="2026-09-21T08:20:00Z",
        limit=20,
    )
    data, _side = _check(await reads.search_messages(args, s))
    assert [r["text"] for r in data["results"]] == ["needle user:100 2", "needle user:100 1"]
    request = next(r for identity, r in seen if identity == "user:100")
    assert request.min_date == BASE - timedelta(seconds=1)
    assert request.max_date == BASE + timedelta(minutes=20, seconds=1)


async def test_continuation_delivers_every_hit_exactly_once(world):
    _conn, fake, reads, snap, _refs = world
    search, _seen = _search_server(
        {"user:100": [(i, i) for i in range(1, 6)], "chat:9": [(i, i) for i in range(10, 13)]}
    )
    fake.script["messages.SearchRequest"] = search
    got, cursor = [], None
    for _page in range(20):
        extra = {"cursor": cursor} if cursor else {}
        args, s = snap(
            "telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=2, **extra
        )
        data, side = _check(await reads.search_messages(args, s))
        got += [r["message_ref"] for r in data["results"]]
        cursor = side.get("_next_cursor")
        if cursor is None:
            assert side["_coverage"]["complete"] is True
            break
        assert "response_limit" in side["_coverage"]["partial_reasons"]
    assert len(got) == 8 and len(set(got)) == 8


async def test_cross_search_attributes_shared_hits_and_intersects_egress(world):
    conn, fake, reads, snap, _refs = world
    conn.execute("UPDATE policy_state SET include_archived = 1, policy_epoch = policy_epoch + 1")
    conn.commit()
    search, _seen = _search_server({"channel:7": [(30, 8)], "user:101": [(40, 9)]})
    fake.script["messages.SearchRequest"] = search
    args, s = snap(
        "telegram_cross_project_search",
        project_refs=[PROJECT_REF, BETA_REF],
        query="needle",
        limit=20,
    )
    raw = await reads.cross_project_search(args, s)
    data, side = _check(reads._mint.__self__.apply_egress(raw, s))
    shared = next(
        r
        for r in data["results"]
        if "channel" in (r["peer_display_name"] or "").lower()
        or r["origin_project_refs"] == sorted([PROJECT_REF, BETA_REF])
    )
    assert shared["origin_project_refs"] == sorted([PROJECT_REF, BETA_REF])
    assert [m["project_ref"] for m in shared["matched_projects"]] == sorted([PROJECT_REF, BETA_REF])
    assert shared["text"] is None  # Beta is metadata_only: the most restrictive grant wins
    assert data["search_scope"] == "cross_project" and len(data["projects"]) == 2
    assert [c["eligible_peers"] for c in side["_coverage"]["project_coverage"]] == [3, 2]


@pytest.mark.parametrize(
    "reason",
    ["rpc_budget", "deadline", "hit_budget", "peer_budget", "telegram_partial", "response_limit"],
)
async def test_every_partial_reason_is_reported_honestly(world, reason):
    _conn, fake, reads, snap, _refs = world
    corpus = {
        "user:100": [(i, i % 50) for i in range(1, 30)],
        "chat:9": [(i, i % 50) for i in range(100, 130)],
    }
    search, _seen = _search_server(
        corpus, inexact={"chat:9"} if reason == "telegram_partial" else ()
    )
    fake.script["messages.SearchRequest"] = search
    limit = 50
    if reason == "rpc_budget":
        reads._max_rpcs = 2  # dialogs + one search
    if reason == "response_limit":
        limit = 3
    if reason == "deadline":

        async def slow(request):
            await asyncio.sleep(0.3)
            return search(request)

        fake.script["messages.SearchRequest"] = lambda r: slow(r)
        fake.__class__ = type("Awaiting", (type(fake),), {"__call__": _awaiting_call})
        reads._deadline_s = 0.4
    args, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=limit)
    if reason == "peer_budget":
        s = dataclasses.replace(s, peer_cap=1)
    if reason == "hit_budget":
        from telegram_mcp.telegram import search as engine

        original = engine.run_page

        async def small_budget(*a, **k):
            return await original(*a, **{**k, "max_hits": 5})

        engine_patch = pytest.MonkeyPatch()
        engine_patch.setattr("telegram_mcp.telegram.reads.run_page", small_budget)
    try:
        _data, side = _check(await reads.search_messages(args, s))
    finally:
        if reason == "hit_budget":
            engine_patch.undo()
    assert reason in side["_coverage"]["partial_reasons"]
    assert side["_coverage"]["complete"] is False and side["_partial"] is True


async def _awaiting_call(self, request):
    from telegram_mcp.telegram.telethon_adapter import qualified

    name = qualified(request)
    self.calls.append(name)
    outcome = self.script.get(name)
    if outcome is None:
        result = self._default(name, request)
    elif callable(outcome):
        result = outcome(request)
    else:
        result = outcome
    if asyncio.iscoroutine(result):
        result = await result
    for entity in [*getattr(result, "users", []), *getattr(result, "chats", [])]:
        self.session.remember(entity)
    return result


async def test_an_unreachable_member_is_never_claimed_complete(world):
    _conn, fake, reads, snap, _refs = world
    del fake.session.entities[-9]  # Team is not in the entity cache
    search, _seen = _search_server({"user:100": [(1, 1)]})
    fake.script["messages.SearchRequest"] = search
    args, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=20)
    _data, side = _check(await reads.search_messages(args, s))
    assert side["_coverage"]["complete"] is False
    assert side["_coverage"]["partial_reasons"] == ["telegram_partial"]


async def test_a_long_page_is_held_under_the_cap_without_losing_hits(world):
    _conn, fake, reads, snap, _refs = world
    corpus = {"user:100": [(i, i) for i in range(1, 11)]}

    def search(request):
        if _peer_key(request.peer) != "user:100":
            return types.messages.Messages(messages=[], topics=[], chats=[], users=[])
        rows = sorted(
            (r for r in corpus["user:100"] if not request.offset_id or r[0] < request.offset_id),
            key=lambda r: -r[0],
        )[: request.limit]
        return types.messages.Messages(
            messages=[
                types.Message(
                    id=i,
                    peer_id=types.PeerUser(100),
                    date=BASE + timedelta(minutes=m),
                    message="\x01" * 4096,
                )
                for i, m in rows
            ],
            topics=[],
            chats=[],
            users=[],
        )

    fake.script["messages.SearchRequest"] = search
    got, cursor = [], None
    for _page in range(20):
        extra = {"cursor": cursor} if cursor else {}
        args, s = snap(
            "telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=10, **extra
        )
        data, side = _check(await reads.search_messages(args, s))
        got += [r["message_ref"] for r in data["results"]]
        cursor = side.get("_next_cursor")
        if cursor is None:
            break
    assert len(got) == 10 and len(set(got)) == 10


async def test_a_flood_wait_fails_the_call(world):
    _conn, fake, reads, snap, _refs = world
    fake.script["messages.SearchRequest"] = errors.FloodWaitError(request=None, capture=4)
    args, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=20)
    with pytest.raises(RetrievalRefusal) as exc:
        await reads.search_messages(args, s)
    assert (exc.value.code, exc.value.retry_after) == ("FLOOD_WAIT", 4)


async def _pages(reads, snap, **args):
    out, cursor = [], None
    for _page in range(40):
        extra = {"cursor": cursor} if cursor else {}
        a, s = snap(
            "telegram_search_messages", project_ref=PROJECT_REF, query="needle", **args, **extra
        )
        data, side = _check(await reads.search_messages(a, s))
        out.append((data, side))
        cursor = side.get("_next_cursor")
        if cursor is None:
            return out
    raise AssertionError("continuation never ended")


async def test_telegram_uncertainty_survives_the_cursor(world):
    _conn, fake, reads, snap, _refs = world
    search, _seen = _search_server(
        {"chat:9": [(i, i) for i in range(10, 14)], "user:100": [(i, i) for i in range(1, 4)]},
        inexact={"chat:9"},
    )
    fake.script["messages.SearchRequest"] = search
    pages = await _pages(reads, snap, limit=2)
    first = next(
        i
        for i, (_d, side) in enumerate(pages)
        if "telegram_partial" in side["_coverage"]["partial_reasons"]
    )
    assert first == 0 and len(pages) > 1
    for _data, side in pages[first:]:
        assert "telegram_partial" in side["_coverage"]["partial_reasons"]
        assert side["_coverage"]["complete"] is False
    assert sum(len(d["results"]) for d, _s in pages) == 7


async def test_a_continuation_checks_dialogs_in_one_batch(world):
    _conn, fake, reads, snap, _refs = world

    def withholding(request):  # one hit per request, and always "more": a buggy channel
        identity = _peer_key(request.peer)
        top = (request.offset_id or 50) - 1
        return types.messages.MessagesSlice(
            count=99,
            messages=[
                types.Message(id=top, peer_id=_telegram_peer(identity), date=BASE, message="needle")
            ],
            topics=[],
            chats=[],
            users=[],
        )

    fake.script["messages.SearchRequest"] = withholding
    a, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=2)
    _data, side = _check(await reads.search_messages(a, s))
    a, s = snap(
        "telegram_search_messages",
        project_ref=PROJECT_REF,
        query="needle",
        limit=2,
        cursor=side["_next_cursor"],
    )
    fake.calls.clear()
    _check(await reads.search_messages(a, s))
    assert fake.calls.count("messages.GetPeerDialogsRequest") == 1, fake.calls


async def test_a_cursor_naming_an_unknown_peer_fails_closed(world):
    _conn, fake, reads, snap, _refs = world
    fake.script["messages.SearchRequest"] = _search_server({})[0]
    a, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=2)
    s = dataclasses.replace(
        s, state={"per_peer": {"tgp_" + "z" * 26: {"offset_id": 5}}, "next_unstarted_index": 3}
    )
    with pytest.raises(RetrievalRefusal) as exc:
        await reads.search_messages(a, s)
    assert exc.value.code == "INTERNAL_ERROR"


async def test_coverage_counts_every_telegram_request_the_call_made(world):
    """Dialog checks are Telegram RPCs too (spec §13.2's 20)."""
    conn, fake, reads, snap, _refs = world
    conn.execute("UPDATE policy_state SET include_archived = 1, policy_epoch = policy_epoch + 1")
    conn.commit()  # every peer is really searched: no non-request to miscount
    search, _seen = _search_server(
        {"user:100": [(1, 1)], "chat:9": [(2, 2)], "channel:7": [(3, 3)]}
    )
    fake.script["messages.SearchRequest"] = search
    a, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=20)
    _data, side = _check(await reads.search_messages(a, s))
    made = [c for c in fake.calls if c.startswith(("messages.", "channels."))]
    assert "messages.GetPeerDialogsRequest" in made
    assert side["_coverage"]["telegram_rpcs"] == len(made)


async def test_an_owner_excluded_peer_is_not_eligible(world):
    """Spec §21.4: the universe is the project after intersection with owner scope."""
    _conn, fake, reads, snap, _refs = world
    search, seen = _search_server({"user:100": [(1, 1)], "chat:9": [(2, 2)]})
    fake.script["messages.SearchRequest"] = search
    a, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=20)
    _data, side = _check(await reads.search_messages(a, s))
    c = side["_coverage"]
    assert "channel:7" not in {identity for identity, _ in seen}  # archived: owner excludes it
    assert (c["eligible_peers"], c["peers_scanned"], c["complete"]) == (2, 2, True)
    assert c["project_coverage"][0]["eligible_peers"] == 2


async def test_a_search_page_holds_combined_text_under_32000_codepoints(world):
    _conn, fake, reads, snap, _refs = world
    corpus = {"user:100": [(i, i) for i in range(1, 11)]}

    def search(request):
        if _peer_key(request.peer) != "user:100":
            return types.messages.Messages(messages=[], topics=[], chats=[], users=[])
        rows = sorted(
            (r for r in corpus["user:100"] if not request.offset_id or r[0] < request.offset_id),
            key=lambda r: -r[0],
        )
        page = rows[: request.limit]
        cls = types.messages.MessagesSlice if len(rows) > len(page) else types.messages.Messages
        extra = {"count": len(rows)} if cls is types.messages.MessagesSlice else {}
        return cls(
            messages=[
                types.Message(
                    id=i,
                    peer_id=types.PeerUser(100),
                    date=BASE + timedelta(minutes=m),
                    message="a" * 4000,  # ASCII: the codepoint cap binds first
                )
                for i, m in page
            ],
            topics=[],
            chats=[],
            users=[],
            **extra,
        )

    fake.script["messages.SearchRequest"] = search
    got, cursor, first = [], None, None
    for _page in range(10):
        extra = {"cursor": cursor} if cursor else {}
        a, s = snap(
            "telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=10, **extra
        )
        data, side = _check(await reads.search_messages(a, s))
        first = first or data
        got += [r["message_ref"] for r in data["results"]]
        cursor = side.get("_next_cursor")
        if cursor is None:
            break
    assert sum(len(r["text"]) for r in first["results"]) <= 32_000 and len(first["results"]) < 10
    assert len(got) == 10 and len(set(got)) == 10


async def test_search_skips_a_peer_the_owner_class_excludes(world, monkeypatch):
    """Task 4A: search asks the one evaluator, with live facts, before scanning a peer."""
    import telegram_mcp.telegram.reads as reads_module

    conn, fake, reads, snap, _refs = world
    conn.execute("UPDATE policy_state SET include_groups = 0, policy_epoch = policy_epoch + 1")
    conn.commit()
    decided: list[tuple[str | None, bool]] = []
    real = reads_module.admit_live

    def spy(view, request, **facts):
        verdict = real(view, request, **facts)
        decided.append((request.peer_identity, verdict))
        return verdict

    monkeypatch.setattr(reads_module, "admit_live", spy)
    search, seen = _search_server({"user:100": [(1, 1)], "chat:9": [(2, 2)]})
    fake.script["messages.SearchRequest"] = search
    a, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=20)
    _check(await reads.search_messages(a, s))
    assert "chat:9" not in {identity for identity, _ in seen}
    assert ("chat:9", False) in decided and ("user:100", True) in decided
