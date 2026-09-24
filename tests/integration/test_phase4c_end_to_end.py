"""The three 4c tools through ingress, consent and coordinator on a fake Telegram."""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from telethon import errors
from telethon.tl import types

from comms.transports.telegram.consent.challenge import jcs_dumps
from comms.transports.telegram.storage.refstore import RefStore
from tests.authority_fixtures import BETA_REF, PROJECT_REF, seed_second_project
from tests.integration.test_phase4a_end_to_end import CODEX, call
from tests.integration.test_phase4b_end_to_end import _close, _world

BASE = datetime(2026, 9, 21, 8, 0, 0, tzinfo=UTC)


def _hits(request, identity_to_rows):
    peer = request.peer
    key = (
        ("user", peer.user_id)
        if hasattr(peer, "user_id")
        else (("chat", peer.chat_id) if hasattr(peer, "chat_id") else ("channel", peer.channel_id))
    )
    make = {"user": types.PeerUser, "chat": types.PeerChat, "channel": types.PeerChannel}[key[0]]
    rows = identity_to_rows.get(f"{key[0]}:{key[1]}", [])
    rows = [r for r in rows if not request.offset_id or r < request.offset_id]
    page = rows[: request.limit]
    messages = [
        types.Message(
            id=i, peer_id=make(key[1]), date=BASE + timedelta(minutes=i), message=f"needle {i}"
        )
        for i in page
    ]
    if len(rows) > len(page):  # like Telegram: a slice while more remain
        return types.messages.MessagesSlice(
            count=len(rows), messages=messages, topics=[], chats=[], users=[]
        )
    return types.messages.Messages(messages=messages, topics=[], chats=[], users=[])


def _receipts(world):
    return world["conn"].execute("SELECT count(*) FROM disclosure_receipts").fetchone()[0]


async def test_search_carries_coverage_bound_into_the_signed_proof(tmp_path, monkeypatch):
    world = await _world(tmp_path, monkeypatch)
    try:
        world["fake"].script["messages.SearchRequest"] = lambda r: _hits(r, {"user:100": [3, 2, 1]})
        body = await call(
            world,
            CODEX,
            "telegram_search_messages",
            {"project_ref": PROJECT_REF, "query": "needle"},
        )
        assert body["ok"] is True, body
        coverage = body["meta"]["coverage"]
        assert coverage["complete"] is True and body["meta"]["partial"] is False
        proof = body["meta"]["disclosure"]["proof_payload"]
        assert proof["canonical_coverage_digest"] == hashlib.sha256(jcs_dumps(coverage)).hexdigest()
        assert [r["text"] for r in body["data"]["results"]] == ["needle 3", "needle 2", "needle 1"]
        assert "messages.SearchGlobalRequest" not in world["fake"].calls
    finally:
        await _close(world)


async def test_get_context_through_ingress(tmp_path, monkeypatch):
    world = await _world(tmp_path, monkeypatch)
    try:
        conn = world["conn"]
        refs = RefStore(conn, account_id=1)
        anchor = refs.message_ref(refs.peer_by_identity("user:100").row_id, 1)
        conn.commit()
        world["fake"].script["messages.GetMessagesRequest"] = types.messages.Messages(
            messages=[
                types.Message(id=1, peer_id=types.PeerUser(100), date=BASE, message="anchor")
            ],
            topics=[],
            chats=[],
            users=[],
        )
        body = await call(
            world,
            CODEX,
            "telegram_get_context",
            {"project_ref": PROJECT_REF, "message_ref": anchor, "before": 2, "after": 2},
        )
        assert body["ok"] is True, body
        assert body["data"]["anchor_message_ref"] == anchor and body["meta"]["coverage"] is None
    finally:
        await _close(world)


async def test_a_shared_peer_removed_between_retrieve_and_commit_is_discarded(
    tmp_path, monkeypatch
):
    """Design §4.5 race test: no stale matched_projects is ever emitted."""
    world = await _world(tmp_path, monkeypatch)
    try:
        conn = world["conn"]
        seed_second_project(conn)
        conn.execute("UPDATE policy_state SET include_archived = 1")
        conn.commit()

        def search_then_unshare(request):
            conn.execute(
                "DELETE FROM project_peers WHERE project_id = 2 AND peer_id ="
                " (SELECT id FROM peers WHERE telegram_peer_id = 7)"
            )
            conn.commit()
            return _hits(request, {"channel:7": [5]})

        world["fake"].script["messages.SearchRequest"] = search_then_unshare
        before = _receipts(world)
        body = await call(
            world,
            CODEX,
            "telegram_cross_project_search",
            {"project_refs": [PROJECT_REF, BETA_REF], "query": "needle"},
        )
        assert body["ok"] is False and body["error"]["code"] == "POLICY_CHANGED", body
        assert _receipts(world) == before
    finally:
        await _close(world)


async def test_the_query_text_never_leaves_memory(tmp_path, monkeypatch, caplog):
    """Design §3.4 / spec §21.5: a random canary query, on a success that mints a
    cursor and on a Telegram failure, never reaches any file under the runtime
    directory (SQLite and its WAL/SHM, keys, sockets, session) or any log line."""
    caplog.set_level(logging.DEBUG)
    canary = "canary" + secrets.token_hex(12)
    world = await _world(tmp_path, monkeypatch)
    try:
        world["fake"].script["messages.SearchRequest"] = lambda r: _hits(r, {"user:100": [3, 2, 1]})
        ok = await call(
            world,
            CODEX,
            "telegram_search_messages",
            {"project_ref": PROJECT_REF, "query": canary, "limit": 2},
        )
        assert ok["ok"] is True and ok["meta"]["next_cursor"], ok
        world["fake"].script["messages.SearchRequest"] = errors.FloodWaitError(
            request=None, capture=3
        )
        failed = await call(
            world,
            CODEX,
            "telegram_search_messages",
            {"project_ref": PROJECT_REF, "query": canary},
        )
        assert failed["ok"] is False, failed
        world["conn"].commit()
    finally:
        await _close(world)
    needle = canary.encode()
    leaks = [
        str(path) for path in tmp_path.rglob("*") if path.is_file() and needle in path.read_bytes()
    ]
    assert leaks == []
    assert canary not in caplog.text
