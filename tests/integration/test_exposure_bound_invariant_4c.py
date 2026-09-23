"""Gauntlet G-8 for 4c: the reservation dominates the Phase-3 measurement.

get_context, search_messages and cross_project_search, every egress mode,
seeded adversarial strings (the generators of the 4b invariant).
"""

import random

import pytest
from telethon.tl import types

from telegram_mcp.disclosure.budget import buckets_for
from telegram_mcp.disclosure.coordinator import RetrievalRefusal, _split_sidecar
from telegram_mcp.storage.refstore import RefStore
from telegram_mcp.telegram.reads import TelegramReads
from tests.authority_fixtures import BETA_REF, PROJECT_REF, seed_second_project
from tests.integration.test_exposure_bound_invariant import _script
from tests.integration.test_telegram_reads import make_reads_world

ROUNDS = 8


@pytest.fixture
async def world(tmp_path):
    conn, fake, reads, snap, _refs = await make_reads_world(tmp_path)
    seed_second_project(conn)
    authority = reads._mint.__self__
    reads = TelegramReads(
        reads._session,
        conn,
        mint_cursor=authority.mint_project_cursor,
        mint_search_cursor=authority.mint_search_cursor,
    )
    return conn, fake, reads, snap, authority


def _rehome(history, request):
    """Serve the adversarial page as if it came from the searched peer."""
    peer = request.peer
    if hasattr(peer, "user_id"):
        target = types.PeerUser(peer.user_id)
    elif hasattr(peer, "chat_id"):
        target = types.PeerChat(peer.chat_id)
    else:
        target = types.PeerChannel(peer.channel_id)
    for message in history.messages:
        message.peer_id = target
    history.messages = history.messages[: request.limit]
    return history


@pytest.mark.parametrize("egress", [("metadata_only", None), ("excerpt", 64), ("full_text", None)])
async def test_actual_charge_never_exceeds_the_reservation(world, egress):
    conn, fake, reads, snap, authority = world
    conn.execute("UPDATE client_projects SET egress_level = ?, excerpt_max_codepoints = ?", egress)
    conn.execute("UPDATE policy_state SET include_archived = 1")
    conn.commit()
    rng = random.Random(f"g8-4c-{egress}")
    anchor = RefStore(conn, account_id=1).message_ref(
        RefStore(conn, account_id=1).peer_by_identity("chat:9").row_id, 1
    )
    conn.commit()
    for _round in range(ROUNDS):
        limit = rng.randint(1, 50)
        script = _script(rng, max(limit, 3))
        history = script["messages.GetHistoryRequest"]
        fake.script.update(script)
        fake.script["messages.SearchRequest"] = lambda r, h=history: _rehome(h, r)
        fake.script["messages.GetMessagesRequest"] = types.messages.Messages(
            messages=[m for m in history.messages if m.id == 1] or history.messages[-1:],
            topics=[],
            chats=history.chats,
            users=history.users,
        )
        cases = [
            (
                "telegram_get_context",
                {
                    "message_ref": anchor,
                    "before": rng.randint(0, 50),
                    "after": rng.randint(0, 50),
                    "project_ref": PROJECT_REF,
                },
            ),
            (
                "telegram_search_messages",
                {"project_ref": PROJECT_REF, "query": "q", "limit": limit},
            ),
            (
                "telegram_cross_project_search",
                {"project_refs": [PROJECT_REF, BETA_REF], "query": "q", "limit": limit},
            ),
        ]
        for tool, args in cases:
            validated, snapshot = snap(tool, **args)
            method = {
                "telegram_get_context": reads.get_context,
                "telegram_search_messages": reads.search_messages,
                "telegram_cross_project_search": reads.cross_project_search,
            }[tool]
            try:
                raw = await method(validated, snapshot)
            except RetrievalRefusal as exc:  # the generated page may lack the anchor
                assert exc.code == "MESSAGE_NOT_FOUND", exc
                continue
            data, _side = _split_sidecar(authority.apply_egress(raw, snapshot))
            actual = buckets_for(tool, data, client_id=snapshot.client_id)
            reserved = authority.worst_case_buckets(tool, snapshot)
            for key, usage in actual.items():
                assert key in reserved, (tool, key)
                assert usage.records <= reserved[key].records, (tool, egress, usage, reserved[key])
                assert usage.bytes <= reserved[key].bytes, (tool, egress, usage, reserved[key])
